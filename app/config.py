from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "sample-data"
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "copilot.db"


def get_data_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_DATA_DIR.resolve()


def get_database_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("DATABASE_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_DATABASE_PATH.resolve()
