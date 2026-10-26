"""Evidence findings and report traceability, audited against the original fictional files."""
import importlib.util
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import FIXTURES, new_source

spec = importlib.util.spec_from_file_location("audit_report", FIXTURES.parent / "audit_report.py")
audit_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_report)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "findings.sqlite3"))
    with TestClient(app) as c:
        yield c


def ok(res, status=200):
    assert res.status_code == status, res.text
    return res.json()


@pytest.fixture
def case(client):
    """Reconstructs the fictional case from the fixture documents through the public API."""
    pid = ok(client.post("/api/patients", json={"label": "Fictional Case M25"}), 201)["id"]
    lab1 = new_source(client, pid, "Imaginary Valley lab 2026-08-07", document="lab_2026-08-07_imaginary_valley.txt", source_date="2026-08-08")
    lab2 = new_source(client, pid, "Imaginary Valley lab 2026-09-18", document="lab_2026-09-18_imaginary_valley.pdf", source_date="2026-09-19")
    portal = new_source(client, pid, "Portal copy 2026-09-18", document="lab_2026-09-18_portal_copy.txt", source_date="2026-09-20")
    ct1 = new_source(client, pid, "Baseline CT report", kind="radiology", document="ct_2026-08-10_baseline.txt", source_date="2026-08-10")
    ct2 = new_source(client, pid, "Follow-up CT report", kind="radiology", document="ct_2026-10-12_followup.pdf", source_date="2026-10-12")
    note = new_source(client, pid, "Clinic note without file", kind="oncology-note", source_date="2026-08-14")
    for src in (lab1, lab2, portal):
        for proposal in ok(client.post(f"/api/sources/{src['id']}/extract"), 201)["created"]:
            ok(client.post(f"/api/extractions/{proposal['id']}/accept", json={}))
    s1 = ok(client.post("/api/studies", json={"patient_id": pid, "source_id": ct1["id"], "date": "2026-08-10", "modality": "CT",
                                              "study_label": "Baseline abdominal CT (fictional)", "anchor_quote": "Study date: 2026-08-10"}), 201)
    s2 = ok(client.post("/api/studies", json={"patient_id": pid, "source_id": ct2["id"], "date": "2026-10-12", "modality": "CT",
                                              "study_label": "Follow-up abdominal CT (fictional)", "anchor_quote": "Study date: 2026-10-12"}), 201)
    m1 = ok(client.post("/api/measurements", json={"patient_id": pid, "source_id": ct1["id"], "study_id": s1["id"], "lesion_key": "reference-A",
                                                   "site": "Fictional soft-tissue lesion", "long_axis_mm": 38, "short_axis_mm": 22,
                                                   "anchor_quote": "measures 38 x 22 mm"}), 201)
    m2 = ok(client.post("/api/measurements", json={"patient_id": pid, "source_id": ct2["id"], "study_id": s2["id"], "lesion_key": "reference-A",
                                                   "site": "Fictional soft-tissue lesion", "long_axis_mm": 31, "short_axis_mm": 18,
                                                   "anchor_quote": "measures 31 x 18 mm"}), 201)
    tx = ok(client.post("/api/treatments", json={"patient_id": pid, "source_id": note["id"], "date": "2026-08-14",
                                                 "regimen": "Fictional regimen X", "cycle": 1}), 201)
    return {"pid": pid, "lab1": lab1, "lab2": lab2, "portal": portal, "ct1": ct1, "ct2": ct2, "note": note,
            "s1": s1, "s2": s2, "m1": m1, "m2": m2, "tx": tx}


def test_findings_surface_conflicts_without_resolving(client, case):
    findings = ok(client.get(f"/api/patients/{case['pid']}/evidence"))
    kinds = {f["kind"]: f for f in findings}
    conflict = kinds["conflicting_values"]
    assert conflict["severity"] == "conflict" and "2026-09-18" in conflict["message"]
    assert "202 U/mL" in conflict["message"] and "220 U/mL" in conflict["message"]
    assert set(conflict["source_ids"]) == {case["lab2"]["id"], case["portal"]["id"]}
    assert "does not choose" in conflict["message"]
    labs = ok(client.get(f"/api/patients/{case['pid']}/labs"))
    assert sorted(l["value"] for l in labs) == [202, 220, 310]       # both conflicting values are kept
    assert kinds["source_without_document"]["records"] == [{"entity": "treatments", "id": case["tx"]["id"], "source_id": case["note"]["id"]}]
    assert findings[0]["severity"] == "conflict"


