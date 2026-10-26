"""M3.0 security regression suite. Synthetic, locally constructed data only.

Two kinds of test:
- invariants that hold today and must keep holding;
- known gaps, marked `xfail(strict=True)`: they document a confirmed weakness and FAIL the suite as soon as the
  weakness is fixed, so the marker (and docs/M3_THREAT_MODEL.md) must then be updated.
"""
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import zlib
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db
from app.backup import backup, verify_backup
from app.main import app
from app.restore import RestoreError, restore
from conftest import attach, new_source

REPO = Path(__file__).resolve().parents[3]
APP = Path(__file__).resolve().parents[1] / "app"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "live" / "oncoscope.sqlite3"))
    monkeypatch.delenv("ONCOSCOPE_DOCUMENT_DIR", raising=False)
    with TestClient(app) as c:
        yield c


def case(client, label="Fictional Case SEC"):
    pid = client.post("/api/patients", json={"label": label}).json()["id"]
    src = new_source(client, pid, f"{label} lab", document="lab_2026-08-07_imaginary_valley.txt", source_date="2026-08-08")
    lab = client.post("/api/labs", json={"patient_id": pid, "source_id": src["id"], "date": "2026-08-07", "test": "CA-125",
                                         "value": 310, "unit": "U/mL", "anchor_quote": "CA-125          310      U/mL"}).json()
    return pid, src, lab


# --- Network exposure and browser-origin attacks ---------------------------------------------------------

