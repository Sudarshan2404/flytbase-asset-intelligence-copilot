from __future__ import annotations

import sqlite3

from fastapi import FastAPI

from app.config import get_database_path
from app.copilot.schemas import ChatRequest, ChatResponse
from app.copilot.service import handle_chat
from app.db import connect, init_schema, table_counts

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


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Answer a question for an explicit caller-supplied user_id.

    The user_id is not authenticated in this milestone. A later integration
    should replace it with a verified identity before calling handle_chat.
    """
    db_path = get_database_path()
    conn = connect(db_path)
    try:
        init_schema(conn)
        return handle_chat(conn, request)
    finally:
        conn.close()
