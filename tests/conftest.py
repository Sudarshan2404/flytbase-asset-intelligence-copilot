from __future__ import annotations

from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.ingest import ingest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mini-data"


@pytest.fixture
def mini_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "mini.db"
    ingest(FIXTURE_DIR, db_path)
    return db_path


@pytest.fixture(scope="session")
def baseline_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("baseline") / "copilot.db"
    ingest(PROJECT_ROOT / "sample-data", db_path)
    return db_path
