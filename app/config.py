from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "sample-data"
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "copilot.db"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


def load_env_file(path: Path | None = None, *, override: bool = False) -> None:
    """Load KEY=value pairs from a .env file into os.environ. Never logs values."""
    env_path = path or DEFAULT_ENV_PATH
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value


load_env_file()


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
