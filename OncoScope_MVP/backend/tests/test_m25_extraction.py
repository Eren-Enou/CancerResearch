"""CA-125 extraction pipeline and review queue. Fictional fixture documents only."""
import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.extraction import PARSER_VERSION, extract_ca125, parse_date
from app.main import app
from conftest import FIXTURES, new_source


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "extract.sqlite3"))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pid(client):
    return client.post("/api/patients", json={"label": "Fictional Case M25"}).json()["id"]


def run(client, source_id):
    res = client.post(f"/api/sources/{source_id}/extract")
    assert res.status_code == 201, res.text
    return res.json()


def codes(proposal):
    return [i["code"] for i in proposal["issues"]]


# --- Parser rules (pure) -------------------------------------------------------------------------

def test_parser_reports_instead_of_guessing():
    text = (FIXTURES / "lab_2026-10_ambiguous_fax.txt").read_text(encoding="utf-8")
    first, repeat, pending = extract_ca125([text])
    assert first.fields["value"] == 188 and first.fields["date"] is None and [i["code"] for i in first.issues] == ["date_ambiguous"]
    assert "2026-09-10" in first.issues[0]["message"] and "2026-10-09" in first.issues[0]["message"]
    assert repeat.fields["value"] is None and {"value_qualifier", "line_qualifier"} <= {i["code"] for i in repeat.issues}
    assert pending.fields["value"] is None and "value_not_found" in [i["code"] for i in pending.issues]
    assert text[first.start:first.end] == first.original_text


@pytest.mark.parametrize("line, value, unit, issue", [
    ("CA-125 87.5 U/mL", 87.5, "U/mL", None),
    ("CA125=40 kU/L", 40, "kU/L", None),
    ("ca 125: 12 u/ml", 12, None, "unit_not_accepted"),
    ("CA-125 1,234 U/mL", None, "U/mL", "value_separator"),
    ("CA-125 87.5", 87.5, None, "unit_missing"),
    ("CA-125 >1000 U/mL", None, "U/mL", "value_qualifier"),
    ("CA-125 not done", None, None, "value_not_found"),
    ("CA-125 87.5 U/mL 2026-09-01 2026-09-02", 87.5, "U/mL", "date_ambiguous"),
])
def test_parser_line_rules(line, value, unit, issue):
    (p,) = extract_ca125([f"Collection date: 2026-09-01\n{line}\n"])
    assert (p.fields["value"], p.fields["unit"]) == (value, unit)
    found = [i["code"] for i in p.issues]
    assert (issue in found) if issue else found == []


def test_parser_dates():
    assert parse_date("18 Sep 2026")[0][0].isoformat() == "2026-09-18"
    assert parse_date("September 18, 2026")[0][0].isoformat() == "2026-09-18"
    assert len(parse_date("09/10/2026")[0]) == 2       # ambiguous
    assert len(parse_date("18/09/2026")[0]) == 1       # only one valid reading
    assert parse_date("31/02/2026")[0] == []
    assert parse_date("Smarch 3 2026")[0] == []
    (p,) = extract_ca125(["Report date: 2026-09-01\nCollection date: 2026-09-05\nCA-125 10 U/mL\n"], source_date="2026-09-02")
    assert {"date_after_report", "date_after_source"} <= {i["code"] for i in p.issues}
    (p,) = extract_ca125(["CA-125 10 U/mL\n"])
    assert [i["code"] for i in p.issues] == ["date_missing"]


# --- Review queue (API) ---------------------------------------------------------------------------

