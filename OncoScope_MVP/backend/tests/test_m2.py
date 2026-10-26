"""M2 hardening tests. All data is fictional and created fresh in a temporary database."""
import csv
import io
import json
import re
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db
from app.backup import backup
from app.main import app
from app.units import exact_difference, format_number

PDF = b"%PDF-1.4\n% Fictional synthetic document for OncoScope tests\n%%EOF\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Blank database: no seed."""
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "blank.sqlite3"))
    with TestClient(app) as c:
        yield c


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture
def case(client):
    """The required demonstration data, created only through the public API."""
    patient = ok(client.post("/api/patients", json={"label": "Fictional Demo Case M2"}), 201)
    pid = patient["id"]
    src = ok(client.post("/api/sources", json={"patient_id": pid, "label": "Fictional lab printout", "kind": "lab",
                                               "source_date": "2026-09-01", "origin": "Imaginary Clinic Lab"}), 201)
    rad1 = ok(client.post("/api/sources", json={"patient_id": pid, "label": "Fictional CT report 1", "kind": "radiology",
                                                "source_date": "2026-08-02"}), 201)
    rad2 = ok(client.post("/api/sources", json={"patient_id": pid, "label": "Fictional CT report 2", "kind": "radiology",
                                                "source_date": "2026-10-02"}), 201)
    lab = ok(client.post("/api/labs", json={"patient_id": pid, "source_id": src["id"], "date": "2026-09-01",
                                            "test": "CA-125", "value": 87.5, "unit": "U/mL"}), 201)
    tx = ok(client.post("/api/treatments", json={"patient_id": pid, "source_id": src["id"], "date": "2026-09-03",
                                                 "regimen": "Fictional regimen X", "cycle": 1}), 201)
    s1 = ok(client.post("/api/studies", json={"patient_id": pid, "source_id": rad1["id"], "date": "2026-08-02",
                                              "modality": "CT", "study_label": "Fictional CT 1"}), 201)
    s2 = ok(client.post("/api/studies", json={"patient_id": pid, "source_id": rad2["id"], "date": "2026-10-02",
                                              "modality": "CT", "study_label": "Fictional CT 2"}), 201)
    m1 = ok(client.post("/api/measurements", json={"patient_id": pid, "source_id": rad1["id"], "study_id": s1["id"],
                                                   "lesion_key": "demo-L1", "site": "Fictional site", "long_axis_mm": 41.3,
                                                   "short_axis_mm": 20}), 201)
    m2 = ok(client.post("/api/measurements", json={"patient_id": pid, "source_id": rad2["id"], "study_id": s2["id"],
                                                   "lesion_key": "demo-L1", "site": "Fictional site", "long_axis_mm": 35.1}), 201)
    return {"pid": pid, "src": src, "rad1": rad1, "rad2": rad2, "lab": lab, "tx": tx, "s1": s1, "s2": s2, "m1": m1, "m2": m2}


# --- Required demonstration -------------------------------------------------------------------

