"""Import the financial-to-statistical account map from the NetSuite lookup CSV.

The input is the output of SynchroHRM's ``deploy/netsuite-account-lookup.sh``.
Accounting's rule is that a financial internal ID must never leave TeamSync, so
an import is all-or-nothing: one bad row rejects the file and the table is left
as it was.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from teamsync.db import repo

STAT_NUMBER_OFFSET = 900_000
REQUIRED_COLUMNS = (
    "financial_id",
    "financial_number",
    "financial_name",
    "financial_type",
    "stat_number",
    "stat_id",
    "stat_name",
    "stat_inactive",
    "status",
)


class AccountMapError(ValueError):
    """The file cannot be imported. The message lists every problem found."""


def _parse_row(row: dict[str, str]) -> repo.AccountMapRow | str:
    """Return the validated row, or a description of what is wrong with it."""
    fin_id = row["financial_id"].strip()
    fin_number = row["financial_number"].strip()
    stat_number = row["stat_number"].strip()
    stat_id = row["stat_id"].strip()
    status = row["status"].strip()

    if status not in ("mapped", "stat_account_missing"):
        return f"status is {status!r}; resolve it in NetSuite and run the lookup again"
    if not fin_id.isdigit():
        return f"financial_id {fin_id!r} is not numeric"
    if not fin_number.isdigit():
        return f"financial_number {fin_number!r} is not numeric"
    if not row["financial_name"].strip():
        return "financial_name is blank"
    expected = str(int(fin_number) + STAT_NUMBER_OFFSET)
    if stat_number != expected:
        return f"stat_number {stat_number!r} should be {expected}"
    if len(expected) != 6 or not expected.startswith("90"):
        return f"stat_number {expected} is outside the 90xxxx range"
    if status == "mapped" and not stat_id.isdigit():
        return f"status is mapped but stat_id is {stat_id!r}"
    if status == "stat_account_missing" and stat_id:
        return f"status is stat_account_missing but stat_id is {stat_id!r}"
    if stat_id == fin_id:
        return "stat_id equals financial_id"
    if status == "mapped" and row["stat_inactive"].strip().upper() != "F":
        return f"stat account {stat_number} is not active"

    return repo.AccountMapRow(
        financial_internal_id=fin_id,
        financial_number=fin_number,
        financial_name=row["financial_name"].strip(),
        financial_type=row["financial_type"].strip() or None,
        stat_number=stat_number,
        stat_internal_id=stat_id or None,
        stat_name=row["stat_name"].strip() or None,
    )


def parse_account_map(path: Path) -> list[repo.AccountMapRow]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise AccountMapError(f"missing columns: {', '.join(missing)}")
            raw = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise AccountMapError(f"not a readable UTF-8 CSV: {exc}") from exc

    if not raw:
        raise AccountMapError("the file has no data rows")

    rows: list[repo.AccountMapRow] = []
    problems: list[str] = []
    seen: set[str] = set()
    for line_no, record in enumerate(raw, start=2):
        if None in record or any(record[c] is None for c in REQUIRED_COLUMNS):
            problems.append(f"line {line_no}: wrong number of columns")
            continue
        parsed = _parse_row(record)
        if isinstance(parsed, str):
            problems.append(f"line {line_no}: {parsed}")
        elif parsed.financial_internal_id in seen:
            problems.append(f"line {line_no}: duplicate financial_id {parsed.financial_internal_id}")
        else:
            seen.add(parsed.financial_internal_id)
            rows.append(parsed)
    if problems:
        raise AccountMapError("; ".join(problems))
    return rows


def import_account_map(conn: sqlite3.Connection, path: Path) -> list[repo.AccountMapRow]:
    """Validate the whole file, then upsert every row in one transaction."""
    rows = parse_account_map(path)
    conn.execute("BEGIN")
    try:
        for row in rows:
            repo.upsert_account_map_row(conn, row, path.name)
        conn.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK")
        raise AccountMapError(f"conflicts with an existing row: {exc}") from exc
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return rows
