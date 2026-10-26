"""M2.5 evidence anchors, provenance and document text. Fictional fixture documents only."""
import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from conftest import attach, fixture_bytes, new_source


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "m25.sqlite3"))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pid(client):
    return client.post("/api/patients", json={"label": "Fictional Case M25"}).json()["id"]


def lab(client, pid, source_id, **extra):
    body = {"patient_id": pid, "source_id": source_id, "date": "2026-08-07", "test": "CA-125", "value": 310, "unit": "U/mL", **extra}
    return client.post("/api/labs", json=body)


def test_document_text_is_deterministic_and_original_unchanged(client, pid):
    src = new_source(client, pid, "Fictional lab PDF", document="lab_2026-09-18_imaginary_valley.pdf")
    text = client.get(f"/api/sources/{src['id']}/document/text").json()
    assert text["extractor"].startswith("pypdf-") and text["has_text_layer"]
    assert "CA-125   202   U/mL" in text["pages"][0]["text"]
    assert client.get(f"/api/sources/{src['id']}/document/text").json() == text   # cached, identical
    original = fixture_bytes("lab_2026-09-18_imaginary_valley.pdf")
    assert client.get(f"/api/sources/{src['id']}/document").content == original
    assert src["document_sha256"] == hashlib.sha256(original).hexdigest()
    ct = new_source(client, pid, "Fictional CT PDF", kind="radiology", document="ct_2026-10-12_followup.pdf")
    pages = client.get(f"/api/sources/{ct['id']}/document/text").json()["pages"]
    assert len(pages) == 2 and "31 x 18 mm" in pages[1]["text"]
    img = new_source(client, pid, "Fictional scan", kind="other", document="note_2026-09-25_scanned_placeholder.png")
    assert client.get(f"/api/sources/{img['id']}/document/text").json() == {
        "source_id": img["id"], "document_sha256": img["document_sha256"], "media_type": "image/png",
        "filename": "note_2026-09-25_scanned_placeholder.png", "extractor": None, "pages": [], "has_text_layer": False}


def test_quote_anchor_resolved_to_exact_offsets(client, pid):
    src = new_source(client, pid, "Fictional lab TXT", document="lab_2026-08-07_imaginary_valley.txt")
    res = lab(client, pid, src["id"], anchor_quote="CA-125          310      U/mL")
    assert res.status_code == 201, res.text
    rec = res.json()
    page_text = client.get(f"/api/sources/{src['id']}/document/text").json()["pages"][0]["text"]
    assert rec["anchor_page"] == 1 and page_text[rec["anchor_start"]:rec["anchor_end"]] == rec["anchor_quote"]
    assert rec["provenance"] == "transcribed"


def test_anchor_rules(client, pid):
    txt = new_source(client, pid, "Fictional lab TXT", document="lab_2026-08-07_imaginary_valley.txt")
    ct = new_source(client, pid, "Fictional CT PDF", kind="radiology", document="ct_2026-10-12_followup.pdf")
    img = new_source(client, pid, "Fictional scan", kind="other", document="note_2026-09-25_scanned_placeholder.png")
    bare = new_source(client, pid, "Metadata-only fictional source")
    cases = [
        (txt, {"anchor_quote": "CA-125  999 U/mL"}, "does not occur"),
        (txt, {"anchor_quote": "2026-08-0"}, "occurs 2 times"),   # report date and collection date
        (txt, {"anchor_quote": "CA-125", "anchor_page": 2}, "Page 2 does not exist"),
        (txt, {"anchor_quote": "CA-125", "anchor_page": 1, "anchor_start": 0, "anchor_end": 6}, "does not match"),
        (txt, {"anchor_region": "10,10,20,5"}, "not by region"),
        (img, {"anchor_quote": "CA-125"}, "no text layer"),
        (img, {"anchor_region": "90,10,20,5"}, "within 0–100%"),
        (img, {"anchor_region": "ten,10,20,5"}, "x,y,w,h"),
        (bare, {"anchor_quote": "CA-125"}, "Attach the document"),
        (txt, {"provenance": "user_observation", "anchor_quote": "CA-125"}, "cannot carry a document anchor"),
        (txt, {"provenance": "extracted"}, "review-queue"),
        (txt, {"anchor_start": 3}, "together"),
    ]
    for source, extra, message in cases:
        res = lab(client, pid, source["id"], **extra)
        assert res.status_code == 422, (extra, res.text)
        assert message in json.dumps(res.json(), ensure_ascii=False), (extra, res.json())
    ok_region = lab(client, pid, img["id"], anchor_region="10,37.5,80,15")
    assert ok_region.status_code == 201 and ok_region.json()["anchor_region"] == "10,37.5,80,15"
    ok_page = client.post("/api/measurements", json={"patient_id": pid, "source_id": ct["id"],
        "study_id": client.post("/api/studies", json={"patient_id": pid, "source_id": ct["id"], "date": "2026-10-12", "modality": "CT",
                                                       "study_label": "Fictional CT", "anchor_quote": "Study date: 2026-10-12"}).json()["id"],
        "lesion_key": "reference-A", "site": "Fictional", "long_axis_mm": 31, "short_axis_mm": 18,
        "anchor_quote": "measures 31 x 18 mm"})
    assert ok_page.status_code == 201 and ok_page.json()["anchor_page"] == 2
    user = lab(client, pid, bare["id"], provenance="user_observation")
    assert user.status_code == 201 and user.json()["provenance"] == "user_observation"


