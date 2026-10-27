"""M3.1-A protected local access: sessions, lock, rate limiting, foreign origins, audit actors. Fictional data only."""
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth, db
from app.main import PUBLIC_ROUTES, app
from conftest import attach, new_source

PASSPHRASE = "fictional passphrase for tests"
APP_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def protected(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_MODE", "protected")
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "protected" / "oncoscope.sqlite3"))
    monkeypatch.delenv("ONCOSCOPE_DOCUMENT_DIR", raising=False)
    with TestClient(app) as c:
        yield c


def set_up(client) -> dict:
    r = client.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": PASSPHRASE})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def unlock(client, passphrase=PASSPHRASE):
    return client.post("/api/auth/unlock", json={"passphrase": passphrase})


def bearer(r) -> dict:
    return {"Authorization": f"Bearer {r.json()['token']}"}


class Authed:
    """A client wrapper that sends the session header with every request, like the UI does."""
    def __init__(self, client, headers):
        self.client, self.headers = client, headers

    def __getattr__(self, method):
        def call(url, **kw):
            return getattr(self.client, method)(url, **{**kw, "headers": {**self.headers, **kw.get("headers", {})}})
        return call


def fictional_case(c) -> tuple[int, dict, dict]:
    pid = c.post("/api/patients", json={"label": "Fictional Protected Case"}).json()["id"]
    src = new_source(c, pid, "Fictional lab", document="lab_2026-08-07_imaginary_valley.txt", source_date="2026-08-08")
    lab = c.post("/api/labs", json={"patient_id": pid, "source_id": src["id"], "date": "2026-08-07", "test": "CA-125",
                                    "value": 310, "unit": "U/mL", "anchor_quote": "CA-125          310      U/mL"})
    assert lab.status_code == 201, lab.text
    return pid, src, lab.json()


def every_protected_route():
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD"}:
            if (method, route.path) not in PUBLIC_ROUTES:
                yield method, re.sub(r"\{[^}]+\}", "1", route.path)


# 1. Unauthenticated requests -------------------------------------------------------------------------------

def test_every_case_data_route_requires_a_session(protected):
    headers = set_up(protected)
    fictional_case(Authed(protected, headers))
    routes = list(every_protected_route())
    assert len(routes) > 30
    for method, path in routes:
        r = protected.request(method, path, json={})
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.json() == {"detail": "OncoScope is locked. Unlock it to continue."}
        assert r.headers["www-authenticate"] == "Bearer" and r.headers["cache-control"] == "no-store"
    # The documented public operations say nothing about cases or the owner.
    assert protected.get("/api/auth/status").json() == {"mode": "protected", "setup_required": False, "recovery_available": True, "encrypted_at_rest": False}
    assert "Fictional" not in protected.get("/api/health").text


def test_setup_is_one_time_and_enforces_the_passphrase_policy(protected):
    assert protected.get("/api/auth/status").json()["setup_required"] is True
    assert protected.post("/api/auth/setup", json={"display_name": "Fictional Owner", "passphrase": "short"}).status_code == 422
    assert protected.post("/api/auth/setup", json={"display_name": "   ", "passphrase": PASSPHRASE}).status_code == 422
    assert unlock(protected).status_code == 409                       # nothing to unlock yet
    set_up(protected)
    again = protected.post("/api/auth/setup", json={"display_name": "Fictional Intruder", "passphrase": PASSPHRASE + "x"})
    assert again.status_code == 409
    with db.connection() as conn:
        rows = conn.execute("SELECT display_name, password_verifier FROM local_owner").fetchall()
        assert [r[0] for r in rows] == ["Fictional Owner"]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM local_owner")


def test_only_a_salted_argon2id_verifier_is_stored(protected, tmp_path, monkeypatch):
    set_up(protected)
    with db.connection() as conn:
        first = conn.execute("SELECT password_verifier FROM local_owner").fetchone()[0]
    assert first.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert PASSPHRASE.encode() not in Path(os.environ["ONCOSCOPE_DB_PATH"]).read_bytes()
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "second" / "oncoscope.sqlite3"))
    with TestClient(app) as other:
        set_up(other)
        with db.connection() as conn:
            second = conn.execute("SELECT password_verifier FROM local_owner").fetchone()[0]
    assert first != second                                            # same passphrase, different salt


# 2. Incorrect and expired tokens ---------------------------------------------------------------------------

