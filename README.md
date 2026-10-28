# TeamSync

Ingests NetSuite journal-entry exports (payroll JE files) that Box Drive syncs to
a workstation, stores the raw files and parsed lines in SQLite, and will serve
them from a small FastAPI/Uvicorn web app on an internal server.

See [PLAN.md](PLAN.md) for scope, data model, decisions and milestones.

## Status

| Milestone | State |
| --- | --- |
| 1. Parser, rules, tests (csv, xls, xlsx, ods) | done |
| 2. SQLite layer and CLI | done |
| 3. FastAPI UI with login | done |
| 4. Upload API and Box push script | done |
| 5. Fedora deploy bundle (Ubuntu alongside), one-line installer | written, not yet run on a server |
| 6. Admin, users, tokens, property aliases | done |

## Running the web app locally

```powershell
.venv\Scripts\teamsync --data-dir storage ingest-dir example-data
$env:TEAMSYNC_DATA_DIR = "storage"; $env:TEAMSYNC_ADMIN_PASSWORD = "local-dev-password"
.venv\Scripts\teamsync serve      # http://127.0.0.1:8000, sign in as admin
```

Without `TEAMSYNC_ADMIN_PASSWORD` the first start generates one and writes it to
`<data-dir>/initial-admin-password.txt`. Pages: dashboard, entries with filters and
CSV export, entry detail with lines and flags, files with raw download, properties
(alias raw filename names to canonical properties), admin (users, device tokens, run
history, upload, sync, reparse). JSON API under `/api/`, docs at `/api/docs`.

## Server install

```bash
curl -fsSL https://raw.githubusercontent.com/AELovelace/TeamSync/main/deploy/install.sh | sudo bash
```

Detects Fedora/RHEL or Ubuntu/Debian, clones the repo to `/opt/teamsync`, sets up the
service account, venv, systemd unit, nginx with a self-signed cert and the firewall, then
starts the service. Details and options in [deploy/fedora/DEPLOYMENT.md](deploy/fedora/DEPLOYMENT.md).

## Workstation push

`tools/push_from_box.py` hashes everything under the Box folder, asks the server
which hashes are new, and uploads only those. `tools/Register-PushTask.ps1` wraps
it in a Windows scheduled task. Create the device token in Admin or with
`teamsync create-token <name>`. See [deploy/fedora/DEPLOYMENT.md](deploy/fedora/DEPLOYMENT.md).

## Development

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python tools\generate_fixtures.py   # only if tests/fixtures is missing
.venv\Scripts\python -m pytest -q
.venv\Scripts\ruff check teamsync tests tools
```

## CLI

Settings come from `TEAMSYNC_*` environment variables or a `.env` file.
`--data-dir` overrides `TEAMSYNC_DATA_DIR` (default `./storage`).

```powershell
.venv\Scripts\teamsync init-db
.venv\Scripts\teamsync ingest-dir example-data
.venv\Scripts\teamsync stats
.venv\Scripts\teamsync list --flag suspect_column_shift
.venv\Scripts\teamsync show 12
.venv\Scripts\teamsync reparse
```

`ingest-dir` walks a folder recursively, skips Box and Windows housekeeping
files, deduplicates by SHA-256, keeps the raw bytes under `<data-dir>/files/`,
parses anything with a spreadsheet extension, and records everything else as
`unsupported`. `reparse` re-runs the current parser over every stored file and
rebuilds the entries, which is how a parser fix gets applied to history.

## Layout

```
teamsync/
  config.py          TEAMSYNC_* settings
  cli.py             teamsync command
  parsers/           readers (csv, calamine) -> je_rows -> rules
  db/                sqlite connection, numbered SQL migrations, repo functions
  sources/           FileSource protocol, LocalDirectorySource
  services/          IngestionService (store, parse, flag, reparse)
tests/               pytest suite; tests/fixtures are synthetic
tools/               generate_fixtures.py
example-data/        real sample exports, gitignored; tests use them when present
```
