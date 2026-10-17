from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Iterable

from app.db import init_schema
from app.domain.corrections import effective_inspection, effective_spill
from app.domain.dates import in_date_window, parse_date
from app.domain.production import (
    STATUS_NO_INSPECTION,
    STATUS_NOT_PRODUCING,
    production_from_temps,
)

DAYS_PER_NON_PRODUCING_INSPECTION = 7


def _ensure_schema(conn: sqlite3.Connection) -> None:
    init_schema(conn)


def _get_well(conn: sqlite3.Connection, asset_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM wells WHERE asset_id = ?", (asset_id,)).fetchone()


def resolve_area(conn: sqlite3.Connection, area: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM areas WHERE area_id = ? OR name = ?",
        (area, area),
    ).fetchone()


def area_asset_ids(conn: sqlite3.Connection, area: str) -> list[str]:
    row = resolve_area(conn, area)
    if row is None:
        return []
    return [
        item["asset_id"]
        for item in conn.execute(
            "SELECT asset_id FROM area_assets WHERE area_id = ? ORDER BY asset_id",
            (row["area_id"],),
        )
    ]


def _inspections_for_well(
    conn: sqlite3.Connection,
    asset_id: str,
    start: date | str | None = None,
    end: date | str | None = None,
) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT * FROM inspections
        WHERE asset_id = ?
        ORDER BY inspected_at, inspection_id
        """,
        (asset_id,),
    ).fetchall()
    if start is None or end is None:
        return rows
    return [row for row in rows if in_date_window(row["inspected_at"], start, end)]


def latest_inspection(conn: sqlite3.Connection, asset_id: str) -> dict[str, Any] | None:
    _ensure_schema(conn)
    row = conn.execute(
        """
        SELECT * FROM inspections
        WHERE asset_id = ?
        ORDER BY inspected_at DESC, inspection_id DESC
        LIMIT 1
        """,
        (asset_id,),
    ).fetchone()
    if row is None:
        return None
    view = effective_inspection(conn, row)
    return {
        "inspection_id": view.inspection_id,
        "asset_id": view.asset_id,
        "inspected_at": view.inspected_at,
        "inspector_user_id": view.original.get("inspector_user_id"),
        "method": view.original.get("method"),
        "notes": view.original.get("notes"),
        "original": view.original,
        "effective": view,
    }


def current_production_status(conn: sqlite3.Connection, asset_id: str) -> dict[str, Any]:
    _ensure_schema(conn)
    well = _get_well(conn, asset_id)
    if well is None:
        return {
            "asset_id": asset_id,
            "status": "unknown_well",
            "producing": None,
            "recorded_status": None,
            "recorded_status_authoritative": False,
            "message": f"Well {asset_id} was not found",
        }
    latest = latest_inspection(conn, asset_id)
    if latest is None:
        return {
            "asset_id": asset_id,
            "well_type": well["well_type"],
            "status": STATUS_NO_INSPECTION,
            "producing": None,
            "recorded_status": well["recorded_status"],
            "recorded_status_authoritative": False,
            "inspection": None,
            "message": f"No inspections are stored for {asset_id}",
        }
    view = latest["effective"]
    production = production_from_temps(
        well["well_type"],
        view.wellhead_temp_c,
        view.ambient_temp_c,
    )
    return {
        "asset_id": asset_id,
        "well_type": well["well_type"],
        "recorded_status": well["recorded_status"],
        "recorded_status_authoritative": False,
        "inspection_id": view.inspection_id,
        "inspected_at": view.inspected_at,
        "inspector_user_id": latest["inspector_user_id"],
        "method": latest["method"],
        **production,
        "inspection": latest,
        "original_wellhead_temp_c": view.original["wellhead_temp_c"],
        "original_ambient_temp_c": view.original["ambient_temp_c"],
        "provenance": list(view.provenance),
    }


def production_history(
    conn: sqlite3.Connection,
    asset_id: str,
    start: date | str,
    end: date | str,
) -> dict[str, Any]:
    _ensure_schema(conn)
    well = _get_well(conn, asset_id)
    well_type = well["well_type"] if well else None
    readings = []
    for row in _inspections_for_well(conn, asset_id, start, end):
        view = effective_inspection(conn, row)
        production = production_from_temps(well_type, view.wellhead_temp_c, view.ambient_temp_c)
        readings.append(
            {
                "inspection_id": view.inspection_id,
                "inspected_at": view.inspected_at,
                "wellhead_temp_c": view.wellhead_temp_c,
                "ambient_temp_c": view.ambient_temp_c,
                "original_wellhead_temp_c": view.original["wellhead_temp_c"],
                "original_ambient_temp_c": view.original["ambient_temp_c"],
                **production,
            }
        )
    return {
        "asset_id": asset_id,
        "well_type": well_type,
        "start": parse_date(start).isoformat(),
        "end": parse_date(end).isoformat(),
        "readings": readings,
    }


def downtime_for_well(
    conn: sqlite3.Connection,
    asset_id: str,
    start: date | str,
    end: date | str,
) -> dict[str, Any]:
    """Count 7 days for each in-window inspection that establishes non-production.

    Missed inspections are absent from the table and contribute 0 days.
    Inspections with missing measurements or an unknown well type do not
    establish non-production and are listed separately.
    """
    history = production_history(conn, asset_id, start, end)
    not_producing = []
    unknown = []
    for reading in history["readings"]:
        if reading["status"] == STATUS_NOT_PRODUCING:
            not_producing.append(reading)
        elif reading["producing"] is None:
            unknown.append(reading)
    return {
        "asset_id": asset_id,
        "well_type": history["well_type"],
        "start": history["start"],
        "end": history["end"],
        "days": len(not_producing) * DAYS_PER_NON_PRODUCING_INSPECTION,
        "not_producing_count": len(not_producing),
        "not_producing_inspections": not_producing,
        "unknown_inspections": unknown,
        "inspection_count": len(history["readings"]),
    }


def top_wells_by_downtime(
    conn: sqlite3.Connection,
    area: str,
    start: date | str,
    end: date | str,
    limit: int | None = None,
) -> dict[str, Any]:
    _ensure_schema(conn)
    area_row = resolve_area(conn, area)
    asset_ids = area_asset_ids(conn, area) if area_row else []
    ranked = [downtime_for_well(conn, asset_id, start, end) for asset_id in asset_ids]
    ranked.sort(key=lambda item: (-item["days"], item["asset_id"]))
    if limit is not None:
        ranked = ranked[:limit]
    return {
        "area_id": area_row["area_id"] if area_row else None,
        "area_name": area_row["name"] if area_row else None,
        "start": parse_date(start).isoformat(),
        "end": parse_date(end).isoformat(),
        "wells": ranked,
    }


def _iter_candidate_inspections(
    conn: sqlite3.Connection,
    *,
    asset_id: str | None,
    area: str | None,
) -> Iterable[sqlite3.Row]:
    if asset_id:
        yield from conn.execute(
            "SELECT * FROM inspections WHERE asset_id = ? ORDER BY inspected_at, inspection_id",
            (asset_id,),
        )
        return
    if area:
        ids = area_asset_ids(conn, area)
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        yield from conn.execute(
            f"""
            SELECT * FROM inspections
            WHERE asset_id IN ({placeholders})
            ORDER BY inspected_at, inspection_id
            """,
            ids,
        )
        return
    yield from conn.execute("SELECT * FROM inspections ORDER BY inspected_at, inspection_id")


def effective_spills(
    conn: sqlite3.Connection,
    *,
    asset_id: str | None = None,
    area: str | None = None,
    start: date | str | None = None,
    end: date | str | None = None,
) -> list[dict[str, Any]]:
    """Return effective (correction-aware) spills. Original rows are not changed."""
    _ensure_schema(conn)
    results = []
    for row in _iter_candidate_inspections(conn, asset_id=asset_id, area=area):
        if start is not None and end is not None and not in_date_window(row["inspected_at"], start, end):
            continue
        spill = effective_spill(effective_inspection(conn, row))
        if spill["effective_spill_detected"]:
            results.append(spill)
    return results


def effective_spill_summary(
    conn: sqlite3.Connection,
    *,
    asset_id: str | None = None,
    area: str | None = None,
    start: date | str | None = None,
    end: date | str | None = None,
) -> dict[str, Any]:
    spills = effective_spills(conn, asset_id=asset_id, area=area, start=start, end=end)
    volumes = [item["effective_volume_bbl"] for item in spills if item["effective_volume_bbl"] is not None]
    classes: dict[str, int] = {}
    for item in spills:
        label = item["classification"] or "unknown_volume"
        classes[label] = classes.get(label, 0) + 1
    return {
        "asset_id": asset_id,
        "area": area,
        "start": parse_date(start).isoformat() if start else None,
        "end": parse_date(end).isoformat() if end else None,
        "spill_count": len(spills),
        "total_volume_bbl": round(sum(volumes), 1) if volumes else 0.0,
        "unknown_volume_count": sum(1 for item in spills if item["unknown_volume"]),
        "classifications": classes,
        "spills": spills,
    }


def maintenance_started_in_window(
    conn: sqlite3.Connection,
    *,
    asset_id: str | None = None,
    area: str | None = None,
    start: date | str,
    end: date | str,
) -> list[dict[str, Any]]:
    """Assign maintenance records to a window using started_at only."""
    _ensure_schema(conn)
    params: list[Any] = []
    clauses = ["started_at IS NOT NULL"]
    if asset_id:
        clauses.append("asset_id = ?")
        params.append(asset_id)
    elif area:
        ids = area_asset_ids(conn, area)
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        clauses.append(f"asset_id IN ({placeholders})")
        params.extend(ids)
    rows = conn.execute(
        f"""
        SELECT maintenance_id, asset_id, started_at, completed_at, downtime_hours
        FROM maintenance
        WHERE {' AND '.join(clauses)}
        ORDER BY started_at, maintenance_id
        """,
        params,
    ).fetchall()
    matches = []
    for row in rows:
        if in_date_window(row["started_at"], start, end):
            matches.append(dict(row))
    return matches
