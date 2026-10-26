"""OncoScope local API: descriptive, source-linked clinical record organization."""
from contextlib import asynccontextmanager
import json
import sqlite3
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import auth, documents, extraction
from .db import (
    DEMO_ACTOR, OBSERVATION_TABLES, RECORD_TABLES, SCHEMA_VERSION, app_mode, audit, audit_head, connection, current_actor,
    init_db, insert_record, now_utc, update_record, verify_audit_chain,
)
from .models import (
    ANCHOR_FIELDS, CREATE_MODELS, EDITABLE_FIELDS, NULLABLE_FIELDS, Correction, EventCreate, LabCorrection, LabCreate,
    ExtractionAccept, ExtractionReject, MeasurementCreate, OwnerSetup, PatientCreate, SourceCreate, StatusChange, StudyCreate,
    TreatmentCreate, Unlock,
)
from .models import ChangePassphrase, Recover
from .evidence import build_findings
from .reporting import build_csv, build_export, build_report, patient_records
from .units import exact_difference, format_number, rule_for, test_key, unit_spelling_conflict


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    auth.reset()
    if app_mode() == "demo":
        with connection() as conn:
            if auth.owner(conn):
                raise RuntimeError("Demonstration mode refuses to open protected storage (this database has a local owner)")
    yield


app = FastAPI(
    title="OncoScope local API", version="0.5.0", lifespan=lifespan,
    description="Local-only prototype. Organizes sources; does not diagnose, stage, or assess clinical response.",
)
# Rejects requests whose Host header is not loopback (DNS-rebinding protection for a local service).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


# The only operations reachable without a session in protected mode. Everything else needs a valid bearer token.
PUBLIC_ROUTES = {("GET", "/api/health"), ("GET", "/api/auth/status"), ("POST", "/api/auth/setup"), ("POST", "/api/auth/unlock"),
                 ("POST", "/api/auth/recover")}
LOOPBACK_PEERS = {"127.0.0.1", "::1", "testclient"}   # "testclient" is the in-process test transport, not a network peer
LOCKED = {"detail": "OncoScope is locked. Unlock it to continue."}


def foreign_origin(request: Request) -> bool:
    """True for requests a browser marks as coming from another web origin. The UI is same-origin (via the dev proxy)."""
    if request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        return True
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    return origin is not None and origin not in (f"http://{host}", f"https://{host}")


@app.middleware("http")
async def access_control(request: Request, call_next):
    # Loopback-only even if someone binds the server to another interface.
    if request.client is None or request.client.host not in LOOPBACK_PEERS:
        return JSONResponse({"detail": "OncoScope accepts connections from this computer only"}, status_code=403)
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    if foreign_origin(request):
        return JSONResponse({"detail": "Requests from other web origins are refused"}, status_code=403)
    request.state.session = None
    if app_mode() == "demo":
        actor = DEMO_ACTOR
    elif (request.method, request.url.path) in PUBLIC_ROUTES:
        actor = None
    else:
        # Missing, unknown, expired and revoked tokens all get the same answer.
        session = auth.session_for(auth.bearer_token(request.headers.get("authorization")))
        if session is None:
            return JSONResponse(LOCKED, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        actor = session.actor
        request.state.session = session
    marker = current_actor.set(actor)
    try:
        return await call_next(request)
    finally:
        current_actor.reset(marker)


@app.middleware("http")
async def no_store_headers(request: Request, call_next):
    # Case content must not land in browser or proxy caches; documents and reports are fetched fresh every time.
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers["Referrer-Policy"] = "no-referrer"
    return response



@app.exception_handler(RequestValidationError)
async def validation_handler(_request: Request, exc: RequestValidationError):
    # The default handler echoes the rejected input, which crashes JSON encoding for NaN/Infinity
    # and needlessly reflects submitted record content. Report location and message only.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", "Invalid value")} for e in exc.errors()
    ]})


@app.exception_handler(auth.AuthError)
async def auth_error_handler(_request: Request, exc: auth.AuthError):
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail}, headers=headers)


ENTITY_LABEL = {"sources": "source", "events": "event", "labs": "lab", "treatments": "treatment",
                "studies": "study", "measurements": "measurement"}


def rows(conn: sqlite3.Connection, query: str, args: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(query, args).fetchall()]


def get_patient(conn: sqlite3.Connection, patient_id: int) -> dict:
    row = conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Patient record not found")
    return dict(row)


def ensure_patient(conn: sqlite3.Connection, patient_id: int) -> None:
    get_patient(conn, patient_id)


def ensure_source(conn: sqlite3.Connection, patient_id: int, source_id: int) -> None:
    if not conn.execute("SELECT 1 FROM sources WHERE id=? AND patient_id=?", (source_id, patient_id)).fetchone():
        raise HTTPException(422, f"Source #{source_id} must exist and belong to this patient record")


