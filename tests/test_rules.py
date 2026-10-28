from __future__ import annotations

from pathlib import Path

from teamsync.parsers import FLAG_DESCRIPTIONS, RULES, parse_file


def test_every_rule_has_a_description() -> None:
    names = {rule.__name__ for rule in RULES}
    assert names == set(FLAG_DESCRIPTIONS)


def test_column_shift_fixture(fixtures: Path) -> None:
    path = fixtures / "Shifted Inn JE_100002_200002.CSV"
    e = parse_file(path.read_bytes(), path.name)
    assert e.is_balanced
    assert "suspect_column_shift" in e.flags
    assert "class_populated" in e.flags
    assert "line_memo_populated" in e.flags
    assert "unbalanced" not in e.flags


def test_multi_subsidiary_text_account_fixture(fixtures: Path) -> None:
    path = fixtures / "Multi Store JE_100003_200003.CSV"
    e = parse_file(path.read_bytes(), path.name)
    assert e.subsidiaries == ["51", "52"]
    assert e.locations == ["71", "72"]
    assert set(e.flags) == {"text_account_ref", "multi_subsidiary", "multi_location"}
    assert not e.lines[0].account_is_numeric
    assert e.lines[0].account_ref == "Payroll Clearing"


def test_mixed_unbalanced_memo_mismatch_fixture(fixtures: Path) -> None:
    path = fixtures / "Mixed Lodge JE_100004_200004.CSV"
    e = parse_file(path.read_bytes(), path.name)
    assert not e.is_balanced
    assert e.total_credit - e.total_debit == e.total_credit - e.total_debit  # sanity
    assert str(e.total_credit - e.total_debit) == "0.01"
    assert e.dept_style == "mixed"
    # filename says 100004.200004, memo says 999999.888888
    assert (e.payroll_id, e.batch_id) == ("100004", "200004")
    assert e.run_id == "4000004"
    assert set(e.flags) == {"unbalanced", "memo_filename_mismatch", "mixed_dept_style"}


def test_cp1252_fixture_flags_encoding(fixtures: Path) -> None:
    path = fixtures / "Café Lodge JE_100005_200005.CSV"
    e = parse_file(path.read_bytes(), path.name)
    assert e.flags == ["non_utf8_encoding"]
    assert e.property_name_raw == "Café Lodge"


def test_flags_are_in_rule_order(fixtures: Path) -> None:
    path = fixtures / "Multi Store JE_100003_200003.CSV"
    e = parse_file(path.read_bytes(), path.name)
    order = [rule.__name__ for rule in RULES]
    assert e.flags == sorted(e.flags, key=order.index)
