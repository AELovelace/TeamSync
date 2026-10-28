from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from teamsync.db import open_database
from teamsync.services import AuthError, AuthService
from teamsync.services.auth import hash_password, verify_password


@pytest.fixture
def auth(tmp_path: Path) -> AuthService:
    conn: sqlite3.Connection = open_database(tmp_path / "a.sqlite3")
    yield AuthService(conn)
    conn.close()


def test_password_hashing_round_trip() -> None:
    stored = hash_password("correct horse battery")
    assert stored.startswith("scrypt$16384$8$1$")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "garbage")
    assert hash_password("same") != hash_password("same")  # salted


def test_ensure_admin_generates_once(auth: AuthService) -> None:
    generated = auth.ensure_admin(None)
    assert generated and len(generated) >= 16
    assert auth.ensure_admin(None) is None
    token, user = auth.login("admin", generated)
    assert user["role"] == "admin"
    assert auth.user_for_session(token)["username"] == "admin"


def test_ensure_admin_uses_configured_password(auth: AuthService) -> None:
    assert auth.ensure_admin("configured-pass-1") is None
    auth.login("ADMIN", "configured-pass-1")  # case-insensitive username


def test_login_failures(auth: AuthService) -> None:
    auth.ensure_admin("configured-pass-1")
    with pytest.raises(AuthError):
        auth.login("admin", "nope")
    with pytest.raises(AuthError):
        auth.login("ghost", "configured-pass-1")
    assert auth.user_for_session("not-a-token") is None
    assert auth.user_for_session(None) is None


def test_user_management(auth: AuthService) -> None:
    auth.ensure_admin("configured-pass-1")
    with pytest.raises(AuthError, match="at least"):
        auth.create_user("bob", "short", "viewer")
    with pytest.raises(AuthError, match="Role"):
        auth.create_user("bob", "long-enough-pw", "owner")
    uid = auth.create_user("bob", "long-enough-pw", "viewer")
    with pytest.raises(AuthError, match="exists"):
        auth.create_user("Bob", "long-enough-pw", "viewer")
    token, _ = auth.login("bob", "long-enough-pw")
    auth.set_password(uid, "another-long-pw")
    assert auth.user_for_session(token) is None  # sessions dropped on password change
    auth.login("bob", "another-long-pw")
    admin_id = auth.get_user_by_name("admin")["id"]
    with pytest.raises(AuthError, match="last admin"):
        auth.delete_user(admin_id)
    auth.delete_user(uid)
    assert [u["username"] for u in auth.list_users()] == ["admin"]


def test_logout(auth: AuthService) -> None:
    auth.ensure_admin("configured-pass-1")
    token, _ = auth.login("admin", "configured-pass-1")
    auth.logout(token)
    assert auth.user_for_session(token) is None


def test_device_tokens(auth: AuthService) -> None:
    tid, plain = auth.create_device_token("workstation", "admin")
    assert plain.startswith("tsk_") and len(plain.split("_", 2)[1]) == 8
    row = auth.verify_device_token(plain)
    assert row is not None and row["name"] == "workstation"
    assert auth.list_device_tokens()[0]["last_used_at"]
    assert auth.verify_device_token(plain[:-1] + "x") is None
    assert auth.verify_device_token("tsk_deadbeef_whatever") is None
    assert auth.verify_device_token("nonsense") is None
    auth.revoke_device_token(tid)
    assert auth.verify_device_token(plain) is None
    assert auth.list_device_tokens()[0]["revoked_at"]
    with pytest.raises(AuthError):
        auth.create_device_token("   ", None)
