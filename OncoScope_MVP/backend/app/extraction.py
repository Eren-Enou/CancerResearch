"""Deterministic, rule-based extraction of CA-125 results from fictional lab report text.

The parser only *proposes* transcriptions. Anything missing, ambiguous or unsupported is reported as an issue and the
field is left empty; nothing is guessed, normalised or converted. Proposals enter a review queue and become records
only through an explicit, audited human decision.

Version 2 reads layouts rather than templates: label variants ("CA125", "Cancer antigen 125"), table separators and dotted
leaders, a unit printed before the value, flag and reference-range columns, dates printed on the result row, labelled
collection vs report dates (with time suffixes), and a value on the line after the test name. Lines that mention CA-125
without a result, or that carry more than one candidate value, are reported rather than resolved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .units import rule_for

PARSER = "ca125-lab"
PARSER_VERSION = "2.1"

# Letters may not precede the name, but digits may: PDF text layers sometimes glue cells together ("U/mL88CA-125").
TEST_PATTERN = re.compile(r"(?<![A-Za-z])(?:CA[\s-]?125|cancer\s+antigen\s+125)(?!\d)", re.IGNORECASE)
NUMBER = r"\d+(?:[.,]\d+)*"
RESULT_PATTERN = re.compile(
    r"^\s*(?:\((?P<paren>[^)]*)\))?\s*(?:(?:result|value)\b\s*)?[:=]?\s*(?P<cmp>[<>≤≥]=?)?\s*(?P<num>" + NUMBER + r")"
    r"(?:\s+(?P<flag>HH|LL|H|L|A|\*)(?=\s|$))?\s*(?P<unit>[A-Za-zµμ%][A-Za-z0-9µμ%/.]*)?", re.IGNORECASE)
NEXT_LINE_VALUE = re.compile(
    r"^\s*(?:(?:result|value)\b\s*)?[:=]?\s*(?P<cmp>[<>≤≥]=?)?\s*(?P<num>" + NUMBER + r")(?:\s+(?P<flag>HH|LL|H|L|A|\*)(?=\s|$))?"
    r"\s*(?P<unit>[A-Za-zµμ%][A-Za-z0-9µμ%/.]*)?", re.IGNORECASE)
RESULT_WORDS = re.compile(r"^\s*[:=]|\b(?:result|value|pending|not done|cancelled|see below)\b", re.IGNORECASE)
REFERENCE_TAIL = re.compile(r"\b(?:ref(?:erence)?|range|normal|interval)\b.*$", re.IGNORECASE)
RANGE = re.compile(r"[<>≤≥]=?\s*\d+(?:[.,]\d+)?|\b\d+(?:[.,]\d+)?\s*[-–]\s*\d+(?:[.,]\d+)?\b")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
NUM_DATE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
NAMED_DATE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b|\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
FULL_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                            "september", "october", "november", "december"], 1)} | {"sept": 9}
COLLECTION_LABEL = re.compile(r"^\s*(?:collection date(?:/time)?|date collected|date of collection|specimen collected|"
                              r"collected|specimen date|sample date|drawn)\b\s*(?P<colon>[:\-])?\s*(?P<value>.*?)\s*$", re.I)
REPORT_LABEL = re.compile(r"^\s*(?:report date|reported|date reported|resulted|final report|letter date)\b\s*(?P<colon>[:\-])?\s*"
                          r"(?P<value>.*?)\s*$", re.I)


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


def _valid(parts: list[tuple[int, int, int]]) -> list[date]:
    out = []
    for y, mo, d in parts:
        try:
            out.append(date(y, mo, d))
        except ValueError:
            pass
    return out


def _month(name: str) -> int | None:
    return MONTHS.get(name.lower()) or FULL_MONTHS.get(name.lower())


def parse_date(text: str) -> tuple[list[date], str]:
    """Every valid reading of a whole date string, and its format. More than one reading means ambiguous."""
    text = text.strip()
    if m := ISO_DATE.fullmatch(text):
        return _valid([(int(m[1]), int(m[2]), int(m[3]))]), "iso"
    if m := NUM_DATE.fullmatch(text):
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        return sorted(set(_valid([(year, b, a), (year, a, b)]))), "numeric"
    if (m := NAMED_DATE.fullmatch(text)) and (number := _month(m[2] or m[4])):
        day, year = (m[1], m[3]) if m[1] else (m[5], m[6])
        return _valid([(int(year), number, int(day))]), "named-month"
    return [], "unrecognised"


def find_dates(text: str) -> list[tuple[int, int, list[date], str]]:
    """Date tokens anywhere in a text: (start, end, readings, raw). Numeric tokens may have two readings."""
    found = []
    for pattern in (ISO_DATE, NUM_DATE, NAMED_DATE):
        for m in pattern.finditer(text):
            if any(s <= m.start() < e for s, e, _r, _t in found):
                continue
            readings, _fmt = parse_date(m.group(0))
            if readings:
                found.append((m.start(), m.end(), readings, m.group(0)))
    return sorted(found)


def _leading_date(value: str) -> tuple[list[date], str] | None:
    """The date at the start of a labelled value ('2026-08-28 07:45' -> 2026-08-28); None when there is none."""
    hits = [d for d in find_dates(value) if d[0] == 0]
    return (hits[0][2], hits[0][3]) if hits else None


def _labelled(lines: list[str], label: re.Pattern) -> list[dict]:
    found = []
    for line in lines:
        m = label.match(line)
        if not m:
            continue
        lead = _leading_date(m["value"])
        if lead:
            found.append({"text": lead[1], "readings": lead[0]})
        elif m["colon"] and m["value"]:
            found.append({"text": m["value"], "readings": []})   # labelled but unreadable: reported, not guessed
    return found


def _mask(line: str) -> str:
    """Same-length copy for parsing: table pipes, tabs and dotted leaders become spaces so offsets stay valid."""
    masked = line.replace("|", " ").replace("\t", " ")
    return re.sub(r"\.{3,}", lambda m: " " * len(m.group(0)), masked)


def extract_ca125(pages: list[str], source_date: str | None = None) -> list[Proposal]:
    """One proposal per CA-125 statement, in document order."""
    all_lines = [line for text in pages for line in text.split("\n")]
    collection = _labelled(all_lines, COLLECTION_LABEL)
    reports = _labelled(all_lines, REPORT_LABEL)
    proposals = []
    for page_no, text in enumerate(pages, 1):
        lines = text.split("\n")
        starts = [0]
        for line in lines[:-1]:
            starts.append(starts[-1] + len(line) + 1)
        consumed: set[int] = set()
        for i, line in enumerate(lines):
            if i in consumed:
                continue
            masked = _mask(line)
            match = TEST_PATTERN.search(masked)
            if not match or COLLECTION_LABEL.match(line) or REPORT_LABEL.match(line):
                continue
            stripped = line.strip()
            start = starts[i] + line.index(stripped)
            proposal = Proposal(page=page_no, start=start, end=start + len(stripped), original_text=stripped)
            rest = masked[match.end():]
            next_i = next((j for j in range(i + 1, len(lines)) if lines[j].strip()), None)
            used_next = _read_result(proposal, rest, lines[next_i] if next_i is not None else None)
            if used_next:
                consumed.add(next_i)
                nxt = lines[next_i].strip()
                proposal.end = starts[next_i] + lines[next_i].index(nxt) + len(nxt)
                proposal.original_text = text[proposal.start:proposal.end]
            _read_date(proposal, line[:match.start()] + " " + rest, collection, reports, source_date)
            proposals.append(proposal)
    return proposals


def _read_result(p: Proposal, rest: str, next_line: str | None) -> bool:
    """Reads value, unit and qualifiers after the test name. Returns True if the value came from the next line."""
    rule = rule_for("CA-125")
    p.fields.update({"test": rule.display, "value": None, "unit": None})
    m = RESULT_PATTERN.match(rest)
    paren = None
    if not m or m["num"] is None:
        pm = re.match(r"^\s*\((?P<paren>[^)]*)\)", rest)
        paren = pm["paren"] if pm else None
    else:
        paren = m["paren"]
    unit_hint = paren.strip() if paren and paren.strip() in rule.accepted_units else None
    if paren and not unit_hint:
        p.fields["qualifier_note"] = paren
        p.issue("line_qualifier", f"The line carries a qualifier '({paren})'; check whether it is a separate result")
    used_next = False
    if m is None or m["num"] is None:
        after_paren = rest[pm.end():] if (pm := re.match(r"^\s*\([^)]*\)", rest)) else rest
        candidate = next_line and not TEST_PATTERN.search(_mask(next_line)) and NEXT_LINE_VALUE.match(_mask(next_line))
        if candidate and not re.search(r"\d", after_paren):
            m, used_next = candidate, True
            p.issue("value_on_next_line", "The value is printed on the line after the test name; confirm they belong together")
        else:
            if re.search(r"\d", after_paren) or RESULT_WORDS.search(after_paren):
                p.issue("value_not_found", "No numeric result follows the test name on this line")
            else:
                p.issue("no_result_on_line", "CA-125 is mentioned without a result on this line")
            return False
    num, cmp_, unit, flag = m["num"], m["cmp"], m["unit"], m["flag"]
    if flag:
        p.fields["flag_as_written"] = flag
    if cmp_:
        p.fields["qualifier"] = f"{cmp_}{num}"
        p.issue("value_qualifier", f"Result is reported as '{cmp_}{num}', a bound rather than a measured value; it is not converted to a number")
    elif "," in num:
        p.issue("value_separator", f"'{num}' uses a comma; decimal vs thousands separator cannot be decided")
    elif re.fullmatch(r"\d{1,3}\.\d{3}", num):
        p.issue("value_separator", f"'{num}' may use '.' as a thousands separator (as in many European reports); it is not read as a number")
    else:
        p.fields["value"] = float(num)
    if unit_hint and unit and unit != unit_hint:
        p.issue("unit_conflict", f"The line names two units ('{unit_hint}' and '{unit}')")
    unit = unit or unit_hint
    if unit is None:
        p.issue("unit_missing", "No unit is printed with the value")
    elif unit not in rule.accepted_units:
        p.fields["unit_as_written"] = unit
        p.issue("unit_not_accepted", f"Unit '{unit}' is not an accepted spelling for CA-125 ({', '.join(rule.accepted_units)}); it is not normalised")
    else:
        p.fields["unit"] = unit
    if not used_next:
        tail = rest[m.end():]
        for s, e, _r, _t in reversed(find_dates(tail)):
            tail = tail[:s] + " " * (e - s) + tail[e:]
        tail = RANGE.sub(" ", REFERENCE_TAIL.sub(" ", tail))
        others = re.findall(NUMBER, tail)
        if others:
            p.issue("multiple_values", f"The line contains other numbers after the result ({', '.join(others)}); "
                                       "check which value the document reports")
    return used_next


def _read_date(p: Proposal, line_context: str, collection: list[dict], reports: list[dict], source_date: str | None) -> None:
    p.fields["date"] = None
    on_line = find_dates(line_context)
    readings = {d for _s, _e, ds, _t in on_line for d in ds}
    if on_line:
        if len(readings) == 1:
            p.fields["date"], p.fields["date_basis"] = next(iter(readings)).isoformat(), f"date printed on the result line '{on_line[0][3]}'"
        else:
            p.issue("date_ambiguous", f"The result line's date(s) {', '.join(repr(t) for *_x, t in on_line)} allow several readings "
                                      f"({', '.join(sorted(d.isoformat() for d in readings))})")
    elif collection:
        readings = {d for c in collection for d in c["readings"]}
        unreadable = [c["text"] for c in collection if not c["readings"]]
        texts = [c["text"] for c in collection]
        if len(readings) == 1 and not unreadable:
            p.fields["date"], p.fields["date_basis"] = next(iter(readings)).isoformat(), f"labelled collection date '{texts[0]}'"
        elif unreadable and not readings:
            p.issue("date_unrecognised", f"Collection date '{unreadable[0]}' is not in a recognised format")
        else:
            p.issue("date_ambiguous", f"Collection date {' / '.join(repr(t) for t in texts)} has several possible readings "
                                      f"({', '.join(sorted(d.isoformat() for d in readings))})")
    elif reports:
        p.issue("date_missing", f"No collection date is printed; the report date '{reports[0]['text']}' is not used as the collection date")
    else:
        p.issue("date_missing", "No collection date on the line or labelled in the document")
    if p.fields["date"]:
        observed = date.fromisoformat(p.fields["date"])
        report_dates = {d for r in reports for d in r["readings"]}
        if report_dates and observed > max(report_dates):
            p.issue("date_after_report", f"Observation date {observed} is after the report date {max(report_dates)}")
        if source_date and observed > date.fromisoformat(source_date):
            p.issue("date_after_source", f"Observation date {observed} is after the source document date {source_date}")
        if observed > date.today():
            p.issue("date_in_future", f"Observation date {observed} is in the future")
