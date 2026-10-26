"""Explicit unit and value rules. Values are stored exactly as entered and never converted."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LabRule:
    display: str
    accepted_units: tuple[str, ...]   # exact spellings; the first is the default chart unit
    min_value: float | None = None


# Registry of tests with known unit spellings. Accepting a unit here does NOT mean values in
# different units are comparable or converted: each unit forms its own series.
LAB_RULES: dict[str, LabRule] = {
    "ca125": LabRule(display="CA-125", accepted_units=("U/mL", "kU/L"), min_value=0),
}

UNIT_PATTERN = re.compile(r"^[A-Za-z%µμ][A-Za-z0-9%µμ/.^*·()\[\] -]*$")
MAX_MEASUREMENT_MM = 1000.0


def test_key(name: str) -> str:
    """Matching key for test names: 'CA-125', 'ca 125' and 'CA125' are the same test."""
    return re.sub(r"[\s\-_]", "", name).lower()


def rule_for(test: str) -> LabRule | None:
    return LAB_RULES.get(test_key(test))


def lab_value_errors(test: str, value: float, unit: str) -> list[str]:
    errors = []
    if not UNIT_PATTERN.match(unit):
        errors.append(f"Unit '{unit}' is not a recognizable unit string (start with a letter, %, or µ; no control characters)")
    rule = rule_for(test)
    if rule:
        if unit not in rule.accepted_units:
            errors.append(f"Unit '{unit}' is not accepted for {rule.display}. Enter it exactly as one of: "
                          f"{', '.join(rule.accepted_units)} (no automatic conversion is performed)")
        if rule.min_value is not None and value < rule.min_value:
            errors.append(f"{rule.display} value must be ≥ {format_number(rule.min_value)}")
    return errors


def unit_spelling_conflict(unit: str, existing_units: set[str]) -> str | None:
    """A unit that differs from an existing one only by case would silently split a series."""
    for existing in existing_units:
        if existing != unit and existing.lower() == unit.lower():
            return existing
    return None


def format_number(value: float | int | None) -> str:
    """Plain decimal text with no exponent and no trailing '.0' (e.g. 1234567, 0.00012, 38.5)."""
    if value is None:
        return "not recorded"
    text = format(Decimal(repr(float(value))).normalize(), "f")
    return text


def exact_difference(baseline: float, followup: float) -> float:
    """followup − baseline computed in decimal so 38.1 → 31.2 is exactly −6.9, not −6.900000000000002."""
    return float(Decimal(repr(float(followup))) - Decimal(repr(float(baseline))))
