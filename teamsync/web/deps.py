"""Request dependencies: database connection, session user, device token."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from teamsync.config import Settings
from teamsync.db import connect
from teamsync.services import AuthService

SESSION_COOKIE = "teamsync_session"


class LoginRequired(Exception):
    """Raised by page dependencies; the app turns it into a redirect to /login."""


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def get_conn(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(settings.database_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def optional_user(request: Request, conn: Conn) -> sqlite3.Row | None:
    return AuthService(conn).user_for_session(request.cookies.get(SESSION_COOKIE))


def page_user(user: Annotated[sqlite3.Row | None, Depends(optional_user)]) -> sqlite3.Row:
    if user is None:
        raise LoginRequired()
    return user


def page_admin(user: Annotated[sqlite3.Row, Depends(page_user)]) -> sqlite3.Row:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    return user


def api_principal(request: Request, conn: Conn) -> dict[str, str]:
    """API calls accept a device token (Authorization: Bearer tsk_...) or a logged-in session."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = AuthService(conn).verify_device_token(auth[7:])
        if token is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked device token.")
        return {"kind": "device", "name": token["name"], "role": token["scope"]}
    user = AuthService(conn).user_for_session(request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return {"kind": "user", "name": user["username"], "role": user["role"]}


def api_admin(principal: Annotated[dict[str, str], Depends(api_principal)]) -> dict[str, str]:
    if principal["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    return principal


def api_reader(principal: Annotated[dict[str, str], Depends(api_principal)]) -> dict[str, str]:
    """Routes that return data: a signed-in user of either role, or an admin-scoped token."""
    if principal["role"] not in ("admin", "viewer"):
        raise HTTPException(
            status_code=403, detail="This device token may only check hashes and upload files."
        )
    return principal


def api_uploader(principal: Annotated[dict[str, str], Depends(api_principal)]) -> dict[str, str]:
    """The push path: hash check and upload. Any device token, or an admin user."""
    if principal["role"] not in ("admin", "pusher"):
        raise HTTPException(status_code=403, detail="Admin role or a device token required.")
    return principal


PageUser = Annotated[sqlite3.Row, Depends(page_user)]
PageAdmin = Annotated[sqlite3.Row, Depends(page_admin)]
ApiPrincipal = Annotated[dict[str, str], Depends(api_principal)]
ApiAdmin = Annotated[dict[str, str], Depends(api_admin)]
ApiReader = Annotated[dict[str, str], Depends(api_reader)]
ApiUploader = Annotated[dict[str, str], Depends(api_uploader)]
Templates = Annotated[Jinja2Templates, Depends(get_templates)]
AppSettings = Annotated[Settings, Depends(get_settings)]
