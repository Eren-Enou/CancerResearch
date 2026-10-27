"""SQLite/SQLCipher persistence, versioned migrations, and a hash-chained audit log. No network calls.

Protected mode (M3.1-C) stores the database encrypted with SQLCipher 4 (whole file: pages, journals, indexes), keyed
with a raw 256-bit key derived from the vault's DEK. A protected connection can only be opened with that key, which
exists only inside an unlocked session; there is no unencrypted fallback. Demonstration mode keeps an ordinary SQLite
file (the same engine without a key) for its separate synthetic database.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

# The sqlcipher3 wheel bundles SQLCipher 4 (a SQLite build with page encryption). Without a key it behaves like
# sqlite3, so one driver serves both modes and one set of exception types applies everywhere.
import sqlcipher3.dbapi2 as sqlite


def _cipher_version() -> str:
    version = sqlite.connect(":memory:").execute("PRAGMA cipher_version").fetchone()
    if not version or not version[0]:
        raise RuntimeError("The installed sqlcipher3 module has no SQLCipher support; OncoScope will not store "
                           "protected data without encryption")
    return version[0]


SQLCIPHER_VERSION = _cipher_version()   # fail at import, never later with a silent plaintext fallback
SQLITE_HEADER = b"SQLite format 3\x00"

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "oncoscope.sqlite3"
# Demonstration mode keeps its own database (and therefore its own document store) so it never opens protected storage.
DEMO_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "demo" / "oncoscope-demo.sqlite3"
MODES = ("protected", "demo")

# Tables holding source-linked patient records. Names are trusted server-side constants.
RECORD_TABLES = ("sources", "events", "labs", "treatments", "studies", "measurements")
OBSERVATION_TABLES = RECORD_TABLES[1:]
PROVENANCES = ("transcribed", "extracted", "user_observation")
VERIFICATION_STATES = ("entered", "reviewed", "verified")


def app_mode() -> str:
    """'protected' (the default: a local owner must unlock) or 'demo' (explicit, synthetic demonstrations and tests)."""
    mode = os.environ.get("ONCOSCOPE_MODE", "protected").strip().lower()
    if mode not in MODES:
        raise RuntimeError(f"ONCOSCOPE_MODE must be one of {', '.join(MODES)}")
    return mode


def database_path() -> Path:
    default = DEFAULT_DB_PATH if app_mode() == "protected" else DEMO_DB_PATH
    return Path(os.environ.get("ONCOSCOPE_DB_PATH", str(default))).expanduser()


def document_dir() -> Path:
    """Content-addressed store for synthetic source documents, kept beside the database."""
    return Path(os.environ.get("ONCOSCOPE_DOCUMENT_DIR", str(database_path().parent / "documents"))).expanduser()


def vault_path() -> Path:
    """The key vault (M3.1-B), beside the database by default. Demonstration mode never reads or writes it."""
    return Path(os.environ.get("ONCOSCOPE_VAULT_PATH", str(database_path().parent / "oncoscope-vault.json"))).expanduser()


def now_utc() -> str:
    """System timestamps are UTC with an explicit Z; clinical dates stay calendar dates."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def migration_state_path() -> Path:
    """Progress record of `python -m app.encrypt_migrate`; while it exists the server refuses to unlock."""
    return database_path().parent / "encrypt-migration.json"


class DatabaseLocked(RuntimeError):
    """A protected database was requested without a database key (no unlocked session)."""


class DatabaseRefused(RuntimeError):
    """The protected database cannot be opened safely: missing, plaintext, or not opening with this key."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def file_kind(path: Path) -> str:
    """'missing', 'empty', 'plaintext' (a SQLite header) or 'encrypted' (no header: SQLCipher, or not a database)."""
    if not path.exists():
        return "missing"
    with open(path, "rb") as handle:
        head = handle.read(len(SQLITE_HEADER))
    if not head:
        return "empty"
    return "plaintext" if head == SQLITE_HEADER else "encrypted"


# The database key for the current request, set from the unlocked session (or explicitly by the CLI tools). It is a
# reference to the session's bytearray, which is zeroed when the session ends.
database_key: ContextVar[bytes | bytearray | None] = ContextVar("database_key", default=None)


@contextmanager
def keyed(key: bytes | bytearray | None) -> Iterator[None]:
    marker = database_key.set(key)
    try:
        yield
    finally:
        database_key.reset(marker)


def _connect(path: Path) -> sqlite.Connection:
    """Ordinary (unencrypted) SQLite: demonstration mode, and the source side of the migration tool."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite.connect(str(path))
    conn.row_factory = sqlite.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def key_pragma(key: bytes | bytearray) -> str:
    # A raw key (x'…'), so SQLCipher skips its own passphrase KDF: the key is already a 256-bit HKDF output.
    return f"PRAGMA key = \"x'{bytes(key).hex()}'\""


