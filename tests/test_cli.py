from __future__ import annotations

import json
from pathlib import Path

import pytest

from teamsync.cli import main


def test_cli_end_to_end(tmp_path: Path, fixtures: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = str(tmp_path / "data")

    assert main(["--data-dir", data_dir, "init-db"]) == 0
    assert (tmp_path / "data" / "teamsync.sqlite3").is_file()

    assert main(["--data-dir", data_dir, "ingest-dir", str(fixtures)]) == 0
    out = capsys.readouterr().out
    assert "seen=11 new=11 duplicate=0 parsed=9 failed=0" in out

    assert main(["--data-dir", data_dir, "stats"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["entries"] == 9

    assert main(["--data-dir", data_dir, "list", "--flag", "suspect_column_shift"]) == 0
    out = capsys.readouterr().out
    assert "Shifted Inn" in out and out.strip().endswith("1 entries")

    assert main(["--data-dir", data_dir, "show", "1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entry"]["id"] == 1 and len(payload["lines"]) == payload["entry"]["line_count"]

    assert main(["--data-dir", data_dir, "reparse"]) == 0
    assert "reparsed=11 parsed=9 failed=0" in capsys.readouterr().out


def test_cli_ingest_missing_dir(tmp_path: Path) -> None:
    assert main(["--data-dir", str(tmp_path), "ingest-dir", str(tmp_path / "nope")]) == 2


def test_cli_user_and_token_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = str(tmp_path / "data")
    assert (
        main(
            ["--data-dir", data_dir, "create-user", "ops", "--role", "admin", "--password", "ops-password-1"]
        )
        == 0
    )
    assert main(["--data-dir", data_dir, "create-user", "ops", "--password", "ops-password-1"]) == 1
    assert main(["--data-dir", data_dir, "set-password", "ops", "--password", "ops-password-2"]) == 0
    assert main(["--data-dir", data_dir, "set-password", "nobody", "--password", "ops-password-2"]) == 1
    capsys.readouterr()
    assert main(["--data-dir", data_dir, "create-token", "box-workstation"]) == 0
    assert capsys.readouterr().out.strip().startswith("tsk_")
    assert main(["--data-dir", data_dir, "create-token", "ops", "--scope", "admin"]) == 0
    assert capsys.readouterr().out.strip().startswith("tsk_")
    with pytest.raises(SystemExit):
        main(["--data-dir", data_dir, "create-token", "x", "--scope", "root"])
