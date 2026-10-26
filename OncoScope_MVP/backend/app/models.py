"""Strict API inputs. Dates remain date-only, numeric values retain units."""
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from .units import MAX_MEASUREMENT_MM, lab_value_errors

EARLIEST_DATE = date(1900, 1, 1)


def _single_line(value: str) -> str:
    if any(ch in value for ch in "\r\n\t\x00"):
        raise ValueError("must be a single line without control characters")
    return value


def _not_future(value: date) -> date:
    # Observation dates are local calendar dates; an observation cannot be dated after today.
    if value > date.today():
        raise ValueError("observation date cannot be in the future")
    return value


def _plausible(value: date) -> date:
    if value < EARLIEST_DATE:
        raise ValueError(f"date must be on or after {EARLIEST_DATE.isoformat()}")
    return value


Line = Annotated[str, AfterValidator(_single_line)]
CalendarDate = Annotated[date, AfterValidator(_plausible)]
ObservedDate = Annotated[date, AfterValidator(_plausible), AfterValidator(_not_future)]
LesionKey = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
SourceKind = Literal["lab", "radiology", "oncology-note", "treatment", "manual", "other"]
EventKind = Literal["diagnosis", "symptom", "procedure", "consultation", "plan", "other"]
Modality = Literal["CT", "MRI", "PET", "US", "other"]
Strict = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PatientCreate(BaseModel):
    """Only synthetic cases may be created in this phase; there is no is_synthetic input."""
    model_config = Strict
    label: Line = Field(min_length=1, max_length=100)


class Entry(BaseModel):
    model_config = Strict
    patient_id: int = Field(gt=0)
    source_id: int = Field(gt=0)


class SourceCreate(BaseModel):
    model_config = Strict
    patient_id: int = Field(gt=0)
    label: Line = Field(min_length=1, max_length=200)
    kind: SourceKind
    source_date: ObservedDate | None = None
    origin: Line = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)


class EventCreate(Entry):
    date: CalendarDate
    kind: EventKind
    title: Line = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def only_plans_in_future(self):
        if self.kind != "plan":
            _not_future(self.date)
        return self


class LabCreate(Entry):
    date: ObservedDate
    test: Line = Field(min_length=1, max_length=100)
    value: float = Field(allow_inf_nan=False)
    unit: Line = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unit_rules(self):
        errors = lab_value_errors(self.test, self.value, self.unit)
        if errors:
            raise ValueError("; ".join(errors))
        return self


class TreatmentCreate(Entry):
    date: ObservedDate
    regimen: Line = Field(min_length=1, max_length=200)
    cycle: int = Field(gt=0, le=100)
    notes: str = Field(default="", max_length=2000)


class StudyCreate(Entry):
    date: ObservedDate
    modality: Modality
    study_label: Line = Field(min_length=1, max_length=200)
    report_text: str = Field(default="", max_length=20000)


class MeasurementCreate(Entry):
    study_id: int = Field(gt=0)
    lesion_key: LesionKey
    site: Line = Field(min_length=1, max_length=200)
    long_axis_mm: float = Field(gt=0, le=MAX_MEASUREMENT_MM, allow_inf_nan=False)
    short_axis_mm: float | None = Field(default=None, gt=0, le=MAX_MEASUREMENT_MM, allow_inf_nan=False)
    notes: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def short_not_longer(self):
        if self.short_axis_mm is not None and self.short_axis_mm > self.long_axis_mm:
            raise ValueError("short_axis_mm cannot exceed long_axis_mm (long axis is the longest diameter)")
        return self


CREATE_MODELS: dict[str, type[BaseModel]] = {
    "sources": SourceCreate, "events": EventCreate, "labs": LabCreate,
    "treatments": TreatmentCreate, "studies": StudyCreate, "measurements": MeasurementCreate,
}
# Columns a correction may change. patient_id, ids, uids and document fields are never editable.
EDITABLE_FIELDS: dict[str, tuple[str, ...]] = {
    table: tuple(name for name in model.model_fields if name != "patient_id")
    for table, model in CREATE_MODELS.items()
}
NULLABLE_FIELDS = {("sources", "source_date"), ("measurements", "short_axis_mm")}


class Correction(BaseModel):
    """A correction names only the changed fields and always carries a reason."""
    model_config = Strict
    changes: dict[str, str | int | float | None] = Field(min_length=1)
    reason: Line = Field(min_length=3, max_length=500)


class StatusChange(BaseModel):
    model_config = Strict
    status: Literal["entered", "reviewed", "verified"]
    reason: Line = Field(min_length=3, max_length=500)


class LabCorrection(BaseModel):
    """Phase 1 lab correction contract, kept for compatibility; routed through the generic correction."""
    model_config = Strict
    source_id: int = Field(gt=0)
    value: float = Field(allow_inf_nan=False)
    unit: Line = Field(min_length=1, max_length=50)
    reason: Line = Field(min_length=3, max_length=500)
