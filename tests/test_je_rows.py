from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from teamsync.parsers import (
    RowParseError,
    UnrecognizedLayout,
    parse_file,
    parse_filename,
    parse_rows,
    read_rows,
)
from teamsync.parsers.je_rows import classify_department, overall_dept_style, parse_amount, parse_date

CLEAN_STEM = "Sample Hotel JE_100001_200001"


# --- filename --------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "prop", "payroll", "batch"),
    [
        ("BW KENNEWICK JE_914339_202641.CSV", "BW KENNEWICK", "914339", "202641"),
        ("One Stop #44 JE_906526_202626.CSV", "One Stop #44", "906526", "202626"),
        ("C&M Country Store JE_913663_202636.CSV", "C&M Country Store", "913663", "202636"),
        ("Sample Hotel JE_100001_200001.xlsx", "Sample Hotel", "100001", "200001"),
        ("sample hotel je_1_2.ods", "sample hotel", "1", "2"),
    ],
)
def test_parse_filename_matches(name: str, prop: str, payroll: str, batch: str) -> None:
    parts = parse_filename(name)
    assert parts.matched
    assert (parts.property_name_raw, parts.payroll_id, parts.batch_id) == (prop, payroll, batch)


def test_parse_filename_fallback() -> None:
    parts = parse_filename("random-report.csv")
    assert not parts.matched
    assert parts.property_name_raw == "random-report"
    assert parts.payroll_id is None and parts.batch_id is None


# --- cell helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "iso"),
    [
        ("10/15/2026", "2026-10-15"),
        ("1/5/2026", "2026-01-05"),
        ("2026-10-15", "2026-10-15"),
        ("10/15/26", "2026-10-15"),
    ],
)
def test_parse_date(raw: str, iso: str) -> None:
    assert parse_date(raw, 2, "x") == iso


def test_parse_date_rejects_garbage() -> None:
    with pytest.raises(RowParseError, match="row 7"):
        parse_date("yesterday", 7, "tranDate")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),
        ("  ", None),
        ("1821.78", Decimal("1821.78")),
        ("3380", Decimal("3380.00")),
        ("1,234.5", Decimal("1234.50")),
        ("(12.34)", Decimal("-12.34")),
        ("4.375", Decimal("4.38")),
    ],
)
def test_parse_amount(raw: str, expected: Decimal | None) -> None:
    assert parse_amount(raw, 2, "x") == expected


def test_parse_amount_rejects_text() -> None:
    with pytest.raises(RowParseError):
        parse_amount("twelve", 3, "debitAmount")


@pytest.mark.parametrize(
    ("raw", "style"),
    [
        ("", "blank"),
        ("111", "numeric"),
        ("Operations:Hotel Ops:Housekeeping", "path"),
        ("C-Store", "name"),
        ("HOUSEKEEPING", "name"),
    ],
)
def test_classify_department(raw: str, style: str) -> None:
    assert classify_department(raw) == style


def test_overall_dept_style_ignores_blanks() -> None:
    assert overall_dept_style(["blank", "numeric", "blank"]) == "numeric"
    assert overall_dept_style(["blank", "blank"]) == "blank"
    assert overall_dept_style(["numeric", "path"]) == "mixed"


# --- whole files -----------------------------------------------------------


def test_clean_entry(fixtures: Path) -> None:
    path = fixtures / f"{CLEAN_STEM}.CSV"
    e = parse_file(path.read_bytes(), path.name)
    assert e.property_name_raw == "Sample Hotel"
    assert (e.payroll_id, e.batch_id, e.run_id) == ("100001", "200001", "4000001")
    assert e.header_memo == "100001.200001.4000001"
    assert e.posting_period == "2026-10-15"
    assert e.tran_date == "2026-10-20"
    assert e.subsidiaries == ["41"]
    assert e.locations == ["61"]
    assert e.line_count == 10
    assert e.total_debit == Decimal("11851.00")
    assert e.total_credit == Decimal("11851.00")
    assert e.is_balanced
    assert e.dept_style == "path"
    assert e.flags == []
    first = e.lines[0]
    assert first.line_no == 1
    assert first.account_ref == "279" and first.account_is_numeric
    assert first.debit is None and first.credit == Decimal("1500.00")
    assert first.department_ref == "Administration:G&A:General Manager"


@pytest.mark.parametrize("ext", [".xlsx", ".xls", ".ods"])
def test_workbook_formats_parse_identically_to_csv(fixtures: Path, ext: str) -> None:
    csv_entry = parse_file((fixtures / f"{CLEAN_STEM}.CSV").read_bytes(), f"{CLEAN_STEM}.CSV")
    wb_entry = parse_file((fixtures / f"{CLEAN_STEM}{ext}").read_bytes(), f"{CLEAN_STEM}{ext}")
    # Only the source filename may differ.
    csv_entry.source_filename = wb_entry.source_filename = "x"
    assert csv_entry == wb_entry


def test_two_sheet_workbook_is_flagged_but_parses(fixtures: Path) -> None:
    path = fixtures / "Sample Hotel Two Sheets JE_100001_200001.xlsx"
    e = parse_file(path.read_bytes(), path.name)
    assert e.flags == ["extra_sheets_ignored"]
    assert e.total_debit == Decimal("11851.00")


def test_unrecognized_layout(fixtures: Path) -> None:
    path = fixtures / "random-report.csv"
    with pytest.raises(UnrecognizedLayout):
        parse_file(path.read_bytes(), path.name)


def test_empty_rows_is_unrecognized() -> None:
    with pytest.raises(UnrecognizedLayout):
        parse_rows([], "x.csv")


def test_header_only_is_unrecognized(fixtures: Path) -> None:
    rows = read_rows((fixtures / f"{CLEAN_STEM}.CSV").read_bytes(), "x.csv").rows[:1]
    with pytest.raises(UnrecognizedLayout, match="no data rows"):
        parse_rows(rows, "x.csv")


def test_memo_fills_ids_when_filename_does_not_match(fixtures: Path) -> None:
    rows = read_rows((fixtures / f"{CLEAN_STEM}.CSV").read_bytes(), "x.csv").rows
    e = parse_rows(rows, "odd name.csv")
    assert not e.filename_matched
    assert (e.payroll_id, e.batch_id, e.run_id) == ("100001", "200001", "4000001")
    assert e.property_name_raw == "odd name"


def test_short_rows_are_padded() -> None:
    rows = [
        [
            "subsidiary",
            "postingperiod",
            "journalItemheader_memo",
            "tranDate",
            "journalItemLine_accountRef",
            "journalItemLine_debitAmount",
            "journalItemLine_creditAmount",
            "journalItemLine_memo",
            "journalItemLine_departmentRef",
            "journalItemLine_classRef",
            "journalItemLine_locationRef",
        ],
        ["1", "10/15/2026", "1.2.3", "10/20/2026", "279", "", "5.00"],
        ["1", "10/15/2026", "1.2.3", "10/20/2026", "767", "5.00", ""],
    ]
    e = parse_rows(rows, "P JE_1_2.csv")
    assert e.is_balanced
    assert e.locations == [""]