def test_required_demonstration_from_blank_database(client, case):
    pid = case["pid"]
    assert ok(client.get("/api/patients"))[0]["is_synthetic"] == 1

    # Chart series and timeline show exactly what the database holds.
    with db.connection() as conn:
        stored = [dict(r) for r in conn.execute("SELECT * FROM labs WHERE patient_id=?", (pid,))]
    series = ok(client.get(f"/api/patients/{pid}/labs/series", params={"test": "CA-125"}))
    assert [(p["date"], p["value"], p["unit"]) for p in series["points"]] == [(r["date"], r["value"], r["unit"]) for r in stored]
    timeline = ok(client.get(f"/api/patients/{pid}/timeline"))
    assert {"CA-125: 87.5 U/mL", "Fictional regimen X (cycle 1)"} <= {t["label"] for t in timeline}
    measurement_rows = [t for t in timeline if t["entity"] == "measurements"]
    assert {(t["id"], t["date"]) for t in measurement_rows} == {(case["m1"]["id"], "2026-08-02"), (case["m2"]["id"], "2026-10-02")}

    # Comparison shows exactly one matched difference: 35.1 - 41.3 = -6.2 (not -6.199999...).
    cmp_ = ok(client.get(f"/api/patients/{pid}/compare", params={"baseline_id": case["s1"]["id"], "followup_id": case["s2"]["id"]}))
    assert len(cmp_["measurements"]) == 1
    assert cmp_["measurements"][0]["difference_mm"] == -6.2
    assert cmp_["measurements"][0]["status"] == "matched"

    # Report cites every source and every record.
    report = client.get(f"/api/patients/{pid}/report.md").text
    for source in (case["src"], case["rad1"], case["rad2"]):
        assert f"source #{source['id']}: {source['label']}" in report
    assert f"[lab #{case['lab']['id']}; entered] [source #{case['src']['id']}: Fictional lab printout]" in report
    assert f"[treatment #{case['tx']['id']}; entered]" in report
    assert f"[measurement #{case['m1']['id']}; entered] [source #{case['rad1']['id']}" in report
    assert "long axis 41.3 mm, short axis 20 mm" in report

    # Correct the lab value and a measurement; both originals and corrections appear in audit history.
    ok(client.patch(f"/api/records/labs/{case['lab']['id']}", json={"changes": {"value": 78.5}, "reason": "Transcription error: digits swapped"}))
    ok(client.patch(f"/api/records/measurements/{case['m2']['id']}", json={"changes": {"long_axis_mm": 36.1}, "reason": "Re-read fictional report"}))
    history = ok(client.get(f"/api/records/labs/{case['lab']['id']}/history"))
    assert [h["action"] for h in history] == ["create", "correct"]
    detail = json.loads(history[1]["detail_json"])
    assert (detail["before"]["value"], detail["after"]["value"], detail["changed_fields"]) == (87.5, 78.5, ["value"])
    assert json.loads(history[0]["detail_json"])["value"] == 87.5
    assert history[1]["reason"] == "Transcription error: digits swapped"
    cmp_ = ok(client.get(f"/api/patients/{pid}/compare", params={"baseline_id": case["s1"]["id"], "followup_id": case["s2"]["id"]}))
    assert cmp_["measurements"][0]["difference_mm"] == -5.2
    report = client.get(f"/api/patients/{pid}/report.md").text
    assert f"CA-125: 78.5 U/mL [lab #{case['lab']['id']}; corrected, revision 2; entered]" in report
    assert "87.5" not in report.split("## Laboratory")[1].split("##")[0]
    assert ok(client.get("/api/audit/verify"))["valid"] is True


def test_invalid_ids(client, case):
    pid = case["pid"]
    assert client.get("/api/patients/9999/timeline").status_code == 404
    assert client.get("/api/records/labs/9999").status_code == 404
    assert client.get("/api/records/not-a-table/1").status_code == 404
    assert client.patch("/api/records/labs/9999", json={"changes": {"value": 1}, "reason": "Fictional"}).status_code == 404
    assert client.patch("/api/labs/9999", json={"source_id": 1, "value": 1, "unit": "U/mL", "reason": "Fictional"}).status_code == 404
    assert client.get("/api/patients/abc/labs").status_code == 422
    assert client.get(f"/api/patients/{pid}/compare", params={"baseline_id": 0, "followup_id": 1}).status_code == 422
    assert client.get(f"/api/patients/{pid}/compare", params={"baseline_id": case["s1"]["id"], "followup_id": 999}).status_code == 422
    assert client.post("/api/labs", json={"patient_id": 999, "source_id": case["src"]["id"], "date": "2026-09-01",
                                          "test": "CA-125", "value": 5, "unit": "U/mL"}).status_code == 404
    assert client.post("/api/measurements", json={"patient_id": pid, "source_id": case["rad1"]["id"], "study_id": 999,
                                                  "lesion_key": "x", "site": "y", "long_axis_mm": 5}).status_code == 422