def test_invalid_tokens_and_tokens_outside_the_header_are_rejected(protected):
    headers = set_up(protected)
    token = headers["Authorization"].split()[1]
    assert protected.get("/api/patients", headers=headers).status_code == 200
    for bad in ({"Authorization": "Bearer " + token[:-2] + "xx"}, {"Authorization": "Bearer "}, {"Authorization": token},
                {"Authorization": f"Basic {token}"}, {"X-Session-Token": token}):
        assert protected.get("/api/patients", headers=bad).status_code == 401, bad
    assert protected.get(f"/api/patients?token={token}").status_code == 401
    protected.cookies.set("token", token)
    assert protected.get("/api/patients").status_code == 401
    protected.cookies.clear()


def test_idle_and_absolute_expiry_end_the_session(protected, monkeypatch):
    headers = set_up(protected)
    monkeypatch.setenv("ONCOSCOPE_SESSION_IDLE_SECONDS", "1")
    assert protected.get("/api/patients", headers=headers).status_code == 200
    time.sleep(1.2)
    assert protected.get("/api/patients", headers=headers).status_code == 401
    assert protected.get("/api/patients", headers=headers).status_code == 401   # expired sessions are deleted, not revived
    monkeypatch.setenv("ONCOSCOPE_SESSION_IDLE_SECONDS", "900")
    monkeypatch.setenv("ONCOSCOPE_SESSION_MAX_SECONDS", "1")
    fresh = bearer(unlock(protected))
    assert protected.get("/api/patients", headers=fresh).status_code == 200
    time.sleep(0.6)
    assert protected.get("/api/patients", headers=fresh).status_code == 200    # activity does not extend the lifetime
    time.sleep(0.6)
    assert protected.get("/api/patients", headers=fresh).status_code == 401


def test_restarting_the_server_ends_every_session(protected):
    headers = set_up(protected)
    with TestClient(app) as restarted:
        assert restarted.get("/api/patients", headers=headers).status_code == 401
        assert unlock(restarted).status_code == 200


# 3. Locking --------------------------------------------------------------------------------------------------

def test_lock_revokes_the_current_session_immediately(protected):
    headers = set_up(protected)
    assert protected.get("/api/patients", headers=headers).status_code == 200
    assert protected.post("/api/auth/lock", headers=headers).json() == {"locked": True}
    assert protected.get("/api/patients", headers=headers).status_code == 401
    assert protected.post("/api/auth/lock", headers=headers).status_code == 401
    assert protected.get("/api/patients", headers=bearer(unlock(protected))).status_code == 200


def test_unlocking_again_ends_the_earlier_session(protected):
    first = set_up(protected)
    second = bearer(unlock(protected))
    assert protected.get("/api/patients", headers=first).status_code == 401
    assert protected.get("/api/patients", headers=second).status_code == 200


# 4. Foreign origins and loopback ----------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    {"Origin": "https://attacker.example"}, {"Origin": "null"}, {"Origin": "http://127.0.0.1:9999"},
    {"Origin": "http://localhost"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
])
def test_a_foreign_origin_cannot_use_a_protected_session(protected, extra):
    headers = set_up(protected)
    for method, path, body in (("GET", "/api/patients", None), ("POST", "/api/patients", {"label": "Fictional injected"}),
                               ("POST", "/api/auth/unlock", {"passphrase": PASSPHRASE}), ("POST", "/api/auth/lock", None)):
        r = protected.request(method, path, json=body, headers={**headers, **extra})
        assert r.status_code == 403 and "access-control-allow-origin" not in r.headers, (method, path)
    assert protected.get("/api/patients", headers=headers).json() == []          # nothing was created, session still valid


def test_same_origin_browser_requests_are_accepted(protected):
    headers = set_up(protected)
    same = {**headers, "Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    assert protected.post("/api/patients", json={"label": "Fictional same-origin"}, headers=same).status_code == 201


def test_non_loopback_peers_are_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_MODE", "protected")
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "lan.sqlite3"))
    with TestClient(app, client=("192.168.1.20", 50000)) as lan:
        for path in ("/api/health", "/api/auth/status", "/docs"):
            assert lan.get(path).status_code == 403, path
        assert lan.post("/api/auth/setup", json={"display_name": "Fictional", "passphrase": PASSPHRASE}).status_code == 403


# 5. Wrong passphrases ----------------------------------------------------------------------------------------

def test_wrong_passphrases_fail_safely_and_are_rate_limited(protected, monkeypatch):
    set_up(protected)
    for _ in range(auth.MAX_FAILURES):
        r = unlock(protected, "fictional wrong passphrase")
        assert r.status_code == 401 and r.json() == {"detail": "Incorrect passphrase"}
        assert "token" not in r.text and "wrong" not in r.text
    blocked = unlock(protected)                                        # even the right passphrase waits
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) == auth.LOCKOUT_BASE_SECONDS
    monkeypatch.setattr(auth, "_blocked_until", 0.0)                   # the lockout has elapsed
    again = unlock(protected, "fictional wrong passphrase")
    assert again.status_code == 401
    assert int(unlock(protected).headers["retry-after"]) == 2 * auth.LOCKOUT_BASE_SECONDS   # the delay doubles
    monkeypatch.setattr(auth, "_blocked_until", 0.0)
    assert unlock(protected).status_code == 200                        # success clears the count
    assert unlock(protected, "fictional wrong passphrase").status_code == 401
    assert unlock(protected).status_code == 200


