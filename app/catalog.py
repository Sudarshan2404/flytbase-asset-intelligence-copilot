from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.config import get_database_path
from app.db import connect, init_schema, table_counts
from app.domain.compliance import evaluate_well_compliance
from app.domain.corrections import correction_history
from app.domain.dates import EVALUATION_TODAY, lookback_start
from app.domain.knowledge import list_claims, list_shared_notes
from app.domain.queries import current_production_status, latest_inspection

router = APIRouter(prefix="/api")


def _open():
    conn = connect(get_database_path())
    init_schema(conn)
    return conn


@router.get("/overview")
def overview() -> dict[str, Any]:
    conn = _open()
    try:
        counts = table_counts(conn)
        areas = [
            dict(row)
            for row in conn.execute(
                """
                SELECT a.area_id, a.name, a.center_lat, a.center_lon,
                       (SELECT COUNT(*) FROM area_assets aa WHERE aa.area_id = a.area_id) AS well_count
                FROM areas a
                ORDER BY a.area_id
                """
            )
        ]
        recent_inspections = [
            dict(row)
            for row in conn.execute(
                """
                SELECT inspection_id, asset_id, inspected_at, spill_detected, wellhead_temp_c, ambient_temp_c
                FROM inspections
                WHERE inspected_at IS NOT NULL
                ORDER BY inspected_at DESC
                LIMIT 8
                """
            )
        ]
        recent_maintenance = [
            dict(row)
            for row in conn.execute(
                """
                SELECT maintenance_id, asset_id, started_at, issue, equipment_kind
                FROM maintenance
                WHERE started_at IS NOT NULL
                ORDER BY started_at DESC
                LIMIT 5
                """
            )
        ]
        return {
            "as_of": EVALUATION_TODAY.isoformat(),
            "counts": counts,
            "areas": areas,
            "recent_inspections": recent_inspections,
            "recent_maintenance": recent_maintenance,
            "identity_authenticated": False,
        }
    finally:
        conn.close()


@router.get("/users")
def list_users() -> dict[str, Any]:
    conn = _open()
    try:
        users = [
            dict(row)
            for row in conn.execute(
                "SELECT user_id, name, role, team_id FROM users ORDER BY user_id"
            )
        ]
        return {
            "users": users,
            "identity_authenticated": False,
            "note": "Caller-supplied user_id is not authenticated.",
        }
    finally:
        conn.close()


@router.get("/conversations")
def list_conversations(
    user_id: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Text threads stored for model context. Visualization payloads are not persisted."""
    conn = _open()
    try:
        rows = conn.execute(
            """
            SELECT c.conversation_id, c.started_at, c.title,
                   (
                       SELECT m.content FROM messages m
                       WHERE m.conversation_id = c.conversation_id
                       ORDER BY m.created_at DESC, m.message_id DESC
                       LIMIT 1
                   ) AS last_text
            FROM conversations c
            WHERE c.user_id = ?
            ORDER BY c.started_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return {
            "user_id": user_id,
            "identity_authenticated": False,
            "visuals_persisted": False,
            "note": (
                "The backend stores message text for this user so the model can continue a thread. "
                "Chart specifications are not stored; they are only rendered for replies in the current session."
            ),
            "conversations": [dict(row) for row in rows],
        }
    finally:
        conn.close()


@router.get("/areas")
def list_areas() -> dict[str, Any]:
    conn = _open()
    try:
        return {
            "areas": [
                dict(row)
                for row in conn.execute("SELECT area_id, name, team_id FROM areas ORDER BY area_id")
            ]
        }
    finally:
        conn.close()


@router.get("/wells")
def list_wells(
    q: str | None = None,
    area: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    conn = _open()
    try:
        clauses: list[str] = ["1=1"]
        params: list[Any] = []
        if q:
            clauses.append("(asset_id LIKE ? OR name LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if area:
            clauses.append("(area_id = ? OR area_id IN (SELECT area_id FROM areas WHERE name = ?))")
            params.extend([area, area])
        where = " AND ".join(clauses)
        total = conn.execute(f"SELECT COUNT(*) FROM wells WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT asset_id, name, well_type, area_id, team_id, recorded_status, lat, lon, installed_on
            FROM wells
            WHERE {where}
            ORDER BY asset_id
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        return {"total": total, "limit": limit, "offset": offset, "wells": [dict(row) for row in rows]}
    finally:
        conn.close()


@router.get("/wells/{asset_id}")
def well_detail(asset_id: str) -> dict[str, Any]:
    conn = _open()
    try:
        well = conn.execute(
            """
            SELECT asset_id, name, well_type, area_id, team_id, in_charge_user_id,
                   recorded_status, lat, lon, installed_on
            FROM wells WHERE asset_id = ?
            """,
            (asset_id,),
        ).fetchone()
        if well is None:
            raise HTTPException(status_code=404, detail=f"Unknown well: {asset_id}")
        status = current_production_status(conn, asset_id)
        inspection = latest_inspection(conn, asset_id)
        notes = list_shared_notes(conn, asset_id)
        return {
            "well": dict(well),
            "production": {
                "asset_id": status.get("asset_id"),
                "status": status.get("status"),
                "producing": status.get("producing"),
                "inspected_at": status.get("inspected_at"),
                "inspection_id": status.get("inspection_id"),
                "wellhead_temp_c": status.get("wellhead_temp_c"),
                "ambient_temp_c": status.get("ambient_temp_c"),
                "delta_c": status.get("delta_c"),
                "message": status.get("message"),
            },
            "latest_inspection": None
            if inspection is None
            else {
                "inspection_id": inspection.get("inspection_id"),
                "inspected_at": inspection.get("inspected_at"),
                "notes": inspection.get("notes"),
                "method": inspection.get("method"),
            },
            "shared_notes": notes[:20],
        }
    finally:
        conn.close()


@router.get("/compliance")
def compliance(
    asset_id: str = Query(min_length=1),
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    start_date = start or lookback_start(EVALUATION_TODAY, years=1).isoformat()
    end_date = end or EVALUATION_TODAY.isoformat()
    conn = _open()
    try:
        well = conn.execute("SELECT asset_id FROM wells WHERE asset_id = ?", (asset_id,)).fetchone()
        if well is None:
            raise HTTPException(status_code=404, detail=f"Unknown well: {asset_id}")
        findings = [item.as_dict() for item in evaluate_well_compliance(conn, asset_id, start_date, end_date)]
        return {
            "asset_id": asset_id,
            "start": start_date,
            "end": end_date,
            "findings": findings,
        }
    finally:
        conn.close()


@router.get("/knowledge/notes")
def knowledge_notes(asset_id: str | None = None) -> dict[str, Any]:
    conn = _open()
    try:
        return {"scope": "shared", "notes": list_shared_notes(conn, asset_id)}
    finally:
        conn.close()


@router.get("/knowledge/claims")
def knowledge_claims(asset_id: str | None = None) -> dict[str, Any]:
    conn = _open()
    try:
        return {
            "scope": "shared_unconfirmed",
            "note": "Claims are not private conversation memory. They stay unconfirmed until explicitly confirmed.",
            "claims": list_claims(conn, asset_id),
        }
    finally:
        conn.close()


@router.get("/knowledge/corrections")
def knowledge_corrections(inspection_id: str | None = None) -> dict[str, Any]:
    conn = _open()
    try:
        return {
            "scope": "auditable",
            "corrections": correction_history(conn, inspection_id=inspection_id),
        }
    finally:
        conn.close()
