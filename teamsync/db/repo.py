"""Plain SQL access functions. No ORM. Every function takes the connection first."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from teamsync.parsers import ParsedJournalEntry


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- ingest runs -----------------------------------------------------------


def start_run(conn: sqlite3.Connection, trigger_source: str) -> int:
    cur = conn.execute(
        "INSERT INTO ingest_runs (trigger_source, status, started_at) VALUES (?, 'running', ?)",
        (trigger_source, now_iso()),
    )
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    files_seen: int,
    files_new: int,
    files_duplicate: int,
    files_failed: int,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE ingest_runs
        SET status = ?, finished_at = ?, files_seen = ?, files_new = ?, files_duplicate = ?,
            files_failed = ?, error = ?
        WHERE id = ?
        """,
        (status, now_iso(), files_seen, files_new, files_duplicate, files_failed, error, run_id),
    )


def recent_runs(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM ingest_runs ORDER BY id DESC LIMIT ?", (limit,)))


# --- source files ----------------------------------------------------------


def find_file_by_hash(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM source_files WHERE sha256 = ?", (sha256,)).fetchone()


def known_hashes(conn: sqlite3.Connection, hashes: Iterable[str]) -> set[str]:
    wanted = list(dict.fromkeys(hashes))
    found: set[str] = set()
    for i in range(0, len(wanted), 500):
        chunk = wanted[i : i + 500]
        marks = ",".join("?" * len(chunk))
        found.update(
            r["sha256"]
            for r in conn.execute(f"SELECT sha256 FROM source_files WHERE sha256 IN ({marks})", chunk)
        )
    return found


def insert_source_file(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    original_name: str,
    file_kind: str,
    parse_status: str,
    source_kind: str,
    source_path: str | None,
    batch_folder: str | None,
    size_bytes: int,
    stored_path: str,
    ingest_run_id: int | None,
    parse_error: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO source_files
          (sha256, original_name, file_kind, parse_status, parse_error, source_kind, source_path,
           batch_folder, size_bytes, stored_path, received_at, ingest_run_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sha256,
            original_name,
            file_kind,
            parse_status,
            parse_error,
            source_kind,
            source_path,
            batch_folder,
            size_bytes,
            stored_path,
            now_iso(),
            ingest_run_id,
        ),
    )
    return int(cur.lastrowid)


def set_parse_status(
    conn: sqlite3.Connection, source_file_id: int, status: str, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE source_files SET parse_status = ?, parse_error = ? WHERE id = ?",
        (status, error, source_file_id),
    )


def get_source_file(conn: sqlite3.Connection, source_file_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM source_files WHERE id = ?", (source_file_id,)).fetchone()


def list_source_files(
    conn: sqlite3.Connection, *, status: str | None = None, limit: int = 500
) -> list[sqlite3.Row]:
    if status:
        return list(
            conn.execute(
                "SELECT * FROM source_files WHERE parse_status = ? ORDER BY id DESC LIMIT ?", (status, limit)
            )
        )
    return list(conn.execute("SELECT * FROM source_files ORDER BY id DESC LIMIT ?", (limit,)))


def all_source_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM source_files ORDER BY id"))


# --- journal entries -------------------------------------------------------


def delete_entry_for_file(conn: sqlite3.Connection, source_file_id: int) -> None:
    conn.execute("DELETE FROM journal_entries WHERE source_file_id = ?", (source_file_id,))


def insert_entry(
    conn: sqlite3.Connection,
    source_file_id: int,
    entry: ParsedJournalEntry,
    extra_flags: Iterable[str] = (),
) -> int:
    flags = list(entry.flags) + [f for f in extra_flags if f not in entry.flags]
    cur = conn.execute(
        """
        INSERT INTO journal_entries
          (source_file_id, property_name_raw, payroll_id, batch_id, run_id, header_memo, posting_period,
           tran_date, subsidiaries, locations, line_count, total_debit, total_credit, is_balanced,
           dept_style, validation_flags, parser_version, parsed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_file_id,
            entry.property_name_raw,
            entry.payroll_id,
            entry.batch_id,
            entry.run_id,
            entry.header_memo,
            entry.posting_period,
            entry.tran_date,
            json.dumps(entry.subsidiaries),
            json.dumps(entry.locations),
            entry.line_count,
            str(entry.total_debit),
            str(entry.total_credit),
            int(entry.is_balanced),
            entry.dept_style,
            json.dumps(flags),
            entry.parser_version,
            now_iso(),
        ),
    )
    entry_id = int(cur.lastrowid)
    conn.executemany(
        """
        INSERT INTO journal_lines
          (journal_entry_id, line_no, subsidiary, account_ref, account_is_numeric, debit, credit,
           line_memo, department_ref, department_style, class_ref, location_ref)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                entry_id,
                ln.line_no,
                ln.subsidiary,
                ln.account_ref,
                int(ln.account_is_numeric),
                None if ln.debit is None else str(ln.debit),
                None if ln.credit is None else str(ln.credit),
                ln.line_memo,
                ln.department_ref,
                ln.department_style,
                ln.class_ref,
                ln.location_ref,
            )
            for ln in entry.lines
        ],
    )
    return entry_id


def other_entries_for_batch(
    conn: sqlite3.Connection, payroll_id: str, batch_id: str, exclude_source_file_id: int | None = None
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT id, source_file_id FROM journal_entries
            WHERE payroll_id = ? AND batch_id = ? AND source_file_id IS NOT ?
            """,
            (payroll_id, batch_id, exclude_source_file_id),
        )
    )


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT je.*, sf.original_name, sf.sha256, sf.batch_folder, sf.received_at, sf.file_kind
        FROM journal_entries je JOIN source_files sf ON sf.id = je.source_file_id
        WHERE je.id = ?
        """,
        (entry_id,),
    ).fetchone()


def get_lines(conn: sqlite3.Connection, entry_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM journal_lines WHERE journal_entry_id = ? ORDER BY line_no", (entry_id,))
    )


@dataclass(slots=True)
class EntryFilter:
    property_name: str | None = None
    batch_folder: str | None = None
    flag: str | None = None
    tran_date_from: str | None = None
    tran_date_to: str | None = None
    limit: int = 200
    offset: int = 0


_ENTRY_FROM = """
    FROM journal_entries je
    JOIN source_files sf ON sf.id = je.source_file_id
    LEFT JOIN property_aliases pa ON pa.alias = je.property_name_raw
    LEFT JOIN properties p ON p.id = pa.property_id
"""


def _entry_where(f: EntryFilter) -> tuple[str, list[object]]:
    where: list[str] = []
    params: list[object] = []
    if f.property_name:
        where.append("(je.property_name_raw LIKE ? OR p.canonical_name LIKE ?)")
        params.extend([f"%{f.property_name}%", f"%{f.property_name}%"])
    if f.batch_folder:
        where.append("sf.batch_folder = ?")
        params.append(f.batch_folder)
    if f.flag:
        if f.flag == "any":
            where.append("je.validation_flags != '[]'")
        else:
            where.append("EXISTS (SELECT 1 FROM json_each(je.validation_flags) WHERE value = ?)")
            params.append(f.flag)
    if f.tran_date_from:
        where.append("je.tran_date >= ?")
        params.append(f.tran_date_from)
    if f.tran_date_to:
        where.append("je.tran_date <= ?")
        params.append(f.tran_date_to)
    return (" WHERE " + " AND ".join(where)) if where else "", params


def list_entries(conn: sqlite3.Connection, f: EntryFilter | None = None) -> list[sqlite3.Row]:
    f = f or EntryFilter()
    where, params = _entry_where(f)
    sql = (
        "SELECT je.*, sf.original_name, sf.batch_folder, sf.received_at, sf.file_kind, "
        "p.canonical_name, p.id AS property_id"
        + _ENTRY_FROM
        + where
        + " ORDER BY je.tran_date DESC, je.property_name_raw, je.id DESC LIMIT ? OFFSET ?"
    )
    return list(conn.execute(sql, [*params, f.limit, f.offset]))


def count_entries(conn: sqlite3.Connection, f: EntryFilter | None = None) -> int:
    where, params = _entry_where(f or EntryFilter())
    return int(conn.execute("SELECT COUNT(*)" + _ENTRY_FROM + where, params).fetchone()[0])


def entries_for_batch(conn: sqlite3.Connection, payroll_id: str, batch_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT je.id, je.line_count, je.total_debit, je.validation_flags, sf.original_name, sf.received_at"
            + _ENTRY_FROM
            + " WHERE je.payroll_id = ? AND je.batch_id = ? ORDER BY je.id",
            (payroll_id, batch_id),
        )
    )


