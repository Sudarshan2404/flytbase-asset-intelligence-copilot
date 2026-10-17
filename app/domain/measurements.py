"""Discover stored measurements and rank oil-production volume when it exists."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from app.db import init_schema
from app.domain.dates import EVALUATION_TODAY, in_date_window, parse_date

INSPECTED_TABLES = ("inspections", "wells", "maintenance", "regulatory_events")

KNOWN_PRODUCTION_VOLUME_FIELDS = (
    "oil_volume_bbl",
    "oil_produced_bbl",
    "produced_oil_bbl",
    "production_volume_bbl",
    "production_bbl",
    "produced_bbl",
    "daily_oil_bbl",
    "oil_bbl",
    "production_total_bbl",
    "barrels_produced",
)

_OIL_QUANTITY_RE = re.compile(
    r"""
    (
        produced\s+the\s+most\s+oil
        | most\s+oil\s+(produced|in)
        | (how\s+much|total)\s+oil\s+(did|was|have|has|produced)
        | oil\s+(production|output|volume|totals?)
        | (production|produced)\s+(volume|total|barrels)
        | barrels\s+(of\s+oil\s+)?produced
        | which\s+well\s+produced
        | rank\s+wells\s+by\s+oil
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_NOT_OIL_QUANTITY_RE = re.compile(
    r"\b(spill|spills|producing oil right now|not producing|is\s+\S+\s+producing)\b",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def is_oil_quantity_question(text: str | None) -> bool:
    """True for barrels-produced / production-total questions, not status or spills."""
    if not text:
        return False
    if _NOT_OIL_QUANTITY_RE.search(text) and not _OIL_QUANTITY_RE.search(text):
        return False
    if re.search(r"\bspill", text, re.IGNORECASE):
        return False
    return bool(_OIL_QUANTITY_RE.search(text))


def infer_quantity_window(text: str | None) -> tuple[str, str]:
    year = str(EVALUATION_TODAY.year)
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    if not text:
        return start, end
    dates = _ISO_DATE_RE.findall(text)
    if len(dates) >= 2:
        first, second = dates[0], dates[1]
        if first <= second:
            return first, second
        return second, first
    if len(dates) == 1:
        return dates[0], dates[0]
    years = _YEAR_RE.findall(text)
    if years:
        chosen = years[-1]
        return f"{chosen}-01-01", f"{chosen}-12-31"
    return start, end


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _sample_raw_keys(conn: sqlite3.Connection, table: str, *, limit: int = 250) -> set[str]:
    if "raw_json" not in _table_columns(conn, table):
        return set()
    keys: set[str] = set()
    rows = conn.execute(f"SELECT raw_json FROM {table} LIMIT ?", (limit,)).fetchall()
    extra = conn.execute(
        f"SELECT raw_json FROM {table} ORDER BY rowid DESC LIMIT ?",
        (min(limit, 80),),
    ).fetchall()
    for row in [*rows, *extra]:
        raw = row[0] if not isinstance(row, sqlite3.Row) else row["raw_json"]
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            keys.update(str(key) for key in payload)
    return keys


def is_production_volume_field(name: str) -> bool:
    field = name.lower()
    if "spill" in field:
        return False
    if field in KNOWN_PRODUCTION_VOLUME_FIELDS:
        return True
    return "bbl" in field and any(token in field for token in ("oil", "produced", "production"))


def _classify_field(name: str) -> str:
    field = name.lower()
    if "spill" in field:
        return "spill"
    if is_production_volume_field(field):
        return "production_volume"
    if field in {"wellhead_temp_c", "ambient_temp_c", "delta_c"}:
        return "temperature"
    if field in {"producing", "recorded_status"}:
        return "status"
    return "other"


def available_measurements(conn: sqlite3.Connection) -> dict[str, Any]:
    """Inspect table columns and source raw_json keys. Does not invent fields."""
    init_schema(conn)
    fields: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for table in INSPECTED_TABLES:
        try:
            columns = _table_columns(conn, table)
        except sqlite3.Error:
            continue
        raw_keys = _sample_raw_keys(conn, table)
        for name in columns:
            if name in {"raw_json", "source_path"}:
                continue
            key = (table, name)
            if key in seen:
                continue
            seen.add(key)
            fields.append({"table": table, "field": name, "source": "column", "kind": _classify_field(name)})
        for name in sorted(raw_keys):
            key = (table, name)
            if key in seen:
                continue
            seen.add(key)
            fields.append({"table": table, "field": name, "source": "raw_json", "kind": _classify_field(name)})
    production_volume_fields = [item for item in fields if item["kind"] == "production_volume"]
    return {
        "tables_inspected": list(INSPECTED_TABLES),
        "fields": fields,
        "production_volume_fields": production_volume_fields,
        "spill_volume_fields": [item for item in fields if item["kind"] == "spill" and "bbl" in item["field"]],
        "temperature_fields": [item for item in fields if item["kind"] == "temperature"],
        "metric_available": bool(production_volume_fields),
        "message": (
            "Oil production volume fields are present."
            if production_volume_fields
            else (
                "Oil production volume is not recorded. Inspections have temperatures and "
                "spill_est_volume_bbl (spill estimates only). Producing status is derived "
                "from wellhead minus air temperature, not barrels produced."
            )
        ),
    }


def _volume_source(catalog: dict[str, Any]) -> dict[str, str] | None:
    for item in catalog.get("production_volume_fields") or []:
        if item["table"] == "inspections" and item["source"] == "column":
            return item
    for item in catalog.get("production_volume_fields") or []:
        if item["table"] == "inspections":
            return item
    fields = catalog.get("production_volume_fields") or []
    return fields[0] if fields else None


def _sum_from_column(
    conn: sqlite3.Connection,
    field: str,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", field):
        return []
    totals: dict[str, float] = {}
    rows = conn.execute(
        f"""
        SELECT asset_id, inspected_at, {field} AS volume
        FROM inspections
        WHERE {field} IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        if not in_date_window(row["inspected_at"], start, end):
            continue
        try:
            volume = float(row["volume"])
        except (TypeError, ValueError):
            continue
        asset_id = row["asset_id"]
        totals[asset_id] = totals.get(asset_id, 0.0) + volume
    return _rank(totals)


def _sum_from_raw_json(
    conn: sqlite3.Connection,
    field: str,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    totals: dict[str, float] = {}
    rows = conn.execute("SELECT asset_id, inspected_at, raw_json FROM inspections").fetchall()
    for row in rows:
        if not in_date_window(row["inspected_at"], start, end):
            continue
        try:
            payload = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get(field) in (None, ""):
            continue
        try:
            volume = float(payload[field])
        except (TypeError, ValueError):
            continue
        asset_id = row["asset_id"]
        totals[asset_id] = totals.get(asset_id, 0.0) + volume
    return _rank(totals)


def _rank(totals: dict[str, float]) -> list[dict[str, Any]]:
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return [{"asset_id": asset_id, "total_bbl": round(total, 3)} for asset_id, total in ranked]


def oil_production_totals(
    conn: sqlite3.Connection,
    start: str,
    end: str,
) -> dict[str, Any]:
    """Sum produced oil by well when a production-volume field exists. Never uses spill or temperature."""
    init_schema(conn)
    parse_date(start)
    parse_date(end)
    catalog = available_measurements(conn)
    source = _volume_source(catalog)
    if source is None:
        return {
            "metric": "oil_production_volume_bbl",
            "metric_available": False,
            "start": start,
            "end": end,
            "wells": [],
            "field": None,
            "source": None,
            "tables_inspected": catalog["tables_inspected"],
            "available_measurements": [
                f"{item['table']}.{item['field']}"
                for item in catalog["fields"]
                if item["table"] == "inspections" and item["field"]
                in {"wellhead_temp_c", "ambient_temp_c", "spill_est_volume_bbl", "spill_detected"}
            ],
            "message": (
                "Oil production volume is not recorded in this dataset, so no well can be "
                "ranked by oil produced. Inspections store wellhead_temp_c, ambient_temp_c, "
                "and spill_est_volume_bbl (spill estimates only). Producing status is derived "
                "from temperature, not barrels."
            ),
            "source_path": "inspections",
            "excerpt": catalog["message"],
        }

    if source["source"] == "column" and source["table"] == "inspections":
        wells = _sum_from_column(conn, source["field"], start, end)
    elif source["table"] == "inspections":
        wells = _sum_from_raw_json(conn, source["field"], start, end)
    else:
        return {
            "metric": "oil_production_volume_bbl",
            "metric_available": False,
            "start": start,
            "end": end,
            "wells": [],
            "field": source["field"],
            "source": f"{source['table']}.{source['source']}",
            "tables_inspected": catalog["tables_inspected"],
            "message": (
                f"Found {source['field']} on {source['table']}, but production totals are "
                "only calculated from inspection measurements. No inspection production "
                "volume is available."
            ),
            "source_path": source["table"],
        }

    top = wells[0]["asset_id"] if wells else None
    return {
        "metric": "oil_production_volume_bbl",
        "metric_available": True,
        "start": start,
        "end": end,
        "field": source["field"],
        "source": f"{source['table']}.{source['source']}",
        "tables_inspected": catalog["tables_inspected"],
        "wells": wells,
        "top_asset_id": top,
        "message": (
            f"{top} produced the most oil between {start} and {end}: "
            f"{wells[0]['total_bbl']} bbl, summed from {source['table']}.{source['field']}."
            if wells
            else (
                f"Oil volume is recorded in {source['table']}.{source['field']}, but no "
                f"values fall between {start} and {end}."
            )
        ),
        "source_path": "inspections",
    }
