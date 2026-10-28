from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLE_DATA = Path(__file__).resolve().parent.parent / "example-data"


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def example_files() -> list[Path]:
    """Real sample exports when present locally. Gitignored, so CI skips these."""
    if not EXAMPLE_DATA.is_dir():
        pytest.skip("example-data/ not present")
    files = sorted(p for p in EXAMPLE_DATA.iterdir() if p.suffix.lower() == ".csv")
    if not files:
        pytest.skip("example-data/ has no CSV files")
    return files