def test_missing_source_attempts(client, case):
    pid = case["pid"]
    base = {"patient_id": pid, "date": "2026-09-01", "test": "CA-125", "value": 5, "unit": "U/mL"}
    assert client.post("/api/labs", json=base).status_code == 422                          # no source_id
    assert client.post("/api/labs", json={**base, "source_id": None}).status_code == 422
    assert client.post("/api/labs", json={**base, "source_id": 0}).status_code == 422
    assert client.post("/api/labs", json={**base, "source_id": 999}).status_code == 422
    other = ok(client.post("/api/patients", json={"label": "Fictional Case Other"}), 201)
    foreign = ok(client.post("/api/sources", json={"patient_id": other["id"], "label": "Other fictional", "kind": "manual"}), 201)
    assert client.post("/api/labs", json={**base, "source_id": foreign["id"]}).status_code == 422
    # Corrections cannot re-point a record at a missing or foreign source either.
    for bad in (999, foreign["id"]):
        r = client.patch(f"/api/records/labs/{case['lab']['id']}", json={"changes": {"source_id": bad}, "reason": "Fictional test"})
        assert r.status_code == 422
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM labs").fetchone()[0] == 1


def test_mixed_units_are_never_merged_or_converted(client, case):
    pid, sid = case["pid"], case["src"]["id"]
    lab = {"patient_id": pid, "source_id": sid, "date": "2026-09-10", "test": "CA-125"}
    assert client.post("/api/labs", json={**lab, "value": 80, "unit": "u/ml"}).status_code == 422   # spelling
    assert client.post("/api/labs", json={**lab, "value": 80, "unit": "IU/mL"}).status_code == 422  # not accepted
    assert client.post("/api/labs", json={**lab, "value": -1, "unit": "U/mL"}).status_code == 422
    other_unit = ok(client.post("/api/labs", json={**lab, "test": "ca 125", "value": 80, "unit": "kU/L"}), 201)
    series = ok(client.get(f"/api/patients/{pid}/labs/series", params={"test": "CA-125"}))
    assert series["unit"] == "U/mL" and series["available_units"] == ["U/mL", "kU/L"]
    assert [p["id"] for p in series["points"]] == [case["lab"]["id"]]
    assert [p["id"] for p in series["excluded"]] == [other_unit["id"]]
    assert ok(client.get(f"/api/patients/{pid}/labs/series", params={"test": "CA125", "unit": "kU/L"}))["points"][0]["value"] == 80
    # Unregistered tests: any well-formed unit, but no case-only variants of an existing unit.
    ok(client.post("/api/labs", json={**lab, "test": "Fictional marker Z", "value": -2.5, "unit": "mg/dL"}), 201)
    clash = client.post("/api/labs", json={**lab, "test": "fictional marker z", "value": 3, "unit": "mg/dl"})
    assert clash.status_code == 422 and "mg/dL" in clash.json()["detail"]
    assert client.post("/api/labs", json={**lab, "test": "Fictional marker Z", "value": 3, "unit": "12"}).status_code == 422
    nan = client.post("/api/labs", content='{"patient_id": %d, "source_id": %d, "date": "2026-09-10", "test": "CA-125", "value": NaN, "unit": "U/mL"}' % (pid, sid),
                      headers={"Content-Type": "application/json"})
    assert nan.status_code == 422


