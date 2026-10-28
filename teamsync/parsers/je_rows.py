"""Parse rows of strings into a NetSuite journal entry.

Pure: no IO, no database. Takes the output of ``readers.read_rows`` plus the
original filename, returns a ``ParsedJournalEntry`` with lines, totals and a
few derived facts. Validation flags are added by ``rules.apply_rules``.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import PurePath

PARSER_VERSION = "1"

EXPECTED_HEADER: tuple[str, ...] = (
    "subsidiary",
    "postingperiod",
    "journalitemheader_memo",
    "trandate",
    "journalitemline_accountref",
    "journalitemline_debitamount",
    "journalitemline_creditamount",
    "journalitemline_memo",
    "journalitemline_departmentref",
    "journalitemline_classref",
    "journalitemline_locationref",
)

CENT = Decimal("0.01")

FILENAME_RE = re.compile(r"^(?P<property>.*?)\s*JE_(?P<payroll>\d+)_(?P<batch>\d+)$", re.IGNORECASE)
MEMO_RE = re.compile(r"^(?P<payroll>\d+)\.(?P<batch>\d+)(?:\.(?P<run>\d+))?$")


class UnrecognizedLayout(Exception):
    """The first row is not the NetSuite JE header."""


class RowParseError(Exception):
    """A data row could not be interpreted. Carries the 1-based row number."""

    def __init__(self, row_no: int, message: str) -> None:
        super().__init__(f"row {row_no}: {message}")
        self.row_no = row_no


@dataclass(slots=True)
class FilenameParts:
    property_name_raw: str
    payroll_id: str | None
    batch_id: str | None
    matched: bool


@dataclass(slots=True)
class JournalLine:
    line_no: int
    subsidiary: str
    posting_period: str
    header_memo: str
    tran_date: str
    account_ref: str
    account_is_numeric: bool
    debit: Decimal | None
    credit: Decimal | None
    line_memo: str
    department_ref: str
    department_style: str
    class_ref: str
    location_ref: str


@dataclass(slots=True)
class ParsedJournalEntry:
    source_filename: str
    property_name_raw: str
    payroll_id: str
    batch_id: str
    run_id: str | None
    header_memo: str
    posting_period: str
    tran_date: str
    subsidiaries: list[str]
    locations: list[str]
    lines: list[JournalLine]
    total_debit: Decimal
    total_credit: Decimal
    dept_style: str
    filename_matched: bool
    header_memos: list[str]
    posting_periods: list[str]
    tran_dates: list[str]
    reader_warnings: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit

    @property
    def line_count(self) -> int:
        return len(self.lines)


# --- helpers ---------------------------------------------------------------


def parse_filename(filename: str) -> FilenameParts:
    stem = PurePath(filename).stem.strip()
    match = FILENAME_RE.match(stem)
    if not match:
        return FilenameParts(property_name_raw=stem, payroll_id=None, batch_id=None, matched=False)
    return FilenameParts(
        property_name_raw=match.group("property").strip() or stem,
        payroll_id=match.group("payroll"),
        batch_id=match.group("batch"),
        matched=True,
    )


def normalize_header(cells: list[str]) -> tuple[str, ...]:
    return tuple(c.strip().lower() for c in cells)


def parse_date(value: str, row_no: int, column: str) -> str:
    text = value.strip()
    if not text:
        raise RowParseError(row_no, f"{column} is blank")
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise RowParseError(row_no, f"{column} is not a date: {text!r}")


def parse_amount(value: str, row_no: int, column: str) -> Decimal | None:
    text = value.strip().replace(",", "")
    if not text:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return Decimal(text).quantize(CENT)
    except InvalidOperation as exc:
        raise RowParseError(row_no, f"{column} is not a number: {value!r}") from exc


def classify_department(value: str) -> str:
    text = value.strip()
    if not text:
        return "blank"
    if text.isdigit():
        return "numeric"
    if ":" in text:
        return "path"
    return "name"


def overall_dept_style(styles: list[str]) -> str:
    present = {s for s in styles if s != "blank"}
    if not present:
        return "blank"
    if len(present) == 1:
        return present.pop()
    return "mixed"


def _unique_in_order(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for v in values:
        seen.setdefault(v, None)
    return list(seen)


# --- main entry point ------------------------------------------------------


def parse_rows(
    rows: list[list[str]], filename: str, reader_warnings: list[str] | None = None
) -> ParsedJournalEntry:
    if not rows:
        raise UnrecognizedLayout("file is empty")

    header = normalize_header(rows[0])
    if header[: len(EXPECTED_HEADER)] != EXPECTED_HEADER:
        raise UnrecognizedLayout(f"unexpected header: {rows[0]!r}")

    width = len(EXPECTED_HEADER)
    lines: list[JournalLine] = []
    for index, raw in enumerate(rows[1:], start=2):
        cells = list(raw[:width]) + [""] * max(0, width - len(raw))
        (
            subsidiary,
            posting_period,
            header_memo,
            tran_date,
            account_ref,
            debit_raw,
            credit_raw,
            line_memo,
            department_ref,
            class_ref,
            location_ref,
        ) = (c.strip() for c in cells)
        if not subsidiary and not account_ref and not debit_raw and not credit_raw:
            continue
        lines.append(
            JournalLine(
                line_no=len(lines) + 1,
                subsidiary=subsidiary,
                posting_period=parse_date(posting_period, index, "postingperiod"),
                header_memo=header_memo,
                tran_date=parse_date(tran_date, index, "tranDate"),
                account_ref=account_ref,
                account_is_numeric=account_ref.isdigit(),
                debit=parse_amount(debit_raw, index, "debitAmount"),
                credit=parse_amount(credit_raw, index, "creditAmount"),
                line_memo=line_memo,
                department_ref=department_ref,
                department_style=classify_department(department_ref),
                class_ref=class_ref,
                location_ref=location_ref,
            )
        )

    if not lines:
        raise UnrecognizedLayout("header present but no data rows")

    name = parse_filename(filename)
    header_memos = _unique_in_order([ln.header_memo for ln in lines])
    header_memo = header_memos[0]
    memo_match = MEMO_RE.match(header_memo)

    payroll_id = name.payroll_id or (memo_match.group("payroll") if memo_match else "")
    batch_id = name.batch_id or (memo_match.group("batch") if memo_match else "")
    run_id = memo_match.group("run") if memo_match else None

    posting_periods = _unique_in_order([ln.posting_period for ln in lines])
    tran_dates = _unique_in_order([ln.tran_date for ln in lines])

    return ParsedJournalEntry(
        source_filename=filename,
        property_name_raw=name.property_name_raw,
        payroll_id=payroll_id,
        batch_id=batch_id,
        run_id=run_id,
        header_memo=header_memo,
        posting_period=posting_periods[0],
        tran_date=tran_dates[0],
        subsidiaries=_unique_in_order([ln.subsidiary for ln in lines]),
        locations=_unique_in_order([ln.location_ref for ln in lines]),
        lines=lines,
        total_debit=sum((ln.debit or Decimal(0) for ln in lines), Decimal(0)).quantize(CENT),
        total_credit=sum((ln.credit or Decimal(0) for ln in lines), Decimal(0)).quantize(CENT),
        dept_style=overall_dept_style([ln.department_style for ln in lines]),
        filename_matched=name.matched,
        header_memos=header_memos,
        posting_periods=posting_periods,
        tran_dates=tran_dates,
        reader_warnings=list(reader_warnings or []),
    )
