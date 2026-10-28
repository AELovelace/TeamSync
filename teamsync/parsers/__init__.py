"""File -> rows -> journal entry -> flags."""

from __future__ import annotations

from .je_rows import (
    PARSER_VERSION,
    JournalLine,
    ParsedJournalEntry,
    RowParseError,
    UnrecognizedLayout,
    parse_filename,
    parse_rows,
)
from .readers import PARSEABLE_KINDS, ReadResult, UnsupportedFormat, file_kind, read_rows
from .rules import FLAG_DESCRIPTIONS, RULES, apply_rules


def parse_file(data: bytes, filename: str) -> ParsedJournalEntry:
    """Convenience: bytes in, flagged journal entry out.

    Raises ``UnsupportedFormat``, ``UnrecognizedLayout`` or ``RowParseError``.
    """
    result = read_rows(data, filename)
    entry = parse_rows(result.rows, filename, reader_warnings=result.warnings)
    return apply_rules(entry)


__all__ = [
    "FLAG_DESCRIPTIONS",
    "PARSEABLE_KINDS",
    "PARSER_VERSION",
    "RULES",
    "JournalLine",
    "ParsedJournalEntry",
    "ReadResult",
    "RowParseError",
    "UnrecognizedLayout",
    "UnsupportedFormat",
    "apply_rules",
    "file_kind",
    "parse_file",
    "parse_filename",
    "parse_rows",
    "read_rows",
]
