"""Synthetic source-document storage: content-addressed by SHA-256, immutable once attached."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .db import document_dir

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
# Deliberately excludes HTML, SVG and DICOM: nothing that can run script or carry scan identifiers.
ALLOWED_TYPES = {
    ".pdf": ("application/pdf", lambda b: b.startswith(b"%PDF-")),
    ".png": ("image/png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    ".txt": ("text/plain; charset=utf-8", lambda b: _is_utf8_text(b)),
}


class DocumentError(ValueError):
    pass


def _is_utf8_text(data: bytes) -> bool:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return "\x00" not in text


def safe_filename(name: str) -> str:
    base = re.split(r"[\\/]", name)[-1].strip()
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:120].strip(" .")
    if not cleaned:
        raise DocumentError("A filename is required")
    return cleaned


def inspect(filename: str, data: bytes) -> tuple[str, str]:
    """Returns (media_type, sha256) or raises DocumentError."""
    if not data:
        raise DocumentError("Document is empty")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentError(f"Document exceeds {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB limit")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_TYPES:
        raise DocumentError(f"Only {', '.join(sorted(ALLOWED_TYPES))} synthetic documents are accepted")
    media_type, signature_ok = ALLOWED_TYPES[suffix]
    if not signature_ok(data):
        raise DocumentError(f"File content does not match its {suffix} extension")
    return media_type, hashlib.sha256(data).hexdigest()


def path_for(sha256: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise DocumentError("Invalid document hash")
    return document_dir() / sha256[:2] / sha256


def store(sha256: str, data: bytes) -> None:
    target = path_for(sha256)
    if target.exists():
        return  # content-addressed: identical bytes already stored
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".partial")
    temp.write_bytes(data)
    temp.replace(target)


def load_verified(sha256: str) -> tuple[bytes | None, str | None]:
    """Returns (bytes, actual_sha256). bytes is None when the stored file is missing."""
    target = path_for(sha256)
    if not target.exists():
        return None, None
    data = target.read_bytes()
    return data, hashlib.sha256(data).hexdigest()