def open_encrypted(path: Path, key: bytes | bytearray | None, *, create: bool = False) -> sqlite.Connection:
    """A SQLCipher connection, verified to decrypt before it is returned. Never opens a plaintext or missing file
    (unless `create` asks for a brand-new one), so a wrong or absent key can never lead to a new unencrypted database."""
    if key is None or len(key) != 32 or not any(key):
        raise DatabaseLocked("OncoScope is locked")
    kind = file_kind(path)
    if kind == "plaintext":
        raise DatabaseRefused("plaintext", "The database is not encrypted. Run `python -m app.encrypt_migrate` to "
                                           "migrate it; OncoScope will not use an unencrypted protected database.")
    if kind in ("missing", "empty") and not create:
        raise DatabaseRefused("missing", "The encrypted database file is missing or empty. Restore it from a backup; "
                                         "OncoScope will not create a new database over an existing installation.")
    if kind == "encrypted" and create:
        raise DatabaseRefused("exists", "A database file already exists; it was not replaced")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite.connect(str(path))
    try:
        conn.execute(key_pragma(key))
        # Off: the M3.0 proof of concept (SQLCipher 4.12) crashed the process on a wrong key with it on. The vault
        # authenticates the key first anyway, so SQLCipher only ever sees a key that decrypted the vault.
        conn.execute("PRAGMA cipher_memory_security = OFF")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()   # the first read checks the key (page HMAC)
    except sqlite.DatabaseError:
        conn.close()
        raise DatabaseRefused("wrong_key", "The database does not open with this installation's key (it was replaced "
                                           "or damaged). Nothing was changed.") from None
    conn.row_factory = sqlite.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")   # sorts and temporary tables never touch disk
    conn.execute("PRAGMA secure_delete = ON")
    return conn


def _protected_key() -> bytes | bytearray:
    key = database_key.get()
    if key is None or not any(key):
        raise DatabaseLocked("OncoScope is locked")
    return key


def _open() -> sqlite.Connection:
    if app_mode() == "protected":
        return open_encrypted(database_path(), _protected_key())
    path = database_path()
    if file_kind(path) == "encrypted":
        raise DatabaseRefused("encrypted", "Demonstration mode refuses to open an encrypted (protected) database")
    return _connect(path)


@contextmanager
def connection() -> Iterator[sqlite.Connection]:
    conn = _open()
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


def _m1_phase1_baseline(conn: sqlite.Connection) -> None:
    # IF NOT EXISTS lets unversioned Phase 1 databases adopt version 1 unchanged.
    for statement in PHASE1_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _m2_provenance_and_audit(conn: sqlite.Connection) -> None:
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


def _m3_evidence_and_extraction(conn: sqlite.Connection) -> None:
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


def _m4_local_owner_and_audit_actor(conn: sqlite.Connection) -> None:
    # One local owner per installation. Only an Argon2id verifier (salted, encoded by argon2-cffi) is stored.
    conn.execute("""CREATE TABLE local_owner (
      id INTEGER PRIMARY KEY CHECK(id = 1), uid TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
      password_verifier TEXT NOT NULL CHECK(password_verifier LIKE '$argon2id$%'),
      created_at TEXT NOT NULL, updated_at TEXT
    )""")
    conn.execute("""CREATE TRIGGER local_owner_no_delete BEFORE DELETE ON local_owner
                    BEGIN SELECT RAISE(ABORT, 'the local owner cannot be removed'); END""")
    # Existing entries keep NULL actors ("unknown"): nobody is invented after the fact, and their hashes are unchanged.
    for column in ("actor_id TEXT", "actor_label TEXT"):
        conn.execute(f"ALTER TABLE audit_log ADD COLUMN {column}")


