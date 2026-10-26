"""Backup manifests and non-destructive, verified restore of a fictional case."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.backup import backup, verify_backup
from app.main import app
from app.restore import RestoreError, main as restore_cli, restore
from conftest import fixture_bytes, new_source


@pytest.fixture
def live(tmp_path, monkeypatch):
    path = tmp_path / "live" / "oncoscope.sqlite3"
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(path))
    monkeypatch.delenv("ONCOSCOPE_DOCUMENT_DIR", raising=False)
    with TestClient(app) as client:
        pid = client.post("/api/patients", json={"label": "Fictional Case M25"}).json()["id"]
        src = new_source(client, pid, "Imaginary Valley lab", document="lab_2026-08-07_imaginary_valley.txt", source_date="2026-08-08")
        ct = new_source(client, pid, "Follow-up CT", kind="radiology", document="ct_2026-10-12_followup.pdf")
        (proposal,) = client.post(f"/api/sources/{src['id']}/extract").json()["created"]
        lab = client.post(f"/api/extractions/{proposal['id']}/accept", json={}).json()["record"]
        client.patch(f"/api/records/labs/{lab['id']}", json={"changes": {"value": 301}, "reason": "Deliberate fictional error for the demo"})
        client.patch(f"/api/records/labs/{lab['id']}", json={"changes": {"value": 310}, "reason": "Corrected back to the printout"})
        export = client.get(f"/api/patients/{pid}/export.json").json()
    return {"path": path, "pid": pid, "lab": lab, "src": src, "ct": ct, "export": export}


def comparable(export: dict) -> dict:
    return {k: v for k, v in export.items() if k != "generated_at"}


def test_backup_manifest_describes_case(live, tmp_path):
    target = backup(tmp_path / "backups")
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["integrity_check"] == "ok" and manifest["audit_chain"]["valid"] and manifest["schema_version"] == db.SCHEMA_VERSION
    assert manifest["tables"]["labs"]["rows"] == 1 and manifest["tables"]["extractions"]["rows"] == 1
    assert set(manifest["documents"]) == {live["src"]["document_sha256"], live["ct"]["document_sha256"]}
    assert all(d["valid"] for d in manifest["documents"].values())
    assert verify_backup(target)["ok"]


def test_restore_reproduces_case_into_new_database(live, tmp_path, monkeypatch):
    bundle = backup(tmp_path / "backups")
    target = tmp_path / "restored" / "oncoscope.sqlite3"
    result = restore(bundle, target)
    assert result["consistent"] and result["differences"] == [] and result["preserved_previous"] is None
    assert result["documents_copied"] == 2
    monkeypatch.setenv("ONCOSCOPE_DB_PATH", str(target))
    with TestClient(app) as client:
        export = client.get(f"/api/patients/{live['pid']}/export.json").json()
        assert comparable(export) == comparable(live["export"])
        history = client.get(f"/api/records/labs/{live['lab']['id']}/history").json()
        corrections = [json.loads(h["detail_json"]) for h in history if h["action"] == "correct"]
        assert [(c["before"]["value"], c["after"]["value"], c["reason"]) for c in corrections] == [
            (310, 301, "Deliberate fictional error for the demo"), (301, 310, "Corrected back to the printout")]
        for src in (live["src"], live["ct"]):
            assert client.get(f"/api/sources/{src['id']}/document/verify").json()["valid"]
        assert client.get(f"/api/sources/{live['src']['id']}/document").content == fixture_bytes("lab_2026-08-07_imaginary_valley.txt")
        assert client.get("/api/audit/verify").json()["valid"]


def test_restore_over_existing_database_preserves_it(live, tmp_path):
    bundle = backup(tmp_path / "backups")
    # Diverge the live database after the backup, then restore over it.
    with db.connection() as conn:
        db.insert_record(conn, "patients", {"label": "Fictional case added after backup", "is_synthetic": 1})
    result = restore(bundle, live["path"])
    assert result["consistent"] and result["preserved_previous"]
    preserved = sqlite3.connect(next(p for p in (tmp_path / "live" / "pre-restore-backups").glob("*/oncoscope.sqlite3")))
    assert preserved.execute("SELECT count(*) FROM patients").fetchone()[0] == 2     # the diverged state was kept
    preserved.close()
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM patients").fetchone()[0] == 1


def test_tampered_or_incomplete_backups_are_refused(live, tmp_path, capsys):
    target = tmp_path / "restored" / "oncoscope.sqlite3"
    doc_tampered = backup(tmp_path / "b1")
    doc_file = next(f for f in (doc_tampered / "documents").rglob("*") if f.is_file())
    doc_file.write_bytes(doc_file.read_bytes() + b"x")
    with pytest.raises(RestoreError, match="hash mismatch"):
        restore(doc_tampered, target)

    row_tampered = backup(tmp_path / "b2")
    conn = sqlite3.connect(row_tampered / "oncoscope.sqlite3")
    conn.execute("UPDATE labs SET value=999")
    conn.commit()
    conn.close()
    with pytest.raises(RestoreError, match="table labs"):
        restore(row_tampered, target)

    no_manifest = backup(tmp_path / "b3")
    (no_manifest / "manifest.json").unlink()
    assert restore_cli([str(no_manifest), "--target", str(target)]) == 1
    assert "missing manifest.json" in capsys.readouterr().out
    assert not target.exists()          # nothing was written by any refused restore


def test_conflicting_document_in_target_store_stops_restore(live, tmp_path):
    bundle = backup(tmp_path / "backups")
    target = tmp_path / "restored" / "oncoscope.sqlite3"
    sha = live["src"]["document_sha256"]
    clash = target.parent / "documents" / sha[:2] / sha
    clash.parent.mkdir(parents=True)
    clash.write_bytes(b"different bytes")
    with pytest.raises(RestoreError, match="different bytes"):
        restore(bundle, target)
    assert clash.read_bytes() == b"different bytes" and not target.exists()


def test_restore_cli_reports_consistency(live, tmp_path, capsys):
    bundle = backup(tmp_path / "backups")
    assert restore_cli([str(bundle), "--target", str(tmp_path / "cli" / "oncoscope.sqlite3")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["consistent"] is True and out["tables"]["audit_log"] == live["export"]["audit_chain"]["entries_checked"]
