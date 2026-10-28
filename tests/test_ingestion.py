from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from teamsync.db import open_database, repo
from teamsync.services import IngestionService
from teamsync.sources import IncomingFile, LocalDirectorySource
from teamsync.sources.local_dir import should_skip


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = open_database(tmp_path / "db" / "t.sqlite3")
    yield c
    c.close()


@pytest.fixture
def service(conn: sqlite3.Connection, tmp_path: Path) -> IngestionService:
    return IngestionService(conn, tmp_path / "files")


def test_migrations_apply_once(tmp_path: Path) -> None:
    path = tmp_path / "m.sqlite3"
    c1 = open_database(path)
    names = {r["name"] for r in c1.execute("SELECT name FROM schema_migrations")}
    assert "0001_init.sql" in names
    c1.close()
    c2 = open_database(path)  # second open must not re-apply
    assert c2.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(names)
    assert c2.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    c2.close()


@pytest.mark.parametrize(
    ("name", "skip"),
    [
        ("desktop.ini", True),
        ("Thumbs.db", True),
        (".hidden", True),
        ("~$lock.xlsx", True),
        ("x.boxnote", True),
        ("Sample JE_1_2.CSV", False),
        ("report.pdf", False),
    ],
)
def test_should_skip(name: str, skip: bool) -> None:
    assert should_skip(Path(name)) is skip


def test_ingest_fixtures_directory(service: IngestionService, fixtures: Path) -> None:
    summary = service.ingest(LocalDirectorySource(fixtures).iter_files(), "cli")
    by_name = {o.name: o for o in summary.outcomes}

    assert summary.seen == 11
    assert summary.new == 11
    assert summary.duplicate == 0
    assert summary.parsed == 9
    assert by_name["notes.txt"].parse_status == "unsupported"
    assert by_name["random-report.csv"].parse_status == "unrecognized_layout"
    assert by_name["Shifted Inn JE_100002_200002.CSV"].flags[:1] == ["suspect_column_shift"]

    # the four re-saves of the clean sample share payroll/batch with the CSV
    dupes = [o for o in summary.outcomes if "duplicate_batch" in o.flags]
    assert len(dupes) == 4
    assert all(o.name.startswith("Sample Hotel") for o in dupes)

    run = repo.recent_runs(service.conn, 1)[0]
    assert run["status"] == "ok"
    assert (run["files_seen"], run["files_new"], run["files_failed"]) == (11, 11, 0)

    s = repo.summary(service.conn)
    assert s["files"] == 11 and s["entries"] == 9
    assert s["files_unsupported"] == 1 and s["files_failed"] == 1
    assert s["flag_counts"]["duplicate_batch"] == 4

    # raw bytes kept on disk under hash sharding, for every file including unsupported
    for row in repo.all_source_files(service.conn):
        stored = service.files_dir / row["stored_path"]
        assert stored.is_file(), row["original_name"]
        assert stored.parent.name == row["sha256"][:2]
        assert row["batch_folder"] is None  # fixtures sit at the root of the walked dir


def test_reingest_is_all_duplicates(service: IngestionService, fixtures: Path) -> None:
    service.ingest(LocalDirectorySource(fixtures).iter_files(), "cli")
    again = service.ingest(LocalDirectorySource(fixtures).iter_files(), "cli")
    assert again.seen == 11 and again.duplicate == 11 and again.new == 0
    assert repo.summary(service.conn)["entries"] == 9