def test_measurement_value_rules(client, case):
    base = {"patient_id": case["pid"], "source_id": case["rad1"]["id"], "study_id": case["s1"]["id"], "site": "Fictional"}
    for i, bad in enumerate([{"long_axis_mm": 0}, {"long_axis_mm": -4}, {"long_axis_mm": 10, "short_axis_mm": 0},
                             {"long_axis_mm": 10, "short_axis_mm": -1}, {"long_axis_mm": 10, "short_axis_mm": 12},
                             {"long_axis_mm": 1001}, {"long_axis_mm": "abc"}]):
        r = client.post("/api/measurements", json={**base, "lesion_key": f"bad-{i}", **bad})
        assert r.status_code == 422, bad
    assert client.post("/api/measurements", json={**base, "lesion_key": "has space", "long_axis_mm": 5}).status_code == 422
    # A correction that would make short > long is rejected using the merged record.
    r = client.patch(f"/api/records/measurements/{case['m1']['id']}", json={"changes": {"long_axis_mm": 15}, "reason": "Fictional test"})
    assert r.status_code == 422
    r = client.patch(f"/api/records/measurements/{case['m1']['id']}", json={"changes": {"long_axis_mm": 0}, "reason": "Fictional test"})
    assert r.status_code == 422
    ok(client.patch(f"/api/records/measurements/{case['m1']['id']}", json={"changes": {"short_axis_mm": None}, "reason": "Fictional: not reported"}))


def test_dates_and_single_line_fields(client, case):
    pid, sid = case["pid"], case["src"]["id"]
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert client.post("/api/labs", json={"patient_id": pid, "source_id": sid, "date": tomorrow, "test": "CA-125", "value": 5, "unit": "U/mL"}).status_code == 422
    assert client.post("/api/labs", json={"patient_id": pid, "source_id": sid, "date": "2026-02-30", "test": "CA-125", "value": 5, "unit": "U/mL"}).status_code == 422
    assert client.post("/api/labs", json={"patient_id": pid, "source_id": sid, "date": "1850-01-01", "test": "CA-125", "value": 5, "unit": "U/mL"}).status_code == 422
    assert client.post("/api/events", json={"patient_id": pid, "source_id": sid, "date": tomorrow, "kind": "consultation", "title": "Fictional"}).status_code == 422
    ok(client.post("/api/events", json={"patient_id": pid, "source_id": sid, "date": tomorrow, "kind": "plan", "title": "Fictional planned review"}), 201)
    assert client.post("/api/events", json={"patient_id": pid, "source_id": sid, "date": "2026-09-01", "kind": "other", "title": "Line one\n## Injected heading"}).status_code == 422


def test_corrections_for_every_record_type(client, case):
    edits = {
        "sources": (case["src"]["id"], {"label": "Fictional lab printout (corrected)", "origin": "Imaginary Lab 2"}),
        "labs": (case["lab"]["id"], {"date": "2026-08-31", "unit": "kU/L"}),
        "treatments": (case["tx"]["id"], {"cycle": 2}),
        "studies": (case["s1"]["id"], {"study_label": "Fictional CT 1 (relabelled)", "modality": "MRI"}),
        "measurements": (case["m1"]["id"], {"site": "Fictional site B"}),
    }
    event = ok(client.post("/api/events", json={"patient_id": case["pid"], "source_id": case["src"]["id"], "date": "2026-09-02",
                                                "kind": "consultation", "title": "Fictional visit"}), 201)
    edits["events"] = (event["id"], {"title": "Fictional visit (corrected)", "source_id": case["rad1"]["id"]})
    for entity, (record_id, changes) in edits.items():
        before = ok(client.get(f"/api/records/{entity}/{record_id}"))
        after = ok(client.patch(f"/api/records/{entity}/{record_id}", json={"changes": changes, "reason": "Fictional correction"}))
        assert after["revision"] == before["revision"] + 1 and after["uid"] == before["uid"]
        for k, v in changes.items():
            assert after[k] == v
        last = ok(client.get(f"/api/records/{entity}/{record_id}/history"))[-1]
        detail = json.loads(last["detail_json"])
        assert last["action"] == "correct" and set(changes) <= set(detail["changed_fields"])
        assert all(detail["before"][k] == before[k] for k in changes)


