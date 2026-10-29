# TeamSync: Box JE report ingestion

Phase 1 plan. Scope is deliberately narrow: pick up the NetSuite journal-entry files
that Box Drive syncs to `C:\Users\Edith\Box`, parse them, store them in SQLite,
and expose them through a small web app on an internal server. No NetSuite posting,
no email, no Box API yet.

Decisions made 2026-10-28:

- Fedora is the first deployment target. The Ubuntu bundle is written but tested second.
- Internal TLS certificate and local DNS for `teamsync.eternalhotels.com`.
- Accepted input formats: `.csv`, `.xls`, `.xlsx`, and `.ods` (LibreOffice Calc). `.xlsb` comes free with the same reader.
- Raw files are retained on the server indefinitely as a backup. No pruning.

## 1. What we are ingesting

Source today: `C:\Users\Edith\Box\Singh JE Reports\<batch folder>\*.CSV`

- One batch folder per payroll run, named by hand
  (`JE Reports 10.20.26, 10.21.26 and manual payroll batches`).
- One file per journal entry. Filename pattern:
  `<Property name> JE_<payrollId>_<batchId>.<ext>`. Today every file is `.CSV`;
  spreadsheets with the same 11 columns on the first sheet are treated identically.
- Identical 11-column header in every file (NetSuite JE import layout):
  `subsidiary, postingperiod, journalItemheader_memo, tranDate,
  journalItemLine_accountRef, journalItemLine_debitAmount,
  journalItemLine_creditAmount, journalItemLine_memo,
  journalItemLine_departmentRef, journalItemLine_classRef,
  journalItemLine_locationRef`
- Header memo is `<payrollId>.<batchId>.<runId>` and matches the filename.
- Every file balances to the cent. Credits sit on 279 / 1402 / 1405,
  debits on 767 / 773 / 852 / 761 / 769 / 772 / 766 / 750. 405 appears on either side.

Known dirt the parser has to survive, all present in the 25 sample files
(copied to `example-data/`, which is gitignored):

| Problem | Example | Handling |
| --- | --- | --- |
| Department column is free-form | numeric IDs, `Operations:Hotel Ops:...` paths, bare names, blank, `Housepeerson` typo | Store raw. Classify style per row. Normalize later via a mapping table. |
| Account given as text | `Payroll Clearing` in C&M Country Store | Store raw. Flag `account_is_numeric = 0`. |
| Reference columns all equal the subsidiary | Sleep Inn Pasco: account, memo, dept, class, location all `15` | Validation rule flags file as `suspect_column_shift`. Still stored. |
| Multi-subsidiary file | C&M spans subsidiaries 28 to 32 | One `journal_entry` row, lines carry their own subsidiary and location. |
| Supplemental small entries | second file per property with 4 to 5 lines | Nothing special. Same parser. |
| Filename typos | `Captial City Lodging` | Property name is derived from filename but kept raw. Alias table later. |

## 2. Stack

Python first, as requested. Uvicorn-served ASGI app.

| Concern | Choice | Why |
| --- | --- | --- |
| Runtime | Python 3.12+ (dev box has 3.14.3) | |
| Web | FastAPI on Uvicorn | Async, typed, auto OpenAPI docs, trivial to put behind nginx. |
| DB | SQLite via stdlib `sqlite3`, WAL mode | Zero deps, matches SynchroHRM. Migrations as numbered SQL files applied in order. |
| Templates | Jinja2 server-rendered pages plus a little vanilla JS | Same shape as SynchroHRM's admin panel, no build step. |
| CSV parsing | stdlib `csv` and `decimal.Decimal` | Money must never touch floats. |
| Spreadsheet reading | `python-calamine` | One Rust-backed reader for xls, xlsx, xlsb, and ods. Has a cp314 Windows wheel. Returns cell values, which we feed into the same row normalizer the CSV path uses. Fallback if a wheel is ever missing on the server: `openpyxl` + `xlrd` + `odfpy`, all pure Python. |
| Scheduling | `asyncio` background task on an interval, with a manual `POST /api/sync` trigger | node-cron equivalent without a dependency. |
| Config | `.env` via `pydantic-settings`, prefixed `TEAMSYNC_` | Same pattern as `SYNCHRO_*`. |
| Auth | Session cookie, scrypt password hashes, single admin seeded on first run | Lifted from SynchroHRM's AuthService design. |
| Packaging | `pyproject.toml`, `pip install -e .`, `uv` optional | |
| Deploy | systemd unit + nginx reverse proxy, Fedora and Ubuntu bundles | Copy SynchroHRM's `deploy/` layout, swap Node for a venv. |