def test_reparse_rebuilds_entries(service: IngestionService, fixtures: Path) -> None:
    first = service.ingest(LocalDirectorySource(fixtures).iter_files(), "cli")
    before_ids = {o.source_file_id: o.entry_id for o in first.outcomes if o.entry_id}
    summary = service.reparse_all()
    assert summary.seen == 11 and summary.parsed == 9 and summary.failed == 0
    after = repo.summary(service.conn)
    assert after["entries"] == 9 and after["lines"] == 10 * 5 + 4 + 6 + 5 + 2
    # entries were deleted and recreated, so ids moved on and lines cascaded
    for sfid, old_id in before_ids.items():
        row = service.conn.execute(
            "SELECT id FROM journal_entries WHERE source_file_id = ?", (sfid,)
        ).fetchone()
        assert row["id"] != old_id
    assert (
        service.conn.execute(
            "SELECT COUNT(*) FROM journal_lines WHERE journal_entry_id NOT IN (SELECT id FROM journal_entries)"
        ).fetchone()[0]
        == 0
    )


def test_batch_folder_and_source_path_recorded(
    service: IngestionService, fixtures: Path, tmp_path: Path
) -> None:
    root = tmp_path / "box"
    batch = root / "JE Reports 10.20.26"
    batch.mkdir(parents=True)
    src = fixtures / "Sample Hotel JE_100001_200001.CSV"
    (batch / src.name).write_bytes(src.read_bytes())
    (batch / "desktop.ini").write_text("[.ShellClassInfo]")
    summary = service.ingest(LocalDirectorySource(root, source_kind="push").iter_files(), "schedule")
    assert summary.seen == 1
    row = repo.all_source_files(service.conn)[0]
    assert row["batch_folder"] == "JE Reports 10.20.26"
    assert row["source_path"] == "JE Reports 10.20.26/Sample Hotel JE_100001_200001.CSV"
    assert row["source_kind"] == "push"


def test_stored_entry_round_trip(service: IngestionService, fixtures: Path) -> None:
    src = fixtures / "Multi Store JE_100003_200003.CSV"
    summary = service.ingest(
        [IncomingFile(name=src.name, data=src.read_bytes(), source_kind="upload")], "upload"
    )
    entry_id = summary.outcomes[0].entry_id
    entry = repo.get_entry(service.conn, entry_id)
    assert entry["property_name_raw"] == "Multi Store"
    assert json.loads(entry["subsidiaries"]) == ["51", "52"]
    assert json.loads(entry["validation_flags"]) == ["text_account_ref", "multi_subsidiary", "multi_location"]
    assert entry["total_debit"] == "4200.00" and entry["is_balanced"] == 1
    lines = repo.get_lines(service.conn, entry_id)
    assert len(lines) == 6
    assert lines[0]["account_ref"] == "Payroll Clearing" and lines[0]["account_is_numeric"] == 0
    assert lines[0]["credit"] == "3000.00" and lines[0]["debit"] is None


def test_list_entries_filters(service: IngestionService, fixtures: Path) -> None:
    service.ingest(LocalDirectorySource(fixtures).iter_files(), "cli")
    flagged = repo.list_entries(service.conn, repo.EntryFilter(flag="unbalanced"))
    assert [r["property_name_raw"] for r in flagged] == ["Mixed Lodge"]
    by_prop = repo.list_entries(service.conn, repo.EntryFilter(property_name="sample hotel"))
    assert len(by_prop) == 5
    assert len(repo.list_entries(service.conn, repo.EntryFilter(tran_date_from="2027-01-01"))) == 0


def test_known_hashes(service: IngestionService, fixtures: Path) -> None:
    service.ingest(LocalDirectorySource(fixtures).iter_files(), "cli")
    hashes = [r["sha256"] for r in repo.all_source_files(service.conn)]
    assert repo.known_hashes(service.conn, hashes + ["0" * 64]) == set(hashes)


def test_corrupt_workbook_is_recorded_not_raised(service: IngestionService) -> None:
    summary = service.ingest(
        [IncomingFile(name="broken JE_1_2.xlsx", data=b"not a zip", source_kind="upload")], "upload"
    )
    o = summary.outcomes[0]
    assert o.status == "new" and o.parse_status == "failed"
    assert o.error
    assert repo.recent_runs(service.conn, 1)[0]["status"] == "partial"
