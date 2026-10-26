"""The repository hygiene check passes on this repository and catches planted sensitive files."""
import importlib.util
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_repo_hygiene.py"
spec = importlib.util.spec_from_file_location("check_repo_hygiene", SCRIPT)
hygiene = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hygiene)


def test_this_repository_is_clean():
    assert hygiene.check(SCRIPT.parents[2]) == []


def test_planted_files_are_caught(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    planted = {
        "OncoScope_MVP/backend/data/oncoscope.sqlite3": b"SQLite format 3\x00",
        "OncoScope_MVP/backend/data/documents/ab/" + "ab" * 32: b"%PDF-1.4",
        "notes/.env": b"ONCOSCOPE_KEY=fictional",
        "OncoScope_MVP/backend/data/backups/oncoscope-20261026T010101Z/manifest.json": b"{}",
        "scan_of_report.pdf": b"%PDF-1.4 fictional",
        "config.py": b"TOKEN = 'ghp_" + b"a" * 36 + b"'",
        "OncoScope_MVP/fixtures/fictional/unlabelled.txt": b"CA-125 12 U/mL",
        "keys/server.pem": b"-----BEGIN RSA " + b"PRIVATE KEY-----",   # split so this file does not trip the check
    }
    for rel, data in planted.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    subprocess.run(["git", "add", "-f", "."], cwd=tmp_path, check=True)
    problems = "\n".join(hygiene.check(tmp_path))
    for rel in planted:
        assert rel in problems, rel
    assert "GitHub token" in problems and "private key" in problems and "FICTIONAL banner" in problems
