"""Walk a local directory tree and yield every file in it.

Used for example-data in development and for a server-side watch directory.
Box Drive and Windows housekeeping files are skipped by name.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .base import IncomingFile

SKIP_NAMES = frozenset({"desktop.ini", "thumbs.db", ".ds_store"})
SKIP_SUFFIXES = (".boxnote", ".tmp", ".crdownload", ".part")


def should_skip(path: Path) -> bool:
    name = path.name.lower()
    if name in SKIP_NAMES or name.startswith(("~$", ".")):
        return True
    return name.endswith(SKIP_SUFFIXES)


class LocalDirectorySource:
    def __init__(self, root: Path | str, source_kind: str = "local") -> None:
        self.root = Path(root)
        self.source_kind = source_kind

    def iter_files(self) -> Iterator[IncomingFile]:
        if not self.root.is_dir():
            return
        for path in sorted(p for p in self.root.rglob("*") if p.is_file()):
            if should_skip(path):
                continue
            rel = path.relative_to(self.root)
            yield IncomingFile(
                name=path.name,
                data=path.read_bytes(),
                source_kind=self.source_kind,
                source_path=rel.as_posix(),
                batch_folder=rel.parent.name or None,
            )
