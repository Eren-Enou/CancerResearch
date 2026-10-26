"""Evidence findings: where the documentation agrees, conflicts, repeats itself or is missing support.

Findings are descriptive. Nothing here resolves a conflict, picks a "correct" value, or interprets a trend.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from .units import format_number, test_key


def _rows(conn: sqlite3.Connection, query: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(query, args).fetchall()]


def _ref(entity: str, record: dict) -> dict:
    return {"entity": entity, "id": record["id"], "source_id": record.get("source_id")}


def build_findings(conn: sqlite3.Connection, patient_id: int) -> list[dict]:
    q = lambda table: _rows(conn, f"SELECT * FROM {table} WHERE patient_id=? ORDER BY id", (patient_id,))
    sources = {s["id"]: s for s in q("sources")}
    labs, studies, treatments, events, measurements = q("labs"), q("studies"), q("treatments"), q("events"), q("measurements")
    findings: list[dict] = []

    def add(kind: str, severity: str, message: str, records: list[dict]):
        key = "|".join(f"{r['entity']}#{r['id']}" for r in records) or message
        findings.append({"id": f"{kind}:{key}", "kind": kind, "severity": severity, "message": message, "records": records,
                         "source_ids": sorted({r["source_id"] for r in records if r.get("source_id")})})

    # Same test on the same date: agreeing values (possible duplicate reports) or conflicting values. Never resolved.
    by_test_date = defaultdict(list)
    for lab in labs:
        by_test_date[(test_key(lab["test"]), lab["date"])].append(lab)
    for (_key, day), group in sorted(by_test_date.items(), key=lambda kv: kv[0][1]):
        if len(group) < 2:
            continue
        values = {(lab["value"], lab["unit"]) for lab in group}
        listing = "; ".join(f"lab #{lab['id']} = {format_number(lab['value'])} {lab['unit']} (source #{lab['source_id']})" for lab in group)
        refs = [_ref("labs", lab) for lab in group]
        if len({unit for _v, unit in values}) > 1:
            add("different_units_same_date", "attention", f"{group[0]['test']} on {day} is recorded in different units: {listing}. "
                "Values in different units are not compared.", refs)
        elif len(values) > 1:
            add("conflicting_values", "conflict", f"{group[0]['test']} on {day} has conflicting recorded values: {listing}. "
                "Check the original documents; the application does not choose between them.", refs)
        else:
            same_source = len({lab["source_id"] for lab in group}) == 1
            add("possible_duplicate", "attention", f"{group[0]['test']} on {day} is recorded {len(group)} times with the same value "
                f"({listing}); {'the same source repeats it' if same_source else 'several documents may describe the same draw'}.", refs)

    for entity, records, fields, label in (
        ("studies", studies, ("date", "modality"), lambda r: f"{r['modality']} study"),
        ("treatments", treatments, ("date", "regimen", "cycle"), lambda r: f"{r['regimen']} cycle {r['cycle']}"),
        ("events", events, ("date", "kind"), lambda r: f"{r['kind']} event"),
    ):
        groups = defaultdict(list)
        for r in records:
            groups[tuple(r[f] for f in fields)].append(r)
        for group in groups.values():
            if len(group) > 1:
                ids = ", ".join(f"#{r['id']} (source #{r['source_id']})" for r in group)
                add("possible_same_event", "attention", f"{label(group[0])} on {group[0]['date']} appears in {len(group)} records: {ids}. "
                    "They may describe one event; they are kept separate.", [_ref(entity, r) for r in group])

    study_by_id = {s["id"]: s for s in studies}
    dated = [("labs", r, r["date"]) for r in labs] + [("studies", r, r["date"]) for r in studies] + \
            [("treatments", r, r["date"]) for r in treatments] + [("events", r, r["date"]) for r in events if r["kind"] != "plan"] + \
            [("measurements", r, study_by_id[r["study_id"]]["date"]) for r in measurements]
    for entity, r, day in dated:
        src = sources.get(r["source_id"])
        if src and src["source_date"] and day > src["source_date"]:
            add("date_after_source", "attention", f"{entity[:-1] if entity != 'studies' else 'study'} #{r['id']} is dated {day}, after its "
                f"source #{src['id']} document date {src['source_date']}.", [_ref(entity, r)])

    unanchored = defaultdict(list)
    for entity, r, _day in dated + [("events", r, r["date"]) for r in events if r["kind"] == "plan"]:
        src = sources.get(r["source_id"])
        if r["provenance"] == "user_observation":
            continue
        if src and not src["document_sha256"]:
            unanchored[("source_without_document", src["id"])].append(_ref(entity, r))
        elif r["anchor_quote"] is None and r["anchor_region"] is None:
            unanchored[("unanchored_transcription", src["id"] if src else 0)].append(_ref(entity, r))
    for (kind, sid), refs in sorted(unanchored.items()):
        if kind == "source_without_document":
            msg = f"Source #{sid} has no attached document, so {len(refs)} record(s) citing it cannot be checked against an original."
        else:
            msg = f"{len(refs)} transcribed record(s) from source #{sid} do not point to a page, passage or region of its document."
        add(kind, "attention", msg, refs)

    users = [_ref(e, r) for e, r, _d in dated if r["provenance"] == "user_observation"]
    if users:
        add("user_observations", "info", f"{len(users)} record(s) are user observations, not transcriptions of a document.", users)

    for m in measurements:
        study = study_by_id[m["study_id"]]
        if m["source_id"] != study["source_id"]:
            add("measurement_source_differs", "info", f"Measurement #{m['id']} cites source #{m['source_id']} but its study #{study['id']} "
                f"cites source #{study['source_id']}.", [_ref("measurements", m), _ref("studies", study)])

    for row in _rows(conn, "SELECT * FROM extractions WHERE patient_id=? AND status='pending' ORDER BY id", (patient_id,)):
        issues = [i["code"] for i in json.loads(row["issues_json"])]
        add("pending_review", "attention" if issues else "info",
            f"Extraction #{row['id']} from source #{row['source_id']} awaits review" + (f" (issues: {', '.join(issues)})" if issues else "") + ".",
            [{"entity": "extractions", "id": row["id"], "source_id": row["source_id"]}])
    order = {"conflict": 0, "attention": 1, "info": 2}
    return sorted(findings, key=lambda f: order[f["severity"]])