def _m5_vault_binding(conn: sqlite.Connection) -> None:
    # Which vault and DEK this installation uses. Set once the vault is written and verified; while NULL the next unlock
    # creates (or adopts) the vault. Once set, a missing or different vault is refused, never silently replaced.
    for column in ("vault_id TEXT", "vault_key_id TEXT"):
        conn.execute(f"ALTER TABLE local_owner ADD COLUMN {column}")


def _m6_retire_passphrase_verifier(conn: sqlite.Connection) -> None:
    # Since M3.1-B the vault decides whether a passphrase is correct, and an encrypted database cannot be read before
    # unlock anyway. The owner row stays: it binds this database to its vault and names the audit actor.
    conn.execute("ALTER TABLE local_owner DROP COLUMN password_verifier")


MIGRATIONS: list[tuple[int, str, Callable[[sqlite.Connection], None]]] = [
    (1, "phase1 baseline schema", _m1_phase1_baseline),
    (2, "record uids, revisions, verification status, source documents, hash-chained audit", _m2_provenance_and_audit),
    (3, "evidence anchors, provenance, extraction review queue, document text cache", _m3_evidence_and_extraction),
    (4, "local owner verifier, audit actor", _m4_local_owner_and_audit_actor),
    (5, "key vault binding", _m5_vault_binding),
    (6, "retire the M3.1-A passphrase verifier (the vault is authoritative)", _m6_retire_passphrase_verifier),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]
LAST_PLAINTEXT_SCHEMA = 5   # what the migration tool brings a legacy plaintext database to before encrypting it


def migrate(conn: sqlite.Connection, up_to: int | None = None) -> list[int]:
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
    """Applies pending migrations. Protected mode needs the database key (it runs at unlock, not at server start)."""
    conn = _open()
    conn.isolation_level = None  # migrations manage their own transactions
    try:
        migrate(conn)
    finally:
        conn.close()


# --- Writes -----------------------------------------------------------------------------------

# Who is responsible for the current request's writes: (actor_id, actor_label). Set by the API's access middleware
# from the authenticated session (or to the demonstration actor in demo mode); never taken from request content.
current_actor: ContextVar[tuple[str, str] | None] = ContextVar("current_actor", default=None)
DEMO_ACTOR = ("demo", "Synthetic demonstration mode (no sign-in)")

def insert_record(conn: sqlite.Connection, table: str, data: dict) -> dict:
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


def update_record(conn: sqlite.Connection, table: str, item_id: int, changes: dict, reason: str,
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
    # Actors were added in schema v4. Hashing them only when present keeps every earlier entry's hash unchanged.
    if entry.get("actor_id") is not None:
        payload["actor_id"] = entry["actor_id"]
        payload["actor_label"] = entry.get("actor_label")
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def audit(conn: sqlite.Connection, entity: str, entity_id: int, action: str, detail: dict,
          patient_id: int | None = None, reason: str | None = None) -> None:
    actor = current_actor.get()
    if actor is None and app_mode() == "protected":
        raise RuntimeError("Refusing to record a change without an authenticated actor")
    last = conn.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    entry = {
        "entity_type": entity, "entity_id": entity_id, "patient_id": patient_id, "action": action,
        "detail_json": json.dumps(detail, default=str, sort_keys=True), "reason": reason, "recorded_at": now_utc(),
        "actor_id": actor[0] if actor else None, "actor_label": actor[1] if actor else None,
    }
    prev_hash = last["entry_hash"] if last else None
    row = {**entry, "prev_hash": prev_hash, "entry_hash": _entry_hash(prev_hash, entry)}
    if actor is None:  # no actor columns named, so pre-v4 databases (upgrade tests) can still be written
        del row["actor_id"], row["actor_label"]
    conn.execute(f"INSERT INTO audit_log({','.join(row)}) VALUES({','.join(':' + k for k in row)})", row)


def audit_head(conn: sqlite.Connection) -> dict:
    """The chain's last entry. Recorded outside the database (backup manifests, a note), it exposes truncation that the
    chain itself cannot: deleting the newest entries leaves a shorter chain that still verifies."""
    row = conn.execute("SELECT count(*) AS n, max(id) AS last_id FROM audit_log").fetchone()
    last = conn.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return {"entries": row["n"] if isinstance(row, sqlite.Row) else row[0],
            "last_id": row["last_id"] if isinstance(row, sqlite.Row) else row[1],
            "last_hash": (last[0] if last else None)}


def verify_audit_chain(conn: sqlite.Connection) -> dict:
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
