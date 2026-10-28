"""Checks against the real sample exports in example-data/ when they are present.

These encode what we learned from the first batch. They skip in any checkout
without the gitignored folder.
"""

from __future__ import annotations

from pathlib import Path

from teamsync.parsers import parse_file


def _by_name(files: list[Path], prefix: str) -> Path:
    for p in files:
        if p.name.startswith(prefix):
            return p
    raise AssertionError(f"no example file starts with {prefix!r}")


def test_every_example_parses_and_balances(example_files: list[Path]) -> None:
    for path in example_files:
        e = parse_file(path.read_bytes(), path.name)
        assert e.is_balanced, path.name
        assert e.filename_matched, path.name
        assert "memo_filename_mismatch" not in e.flags, path.name
        assert e.run_id, path.name
        assert e.posting_period == "2026-10-15", path.name
        assert e.tran_date in {"2026-10-20", "2026-10-21"}, path.name
        assert len(e.header_memos) == 1, path.name


def test_sleep_inn_pasco_column_shift(example_files: list[Path]) -> None:
    e = parse_file(
        _by_name(example_files, "SLEEP INN PASCO").read_bytes(), "SLEEP INN PASCO JE_909973_202629.CSV"
    )
    assert "suspect_column_shift" in e.flags
    assert e.is_balanced


def test_cm_country_store_multi_subsidiary_text_account(example_files: list[Path]) -> None:
    path = _by_name(example_files, "C&M Country Store")
    e = parse_file(path.read_bytes(), path.name)
    assert set(e.flags) == {"text_account_ref", "multi_subsidiary", "multi_location"}
    assert e.subsidiaries == ["28", "29", "30", "31", "32"]


def test_hawthorne_mixed_departments(example_files: list[Path]) -> None:
    path = _by_name(example_files, "Hawthorne")
    e = parse_file(path.read_bytes(), path.name)
    assert e.flags == ["mixed_dept_style"]


def test_clean_files_have_no_flags(example_files: list[Path]) -> None:
    for prefix in ("Best Western Pendleton", "BW PLUS DAYTON", "Eternal", "WALDROP OIL"):
        path = _by_name(example_files, prefix)
        e = parse_file(path.read_bytes(), path.name)
        assert e.flags == [], path.name