def get_record(conn: sqlite3.Connection, entity: str, record_id: int) -> dict:
    if entity not in RECORD_TABLES:
        raise HTTPException(404, f"Unknown record type '{entity}'")
    row = conn.execute(f"SELECT * FROM {entity} WHERE id=?", (record_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"{ENTITY_LABEL[entity].capitalize()} #{record_id} not found")
    return dict(row)


def check_references(conn: sqlite3.Connection, table: str, data: dict, record_id: int | None = None) -> None:
    """Cross-record rules the request model alone cannot check."""
    if table != "sources":
        ensure_source(conn, data["patient_id"], data["source_id"])
    if table == "measurements":
        study = conn.execute("SELECT 1 FROM studies WHERE id=? AND patient_id=?", (data["study_id"], data["patient_id"])).fetchone()
        if not study:
            raise HTTPException(422, f"Study #{data['study_id']} must exist and belong to this patient record")
    if table == "labs":
        existing = {r["unit"] for r in rows(conn, "SELECT test, unit FROM labs WHERE patient_id=? AND id IS NOT ?",
                                            (data["patient_id"], record_id)) if test_key(r["test"]) == test_key(data["test"])}
        clash = unit_spelling_conflict(data["unit"], existing)
        if clash:
            raise HTTPException(422, f"Unit '{data['unit']}' differs from the existing '{clash}' for {data['test']} only by "
                                     f"letter case. Use '{clash}' so the values stay in one series.")


def document_pages(conn: sqlite3.Connection, source: dict) -> list[str]:
    """Text pages of a source's attached document, extracted deterministically and cached by hash + extractor."""
    sha, media = source["document_sha256"], source["document_media_type"]
    extractor = documents.extractor_for(media)
    if extractor is None:
        return []
    cached = rows(conn, "SELECT text FROM document_text WHERE document_sha256=? AND extractor=? ORDER BY page", (sha, extractor))
    if cached:
        return [r["text"] for r in cached]
    try:
        data, actual = documents.load_verified(sha)
    except documents.DocumentError as exc:
        raise HTTPException(409, "The recorded document reference is invalid; the source record may have been altered") from exc
    if data is None or actual != sha:
        raise HTTPException(409, f"Document of source #{source['id']} is missing or fails its SHA-256 check")
    try:
        pages = documents.extract_pages(data, media)
    except documents.DocumentError as exc:
        raise HTTPException(422, str(exc)) from exc
    conn.executemany("INSERT OR IGNORE INTO document_text(document_sha256, extractor, page, text) VALUES(?,?,?,?)",
                     [(sha, extractor, i, text) for i, text in enumerate(pages, 1)])
    return pages


def apply_evidence(conn: sqlite3.Connection, table: str, data: dict) -> dict:
    """Checks an observation's anchor against its source document and resolves exact character offsets."""
    if table not in OBSERVATION_TABLES or not any(data.get(f) is not None for f in ANCHOR_FIELDS):
        return data
    source = get_record(conn, "sources", data["source_id"])
    if not source["document_sha256"]:
        raise HTTPException(422, f"Attach the document to source #{source['id']} before anchoring an observation to it")
    media = source["document_media_type"]
    try:
        if data.get("anchor_region") is not None:
            if media.startswith("text/plain"):
                raise documents.DocumentError("Text documents are anchored by quoted passage, not by region")
            documents.parse_region(data["anchor_region"])
        if data.get("anchor_quote") is not None:
            if media.startswith("image/"):
                raise documents.DocumentError("Image documents have no text layer; anchor them by region (x,y,w,h)")
            page, start, end = documents.resolve_quote(document_pages(conn, source), data.get("anchor_page"),
                                                       data["anchor_quote"], data.get("anchor_start"), data.get("anchor_end"))
            return {**data, "anchor_page": page, "anchor_start": start, "anchor_end": end}
        if data.get("anchor_page") is not None:
            count = 1 if media.startswith("image/") else len(document_pages(conn, source))
            if data["anchor_page"] > count:
                raise documents.DocumentError(f"Page {data['anchor_page']} does not exist; the document has {count} page(s)")
    except documents.DocumentError as exc:
        raise HTTPException(422, str(exc)) from exc
    return data


def create(table: str, payload: BaseModel) -> dict:
    data = payload.model_dump(mode="json")
    if data.get("provenance") == "extracted":
        raise HTTPException(422, "Extracted observations are created only by accepting a review-queue proposal")
    with connection() as conn:
        ensure_patient(conn, data["patient_id"])
        check_references(conn, table, data)
        data = apply_evidence(conn, table, data)
        try:
            return insert_record(conn, table, data)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, f"Conflicting or invalid record: {exc}") from exc


def validation_error(exc: ValidationError) -> HTTPException:
    return HTTPException(422, [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors(include_url=False)])