Dependencies: `fastapi`, `uvicorn[standard]`, `jinja2`, `pydantic-settings`,
`python-multipart` (upload endpoint), `python-calamine`. Dev: `pytest`, `httpx`, `ruff`.

Spreadsheet handling rules:

- Only the first sheet is read. Other sheets are noted in a `extra_sheets_ignored` flag.
- Header row must match the 11 NetSuite columns after trimming and case-folding, or the file is stored with status `unrecognized_layout` and not parsed into lines.
- Numeric cells are converted via `Decimal(str(value))`, never through float arithmetic. Date cells become ISO dates. Text cells that look like dates (`10/15/2026`) go through the same date parser the CSV path uses.
- Workbook rows are turned into the same list-of-strings shape the CSV reader produces, so there is exactly one parser downstream.

## 3. How files get from Box to the server

Box Drive only syncs to Edith's workstation. The server cannot see that path.
Two options, pick one in phase 1 and leave the door open for the other:

**A. Push from the workstation (recommended for phase 1).**
A small Python script (`teamsync-push`, in this repo) runs on the Windows box on a
scheduled task. It walks the Box folder, SHA-256 hashes every file it finds
(desktop.ini and Box's own metadata files excluded), asks the server
`GET /api/files/known?hashes=...`, and uploads anything new via
`POST /api/upload` with a device token. The server never needs Box credentials.

**B. Server pulls from Box API.**
Box app with a JWT or client-credentials grant, list folder, download files.
Cleaner long term, but needs a Box developer app approved by the Box admin.
Deferred to phase 2. The ingestion code is written against an abstract
`FileSource` so this slots in without touching the parser or DB.

For local dev, a third source reads `example-data/` directly, same as
SynchroHRM's `ExampleDataAttachmentSource`.

## 4. Data model

```sql
-- what arrived
source_files (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  original_name TEXT NOT NULL,
  file_kind TEXT NOT NULL,               -- 'csv' | 'xls' | 'xlsx' | 'xlsb' | 'ods' | 'other'
  parse_status TEXT NOT NULL,            -- 'parsed' | 'unrecognized_layout' | 'unsupported' | 'failed'
  source_kind TEXT NOT NULL,            -- 'push' | 'example' | 'box_api'
  source_path TEXT,                      -- relative path inside Box, if known
  batch_folder TEXT,                     -- parent folder name, as-is
  size_bytes INTEGER NOT NULL,
  stored_path TEXT NOT NULL,             -- under TEAMSYNC_DATA_DIR/files/
  received_at TEXT NOT NULL,
  ingest_run_id INTEGER REFERENCES ingest_runs(id)
);

-- each parse attempt, so a parser fix can re-run over stored files
ingest_runs (
  id INTEGER PRIMARY KEY,
  trigger_source TEXT NOT NULL,          -- 'schedule' | 'manual' | 'upload' | 'reparse'
  status TEXT NOT NULL,                  -- 'running' | 'ok' | 'partial' | 'failed'
  started_at TEXT NOT NULL,
  finished_at TEXT,
  files_seen INTEGER, files_new INTEGER, files_failed INTEGER,
  error TEXT
);

-- one row per CSV = one NetSuite journal entry
journal_entries (
  id INTEGER PRIMARY KEY,
  source_file_id INTEGER NOT NULL UNIQUE REFERENCES source_files(id),
  property_name_raw TEXT NOT NULL,       -- from filename, before ' JE_'
  payroll_id TEXT NOT NULL,              -- 914339
  batch_id TEXT NOT NULL,                -- 202641
  run_id TEXT,                           -- 4010604, third memo segment
  header_memo TEXT NOT NULL,
  posting_period TEXT NOT NULL,          -- ISO date
  tran_date TEXT NOT NULL,               -- ISO date
  subsidiaries TEXT NOT NULL,            -- JSON array, usually one
  locations TEXT NOT NULL,               -- JSON array, usually one
  line_count INTEGER NOT NULL,
  total_debit TEXT NOT NULL,             -- Decimal as string
  total_credit TEXT NOT NULL,
  is_balanced INTEGER NOT NULL,
  dept_style TEXT NOT NULL,              -- 'numeric' | 'path' | 'name' | 'blank' | 'mixed'
  validation_flags TEXT NOT NULL,        -- JSON array of rule names that fired
  parser_version TEXT NOT NULL,
  parsed_at TEXT NOT NULL
  -- No UNIQUE(payroll_id, batch_id): a property can ship a supplemental second file
  -- for the same run. A second entry with the same pair gets the duplicate_batch flag.
);

journal_lines (
  id INTEGER PRIMARY KEY,
  journal_entry_id INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
  line_no INTEGER NOT NULL,
  subsidiary TEXT NOT NULL,
  account_ref TEXT NOT NULL,
  account_is_numeric INTEGER NOT NULL,
  debit TEXT,                            -- Decimal as string, NULL when blank
  credit TEXT,
  line_memo TEXT,
  department_ref TEXT,
  department_style TEXT,                 -- per line
  class_ref TEXT,
  location_ref TEXT
);

-- normalization, filled in by hand through the UI, not required to ingest
properties (
  id INTEGER PRIMARY KEY,
  canonical_name TEXT NOT NULL UNIQUE,
  netsuite_subsidiary TEXT,
  netsuite_location TEXT
);
property_aliases (alias TEXT PRIMARY KEY COLLATE NOCASE, property_id INTEGER NOT NULL);

app_users, app_sessions, device_tokens   -- as SynchroHRM, plus device_tokens.scope ('pusher' | 'admin')
```

Raw file bytes are kept on disk under `TEAMSYNC_DATA_DIR/files/<sha256[:2]>/<sha256>.<ext>`,
retained indefinitely as a backup of what Box held. Any parser change can be
replayed with `teamsync reparse`, and the admin UI exposes a per-file download.
The data dir should sit on a volume that is included in the server's normal backups.

## 5. Validation rules (phase 1)

Each rule is a small function that returns a flag name or nothing. Flags are
stored on the entry and shown in the UI. Nothing is rejected.

- `unbalanced`: total debit != total credit.
- `memo_filename_mismatch`: header memo does not start with `<payrollId>.<batchId>`.
- `multiple_header_memos`: more than one distinct header memo in a file.
- `suspect_column_shift`: account, department, class, and location all equal the subsidiary on every row (the Sleep Inn Pasco case).
- `text_account_ref`: any account ref that is not all digits.
- `mixed_dept_style`: more than one department style in one file.
- `multi_subsidiary`: more than one subsidiary in one file.
- `class_populated`: class column is non-empty anywhere (it is always blank today, so this is worth knowing).

## 6. Web surface

Server-rendered, behind login.

- `/` dashboard: last sync, counts, entries with flags.
- `/entries` list with filters (property, batch folder, date range, flag).
- `/entries/{id}` header, lines table, raw CSV download, flags with explanations.
- `/properties` alias management.
- `/admin` users, device tokens, run history, manual sync button, reparse button.
- `/health` unauthenticated, for nginx and monitoring.

JSON API for the push script and future consumers:

- `POST /api/files/known` with `{"hashes": [...]}` returns which hashes already exist.
  (`GET ?hashes=a,b,c` exists for a handful; nginx's 8 KB request line caps it at ~120.)
- `POST /api/upload` multipart, device-token auth, returns `source_file_id` and parse result.
  Both accept a `pusher`-scoped token; everything else needs `admin` or a signed-in user.
- `POST /api/sync` manual trigger.
- `GET /api/entries`, `GET /api/entries/{id}` JSON mirrors of the pages.
- `GET /api/export/entries.csv` flattened lines, for whoever wants Excel.

## 7. Repo layout

```
TeamSync/
  pyproject.toml
  .env.example
  PLAN.md
  README.md
  teamsync/
    __init__.py
    config.py            # pydantic-settings, TEAMSYNC_* vars
    main.py              # FastAPI app factory, lifespan starts the scheduler
    db/
      connection.py      # sqlite3 with WAL, row factory, one connection per request
      migrations/        # 0001_init.sql, 0002_...
      repo.py            # plain SQL functions, no ORM
    parsers/
      readers.py         # bytes + ext -> list[list[str]] rows (csv module or calamine)
      je_rows.py         # rows -> ParsedJournalEntry (pure, no IO)
      rules.py           # validation rules
    sources/
      base.py            # FileSource protocol
      example_dir.py
      upload.py
    services/
      ingestion.py       # orchestrates source -> store -> parse -> flags
      auth.py
    web/
      routes_pages.py
      routes_api.py
      templates/
      static/
  tools/
    push_from_box.py     # the workstation side of option A
  tests/
    fixtures/            # sanitized samples: clean csv, pasco shift, c&m multi-sub, text account,
                         # plus the clean sample re-saved as xlsx, xls, and ods
    test_readers.py
    test_je_rows.py
    test_rules.py
    test_api.py
  deploy/
    fedora/              # tested first: install-server.sh, teamsync.service, nginx.conf,
                         # env.production.example, DEPLOYMENT.md
    ubuntu/              # same files, tested second
```

## 8. Milestones

Progress 2026-10-28: milestones 1 through 6 are built. 110 tests pass. Smoke
tested locally: `teamsync serve`, login, token creation, then
`tools/push_from_box.py` against the real Box folder uploaded all 25 files,
a second run uploaded nothing, dashboard and export reflected the data.
Server installed and push task registered 2026-10-28.

Progress 2026-10-29 (0.2.0): code audit, five fixes. Hash check moved to POST
(the GET form would hit nginx's 414 past ~120 files). New device tokens render in
the POST response instead of a redirect URL. Device tokens carry a `pusher` or
`admin` scope. The installers leave `/opt/teamsync` root-owned and read-only to
the service account. The push task runs under S4U logon. 112 tests.

1. **Parser and tests.** `readers.py`, `je_rows.py`, and `rules.py` against the 25 samples plus xlsx/xls/ods re-saves of one of them. Every file parses, every flag listed above fires on the right file, and all four formats produce byte-identical parsed output. No web yet. This locks the data model.
2. **SQLite layer and CLI.** Migrations, repo functions, `teamsync ingest-dir example-data/` and `teamsync reparse`. Verify round trip.
3. **FastAPI read-only UI.** Login, dashboard, entries list and detail, health. Run locally on Uvicorn.
4. **Upload API and push script.** Device tokens, `/api/upload`, `tools/push_from_box.py` on a Windows scheduled task pointing at the Box folder.
5. **Deploy bundle.** systemd unit, nginx with a self-signed cert for `teamsync.eternalhotels.com` (Let's Encrypt swap documented), install script for Fedora mirroring SynchroHRM. Stand it up on the internal server. Ubuntu bundle written alongside, tested later.
6. **Admin and normalization.** Property aliases, user management, run history, manual sync and reparse buttons.

Phase 2 candidates, not in scope now: Box API pull, department mapping to a
canonical NetSuite department list, NetSuite JE posting, email digest on flags.

## 9. Decisions log

Resolved 2026-10-28:

- **Server OS:** Fedora first. Ubuntu bundle maintained but tested second.
- **Hostname:** `teamsync.eternalhotels.com`, internal TLS cert, local DNS. Same approach as SynchroHRM's `synchro.eternalhotels.com`.
- **Formats:** csv, xls, xlsx, ods. xlsb accepted because the reader supports it for free.
- **Retention:** raw files kept on the server indefinitely as backup.

- **Unsupported files:** the push script uploads every file in the Box folder regardless of extension. Spreadsheet formats are parsed; anything else is stored with `parse_status = 'unsupported'` and `file_kind = 'other'` so the UI shows what else lands in the folder. Raw bytes are retained like everything else.
- **TLS:** self-signed cert for now, generated by the install script if none exists, same as SynchroHRM. The usual practice is a public subdomain with a Let's Encrypt cert, so the nginx config keeps cert and key paths as two variables and `DEPLOYMENT.md` documents the swap to certbot-managed paths. Nothing else in the bundle changes when that happens.

Resolved 2026-10-29:

- **Token scope:** device tokens default to `pusher` (hash check and upload only). The
  workstation never needs more. `admin` tokens exist for scripts that read or reparse.
- **Token display:** shown once in the body of the create response, `Cache-Control: no-store`,
  never in a URL, so it stays out of browser history and nginx's access log.
- **Code ownership on the server:** root owns the checkout and venv, the service account only
  reads them, and the unit mounts the app dir read-only. SynchroHRM's chown-to-service-account
  habit is not carried over.
- **Push task logon:** S4U, so pushes continue while signed out. Box Drive only syncs while
  signed in, so this just picks up anything that landed before sign-out.
- **Phase 2:** still parked. Nothing pulled forward.
