from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class IncomingFile:
    name: str
    data: bytes
    source_kind: str  # push | local | upload | box_api
    source_path: str | None = None  # relative path at the source
    batch_folder: str | None = None  # immediate parent folder name at the source


class FileSource(Protocol):
    def iter_files(self) -> Iterator[IncomingFile]: ...