def correct(entity: str, record_id: int, correction: Correction) -> dict:
    with connection() as conn:
        before = get_record(conn, entity, record_id)
        unknown = set(correction.changes) - set(EDITABLE_FIELDS[entity])
        if unknown:
            raise HTTPException(422, f"Field(s) not correctable for {entity}: {', '.join(sorted(unknown))}")
        for name, value in correction.changes.items():
            if value is None and (entity, name) not in NULLABLE_FIELDS:
                raise HTTPException(422, f"{name} cannot be cleared")
        if "provenance" in correction.changes and "extracted" in (correction.changes["provenance"], before.get("provenance")):
            raise HTTPException(422, "The provenance of an extracted observation is fixed by its review-queue decision")
        merged = {name: before[name] for name in EDITABLE_FIELDS[entity]} | correction.changes
        if entity in OBSERVATION_TABLES and {"anchor_quote", "anchor_page", "source_id"} & set(correction.changes) \
                and "anchor_start" not in correction.changes:
            merged |= {"anchor_start": None, "anchor_end": None}  # re-locate the passage instead of trusting stale offsets
        try:
            validated = CREATE_MODELS[entity].model_validate({**merged, "patient_id": before["patient_id"]})
        except ValidationError as exc:
            raise validation_error(exc) from exc
        data = validated.model_dump(mode="json")
        check_references(conn, entity, data, record_id)
        data = apply_evidence(conn, entity, data)
        changes = {name: data[name] for name in EDITABLE_FIELDS[entity] if data[name] != before[name]}
        if not changes:
            raise HTTPException(422, "Correction does not change any stored value")
        # A changed value is no longer the value that was reviewed or verified.
        if before["verification_status"] != "entered":
            changes["verification_status"] = "entered"
        try:
            return update_record(conn, entity, record_id, changes, correction.reason)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, f"Conflicting or invalid record: {exc}") from exc


# --- Health and patients ------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "scope": "local", "clinical_decision_support": False, "schema_version": SCHEMA_VERSION,
            "mode": app_mode()}


# --- Protected access ---------------------------------------------------------------------------

def record_events(conn: sqlite3.Connection, events: list[tuple[str, dict]]) -> None:
    """Security events go into the same hash-chained audit log, with no case and never with any secret in them."""
    for action, detail in events:
        audit(conn, "local_owner", 1, action, detail)


def open_session(conn: sqlite3.Connection, result: auth.Unlocked) -> dict:
    """Starts the session for a setup/unlock/recovery and returns it. The reply is no-store and shown once: the token
    is kept in UI memory only; a recovery key, when one is issued, must be shown to the owner and is never stored."""
    row = result.owner
    marker = current_actor.set(auth.actor_of(row))
    try:
        record_events(conn, result.events)
        token = auth.start_session(row, result.opened)
        audit(conn, "local_owner", 1, "unlock", {"vault_id": result.opened.doc["vault_id"]})
    finally:
        current_actor.reset(marker)
    return {"token": token, "actor": {"id": auth.actor_of(row)[0], "label": row["display_name"]},
            "idle_seconds": auth.idle_seconds(), "max_seconds": auth.max_seconds(),
            "vault": {"key_id": result.opened.doc["key_id"], "recovery_confirmed": result.opened.doc["recovery_confirmed"]},
            "recovery_key": result.recovery_key}


def require_protected(action: str) -> None:
    if app_mode() != "protected":
        raise HTTPException(409, f"Demonstration mode has nothing to {action}")


def current_session(request: Request) -> auth.Session:
    session = getattr(request.state, "session", None)
    if session is None:   # only reachable in demonstration mode, where there are no sessions or keys
        raise HTTPException(409, "Demonstration mode has no key vault")
    return session


@app.get("/api/auth/status")
def auth_status():
    """Enough for the lock screen; nothing about cases or the owner."""
    protected = app_mode() == "protected"
    with connection() as conn:
        row = auth.owner(conn) if protected else None
    return {"mode": app_mode(), "setup_required": protected and row is None,
            "recovery_available": bool(row and row["vault_id"]), "encrypted_at_rest": False}


@app.post("/api/auth/setup", status_code=201)
def auth_setup(payload: OwnerSetup):
    with connection() as conn:
        return open_session(conn, auth.setup(conn, payload.display_name, payload.passphrase))


@app.post("/api/auth/unlock")
def auth_unlock(payload: Unlock):
    require_protected("unlock")
    with connection() as conn:
        return open_session(conn, auth.unlock(conn, payload.passphrase))


@app.post("/api/auth/recover")
def auth_recover(payload: Recover):
    """Recovery key + new passphrase → the same DEK, re-wrapped under the new passphrase, and a new session."""
    require_protected("recover")
    with connection() as conn:
        return open_session(conn, auth.recover(conn, payload.recovery_key, payload.new_passphrase))