def test_correction_guards(client, case):
    lab_id = case["lab"]["id"]
    patch = lambda body: client.patch(f"/api/records/labs/{lab_id}", json=body)
    assert patch({"changes": {"value": 1}}).status_code == 422                               # no reason
    assert patch({"changes": {"value": 1}, "reason": "x"}).status_code == 422                # too short
    assert patch({"changes": {}, "reason": "Fictional"}).status_code == 422
    assert patch({"changes": {"patient_id": 2}, "reason": "Fictional"}).status_code == 422   # immutable field
    assert patch({"changes": {"uid": "x"}, "reason": "Fictional"}).status_code == 422
    assert patch({"changes": {"value": 87.5}, "reason": "Fictional"}).status_code == 422     # no-op
    assert patch({"changes": {"unit": None}, "reason": "Fictional"}).status_code == 422
    assert patch({"changes": {"unit": "u/ml"}, "reason": "Fictional"}).status_code == 422
    with db.connection() as conn:
        assert conn.execute("SELECT revision FROM labs WHERE id=?", (lab_id,)).fetchone()[0] == 1
    # Phase 1 endpoint still works and routes through the same audit path.
    out = ok(client.patch(f"/api/labs/{lab_id}", json={"source_id": case["src"]["id"], "value": 90, "unit": "U/mL", "reason": "Fictional"}))
    assert out["value"] == 90 and out["revision"] == 2


def test_verification_status_and_reset_on_correction(client, case):
    url = f"/api/records/labs/{case['lab']['id']}"
    assert client.post(url + "/status", json={"status": "approved", "reason": "Fictional"}).status_code == 422
    assert ok(client.post(url + "/status", json={"status": "verified", "reason": "Checked against fictional printout"}))["verification_status"] == "verified"
    assert client.post(url + "/status", json={"status": "verified", "reason": "Again"}).status_code == 422
    after = ok(client.patch(url, json={"changes": {"value": 88}, "reason": "Fictional correction"}))
    assert after["verification_status"] == "entered"
    assert [h["action"] for h in ok(client.get(url + "/history"))] == ["create", "status", "correct"]


def test_audit_log_append_only_and_tamper_evident(client, case):
    with db.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_log SET reason='x' WHERE id=1")
    with db.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_log WHERE id=1")
    assert ok(client.get("/api/audit/verify"))["valid"]
    with db.connection() as conn:  # simulate out-of-band tampering by someone who drops the trigger
        conn.execute("DROP TRIGGER audit_log_no_update")
        conn.execute("UPDATE audit_log SET detail_json='{}' WHERE id=3")
    result = ok(client.get("/api/audit/verify"))
    assert result == {"valid": False, "entries_checked": 2, "first_invalid_id": 3}
    assert "FAILED at entry #3" in client.get(f"/api/patients/{case['pid']}/report.md").text


def test_comparison_rules(client, case):
    pid = case["pid"]
    url = f"/api/patients/{pid}/compare"
    reversed_ = client.get(url, params={"baseline_id": case["s2"]["id"], "followup_id": case["s1"]["id"]})
    assert reversed_.status_code == 422 and "swap" in reversed_.json()["detail"]
    assert client.get(url, params={"baseline_id": case["s1"]["id"], "followup_id": case["s1"]["id"]}).status_code == 422
    base = {"patient_id": pid, "source_id": case["rad2"]["id"], "study_id": case["s2"]["id"]}
    ok(client.post("/api/measurements", json={**base, "lesion_key": "demo-L2", "site": "Fictional new", "long_axis_mm": 9}), 201)
    ok(client.patch(f"/api/records/measurements/{case['m2']['id']}", json={"changes": {"site": "Different fictional site"}, "reason": "Fictional"}))
    rows = {r["lesion_key"]: r for r in ok(client.get(url, params={"baseline_id": case["s1"]["id"], "followup_id": case["s2"]["id"]}))["measurements"]}
    assert rows["demo-L2"]["difference_mm"] is None and rows["demo-L2"]["status"] == "only in later study"
    assert rows["demo-L2"]["baseline_mm"] is None
    assert rows["demo-L1"]["site_differs"] is True and rows["demo-L1"]["difference_mm"] == -6.2


