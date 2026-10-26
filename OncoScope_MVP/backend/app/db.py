"""SQLite persistence, versioned migrations, and a hash-chained audit log. No network calls."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "oncoscope.sqlite3"

# Tables holding source-linked patient records. Names are trusted server-side constants.
RECORD_TABLES = ("sources", "events", "labs", "treatments", "studies", "measurements")
OBSERVATION_TABLES = RECORD_TABLES[1:]
PROVENANCES = ("transcribed", "extracted", "user_observation")
VERIFICATION_STATES = ("entered", "reviewed", "verified")


def database_path() -> Path:
    return Path(os.environ.get("ONCOSCOPE_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()


def document_dir() -> Path:
    """Content-addressed store for synthetic source documents, kept beside the database."""
    return Path(os.environ.get("ONCOSCOPE_DOCUMENT_DIR", str(database_path().parent / "documents"))).expanduser()


def now_utc() -> str:
    """System timestamps are UTC with an explicit Z; clinical dates stay calendar dates."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = _connect(database_path())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- Migrations -------------------------------------------------------------------------------
# Each migration runs once, inside its own transaction, and bumps PRAGMA user_version.
# Never edit a released migration; append a new one.

PHASE1_SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
  id INTEGER PRIMARY KEY, label TEXT NOT NULL,
  is_synthetic INTEGER NOT NULL DEFAULT 1 CHECK(is_synthetic IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
  label TEXT NOT NULL, kind TEXT NOT NULL,
  source_date TEXT, note TEXT NOT NULL DEFAULT '',
  entered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
  date TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  source_id INTEGER NOT NULL REFERENCES sources(id),
  entered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS labs (
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
  date TEXT NOT NULL, test TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  entered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS treatments (
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
  date TEXT NOT NULL, regimen TEXT NOT NULL, cycle INTEGER NOT NULL CHECK(cycle > 0),
  notes TEXT NOT NULL DEFAULT '', source_id INTEGER NOT NULL REFERENCES sources(id),
  entered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS studies (
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
  date TEXT NOT NULL, modality TEXT NOT NULL, study_label TEXT NOT NULL,
  report_text TEXT NOT NULL DEFAULT '', source_id INTEGER NOT NULL REFERENCES sources(id),
  entered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS measurements (
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
  study_id INTEGER NOT NULL REFERENCES studies(id),
  lesion_key TEXT NOT NULL, site TEXT NOT NULL,
  long_axis_mm REAL NOT NULL CHECK(long_axis_mm > 0),
  short_axis_mm REAL CHECK(short_axis_mm > 0),
  notes TEXT NOT NULL DEFAULT '', source_id INTEGER NOT NULL REFERENCES sources(id),
  entered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(study_id, lesion_key)
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
  action TEXT NOT NULL, detail_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_patient_date ON events(patient_id, date);
CREATE INDEX IF NOT EXISTS idx_labs_patient_date ON labs(patient_id, date);
CREATE INDEX IF NOT EXISTS idx_studies_patient_date ON studies(patient_id, date)
"""


def _m1_phase1_baseline(conn: sqlite3.Connection) -> None:
    # IF NOT EXISTS lets unversioned Phase 1 databases adopt version 1 unchanged.
    for statement in PHASE1_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _m2_provenance_and_audit(conn: sqlite3.Connection) -> None:
    for table in ("patients", *RECORD_TABLES):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN uid TEXT")
        for (row_id,) in conn.execute(f"SELECT id FROM {table} WHERE uid IS NULL").fetchall():
            conn.execute(f"UPDATE {table} SET uid=? WHERE id=?", (str(uuid.uuid4()), row_id))
        conn.execute(f"CREATE UNIQUE INDEX idx_{table}_uid ON {table}(uid)")
    for table in RECORD_TABLES:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0)")
        conn.execute(f"ALTER TABLE {table} ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'entered' "
                     "CHECK(verification_status IN ('entered','reviewed','verified'))")
        conn.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT")
    conn.execute("ALTER TABLE sources ADD COLUMN origin TEXT NOT NULL DEFAULT ''")
    for column, kind in (("document_sha256", "TEXT"), ("document_filename", "TEXT"), ("document_media_type", "TEXT"),
                         ("document_size", "INTEGER"), ("document_attached_at", "TEXT")):
        conn.execute(f"ALTER TABLE sources ADD COLUMN {column} {kind}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_measurements_study ON measurements(study_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_treatments_patient_date ON treatments(patient_id, date)")

    for column in ("patient_id INTEGER", "reason TEXT", "prev_hash TEXT", "entry_hash TEXT"):
        conn.execute(f"ALTER TABLE audit_log ADD COLUMN {column}")
    for table in RECORD_TABLES:
        conn.execute(f"""UPDATE audit_log SET patient_id=(SELECT t.patient_id FROM {table} t WHERE t.id=audit_log.entity_id)
                         WHERE entity_type=? AND patient_id IS NULL""", (table,))
    reason_rows = conn.execute("SELECT id, detail_json FROM audit_log WHERE action='correct'").fetchall()
    for row in reason_rows:
        conn.execute("UPDATE audit_log SET reason=? WHERE id=?", (json.loads(row["detail_json"]).get("reason"), row["id"]))
    prev = None
    for row in conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall():
        entry_hash = _entry_hash(prev, dict(row))
        conn.execute("UPDATE audit_log SET prev_hash=?, entry_hash=? WHERE id=?", (prev, entry_hash, row["id"]))
        prev = entry_hash
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_patient ON audit_log(patient_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id)")
    # Append-only: SQLite itself refuses edits or deletions of audit history.
    conn.execute("""CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
                    BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""")
    conn.execute("""CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
                    BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""")


def _m3_evidence_and_extraction(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE extractions (
      id INTEGER PRIMARY KEY, uid TEXT NOT NULL UNIQUE,
      patient_id INTEGER NOT NULL REFERENCES patients(id), source_id INTEGER NOT NULL REFERENCES sources(id),
      document_sha256 TEXT NOT NULL, parser TEXT NOT NULL, parser_version TEXT NOT NULL,
      page INTEGER NOT NULL CHECK(page > 0), start INTEGER NOT NULL CHECK(start >= 0), "end" INTEGER NOT NULL,
      original_text TEXT NOT NULL, proposed_json TEXT NOT NULL, issues_json TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
      accepted_json TEXT, decision_reason TEXT, decided_at TEXT,
      record_entity TEXT, record_id INTEGER, created_at TEXT NOT NULL,
      CHECK("end" > start),
      UNIQUE(source_id, parser, parser_version, page, start)
    )""")
    conn.execute("CREATE INDEX idx_extractions_patient ON extractions(patient_id, status)")
    # Derived, reproducible text of an attached document; keyed by hash and extractor version, never by source.
    conn.execute("""CREATE TABLE document_text (
      document_sha256 TEXT NOT NULL, extractor TEXT NOT NULL, page INTEGER NOT NULL CHECK(page > 0), text TEXT NOT NULL,
      PRIMARY KEY(document_sha256, extractor, page)
    )""")
    for table in RECORD_TABLES[1:]:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN provenance TEXT NOT NULL DEFAULT 'transcribed' "
                     "CHECK(provenance IN ('transcribed','extracted','user_observation'))")
        for column in ("anchor_page INTEGER", "anchor_quote TEXT", "anchor_start INTEGER", "anchor_end INTEGER",
                       "anchor_region TEXT", "extraction_id INTEGER REFERENCES extractions(id)"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
    conn.execute("CREATE INDEX idx_sources_document ON sources(patient_id, document_sha256)")


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "phase1 baseline schema", _m1_phase1_baseline),
    (2, "record uids, revisions, verification status, source documents, hash-chained audit", _m2_provenance_and_audit),
    (3, "evidence anchors, provenance, extraction review queue, document text cache", _m3_evidence_and_extraction),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def migrate(conn: sqlite3.Connection, up_to: int | None = None) -> list[int]:
    """Apply pending migrations in order (optionally stopping at `up_to`, for upgrade tests). Returns versions applied."""
    applied = []
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"Database schema v{current} is newer than this application (v{SCHEMA_VERSION})")
    for version, name, step in MIGRATIONS:
        if version <= current or (up_to is not None and version > up_to):
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            step(conn)
            conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)""")
            conn.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)", (version, name, now_utc()))
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def init_db() -> None:
    conn = _connect(database_path())
    conn.isolation_level = None  # migrations manage their own transactions
    try:
        migrate(conn)
    finally:
        conn.close()


# --- Writes -----------------------------------------------------------------------------------

def insert_record(conn: sqlite3.Connection, table: str, data: dict) -> dict:
    # These table/column names come only from trusted server-side constants.
    data = {**data, "uid": str(uuid.uuid4())}
    data.setdefault("created_at" if table in ("patients", "extractions") else "entered_at", now_utc())
    columns = ", ".join(data)
    slots = ", ".join("?" for _ in data)
    cursor = conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({slots})", tuple(data.values()))
    item_id = cursor.lastrowid
    row = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone())
    patient_id = item_id if table == "patients" else row["patient_id"]
    audit(conn, table, item_id, "create", row, patient_id=patient_id)
    return row


def update_record(conn: sqlite3.Connection, table: str, item_id: int, changes: dict, reason: str,
                  action: str = "correct") -> dict:
    """Apply an explicit, reasoned change. The complete prior row is kept in the audit log."""
    before = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone())
    assignments = ", ".join(f"{column}=?" for column in changes)
    conn.execute(f"UPDATE {table} SET {assignments}, revision=revision+1, updated_at=? WHERE id=?",
                 (*changes.values(), now_utc(), item_id))
    after = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone())
    audit(conn, table, item_id, action, {
        "before": before, "after": after, "changed_fields": sorted(changes), "reason": reason,
    }, patient_id=before["patient_id"], reason=reason)
    return after


def _entry_hash(prev_hash: str | None, entry: dict) -> str:
    payload = {key: entry.get(key) for key in
               ("entity_type", "entity_id", "patient_id", "action", "detail_json", "reason", "recorded_at")}
    payload["prev_hash"] = prev_hash
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def audit(conn: sqlite3.Connection, entity: str, entity_id: int, action: str, detail: dict,
          patient_id: int | None = None, reason: str | None = None) -> None:
    last = conn.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    entry = {
        "entity_type": entity, "entity_id": entity_id, "patient_id": patient_id, "action": action,
        "detail_json": json.dumps(detail, default=str, sort_keys=True), "reason": reason, "recorded_at": now_utc(),
    }
    prev_hash = last["entry_hash"] if last else None
    conn.execute(
        """INSERT INTO audit_log(entity_type,entity_id,patient_id,action,detail_json,reason,recorded_at,prev_hash,entry_hash)
           VALUES(:entity_type,:entity_id,:patient_id,:action,:detail_json,:reason,:recorded_at,:prev_hash,:entry_hash)""",
        {**entry, "prev_hash": prev_hash, "entry_hash": _entry_hash(prev_hash, entry)},
    )


def verify_audit_chain(conn: sqlite3.Connection) -> dict:
    """Recompute every entry hash. Detects edits made outside the app (e.g. with triggers dropped)."""
    prev = None
    count = 0
    for row in conn.execute("SELECT * FROM audit_log ORDER BY id"):
        entry = dict(row)
        if entry["prev_hash"] != prev or entry["entry_hash"] != _entry_hash(prev, entry):
            return {"valid": False, "entries_checked": count, "first_invalid_id": entry["id"]}
        prev = entry["entry_hash"]
        count += 1
    return {"valid": True, "entries_checked": count, "first_invalid_id": None}
