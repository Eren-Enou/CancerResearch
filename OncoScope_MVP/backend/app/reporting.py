"""Descriptive Markdown report and structured export. Every fact names its record and source."""
from __future__ import annotations

import csv
import io
import json
import sqlite3

from .db import RECORD_TABLES, SCHEMA_VERSION, now_utc, verify_audit_chain
from .units import format_number

EXPORT_FORMAT = "oncoscope-synthetic-export"


def _rows(conn: sqlite3.Connection, query: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(query, args).fetchall()]


def patient_records(conn: sqlite3.Connection, patient_id: int) -> dict[str, list[dict]]:
    order = {"sources": "id", "events": "date, id", "labs": "date, id", "treatments": "date, id",
             "studies": "date, id", "measurements": "study_id, lesion_key, id"}
    return {t: _rows(conn, f"SELECT * FROM {t} WHERE patient_id=? ORDER BY {order[t]}", (patient_id,)) for t in RECORD_TABLES}


def _one_line(text: str) -> str:
    return " ".join(text.split())


def build_report(conn: sqlite3.Connection, patient: dict) -> str:
    records = patient_records(conn, patient["id"])
    sources = {s["id"]: s for s in records["sources"]}
    studies = {s["id"]: s for s in records["studies"]}
    cited: dict[int, int] = {}

    def trace(record: dict, entity: str) -> str:
        cited[record["source_id"]] = cited.get(record["source_id"], 0) + 1
        src = sources.get(record["source_id"])
        source = f"source #{src['id']}: {src['label']}" if src else f"source #{record['source_id']}: UNAVAILABLE"
        revision = f"; corrected, revision {record['revision']}" if record["revision"] > 1 else ""
        return f"[{entity} #{record['id']}{revision}; {record['verification_status']}] [{source}]"

    lines = [f"# OncoScope record: {patient['label']}", "",
             f"Generated: {now_utc()} (UTC) | Synthetic data: {'YES' if patient['is_synthetic'] else 'NO'} | Schema v{SCHEMA_VERSION}", "",
             "> Descriptive record only. Not a medical interpretation, response assessment, or treatment recommendation.",
             "> Each line cites `[record type #id; verification state]` and `[source #id: label]`. Corrected values show",
             "> their current revision; the original values are in the audit log.", "",
             "## Clinical events", ""]
    lines += [f"- {r['date']} — {r['title']} ({r['kind']}) {trace(r, 'event')}" for r in records["events"]] or ["- None recorded"]
    lines += ["", "## Treatment administration records", ""]
    lines += [f"- {r['date']} — {r['regimen']} / cycle {r['cycle']}" + (f" — {_one_line(r['notes'])}" if r["notes"] else "")
              + f" {trace(r, 'treatment')}" for r in records["treatments"]] or ["- None recorded"]
    lines += ["", "## Laboratory values (as entered; no unit conversion)", ""]
    lines += [f"- {r['date']} — {r['test']}: {format_number(r['value'])} {r['unit']} {trace(r, 'lab')}"
              for r in records["labs"]] or ["- None recorded"]
    lines += ["", "## Imaging reports on file", ""]
    for r in records["studies"]:
        lines += [f"### {r['date']} — {r['study_label']} ({r['modality']})", "", f"Record: {trace(r, 'study')}", "",
                  r["report_text"] or "No report entered.", ""]
    if not records["studies"]:
        lines += ["- None recorded", ""]
    lines += ["## Recorded lesion measurements (not response assessment)", ""]
    for r in records["measurements"]:
        study = studies.get(r["study_id"])
        study_ref = f"study #{r['study_id']}, {study['date']}" if study else f"study #{r['study_id']}"
        short = f"{format_number(r['short_axis_mm'])} mm" if r["short_axis_mm"] is not None else "not recorded"
        lines += [f"- {study_ref} — {r['lesion_key']} ({r['site']}): long axis {format_number(r['long_axis_mm'])} mm, "
                  f"short axis {short} {trace(r, 'measurement')}"]
    if not records["measurements"]:
        lines += ["- None recorded"]
    lines += ["", "## Source register", ""]
    for s in records["sources"]:
        document = (f"document `{s['document_filename']}` sha256 `{s['document_sha256']}`" if s["document_sha256"]
                    else "no document attached (metadata only)")
        origin = f"; origin: {s['origin']}" if s["origin"] else ""
        lines += [f"- source #{s['id']}: {s['label']} — {s['kind']}, dated {s['source_date'] or 'not recorded'}{origin}; "
                  f"{document}; cited by {cited.get(s['id'], 0)} record(s); revision {s['revision']}, {s['verification_status']}"]
    if not records["sources"]:
        lines += ["- None registered"]
    chain = verify_audit_chain(conn)
    lines += ["", "## Audit", "",
              f"- Audit entries for this record: {conn.execute('SELECT count(*) FROM audit_log WHERE patient_id=?', (patient['id'],)).fetchone()[0]}",
              f"- Audit hash chain: {'verified' if chain['valid'] else 'FAILED at entry #' + str(chain['first_invalid_id'])} ({chain['entries_checked']} entries checked)",
              "", "All values require verification against their original sources."]
    return "\n".join(lines) + "\n"


def build_export(conn: sqlite3.Connection, patient: dict) -> dict:
    records = patient_records(conn, patient["id"])
    audit_rows = _rows(conn, "SELECT * FROM audit_log WHERE patient_id=? ORDER BY id", (patient["id"],))
    for row in audit_rows:
        row["detail"] = json.loads(row.pop("detail_json"))
    return {
        "format": EXPORT_FORMAT, "schema_version": SCHEMA_VERSION, "generated_at": now_utc(),
        "notice": "Synthetic demonstration data. Descriptive record only; no clinical interpretation.",
        "patient": patient, **records, "audit_log": audit_rows, "audit_chain": verify_audit_chain(conn),
        "documents_included": False,
    }


def _csv_cell(value):
    # Spreadsheet formula-injection guard for text cells; numbers are written unchanged.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def build_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    if rows:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: _csv_cell(v) for k, v in r.items()} for r in rows)
    return out.getvalue()