def test_malformed_unlock_requests_do_not_echo_the_passphrase(protected):
    set_up(protected)
    r = protected.post("/api/auth/unlock", json={"passphrase": PASSPHRASE, "extra": "SENTINEL-EXTRA"})
    assert r.status_code == 422 and PASSPHRASE not in r.text and "SENTINEL" not in r.text
    r = protected.post("/api/auth/unlock", content=f"passphrase={PASSPHRASE}", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 422 and PASSPHRASE not in r.text


# 6. Audit actors --------------------------------------------------------------------------------------------

def test_every_new_audit_entry_names_the_authenticated_owner(protected):
    headers = set_up(protected)
    c = Authed(protected, headers)
    pid, src, lab = fictional_case(c)
    assert c.patch(f"/api/records/labs/{lab['id']}", json={"changes": {"value": 311}, "reason": "Fictional typo"}).status_code == 200
    assert c.post(f"/api/records/labs/{lab['id']}/status", json={"status": "verified", "reason": "Fictional check"}).status_code == 200
    other = new_source(c, pid, "Fictional second lab", document="lab_2026-09-18_portal_copy.txt", source_date="2026-09-19")
    created = c.post(f"/api/sources/{other['id']}/extract").json()["created"]
    assert created
    accepted = c.post(f"/api/extractions/{created[0]['id']}/accept", json={"reason": "Fictional review"})
    assert accepted.status_code == 200, accepted.text
    for p in created[1:]:
        c.post(f"/api/extractions/{p['id']}/reject", json={"reason": "Fictional rejection"})
    c.post("/api/auth/lock")
    with db.connection() as conn:
        owner_uid = conn.execute("SELECT uid FROM local_owner").fetchone()[0]
        entries = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id")]
        assert db.verify_audit_chain(conn)["valid"]
    actions = {e["action"] for e in entries}
    assert {"owner_setup", "unlock", "create", "attach_document", "correct", "status", "accept", "lock"} <= actions
    assert all(e["actor_id"] == f"owner:{owner_uid}" and e["actor_label"] == "Fictional Owner" for e in entries)


def test_the_actor_is_covered_by_the_entry_hash(protected):
    headers = set_up(protected)
    Authed(protected, headers).post("/api/patients", json={"label": "Fictional"})
    with db.connection() as conn:
        conn.execute("DROP TRIGGER audit_log_no_update")
        conn.execute("UPDATE audit_log SET actor_label='Fictional Someone Else' WHERE action='create'")
        bad_id = conn.execute("SELECT id FROM audit_log WHERE action='create'").fetchone()[0]
        assert db.verify_audit_chain(conn) == {"valid": False, "entries_checked": bad_id - 1, "first_invalid_id": bad_id}


def test_protected_writes_without_an_actor_are_refused(protected):
    set_up(protected)
    with pytest.raises(RuntimeError, match="authenticated actor"), db.connection() as conn:
        db.insert_record(conn, "patients", {"label": "Fictional unattributed", "is_synthetic": 1})
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM patients").fetchone()[0] == 0


# 7. Downloads and exports -------------------------------------------------------------------------------------

def test_documents_reports_and_exports_require_a_session(protected):
    headers = set_up(protected)
    pid, src, _ = fictional_case(Authed(protected, headers))
    for path in (f"/api/sources/{src['id']}/document", f"/api/sources/{src['id']}/document/text",
                 f"/api/sources/{src['id']}/document/verify", f"/api/patients/{pid}/report.md",
                 f"/api/patients/{pid}/export.json", f"/api/patients/{pid}/export/labs.csv",
                 f"/api/patients/{pid}/export/audit_log.csv", f"/api/patients/{pid}/audit", "/api/audit/verify"):
        locked = protected.get(path)
        assert locked.status_code == 401 and "Fictional" not in locked.text and "310" not in locked.text, path
        assert protected.get(path, headers=headers).status_code == 200, path
    upload = protected.put(f"/api/sources/{src['id']}/document", content=b"FICTIONAL",
                           headers={"Content-Type": "application/octet-stream", "X-Document-Filename": "x.txt"})
    assert upload.status_code == 401


# 8. Existing synthetic databases ----------------------------------------------------------------------------

def test_existing_v3_database_and_audit_history_survive_protection(tmp_path, monkeypatch):
    path = tmp_path / "legacy" / "oncoscope.sqlite3"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    db.migrate(conn, up_to=3)
    conn.execute("BEGIN")
    db.insert_record(conn, "patients", {"label": "Fictional v3 case", "is_synthetic": 1})
    db.insert_record(conn, "sources", {"patient_id": 1, "label": "Fictional v3 source", "kind": "lab"})
    db.insert_record(conn, "labs", {"patient_id": 1, "source_id": 1, "date": "2026-07-01", "test": "CA-125", "value": 42, "unit": "U/mL"})
    conn.execute("COMMIT")
    before = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id")]
    labs_before = [dict(r) for r in conn.execute("SELECT * FROM labs")]
    conn.close()

    monkeypatch.setenv("ONCOSCOPE_MODE", "protected")
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(path))
    with TestClient(app) as c:
        assert c.get("/api/patients").status_code == 401                # the existing case is now behind the lock
        headers = set_up(c)
        audit = c.get("/api/patients/1/audit", headers=headers).json()
        assert c.get("/api/patients/1/labs", headers=headers).json() == labs_before
        c.post("/api/labs", headers=headers, json={"patient_id": 1, "source_id": 1, "date": "2026-07-02", "test": "CA-125",
                                                   "value": 40, "unit": "U/mL"})
        assert c.get("/api/audit/verify", headers=headers).json()["valid"] is True
    with db.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 5
        after = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id")]
    for old, new in zip(before, after):
        assert {k: new[k] for k in old} == old                          # same rows, same hashes
        assert new["actor_id"] is None and new["actor_label"] is None   # unknown stays unknown
    new = after[len(before):]
    assert [e["action"] for e in new] == ["owner_setup", "vault_initialized", "recovery_key_issued", "unlock", "create"]
    assert {e["actor_label"] for e in new} == {"Fictional Owner"}
    assert {e["actor_id"] for e in audit if e["id"] <= len(before)} == {None}