@app.get("/api/vault/status")
def vault_status(request: Request):
    return auth.vault_status(current_session(request))


@app.post("/api/vault/passphrase")
def vault_change_passphrase(payload: ChangePassphrase, request: Request):
    session = current_session(request)
    with connection() as conn:
        record_events(conn, auth.change_passphrase(conn, session, payload.current_passphrase, payload.new_passphrase))
    return {"changed": True, "key_id": session.keys.doc["key_id"]}


@app.post("/api/vault/recovery-key/confirm")
def vault_confirm_recovery_key(request: Request):
    session = current_session(request)
    events = auth.confirm_recovery_key(session)
    with connection() as conn:
        record_events(conn, events)
    return {"recovery_confirmed": True}


@app.post("/api/auth/lock")
def auth_lock(request: Request):
    """Revokes the presented session immediately and drops its vault key from memory."""
    require_protected("lock")
    with connection() as conn:
        audit(conn, "local_owner", 1, "lock", {})
    auth.end_session(auth.bearer_token(request.headers.get("authorization")))
    return {"locked": True}


@app.get("/api/patients")
def patients():
    with connection() as conn:
        return rows(conn, "SELECT * FROM patients ORDER BY id")


@app.post("/api/patients", status_code=201)
def post_patient(payload: PatientCreate):
    with connection() as conn:
        return insert_record(conn, "patients", {"label": payload.label, "is_synthetic": 1})


@app.get("/api/patients/{patient_id}/dashboard")
def dashboard(patient_id: int):
    with connection() as conn:
        patient = get_patient(conn, patient_id)
        counts = {
            table: conn.execute(f"SELECT count(*) FROM {table} WHERE patient_id=?", (patient_id,)).fetchone()[0]
            for table in RECORD_TABLES
        }
        return {"patient": patient, "counts": counts}


def list_records(table: str, patient_id: int, sort: str):
    with connection() as conn:
        ensure_patient(conn, patient_id)
        return rows(conn, f"SELECT * FROM {table} WHERE patient_id=? ORDER BY {sort}", (patient_id,))


# --- Record creation and listing ----------------------------------------------------------------

@app.get("/api/patients/{patient_id}/sources")
def sources(patient_id: int):
    return list_records("sources", patient_id, "source_date DESC, id DESC")


@app.post("/api/sources", status_code=201)
def post_source(payload: SourceCreate):
    return create("sources", payload)


@app.get("/api/patients/{patient_id}/events")
def events(patient_id: int):
    return list_records("events", patient_id, "date DESC, id DESC")


@app.post("/api/events", status_code=201)
def post_event(payload: EventCreate):
    return create("events", payload)


@app.get("/api/patients/{patient_id}/labs")
def labs(patient_id: int, test: str | None = None):
    with connection() as conn:
        ensure_patient(conn, patient_id)
        result = rows(conn, "SELECT * FROM labs WHERE patient_id=? ORDER BY date ASC, id ASC", (patient_id,))
        return [r for r in result if not test or test_key(r["test"]) == test_key(test)]


@app.get("/api/patients/{patient_id}/labs/series")
def lab_series(patient_id: int, test: str = Query(min_length=1), unit: str | None = None):
    """One plottable series: same test, one exact unit. Other units are listed, never converted."""
    matching = [r for r in labs(patient_id) if test_key(r["test"]) == test_key(test)]
    units_seen = sorted({r["unit"] for r in matching})
    rule = rule_for(test)
    if unit is None:
        unit = rule.accepted_units[0] if rule else (matching[-1]["unit"] if matching else None)
    points = [r for r in matching if r["unit"] == unit]
    # Arithmetic between consecutive documented values in the same unit; no trend or response is inferred.
    changes = [{"from_id": a["id"], "to_id": b["id"], "from_date": a["date"], "to_date": b["date"],
                "difference": exact_difference(a["value"], b["value"]), "unit": unit}
               for a, b in zip(points, points[1:])]
    return {
        "test": rule.display if rule else test, "unit": unit, "available_units": units_seen,
        "points": points, "changes": changes,
        "excluded": [r for r in matching if r["unit"] != unit],
        "note": "Values are plotted exactly as entered. Results in other units are excluded, not converted.",
    }


@app.post("/api/labs", status_code=201)
def post_lab(payload: LabCreate):
    return create("labs", payload)


@app.patch("/api/labs/{lab_id}")
def correct_lab(lab_id: int, correction: LabCorrection):
    changes = correction.model_dump(exclude={"reason"})
    with connection() as conn:
        current = get_record(conn, "labs", lab_id)
    changes = {k: v for k, v in changes.items() if v != current[k]}
    if not changes:
        raise HTTPException(422, "Correction does not change any stored value")
    return correct("labs", lab_id, Correction(changes=changes, reason=correction.reason))


