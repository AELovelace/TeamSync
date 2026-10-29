"""Server-rendered pages."""

from __future__ import annotations

import sqlite3
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from teamsync.db import repo
from teamsync.services import AuthError, AuthService, IngestionService
from teamsync.services.auth import TOKEN_SCOPES
from teamsync.sources import IncomingFile

from . import ops
from .deps import SESSION_COOKIE, AppSettings, Conn, PageAdmin, PageUser, Templates, optional_user

router = APIRouter(include_in_schema=False)

PAGE_SIZE = 50


def render(templates: Templates, request: Request, name: str, **ctx: object) -> HTMLResponse:
    ctx.setdefault("msg", request.query_params.get("msg"))
    ctx.setdefault("err", request.query_params.get("err"))
    return templates.TemplateResponse(request, name, ctx)


def back(url: str, *, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    return RedirectResponse(url=url, status_code=303)


def _safe_next(value: str | None) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") else "/"


# --- auth ------------------------------------------------------------------


@router.get("/login")
def login_form(
    request: Request,
    templates: Templates,
    user: Annotated[sqlite3.Row | None, Depends(optional_user)],
    next: str = "/",
):
    if user is not None:
        return RedirectResponse(url=_safe_next(next), status_code=303)
    return render(templates, request, "login.html", next=_safe_next(next), user=None)


@router.post("/login")
def login_submit(
    request: Request,
    templates: Templates,
    conn: Conn,
    settings: AppSettings,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
):
    try:
        token, _user = AuthService(conn).login(username, password)
    except AuthError as exc:
        return render(templates, request, "login.html", next=_safe_next(next), user=None, err=str(exc))
    response = RedirectResponse(url=_safe_next(next), status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=7 * 24 * 3600,
        path="/",
    )
    return response


@router.post("/logout")
def logout(request: Request, conn: Conn):
    AuthService(conn).logout(request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# --- dashboard -------------------------------------------------------------


@router.get("/")
def dashboard(request: Request, templates: Templates, conn: Conn, user: PageUser, settings: AppSettings):
    summary = repo.summary(conn)
    runs = repo.recent_runs(conn, 8)
    flagged = repo.list_entries(conn, repo.EntryFilter(flag="any", limit=15))
    recent = repo.list_entries(conn, repo.EntryFilter(limit=10))
    return render(
        templates,
        request,
        "dashboard.html",
        user=user,
        summary=summary,
        runs=runs,
        flagged=flagged,
        recent=recent,
        watch_dir=settings.watch_dir,
    )


# --- entries ---------------------------------------------------------------


@router.get("/entries")
def entries(
    request: Request,
    templates: Templates,
    conn: Conn,
    user: PageUser,
    q: str = "",
    flag: str = "",
    batch_folder: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
):
    page = max(1, page)
    f = repo.EntryFilter(
        property_name=q or None,
        flag=flag or None,
        batch_folder=batch_folder or None,
        tran_date_from=date_from or None,
        tran_date_to=date_to or None,
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
    )
    total = repo.count_entries(conn, f)
    rows = repo.list_entries(conn, f)
    query = {
        k: v
        for k, v in (
            ("q", q),
            ("flag", flag),
            ("batch_folder", batch_folder),
            ("date_from", date_from),
            ("date_to", date_to),
        )
        if v
    }
    return render(
        templates,
        request,
        "entries.html",
        user=user,
        rows=rows,
        total=total,
        page=page,
        pages=max(1, -(-total // PAGE_SIZE)),
        query=query,
        query_string=urlencode(query),
        batch_folders=repo.distinct_batch_folders(conn),
        filters={
            "q": q,
            "flag": flag,
            "batch_folder": batch_folder,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@router.get("/entries/{entry_id}")
def entry_detail(request: Request, templates: Templates, conn: Conn, user: PageUser, entry_id: int):
    entry = repo.get_entry(conn, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such entry.")
    lines = repo.get_lines(conn, entry_id)
    siblings = [
        r for r in repo.entries_for_batch(conn, entry["payroll_id"], entry["batch_id"]) if r["id"] != entry_id
    ]
    alias = conn.execute(
        "SELECT p.id, p.canonical_name FROM property_aliases pa JOIN properties p ON p.id = pa.property_id WHERE pa.alias = ?",
        (entry["property_name_raw"],),
    ).fetchone()
    return render(
        templates,
        request,
        "entry.html",
        user=user,
        entry=entry,
        lines=lines,
        siblings=siblings,
        canonical=alias,
        flags=ops.flags_from_json(entry["validation_flags"]),
        subsidiaries=ops.flags_from_json(entry["subsidiaries"]),
        locations=ops.flags_from_json(entry["locations"]),
    )


# --- files -----------------------------------------------------------------


@router.get("/files")
def files(request: Request, templates: Templates, conn: Conn, user: PageUser, status: str = ""):
    rows = repo.list_source_files(conn, status=status or None, limit=500)
    entry_ids = {
        r["source_file_id"]: r["id"] for r in conn.execute("SELECT id, source_file_id FROM journal_entries")
    }
    return render(templates, request, "files.html", user=user, rows=rows, status=status, entry_ids=entry_ids)


@router.get("/files/{source_file_id}/download")
def file_download(conn: Conn, settings: AppSettings, user: PageUser, source_file_id: int):
    row = repo.get_source_file(conn, source_file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such file.")
    path = IngestionService(conn, settings.files_dir).stored_path_for(source_file_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=410, detail="Stored bytes are missing.")
    return FileResponse(path, filename=row["original_name"], media_type="application/octet-stream")


# --- properties ------------------------------------------------------------


@router.get("/properties")
def properties(request: Request, templates: Templates, conn: Conn, user: PageUser):
    return render(
        templates,
        request,
        "properties.html",
        user=user,
        properties=repo.list_properties(conn),
        raw_names=repo.raw_property_names(conn),
    )


@router.post("/properties")
def property_create(
    conn: Conn,
    user: PageAdmin,
    canonical_name: Annotated[str, Form()],
    netsuite_subsidiary: Annotated[str, Form()] = "",
    netsuite_location: Annotated[str, Form()] = "",
    alias: Annotated[str, Form()] = "",
):
    name = canonical_name.strip()
    if not name:
        return back("/properties", err="Property name is required.")
    try:
        pid = repo.create_property(conn, name, netsuite_subsidiary.strip(), netsuite_location.strip())
    except sqlite3.IntegrityError:
        return back("/properties", err=f"'{name}' already exists.")
    if alias.strip():
        repo.set_alias(conn, alias.strip(), pid)
    return back("/properties", msg=f"Created {name}.")


@router.post("/properties/alias")
def property_alias(
    conn: Conn,
    user: PageAdmin,
    alias: Annotated[str, Form()],
    property_id: Annotated[str, Form()] = "",
    new_name: Annotated[str, Form()] = "",
):
    alias = alias.strip()
    if not alias:
        return back("/properties", err="Alias is required.")
    if new_name.strip():
        try:
            pid = repo.create_property(conn, new_name.strip())
        except sqlite3.IntegrityError:
            return back("/properties", err=f"'{new_name.strip()}' already exists.")
    elif property_id.isdigit() and repo.get_property(conn, int(property_id)):
        pid = int(property_id)
    else:
        return back("/properties", err="Pick a property or type a new name.")
    repo.set_alias(conn, alias, pid)
    return back("/properties", msg=f"Mapped '{alias}'.")


@router.post("/properties/alias/delete")
def property_alias_delete(conn: Conn, user: PageAdmin, alias: Annotated[str, Form()]):
    repo.delete_alias(conn, alias)
    return back("/properties", msg=f"Unmapped '{alias}'.")


@router.post("/properties/{property_id}/delete")
def property_delete(conn: Conn, user: PageAdmin, property_id: int):
    repo.delete_property(conn, property_id)
    return back("/properties", msg="Property deleted.")


# --- admin -----------------------------------------------------------------


def _admin_page(
    request: Request,
    templates: Templates,
    conn: sqlite3.Connection,
    user: sqlite3.Row,
    settings: AppSettings,
    *,
    new_token: str | None = None,
) -> HTMLResponse:
    auth = AuthService(conn)
    response = render(
        templates,
        request,
        "admin.html",
        user=user,
        users=auth.list_users(),
        tokens=auth.list_device_tokens(),
        runs=repo.recent_runs(conn, 25),
        watch_dir=settings.watch_dir,
        sync_interval=settings.sync_interval_seconds,
        new_token=new_token,
        token_scopes=TOKEN_SCOPES,
    )
    if new_token:
        response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/admin")
def admin(request: Request, templates: Templates, conn: Conn, user: PageAdmin, settings: AppSettings):
    return _admin_page(request, templates, conn, user, settings)


@router.post("/admin/users")
def admin_user_create(
    conn: Conn,
    user: PageAdmin,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    role: Annotated[str, Form()] = "viewer",
):
    try:
        AuthService(conn).create_user(username, password, role)
    except AuthError as exc:
        return back("/admin", err=str(exc))
    return back("/admin", msg=f"Created user {username.strip()}.")


@router.post("/admin/users/{user_id}/password")
def admin_user_password(conn: Conn, user: PageAdmin, user_id: int, password: Annotated[str, Form()]):
    try:
        AuthService(conn).set_password(user_id, password)
    except AuthError as exc:
        return back("/admin", err=str(exc))
    return back("/admin", msg="Password updated. That user's sessions were signed out.")


@router.post("/admin/users/{user_id}/role")
def admin_user_role(conn: Conn, user: PageAdmin, user_id: int, role: Annotated[str, Form()]):
    if user_id == user["id"]:
        return back("/admin", err="You cannot change your own role.")
    try:
        AuthService(conn).set_role(user_id, role)
    except AuthError as exc:
        return back("/admin", err=str(exc))
    return back("/admin", msg="Role updated.")


@router.post("/admin/users/{user_id}/delete")
def admin_user_delete(conn: Conn, user: PageAdmin, user_id: int):
    if user_id == user["id"]:
        return back("/admin", err="You cannot delete yourself.")
    try:
        AuthService(conn).delete_user(user_id)
    except AuthError as exc:
        return back("/admin", err=str(exc))
    return back("/admin", msg="User deleted.")


@router.post("/admin/tokens")
def admin_token_create(
    request: Request,
    templates: Templates,
    conn: Conn,
    settings: AppSettings,
    user: PageAdmin,
    name: Annotated[str, Form()],
    scope: Annotated[str, Form()] = "pusher",
):
    try:
        _id, plain = AuthService(conn).create_device_token(name, user["username"], scope)
    except AuthError as exc:
        return back("/admin", err=str(exc))
    # Rendered straight from the POST: the token never travels in a URL, so it stays out
    # of browser history and the reverse proxy's access log.
    return _admin_page(request, templates, conn, user, settings, new_token=plain)


@router.post("/admin/tokens/{token_id}/revoke")
def admin_token_revoke(conn: Conn, user: PageAdmin, token_id: int):
    AuthService(conn).revoke_device_token(token_id)
    return back("/admin", msg="Token revoked.")


@router.post("/admin/sync")
async def admin_sync(request: Request, settings: AppSettings, user: PageAdmin):
    if not settings.watch_dir:
        return back("/admin", err="No watch directory is configured on this server.")
    summary = await run_in_threadpool(ops.ingest_watch_dir, request.app, "manual")
    return back(
        "/admin",
        msg=f"Sync run {summary.run_id}: {summary.new} new, {summary.duplicate} already stored, {summary.failed} failed.",
    )


@router.post("/admin/reparse")
async def admin_reparse(request: Request, user: PageAdmin):
    summary = await run_in_threadpool(ops.reparse_all, request.app)
    return back(
        "/admin", msg=f"Reparse run {summary.run_id}: {summary.parsed} parsed, {summary.failed} failed."
    )


@router.post("/admin/upload")
async def admin_upload(
    request: Request,
    settings: AppSettings,
    user: PageAdmin,
    files: Annotated[list[UploadFile], File()],
):
    incoming: list[IncomingFile] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        if len(data) > settings.max_upload_bytes:
            return back("/admin", err=f"{f.filename} is larger than the upload limit.")
        incoming.append(IncomingFile(name=f.filename or "upload.bin", data=data, source_kind="upload"))
    if not incoming:
        return back("/admin", err="No files received.")
    summary = await run_in_threadpool(ops.ingest_files, request.app, incoming, "upload")
    return back(
        "/admin",
        msg=f"Upload run {summary.run_id}: {summary.new} new, {summary.duplicate} already stored, {summary.parsed} parsed.",
    )
