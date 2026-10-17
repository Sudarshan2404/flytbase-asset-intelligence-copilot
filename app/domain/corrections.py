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
NUMERIC_FIELDS = frozenset(
    {"wellhead_temp_c", "ambient_temp_c", "spill_est_volume_bbl", "spill_area_pct"}
)
BOOLEAN_FIELDS = frozenset({"spill_detected"})
SCOPES = frozenset({"inspection", "well", "well_type"})
CORRECTION_ACTIVE = "active"
CORRECTION_SUPERSEDED = "superseded"
CORRECTION_RETRACTED = "retracted"
KNOWN_WELL_TYPES = frozenset({"T200", "T350"})

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
    provenance: tuple[dict[str, Any], ...] = ()


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


def _normalize_field_value(field_name: str, value: Any) -> Any:
    if field_name in BOOLEAN_FIELDS:
        normalized = _as_optional_bool(value)
        if normalized is None:
            raise ValueError(f"{field_name} must be a boolean")
        return normalized
    if field_name in NUMERIC_FIELDS:
        try:
            return _as_optional_float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be numeric") from exc
    return value


def _original_inspection_value(conn: sqlite3.Connection, inspection_id: str, field_name: str) -> Any:
    row = conn.execute("SELECT * FROM inspections WHERE inspection_id = ?", (inspection_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown inspection_id: {inspection_id}")
    raw = row[field_name]
    if field_name in BOOLEAN_FIELDS:
        return _as_optional_bool(raw)
    if field_name in NUMERIC_FIELDS:
        return _as_optional_float(raw)
    return raw


def record_correction(
    conn: sqlite3.Connection,
    *,
    scope: str,
    target_id: str,
    field_name: str | None = None,
    corrected_value: Any = None,
    rule_id: str | None = None,
    parameter: str | None = None,
    well_type: str | None = None,
    equipment_kind: str | None = None,
    author_user_id: str | None = None,
    reason: str | None = None,
    created_at: str | None = None,
    correction_id: str | None = None,
    inspection_id: str | None = None,
) -> dict[str, Any]:
    """Validated, append-only correction. Previous active rows are superseded, not deleted."""
    init_schema(conn)
    if scope not in SCOPES:
        raise ValueError(f"Unsupported correction scope: {scope}")
    assigned_id = correction_id or f"SCR-{uuid4().hex[:12]}"

    if scope == "inspection":
        if not field_name:
            raise ValueError("Inspection-scope corrections require field_name")
        if field_name not in CORRECTABLE_FIELDS:
            raise ValueError(f"Unsupported correction field: {field_name}")
        target_inspection = inspection_id or target_id
        original = _original_inspection_value(conn, target_inspection, field_name)
        value = _normalize_field_value(field_name, corrected_value)
        conn.execute(
            """
            UPDATE structured_corrections
            SET status = ?
            WHERE inspection_id = ? AND field_name = ? AND IFNULL(status, 'active') = 'active'
            """,
            (CORRECTION_SUPERSEDED, target_inspection, field_name),
        )
        conn.execute(
            """
            INSERT INTO structured_corrections (
                correction_id, inspection_id, field_name, corrected_value_json,
                author_user_id, reason, created_at, scope, target_type, target_id,
                original_value_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assigned_id,
                target_inspection,
                field_name,
                json.dumps(value),
                author_user_id,
                reason,
                created_at,
                "inspection",
                "inspection",
                target_inspection,
                json.dumps(original),
                CORRECTION_ACTIVE,
            ),
        )
        return {
            "correction_id": assigned_id,
            "scope": "inspection",
            "target_id": target_inspection,
            "field_name": field_name,
            "original_value": original,
            "corrected_value": value,
            "author_user_id": author_user_id,
            "reason": reason,
            "status": CORRECTION_ACTIVE,
        }

    if scope in {"well", "well_type"}:
        if rule_id != "RCA" or parameter != "threshold":
            raise ValueError("Well and well-type scopes currently support RCA threshold rules only")
        try:
            threshold = int(corrected_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("RCA threshold must be an integer") from exc
        if threshold < 1:
            raise ValueError("RCA threshold must be at least 1")
        resolved_well_type = well_type
        asset_id = None
        if scope == "well":
            well = conn.execute("SELECT asset_id, well_type FROM wells WHERE asset_id = ?", (target_id,)).fetchone()
            if well is None:
                raise ValueError(f"Unknown well: {target_id}")
            asset_id = well["asset_id"]
            resolved_well_type = well["well_type"]
        elif target_id not in KNOWN_WELL_TYPES and (well_type or target_id) not in KNOWN_WELL_TYPES:
            raise ValueError(f"Unknown well_type: {target_id}")
        else:
            resolved_well_type = well_type or target_id
        original = 3
        conn.execute(
            """
            UPDATE rule_overrides
            SET status = ?
            WHERE rule_id = 'RCA' AND parameter = 'threshold'
              AND IFNULL(well_type, '') = IFNULL(?, '')
              AND IFNULL(equipment_kind, '') = IFNULL(?, '')
              AND IFNULL(asset_id, '') = IFNULL(?, '')
              AND IFNULL(status, 'active') = 'active'
            """,
            (CORRECTION_SUPERSEDED, resolved_well_type, equipment_kind, asset_id),
        )
        conn.execute(
            """
            INSERT INTO rule_overrides (
                override_id, rule_id, well_type, equipment_kind, parameter, value_json,
                author_user_id, reason, created_at, asset_id, original_value_json, status
            ) VALUES (?, 'RCA', ?, ?, 'threshold', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assigned_id,
                resolved_well_type,
                equipment_kind,
                json.dumps(threshold),
                author_user_id,
                reason,
                created_at,
                asset_id,
                json.dumps(original),
                CORRECTION_ACTIVE,
            ),
        )
        return {
            "correction_id": assigned_id,
            "scope": scope,
            "target_id": target_id,
            "field_name": "threshold",
            "original_value": original,
            "corrected_value": threshold,
            "author_user_id": author_user_id,
            "reason": reason,
            "status": CORRECTION_ACTIVE,
            "well_type": resolved_well_type,
            "equipment_kind": equipment_kind,
            "asset_id": asset_id,
        }

    raise ValueError(f"Unsupported correction scope: {scope}")


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
    result = record_correction(
        conn,
        scope="inspection",
        target_id=inspection_id,
        inspection_id=inspection_id,
        field_name=field_name,
        corrected_value=corrected_value,
        author_user_id=author_user_id,
        reason=reason,
        created_at=created_at,
        correction_id=correction_id,
    )
    return result["correction_id"]


def correction_history(conn: sqlite3.Connection, *, inspection_id: str | None = None) -> list[dict[str, Any]]:
    init_schema(conn)
    rows = conn.execute(
        """
        SELECT correction_id, inspection_id, field_name, corrected_value_json, original_value_json,
               author_user_id, reason, created_at, scope, target_id, status
        FROM structured_corrections
        WHERE inspection_id = ? OR ? IS NULL
        ORDER BY created_at, correction_id
        """,
        (inspection_id, inspection_id),
    ).fetchall()
    history = []
    for row in rows:
        history.append(
            {
                "correction_id": row["correction_id"],
                "inspection_id": row["inspection_id"],
                "field_name": row["field_name"],
                "original_value": json.loads(row["original_value_json"]) if row["original_value_json"] else None,
                "corrected_value": json.loads(row["corrected_value_json"]),
                "author_user_id": row["author_user_id"],
                "reason": row["reason"],
                "created_at": row["created_at"],
                "scope": row["scope"] or "inspection",
                "status": row["status"] or CORRECTION_ACTIVE,
            }
        )
    return history


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
            SELECT correction_id, field_name, corrected_value_json, author_user_id, reason, original_value_json
            FROM structured_corrections
            WHERE inspection_id = ? AND IFNULL(status, 'active') = 'active'
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
    provenance: list[dict[str, Any]] = []
    if formal:
        provenance.append(
            {
                "field_name": "spill_detected",
                "original_value": _as_optional_bool(original.get("spill_detected")),
                "effective_value": False,
                "correction_ids": [item["note_id"] for item in formal],
                "source": "formal_correction",
                "reason": "Formal correction notes void spill detection for the named inspection.",
            }
        )
    for item in structured:
        structured_ids.append(item["correction_id"])
        field_name = item["field_name"]
        value = json.loads(item["corrected_value_json"])
        before = {
            "wellhead_temp_c": wellhead,
            "ambient_temp_c": ambient,
            "spill_detected": spill_detected,
            "spill_est_volume_bbl": volume,
            "spill_area_pct": area,
        }[field_name]
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
        provenance.append(
            {
                "field_name": field_name,
                "original_value": json.loads(item["original_value_json"]) if item["original_value_json"] else before,
                "effective_value": {
                    "wellhead_temp_c": wellhead,
                    "ambient_temp_c": ambient,
                    "spill_detected": spill_detected,
                    "spill_est_volume_bbl": volume,
                    "spill_area_pct": area,
                }[field_name],
                "correction_id": item["correction_id"],
                "author_user_id": item["author_user_id"],
                "reason": item["reason"],
                "source": "structured_correction",
            }
        )

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
        provenance=tuple(provenance),
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
        "provenance": [item for item in view.provenance if item["field_name"].startswith("spill") or item["field_name"] == "spill_detected"],
    }
