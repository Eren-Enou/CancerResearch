"""Scores the extraction pipeline against the hand-written ground truth of the fictional benchmark corpus.

Usage: python -m app.benchmark [--json out.json] [--markdown out.md]

Two levels:
1. Pipeline: the real text extraction + parser on every document, compared field by field with the manifest.
2. System: the documents are imported into a throwaway database through the API, extracted twice (duplicate handling),
   every issue-free proposal is accepted (the most permissive reviewer), and the accepted records, their evidence anchors
   and the cross-document findings are checked against the manifest.

Gate: zero silently incorrect results — an issue-free proposal whose fields differ from the ground truth, that resolves an
ambiguity the ground truth says must stay visible, or that has no ground truth at all. Failures are listed, never dropped.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from .documents import extract_pages
from .extraction import PARSER, PARSER_VERSION, extract_ca125

BENCH = Path(__file__).resolve().parents[2] / "fixtures" / "benchmark"
FIELDS = ("date", "value", "unit")


def _match(expected: list[dict], proposals: list, pages: list[str]) -> tuple[list[tuple], list]:
    """Pairs each expected statement with the proposal citing it (same page, evidence inside the cited passage)."""
    free = list(proposals)
    pairs = []
    for exp in expected:
        hit = next((p for p in free if p.page == exp["page"] and exp["evidence"] in p.original_text), None)
        if hit:
            free.remove(hit)
        pairs.append((exp, hit))
    return pairs, free


def evaluate_pipeline(manifest: dict, documents: Path) -> dict:
    rows, outcome_counts = [], Counter()
    field_checks = Counter()
    anchor = Counter()
    for d in manifest["documents"]:
        data = (documents / d["file"]).read_bytes()
        pages = extract_pages(data, d["media_type"])
        has_text = any(p.strip() for p in pages)
        if not has_text:
            outcome = "unsupported_identified" if not d["text_layer"] else "missed_text_layer"
            for exp in d["expected_observations"]:
                rows.append({"doc": d["id"], "layout": d["layout"], "expected": exp, "proposal": None, "outcome": outcome})
                outcome_counts[outcome] += 1
            continue
        proposals = extract_ca125(pages, d["source_date"])
        pairs, unmatched = _match([e for e in d["expected_observations"] if e["status"] != "no_text"], proposals, pages)
        for exp, prop in pairs:
            outcome, field_errors = _classify(exp, prop)
            if prop:
                for f in FIELDS:
                    if exp[f] is not None:
                        field_checks[f"{f}_checked"] += 1
                        field_checks[f"{f}_correct"] += prop.fields.get(f) == exp[f]
                    elif prop.fields.get(f) is not None and exp["status"] != "mention":
                        field_checks[f"{f}_guessed"] += 1
                anchor["checked"] += 1
                anchor["correct"] += (prop.page == exp["page"] and exp["evidence"] in prop.original_text
                                      and pages[prop.page - 1][prop.start:prop.end] == prop.original_text)
            outcome_counts[outcome] += 1
            rows.append({"doc": d["id"], "layout": d["layout"], "expected": exp, "proposal": _p(prop), "outcome": outcome,
                         "field_errors": field_errors})
        for prop in unmatched:
            outcome = "spurious_flagged" if prop.issues else "silent_incorrect"
            outcome_counts[outcome] += 1
            rows.append({"doc": d["id"], "layout": d["layout"], "expected": None, "proposal": _p(prop), "outcome": outcome})
    complete = sum(1 for r in rows if r["expected"] and r["expected"]["status"] == "complete")
    flagged = sum(1 for r in rows if r["expected"] and r["expected"]["status"] == "flagged")
    clean = sum(1 for r in rows if r["proposal"] and not r["proposal"]["issues"])
    return {
        "rows": rows, "outcomes": dict(outcome_counts),
        "precision_clean": _ratio(outcome_counts["correct"], clean),
        "recall_complete": _ratio(outcome_counts["correct"], complete),
        "ambiguity_preserved": _ratio(outcome_counts["flagged_correct"] + outcome_counts["flagged_field_error"], flagged),
        "field_accuracy": {f: _ratio(field_checks[f"{f}_correct"], field_checks[f"{f}_checked"]) for f in FIELDS},
        "guessed_fields": {f: field_checks[f"{f}_guessed"] for f in FIELDS},
        "anchor_accuracy": _ratio(anchor["correct"], anchor["checked"]),
        "silent_incorrect": outcome_counts["silent_incorrect"],
    }


def _classify(exp: dict, prop) -> tuple[str, list[str]]:
    if prop is None:
        return ("mention_ignored" if exp["status"] == "mention" else "missed"), []
    errors = [f for f in FIELDS if exp[f] is not None and prop.fields.get(f) != exp[f]]
    if exp["status"] == "flagged":
        errors += [f"{f} guessed" for f in FIELDS if exp[f] is None and prop.fields.get(f) is not None and f != "date"
                   or (f == "date" and exp[f] is None and prop.fields.get(f) is not None)]
    codes = {i["code"] for i in prop.issues}
    if exp["status"] == "complete":
        if codes:
            return "flagged_unnecessarily", errors
        return ("correct" if not errors else "silent_incorrect"), errors
    if exp["status"] == "mention":
        return ("mention_flagged" if codes else "silent_incorrect"), []
    if not codes:
        return "silent_incorrect", errors or ["ambiguity resolved silently"]
    if not codes & set(exp["expected_issues"]):
        return "flagged_other_issue", errors
    return ("flagged_correct" if not errors else "flagged_field_error"), errors


def _p(prop) -> dict | None:
    if prop is None:
        return None
    return {"page": prop.page, "start": prop.start, "end": prop.end, "original_text": prop.original_text,
            "fields": {k: prop.fields.get(k) for k in ("test", *FIELDS)}, "issues": [i["code"] for i in prop.issues]}


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def evaluate_system(manifest: dict, documents: Path) -> dict:
    """Imports the corpus through the API into a temporary database and checks review-queue behaviour end to end."""
    from fastapi.testclient import TestClient
    tmp = tempfile.mkdtemp(prefix="oncoscope-benchmark-")
    previous = os.environ.get("ONCOSCOPE_DB_PATH")
    os.environ["ONCOSCOPE_DB_PATH"] = str(Path(tmp) / "benchmark.sqlite3")
    try:
        from .main import app
        with TestClient(app) as c:
            pid = c.post("/api/patients", json={"label": manifest["case"]}).json()["id"]
            source_of, unsupported = {}, {}
            for d in manifest["documents"]:
                s = c.post("/api/sources", json={"patient_id": pid, "label": f"{d['id']} {d['layout']}", "kind": "lab" if d["category"] == "lab" else "other",
                                                 "source_date": d["source_date"]}).json()
                c.put(f"/api/sources/{s['id']}/document", content=(documents / d["file"]).read_bytes(),
                      headers={"Content-Type": "application/octet-stream", "X-Document-Filename": d["file"]}).raise_for_status()
                source_of[d["id"]] = s["id"]
            first = second = 0
            for d in manifest["documents"]:
                r = c.post(f"/api/sources/{source_of[d['id']]}/extract")
                if r.status_code == 422:
                    unsupported[d["id"]] = r.json()["detail"]
                    continue
                first += len(r.json()["created"])
                second += len(c.post(f"/api/sources/{source_of[d['id']]}/extract").json()["created"])
            proposals = c.get(f"/api/patients/{pid}/extractions").json()
            accepted, wrong_accepted = 0, []
            doc_by_source = {v: k for k, v in source_of.items()}
            truth = {d["id"]: d for d in manifest["documents"]}
            for p in proposals:
                if p["issues"]:
                    continue
                res = c.post(f"/api/extractions/{p['id']}/accept", json={})
                if res.status_code != 200:
                    continue
                accepted += 1
                rec = res.json()["record"]
                doc = truth[doc_by_source[p["source_id"]]]
                ok = any(e["status"] == "complete" and (e["date"], e["value"], e["unit"]) == (rec["date"], rec["value"], rec["unit"])
                         for e in doc["expected_observations"])
                if not ok:
                    wrong_accepted.append({"doc": doc["id"], "record": {k: rec[k] for k in ("date", "value", "unit")}})
            labs = c.get(f"/api/patients/{pid}/labs").json()
            provenance_ok = all(l["provenance"] == "extracted" and l["anchor_quote"] and l["extraction_id"] for l in labs)
            texts = {}
            anchors_ok = 0
            for l in labs:
                if l["source_id"] not in texts:
                    texts[l["source_id"]] = c.get(f"/api/sources/{l['source_id']}/document/text").json()["pages"]
                anchors_ok += texts[l["source_id"]][l["anchor_page"] - 1]["text"][l["anchor_start"]:l["anchor_end"]] == l["anchor_quote"]
            findings = c.get(f"/api/patients/{pid}/evidence").json()
            finding_results = []
            for exp in manifest["expected_findings"]:
                wanted = {source_of[x] for x in exp["documents"]}
                hit = next((f for f in findings if f["kind"] == exp["kind"] and exp["date"] in f["message"] and wanted <= set(f["source_ids"])), None)
                finding_results.append({**exp, "detected": hit is not None, "message": hit["message"] if hit else None})
            pending = [p for p in c.get(f"/api/patients/{pid}/extractions", params={"status": "pending"}).json()]
            return {
                "documents_imported": len(source_of), "proposals_first_run": first, "proposals_second_run": second,
                "unsupported_documents": unsupported, "accepted_issue_free": accepted, "accepted_incorrect": wrong_accepted,
                "pending_with_issues": len(pending), "accepted_records_with_valid_provenance": provenance_ok,
                "anchors_verified": f"{anchors_ok}/{len(labs)}", "findings": finding_results,
            }
    finally:
        if previous is None:
            os.environ.pop("ONCOSCOPE_DB_PATH", None)
        else:
            os.environ["ONCOSCOPE_DB_PATH"] = previous
        shutil.rmtree(tmp, ignore_errors=True)   # the throwaway database and document copies do not outlive the run


def run(manifest_path: Path = BENCH / "manifest.json", documents: Path = BENCH / "documents") -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pipeline = evaluate_pipeline(manifest, documents)
    system = evaluate_system(manifest, documents)
    by_type = defaultdict(Counter)
    for r in pipeline["rows"]:
        by_type[r["layout"]][r["outcome"]] += 1
    gates = {
        "zero_silent_incorrect": pipeline["silent_incorrect"] == 0 and not system["accepted_incorrect"],
        "no_duplicate_proposals_on_rerun": system["proposals_second_run"] == 0,
        "all_expected_findings_detected": all(f["detected"] for f in system["findings"]),
        "accepted_provenance_valid": system["accepted_records_with_valid_provenance"]
                                     and system["anchors_verified"].split("/")[0] == system["anchors_verified"].split("/")[1],
    }
    return {"parser": PARSER, "parser_version": PARSER_VERSION, "documents": len(manifest["documents"]), "pipeline": pipeline,
            "system": system, "by_layout": {k: dict(v) for k, v in sorted(by_type.items())}, "gates": gates,
            "passed": all(gates.values())}


def to_markdown(result: dict) -> str:
    p, s = result["pipeline"], result["system"]
    pct = lambda v: "n/a" if v is None else f"{v * 100:.1f}%"
    out = [f"# Extraction benchmark — {result['parser']} v{result['parser_version']}", "",
           f"Corpus: {result['documents']} fictional documents (`fixtures/benchmark`). Generated by `python -m app.benchmark`.", "",
           "## Gates", ""] + [f"- {'PASS' if v else 'FAIL'} — {k.replace('_', ' ')}" for k, v in result["gates"].items()]
    out += ["", "## Pipeline metrics", "", "| Metric | Value |", "|---|---|",
            f"| Precision (issue-free proposals that are fully correct) | {pct(p['precision_clean'])} |",
            f"| Recall (complete statements extracted correctly with no issue) | {pct(p['recall_complete'])} |",
            f"| Ambiguity / unsupported / missing kept visible | {pct(p['ambiguity_preserved'])} |",
            f"| Date accuracy (where stated) | {pct(p['field_accuracy']['date'])} |",
            f"| Value accuracy (where stated) | {pct(p['field_accuracy']['value'])} |",
            f"| Unit accuracy (where stated) | {pct(p['field_accuracy']['unit'])} |",
            f"| Evidence-anchor accuracy | {pct(p['anchor_accuracy'])} |",
            f"| Silently incorrect results | {p['silent_incorrect']} |",
            f"| Guessed fields (value/unit/date filled where ground truth has none) | {sum(p['guessed_fields'].values())} |", "",
            "Outcome counts: " + ", ".join(f"{k} {v}" for k, v in sorted(p["outcomes"].items())), "",
            "## System run (API, throwaway database)", "",
            f"- Documents imported: {s['documents_imported']}; proposals on first run {s['proposals_first_run']}, on re-run {s['proposals_second_run']}",
            f"- Refused as unsupported (no text layer): {', '.join(sorted(s['unsupported_documents'])) or 'none'}",
            f"- Issue-free proposals accepted: {s['accepted_issue_free']}; accepted but incorrect: {len(s['accepted_incorrect'])}",
            f"- Proposals left pending with issues: {s['pending_with_issues']}",
            f"- Accepted records with extracted provenance and anchor: {s['accepted_records_with_valid_provenance']}; anchors re-verified {s['anchors_verified']}",
            ""] + [f"- Finding {f['kind']} on {f['date']} ({', '.join(f['documents'])}): {'detected' if f['detected'] else 'NOT detected'}"
                   for f in s["findings"]]
    out += ["", "## Results by layout", "", "| Layout | Outcomes |", "|---|---|"]
    out += [f"| {k} | {', '.join(f'{o} {n}' for o, n in sorted(v.items()))} |" for k, v in result["by_layout"].items()]
    problems = [r for r in p["rows"] if r["outcome"] in ("missed", "silent_incorrect", "flagged_unnecessarily", "flagged_other_issue",
                                                         "flagged_field_error", "missed_text_layer")]
    out += ["", "## Every non-ideal outcome", ""]
    out += [f"- {r['doc']} ({r['layout']}): **{r['outcome']}** — expected {json.dumps({k: r['expected'][k] for k in ('value', 'unit', 'date', 'status')}) if r['expected'] else 'nothing'}; "
            f"got {json.dumps(r['proposal']['fields']) + ' issues ' + str(r['proposal']['issues']) if r['proposal'] else 'no proposal'}"
            for r in problems] or ["- None"]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Score CA-125 extraction against the fictional benchmark ground truth")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--holdout", action="store_true", help="score the held-out set written after the parser was frozen")
    args = parser.parse_args()
    root = BENCH / "holdout" if args.holdout else BENCH
    result = run(root / "manifest.json", root / "documents")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    md = to_markdown(result)
    if args.markdown:
        args.markdown.write_text(md, encoding="utf-8")
    print(md)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
