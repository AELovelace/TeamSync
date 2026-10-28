"""Turn an uploaded file into rows of strings.

Every supported format ends up as ``list[list[str]]`` so that exactly one
journal-entry parser sits downstream. CSV goes through the stdlib; xls, xlsx,
xlsb and ods go through python-calamine. Cell values are stringified
conservatively: numbers never pass through float arithmetic on the way to
``Decimal``, and dates come out as ISO ``YYYY-MM-DD``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field
from pathlib import PurePath

from python_calamine import CalamineWorkbook

SPREADSHEET_KINDS: dict[str, str] = {
    ".csv": "csv",
    ".xls": "xls",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".xlsb": "xlsb",
    ".ods": "ods",
}

PARSEABLE_KINDS = frozenset(SPREADSHEET_KINDS.values())


class UnsupportedFormat(Exception):
    """The file extension is not one we parse."""


@dataclass(slots=True)
class ReadResult:
    rows: list[list[str]]
    sheet_count: int = 1
    sheet_name: str | None = None
    warnings: list[str] = field(default_factory=list)


def file_kind(filename: str) -> str:
    """Classify a filename by extension. Unknown extensions are ``other``."""
    return SPREADSHEET_KINDS.get(PurePath(filename).suffix.lower(), "other")


def read_rows(data: bytes, filename: str) -> ReadResult:
    kind = file_kind(filename)
    if kind == "csv":
        return _read_csv(data)
    if kind in PARSEABLE_KINDS:
        return _read_workbook(data)
    raise UnsupportedFormat(f"Unsupported file type: {filename}")


# --- CSV -------------------------------------------------------------------


def _decode(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
            if encoding != "utf-8-sig":
                warnings.append(f"decoded as {encoding}")
            return text, warnings
        except UnicodeDecodeError:
            continue
    warnings.append("decoded as latin-1 with replacement")
    return data.decode("latin-1", errors="replace"), warnings


def _read_csv(data: bytes) -> ReadResult:
    text, warnings = _decode(data)
    reader = csv.reader(io.StringIO(text, newline=""))
    rows = [[cell.strip() for cell in row] for row in reader]
    rows = [row for row in rows if any(row)]
    return ReadResult(rows=rows, warnings=warnings)


# --- Workbooks -------------------------------------------------------------


def _cell_to_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        # repr gives the shortest round-trip string, which is what the
        # spreadsheet author typed in practically every case.
        return repr(value)
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    return str(value).strip()


def _read_workbook(data: bytes) -> ReadResult:
    workbook = CalamineWorkbook.from_filelike(io.BytesIO(data))
    names = workbook.sheet_names
    if not names:
        return ReadResult(rows=[], sheet_count=0, warnings=["workbook has no sheets"])
    sheet = workbook.get_sheet_by_index(0)
    raw = sheet.to_python(skip_empty_area=False)
    rows = [[_cell_to_str(cell) for cell in row] for row in raw]
    rows = [row for row in rows if any(row)]
    warnings: list[str] = []
    if len(names) > 1:
        warnings.append(f"ignored {len(names) - 1} extra sheet(s): {', '.join(names[1:])}")
    return ReadResult(rows=rows, sheet_count=len(names), sheet_name=names[0], warnings=warnings)