def test_number_formatting_and_exact_difference():
    assert format_number(1234567.0) == "1234567"
    assert format_number(0.00012) == "0.00012"
    assert format_number(38.0) == "38"
    assert format_number(38.25) == "38.25"
    assert exact_difference(38.1, 31.2) == -6.9
    assert exact_difference(0.3, 0.1) == -0.2


def test_report_is_fully_traceable(client, case):
    pid = case["pid"]
    ok(client.post("/api/labs", json={"patient_id": pid, "source_id": case["src"]["id"], "date": "2026-09-05",
                                      "test": "Fictional marker Q", "value": 1234567, "unit": "cells/L"}), 201)
    report = client.get(f"/api/patients/{pid}/report.md").text
    assert "1234567 cells/L" in report and "e+06" not in report
    with db.connection() as conn:
        for table, label in (("events", "event"), ("labs", "lab"), ("treatments", "treatment"), ("studies", "study"), ("measurements", "measurement")):
            for r in conn.execute(f"SELECT id, source_id FROM {table} WHERE patient_id=?", (pid,)):
                assert re.search(rf"\[{label} #{r['id']};[^\]]*\] \[source #{r['source_id']}: ", report), (table, r["id"])
        n_sources = conn.execute("SELECT count(*) FROM sources WHERE patient_id=?", (pid,)).fetchone()[0]
    register = report.split("## Source register")[1].split("## Audit")[0]
    assert register.count("- source #") == n_sources
    assert "cited by 3 record(s)" in register  # fictional lab printout: lab, treatment, marker Q


def test_export_json_csv_and_synthetic_guard(client, case):
    pid = case["pid"]
    export = ok(client.get(f"/api/patients/{pid}/export.json"))
    assert export["format"] == "oncoscope-synthetic-export" and export["patient"]["is_synthetic"] == 1
    assert [l["value"] for l in export["labs"]] == [87.5] and export["audit_chain"]["valid"]
    assert len(export["audit_log"]) == 10 and export["documents_included"] is False
    ok(client.post("/api/events", json={"patient_id": pid, "source_id": case["src"]["id"], "date": "2026-09-02",
                                        "kind": "other", "title": "=HYPERLINK(\"http://example.invalid\")"}), 201)
    text = client.get(f"/api/patients/{pid}/export/events.csv").text
    row = next(csv.DictReader(io.StringIO(text)))
    assert row["title"].startswith("'=")
    lab_csv = list(csv.DictReader(io.StringIO(client.get(f"/api/patients/{pid}/export/labs.csv").text)))
    assert lab_csv[0]["value"] == "87.5" and lab_csv[0]["unit"] == "U/mL"
    assert client.get(f"/api/patients/{pid}/export/patients.csv").status_code == 404
    with db.connection() as conn:
        conn.execute("UPDATE patients SET is_synthetic=0 WHERE id=?", (pid,))
    assert client.get(f"/api/patients/{pid}/export.json").status_code == 403
    assert client.get(f"/api/patients/{pid}/export/labs.csv").status_code == 403


def test_patient_creation_is_always_synthetic(client):
    assert client.post("/api/patients", json={"label": "Fictional", "is_synthetic": 0}).status_code == 422
    assert client.post("/api/patients", json={"label": ""}).status_code == 422
    assert ok(client.post("/api/patients", json={"label": "Fictional Case Z"}), 201)["is_synthetic"] == 1