def test_host_header_must_be_loopback(client):
    for host in ("attacker.example", "127.0.0.1.attacker.example", "localhost.attacker.example", "0.0.0.0", "[::]", "192.168.1.10"):
        assert client.get("/api/health", headers={"Host": host}).status_code == 400, host
    for host in ("127.0.0.1:8000", "localhost:5173"):
        assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_cross_origin_requests_get_no_cors_grant(client):
    pre = client.options("/api/patients", headers={"Origin": "https://attacker.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in pre.headers
    got = client.get("/api/patients", headers={"Origin": "https://attacker.example"})
    assert "access-control-allow-origin" not in got.headers


def test_simple_cross_site_posts_are_rejected(client):
    """A web page can send text/plain or form posts without a preflight; none of them may change data."""
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
        r = client.post("/api/patients", content='{"label": "Fictional injected"}', headers={"Content-Type": ctype})
        assert r.status_code in (415, 422), ctype
    pid, src, _ = case(client)
    r = client.put(f"/api/sources/{src['id']}/document", content=b"FICTIONAL", headers={"Content-Type": "text/plain", "X-Document-Filename": "a.txt"})
    assert r.status_code == 415
    assert [p["label"] for p in client.get("/api/patients").json()] == ["Fictional Case SEC"]


def test_api_responses_are_not_cacheable(client):
    pid, src, _ = case(client)
    for path in (f"/api/patients/{pid}/report.md", f"/api/patients/{pid}/export.json", f"/api/sources/{src['id']}/document",
                 f"/api/patients/{pid}/labs", "/api/patients"):
        r = client.get(path)
        assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff", path


def test_binds_only_to_loopback_everywhere_configured():
    configs = {
        FRONTEND / "vite.config.ts": r"host:\s*'127\.0\.0\.1'",
        FRONTEND / "package.json": r"vite --host 127\.0\.0\.1",
        FRONTEND / "playwright.config.ts": r"--host 127\.0\.0\.1",
    }
    for path, pattern in configs.items():
        assert re.search(pattern, path.read_text(encoding="utf-8")), path
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    for rel in tracked:
        # Code and configuration only; documentation may discuss 0.0.0.0 as something to refuse.
        if rel.endswith((".py", ".ts", ".tsx", ".json", ".yml")) and "node_modules" not in rel:
            text = (REPO / rel).read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if "0.0.0.0" in line:
                    assert "test_security.py" in rel or "not `0.0.0.0`" in line or "not 0.0.0.0" in line or '"0.0.0.0"' in line, (rel, line)


def test_application_code_makes_no_outbound_network_calls():
    outbound = re.compile(r"^\s*(import|from)\s+(requests|httpx|urllib\.request|http\.client|socket|aiohttp|smtplib|ftplib)\b", re.M)
    for path in APP.glob("*.py"):
        assert not outbound.search(path.read_text(encoding="utf-8")), path
    external = re.compile(r"https?://(?!127\.0\.0\.1|localhost)[A-Za-z0-9.-]+")
    for path in (FRONTEND / "src").rglob("*.ts*"):
        if ".test." in path.name:
            continue
        hits = [m for m in external.findall(path.read_text(encoding="utf-8"))]
        assert not hits, (path, hits)


# --- Logs and diagnostic output -------------------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_server_logs_contain_no_record_content(tmp_path):
    """Runs the real uvicorn process and searches everything it prints for distinctive fictional values."""
    port, log_path = _free_port(), tmp_path / "server.log"
    env = {**os.environ, "ONCOSCOPE_DB_PATH": str(tmp_path / "logcheck.sqlite3")}
    env.pop("ONCOSCOPE_DOCUMENT_DIR", None)
    with open(log_path, "w") as log:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                cwd=APP.parent, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            c = httpx.Client(base_url=f"http://127.0.0.1:{port}/api")
            for _ in range(100):
                try:
                    c.get("/health")
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            pid = c.post("/patients", json={"label": "Fictional SENTINEL-CASE-LABEL"}).json()["id"]
            src = c.post("/sources", json={"patient_id": pid, "label": "SENTINEL-SOURCE-LABEL", "kind": "lab"}).json()["id"]
            c.put(f"/sources/{src}/document", content=b"FICTIONAL SENTINEL-DOCUMENT-TEXT CA-125 1 U/mL",
                  headers={"Content-Type": "application/octet-stream", "X-Document-Filename": "SENTINEL-FILENAME.txt"})
            c.post("/labs", json={"patient_id": pid, "source_id": src, "date": "2026-08-07", "test": "SENTINEL-TEST-NAME",
                                  "value": 4242.5, "unit": "U/mL"})
            c.post("/labs", json={"patient_id": pid, "source_id": src, "date": "not-a-date", "test": "SENTINEL-BAD-INPUT", "value": 1, "unit": "U/mL"})
            for path in (f"/patients/{pid}/report.md", f"/patients/{pid}/export.json", f"/sources/{src}/document",
                         f"/sources/{src}/document/text", f"/patients/{pid}/timeline"):
                c.get(path)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    text = log_path.read_text()
    assert "GET /api/health" in text                    # the log was captured
    assert "SENTINEL" not in text and "4242.5" not in text, [l for l in text.splitlines() if "SENTINEL" in l or "4242" in l]


def test_validation_errors_do_not_echo_submitted_values(client):
    r = client.post("/api/patients", json={"label": "Fictional\nSENTINEL-MULTILINE"})
    assert r.status_code == 422 and "SENTINEL" not in r.text
    r = client.post("/api/labs", content='{"patient_id": 1, "source_id": 1, "date": "2026-08-07", "test": "SENTINEL", "value": NaN, "unit": "U/mL"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "SENTINEL" not in r.text


# --- Cross-case isolation ----------------------------------------------------------------------------------

def test_records_cannot_be_linked_across_cases(client):
    pid_a, src_a, lab_a = case(client, "Fictional Case A")
    pid_b = client.post("/api/patients", json={"label": "Fictional Case B"}).json()["id"]
    src_b = client.post("/api/sources", json={"patient_id": pid_b, "label": "Fictional B note", "kind": "manual"}).json()
    study_b = client.post("/api/studies", json={"patient_id": pid_b, "source_id": src_b["id"], "date": "2026-08-10", "modality": "CT", "study_label": "B"}).json()
    assert client.patch(f"/api/records/labs/{lab_a['id']}", json={"changes": {"source_id": src_b["id"]}, "reason": "Fictional"}).status_code == 422
    assert client.post("/api/labs", json={"patient_id": pid_b, "source_id": src_a["id"], "date": "2026-08-07", "test": "CA-125", "value": 1, "unit": "U/mL"}).status_code == 422
    assert client.post("/api/measurements", json={"patient_id": pid_a, "source_id": src_a["id"], "study_id": study_b["id"], "lesion_key": "x",
                                                  "site": "y", "long_axis_mm": 5}).status_code == 422
    a_audit = client.get(f"/api/patients/{pid_a}/audit").json()
    assert a_audit and all(e["patient_id"] == pid_a for e in a_audit)
    for path in (f"/api/patients/{pid_b}/report.md", f"/api/patients/{pid_b}/export.json", f"/api/patients/{pid_b}/timeline",
                 f"/api/patients/{pid_b}/evidence", f"/api/patients/{pid_b}/extractions"):
        assert "Fictional Case A" not in client.get(path).text, path
    csv = client.get(f"/api/patients/{pid_b}/export/audit_log.csv").text
    assert "Fictional Case A" not in csv


@pytest.mark.xfail(strict=True, reason="Known gap (threat model T1): no authentication; any local process can read any case. M3.1.")
def test_unauthenticated_requests_are_rejected(client):
    case(client)
    assert client.get("/api/patients").status_code == 401


# --- Documents: hashes, malformed input, traversal ----------------------------------------------------------

def test_hash_mismatch_and_invalid_references_are_refused_without_crashing(client):
    pid, src, _ = case(client)
    from app.documents import path_for
    path_for(src["document_sha256"]).write_bytes(b"FICTIONAL tampered")
    assert client.get(f"/api/sources/{src['id']}/document").status_code == 409
    with db.connection() as conn:   # cached text survives; the original check still runs on download
        conn.execute("DELETE FROM document_text")
    assert client.get(f"/api/sources/{src['id']}/document/text").status_code == 409
    assert client.post(f"/api/sources/{src['id']}/extract").status_code == 409
    with db.connection() as conn:
        conn.execute("UPDATE sources SET document_sha256='../../../windows/win.ini' WHERE id=?", (src["id"],))
    for path in (f"/api/sources/{src['id']}/document", f"/api/sources/{src['id']}/document/verify", f"/api/sources/{src['id']}/document/text"):
        r = client.get(path)
        assert r.status_code == 409 and "invalid" in r.text, path


@pytest.mark.parametrize("name, body, status", [
    ("../../../../etc/passwd.txt", b"FICTIONAL", 201),            # stored under its hash; the name is sanitised
    ("..\\..\\boot.ini.txt", b"FICTIONAL two", 201),
    ("report‮txt.pdf", b"%PDF-1.4 fictional", 201),          # right-to-left override stripped
    ("evil.html", b"<script>alert(1)</script>", 422),
    ("image.svg", b"<svg onload=alert(1)>", 422),
    ("fake.pdf", b"MZ\x90\x00 not a pdf", 422),
    ("fake.png", b"%PDF-1.4", 422),
    ("binary.txt", b"\x00\x01\x02", 422),
    ("", b"FICTIONAL", 422),
    ("x" * 300 + ".txt", b"FICTIONAL", 422),
])
def test_malformed_and_hostile_uploads(client, name, body, status):
    pid = client.post("/api/patients", json={"label": "Fictional"}).json()["id"]
    sid = client.post("/api/sources", json={"patient_id": pid, "label": "Fictional", "kind": "manual"}).json()["id"]
    r = client.put(f"/api/sources/{sid}/document", content=body, headers={"Content-Type": "application/octet-stream",
                                                                         "X-Document-Filename": quote(name)})
    assert r.status_code == status, r.text
    if status == 201:
        stored = r.json()["document_filename"]
        assert "/" not in stored and "\\" not in stored and ".." not in stored.replace("...", "") and "‮" not in stored


def test_entity_and_export_paths_are_whitelisted(client):
    pid, _, _ = case(client)
    for path in ("/api/records/..%2F..%2Fpatients/1", "/api/records/sqlite_master/1", "/api/records/labs;DROP/1",
                 f"/api/patients/{pid}/export/sqlite_master.csv", f"/api/patients/{pid}/export/..%2Fpatients.csv"):
        assert client.get(path).status_code == 404, path


def test_decompression_bomb_pdf_is_contained(client):
    """A 0.4 MB PDF whose content stream inflates to 400 MB is refused quickly (pypdf's decompression limit) as unreadable."""
    stream = zlib.compress(b"BT /F1 12 Tf 10 10 Td (FICTIONAL) Tj ET\n" + b" " * 400_000_000, 9)
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            f"<</Length {len(stream)}/Filter/FlateDecode>>stream\n".encode() + stream + b"\nendstream",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    pdf, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):   # a structurally valid PDF, so the parser really reaches the stream
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf += b"trailer\n<</Size 6/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % xref
    assert len(pdf) < 1_000_000
    pid = client.post("/api/patients", json={"label": "Fictional"}).json()["id"]
    sid = client.post("/api/sources", json={"patient_id": pid, "label": "Fictional", "kind": "lab"}).json()["id"]
    assert client.put(f"/api/sources/{sid}/document", content=pdf, headers={"Content-Type": "application/octet-stream",
                                                                          "X-Document-Filename": "bomb.pdf"}).status_code == 201
    started = time.monotonic()
    r = client.get(f"/api/sources/{sid}/document/text")
    assert r.status_code == 422 and "could not be read" in r.text and time.monotonic() - started < 10
    assert client.post(f"/api/sources/{sid}/extract").status_code == 422


# --- Missing evidence ------------------------------------------------------------------------------------------

def test_observations_require_existing_evidence(client):
    pid = client.post("/api/patients", json={"label": "Fictional"}).json()["id"]
    bare = client.post("/api/sources", json={"patient_id": pid, "label": "Fictional note", "kind": "manual"}).json()["id"]
    base = {"patient_id": pid, "date": "2026-08-07", "test": "CA-125", "value": 1, "unit": "U/mL"}
    assert client.post("/api/labs", json=base).status_code == 422
    assert client.post("/api/labs", json={**base, "source_id": 0}).status_code == 422
    assert client.post("/api/labs", json={**base, "source_id": bare, "anchor_quote": "CA-125"}).status_code == 422
    assert client.post("/api/labs", json={**base, "source_id": bare, "provenance": "extracted"}).status_code == 422
    assert client.get(f"/api/patients/{pid}/labs").json() == []


# --- Backups ---------------------------------------------------------------------------------------------------

def test_backup_with_unexpected_or_incomplete_store_is_refused(client, tmp_path):
    case(client)
    planted = backup(tmp_path / "b1")
    (planted / "documents" / "notes.html").write_text("<script>planted</script>")
    assert not verify_backup(planted)["ok"]
    with pytest.raises(RestoreError, match="notes.html"):
        restore(planted, tmp_path / "r1" / "oncoscope.sqlite3")
    incomplete = backup(tmp_path / "b2")
    for f in (incomplete / "documents").rglob("*"):
        if f.is_file():
            f.unlink()
    with pytest.raises(RestoreError, match="missing"):
        restore(incomplete, tmp_path / "r2" / "oncoscope.sqlite3")
    truncated = backup(tmp_path / "b3")
    conn = sqlite3.connect(truncated / "oncoscope.sqlite3")
    conn.execute("DROP TRIGGER audit_log_no_delete")
    conn.execute("DELETE FROM audit_log WHERE id = (SELECT max(id) FROM audit_log)")
    conn.commit()
    conn.close()
    with pytest.raises(RestoreError, match="audit"):
        restore(truncated, tmp_path / "r3" / "oncoscope.sqlite3")
    assert not (tmp_path / "r1" / "oncoscope.sqlite3").exists() and not (tmp_path / "r3" / "oncoscope.sqlite3").exists()


# --- Audit integrity ---------------------------------------------------------------------------------------------

def test_audit_edit_is_detected_and_head_exposes_truncation(client):
    case(client)
    head = client.get("/api/audit/verify").json()["head"]
    with db.connection() as conn:
        conn.execute("DROP TRIGGER audit_log_no_delete")
        conn.execute("DELETE FROM audit_log WHERE id = ?", (head["last_id"],))
    after = client.get("/api/audit/verify").json()
    assert after["head"] != head            # compared with a head recorded earlier, the removal is visible


@pytest.mark.xfail(strict=True, reason="Known gap (threat model T6): without an externally recorded head, removing the newest "
                                       "audit entries leaves a chain that still verifies. M3.1: anchor the head outside the DB.")
def test_tail_truncation_is_detected_without_external_head(client):
    case(client)
    with db.connection() as conn:
        conn.execute("DROP TRIGGER audit_log_no_delete")
        conn.execute("DELETE FROM audit_log WHERE id = (SELECT max(id) FROM audit_log)")
    assert client.get("/api/audit/verify").json()["valid"] is False


@pytest.mark.xfail(strict=True, reason="Known gap (threat model T2/T4): records are stored unencrypted at rest. M3.1.")
def test_database_file_does_not_contain_plaintext_records(client):
    client.post("/api/patients", json={"label": "Fictional SENTINEL-AT-REST"})
    raw = Path(os.environ["ONCOSCOPE_DB_PATH"]).read_bytes()
    assert b"SENTINEL-AT-REST" not in raw
