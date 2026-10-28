"""Users, sessions and device tokens.

Passwords: scrypt with a per-user salt, stored as ``scrypt$N$r$p$salt$hash``.
Sessions: random 256-bit tokens in a cookie, 7 day lifetime.
Device tokens: ``tsk_<prefix>_<secret>``. The prefix is stored in clear for
lookup, the whole token is stored as a SHA-256 hash. The secret has 256 bits of
entropy so a plain hash is sufficient and keeps every API call cheap.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import sqlite3

from teamsync.db.repo import now_iso

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SESSION_TTL = dt.timedelta(days=7)
PASSWORD_MIN_LENGTH = 10
ROLES = ("admin", "viewer")
TOKEN_PREFIX_TAG = "tsk"


class AuthError(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(_unb64(digest))
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _unb64(digest))


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise AuthError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- users -------------------------------------------------------------

    def ensure_admin(self, password: str | None = None) -> str | None:
        """Create the first admin if no users exist. Returns the generated
        password when one had to be invented, else None."""
        if self.conn.execute("SELECT COUNT(*) FROM app_users").fetchone()[0]:
            return None
        generated = None
        if not password:
            password = generated = secrets.token_urlsafe(14)
        self.create_user("admin", password, "admin", validate=False)
        return generated

    def create_user(self, username: str, password: str, role: str, *, validate: bool = True) -> int:
        username = username.strip()
        if not username:
            raise AuthError("Username is required.")
        if role not in ROLES:
            raise AuthError(f"Role must be one of {', '.join(ROLES)}.")
        if validate:
            validate_password(password)
        if self.get_user_by_name(username):
            raise AuthError("That username already exists.")
        now = now_iso()
        cur = self.conn.execute(
            "INSERT INTO app_users (username, password_hash, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (username, hash_password(password), role, now, now),
        )
        return int(cur.lastrowid)

    def set_password(self, user_id: int, password: str) -> None:
        validate_password(password)
        self.conn.execute(
            "UPDATE app_users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(password), now_iso(), user_id),
        )
        self.conn.execute("DELETE FROM app_sessions WHERE user_id = ?", (user_id,))

    def set_role(self, user_id: int, role: str) -> None:
        if role not in ROLES:
            raise AuthError("Unknown role.")
        self.conn.execute(
            "UPDATE app_users SET role = ?, updated_at = ? WHERE id = ?", (role, now_iso(), user_id)
        )

    def delete_user(self, user_id: int) -> None:
        admins = self.conn.execute("SELECT COUNT(*) FROM app_users WHERE role = 'admin'").fetchone()[0]
        target = self.get_user(user_id)
        if target is None:
            return
        if target["role"] == "admin" and admins <= 1:
            raise AuthError("Cannot delete the last admin.")
        self.conn.execute("DELETE FROM app_users WHERE id = ?", (user_id,))

    def get_user(self, user_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT id, username, role, created_at, updated_at FROM app_users WHERE id = ?", (user_id,)
        ).fetchone()

    def get_user_by_name(self, username: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM app_users WHERE username = ?", (username.strip(),)).fetchone()

    def list_users(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT id, username, role, created_at, updated_at FROM app_users ORDER BY username"
            )
        )

    # --- sessions ----------------------------------------------------------

    def login(self, username: str, password: str) -> tuple[str, sqlite3.Row]:
        user = self.get_user_by_name(username or "")
        if user is None or not verify_password(password or "", user["password_hash"]):
            raise AuthError("Invalid username or password.")
        token = secrets.token_urlsafe(32)
        now = dt.datetime.now(dt.UTC)
        self.conn.execute(
            "INSERT INTO app_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (
                token,
                user["id"],
                now_iso(),
                (now + SESSION_TTL).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            ),
        )
        self.purge_expired_sessions()
        return token, user

    def logout(self, token: str | None) -> None:
        if token:
            self.conn.execute("DELETE FROM app_sessions WHERE token = ?", (token,))

    def user_for_session(self, token: str | None) -> sqlite3.Row | None:
        if not token:
            return None
        return self.conn.execute(
            """
            SELECT u.id, u.username, u.role FROM app_sessions s JOIN app_users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at > ?
            """,
            (token, now_iso()),
        ).fetchone()

    def purge_expired_sessions(self) -> None:
        self.conn.execute("DELETE FROM app_sessions WHERE expires_at <= ?", (now_iso(),))

    # --- device tokens -----------------------------------------------------

    def create_device_token(self, name: str, created_by: str | None) -> tuple[int, str]:
        name = name.strip()
        if not name:
            raise AuthError("Token name is required.")
        prefix = secrets.token_hex(4)
        plain = f"{TOKEN_PREFIX_TAG}_{prefix}_{secrets.token_urlsafe(32)}"
        cur = self.conn.execute(
            "INSERT INTO device_tokens (name, token_prefix, token_hash, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
            (name, prefix, _hash_token(plain), now_iso(), created_by),
        )
        return int(cur.lastrowid), plain

    def verify_device_token(self, plain: str | None) -> sqlite3.Row | None:
        if not plain:
            return None
        parts = plain.strip().split("_", 2)
        if len(parts) != 3 or parts[0] != TOKEN_PREFIX_TAG or not parts[1] or not parts[2]:
            return None
        row = self.conn.execute(
            "SELECT * FROM device_tokens WHERE token_prefix = ? AND revoked_at IS NULL", (parts[1],)
        ).fetchone()
        if row is None or not hmac.compare_digest(row["token_hash"], _hash_token(plain.strip())):
            return None
        self.conn.execute("UPDATE device_tokens SET last_used_at = ? WHERE id = ?", (now_iso(), row["id"]))
        return row

    def revoke_device_token(self, token_id: int) -> None:
        self.conn.execute(
            "UPDATE device_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (now_iso(), token_id),
        )

    def list_device_tokens(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM device_tokens ORDER BY revoked_at IS NOT NULL, id DESC"))
