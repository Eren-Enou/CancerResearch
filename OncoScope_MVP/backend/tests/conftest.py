"""Shared helpers for tests that use the committed fictional fixture documents."""
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fictional"
MEDIA = {".txt": "text/plain", ".pdf": "application/pdf", ".png": "image/png"}


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def attach(client, source_id: int, name: str):
    return client.put(f"/api/sources/{source_id}/document", content=fixture_bytes(name),
                      headers={"Content-Type": "application/octet-stream", "X-Document-Filename": name})


def new_source(client, pid: int, label: str, kind: str = "lab", document: str | None = None, source_date: str | None = None) -> dict:
    res = client.post("/api/sources", json={"patient_id": pid, "label": label, "kind": kind, "source_date": source_date})
    assert res.status_code == 201, res.text
    source = res.json()
    if document:
        res = attach(client, source["id"], document)
        assert res.status_code == 201, res.text
        source = res.json()
    return source
