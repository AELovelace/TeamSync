-- TeamSync initial schema. Money is stored as TEXT holding a Decimal with two places.
-- Dates are ISO-8601 TEXT. JSON columns hold arrays of strings.

CREATE TABLE ingest_runs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger_source  TEXT NOT NULL,                 -- schedule | manual | upload | reparse | cli
  status          TEXT NOT NULL,                 -- running | ok | partial | failed
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  files_seen      INTEGER NOT NULL DEFAULT 0,
  files_new       INTEGER NOT NULL DEFAULT 0,
  files_duplicate INTEGER NOT NULL DEFAULT 0,
  files_failed    INTEGER NOT NULL DEFAULT 0,
  error           TEXT
);

CREATE TABLE source_files (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256          TEXT NOT NULL UNIQUE,
  original_name   TEXT NOT NULL,
  file_kind       TEXT NOT NULL,                 -- csv | xls | xlsx | xlsb | ods | other
  parse_status    TEXT NOT NULL,                 -- parsed | unrecognized_layout | unsupported | failed
  parse_error     TEXT,
  source_kind     TEXT NOT NULL,                 -- push | local | upload | box_api
  source_path     TEXT,                          -- relative path at the source, if known
  batch_folder    TEXT,                          -- immediate parent folder name at the source
  size_bytes      INTEGER NOT NULL,
  stored_path     TEXT NOT NULL,                 -- relative to the files dir
  received_at     TEXT NOT NULL,
  ingest_run_id   INTEGER REFERENCES ingest_runs(id)
);
CREATE INDEX idx_source_files_received ON source_files(received_at);
CREATE INDEX idx_source_files_status ON source_files(parse_status);

CREATE TABLE journal_entries (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file_id    INTEGER NOT NULL UNIQUE REFERENCES source_files(id) ON DELETE CASCADE,
  property_name_raw TEXT NOT NULL,
  payroll_id        TEXT NOT NULL,
  batch_id          TEXT NOT NULL,
  run_id            TEXT,
  header_memo       TEXT NOT NULL,
  posting_period    TEXT NOT NULL,
  tran_date         TEXT NOT NULL,
  subsidiaries      TEXT NOT NULL,               -- JSON array
  locations         TEXT NOT NULL,               -- JSON array
  line_count        INTEGER NOT NULL,
  total_debit       TEXT NOT NULL,
  total_credit      TEXT NOT NULL,
  is_balanced       INTEGER NOT NULL,
  dept_style        TEXT NOT NULL,
  validation_flags  TEXT NOT NULL,               -- JSON array
  parser_version    TEXT NOT NULL,
  parsed_at         TEXT NOT NULL
);
CREATE INDEX idx_journal_entries_batch ON journal_entries(payroll_id, batch_id);
CREATE INDEX idx_journal_entries_property ON journal_entries(property_name_raw);
CREATE INDEX idx_journal_entries_tran_date ON journal_entries(tran_date);

CREATE TABLE journal_lines (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  journal_entry_id   INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
  line_no            INTEGER NOT NULL,
  subsidiary         TEXT NOT NULL,
  account_ref        TEXT NOT NULL,
  account_is_numeric INTEGER NOT NULL,
  debit              TEXT,
  credit             TEXT,
  line_memo          TEXT,
  department_ref     TEXT,
  department_style   TEXT,
  class_ref          TEXT,
  location_ref       TEXT,
  UNIQUE(journal_entry_id, line_no)
);
CREATE INDEX idx_journal_lines_account ON journal_lines(account_ref);

CREATE TABLE properties (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_name      TEXT NOT NULL UNIQUE,
  netsuite_subsidiary TEXT,
  netsuite_location   TEXT,
  created_at          TEXT NOT NULL
);

CREATE TABLE property_aliases (
  alias        TEXT PRIMARY KEY COLLATE NOCASE,
  property_id  INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE
);

CREATE TABLE state (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at TEXT NOT NULL
);