def distinct_batch_folders(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT batch_folder FROM source_files WHERE batch_folder IS NOT NULL ORDER BY batch_folder DESC"
        )
    ]


def export_lines(conn: sqlite3.Connection, f: EntryFilter | None = None) -> list[sqlite3.Row]:
    """Flattened lines with their entry header, for CSV export."""
    where, params = _entry_where(f or EntryFilter(limit=1_000_000))
    sql = (
        "SELECT je.id AS entry_id, COALESCE(p.canonical_name, je.property_name_raw) AS property, "
        "je.property_name_raw, je.payroll_id, je.batch_id, je.run_id, je.header_memo, je.posting_period, "
        "je.tran_date, sf.original_name, sf.batch_folder, je.validation_flags, "
        "jl.line_no, jl.subsidiary, jl.account_ref, jl.debit, jl.credit, jl.line_memo, "
        "jl.department_ref, jl.department_style, jl.class_ref, jl.location_ref"
        + _ENTRY_FROM
        + " JOIN journal_lines jl ON jl.journal_entry_id = je.id"
        + where
        + " ORDER BY je.tran_date, je.property_name_raw, je.id, jl.line_no"
    )
    return list(conn.execute(sql, params))


# --- properties ------------------------------------------------------------


def list_properties(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT p.*, (SELECT COUNT(*) FROM property_aliases pa WHERE pa.property_id = p.id) AS alias_count
        FROM properties p ORDER BY p.canonical_name
        """
    )
    result = []
    for r in rows:
        aliases = [
            a[0]
            for a in conn.execute(
                "SELECT alias FROM property_aliases WHERE property_id = ? ORDER BY alias", (r["id"],)
            )
        ]
        result.append({**dict(r), "aliases": aliases})
    return result


def get_property(conn: sqlite3.Connection, property_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()


def create_property(
    conn: sqlite3.Connection, canonical_name: str, subsidiary: str | None = None, location: str | None = None
) -> int:
    cur = conn.execute(
        "INSERT INTO properties (canonical_name, netsuite_subsidiary, netsuite_location, created_at) VALUES (?, ?, ?, ?)",
        (canonical_name.strip(), subsidiary or None, location or None, now_iso()),
    )
    return int(cur.lastrowid)


def delete_property(conn: sqlite3.Connection, property_id: int) -> None:
    conn.execute("DELETE FROM properties WHERE id = ?", (property_id,))


def set_alias(conn: sqlite3.Connection, alias: str, property_id: int) -> None:
    conn.execute(
        "INSERT INTO property_aliases (alias, property_id) VALUES (?, ?) "
        "ON CONFLICT(alias) DO UPDATE SET property_id = excluded.property_id",
        (alias.strip(), property_id),
    )


def delete_alias(conn: sqlite3.Connection, alias: str) -> None:
    conn.execute("DELETE FROM property_aliases WHERE alias = ?", (alias,))


def raw_property_names(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every distinct raw property name seen, with its entry count and current mapping."""
    return list(
        conn.execute(
            """
            SELECT je.property_name_raw, COUNT(*) AS entries, MAX(je.tran_date) AS last_seen,
                   p.id AS property_id, p.canonical_name
            FROM journal_entries je
            LEFT JOIN property_aliases pa ON pa.alias = je.property_name_raw
            LEFT JOIN properties p ON p.id = pa.property_id
            GROUP BY je.property_name_raw
            ORDER BY p.canonical_name IS NOT NULL, je.property_name_raw
            """
        )
    )


def summary(conn: sqlite3.Connection) -> dict[str, object]:
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM source_files) AS files,
          (SELECT COUNT(*) FROM source_files WHERE parse_status = 'parsed') AS files_parsed,
          (SELECT COUNT(*) FROM source_files WHERE parse_status = 'unsupported') AS files_unsupported,
          (SELECT COUNT(*) FROM source_files WHERE parse_status IN ('failed','unrecognized_layout')) AS files_failed,
          (SELECT COUNT(*) FROM journal_entries) AS entries,
          (SELECT COUNT(*) FROM journal_entries WHERE validation_flags != '[]') AS entries_flagged,
          (SELECT COUNT(*) FROM journal_lines) AS lines,
          (SELECT MAX(finished_at) FROM ingest_runs WHERE status != 'running') AS last_run_finished_at
        """
    ).fetchone()
    flag_counts = {
        r["value"]: r["n"]
        for r in conn.execute(
            "SELECT value, COUNT(*) AS n FROM journal_entries, json_each(validation_flags) GROUP BY value ORDER BY n DESC"
        )
    }
    return {**dict(row), "flag_counts": flag_counts}


# --- state -----------------------------------------------------------------


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_state(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        """
        INSERT INTO state (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now_iso()),
    )
