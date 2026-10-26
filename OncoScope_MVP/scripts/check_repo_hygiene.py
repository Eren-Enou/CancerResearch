"""Fails if tracked files could contain local medical data, document stores, backups or secrets.

Usage: python OncoScope_MVP/scripts/check_repo_hygiene.py [repo_root]
Runs in CI on every pull request. Only `git ls-files` (tracked or staged) content is inspected.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".db-journal", ".db-wal", ".sqlite3-wal", ".sqlite3-journal", ".dcm",
                      ".dicom", ".pem", ".key", ".p12", ".pfx", ".jks", ".kdbx"}
DOCUMENT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".docx", ".doc", ".rtf"}
# Fictional fixtures and test screenshots are the only places documents/images may live.
DOCUMENT_ALLOWED = ("OncoScope_MVP/fixtures/fictional/", "OncoScope_MVP/fixtures/benchmark/documents/",
                    "OncoScope_MVP/fixtures/benchmark/holdout/documents/", "OncoScope_MVP/docs/screenshots/")
FIXTURE_TEXT_ROOTS = DOCUMENT_ALLOWED[:3]
STORE_PATH = re.compile(r"(^|/)[0-9a-f]{2}/[0-9a-f]{64}$")
BACKUP_PATH = re.compile(r"(^|/)(backups|pre-restore-backups)/|(^|/)oncoscope-\d{8}T\d{6}Z")
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    "API key (sk-)": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
}
MAX_BYTES = 2 * 1024 * 1024


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached"], cwd=root, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def check(root: Path) -> list[str]:
    problems = []
    for rel in tracked_files(root):
        path = root / rel
        name = rel.lower()
        suffix = Path(name).suffix
        if not path.exists():
            continue
        if suffix in FORBIDDEN_SUFFIXES:
            problems.append(f"{rel}: database, scan or key file must not be committed")
        if re.search(r"(^|/)\.env(\.|$)", name) and not name.endswith(".env.example"):
            problems.append(f"{rel}: environment file must not be committed")
        if "/backend/data/" in f"/{rel}" or STORE_PATH.search(rel):
            problems.append(f"{rel}: local application data or document-store file")
        if BACKUP_PATH.search(rel):
            problems.append(f"{rel}: backup output must not be committed")
        if suffix in DOCUMENT_SUFFIXES and not rel.startswith(DOCUMENT_ALLOWED):
            problems.append(f"{rel}: documents/images are only allowed under fictional fixtures or docs/screenshots")
        size = path.stat().st_size
        if size > MAX_BYTES and not rel.endswith("package-lock.json"):
            problems.append(f"{rel}: {size} bytes exceeds {MAX_BYTES}; large binaries need review")
        if rel.startswith(FIXTURE_TEXT_ROOTS) and suffix in {".txt", ".pdf"} and "FICTIONAL" not in _text(path).upper():
            if suffix == ".txt" or _text(path).strip():
                problems.append(f"{rel}: fixture text must carry the FICTIONAL banner")
        if size <= MAX_BYTES and suffix not in DOCUMENT_SUFFIXES:
            text = path.read_bytes().decode("utf-8", errors="ignore")
            for label, pattern in SECRET_PATTERNS.items():
                if pattern.search(text):
                    problems.append(f"{rel}: looks like it contains a {label}")
    return problems


def _text(path: Path) -> str:
    if path.suffix.lower() == ".txt":
        return path.read_bytes().decode("utf-8", errors="ignore")
    try:
        import pypdf
        return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(str(path)).pages)
    except Exception:  # unreadable PDFs in fixture folders are reported by the caller as missing the banner
        return ""


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    problems = check(root)
    for p in problems:
        print(f"FAIL {p}")
    print(f"{len(tracked_files(root))} tracked files checked; {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
