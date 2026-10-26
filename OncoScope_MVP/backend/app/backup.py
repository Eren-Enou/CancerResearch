"""Non-destructive local backup: `python -m app.backup [target_dir]`. Never overwrites an existing file."""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .db import database_path, document_dir


def backup(target_dir: Path | None = None) -> Path:
    source = database_path()
    if not source.exists():
        raise SystemExit(f"No database at {source}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parent = target_dir or source.parent / "backups"
    parent.mkdir(parents=True, exist_ok=True)
    target, n = parent / f"oncoscope-{stamp}", 1
    while target.exists():
        n += 1
        target = parent / f"oncoscope-{stamp}-{n}"
    target.mkdir()
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target / source.name)
    try:
        src.backup(dst)  # consistent snapshot even while the API is running
    finally:
        dst.close()
        src.close()
    if document_dir().exists():
        shutil.copytree(document_dir(), target / "documents")
    return target


if __name__ == "__main__":
    print(f"Backup written to {backup(Path(sys.argv[1]) if len(sys.argv) > 1 else None)}")
