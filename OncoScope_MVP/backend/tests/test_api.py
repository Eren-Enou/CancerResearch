import json
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.seed import seed
from app.db import connection


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "demo.sqlite3"))
    with TestClient(app) as c:
        seed()
        yield c


def test_seed_idempotent_and_has_provenance(client):
    seed()
    p = client.get("/api/patients").json()
    assert len(p) == 1 and p[0]["is_synthetic"] == 1
    timeline = client.get("/api/patients/1/timeline").json()
    source_ids = {r["id"] for r in client.get("/api/patients/1/sources").json()}
    assert len(timeline) >= 10
    assert all(row["source_id"] in source_ids for row in timeline)


def test_comparison_only_descriptive(client):
    studies = client.get("/api/patients/1/studies").json()
    earlier, later = sorted(studies, key=lambda x: x["date"])
    res = client.get("/api/patients/1/compare", params={"baseline_id": earlier["id"], "followup_id": later["id"]})
    assert res.status_code == 200
    comparison = res.json()
    assert comparison["measurements"][0]["difference_mm"] == -7.0
    assert "No RECIST assessment" in comparison["interpretation"]


def test_cross_patient_source_rejected(client):
    res = client.post("/api/labs", json={"patient_id": 1, "source_id": 999, "date": "2026-10-20", "test": "CA-125", "value": 123, "unit": "U/mL"})
    assert res.status_code == 422


def test_lab_correction_preserves_audit(client):
    lab = client.get("/api/patients/1/labs").json()[0]
    change = {"source_id": lab["source_id"], "value": 301, "unit": "U/mL", "reason": "Corrected from source record"}
    result = client.patch(f"/api/labs/{lab['id']}", json=change)
    assert result.status_code == 200 and result.json()["value"] == 301
    audit = client.get("/api/patients/1/audit").json()
    correction = next(x for x in audit if x["action"] == "correct")
    detail = json.loads(correction["detail_json"])
    assert detail["before"]["value"] == 310
    assert detail["after"]["value"] == 301


def test_report_and_validation(client):
    report = client.get("/api/patients/1/report.md")
    assert report.status_code == 200
    assert "Synthetic data: YES" in report.text
    assert "source #" in report.text
    assert "Recorded lesion measurements" in report.text
    assert "reference-A" in report.text
    bad = client.post("/api/measurements", json={"patient_id": 1, "source_id": 1, "study_id": 1, "lesion_key": "x", "site": "demo", "long_axis_mm": -2})
    assert bad.status_code == 422


def test_new_record_and_missing_patient(client):
    src = client.post("/api/sources", json={"patient_id": 1, "label": "Synthetic new note", "kind": "manual", "source_date": "2026-10-25"})
    assert src.status_code == 201
    event = client.post("/api/events", json={"patient_id": 1, "source_id": src.json()["id"], "date": "2026-10-25", "kind": "other", "title": "Example event"})
    assert event.status_code == 201
    assert client.get("/api/patients/404/dashboard").status_code == 404


def test_source_cannot_be_reused_across_patients(client):
    with connection() as conn:
        second = conn.execute("INSERT INTO patients(label,is_synthetic) VALUES('Synthetic Case B',1)").lastrowid
    source = client.get("/api/patients/1/sources").json()[0]["id"]
    result = client.post("/api/events", json={
        "patient_id": second, "source_id": source, "date": "2026-10-25",
        "kind": "other", "title": "Unauthorized source crossover",
    })
    assert result.status_code == 422


def test_measurement_invalid_study_and_duplicate_key(client):
    source = client.get("/api/patients/1/sources").json()[0]["id"]
    template = {"patient_id": 1, "source_id": source, "lesion_key": "new-demo", "site": "Fictional", "long_axis_mm": 10}
    bad_study = client.post("/api/measurements", json={**template, "study_id": 999})
    assert bad_study.status_code == 422
    study = client.get("/api/patients/1/studies").json()[0]["id"]
    assert client.post("/api/measurements", json={**template, "study_id": study}).status_code == 201
    assert client.post("/api/measurements", json={**template, "study_id": study}).status_code == 409