@app.get("/api/patients/{patient_id}/treatments")
def treatments(patient_id: int):
    return list_records("treatments", patient_id, "date ASC, id ASC")


@app.post("/api/treatments", status_code=201)
def post_treatment(payload: TreatmentCreate):
    return create("treatments", payload)


@app.get("/api/patients/{patient_id}/studies")
def studies(patient_id: int):
    return list_records("studies", patient_id, "date DESC, id DESC")


@app.post("/api/studies", status_code=201)
def post_study(payload: StudyCreate):
    return create("studies", payload)


@app.get("/api/patients/{patient_id}/measurements")
def measurements(patient_id: int):
    return list_records("measurements", patient_id, "study_id ASC, lesion_key ASC")


@app.post("/api/measurements", status_code=201)
def post_measurement(payload: MeasurementCreate):
    return create("measurements", payload)


# --- Corrections, verification state, history -------------------------------------------------

@app.get("/api/records/{entity}/{record_id}")
def record(entity: str, record_id: int):
    with connection() as conn:
        return get_record(conn, entity, record_id)


@app.patch("/api/records/{entity}/{record_id}")
def correct_record(entity: str, record_id: int, correction: Correction):
    return correct(entity, record_id, correction)


@app.post("/api/records/{entity}/{record_id}/status")
def set_status(entity: str, record_id: int, change: StatusChange):
    with connection() as conn:
        current = get_record(conn, entity, record_id)
        if current["verification_status"] == change.status:
            raise HTTPException(422, f"Record is already '{change.status}'")
        return update_record(conn, entity, record_id, {"verification_status": change.status}, change.reason, action="status")


@app.get("/api/records/{entity}/{record_id}/history")
def record_history(entity: str, record_id: int):
    with connection() as conn:
        get_record(conn, entity, record_id)
        return rows(conn, "SELECT * FROM audit_log WHERE entity_type=? AND entity_id=? ORDER BY id", (entity, record_id))


# --- Source documents ----------------------------------------------------------------------------

@app.put("/api/sources/{source_id}/document", status_code=201)
async def attach_document(source_id: int, request: Request):
    # The filename travels in a header, not the URL, so it never appears in access logs or browser history.
    # application/octet-stream is not a CORS "simple" type, so other web origins cannot send this cross-site.
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
        raise HTTPException(415, "Send the document body as application/octet-stream")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > documents.MAX_DOCUMENT_BYTES:
        raise HTTPException(413, "Document exceeds the 10 MB limit")
    filename = unquote(request.headers.get("x-document-filename", ""))
    if not 1 <= len(filename) <= 255:
        raise HTTPException(422, "Send the original filename (1-255 characters) in the X-Document-Filename header")
    data = await request.body()
    try:
        name = documents.safe_filename(filename)
        media_type, sha256 = documents.inspect(name, data)
    except documents.DocumentError as exc:
        raise HTTPException(422, str(exc)) from exc
    with connection() as conn:
        source = get_record(conn, "sources", source_id)
        if source["document_sha256"]:
            raise HTTPException(409, "A document is already attached to this source; attached documents are immutable")
        twin = conn.execute("SELECT id, label FROM sources WHERE patient_id=? AND document_sha256=?",
                            (source["patient_id"], sha256)).fetchone()
        if twin:
            raise HTTPException(409, f"Duplicate import: this exact file is already attached to source #{twin['id']} "
                                     f"({twin['label']}). Link observations to that source instead.")
        documents.store(sha256, data)
        changes = {"document_sha256": sha256, "document_filename": name, "document_media_type": media_type,
                   "document_size": len(data), "document_attached_at": now_utc()}
        return update_record(conn, "sources", source_id, changes, f"Attached document {name}", action="attach_document")


def _document_or_error(source_id: int) -> tuple[dict, bytes | None, str | None]:
    with connection() as conn:
        source = get_record(conn, "sources", source_id)
    if not source["document_sha256"]:
        raise HTTPException(404, "No document attached to this source")
    try:
        data, actual = documents.load_verified(source["document_sha256"])
    except documents.DocumentError as exc:  # a stored reference that is not a valid hash: refuse, never touch the path
        raise HTTPException(409, "The recorded document reference is invalid; the source record may have been altered") from exc
    return source, data, actual


@app.get("/api/sources/{source_id}/document/verify")
def verify_document(source_id: int):
    source, data, actual = _document_or_error(source_id)
    return {"source_id": source_id, "expected_sha256": source["document_sha256"], "actual_sha256": actual,
            "present": data is not None, "valid": actual == source["document_sha256"]}


