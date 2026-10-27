"""Creates a clearly fictional dataset. Never substitutes for actual patient records."""
from pathlib import Path

from .db import (
    DEMO_ACTOR, LAST_PLAINTEXT_SCHEMA, _connect, app_mode, connection, current_actor, init_db, insert_record, migrate,
)


def seed() -> None:
    if app_mode() != "demo":
        raise SystemExit("Seeding writes fictional records; run it in demonstration mode (ONCOSCOPE_MODE=demo).")
    marker = current_actor.set(DEMO_ACTOR)
    try:
        _seed()
    finally:
        current_actor.reset(marker)


def seed_legacy_plaintext(path: Path, schema_version: int = LAST_PLAINTEXT_SCHEMA) -> int:
    """A plaintext database at an older schema with the fictional case, as an earlier OncoScope version left it. For
    migration tests and the encryption demonstration only; run in demonstration mode."""
    if app_mode() != "demo":
        raise SystemExit("Seeding writes fictional records; run it in demonstration mode (ONCOSCOPE_MODE=demo).")
    conn = _connect(path)
    conn.isolation_level = None
    migrate(conn, up_to=schema_version)
    conn.isolation_level = ""
    marker = current_actor.set(DEMO_ACTOR if schema_version >= 4 else None)   # audit actors exist from schema v4
    try:
        pid = _seed_case(conn)
        conn.commit()
        return pid
    finally:
        current_actor.reset(marker)
        conn.close()


def _seed() -> None:
    init_db()
    with connection() as conn:
        existing = conn.execute("SELECT id FROM patients WHERE label='Synthetic Case A'").fetchone()
        if existing:
            print(f"Synthetic Case A already seeded as patient {existing['id']}")
            return
        print(f"Seeded patient {_seed_case(conn)} with synthetic records")


def _seed_case(conn) -> int:
    pid = conn.execute("INSERT INTO patients(label,is_synthetic) VALUES('Synthetic Case A', 1)").lastrowid

    def add(table, **values):
        return insert_record(conn, table, {"patient_id": pid, **values})["id"]

    intake = add("sources", label="Fictional initial oncology note", kind="oncology-note", source_date="2026-08-05", note="Entirely invented demonstration data")
    lab_src = add("sources", label="Fictional serial laboratory results", kind="lab", source_date="2026-10-14", note="Synthetic values, not real laboratory measurements")
    tx_src = add("sources", label="Fictional medication administration log", kind="treatment", source_date="2026-10-10", note="Demonstration records only")
    baseline_src = add("sources", label="Fictional baseline CT report", kind="radiology", source_date="2026-08-10", note="No actual DICOM file is present")
    follow_src = add("sources", label="Fictional follow-up CT report", kind="radiology", source_date="2026-10-12", note="No actual DICOM file is present")
    add("events", date="2026-08-05", kind="consultation", title="Initial case review (synthetic)", detail="Fictional onboarding note for interface demonstration.", source_id=intake)
    add("events", date="2026-09-20", kind="procedure", title="Documented fluid drainage (synthetic)", detail="Demonstration event; no actual volume recorded.", source_id=intake)
    add("events", date="2026-10-15", kind="plan", title="Review follow-up imaging (synthetic)", detail="Fictional plan event; not an actual clinical recommendation.", source_id=intake)
    for day, value in (("2026-08-07", 310), ("2026-08-28", 288), ("2026-09-18", 202), ("2026-10-09", 174), ("2026-10-14", 155)):
        add("labs", date=day, test="CA-125", value=value, unit="U/mL", source_id=lab_src)
    for day, cycle in (("2026-08-14", 1), ("2026-09-04", 2), ("2026-09-25", 3)):
        add("treatments", date=day, regimen="Carboplatin + paclitaxel (fictional)", cycle=cycle, notes="Synthetic administration, not a prescription.", source_id=tx_src)
    first = add("studies", date="2026-08-10", modality="CT", study_label="Baseline abdominal CT (synthetic)",
                report_text="Fictional educational report: a reference lesion was selected for longitudinal demonstration. Not diagnostic.", source_id=baseline_src)
    second = add("studies", date="2026-10-12", modality="CT", study_label="Follow-up abdominal CT (synthetic)",
                 report_text="Fictional educational report: measurement differences shown in the prototype are not a response classification.", source_id=follow_src)
    add("measurements", study_id=first, lesion_key="reference-A", site="Example soft-tissue lesion", long_axis_mm=38.0, short_axis_mm=22.0, notes="Fictional measurement", source_id=baseline_src)
    add("measurements", study_id=second, lesion_key="reference-A", site="Example soft-tissue lesion", long_axis_mm=31.0, short_axis_mm=18.0, notes="Fictional measurement", source_id=follow_src)
    return pid


if __name__ == "__main__":
    seed()
