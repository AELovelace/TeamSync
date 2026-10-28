-- Users, browser sessions and device tokens for the push script.

CREATE TABLE app_users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE app_sessions (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX idx_app_sessions_expires ON app_sessions(expires_at);

CREATE TABLE device_tokens (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL,
  token_prefix TEXT NOT NULL UNIQUE,
  token_hash   TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  created_by   TEXT,
  last_used_at TEXT,
  revoked_at   TEXT
);
