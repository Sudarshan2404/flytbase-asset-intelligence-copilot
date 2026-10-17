from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Iterable

from app.db import init_schema
from app.domain.corrections import effective_inspection, effective_spill
from app.domain.dates import EVALUATION_TODAY, in_date_window, parse_date
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
    inspector = conn.execute(
        "SELECT name FROM users WHERE user_id = ?",
        (latest["inspector_user_id"],),
    ).fetchone()
    return {
        "asset_id": asset_id,
        "well_type": well["well_type"],
        "recorded_status": well["recorded_status"],
        "recorded_status_authoritative": False,
        "inspection_id": view.inspection_id,
        "inspected_at": view.inspected_at,
        "inspector_user_id": latest["inspector_user_id"],
        "inspector_name": inspector["name"] if inspector else None,
        "method": latest["method"],
        "notes": latest.get("notes"),
        "wellhead_temp_c": view.wellhead_temp_c,
        "ambient_temp_c": view.ambient_temp_c,
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


def _month_keys(start: date, end: date) -> list[str]:
    months = []
    current = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while current <= last:
        months.append(current.isoformat()[:7])
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def inspection_heatmap(
    conn: sqlite3.Connection,
    *,
    area: str | None,
    start: date | str,
    end: date | str,
    well_limit: int = 12,
) -> dict[str, Any]:
    """Inspection counts by well and month. Missing months are marked, not zeroed."""
    _ensure_schema(conn)
    start_date = parse_date(start)
    end_date = parse_date(end)
    months = _month_keys(start_date, end_date)
    if area:
        asset_ids = area_asset_ids(conn, area)
    else:
        asset_ids = [row["asset_id"] for row in conn.execute("SELECT asset_id FROM wells ORDER BY asset_id")]
    if not asset_ids:
        return {
            "title": "Inspection activity",
            "description": f"{start_date} to {end_date}",
            "rows_axis": [],
            "cols_axis": months,
            "cells": [],
            "cell_meaning": "Inspection count for that well and month. Missing is not zero.",
        }
    placeholders = ", ".join("?" for _ in asset_ids)
    ranked = conn.execute(
        f"""
        SELECT asset_id, COUNT(*) AS n
        FROM inspections
        WHERE asset_id IN ({placeholders})
          AND inspected_at IS NOT NULL
          AND substr(inspected_at, 1, 10) >= ?
          AND substr(inspected_at, 1, 10) <= ?
        GROUP BY asset_id
        ORDER BY n DESC, asset_id
        LIMIT ?
        """,
        [*asset_ids, start_date.isoformat(), end_date.isoformat(), well_limit],
    ).fetchall()
    selected = [row["asset_id"] for row in ranked]
    counts: dict[tuple[str, str], int] = {}
    if selected:
        selected_ph = ", ".join("?" for _ in selected)
        for row in conn.execute(
            f"""
            SELECT asset_id, substr(inspected_at, 1, 7) AS month, COUNT(*) AS n
            FROM inspections
            WHERE asset_id IN ({selected_ph})
              AND inspected_at IS NOT NULL
              AND substr(inspected_at, 1, 10) >= ?
              AND substr(inspected_at, 1, 10) <= ?
            GROUP BY asset_id, month
            """,
            [*selected, start_date.isoformat(), end_date.isoformat()],
        ):
            counts[(row["asset_id"], row["month"])] = int(row["n"])
    cells = []
    for asset_id in selected:
        for month in months:
            if (asset_id, month) in counts:
                cells.append({"row": asset_id, "col": month, "value": counts[(asset_id, month)], "missing": False})
            else:
                cells.append({"row": asset_id, "col": month, "value": None, "missing": True})
    return {
        "title": "Inspection activity by well and month",
        "description": f"{start_date} to {end_date}",
        "rows_axis": selected,
        "cols_axis": months,
        "cells": cells,
        "cell_meaning": "Inspection count for that well and month. Missing is not zero.",
    }


def wells_with_coordinates(
    conn: sqlite3.Connection,
    *,
    area: str | None = None,
    asset_id: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    _ensure_schema(conn)
    clauses = ["lat IS NOT NULL", "lon IS NOT NULL"]
    params: list[Any] = []
    if asset_id:
        clauses.append("asset_id = ?")
        params.append(asset_id)
    elif area:
        ids = area_asset_ids(conn, area)
        if not ids:
            return {"title": "Well locations", "markers": [], "skipped_without_coordinates": 0}
        placeholders = ", ".join("?" for _ in ids)
        clauses.append(f"asset_id IN ({placeholders})")
        params.extend(ids)
    rows = conn.execute(
        f"""
        SELECT asset_id, name, area_id, lat, lon, well_type
        FROM wells
        WHERE {' AND '.join(clauses)}
        ORDER BY asset_id
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    markers = []
    for row in rows:
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (TypeError, ValueError):
            continue
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            continue
        markers.append(
            {
                "asset_id": row["asset_id"],
                "label": row["name"] or row["asset_id"],
                "lat": lat,
                "lon": lon,
                "area_id": row["area_id"],
                "detail": row["well_type"],
            }
        )
    skipped = 0
    if asset_id:
        well = _get_well(conn, asset_id)
        if well is not None and (well["lat"] is None or well["lon"] is None):
            skipped = 1
    return {
        "title": "Well locations",
        "markers": markers,
        "skipped_without_coordinates": skipped,
    }


def _maintenance_still_open(completed_at: str | None, as_of: date | None = None) -> bool:
    """In-progress jobs store a planned `completed_at` that can be after today."""
    if completed_at is None or str(completed_at).strip() == "":
        return True
    try:
        return parse_date(completed_at) > parse_date(as_of or EVALUATION_TODAY)
    except ValueError:
        return True


def _open_maintenance(conn: sqlite3.Connection, asset_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT maintenance_id, issue, started_at, completed_at, equipment_kind
        FROM maintenance
        WHERE asset_id = ?
        ORDER BY started_at DESC, maintenance_id
        """,
        (asset_id,),
    )
    return [dict(row) for row in rows if _maintenance_still_open(row["completed_at"])]


def non_producing_in_area(conn: sqlite3.Connection, area: str) -> dict[str, Any]:
    """Latest-inspection non-production for an area, with recorded reasons when they exist."""
    _ensure_schema(conn)
    area_row = resolve_area(conn, area)
    wells = []
    for asset_id in area_asset_ids(conn, area):
        status = current_production_status(conn, asset_id)
        if status.get("producing") is not False:
            continue
        open_jobs = _open_maintenance(conn, asset_id)
        notes = status.get("notes") or ""
        if open_jobs:
            reason = (
                f"Open repair {open_jobs[0]['maintenance_id']}: {open_jobs[0]['issue']}, "
                f"started {str(open_jobs[0]['started_at'] or '')[:10]}"
            )
            recorded_reason = True
        else:
            reason = "No recorded reason."
            if notes:
                reason += f' The inspector noted "{notes.strip()}"'
            recorded_reason = False
        wells.append(
            {
                **{key: status.get(key) for key in (
                    "asset_id",
                    "well_type",
                    "delta_c",
                    "recorded_status",
                    "inspection_id",
                    "inspected_at",
                    "notes",
                )},
                "reason": reason,
                "has_recorded_reason": recorded_reason,
                "open_maintenance": open_jobs,
            }
        )
    wells.sort(key=lambda item: item["asset_id"])
    return {
        "area_id": area_row["area_id"] if area_row else None,
        "area_name": area_row["name"] if area_row else area,
        "wells": wells,
    }


def spills_with_coordinates(
    conn: sqlite3.Connection,
    *,
    area: str | None,
    start: date | str,
    end: date | str,
) -> dict[str, Any]:
    spills = effective_spills(conn, area=area, start=start, end=end)
    markers = []
    skipped = 0
    for spill in spills:
        well = conn.execute(
            "SELECT asset_id, name, lat, lon FROM wells WHERE asset_id = ?",
            (spill["asset_id"],),
        ).fetchone()
        if well is None or well["lat"] is None or well["lon"] is None:
            skipped += 1
            continue
        try:
            lat = float(well["lat"])
            lon = float(well["lon"])
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            skipped += 1
            continue
        markers.append(
            {
                "asset_id": spill["asset_id"],
                "label": well["name"] or spill["asset_id"],
                "lat": lat,
                "lon": lon,
                "record_id": spill["inspection_id"],
                "detail": f"{spill.get('effective_volume_bbl')} bbl on {str(spill.get('inspected_at') or '')[:10]}",
                "inspected_at": spill.get("inspected_at"),
                "effective_volume_bbl": spill.get("effective_volume_bbl"),
            }
        )
    return {
        "title": "Effective spills with stored coordinates",
        "markers": markers,
        "spills": spills,
        "skipped_without_coordinates": skipped,
    }


def spill_heatmap_by_area(
    conn: sqlite3.Connection,
    *,
    start: date | str,
    end: date | str,
) -> dict[str, Any]:
    """Effective spill counts by area name and month. Empty months are zero, not missing."""
    _ensure_schema(conn)
    start_date = parse_date(start)
    end_date = parse_date(end)
    months = _month_keys(start_date, end_date)
    areas = [dict(row) for row in conn.execute("SELECT area_id, name FROM areas ORDER BY name")]
    counts: dict[tuple[str, str], int] = {}
    for spill in effective_spills(conn, start=start_date, end=end_date):
        well = conn.execute("SELECT area_id FROM wells WHERE asset_id = ?", (spill["asset_id"],)).fetchone()
        if well is None:
            continue
        month = str(spill.get("inspected_at") or "")[:7]
        counts[(well["area_id"], month)] = counts.get((well["area_id"], month), 0) + 1
    cells = []
    rows_axis = []
    for area in areas:
        rows_axis.append(area["name"] or area["area_id"])
        for month in months:
            cells.append(
                {
                    "row": area["name"] or area["area_id"],
                    "col": month,
                    "value": counts.get((area["area_id"], month), 0),
                    "missing": False,
                }
            )
    return {
        "title": "Effective spills by area and month",
        "description": f"{start_date} to {end_date}",
        "rows_axis": rows_axis,
        "cols_axis": months,
        "cells": cells,
        "cell_meaning": "Count of effective (correction-aware) spills. Zero means no spill that month.",
    }


def maintenance_costs(
    conn: sqlite3.Connection,
    *,
    asset_ids: list[str],
    start: date | str,
    end: date | str,
) -> dict[str, Any]:
    _ensure_schema(conn)
    start_date = parse_date(start)
    end_date = parse_date(end)
    wells = []
    for asset_id in asset_ids:
        row = conn.execute(
            """
            SELECT ROUND(COALESCE(SUM(cost_total_usd), 0), 0) AS total
            FROM maintenance
            WHERE asset_id = ?
              AND started_at IS NOT NULL
              AND substr(started_at, 1, 10) >= ?
              AND substr(started_at, 1, 10) <= ?
            """,
            (asset_id, start_date.isoformat(), end_date.isoformat()),
        ).fetchone()
        wells.append({"asset_id": asset_id, "cost_usd": int(row["total"] or 0)})
    return {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "wells": wells,
        "total_usd": sum(item["cost_usd"] for item in wells),
    }
