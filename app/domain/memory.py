from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

from app.db import init_schema

NO_MEMORY_MESSAGE = (
    "I have no earlier conversation with this user that matches that request, "
    "so I cannot resolve those entities from private memory."
)


def start_conversation(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    started_at: str,
    title: str | None = None,
    conversation_id: str | None = None,
) -> str:
    init_schema(conn)
    assigned = conversation_id or f"CNV-{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO conversations (conversation_id, user_id, started_at, title)
        VALUES (?, ?, ?, ?)
        """,
        (assigned, user_id, started_at, title),
    )
    return assigned


def add_message(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    user_id: str,
    role: str,
    content: str,
    created_at: str,
    entities: list[tuple[str, str]] | None = None,
    message_id: str | None = None,
) -> str:
    init_schema(conn)
    owner = conn.execute(
        "SELECT user_id FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if owner is None:
        raise ValueError(f"Unknown conversation: {conversation_id}")
    if owner["user_id"] != user_id:
        raise ValueError("Cannot write to another user's conversation")
    assigned = message_id or f"MSG-{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO messages (message_id, conversation_id, user_id, role, content, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (assigned, conversation_id, user_id, role, content, created_at),
    )
    for entity_type, entity_id in entities or []:
        conn.execute(
            """
            INSERT INTO conversation_entities (
                conversation_id, user_id, entity_type, entity_id, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (conversation_id, user_id, entity_type, entity_id, created_at),
        )
    return assigned


def discussed_entities(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    entity_type: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    init_schema(conn)
    sql = """
        SELECT entity_type, entity_id, MAX(created_at) AS last_mentioned_at
        FROM conversation_entities
        WHERE user_id = ?
    """
    params: list[Any] = [user_id]
    if entity_type:
        sql += " AND entity_type = ?"
        params.append(entity_type)
    sql += " GROUP BY entity_type, entity_id ORDER BY last_mentioned_at DESC, entity_id"
    rows = list(conn.execute(sql, params))
    if limit is not None:
        rows = rows[:limit]
    return [dict(row) for row in rows]


def resolve_discussed_wells(conn: sqlite3.Connection, user_id: str, *, limit: int = 3) -> dict[str, Any]:
    wells = discussed_entities(conn, user_id, entity_type="well", limit=limit)
    if not wells:
        return {
            "found": False,
            "user_id": user_id,
            "wells": [],
            "message": NO_MEMORY_MESSAGE,
        }
    return {
        "found": True,
        "user_id": user_id,
        "wells": [item["entity_id"] for item in wells],
        "entities": wells,
        "message": None,
    }


def conversation_history(conn: sqlite3.Connection, user_id: str, conversation_id: str) -> list[dict[str, Any]]:
    init_schema(conn)
    owner = conn.execute(
        "SELECT user_id FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if owner is None or owner["user_id"] != user_id:
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT message_id, role, content, created_at
            FROM messages
            WHERE conversation_id = ? AND user_id = ?
            ORDER BY created_at, message_id
            """,
            (conversation_id, user_id),
        )
    ]
