from __future__ import annotations

import re
from datetime import datetime, timezone

from app.copilot.config import CopilotConfigError
from app.copilot.loop import run_tool_loop
from app.copilot.provider import ModelProvider, get_provider
from app.copilot.schemas import ChatRequest, ChatResponse
from app.copilot.tools import WELL_ID_RE
from app.domain.memory import (
    NO_MEMORY_MESSAGE,
    add_message,
    conversation_history,
    resolve_discussed_wells,
    start_conversation,
)

FOLLOW_UP_RE = re.compile(
    r"\b(the three wells we discussed|wells we discussed|those wells|we discussed yesterday)\b",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _history_for_model(rows: list[dict]) -> list[dict]:
    contents = []
    for row in rows[-8:]:
        role = "user" if row["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": row["content"]}]})
    return contents


def handle_chat(conn, request: ChatRequest, provider: ModelProvider | None = None) -> ChatResponse:
    user_id = request.user_id
    conversation_id = request.conversation_id
    if conversation_id:
        owner = conn.execute(
            "SELECT user_id FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if owner is None:
            return ChatResponse(
                answer="That conversation was not found.",
                user_id=user_id,
                conversation_id=conversation_id,
                error="unknown_conversation",
            )
        if owner["user_id"] != user_id:
            return ChatResponse(
                answer="That conversation does not belong to the requesting user.",
                user_id=user_id,
                conversation_id=conversation_id,
                error="conversation_not_owned",
            )
        history_rows = conversation_history(conn, user_id, conversation_id)
    else:
        conversation_id = start_conversation(conn, user_id, started_at=_now())
        history_rows = []

    if FOLLOW_UP_RE.search(request.message):
        memory = resolve_discussed_wells(conn, user_id)
        if not memory["found"]:
            add_message(
                conn,
                conversation_id=conversation_id,
                user_id=user_id,
                role="user",
                content=request.message,
                created_at=_now(),
            )
            add_message(
                conn,
                conversation_id=conversation_id,
                user_id=user_id,
                role="assistant",
                content=NO_MEMORY_MESSAGE,
                created_at=_now(),
            )
            conn.commit()
            return ChatResponse(
                answer=NO_MEMORY_MESSAGE,
                user_id=user_id,
                conversation_id=conversation_id,
            )

    try:
        active_provider = provider or get_provider()
    except CopilotConfigError as exc:
        return ChatResponse(
            answer="The copilot model is not configured, so no AI answer was generated.",
            error=str(exc),
            user_id=user_id,
            conversation_id=conversation_id,
        )

    response = run_tool_loop(
        active_provider,
        conn,
        user_id=user_id,
        user_message=request.message,
        history=_history_for_model(history_rows),
    )
    response.user_id = user_id
    response.conversation_id = conversation_id

    well_ids = [match.group(0) for match in WELL_ID_RE.finditer(request.message)]
    for item in response.tool_trace:
        asset_id = item.arguments.get("asset_id")
        if isinstance(asset_id, str) and WELL_ID_RE.match(asset_id):
            well_ids.append(asset_id)
    entities = [("well", well_id) for well_id in dict.fromkeys(well_ids)]

    add_message(
        conn,
        conversation_id=conversation_id,
        user_id=user_id,
        role="user",
        content=request.message,
        created_at=_now(),
        entities=entities,
    )
    add_message(
        conn,
        conversation_id=conversation_id,
        user_id=user_id,
        role="assistant",
        content=response.answer,
        created_at=_now(),
    )
    conn.commit()
    return response
