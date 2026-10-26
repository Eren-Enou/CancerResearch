"""OncoScope local API: descriptive, source-linked clinical record organization."""
from contextlib import asynccontextmanager
import sqlite3

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import documents
from .db import (
    RECORD_TABLES, SCHEMA_VERSION, connection, init_db, insert_record, now_utc, update_record, verify_audit_chain,
)
from .models import (
    CREATE_MODELS, EDITABLE_FIELDS, NULLABLE_FIELDS, Correction, EventCreate, LabCorrection, LabCreate,
    MeasurementCreate, PatientCreate, SourceCreate, StatusChange, StudyCreate, TreatmentCreate,
)
from .reporting import build_csv, build_export, build_report, patient_records
from .units import exact_difference, format_number, rule_for, test_key, unit_spelling_conflict


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="OncoScope local API", version="0.2.0", lifespan=lifespan,
    description="Local-only prototype. Organizes sources; does not diagnose, stage, or assess clinical response.",
)
# Rejects requests whose Host header is not loopback (DNS-rebinding protection for a local service).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])



@app.exception_handler(RequestValidationError)
async def validation_handler(_request: Request, exc: RequestValidationError):
    # The default handler echoes the rejected input, which crashes JSON encoding for NaN/Infinity
    # and needlessly reflects submitted record content. Report location and message only.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", "Invalid value")} for e in exc.errors()
    ]})


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


def create(table: str, payload: BaseModel) -> dict:
    data = payload.model_dump(mode="json")
    with connection() as conn:
        ensure_patient(conn, data["patient_id"])
        check_references(conn, table, data)
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
        merged = {name: before[name] for name in EDITABLE_FIELDS[entity]} | correction.changes
        try:
            validated = CREATE_MODELS[entity].model_validate({**merged, "patient_id": before["patient_id"]})
        except ValidationError as exc:
            raise validation_error(exc) from exc
        data = validated.model_dump(mode="json")
        changes = {name: data[name] for name in correction.changes if data[name] != before[name]}
        if not changes:
            raise HTTPException(422, "Correction does not change any stored value")
        check_references(conn, entity, data, record_id)
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
    return {"status": "ok", "scope": "local", "clinical_decision_support": False, "schema_version": SCHEMA_VERSION}


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
    return {
        "test": rule.display if rule else test, "unit": unit, "available_units": units_seen,
        "points": [r for r in matching if r["unit"] == unit],
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
async def attach_document(source_id: int, request: Request, filename: str = Query(min_length=1, max_length=255)):
    # application/octet-stream is not a CORS "simple" type, so other web origins cannot send this cross-site.
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
        raise HTTPException(415, "Send the document body as application/octet-stream")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > documents.MAX_DOCUMENT_BYTES:
        raise HTTPException(413, "Document exceeds the 10 MB limit")
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
        documents.store(sha256, data)
        changes = {"document_sha256": sha256, "document_filename": name, "document_media_type": media_type,
                   "document_size": len(data), "document_attached_at": now_utc()}
        return update_record(conn, "sources", source_id, changes, f"Attached document {name}", action="attach_document")


def _document_or_error(source_id: int) -> tuple[dict, bytes | None, str | None]:
    with connection() as conn:
        source = get_record(conn, "sources", source_id)
    if not source["document_sha256"]:
        raise HTTPException(404, "No document attached to this source")
    data, actual = documents.load_verified(source["document_sha256"])
    return source, data, actual


@app.get("/api/sources/{source_id}/document/verify")
def verify_document(source_id: int):
    source, data, actual = _document_or_error(source_id)
    return {"source_id": source_id, "expected_sha256": source["document_sha256"], "actual_sha256": actual,
            "present": data is not None, "valid": actual == source["document_sha256"]}


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
                             "verification_status": r["verification_status"]})

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


@app.get("/api/audit/verify")
def audit_verify():
    with connection() as conn:
        return verify_audit_chain(conn)


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
