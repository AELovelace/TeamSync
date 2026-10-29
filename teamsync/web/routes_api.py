"""JSON API used by the push script and by anything else that wants the data."""

from __future__ import annotations

import csv
import io
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from teamsync.db import repo
from teamsync.services import IngestionService
from teamsync.sources import IncomingFile

from . import ops
from .deps import ApiAdmin, ApiReader, ApiUploader, AppSettings, Conn

router = APIRouter()


@router.get("/health")
def health(conn: Conn) -> dict[str, object]:
    conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "database": True}


def _known_response(conn, hashes: list[str]) -> dict[str, list[str]]:
    wanted = [h.strip().lower() for h in hashes if h and h.strip()]
    bad = [h for h in wanted if len(h) != 64 or any(c not in "0123456789abcdef" for c in h)]
    if bad:
        raise HTTPException(status_code=400, detail=f"Not a sha256 hex digest: {bad[0]}")
    known = repo.known_hashes(conn, wanted)
    return {"known": sorted(known), "unknown": [h for h in wanted if h not in known]}


class KnownRequest(BaseModel):
    hashes: list[str] = Field(default_factory=list, max_length=5000)


@router.get("/api/files/known")
def files_known(
    conn: Conn,
    _: ApiUploader,
    hashes: str = Query("", description="comma-separated sha256; a few dozen at most, POST for more"),
):
    """Short form. The request line has to fit a reverse proxy's header buffer (8 KB on
    stock nginx), so clients with more than about a hundred hashes use the POST form."""
    return _known_response(conn, hashes.split(","))


@router.post("/api/files/known")
def files_known_post(conn: Conn, _: ApiUploader, body: KnownRequest):
    """Which of these sha256 digests the server already holds. JSON body: {\"hashes\": [...]}."""
    return _known_response(conn, body.hashes)


@router.post("/api/upload")
async def upload(
    request: Request,
    settings: AppSettings,
    _: ApiUploader,
    file: Annotated[UploadFile, File()],
    source_path: Annotated[str | None, Form()] = None,
    batch_folder: Annotated[str | None, Form()] = None,
):
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File too large.")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    incoming = IncomingFile(
        name=file.filename or "upload.bin",
        data=data,
        source_kind="push",
        source_path=source_path or None,
        batch_folder=batch_folder or None,
    )
    summary = await run_in_threadpool(ops.ingest_files, request.app, [incoming], "upload")
    return ops.summary_to_dict(summary)


@router.post("/api/sync")
async def sync(request: Request, settings: AppSettings, _: ApiAdmin):
    if not settings.watch_dir:
        raise HTTPException(status_code=409, detail="TEAMSYNC_WATCH_DIR is not configured on this server.")
    summary = await run_in_threadpool(ops.ingest_watch_dir, request.app, "manual")
    return ops.summary_to_dict(summary)


@router.post("/api/reparse")
async def reparse(request: Request, _: ApiAdmin):
    summary = await run_in_threadpool(ops.reparse_all, request.app)
    return ops.summary_to_dict(summary)


@router.get("/api/summary")
def api_summary(conn: Conn, _: ApiReader):
    return repo.summary(conn)


def _filter_from_query(
    property: str | None,
    flag: str | None,
    batch_folder: str | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
    offset: int,
) -> repo.EntryFilter:
    return repo.EntryFilter(
        property_name=property or None,
        flag=flag or None,
        batch_folder=batch_folder or None,
        tran_date_from=date_from or None,
        tran_date_to=date_to or None,
        limit=max(1, min(limit, 1000)),
        offset=max(0, offset),
    )


def _entry_dict(row) -> dict[str, object]:
    d = dict(row)
    d["validation_flags"] = ops.flags_from_json(d.get("validation_flags"))
    for key in ("subsidiaries", "locations"):
        if key in d:
            d[key] = ops.flags_from_json(d[key])
    d["is_balanced"] = bool(d.get("is_balanced"))
    return d


@router.get("/api/entries")
def api_entries(
    conn: Conn,
    _: ApiReader,
    property: str | None = None,
    flag: str | None = None,
    batch_folder: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    f = _filter_from_query(property, flag, batch_folder, date_from, date_to, limit, offset)
    return {
        "total": repo.count_entries(conn, f),
        "entries": [_entry_dict(r) for r in repo.list_entries(conn, f)],
    }


@router.get("/api/entries/{entry_id}")
def api_entry(conn: Conn, _: ApiReader, entry_id: int):
    entry = repo.get_entry(conn, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such entry.")
    lines = [dict(r) for r in repo.get_lines(conn, entry_id)]
    for ln in lines:
        ln["account_is_numeric"] = bool(ln["account_is_numeric"])
    return {"entry": _entry_dict(entry), "lines": lines}


@router.get("/api/files/{source_file_id}/raw")
def api_raw_file(conn: Conn, settings: AppSettings, _: ApiReader, source_file_id: int):
    from fastapi.responses import FileResponse

    row = repo.get_source_file(conn, source_file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such file.")
    path = IngestionService(conn, settings.files_dir).stored_path_for(source_file_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=410, detail="Stored bytes are missing.")
    return FileResponse(path, filename=row["original_name"], media_type="application/octet-stream")


EXPORT_COLUMNS = (
    "entry_id",
    "property",
    "property_name_raw",
    "payroll_id",
    "batch_id",
    "run_id",
    "header_memo",
    "posting_period",
    "tran_date",
    "original_name",
    "batch_folder",
    "validation_flags",
    "line_no",
    "subsidiary",
    "account_ref",
    "debit",
    "credit",
    "line_memo",
    "department_ref",
    "department_style",
    "class_ref",
    "location_ref",
)


@router.get("/api/export/entries.csv")
def export_csv(
    conn: Conn,
    _: ApiReader,
    property: str | None = None,
    flag: str | None = None,
    batch_folder: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    f = _filter_from_query(property, flag, batch_folder, date_from, date_to, 1_000_000, 0)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for row in repo.export_lines(conn, f):
        d = dict(row)
        d["validation_flags"] = " ".join(ops.flags_from_json(d["validation_flags"]))
        writer.writerow([d[c] if d[c] is not None else "" for c in EXPORT_COLUMNS])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="teamsync-entries.csv"'},
    )