def test_anchor_corrections_are_audited_and_relocated(client, pid):
    txt = new_source(client, pid, "Fictional lab TXT", document="lab_2026-08-07_imaginary_valley.txt")
    other = new_source(client, pid, "Fictional portal copy", document="lab_2026-09-18_portal_copy.txt")
    rec = lab(client, pid, txt["id"], anchor_quote="CA-125          310      U/mL").json()
    # Moving the record to another source with a quote absent there is refused (stale offsets are not trusted).
    res = client.patch(f"/api/records/labs/{rec['id']}", json={"changes": {"source_id": other["id"]}, "reason": "Fictional"})
    assert res.status_code == 422 and "does not occur" in res.text
    res = client.patch(f"/api/records/labs/{rec['id']}", json={"changes": {"source_id": other["id"], "anchor_quote": "CA-125: 220 U/mL",
                                                                          "value": 220, "date": "2026-09-18"}, "reason": "Linked to wrong document"})
    assert res.status_code == 200, res.text
    after = res.json()
    other_text = client.get(f"/api/sources/{other['id']}/document/text").json()["pages"][0]["text"]
    assert other_text[after["anchor_start"]:after["anchor_end"]] == "CA-125: 220 U/mL"
    detail = json.loads(client.get(f"/api/records/labs/{rec['id']}/history").json()[-1]["detail_json"])
    assert {"anchor_quote", "anchor_start", "anchor_end", "source_id", "value", "date"} <= set(detail["changed_fields"])
    assert detail["before"]["anchor_quote"] == "CA-125          310      U/mL"
    # Clearing an anchor is allowed and audited; provenance of a transcription can become a user observation.
    res = client.patch(f"/api/records/labs/{rec['id']}", json={"changes": {"anchor_quote": None, "anchor_page": None, "anchor_start": None,
                                                                          "anchor_end": None, "provenance": "user_observation"}, "reason": "Fictional"})
    assert res.status_code == 200 and res.json()["anchor_start"] is None
    assert client.patch(f"/api/records/labs/{rec['id']}", json={"changes": {"provenance": "extracted"}, "reason": "Fictional"}).status_code == 422


def test_duplicate_import_and_unsupported_documents(client, pid):
    first = new_source(client, pid, "Fictional lab TXT", document="lab_2026-08-07_imaginary_valley.txt")
    second = new_source(client, pid, "Same file again")
    dup = attach(client, second["id"], "lab_2026-08-07_imaginary_valley.txt")
    assert dup.status_code == 409 and f"source #{first['id']}" in dup.json()["detail"]
    other_case = client.post("/api/patients", json={"label": "Fictional Case Other"}).json()["id"]
    elsewhere = new_source(client, other_case, "Other case, same file")
    assert attach(client, elsewhere["id"], "lab_2026-08-07_imaginary_valley.txt").status_code == 201   # cases are independent
    put = lambda name, body: client.put(f"/api/sources/{second['id']}/document", content=body,
                                        headers={"Content-Type": "application/octet-stream", "X-Document-Filename": name})
    assert put("scan.dcm", b"DICM" * 10).status_code == 422
    assert put("page.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>").status_code == 422
    assert put("broken.pdf", b"%PDF-1.4 truncated nonsense").status_code == 201    # signature ok, text layer unreadable
    res = client.get(f"/api/sources/{second['id']}/document/text")
    assert res.status_code == 422 and "PDF text layer could not be read" in res.text
    # Cross-case anchoring: a source of another case is refused before any document lookup.
    assert lab(client, pid, elsewhere["id"], anchor_quote="CA-125").status_code == 422


def test_v2_database_upgrades_to_v3(tmp_path, monkeypatch):
    path = tmp_path / "v2.sqlite3"
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(path))
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    db.migrate(conn, up_to=2)
    conn.execute("BEGIN")
    db.insert_record(conn, "patients", {"label": "Fictional v2 case", "is_synthetic": 1})
    db.insert_record(conn, "sources", {"patient_id": 1, "label": "Fictional v2 source", "kind": "lab"})
    db.insert_record(conn, "labs", {"patient_id": 1, "source_id": 1, "date": "2026-07-01", "test": "CA-125", "value": 42, "unit": "U/mL"})
    conn.execute("COMMIT")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    conn.close()
    db.init_db()
    with db.connection() as c:
        lab_row = dict(c.execute("SELECT * FROM labs").fetchone())
        assert (lab_row["value"], lab_row["provenance"], lab_row["anchor_quote"], lab_row["revision"]) == (42, "transcribed", None, 1)
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert db.verify_audit_chain(c) == {"valid": True, "entries_checked": 3, "first_invalid_id": None}
