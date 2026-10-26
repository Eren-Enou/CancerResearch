"""Deterministic, rule-based extraction of CA-125 results from fictional lab report text.

The parser only *proposes* transcriptions. Anything missing, ambiguous or unsupported is reported as an issue and the
field is left empty; nothing is guessed, normalised or converted. Proposals enter a review queue and become records
only through an explicit, audited human decision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .units import rule_for

PARSER = "ca125-lab"
PARSER_VERSION = "1"

TEST_PATTERN = re.compile(r"\bCA[\s-]?125\b", re.IGNORECASE)
# After the test name: optional "(qualifier)", optional ':' or '=', optional comparator, a number, an optional unit.
RESULT_PATTERN = re.compile(
    r"^\s*(?:\((?P<note>[^)]*)\))?\s*[:=]?\s*(?P<cmp>[<>≤≥]=?)?\s*(?P<num>\d+(?:[.,]\d+)*)?\s*(?P<unit>[A-Za-zµμ%][A-Za-z0-9µμ%/.]*)?")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
SLASH_DATE = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$")
NAMED_DATE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})$|^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
FULL_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                            "september", "october", "november", "december"], 1)} | {"sept": 9}
COLLECTION_LABEL = re.compile(r"^\s*(collection date|date collected|collected|specimen date|sample date)\s*[:\-]\s*(?P<value>.+?)\s*$", re.I)
REPORT_LABEL = re.compile(r"^\s*(report date|reported|date reported)\s*[:\-]\s*(?P<value>.+?)\s*$", re.I)


@dataclass
class Proposal:
    page: int
    start: int
    end: int
    original_text: str
    fields: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)

    def issue(self, code: str, message: str) -> None:
        self.issues.append({"code": code, "message": message})


def parse_date(text: str) -> tuple[list[date], str]:
    """Returns every valid reading of a date string, and a format label. More than one reading means ambiguous."""
    text = text.strip()
    m = ISO_DATE.fullmatch(text)
    if m:
        return _valid([(int(m[1]), int(m[2]), int(m[3]))]), "iso"
    m = SLASH_DATE.match(text)
    if m:
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        readings = _valid([(year, b, a), (year, a, b)])  # day-first and month-first
        return sorted(set(readings)), "numeric"
    m = NAMED_DATE.match(text)
    if m:
        day, month, year = (m[1], m[2], m[3]) if m[1] else (m[5], m[4], m[6])
        number = MONTHS.get(month.lower()) or FULL_MONTHS.get(month.lower())
        if number:
            return _valid([(int(year), number, int(day))]), "named-month"
    return [], "unrecognised"


def _valid(parts: list[tuple[int, int, int]]) -> list[date]:
    out = []
    for y, mo, d in parts:
        try:
            out.append(date(y, mo, d))
        except ValueError:
            pass
    return out


def _labelled_dates(lines: list[str], label: re.Pattern) -> list[tuple[str, list[date]]]:
    found = []
    for line in lines:
        m = label.match(line)
        if m:
            found.append((m["value"], parse_date(m["value"])[0]))
    return found


def extract_ca125(pages: list[str], source_date: str | None = None) -> list[Proposal]:
    """One proposal per line that mentions CA-125, in document order."""
    proposals = []
    all_lines = [line for text in pages for line in text.split("\n")]
    collection = _labelled_dates(all_lines, COLLECTION_LABEL)
    reports = _labelled_dates(all_lines, REPORT_LABEL)
    for page_no, text in enumerate(pages, 1):
        offset = 0
        for line in text.split("\n"):
            line_start, offset = offset, offset + len(line) + 1
            match = TEST_PATTERN.search(line)
            if not match or COLLECTION_LABEL.match(line) or REPORT_LABEL.match(line):
                continue
            stripped = line.strip()
            start = line_start + line.index(stripped)
            proposal = Proposal(page=page_no, start=start, end=start + len(stripped), original_text=stripped)
            _read_result(proposal, line[match.end():])
            _read_date(proposal, line, collection, reports, source_date)
            proposals.append(proposal)
    return proposals


def _read_result(p: Proposal, rest: str) -> None:
    rule = rule_for("CA-125")
    p.fields["test"] = rule.display
    m = RESULT_PATTERN.match(rest)
    note = m["note"] if m else None
    if note:
        p.fields["qualifier_note"] = note
        p.issue("line_qualifier", f"The line carries a qualifier '({note})'; check whether it is a separate result")
    num, unit, cmp_ = (m["num"], m["unit"], m["cmp"]) if m else (None, None, None)
    p.fields["value"] = None
    if num is None:
        p.issue("value_not_found", "No numeric result follows the test name on this line")
    elif cmp_:
        p.issue("value_qualifier", f"Result is reported as '{cmp_}{num}', a bound rather than a measured value; it is not converted to a number")
    elif "," in num:
        p.issue("value_separator", f"'{num}' uses a comma; decimal vs thousands separator cannot be decided")
    else:
        p.fields["value"] = float(num)
    p.fields["unit"] = None
    if unit is None or num is None:
        if num is not None:
            p.issue("unit_missing", "No unit follows the value")
    elif unit not in rule.accepted_units:
        p.issue("unit_not_accepted", f"Unit '{unit}' is not an accepted spelling for CA-125 ({', '.join(rule.accepted_units)}); it is not normalised")
        p.fields["unit_as_written"] = unit
    else:
        p.fields["unit"] = unit


def _read_date(p: Proposal, line: str, collection: list[tuple[str, list[date]]], reports: list[tuple[str, list[date]]],
               source_date: str | None) -> None:
    p.fields["date"] = None
    on_line = [d for m in ISO_DATE.finditer(line) for d in _valid([(int(m[1]), int(m[2]), int(m[3]))])]
    if len(set(on_line)) == 1:
        p.fields["date"], p.fields["date_basis"] = on_line[0].isoformat(), "date on the same line"
    elif len(set(on_line)) > 1:
        p.issue("date_ambiguous", "The line contains more than one date")
    elif not collection:
        p.issue("date_missing", "No collection date on the line or labelled in the document")
    else:
        readings = {d for _text, ds in collection for d in ds}
        texts = [t for t, _ in collection]
        unreadable = [t for t, ds in collection if not ds]
        if len(readings) == 1 and not unreadable:
            p.fields["date"], p.fields["date_basis"] = next(iter(readings)).isoformat(), f"labelled collection date '{texts[0]}'"
        elif unreadable and not readings:
            p.issue("date_unrecognised", f"Collection date '{unreadable[0]}' is not in a recognised format")
        else:
            options = ", ".join(sorted(d.isoformat() for d in readings))
            p.issue("date_ambiguous", f"Collection date {' / '.join(repr(t) for t in texts)} has several possible readings ({options})")
    if p.fields["date"]:
        observed = date.fromisoformat(p.fields["date"])
        report_dates = {d for _t, ds in reports for d in ds}
        if report_dates and observed > max(report_dates):
            p.issue("date_after_report", f"Observation date {observed} is after the report date {max(report_dates)}")
        if source_date and observed > date.fromisoformat(source_date):
            p.issue("date_after_source", f"Observation date {observed} is after the source document date {source_date}")
        if observed > date.today():
            p.issue("date_in_future", f"Observation date {observed} is in the future")
