"""SQLite connection and migrations.

Migrations are numbered ``NNNN_name.sql`` files in ``migrations/``, applied in
order inside a transaction each, and recorded in ``schema_migrations``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def applied_migrations(conn: sqlite3.Connection) -> set[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    return {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}


def pending_migrations(conn: sqlite3.Connection) -> list[Path]:
    done = applied_migrations(conn)
    return [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name not in done]


def migrate(conn: sqlite3.Connection) -> list[str]:
    applied: list[str] = []
    for path in pending_migrations(conn):
        sql = path.read_text(encoding="utf-8")
        # executescript commits any open transaction before it runs, so the
        # transaction has to live inside the script itself for atomicity.
        name_literal = path.name.replace("'", "''")
        script = (
            "BEGIN;\n"
            f"{sql}\n"
            "INSERT INTO schema_migrations (name, applied_at) "
            f"VALUES ('{name_literal}', strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(path.name)
    return applied


def open_database(path: Path | str) -> sqlite3.Connection:
    """Connect and bring the schema up to date."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    migrate(conn)
    return conn