def test_extraction_queue_and_acceptance(client, pid):
    src = new_source(client, pid, "Fictional lab TXT", document="lab_2026-08-07_imaginary_valley.txt", source_date="2026-08-08")
    result = run(client, src["id"])
    assert len(result["created"]) == 1 and result["existing"] == []
    proposal = result["created"][0]
    assert proposal["status"] == "pending" and proposal["parser_version"] == PARSER_VERSION
    assert proposal["proposed"] == {"test": "CA-125", "value": 310.0, "unit": "U/mL", "date": "2026-08-07",
                                    "date_basis": "labelled collection date '2026-08-07'"}
    assert proposal["original_text"] == "CA-125          310      U/mL" and proposal["document_sha256"] == src["document_sha256"]
    # No record exists until a person decides; re-running creates nothing new.
    assert client.get(f"/api/patients/{pid}/labs").json() == []
    again = run(client, src["id"])
    assert again["created"] == [] and [e["id"] for e in again["existing"]] == [proposal["id"]]

    accepted = client.post(f"/api/extractions/{proposal['id']}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    record = accepted.json()["record"]
    assert (record["provenance"], record["verification_status"], record["extraction_id"]) == ("extracted", "reviewed", proposal["id"])
    text = client.get(f"/api/sources/{src['id']}/document/text").json()["pages"][0]["text"]
    assert text[record["anchor_start"]:record["anchor_end"]] == proposal["original_text"]
    assert client.post(f"/api/extractions/{proposal['id']}/accept", json={}).status_code == 409
    assert client.post(f"/api/extractions/{proposal['id']}/reject", json={"reason": "Too late"}).status_code == 409
    history = [h for h in client.get(f"/api/patients/{pid}/audit").json() if h["entity_type"] == "extractions"]
    assert [h["action"] for h in history] == ["accept", "create"]
    # Verification remains a separate, explicit step.
    status = client.post(f"/api/records/labs/{record['id']}/status", json={"status": "verified", "reason": "Compared with fictional printout"})
    assert status.json()["verification_status"] == "verified"


def test_issues_require_completion_and_reason(client, pid):
    src = new_source(client, pid, "Fictional fax", document="lab_2026-10_ambiguous_fax.txt")
    first, repeat, pending = run(client, src["id"])["created"]
    assert codes(first) == ["date_ambiguous"]
    url = f"/api/extractions/{first['id']}/accept"
    assert "reason is required" in client.post(url, json={}).text
    assert "missing field(s) before accepting: date" in client.post(url, json={"reason": "Checked with fictional sender"}).text
    res = client.post(url, json={"changes": {"date": "2026-09-10"}, "reason": "Sender confirmed day-first date (fictional)"})
    assert res.status_code == 200, res.text
    entry = next(h for h in client.get(f"/api/patients/{pid}/audit").json() if h["action"] == "accept")
    detail = json.loads(entry["detail_json"])
    assert detail["proposed"]["date"] is None and detail["accepted"]["date"] == "2026-09-10"
    assert detail["changed_fields"] == ["date"] and detail["issues"][0]["code"] == "date_ambiguous"
    assert entry["reason"] == "Sender confirmed day-first date (fictional)"
    # A bound such as '<5' is never turned into a number; the reviewer rejects it with a reason.
    assert client.post(f"/api/extractions/{repeat['id']}/reject", json={}).status_code == 422
    rejected = client.post(f"/api/extractions/{repeat['id']}/reject", json={"reason": "Censored value; not representable"}).json()
    assert rejected["status"] == "rejected" and rejected["record_id"] is None
    assert client.get(f"/api/patients/{pid}/extractions", params={"status": "pending"}).json()[0]["id"] == pending["id"]
    assert len(client.get(f"/api/patients/{pid}/labs").json()) == 1


def test_malformed_acceptance_inputs(client, pid):
    src = new_source(client, pid, "Fictional portal copy", document="lab_2026-09-18_portal_copy.txt")
    (proposal,) = run(client, src["id"])["created"]
    url = f"/api/extractions/{proposal['id']}/accept"
    bad = [
        {"changes": {"value": "abc"}, "reason": "Fictional"},
        {"changes": {"value": -3}, "reason": "Fictional"},
        {"changes": {"unit": "u/ml"}, "reason": "Fictional"},
        {"changes": {"date": "2026-13-01"}, "reason": "Fictional"},
        {"changes": {"date": "2999-01-01"}, "reason": "Fictional"},
        {"changes": {"source_id": 1}, "reason": "Fictional"},
        {"changes": {"value": 221}},
        {"changes": {"value": 221}, "reason": "x"},
    ]
    for body in bad:
        assert client.post(url, json=body).status_code == 422, body
    assert client.post("/api/extractions/999/accept", json={}).status_code == 404
    # A proposal whose stored passage no longer matches the document is refused, not trusted.
    with db.connection() as conn:
        conn.execute("UPDATE extractions SET original_text='CA-125: 230 U/mL' WHERE id=?", (proposal["id"],))
    res = client.post(url, json={})
    assert res.status_code == 422 and "does not match" in res.text
    assert client.get(f"/api/patients/{pid}/labs").json() == []


def test_duplicates_mixed_units_and_ownership(client, pid):
    src = new_source(client, pid, "Fictional lab TXT", document="lab_2026-08-07_imaginary_valley.txt")
    manual = client.post("/api/labs", json={"patient_id": pid, "source_id": src["id"], "date": "2026-08-07", "test": "CA-125",
                                            "value": 310, "unit": "U/mL"})
    assert manual.status_code == 201
    (proposal,) = run(client, src["id"])["created"]
    dup = client.post(f"/api/extractions/{proposal['id']}/accept", json={})
    assert dup.status_code == 409 and f"lab #{manual.json()['id']}" in dup.text
    mixed = client.post(f"/api/extractions/{proposal['id']}/accept", json={"changes": {"unit": "kU/L"}, "reason": "Fictional unit check"})
    assert mixed.status_code == 200 and mixed.json()["record"]["unit"] == "kU/L"
    series = client.get(f"/api/patients/{pid}/labs/series", params={"test": "CA-125"}).json()
    assert len(series["points"]) == 1 and len(series["excluded"]) == 1
    # Extraction belongs to the source's case; no cross-case acceptance path exists.
    other = client.post("/api/patients", json={"label": "Fictional Case Other"}).json()["id"]
    assert client.get(f"/api/patients/{other}/extractions").json() == []


def test_unsupported_documents_for_extraction(client, pid):
    img = new_source(client, pid, "Fictional scan", document="note_2026-09-25_scanned_placeholder.png")
    res = client.post(f"/api/sources/{img['id']}/extract")
    assert res.status_code == 422 and "no text layer" in res.text
    bare = new_source(client, pid, "Metadata only")
    assert client.post(f"/api/sources/{bare['id']}/extract").status_code == 422
    assert client.post("/api/sources/999/extract").status_code == 404
    ct = new_source(client, pid, "Fictional CT", kind="radiology", document="ct_2026-08-10_baseline.txt")
    assert run(client, ct["id"])["created"] == []   # radiology text has no CA-125 lines: nothing proposed
