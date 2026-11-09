from __future__ import annotations

from pathlib import Path

import pytest

from teamsync.cli import main
from teamsync.db import open_database, repo
from teamsync.services import AccountMapError, import_account_map

HEADER = (
    "financial_id,financial_number,financial_name,financial_type,financial_inactive,"
    "stat_number,stat_id,stat_name,stat_inactive,status"
)
MAPPED = '"405","6004","6004 Employee Benefits","Expense","F","906004","1293","906004 (Stat) Benefits","F","mapped"'
MISSING = '"279","1328","1328, Payroll Clearing","OthCurrAsset","F","901328","","","","stat_account_missing"'


def write(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "lookup.csv"
    path.write_text("\n".join((HEADER, *lines)) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def conn(tmp_path: Path):
    c = open_database(tmp_path / "t.sqlite3")
    yield c
    c.close()


def test_import_stores_mapped_and_missing(conn, tmp_path: Path) -> None:
    rows = import_account_map(conn, write(tmp_path, MAPPED, MISSING))
    assert len(rows) == 2
    stored = {r["financial_internal_id"]: r for r in repo.list_account_map(conn)}
    assert stored["405"]["stat_internal_id"] == "1293"
    assert stored["405"]["source"] == "lookup.csv"
    assert stored["279"]["stat_internal_id"] is None
    assert stored["279"]["financial_name"] == "1328, Payroll Clearing"


def test_reimport_fills_in_a_new_stat_account(conn, tmp_path: Path) -> None:
    import_account_map(conn, write(tmp_path, MISSING))
    created = '"279","1328","1328 Payroll Clearing","OthCurrAsset","F","901328","2001","901328 (Stat)","F","mapped"'
    import_account_map(conn, write(tmp_path, created))
    rows = repo.list_account_map(conn)
    assert len(rows) == 1 and rows[0]["stat_internal_id"] == "2001"


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (MAPPED.replace('"906004"', '"906005"'), "should be 906004"),
        (MAPPED.replace('"6004"', '"123456"'), "should be 1023456"),
        (MAPPED.replace('"1293"', '""'), "stat_id"),
        (MISSING.replace('"901328",""', '"901328","1189"'), "stat_account_missing"),
        (MAPPED.replace('"1293"', '"405"'), "equals financial_id"),
        (MAPPED.replace('"mapped"', '"duplicate_stat_number"'), "duplicate_stat_number"),
        (MAPPED.replace('"mapped"', '"financial_id_not_found"'), "financial_id_not_found"),
        (MAPPED.replace('"F","mapped"', '"T","mapped"'), "not active"),
        (MAPPED.replace('"405"', '"abc"'), "not numeric"),
        ('"405","6004"', "wrong number of columns"),
    ],
)
def test_one_bad_row_rejects_the_file(conn, tmp_path: Path, line: str, message: str) -> None:
    import_account_map(conn, write(tmp_path, MISSING))
    before = [dict(r) for r in repo.list_account_map(conn)]
    other = '"750","6121","6121 Gratuities","Expense","F","906121","1368","x","F","mapped"'
    with pytest.raises(AccountMapError, match=message):
        import_account_map(conn, write(tmp_path, other, line))
    assert [dict(r) for r in repo.list_account_map(conn)] == before


def test_duplicate_financial_id_rejected(conn, tmp_path: Path) -> None:
    with pytest.raises(AccountMapError, match="duplicate financial_id 405"):
        import_account_map(conn, write(tmp_path, MAPPED, MAPPED))


def test_stat_id_claimed_by_another_account_rolls_back(conn, tmp_path: Path) -> None:
    import_account_map(conn, write(tmp_path, MAPPED))
    clash = '"750","6121","6121 Gratuities","Expense","F","906121","1293","x","F","mapped"'
    new_first = '"761","6106","6106 Overtime","Expense","F","906106","1353","x","F","mapped"'
    with pytest.raises(AccountMapError, match="conflicts"):
        import_account_map(conn, write(tmp_path, new_first, clash))
    assert [r["financial_internal_id"] for r in repo.list_account_map(conn)] == ["405"]
    assert not conn.in_transaction


def test_missing_column_and_empty_file(conn, tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("financial_id,status\n1,mapped\n", encoding="utf-8")
    with pytest.raises(AccountMapError, match="missing columns"):
        import_account_map(conn, bad)
    with pytest.raises(AccountMapError, match="no data rows"):
        import_account_map(conn, write(tmp_path))


def test_cli_import_and_coverage(tmp_path: Path, fixtures: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    args = ["--data-dir", str(data_dir)]
    assert main([*args, "ingest-dir", str(fixtures)]) == 0

    c = open_database(data_dir / "teamsync.sqlite3")
    ref, count = c.execute(
        "SELECT account_ref, COUNT(*) FROM journal_lines WHERE account_is_numeric = 1 "
        "GROUP BY account_ref ORDER BY 2 DESC, 1 LIMIT 1"
    ).fetchone()
    total = c.execute("SELECT COUNT(*) FROM journal_lines").fetchone()[0]
    c.close()
    capsys.readouterr()

    path = write(tmp_path, MAPPED.replace('"405"', f'"{ref}"'))
    assert main([*args, "import-account-map", str(path)]) == 0
    assert "imported 1 accounts: 1 mapped, 0 waiting" in capsys.readouterr().out

    assert main([*args, "account-map"]) == 0
    out = capsys.readouterr().out
    assert f"lines: mapped={count} waiting_on_stat_account=0 not_in_map={total - count}" in out
    assert "not in map:" in out


def test_cli_import_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--data-dir", str(tmp_path / "data")]
    assert main([*args, "import-account-map", str(tmp_path / "nope.csv")]) == 2
    bad = write(tmp_path, MAPPED.replace('"906004"', '"906005"'))
    assert main([*args, "import-account-map", str(bad)]) == 1
    assert "nothing imported" in capsys.readouterr().err