@app.get("/api/sources/{source_id}/document/text")
def document_text(source_id: int):
    """Deterministic text layer used for anchoring and extraction. The original file is never modified."""
    with connection() as conn:
        source = get_record(conn, "sources", source_id)
        if not source["document_sha256"]:
            raise HTTPException(404, "No document attached to this source")
        media = source["document_media_type"]
        pages = document_pages(conn, source)
    return {"source_id": source_id, "document_sha256": source["document_sha256"], "media_type": media,
            "filename": source["document_filename"], "extractor": documents.extractor_for(media),
            "pages": [{"page": i, "text": t} for i, t in enumerate(pages, 1)],
            "has_text_layer": any(t.strip() for t in pages)}


@app.get("/api/sources/{source_id}/document")
def get_document(source_id: int):
    source, data, actual = _document_or_error(source_id)
    if data is None:
        raise HTTPException(410, "Stored document file is missing")
    if actual != source["document_sha256"]:
        raise HTTPException(409, "Integrity check failed: stored document does not match its recorded SHA-256")
    return Response(data, media_type=source["document_media_type"], headers={
        "Content-Disposition": f'inline; filename="{source["document_filename"]}"',
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "X-Document-SHA256": actual,
    })


# --- Extraction review queue --------------------------------------------------------------------

def extraction_view(row: dict) -> dict:
    out = {k: v for k, v in row.items() if not k.endswith("_json")}
    out["proposed"] = json.loads(row["proposed_json"])
    out["issues"] = json.loads(row["issues_json"])
    out["accepted"] = json.loads(row["accepted_json"]) if row["accepted_json"] else None
    return out


