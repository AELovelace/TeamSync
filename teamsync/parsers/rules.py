"""Validation rules for a parsed journal entry.

Each rule takes a ``ParsedJournalEntry`` and returns a flag name or ``None``.
Rules never reject; they annotate. ``apply_rules`` fills ``entry.flags`` in a
stable order and returns the entry for chaining.
"""

from __future__ import annotations

from collections.abc import Callable

from .je_rows import MEMO_RE, ParsedJournalEntry

Rule = Callable[[ParsedJournalEntry], str | None]

FLAG_DESCRIPTIONS: dict[str, str] = {
    "unbalanced": "Total debits do not equal total credits.",
    "filename_pattern_mismatch": "Filename is not '<Property> JE_<payroll>_<batch>.<ext>'.",
    "memo_filename_mismatch": "Header memo does not start with the filename's '<payroll>.<batch>'.",
    "memo_format_unexpected": "Header memo is not '<payroll>.<batch>.<run>'.",
    "multiple_header_memos": "More than one distinct header memo in one file.",
    "multiple_posting_periods": "More than one posting period in one file.",
    "multiple_tran_dates": "More than one transaction date in one file.",
    "suspect_column_shift": "Account, department, class and location all equal the subsidiary on every row.",
    "text_account_ref": "At least one account reference is a name rather than an internal ID.",
    "mixed_dept_style": "Department column mixes numeric IDs, paths and names.",
    "multi_subsidiary": "More than one subsidiary in one file.",
    "multi_location": "More than one location in one file.",
    "class_populated": "Class column is non-empty somewhere; it is normally blank.",
    "line_memo_populated": "Line memo column is non-empty somewhere; it is normally blank.",
    "both_sides_on_line": "A line has both a debit and a credit.",
    "empty_amount_line": "A line has neither a debit nor a credit.",
    "extra_sheets_ignored": "Workbook had more than one sheet; only the first was read.",
    "non_utf8_encoding": "CSV was not UTF-8 and was decoded with a fallback encoding.",
}


def unbalanced(e: ParsedJournalEntry) -> str | None:
    return None if e.is_balanced else "unbalanced"


def filename_pattern_mismatch(e: ParsedJournalEntry) -> str | None:
    return None if e.filename_matched else "filename_pattern_mismatch"


def memo_filename_mismatch(e: ParsedJournalEntry) -> str | None:
    if not e.filename_matched:
        return None
    prefix = f"{e.payroll_id}.{e.batch_id}"
    return None if e.header_memo.startswith(prefix) else "memo_filename_mismatch"


def memo_format_unexpected(e: ParsedJournalEntry) -> str | None:
    return None if MEMO_RE.match(e.header_memo) else "memo_format_unexpected"


def multiple_header_memos(e: ParsedJournalEntry) -> str | None:
    return "multiple_header_memos" if len(e.header_memos) > 1 else None


def multiple_posting_periods(e: ParsedJournalEntry) -> str | None:
    return "multiple_posting_periods" if len(e.posting_periods) > 1 else None


def multiple_tran_dates(e: ParsedJournalEntry) -> str | None:
    return "multiple_tran_dates" if len(e.tran_dates) > 1 else None


def suspect_column_shift(e: ParsedJournalEntry) -> str | None:
    for ln in e.lines:
        s = ln.subsidiary
        if not (
            ln.account_ref == s and ln.department_ref == s and ln.class_ref == s and ln.location_ref == s
        ):
            return None
    return "suspect_column_shift"


def text_account_ref(e: ParsedJournalEntry) -> str | None:
    return "text_account_ref" if any(not ln.account_is_numeric for ln in e.lines) else None


def mixed_dept_style(e: ParsedJournalEntry) -> str | None:
    return "mixed_dept_style" if e.dept_style == "mixed" else None


def multi_subsidiary(e: ParsedJournalEntry) -> str | None:
    return "multi_subsidiary" if len(e.subsidiaries) > 1 else None


def multi_location(e: ParsedJournalEntry) -> str | None:
    return "multi_location" if len(e.locations) > 1 else None


def class_populated(e: ParsedJournalEntry) -> str | None:
    return "class_populated" if any(ln.class_ref for ln in e.lines) else None


def line_memo_populated(e: ParsedJournalEntry) -> str | None:
    return "line_memo_populated" if any(ln.line_memo for ln in e.lines) else None


def both_sides_on_line(e: ParsedJournalEntry) -> str | None:
    return (
        "both_sides_on_line"
        if any(ln.debit is not None and ln.credit is not None for ln in e.lines)
        else None
    )


def empty_amount_line(e: ParsedJournalEntry) -> str | None:
    return "empty_amount_line" if any(ln.debit is None and ln.credit is None for ln in e.lines) else None


def extra_sheets_ignored(e: ParsedJournalEntry) -> str | None:
    return "extra_sheets_ignored" if any(w.startswith("ignored") for w in e.reader_warnings) else None


def non_utf8_encoding(e: ParsedJournalEntry) -> str | None:
    return "non_utf8_encoding" if any(w.startswith("decoded as") for w in e.reader_warnings) else None


RULES: tuple[Rule, ...] = (
    unbalanced,
    filename_pattern_mismatch,
    memo_filename_mismatch,
    memo_format_unexpected,
    multiple_header_memos,
    multiple_posting_periods,
    multiple_tran_dates,
    suspect_column_shift,
    text_account_ref,
    mixed_dept_style,
    multi_subsidiary,
    multi_location,
    class_populated,
    line_memo_populated,
    both_sides_on_line,
    empty_amount_line,
    extra_sheets_ignored,
    non_utf8_encoding,
)


def apply_rules(entry: ParsedJournalEntry, rules: tuple[Rule, ...] = RULES) -> ParsedJournalEntry:
    flags = [flag for rule in rules if (flag := rule(entry))]
    entry.flags = flags
    return entry
