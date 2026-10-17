from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

VisualType = Literal["status_card", "table", "line_chart", "bar_chart", "map", "heatmap"]
SUPPORTED_VISUAL_TYPES = ("status_card", "table", "line_chart", "bar_chart", "map", "heatmap")


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
    """Compatibility hint: type and title only. Data lives on Visualization."""

    type: VisualType | None = None
    title: str | None = None


class MetricCard(BaseModel):
    label: str
    value: str | int | float | bool | None = None
    unit: str | None = None
    tone: Literal["neutral", "ok", "warn", "alert", "unknown"] = "neutral"
    missing: bool = False
    record_id: str | None = None


class TableColumn(BaseModel):
    key: str
    label: str
    unit: str | None = None


class SeriesSpec(BaseModel):
    key: str
    label: str
    unit: str | None = None


class ChartPoint(BaseModel):
    x: str
    values: dict[str, float | None] = Field(default_factory=dict)
    record_id: str | None = None


class HeatCell(BaseModel):
    row: str
    col: str
    value: float | None = None
    missing: bool = False
    record_id: str | None = None


class MapMarker(BaseModel):
    lat: float
    lon: float
    label: str
    record_id: str | None = None
    detail: str | None = None


class Visualization(BaseModel):
    type: VisualType
    title: str | None = None
    description: str | None = None
    cards: list[MetricCard] = Field(default_factory=list)
    columns: list[TableColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    x_key: str | None = None
    x_label: str | None = None
    y_label: str | None = None
    series: list[SeriesSpec] = Field(default_factory=list)
    points: list[ChartPoint] = Field(default_factory=list)
    rows_axis: list[str] = Field(default_factory=list)
    cols_axis: list[str] = Field(default_factory=list)
    cells: list[HeatCell] = Field(default_factory=list)
    cell_meaning: str | None = None
    color_scale: str | None = None
    markers: list[MapMarker] = Field(default_factory=list)
    units: str | None = None
    empty_reason: str | None = None

    @field_validator("type")
    @classmethod
    def type_supported(cls, value: str) -> str:
        if value not in SUPPORTED_VISUAL_TYPES:
            raise ValueError(f"Unsupported visualization type: {value}")
        return value


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
    visuals: list[Visualization] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)
    pending_confirmation: list[PendingConfirmation] = Field(default_factory=list)
    tool_trace: list[ToolTraceItem] = Field(default_factory=list)
    conversation_id: str | None = None
    user_id: str | None = None
    error: str | None = None
    identity_authenticated: bool = False

    @field_validator("answer")
    @classmethod
    def answer_is_user_facing(cls, value: str) -> str:
        from app.copilot.answers import sanitize_user_answer

        if not (value or "").strip():
            return value
        return sanitize_user_answer(value)
