from __future__ import annotations

from pathlib import Path

import pytest

from teamsync.parsers import UnsupportedFormat, file_kind, read_rows
from teamsync.parsers.je_rows import EXPECTED_HEADER, normalize_header

CLEAN_STEM = "Sample Hotel JE_100001_200001"


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.csv", "csv"),
        ("A.CSV", "csv"),
        ("a.xls", "xls"),
        ("a.xlsx", "xlsx"),
        ("a.xlsm", "xlsx"),
        ("a.xlsb", "xlsb"),
        ("a.ods", "ods"),
        ("a.pdf", "other"),
        ("desktop.ini", "other"),
        ("noext", "other"),
    ],
)
def test_file_kind(name: str, kind: str) -> None:
    assert file_kind(name) == kind


def test_unsupported_format_raises(fixtures: Path) -> None:
    path = fixtures / "notes.txt"
    with pytest.raises(UnsupportedFormat):
        read_rows(path.read_bytes(), path.name)


@pytest.mark.parametrize("ext", [".CSV", ".xlsx", ".xls", ".ods"])
def test_all_formats_yield_the_header(fixtures: Path, ext: str) -> None:
    path = fixtures / f"{CLEAN_STEM}{ext}"
    result = read_rows(path.read_bytes(), path.name)
    assert normalize_header(result.rows[0]) == EXPECTED_HEADER
    assert len(result.rows) == 11  # header + 10 lines


@pytest.mark.parametrize("ext", [".xlsx", ".xls", ".ods"])
def test_workbook_rows_match_csv_rows_after_normalization(fixtures: Path, ext: str) -> None:
    """Dates come out ISO from workbooks and M/D/Y from CSV. Amounts may differ in
    trailing zeros. Everything else must be identical cell for cell."""
    csv_rows = read_rows((fixtures / f"{CLEAN_STEM}.CSV").read_bytes(), f"{CLEAN_STEM}.CSV").rows
    wb_rows = read_rows((fixtures / f"{CLEAN_STEM}{ext}").read_bytes(), f"{CLEAN_STEM}{ext}").rows
    assert len(csv_rows) == len(wb_rows)
    for c_row, w_row in zip(csv_rows[1:], wb_rows[1:], strict=True):
        # subsidiary, header memo, account, memo, dept, class, location are plain text
        for idx in (0, 2, 4, 7, 8, 9, 10):
            assert c_row[idx] == w_row[idx], (idx, c_row, w_row)
        # dates
        assert w_row[1] == "2026-10-15" and c_row[1] == "10/15/2026"
        assert w_row[3] == "2026-10-20" and c_row[3] == "10/20/2026"
        # amounts: compare numerically
        for idx in (5, 6):
            if c_row[idx] == "":
                assert w_row[idx] == ""
            else:
                assert float(c_row[idx]) == float(w_row[idx])


def test_extra_sheets_are_reported(fixtures: Path) -> None:
    path = fixtures / "Sample Hotel Two Sheets JE_100001_200001.xlsx"
    result = read_rows(path.read_bytes(), path.name)
    assert result.sheet_count == 2
    assert result.sheet_name == "JE"
    assert any(w.startswith("ignored 1 extra sheet") for w in result.warnings)
    assert len(result.rows) == 11


def test_cp1252_csv_decodes_with_warning(fixtures: Path) -> None:
    path = fixtures / "Café Lodge JE_100005_200005.CSV"
    result = read_rows(path.read_bytes(), path.name)
    assert result.warnings == ["decoded as cp1252"]
    assert result.rows[1][8] == "Café"


def test_blank_lines_are_dropped() -> None:
    data = b"a,b\r\n\r\n1,2\r\n,\r\n"
    result = read_rows(data, "x.csv")
    assert result.rows == [["a", "b"], ["1", "2"]]
