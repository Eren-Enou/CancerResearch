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


def extractor_for(media_type: str) -> str | None:
    """Identifies the deterministic text extractor. Its version is part of every cached text and anchor check."""
    if media_type.startswith("text/plain"):
        return "utf8-text-v1"
    if media_type == "application/pdf":
        import pypdf
        return f"pypdf-{pypdf.__version__}-v1"
    return None  # images carry no text layer; anchor them by region


def extract_pages(data: bytes, media_type: str) -> list[str]:
    """Page texts. TXT: newlines normalised to \\n, form feeds split pages. PDF: pypdf text layer per page."""
    if media_type.startswith("text/plain"):
        text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        return text.split("\f")
    if media_type == "application/pdf":
        import io
        import pypdf
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            return [(page.extract_text() or "").replace("\r\n", "\n") for page in reader.pages]
        except Exception as exc:  # malformed PDFs are reported, never partially trusted
            raise DocumentError(f"PDF text layer could not be read: {exc.__class__.__name__}") from exc
    return []


def page_count(data: bytes, media_type: str) -> int:
    if media_type.startswith("image/"):
        return 1
    return len(extract_pages(data, media_type))


REGION_PATTERN = re.compile(r"^\d{1,3}(\.\d+)?,\d{1,3}(\.\d+)?,\d{1,3}(\.\d+)?,\d{1,3}(\.\d+)?$")


def parse_region(region: str) -> tuple[float, float, float, float]:
    """Region as 'x,y,w,h' percentages of the page or image."""
    if not REGION_PATTERN.match(region):
        raise DocumentError("Region must be 'x,y,w,h' in percent, e.g. '10,20,30,5'")
    x, y, w, h = (float(v) for v in region.split(","))
    if w <= 0 or h <= 0 or x + w > 100 or y + h > 100:
        raise DocumentError("Region must have positive size and lie within 0–100% of the page")
    return x, y, w, h


def resolve_quote(pages: list[str], page: int | None, quote: str, start: int | None, end: int | None) -> tuple[int, int, int]:
    """Locates a quoted passage exactly. Returns (page, start, end) or raises when absent or ambiguous."""
    if page is not None and not 1 <= page <= len(pages):
        raise DocumentError(f"Page {page} does not exist; the document has {len(pages)} page(s)")
    candidates = [page] if page else list(range(1, len(pages) + 1))
    if start is not None or end is not None:
        if start is None or end is None or page is None:
            raise DocumentError("Offsets need anchor_page, anchor_start and anchor_end together")
        if pages[page - 1][start:end] != quote:
            raise DocumentError(f"The quoted passage does not match page {page} at characters {start}–{end}")
        return page, start, end
    hits = []
    for p in candidates:
        text, i = pages[p - 1], pages[p - 1].find(quote)
        while i != -1:
            hits.append((p, i, i + len(quote)))
            i = text.find(quote, i + 1)
    if not hits:
        raise DocumentError("The quoted passage does not occur in the document text" + (f" on page {page}" if page else ""))
    if len(hits) > 1:
        raise DocumentError(f"The quoted passage occurs {len(hits)} times; select the exact passage in the document viewer")
    return hits[0]


def load_verified(sha256: str) -> tuple[bytes | None, str | None]:
    """Returns (bytes, actual_sha256). bytes is None when the stored file is missing."""
    target = path_for(sha256)
    if not target.exists():
        return None, None
    data = target.read_bytes()
    return data, hashlib.sha256(data).hexdigest()
