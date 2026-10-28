"""Store incoming files, parse them, record the results.

Flow per file: hash -> skip if seen -> write raw bytes to the files dir ->
insert source_files row -> parse -> insert journal_entries + journal_lines or
record why not. Raw bytes are always kept, whatever the parse outcome.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from teamsync.db import repo
from teamsync.parsers import RowParseError, UnrecognizedLayout, UnsupportedFormat, file_kind, parse_file
from teamsync.sources import IncomingFile

log = logging.getLogger(__name__)

INGEST_FLAG_DESCRIPTIONS: dict[str, str] = {
    "duplicate_batch": "Another stored file carries the same payroll and batch IDs.",
}


@dataclass(slots=True)
class FileOutcome:
    name: str
    status: str  # new | duplicate | failed
    parse_status: str | None = None  # parsed | unrecognized_layout | unsupported | failed
    source_file_id: int | None = None
    entry_id: int | None = None
    flags: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class IngestSummary:
    run_id: int
    outcomes: list[FileOutcome] = field(default_factory=list)

    @property
    def seen(self) -> int:
        return len(self.outcomes)

    @property
    def new(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "new")

    @property
    def duplicate(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "duplicate")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "failed" or o.parse_status == "failed")

    @property
    def parsed(self) -> int:
        return sum(1 for o in self.outcomes if o.parse_status == "parsed")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class IngestionService:
    def __init__(self, conn: sqlite3.Connection, files_dir: Path) -> None:
        self.conn = conn
        self.files_dir = Path(files_dir)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    # --- public ------------------------------------------------------------

    def ingest(self, files: Iterable[IncomingFile], trigger_source: str) -> IngestSummary:
        run_id = repo.start_run(self.conn, trigger_source)
        summary = IngestSummary(run_id=run_id)
        try:
            for incoming in files:
                summary.outcomes.append(self.ingest_one(incoming, run_id))
        except Exception as exc:  # a broken source, not a broken file
            log.exception("ingest run %s aborted", run_id)
            self._finish(summary, status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        status = "ok" if summary.failed == 0 else "partial"
        self._finish(summary, status=status)
        return summary

    def ingest_one(self, incoming: IncomingFile, run_id: int) -> FileOutcome:
        digest = sha256_hex(incoming.data)
        existing = repo.find_file_by_hash(self.conn, digest)
        if existing is not None:
            return FileOutcome(
                name=incoming.name,
                status="duplicate",
                parse_status=existing["parse_status"],
                source_file_id=existing["id"],
            )

        kind = file_kind(incoming.name)
        stored_rel = self._store_bytes(digest, incoming.name, incoming.data)

        self.conn.execute("BEGIN")
        try:
            source_file_id = repo.insert_source_file(
                self.conn,
                sha256=digest,
                original_name=incoming.name,
                file_kind=kind,
                parse_status="pending",
                source_kind=incoming.source_kind,
                source_path=incoming.source_path,
                batch_folder=incoming.batch_folder,
                size_bytes=len(incoming.data),
                stored_path=stored_rel,
                ingest_run_id=run_id,
            )
            outcome = self._parse_into(source_file_id, incoming.name, incoming.data)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        outcome.status = "new"
        return outcome

    def reparse_all(self, trigger_source: str = "reparse") -> IngestSummary:
        """Re-run the parser over every stored file. Entries are rebuilt in place."""
        run_id = repo.start_run(self.conn, trigger_source)
        summary = IngestSummary(run_id=run_id)
        for row in repo.all_source_files(self.conn):
            path = self.files_dir / row["stored_path"]
            try:
                data = path.read_bytes()
            except OSError as exc:
                summary.outcomes.append(
                    FileOutcome(
                        name=row["original_name"],
                        status="failed",
                        parse_status="failed",
                        source_file_id=row["id"],
                        error=f"stored file missing: {exc}",
                    )
                )
                repo.set_parse_status(self.conn, row["id"], "failed", f"stored file missing: {path}")
                continue
            self.conn.execute("BEGIN")
            try:
                repo.delete_entry_for_file(self.conn, row["id"])
                outcome = self._parse_into(row["id"], row["original_name"], data)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            outcome.status = "new"
            summary.outcomes.append(outcome)
        self._finish(summary, status="ok" if summary.failed == 0 else "partial")
        return summary

    def stored_path_for(self, source_file_id: int) -> Path | None:
        row = repo.get_source_file(self.conn, source_file_id)
        return None if row is None else self.files_dir / row["stored_path"]

    # --- internals ---------------------------------------------------------

    def _store_bytes(self, digest: str, name: str, data: bytes) -> str:
        suffix = PurePath(name).suffix.lower() or ".bin"
        rel = Path(digest[:2]) / f"{digest}{suffix}"
        target = self.files_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
        return rel.as_posix()

    def _parse_into(self, source_file_id: int, name: str, data: bytes) -> FileOutcome:
        outcome = FileOutcome(name=name, status="new", source_file_id=source_file_id)
        try:
            entry = parse_file(data, name)
        except UnsupportedFormat as exc:
            outcome.parse_status = "unsupported"
            outcome.error = str(exc)
        except UnrecognizedLayout as exc:
            outcome.parse_status = "unrecognized_layout"
            outcome.error = str(exc)
        except RowParseError as exc:
            outcome.parse_status = "failed"
            outcome.error = str(exc)
        except Exception as exc:  # reader blew up on a corrupt workbook, keep going
            log.exception("unexpected parse failure for %s", name)
            outcome.parse_status = "failed"
            outcome.error = f"{type(exc).__name__}: {exc}"
        else:
            extra: list[str] = []
            if repo.other_entries_for_batch(self.conn, entry.payroll_id, entry.batch_id, source_file_id):
                extra.append("duplicate_batch")
            outcome.entry_id = repo.insert_entry(self.conn, source_file_id, entry, extra)
            outcome.parse_status = "parsed"
            outcome.flags = list(entry.flags) + extra
        repo.set_parse_status(self.conn, source_file_id, outcome.parse_status, outcome.error)
        return outcome

    def _finish(self, summary: IngestSummary, *, status: str, error: str | None = None) -> None:
        repo.finish_run(
            self.conn,
            summary.run_id,
            status=status,
            files_seen=summary.seen,
            files_new=summary.new,
            files_duplicate=summary.duplicate,
            files_failed=summary.failed,
            error=error,
        )