def test_findings_duplicates_dates_users_and_pending(client, case):
    pid = case["pid"]
    ok(client.post("/api/studies", json={"patient_id": pid, "source_id": case["note"]["id"], "date": "2026-08-10", "modality": "CT",
                                         "study_label": "Baseline CT mentioned in clinic note"}), 201)
    ok(client.post("/api/labs", json={"patient_id": pid, "source_id": case["note"]["id"], "date": "2026-08-07", "test": "CA 125",
                                      "value": 310, "unit": "U/mL"}), 201)
    ok(client.post("/api/labs", json={"patient_id": pid, "source_id": case["note"]["id"], "date": "2026-08-20", "test": "CA-125",
                                      "value": 250, "unit": "U/mL"}), 201)   # after the note's 2026-08-14 date
    ok(client.post("/api/events", json={"patient_id": pid, "source_id": case["note"]["id"], "date": "2026-08-14", "kind": "symptom",
                                        "title": "Fictional fatigue reported by user", "provenance": "user_observation"}), 201)
    ok(client.post("/api/measurements", json={"patient_id": pid, "source_id": case["ct1"]["id"], "study_id": case["s2"]["id"],
                                              "lesion_key": "reference-B", "site": "Fictional", "long_axis_mm": 9,
                                              "anchor_quote": "Target lesion reference-A"}), 201)
    fax = new_source(client, pid, "Fictional fax", document="lab_2026-10_ambiguous_fax.txt")
    ok(client.post(f"/api/sources/{fax['id']}/extract"), 201)
    unanchored = ok(client.post("/api/labs", json={"patient_id": pid, "source_id": case["lab1"]["id"], "date": "2026-08-07",
                                                   "test": "Hemoglobin", "value": 11.2, "unit": "g/dL"}), 201)
    kinds = {}
    for f in ok(client.get(f"/api/patients/{pid}/evidence")):
        kinds.setdefault(f["kind"], []).append(f)
    assert "several documents may describe the same draw" in kinds["possible_duplicate"][0]["message"]
    assert any("CT study on 2026-08-10" in f["message"] for f in kinds["possible_same_event"])
    assert any("is dated 2026-08-20, after its source" in f["message"] for f in kinds["date_after_source"])
    assert kinds["user_observations"][0]["records"][0]["entity"] == "events"
    assert kinds["measurement_source_differs"]
    assert len(kinds["pending_review"]) == 3 and "date_ambiguous" in kinds["pending_review"][0]["message"]
    assert {"entity": "labs", "id": unanchored["id"], "source_id": case["lab1"]["id"]} in kinds["unanchored_transcription"][0]["records"]


def test_lab_series_reports_consecutive_differences(client, case):
    series = ok(client.get(f"/api/patients/{case['pid']}/labs/series", params={"test": "CA-125"}))
    assert [(c["from_date"], c["to_date"], c["difference"]) for c in series["changes"]] == [
        ("2026-08-07", "2026-09-18", -108.0), ("2026-09-18", "2026-09-18", 18.0)]


def test_report_citations_verify_against_original_files(client, case, tmp_path):
    report = client.get(f"/api/patients/{case['pid']}/report.md").text
    results = audit_report.audit(report, FIXTURES)
    assert len(results) == 7          # 3 extracted labs, 2 studies, 2 measurements
    assert all(r["ok"] for r in results), [r for r in results if not r["ok"]]
    assert {r["file"] for r in results} == {"lab_2026-08-07_imaginary_valley.txt", "lab_2026-09-18_imaginary_valley.pdf",
                                            "lab_2026-09-18_portal_copy.txt", "ct_2026-08-10_baseline.txt", "ct_2026-10-12_followup.pdf"}
    assert any(r["page"] == 2 and r["quote"] == "measures 31 x 18 mm" for r in results)
    assert "[transcribed; no document on file]" in report        # the treatment from the note without a file
    assert "## Documentation findings" in report and "[conflict]" in report
    # Tampering is caught: a copy of the documents where one file differs fails the audit.
    tampered = tmp_path / "docs"
    tampered.mkdir()
    for f in FIXTURES.iterdir():
        (tampered / f.name).write_bytes(f.read_bytes())
    (tampered / "lab_2026-09-18_portal_copy.txt").write_bytes((FIXTURES / "lab_2026-09-18_portal_copy.txt").read_bytes().replace(b"220", b"202"))
    bad = [r for r in audit_report.audit(report, tampered) if not r["ok"]]
    assert len(bad) == 1 and bad[0]["problem"].startswith("SHA-256")


def test_export_includes_extractions_and_findings(client, case):
    export = ok(client.get(f"/api/patients/{case['pid']}/export.json"))
    assert len(export["extractions"]) == 3 and all(e["status"] == "accepted" for e in export["extractions"])
    assert any(f["kind"] == "conflicting_values" for f in export["findings"])
    lab = next(l for l in export["labs"] if l["value"] == 202)
    assert lab["provenance"] == "extracted" and lab["anchor_page"] == 1 and json.loads(json.dumps(lab["anchor_quote"])) == "CA-125   202   U/mL"
