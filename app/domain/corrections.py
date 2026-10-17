from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.db import init_schema
from app.domain.spills import CLASS_UNKNOWN_VOLUME, classify_spill_volume

CORRECTABLE_FIELDS = frozenset(
    {
        "wellhead_temp_c",
        "ambient_temp_c",
        "spill_detected",
        "spill_est_volume_bbl",
        "spill_area_pct",
    }
)

# Baseline formal corrections void a detection. They do not encode replacement numbers.
# Structured corrections (later milestone) are the only safe place for replacement values.


@dataclass(frozen=True)
class EffectiveInspection:
    inspection_id: str
    asset_id: str | None
    inspected_at: str | None
    original: dict[str, Any]
    wellhead_temp_c: float | None
    ambient_temp_c: float | None
    spill_detected: bool | None
    spill_est_volume_bbl: float | None
    spill_area_pct: float | None
    formal_correction_ids: tuple[str, ...]
    structured_correction_ids: tuple[str, ...]
    spill_excluded_by_correction: bool


def _row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _as_optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return bool(value)


def apply_structured_correction(
    conn: sqlite3.Connection,
    *,
    inspection_id: str,
    field_name: str,
    corrected_value: Any,
    author_user_id: str | None = None,
    reason: str | None = None,
    created_at: str | None = None,
    correction_id: str | None = None,
) -> str:
    """Store a field-level correction without mutating the original inspection row."""
    if field_name not in CORRECTABLE_FIELDS:
        raise ValueError(f"Unsupported correction field: {field_name}")
    init_schema(conn)
    assigned_id = correction_id or f"SCR-{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO structured_corrections (
            correction_id, inspection_id, field_name, corrected_value_json,
            author_user_id, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assigned_id,
            inspection_id,
            field_name,
            json.dumps(corrected_value),
            author_user_id,
            reason,
            created_at,
        ),
    )
    return assigned_id


def _formal_corrections(conn: sqlite3.Connection, inspection_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT note_id, inspection_id, note_type, text
            FROM context_notes
            WHERE inspection_id = ? AND note_type = 'correction'
            ORDER BY created_at, note_id
            """,
            (inspection_id,),
        )
    )


def _structured_corrections(conn: sqlite3.Connection, inspection_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT correction_id, field_name, corrected_value_json
            FROM structured_corrections
            WHERE inspection_id = ?
            ORDER BY created_at, correction_id
            """,
            (inspection_id,),
        )
    )


def effective_inspection(conn: sqlite3.Connection, inspection: str | sqlite3.Row) -> EffectiveInspection:
    """Shared correction-aware view of one inspection.

    Formal `correction` notes linked to the inspection void spill detection.
    Free-text is not parsed for replacement values. Structured corrections, when
    present, override named fields after that voiding step.
    """
    init_schema(conn)
    if isinstance(inspection, str):
        row = conn.execute(
            "SELECT * FROM inspections WHERE inspection_id = ?",
            (inspection,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown inspection_id: {inspection}")
    else:
        row = inspection

    original = _row_to_dict(row)
    wellhead = _as_optional_float(original.get("wellhead_temp_c"))
    ambient = _as_optional_float(original.get("ambient_temp_c"))
    spill_detected = _as_optional_bool(original.get("spill_detected"))
    volume = _as_optional_float(original.get("spill_est_volume_bbl"))
    area = _as_optional_float(original.get("spill_area_pct"))

    formal = _formal_corrections(conn, original["inspection_id"])
    spill_excluded = False
    if formal:
        spill_detected = False
        volume = None
        area = None
        spill_excluded = True

    structured = _structured_corrections(conn, original["inspection_id"])
    structured_ids: list[str] = []
    for item in structured:
        structured_ids.append(item["correction_id"])
        field_name = item["field_name"]
        value = json.loads(item["corrected_value_json"])
        if field_name == "wellhead_temp_c":
            wellhead = _as_optional_float(value)
        elif field_name == "ambient_temp_c":
            ambient = _as_optional_float(value)
        elif field_name == "spill_detected":
            spill_detected = _as_optional_bool(value)
            spill_excluded = not bool(spill_detected)
        elif field_name == "spill_est_volume_bbl":
            volume = _as_optional_float(value)
        elif field_name == "spill_area_pct":
            area = _as_optional_float(value)

    return EffectiveInspection(
        inspection_id=original["inspection_id"],
        asset_id=original.get("asset_id"),
        inspected_at=original.get("inspected_at"),
        original={
            "inspection_id": original.get("inspection_id"),
            "asset_id": original.get("asset_id"),
            "inspected_at": original.get("inspected_at"),
            "wellhead_temp_c": _as_optional_float(original.get("wellhead_temp_c")),
            "ambient_temp_c": _as_optional_float(original.get("ambient_temp_c")),
            "spill_detected": _as_optional_bool(original.get("spill_detected")),
            "spill_est_volume_bbl": _as_optional_float(original.get("spill_est_volume_bbl")),
            "spill_area_pct": _as_optional_float(original.get("spill_area_pct")),
            "notes": original.get("notes"),
            "inspector_user_id": original.get("inspector_user_id"),
            "method": original.get("method"),
        },
        wellhead_temp_c=wellhead,
        ambient_temp_c=ambient,
        spill_detected=spill_detected,
        spill_est_volume_bbl=volume,
        spill_area_pct=area,
        formal_correction_ids=tuple(item["note_id"] for item in formal),
        structured_correction_ids=tuple(structured_ids),
        spill_excluded_by_correction=spill_excluded,
    )


def effective_spill(view: EffectiveInspection) -> dict[str, Any]:
    """Spill view used by every downstream spill statistic."""
    is_spill = view.spill_detected is True
    classification = None
    if is_spill:
        classification = classify_spill_volume(view.spill_est_volume_bbl)
    elif view.spill_excluded_by_correction:
        classification = None
    return {
        "inspection_id": view.inspection_id,
        "asset_id": view.asset_id,
        "inspected_at": view.inspected_at,
        "original_spill_detected": view.original["spill_detected"],
        "original_volume_bbl": view.original["spill_est_volume_bbl"],
        "effective_spill_detected": is_spill,
        "effective_volume_bbl": view.spill_est_volume_bbl if is_spill else None,
        "classification": classification,
        "excluded_by_correction": view.spill_excluded_by_correction,
        "formal_correction_ids": view.formal_correction_ids,
        "structured_correction_ids": view.structured_correction_ids,
        "unknown_volume": is_spill and classification == CLASS_UNKNOWN_VOLUME,
    }
