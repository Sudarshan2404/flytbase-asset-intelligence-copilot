from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

VisualType = Literal["status_card", "table", "line_chart", "bar_chart", "map", "heatmap"]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    user_id: str = Field(
        min_length=1,
        description="Caller-supplied user identity. Not authenticated in this milestone.",
    )
    conversation_id: str | None = None


class Citation(BaseModel):
    kind: str
    record_id: str | None = None
    path: str | None = None
    excerpt: str | None = None


class VisualSpec(BaseModel):
    type: VisualType | None = None
    title: str | None = None


class ConflictItem(BaseModel):
    kind: str
    summary: str
    record_ids: list[str] = Field(default_factory=list)


class PendingConfirmation(BaseModel):
    claim_id: str | None = None
    asset_id: str | None = None
    text: str | None = None
    message: str | None = None


class ToolTraceItem(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    error: str | None = None


class ChatResponse(BaseModel):
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    visual: VisualSpec | None = None
    conflicts: list[ConflictItem] = Field(default_factory=list)
    pending_confirmation: list[PendingConfirmation] = Field(default_factory=list)
    tool_trace: list[ToolTraceItem] = Field(default_factory=list)
    conversation_id: str | None = None
    user_id: str | None = None
    error: str | None = None
    # Explicit reminder for later auth work.
    identity_authenticated: bool = False
