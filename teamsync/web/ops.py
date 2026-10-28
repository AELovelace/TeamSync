"""Operations the web layer triggers: ingest from the watch dir, from uploads,
and reparse. Each opens its own connection and holds the app-wide ingest lock
so two syncs never interleave."""

from __future__ import annotations

import json
from collections.abc import Iterable

from fastapi import FastAPI

from teamsync.config import Settings
from teamsync.db import connect
from teamsync.parsers import FLAG_DESCRIPTIONS
from teamsync.services import INGEST_FLAG_DESCRIPTIONS, IngestionService, IngestSummary
from teamsync.sources import IncomingFile, LocalDirectorySource

ALL_FLAG_DESCRIPTIONS: dict[str, str] = {**FLAG_DESCRIPTIONS, **INGEST_FLAG_DESCRIPTIONS}


def flags_from_json(text: str | None) -> list[str]:
    if not text:
        return []
    try:
        value = json.loads(text)
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _settings(app: FastAPI) -> Settings:
    return app.state.settings


def ingest_files(app: FastAPI, files: Iterable[IncomingFile], trigger: str) -> IngestSummary:
    settings = _settings(app)
    with app.state.ingest_lock:
        conn = connect(settings.database_path)
        try:
            return IngestionService(conn, settings.files_dir).ingest(files, trigger)
        finally:
            conn.close()


def ingest_watch_dir(app: FastAPI, trigger: str) -> IngestSummary | None:
    settings = _settings(app)
    if not settings.watch_dir:
        return None
    return ingest_files(
        app, LocalDirectorySource(settings.watch_dir, source_kind="local").iter_files(), trigger
    )


def reparse_all(app: FastAPI, trigger: str = "reparse") -> IngestSummary:
    settings = _settings(app)
    with app.state.ingest_lock:
        conn = connect(settings.database_path)
        try:
            return IngestionService(conn, settings.files_dir).reparse_all(trigger)
        finally:
            conn.close()


def summary_to_dict(summary: IngestSummary) -> dict[str, object]:
    return {
        "run_id": summary.run_id,
        "seen": summary.seen,
        "new": summary.new,
        "duplicate": summary.duplicate,
        "parsed": summary.parsed,
        "failed": summary.failed,
        "files": [
            {
                "name": o.name,
                "status": o.status,
                "parse_status": o.parse_status,
                "source_file_id": o.source_file_id,
                "entry_id": o.entry_id,
                "flags": o.flags,
                "error": o.error,
            }
            for o in summary.outcomes
        ],
    }
