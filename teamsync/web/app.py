"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from teamsync.config import Settings, load_settings
from teamsync.db import open_database
from teamsync.services import AuthService

from . import ops
from .deps import LoginRequired
from .routes_api import router as api_router
from .routes_pages import router as pages_router

log = logging.getLogger(__name__)

HERE = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = open_database(settings.database_path)
        try:
            generated = AuthService(conn).ensure_admin(settings.admin_password)
        finally:
            conn.close()
        if generated:
            marker = settings.data_dir / "initial-admin-password.txt"
            marker.write_text(f"admin\n{generated}\n", encoding="utf-8")
            log.warning("Created user 'admin'. Initial password written to %s", marker)

        watcher: asyncio.Task[None] | None = None
        if settings.watch_dir and settings.sync_interval_seconds > 0:
            watcher = asyncio.create_task(_watch_loop(app, settings))
        try:
            yield
        finally:
            if watcher:
                watcher.cancel()

    app = FastAPI(
        title="TeamSync",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.ingest_lock = threading.Lock()
    app.state.templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.state.templates.env.filters["flags"] = ops.flags_from_json
    app.state.templates.env.globals["flag_descriptions"] = ops.ALL_FLAG_DESCRIPTIONS
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.exception_handler(LoginRequired)
    async def _login_redirect(request: Request, exc: LoginRequired) -> RedirectResponse:
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(url=f"/login?next={target}", status_code=303)

    app.include_router(pages_router)
    app.include_router(api_router)
    return app


async def _watch_loop(app: FastAPI, settings: Settings) -> None:
    assert settings.watch_dir is not None
    log.info("watching %s every %ss", settings.watch_dir, settings.sync_interval_seconds)
    while True:
        try:
            summary = await asyncio.to_thread(ops.ingest_watch_dir, app, "schedule")
            if summary and summary.new:
                log.info(
                    "scheduled sync: run %s new=%s parsed=%s", summary.run_id, summary.new, summary.parsed
                )
        except Exception:
            log.exception("scheduled sync failed")
        await asyncio.sleep(settings.sync_interval_seconds)


def serve(settings: Settings | None = None) -> None:
    """Run under Uvicorn. ``teamsync serve`` calls this."""
    import uvicorn

    settings = settings or load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
