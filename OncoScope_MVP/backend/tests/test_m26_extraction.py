"""Parser v2 layout rules, cross-version idempotency, and the benchmark gates."""
import json

import pytest
from fastapi.testclient import TestClient

from app import benchmark, db
from app.extraction import extract_ca125
from app.main import app
from conftest import new_source


def one(text: str, source_date=None):
    (p,) = extract_ca125([text], source_date)
    return p, [i["code"] for i in p.issues]


@pytest.mark.parametrize("text, value, unit, date_, issues", [
    ("Collected: 2026-08-28 07:45\n| CA-125 | 288 | U/mL | 0-35 |\n", 288, "U/mL", "2026-08-28", []),
    ("Specimen collected: 18 Sep 2026\nCancer antigen 125   202     H     U/mL    <35\n", 202, "U/mL", "2026-09-18", []),
    ("2026-08-07  CA-125  310  U/mL\n", 310, "U/mL", "2026-08-07", []),
    ("Collection date: 2026-08-14\nCA-125 (kU/L)   300\n", 300, "kU/L", "2026-08-14", []),
    ("Collected: 25.09.2026\nCA-125   190   U/mL\n", 190, "U/mL", "2026-09-25", []),
    ("Collected 2026-10-20\nCA-125 .......... 95\n", 95, None, "2026-10-20", ["unit_missing"]),
    ("Collected: 2026-09-18\nCA-125: 202 U/mL (previously reported as 220 U/mL)\n", 202, "U/mL", "2026-09-18", ["multiple_values"]),
    ("Collection date: 2026-07-30\nAnalyte: CA-125\nResult: 512 U/mL\n", 512, "U/mL", "2026-07-30", ["value_on_next_line"]),
    ("Report date: 2026-09-05\nCA-125   133   U/mL\n", 133, "U/mL", None, ["date_missing"]),
    ("Collection date: 2026-08-01\nCA-125 result: >1000 U/mL (above reportable range)\n", None, "U/mL", "2026-08-01", ["value_qualifier"]),
    ("Plan: repeat CA-125 in three weeks.\n", None, None, None, ["no_result_on_line", "date_missing"]),
])
def test_v2_layout_rules(text, value, unit, date_, issues):
    p, codes = one(text)
    assert (p.fields["value"], p.fields["unit"], p.fields["date"]) == (value, unit, date_)
    assert codes == issues
    page_start = text.find(p.original_text)
    assert text[p.start:p.end] == p.original_text and page_start == p.start


def test_qualifier_and_flag_are_kept_as_written():
    p, codes = one("Collected: 2026-10-20\nCA-125   <5   U/mL\n")
    assert p.fields["qualifier"] == "<5" and p.fields["value"] is None and codes == ["value_qualifier"]
    p, _ = one("Collected: 2026-10-20\nCA-125 202 H U/mL\n")
    assert p.fields["flag_as_written"] == "H"      # copied from the document, never interpreted


def test_report_date_message_is_explicit():
    p, _ = one("Report date: 2026-09-05\nCA-125 133 U/mL\n")
    assert "report date '2026-09-05' is not used as the collection date" in p.issues[0]["message"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "m26.sqlite3"))
    with TestClient(app) as c:
        yield c


def test_new_parser_version_does_not_duplicate_pending_proposals(client):
    pid = client.post("/api/patients", json={"label": "Fictional Case M26"}).json()["id"]
    src = new_source(client, pid, "Fictional lab", document="lab_2026-08-07_imaginary_valley.txt")
    (first,) = client.post(f"/api/sources/{src['id']}/extract").json()["created"]
    with db.connection() as conn:   # simulate a proposal left over from parser v1
        conn.execute("UPDATE extractions SET parser_version='1' WHERE id=?", (first["id"],))
    again = client.post(f"/api/sources/{src['id']}/extract").json()
    assert again["created"] == [] and again["existing"][0]["id"] == first["id"]


def test_benchmark_gates_pass_and_report_is_complete():
    result = benchmark.run()
    assert result["passed"], json.dumps(result["gates"])
    assert result["pipeline"]["silent_incorrect"] == 0 and result["system"]["accepted_incorrect"] == []
    assert result["system"]["proposals_second_run"] == 0
    assert set(result["system"]["unsupported_documents"]) == {"B20", "B21"}      # failures stay visible
    assert result["pipeline"]["anchor_accuracy"] == 1.0
    md = benchmark.to_markdown(result)
    assert "## Every non-ideal outcome" in md and "Results by layout" in md