def test_demonstration_mode_refuses_protected_storage(protected, monkeypatch):
    set_up(protected)
    monkeypatch.setenv("ONCOSCOPE_MODE", "demo")
    with pytest.raises(RuntimeError, match="refuses to open protected storage"):
        with TestClient(app):
            pass
    assert db.DEMO_DB_PATH != db.DEFAULT_DB_PATH and db.DEMO_DB_PATH.parent != db.DEFAULT_DB_PATH.parent


def test_demonstration_mode_has_no_owner_or_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(tmp_path / "demo.sqlite3"))
    with TestClient(app) as demo:
        assert demo.get("/api/auth/status").json() == {"mode": "demo", "setup_required": False, "recovery_available": False, "encrypted_at_rest": False}
        assert demo.post("/api/auth/setup", json={"display_name": "Fictional", "passphrase": PASSPHRASE}).status_code == 409
        assert unlock(demo).status_code == 409
        demo.post("/api/patients", json={"label": "Fictional demo"})
        assert {e["actor_id"] for e in demo.get("/api/patients/1/audit").json()} == {"demo"}


def test_mode_must_be_explicit_and_valid(monkeypatch):
    monkeypatch.delenv("ONCOSCOPE_MODE")
    assert db.app_mode() == "protected"                               # protected unless demonstration is requested
    monkeypatch.setenv("ONCOSCOPE_MODE", "open")
    with pytest.raises(RuntimeError):
        db.app_mode()


# Logs ----------------------------------------------------------------------------------------------------------

def test_server_logs_contain_no_tokens_or_passphrases(tmp_path):
    from test_security import _free_port
    port, log_path = _free_port(), tmp_path / "server.log"
    env = {**os.environ, "ONCOSCOPE_MODE": "protected", "ONCOSCOPE_DB_PATH": str(tmp_path / "logcheck.sqlite3")}
    env.pop("ONCOSCOPE_DOCUMENT_DIR", None)
    with open(log_path, "w") as log:
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                cwd=APP_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            c = httpx.Client(base_url=f"http://127.0.0.1:{port}/api")
            for _ in range(100):
                try:
                    c.get("/health")
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            token = c.post("/auth/setup", json={"display_name": "Fictional SENTINEL-OWNER", "passphrase": PASSPHRASE}).json()["token"]
            h = {"Authorization": f"Bearer {token}"}
            c.post("/auth/unlock", json={"passphrase": "fictional SENTINEL-WRONG"})
            c.post("/patients", json={"label": "Fictional"}, headers=h)
            c.get("/patients", headers=h)
            c.post("/auth/lock", headers=h)
            c.get("/patients", headers=h)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    text = log_path.read_text()
    assert "POST /api/auth/unlock" in text and "401" in text
    for secret in (token, PASSPHRASE, "SENTINEL"):
        assert secret not in text, secret
