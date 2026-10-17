from __future__ import annotations

import sqlite3

from fastapi import FastAPI

from app.config import get_database_path
from app.db import table_counts

app = FastAPI(title="FlytBase Asset Intelligence Copilot", version="0.1.0")


@app.get("/health")
def health() -> dict:
    db_path = get_database_path()
    payload: dict = {
        "status": "ok",
        "database_path": str(db_path),
        "database_exists": db_path.is_file(),
    }
    if db_path.is_file():
        conn = sqlite3.connect(db_path)
        try:
            payload["counts"] = table_counts(conn)
        except sqlite3.Error:
            payload["counts"] = {}
        finally:
            conn.close()
    return payload
