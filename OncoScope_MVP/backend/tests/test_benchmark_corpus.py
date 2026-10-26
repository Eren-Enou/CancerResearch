"""The benchmark corpus is reproducible and its ground truth points at real text in the documents."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from app.documents import extract_pages, inspect

BENCH = Path(__file__).resolve().parents[2] / "fixtures" / "benchmark"
MANIFEST = json.loads((BENCH / "manifest.json").read_text(encoding="utf-8"))


def test_corpus_size_and_hashes():
    docs = MANIFEST["documents"]
    assert len(docs) >= 20 and len({d["id"] for d in docs}) == len(docs)
    for d in docs:
        data = (BENCH / "documents" / d["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == d["sha256"], d["file"]
        assert inspect(d["file"], data)[1] == d["sha256"]       # accepted by the app's own upload checks


def test_ground_truth_evidence_exists_on_stated_page():
    for d in MANIFEST["documents"]:
        data = (BENCH / "documents" / d["file"]).read_bytes()
        pages = extract_pages(data, d["media_type"]) if d["text_layer"] else []
        if not d["text_layer"]:
            assert not any(p.strip() for p in pages)
            assert all(o["status"] == "no_text" for o in d["expected_observations"])
            continue
        for item in d["expected_observations"] + d["expected_manual"]:
            assert item["evidence"] in pages[item["page"] - 1], (d["id"], item["evidence"])


def test_required_patterns_are_covered():
    patterns = {p for d in MANIFEST["documents"] for p in d["patterns"]}
    statuses = {o["status"] for d in MANIFEST["documents"] for o in d["expected_observations"]}
    assert {"table", "multi-page", "repeated-measurements", "collection-vs-report-date", "conflicting-value", "duplicate-report",
            "missing-unit", "ambiguous-date", "qualified-value", "no-text-layer", "radiology", "two-column"} <= patterns
    assert statuses == {"complete", "flagged", "mention", "no_text"}
    assert {f["kind"] for f in MANIFEST["expected_findings"]} == {"conflicting_values", "possible_duplicate"}


def test_generator_is_deterministic(tmp_path):
    before = {p.name: p.read_bytes() for p in (BENCH / "documents").iterdir()}
    subprocess.run([sys.executable, str(BENCH / "make_benchmark.py")], check=True, capture_output=True)
    after = {p.name: p.read_bytes() for p in (BENCH / "documents").iterdir()}
    assert before == after
