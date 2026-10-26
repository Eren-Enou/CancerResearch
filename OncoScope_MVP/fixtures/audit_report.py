"""Independent audit of an OncoScope Markdown report against the original fictional files.

Usage: python fixtures/audit_report.py <report.md> [documents_dir]

For every report line that cites a document passage, this script re-reads the ORIGINAL file (it does not use the
application database or code), checks the SHA-256 prefix, re-extracts the page text and confirms the quoted passage
sits exactly at the cited character range. Exit code 1 if any citation fails.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from pathlib import Path

CITATION = re.compile(r"file `(?P<file>[^`]+)` sha256 (?P<sha>[0-9a-f]{12}); page (?P<page>\d+), chars (?P<a>\d+)-(?P<b>\d+): "
                      r"(?P<quote>\"(?:[^\"\\]|\\.)*\")\]")


def page_texts(path: Path) -> list[str]:
    data = path.read_bytes()
    if path.suffix.lower() == ".txt":
        return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\f")
    if path.suffix.lower() == ".pdf":
        import pypdf
        return [(p.extract_text() or "").replace("\r\n", "\n") for p in pypdf.PdfReader(io.BytesIO(data)).pages]
    return []


def audit(report: str, documents: Path) -> list[dict]:
    results = []
    for line in report.splitlines():
        for m in CITATION.finditer(line):
            path = documents / m["file"]
            check = {"file": m["file"], "page": int(m["page"]), "range": (int(m["a"]), int(m["b"])), "quote": json.loads(m["quote"])}
            if not path.exists():
                results.append({**check, "ok": False, "problem": "original file not found"})
                continue
            if hashlib.sha256(path.read_bytes()).hexdigest()[:12] != m["sha"]:
                results.append({**check, "ok": False, "problem": "SHA-256 prefix differs from the original file"})
                continue
            pages = page_texts(path)
            text = pages[check["page"] - 1] if 0 < check["page"] <= len(pages) else None
            found = text[check["range"][0]:check["range"][1]] if text is not None else None
            ok = found == check["quote"]
            results.append({**check, "ok": ok, "problem": None if ok else f"text at range is {found!r}"})
    return results


def main() -> int:
    report = Path(sys.argv[1]).read_text(encoding="utf-8")
    documents = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent / "fictional"
    results = audit(report, documents)
    for r in results:
        print(("OK   " if r["ok"] else "FAIL ") + f"{r['file']} p{r['page']} {r['range'][0]}-{r['range'][1]} {r['quote']!r}"
              + ("" if r["ok"] else f" -> {r['problem']}"))
    print(f"{sum(r['ok'] for r in results)}/{len(results)} citations verified against original files")
    return 0 if results and all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