def test_source_document_hash_and_integrity(client, case):
    sid = case["src"]["id"]
    url = f"/api/sources/{sid}/document"
    put = lambda body, name="fictional.pdf", ctype="application/octet-stream": client.put(
        url, params={"filename": name}, content=body, headers={"Content-Type": ctype})
    assert put(PDF, ctype="text/plain").status_code == 415
    assert put(b"<html><script>alert(1)</script></html>", "fictional.html").status_code == 422
    assert put(b"not really a pdf", "fictional.pdf").status_code == 422
    assert put(b"", "fictional.pdf").status_code == 422
    attached = ok(put(PDF, "..\\..\\fictional report.pdf"), 201)
    assert attached["document_filename"] == "fictional report.pdf" and len(attached["document_sha256"]) == 64
    assert put(PDF).status_code == 409   # immutable
    got = client.get(url)
    assert got.content == PDF and got.headers["content-type"] == "application/pdf"
    assert got.headers["x-content-type-options"] == "nosniff" and got.headers["content-security-policy"] == "sandbox"
    assert ok(client.get(url + "/verify"))["valid"] is True
    assert "sha256 `" + attached["document_sha256"] + "`" in client.get(f"/api/patients/{case['pid']}/report.md").text
    # Tamper with the stored bytes: reads are refused, verification reports the mismatch.
    from app.documents import path_for
    path_for(attached["document_sha256"]).write_bytes(PDF + b"tampered")
    assert client.get(url).status_code == 409
    assert ok(client.get(url + "/verify"))["valid"] is False
    assert client.patch(f"/api/records/sources/{sid}", json={"changes": {"document_sha256": "0" * 64}, "reason": "Fictional"}).status_code == 422
    assert client.get(f"/api/sources/{case['rad1']['id']}/document").status_code == 404


def test_non_loopback_host_rejected(client):
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400
    assert client.get("/api/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_backup_is_non_destructive(client, case, tmp_path):
    first = backup(tmp_path / "backups")
    second = backup(tmp_path / "backups")
    assert first != second and first.exists()
    copy = sqlite3.connect(first / "oncoscope.sqlite3")
    assert copy.execute("SELECT count(*) FROM labs").fetchone()[0] == 1
    copy.close()


def test_phase1_database_migrates_in_place(tmp_path, monkeypatch):
    path = tmp_path / "phase1.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript(db.PHASE1_SCHEMA)
    legacy.execute("INSERT INTO patients(label,is_synthetic) VALUES('Fictional legacy case',1)")
    legacy.execute("INSERT INTO sources(patient_id,label,kind) VALUES(1,'Fictional legacy source','lab')")
    legacy.execute("INSERT INTO labs(patient_id,date,test,value,unit,source_id) VALUES(1,'2026-07-01','CA-125',42,'U/mL',1)")
    legacy.execute("INSERT INTO audit_log(entity_type,entity_id,action,detail_json) VALUES('labs',1,'create','{}')")
    legacy.execute("INSERT INTO audit_log(entity_type,entity_id,action,detail_json) VALUES('labs',1,'correct','{\"reason\": \"legacy fix\"}')")
    legacy.commit()
    legacy.close()
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(path))
    db.init_db()
    db.init_db()  # idempotent
    with db.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")] == list(range(1, db.SCHEMA_VERSION + 1))
        lab = dict(conn.execute("SELECT * FROM labs").fetchone())
        assert (lab["value"], lab["unit"], lab["revision"], lab["verification_status"]) == (42, "U/mL", 1, "entered")
        assert len(lab["uid"]) == 36
        audit_rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id")]
        assert all(r["patient_id"] == 1 for r in audit_rows) and audit_rows[1]["reason"] == "legacy fix"
        assert db.verify_audit_chain(conn)["valid"]
    with TestClient(app) as c:
        assert c.get("/api/patients/1/labs").json()[0]["value"] == 42


def test_newer_schema_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "future.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(path))
    with pytest.raises(RuntimeError, match="newer"):
        db.init_db()