def get_extraction(conn: sqlite3.Connection, extraction_id: int) -> dict:
    row = conn.execute("SELECT * FROM extractions WHERE id=?", (extraction_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Extraction #{extraction_id} not found")
    return dict(row)


@app.post("/api/sources/{source_id}/extract", status_code=201)
def run_extraction(source_id: int):
    """Proposes CA-125 transcriptions from a source document. Re-running never duplicates a proposal."""
    with connection() as conn:
        source = get_record(conn, "sources", source_id)
        if not source["document_sha256"]:
            raise HTTPException(422, "Attach a document to this source before running extraction")
        pages = document_pages(conn, source)
        if not any(page.strip() for page in pages):
            raise HTTPException(422, "This document has no text layer (image or scanned PDF); transcribe it manually with a region anchor")
        created, existing = [], []
        for proposal in extraction.extract_ca125(pages, source["source_date"]):
            # Any parser version that already proposed this passage counts: a new version never duplicates the queue.
            found = conn.execute("""SELECT * FROM extractions WHERE source_id=? AND parser=? AND page=? AND start=?
                                    ORDER BY id DESC LIMIT 1""", (source_id, extraction.PARSER, proposal.page, proposal.start)).fetchone()
            if found:
                existing.append(extraction_view(dict(found)))
                continue
            row = insert_record(conn, "extractions", {
                "patient_id": source["patient_id"], "source_id": source_id, "document_sha256": source["document_sha256"],
                "parser": extraction.PARSER, "parser_version": extraction.PARSER_VERSION, "page": proposal.page,
                "start": proposal.start, "end": proposal.end, "original_text": proposal.original_text,
                "proposed_json": json.dumps(proposal.fields, sort_keys=True), "issues_json": json.dumps(proposal.issues),
            })
            created.append(extraction_view(row))
        return {"parser": extraction.PARSER, "parser_version": extraction.PARSER_VERSION, "created": created, "existing": existing,
                "note": "Proposals are pending review. None of them is a record until a person accepts it."}


@app.get("/api/patients/{patient_id}/extractions")
def list_extractions(patient_id: int, status: str | None = Query(default=None, pattern="^(pending|accepted|rejected)$")):
    with connection() as conn:
        ensure_patient(conn, patient_id)
        query, args = "SELECT * FROM extractions WHERE patient_id=?", (patient_id,)
        if status:
            query, args = query + " AND status=?", args + (status,)
        return [extraction_view(r) for r in rows(conn, query + " ORDER BY source_id, page, start", args)]


def _decide(conn: sqlite3.Connection, row: dict, status: str, reason: str | None, accepted: dict | None, record_id: int | None,
            detail: dict) -> dict:
    conn.execute("""UPDATE extractions SET status=?, decision_reason=?, decided_at=?, accepted_json=?, record_entity=?, record_id=?
                    WHERE id=?""", (status, reason, now_utc(), json.dumps(accepted, sort_keys=True) if accepted else None,
                                    "labs" if record_id else None, record_id, row["id"]))
    audit(conn, "extractions", row["id"], "accept" if status == "accepted" else "reject", detail,
          patient_id=row["patient_id"], reason=reason)
    return extraction_view(dict(conn.execute("SELECT * FROM extractions WHERE id=?", (row["id"],)).fetchone()))


@app.post("/api/extractions/{extraction_id}/accept")
def accept_extraction(extraction_id: int, decision: ExtractionAccept):
    """Turns a reviewed proposal into a lab record with provenance 'extracted' and status 'reviewed' (never 'verified')."""
    with connection() as conn:
        row = get_extraction(conn, extraction_id)
        if row["status"] != "pending":
            raise HTTPException(409, f"Extraction #{extraction_id} was already {row['status']}")
        source = get_record(conn, "sources", row["source_id"])
        if source["document_sha256"] != row["document_sha256"]:
            raise HTTPException(409, "The source document no longer matches the document this proposal was extracted from")
        proposed, issues = json.loads(row["proposed_json"]), json.loads(row["issues_json"])
        final = {k: proposed.get(k) for k in ("date", "test", "value", "unit")} | decision.changes
        changed = sorted(k for k in decision.changes if decision.changes[k] != proposed.get(k))
        if (changed or issues) and not decision.reason:
            raise HTTPException(422, "A reason is required when accepting a proposal that has issues or was corrected")
        missing = [k for k, v in final.items() if v in (None, "")]
        if missing:
            raise HTTPException(422, f"Supply the missing field(s) before accepting: {', '.join(missing)}")
        try:
            lab = CREATE_MODELS["labs"].model_validate({**final, "patient_id": row["patient_id"], "source_id": row["source_id"],
                                                        "anchor_page": row["page"], "anchor_quote": row["original_text"],
                                                        "anchor_start": row["start"], "anchor_end": row["end"]})
        except ValidationError as exc:
            raise validation_error(exc) from exc
        data = lab.model_dump(mode="json") | {"provenance": "extracted"}
        check_references(conn, "labs", data)
        data = apply_evidence(conn, "labs", data)  # re-checks the passage against the stored document text
        duplicate = conn.execute("""SELECT id FROM labs WHERE patient_id=? AND source_id=? AND date=? AND value=? AND unit=?""",
                                 (data["patient_id"], data["source_id"], data["date"], data["value"], data["unit"])).fetchone()
        if duplicate:
            raise HTTPException(409, f"Duplicate: lab #{duplicate['id']} already records this value from the same source")
        record = insert_record(conn, "labs", {**data, "extraction_id": row["id"], "verification_status": "reviewed"})
        accepted = {k: data[k] for k in ("date", "test", "value", "unit")}
        return {"extraction": _decide(conn, row, "accepted", decision.reason, accepted, record["id"], {
            "proposed": proposed, "accepted": accepted, "changed_fields": changed, "issues": issues,
            "record": {"entity": "labs", "id": record["id"]}, "reason": decision.reason}), "record": record}


@app.post("/api/extractions/{extraction_id}/reject")
def reject_extraction(extraction_id: int, decision: ExtractionReject):
    with connection() as conn:
        row = get_extraction(conn, extraction_id)
        if row["status"] != "pending":
            raise HTTPException(409, f"Extraction #{extraction_id} was already {row['status']}")
        return _decide(conn, row, "rejected", decision.reason, None, None, {
            "proposed": json.loads(row["proposed_json"]), "issues": json.loads(row["issues_json"]), "reason": decision.reason})


# --- Derived views ------------------------------------------------------------------------------

@app.get("/api/patients/{patient_id}/timeline")
def timeline(patient_id: int):
    with connection() as conn:
        ensure_patient(conn, patient_id)
        records = patient_records(conn, patient_id)
        study_by_id = {s["id"]: s for s in records["studies"]}
        combined = []

        def add(entity: str, r: dict, day: str, label: str):
            combined.append({"id": r["id"], "uid": r["uid"], "entity": entity, "kind": ENTITY_LABEL[entity],
                             "date": day, "label": label, "source_id": r["source_id"], "revision": r["revision"],
                             "verification_status": r["verification_status"], "provenance": r["provenance"],
                             "anchor_page": r["anchor_page"], "anchor_start": r["anchor_start"], "anchor_end": r["anchor_end"],
                             "anchor_region": r["anchor_region"], "anchor_quote": r["anchor_quote"]})

        for r in records["events"]:
            add("events", r, r["date"], r["title"])
        for r in records["treatments"]:
            add("treatments", r, r["date"], f"{r['regimen']} (cycle {r['cycle']})")
        for r in records["labs"]:
            add("labs", r, r["date"], f"{r['test']}: {format_number(r['value'])} {r['unit']}")
        for r in records["studies"]:
            add("studies", r, r["date"], f"{r['study_label']} ({r['modality']})")
        for r in records["measurements"]:
            study = study_by_id[r["study_id"]]
            short = f", short {format_number(r['short_axis_mm'])} mm" if r["short_axis_mm"] is not None else ""
            add("measurements", r, study["date"],
                f"{r['lesion_key']} ({r['site']}): long {format_number(r['long_axis_mm'])} mm{short} — study #{study['id']}")
        return sorted(combined, key=lambda r: (r["date"], r["kind"], r["id"]), reverse=True)


@app.get("/api/patients/{patient_id}/compare")
def compare(patient_id: int, baseline_id: int = Query(gt=0), followup_id: int = Query(gt=0)):
    with connection() as conn:
        ensure_patient(conn, patient_id)
        if baseline_id == followup_id:
            raise HTTPException(422, "Choose two distinct studies")
        scans = {r["id"]: r for r in rows(conn, "SELECT * FROM studies WHERE patient_id=? AND id IN (?,?)",
                                          (patient_id, baseline_id, followup_id))}
        missing = [str(i) for i in (baseline_id, followup_id) if i not in scans]
        if missing:
            raise HTTPException(422, f"Study #{', #'.join(missing)} does not exist for this patient record")
        baseline, followup = scans[baseline_id], scans[followup_id]
        if baseline["date"] > followup["date"]:
            raise HTTPException(422, f"Earlier study #{baseline_id} ({baseline['date']}) is dated after later study "
                                     f"#{followup_id} ({followup['date']}); swap the selection")
        before = {r["lesion_key"]: r for r in rows(conn, "SELECT * FROM measurements WHERE study_id=?", (baseline_id,))}
        after = {r["lesion_key"]: r for r in rows(conn, "SELECT * FROM measurements WHERE study_id=?", (followup_id,))}
        comparisons = []
        for key in sorted(before.keys() | after.keys()):
            a, b = before.get(key), after.get(key)
            comparisons.append({
                "lesion_key": key, "site": (b or a)["site"],
                "baseline_site": a["site"] if a else None, "followup_site": b["site"] if b else None,
                "site_differs": bool(a and b and a["site"] != b["site"]),
                "baseline_mm": a["long_axis_mm"] if a else None,
                "followup_mm": b["long_axis_mm"] if b else None,
                "difference_mm": exact_difference(a["long_axis_mm"], b["long_axis_mm"]) if a and b else None,
                "status": "matched" if a and b else ("only in earlier study" if a else "only in later study"),
                "baseline_measurement_id": a["id"] if a else None, "followup_measurement_id": b["id"] if b else None,
                "baseline_source_id": a["source_id"] if a else None,
                "followup_source_id": b["source_id"] if b else None,
            })
        return {
            "baseline": baseline, "followup": followup, "measurements": comparisons,
            "interpretation": "Descriptive changes only. No RECIST assessment, response category, or causal inference.",
        }


@app.get("/api/patients/{patient_id}/audit")
def audit_events(patient_id: int):
    with connection() as conn:
        ensure_patient(conn, patient_id)
        return rows(conn, "SELECT * FROM audit_log WHERE patient_id=? ORDER BY id DESC", (patient_id,))


@app.get("/api/patients/{patient_id}/evidence")
def evidence_findings(patient_id: int):
    """Conflicts, possible duplicates, date mismatches and missing support. Reported, never resolved."""
    with connection() as conn:
        ensure_patient(conn, patient_id)
        return build_findings(conn, patient_id)


@app.get("/api/audit/verify")
def audit_verify():
    """Chain validity plus its head. Compare the head with one recorded earlier to detect removed newest entries."""
    with connection() as conn:
        return {**verify_audit_chain(conn), "head": audit_head(conn)}


# --- Report and export --------------------------------------------------------------------------

def synthetic_patient(conn: sqlite3.Connection, patient_id: int) -> dict:
    patient = get_patient(conn, patient_id)
    if not patient["is_synthetic"]:
        raise HTTPException(403, "Export is limited to synthetic demonstration records in this phase")
    return patient


@app.get("/api/patients/{patient_id}/report.md", response_class=PlainTextResponse)
def report(patient_id: int):
    with connection() as conn:
        text = build_report(conn, get_patient(conn, patient_id))
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"X-Content-Type-Options": "nosniff"})


@app.get("/api/patients/{patient_id}/export.json")
def export_json(patient_id: int):
    with connection() as conn:
        data = build_export(conn, synthetic_patient(conn, patient_id))
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="oncoscope-synthetic-{patient_id}.json"'})


@app.get("/api/patients/{patient_id}/export/{entity}.csv")
def export_csv(patient_id: int, entity: str):
    if entity not in (*RECORD_TABLES, "audit_log"):
        raise HTTPException(404, f"Unknown export '{entity}'")
    with connection() as conn:
        synthetic_patient(conn, patient_id)
        data = rows(conn, f"SELECT * FROM {entity} WHERE patient_id=? ORDER BY id", (patient_id,))
    return Response(build_csv(data), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="oncoscope-synthetic-{patient_id}-{entity}.csv"',
        "X-Content-Type-Options": "nosniff",
    })
