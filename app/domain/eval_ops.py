"""Deterministic queries for the 600-well test-dataset evaluation questions."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from app.db import init_schema
from app.domain.corrections import effective_inspection, effective_spill
from app.domain.dates import EVALUATION_TODAY, in_date_window, parse_date, parse_datetime
from app.domain.measurements import available_measurements
from app.domain.production import production_from_temps
from app.domain.queries import current_production_status, latest_inspection

RECALLED_LOTS = {"LOT-2409-A", "LOT-2409-C", "LOT-2410-B"}
GAS_RE = re.compile(r"gas smell|smelled gas|rotten-egg|gas odor|h2s monitor", re.I)
BYPASS_IN_RE = re.compile(r"bypass in place|bypass installed|put the bypass in|had the bypass installed", re.I)
BYPASS_OUT_RE = re.compile(r"bypass (out|removed)|took the bypass out", re.I)
SHUT_IN_RE = re.compile(r"\b(shut in|shut-in|shut down|offline)\b", re.I)
RESTART_RE = re.compile(r"\b(back online|restarted|running again|returned to production|started back)\b", re.I)
H2S_READING_RE = re.compile(r"(\d+(?:\.\d+)?)\s*ppm", re.I)
PARTS_RE = re.compile(r"\*\*Parts installed:\*\*\s*(.+)")
LOT_RE = re.compile(r"lot\s+(LOT-[A-Z0-9-]+)", re.I)
SERIAL_RE = re.compile(r"serial\s+(SN-[A-Z0-9-]+)", re.I)
WARNING_RE = re.compile(
    r"knock|rattle|rattl|vibrat|noisy|noise|grinding|hot|overheat|smell|leaking|leak|"
    r"not right|behav|sound|thump|squeal|hunt|amp|trip|ground fault|slowed|laboring",
    re.I,
)
WAIT_PATTERNS = (
    ("parts", re.compile(r"\b(parts?|back-?order|valve on order|awaiting parts|waiting on parts)\b", re.I)),
    ("weather", re.compile(r"\b(weather|storm|ice|freeze|wind hold|lightning)\b", re.I)),
    ("contractors", re.compile(r"\b(contractor|workover rig|third[- ]party|vendor)\b", re.I)),
    ("crews", re.compile(r"\b(crew|manpower|personnel|no crew|waiting on crew)\b", re.I)),
)


def _ensure(conn: sqlite3.Connection) -> None:
    init_schema(conn)


def _well_map(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {row["asset_id"]: row for row in conn.execute("SELECT * FROM wells")}


def _area_name(conn: sqlite3.Connection, area_id: str | None) -> str | None:
    if not area_id:
        return None
    row = conn.execute("SELECT name FROM areas WHERE area_id = ?", (area_id,)).fetchone()
    return row["name"] if row else area_id


def reservoir_pressure_map(conn: sqlite3.Connection) -> dict[str, Any]:
    catalog = available_measurements(conn)
    pressure_fields = [
        item
        for item in catalog["fields"]
        if "pressure" in item["field"].lower() and "spill" not in item["field"].lower()
    ]
    return {
        "metric": "reservoir_pressure",
        "metric_available": False,
        "fields_found": pressure_fields,
        "tables_inspected": catalog["tables_inspected"],
        "wells": [],
        "message": (
            "Reservoir pressure is not recorded in the inspection or well records. "
            "Inspections store wellhead_temp_c, ambient_temp_c, and spill estimates only. "
            "Mentions of pressure in maintenance reports are flowline/valve tests, not reservoir pressure. "
            "No field map can be drawn without inventing values."
        ),
        "source_path": "inspections",
    }


def gas_response_compliance(conn: sqlite3.Connection, start: str = "2026-11-01") -> dict[str, Any]:
    _ensure(conn)
    wells = _well_map(conn)
    notes = conn.execute(
        """
        SELECT note_id, asset_id, created_at, text, author_user_id
        FROM context_notes
        WHERE note_type = 'operator_note' AND created_at >= ?
        ORDER BY created_at, note_id
        """,
        (start,),
    ).fetchall()
    rows = []
    for note in notes:
        if not GAS_RE.search(note["text"] or ""):
            continue
        reported = parse_datetime(note["created_at"])
        deadline_hours = 12 if parse_date(note["created_at"]) >= date(2027, 1, 1) else 24
        deadline_at = reported + timedelta(hours=deadline_hours)
        response = _first_field_response(conn, note["asset_id"], note["created_at"])
        hours = None
        met = "Missed"
        response_id = None
        response_at = None
        if response:
            response_at = response["at"]
            response_id = response["record_id"]
            hours = round((parse_datetime(response_at) - reported).total_seconds() / 3600, 2)
            met = "Met" if parse_datetime(response_at) <= deadline_at else "Missed"
        well = wells.get(note["asset_id"])
        rows.append(
            {
                "asset_id": note["asset_id"],
                "area": _area_name(conn, well["area_id"] if well else None),
                "reported_at": note["created_at"],
                "note_id": note["note_id"],
                "response_record_id": response_id,
                "response_at": response_at,
                "hours_to_response": hours,
                "deadline_hours": deadline_hours,
                "deadline_at": deadline_at.isoformat().replace("+00:00", "Z"),
                "met": met,
                "text": note["text"],
            }
        )
    return {
        "start": start,
        "rule": "OPS-1: 24h before 2027-01-01, 12h from 2027-01-01; manual inspection or maintenance after the note.",
        "rows": rows,
        "source_path": "fleet/sops/operations-sop-rev2.md",
    }


def _first_field_response(conn: sqlite3.Connection, asset_id: str, after: str) -> dict[str, str] | None:
    job = conn.execute(
        """
        SELECT maintenance_id AS record_id, started_at AS at
        FROM maintenance
        WHERE asset_id = ? AND started_at > ?
        ORDER BY started_at, maintenance_id
        LIMIT 1
        """,
        (asset_id, after),
    ).fetchone()
    inspection = conn.execute(
        """
        SELECT inspection_id AS record_id, inspected_at AS at
        FROM inspections
        WHERE asset_id = ? AND inspected_at > ? AND method = 'manual'
        ORDER BY inspected_at, inspection_id
        LIMIT 1
        """,
        (asset_id, after),
    ).fetchone()
    candidates = [dict(row) for row in (job, inspection) if row is not None]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item["at"])
    return candidates[0]


def _moc_approvals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    docs = conn.execute(
        "SELECT source_path, document_id, content FROM source_documents WHERE source_path LIKE '%moc-%'"
    ).fetchall()
    approvals = []
    for doc in docs:
        wells = re.findall(r"WELL-\d{4,}", doc["content"] or "")
        expires = re.search(r"expires on (\d{1,2} \w+ 20\d{2})", doc["content"] or "", re.I)
        effective = re.search(r"Effective from (\d{1,2} \w+ 20\d{2})", doc["content"] or "", re.I)
        filename = doc["source_path"].replace("\\", "/").split("/")[-1].replace(".md", "")
        memo_id = filename.upper()
        if "extends MOC-2026-031" in (doc["content"] or ""):
            replaces = "MOC-2026-031"
        else:
            replaces = None
        approvals.append(
            {
                "memo_id": memo_id,
                "source_path": doc["source_path"],
                "wells": sorted(set(wells)),
                "expires_on": _parse_long_date(expires.group(1)) if expires else None,
                "effective_from": _parse_long_date(effective.group(1)) if effective else None,
                "replaces": replaces,
            }
        )
    return approvals


def _parse_long_date(text: str) -> str:
    return parse_date(date_from_long(text)).isoformat()


def date_from_long(text: str) -> date:
    text = text.strip()
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date: {text}")


def flowline_bypasses(conn: sqlite3.Connection, as_of: str | None = None) -> dict[str, Any]:
    _ensure(conn)
    today = parse_date(as_of or EVALUATION_TODAY)
    wells = _well_map(conn)
    notes = conn.execute(
        """
        SELECT note_id, asset_id, created_at, text
        FROM context_notes
        WHERE note_type = 'operator_note'
        ORDER BY created_at, note_id
        """
    ).fetchall()
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for note in notes:
        text = note["text"] or ""
        if BYPASS_OUT_RE.search(text):
            kind = "out"
        elif BYPASS_IN_RE.search(text):
            kind = "in"
        else:
            continue
        events[note["asset_id"]].append({"kind": kind, "at": note["created_at"], "note_id": note["note_id"], "text": text})

    approvals = _moc_approvals(conn)
    latest_by_well: dict[str, dict[str, Any]] = {}
    for approval in approvals:
        for asset_id in approval["wells"]:
            current = latest_by_well.get(asset_id)
            if current is None or (approval["expires_on"] or "") >= (current["expires_on"] or ""):
                latest_by_well[asset_id] = approval

    rows = []
    for asset_id, timeline in events.items():
        open_at = None
        open_note = None
        for event in timeline:
            if event["kind"] == "in":
                open_at = event["at"]
                open_note = event["note_id"]
            elif event["kind"] == "out" and open_at:
                rows.append(_bypass_row(conn, wells, asset_id, open_at, event["at"], latest_by_well.get(asset_id), today, open_note))
                open_at = None
        if open_at:
            rows.append(_bypass_row(conn, wells, asset_id, open_at, None, latest_by_well.get(asset_id), today, open_note))
    rows.sort(key=lambda item: (item["area"] or "", item["asset_id"], item["bypass_in"]))
    current = [row for row in rows if row["still_in"]]
    return {
        "as_of": today.isoformat(),
        "current": current,
        "all": rows,
        "source_path": "fleet/sops/operations-sop-rev2.md",
    }


def _bypass_row(conn, wells, asset_id, start, end, approval, today, note_id) -> dict[str, Any]:
    well = wells.get(asset_id)
    still_in = end is None or parse_date(end) > today
    end_display = "still in" if still_in and end is None else (end[:10] if end else "still in")
    if approval is None:
        status = "Not approved"
        valid_until = None
        memo_id = None
    else:
        memo_id = approval["memo_id"]
        valid_until = approval["expires_on"]
        expiry = parse_date(valid_until) if valid_until else None
        last_day = parse_date(end) if end else today
        if expiry and last_day > expiry:
            status = "Expired"
        else:
            status = "Allowed"
    return {
        "asset_id": asset_id,
        "area": _area_name(conn, well["area_id"] if well else None),
        "bypass_in": start[:10],
        "bypass_out": end_display,
        "still_in": still_in and end is None,
        "approval": memo_id,
        "approval_valid_until": valid_until,
        "status": status,
        "note_id": note_id,
    }


def regulator_letter_spills(conn: sqlite3.Connection) -> dict[str, Any]:
    _ensure(conn)
    wells = _well_map(conn)
    target_areas = {"NE-07", "NE-08"}
    rows = []
    for well in wells.values():
        if well["area_id"] not in target_areas:
            continue
        inspections = conn.execute(
            "SELECT * FROM inspections WHERE asset_id = ? ORDER BY inspected_at",
            (well["asset_id"],),
        ).fetchall()
        for row in inspections:
            if not in_date_window(row["inspected_at"], "2026-06-01", EVALUATION_TODAY):
                continue
            spill = effective_spill(effective_inspection(conn, row))
            if not spill.get("effective_spill_detected"):
                continue
            volume = spill.get("effective_volume_bbl")
            if volume is None or float(volume) < 5:
                continue
            job = conn.execute(
                """
                SELECT maintenance_id, report_file FROM maintenance
                WHERE asset_id = ? AND triggered_by_inspection_id = ?
                ORDER BY started_at LIMIT 1
                """,
                (well["asset_id"], row["inspection_id"]),
            ).fetchone()
            report_text = ""
            if job and job["report_file"]:
                path = job["report_file"].replace("\\", "/")
                doc = conn.execute(
                    "SELECT content FROM source_documents WHERE source_path LIKE ?",
                    (f"%{path.split('/')[-1]}",),
                ).fetchone()
                if doc:
                    report_text = doc["content"] or ""
            compliant = "Soil samples sent to the lab." in report_text
            rows.append(
                {
                    "asset_id": well["asset_id"],
                    "area": _area_name(conn, well["area_id"]),
                    "inspection_id": row["inspection_id"],
                    "inspected_at": row["inspected_at"],
                    "volume_bbl": volume,
                    "clean_up_job": job["maintenance_id"] if job else None,
                    "compliant": "Yes" if compliant else "No",
                    "reason": (
                        "Clean-up report contains 'Soil samples sent to the lab.'"
                        if compliant
                        else "No lab-sample sentence in the clean-up report (field readings do not count)."
                    ),
                }
            )
    return {
        "letter": "fleet/sops/regulator-letter-2026-11-20.md",
        "scope": "Reportable spills in Northeast Block 7 and 8 since 2026-06-01",
        "rows": rows,
    }


def status_disagreements(conn: sqlite3.Connection) -> dict[str, Any]:
    _ensure(conn)
    wells = _well_map(conn)
    rows = []
    for well in wells.values():
        inspection = latest_inspection(conn, well["asset_id"])
        if inspection is None:
            continue
        status = current_production_status(conn, well["asset_id"])
        notes = conn.execute(
            """
            SELECT note_id, created_at, text FROM context_notes
            WHERE asset_id = ? AND note_type = 'operator_note'
            ORDER BY created_at DESC, note_id DESC
            """,
            (well["asset_id"],),
        ).fetchall()
        latest_status_note = None
        asserted = None
        for note in notes:
            text = note["text"] or ""
            if SHUT_IN_RE.search(text):
                latest_status_note = note
                asserted = False
                break
            if RESTART_RE.search(text):
                latest_status_note = note
                asserted = True
                break
        if latest_status_note is None:
            continue
        inspection_producing = status.get("producing")
        if asserted is True and inspection_producing is True:
            continue
        if asserted is False and inspection_producing is False:
            continue
        note_time = latest_status_note["created_at"]
        insp_time = inspection["inspected_at"]
        if asserted is False and parse_datetime(note_time) > parse_datetime(insp_time):
            real = "Not producing oil"
            why = "OPS-3: later shut-in operator note overrides the inspection."
            unconfirmed = False
        elif asserted is True and parse_datetime(note_time) > parse_datetime(insp_time):
            real = "Producing oil"
            why = "OPS-3: later restart note; unconfirmed until an inspection."
            unconfirmed = True
        else:
            real = "Producing oil" if inspection_producing else "Not producing oil"
            why = "Latest inspection is newer than the operator status note."
            unconfirmed = False
        rows.append(
            {
                "asset_id": well["asset_id"],
                "area": _area_name(conn, well["area_id"]),
                "inspection_id": inspection["inspection_id"],
                "inspection_at": insp_time,
                "inspection_shows": "Producing oil" if inspection_producing else "Not producing oil",
                "note_id": latest_status_note["note_id"],
                "note_at": note_time,
                "note_text": latest_status_note["text"],
                "status_now": f"{real}{', unconfirmed' if unconfirmed else ''}",
                "reason": why,
            }
        )
    return {"rows": rows, "source_path": "fleet/sops/operations-sop-rev2.md"}


def operator_warning_wells(conn: sqlite3.Connection, start: str, end: str) -> dict[str, Any]:
    _ensure(conn)
    wells = _well_map(conn)
    notes = conn.execute(
        """
        SELECT note_id, asset_id, created_at, text
        FROM context_notes
        WHERE note_type = 'operator_note' AND created_at >= ? AND created_at <= ?
        ORDER BY created_at DESC
        """,
        (start, end + "T23:59:59Z"),
    ).fetchall()
    by_well: dict[str, list] = defaultdict(list)
    for note in notes:
        if WARNING_RE.search(note["text"] or "") and not GAS_RE.search(note["text"] or ""):
            by_well[note["asset_id"]].append(note)
    ranked = []
    for asset_id, well_notes in by_well.items():
        last_repair = conn.execute(
            """
            SELECT maintenance_id, started_at, completed_at, issue
            FROM maintenance
            WHERE asset_id = ? AND maintenance_type = 'corrective'
              AND IFNULL(issue, '') NOT LIKE '%spill%'
            ORDER BY started_at DESC LIMIT 1
            """,
            (asset_id,),
        ).fetchone()
        latest_note = well_notes[0]
        if last_repair and parse_datetime(last_repair["started_at"]) >= parse_datetime(latest_note["created_at"]):
            continue
        well = wells.get(asset_id)
        status = current_production_status(conn, asset_id)
        ranked.append(
            {
                "asset_id": asset_id,
                "area": _area_name(conn, well["area_id"] if well else None),
                "note_id": latest_note["note_id"],
                "note_at": latest_note["created_at"],
                "quote": latest_note["text"],
                "other_signs": (
                    f"Latest inspection {status.get('inspection_id')} producing={status.get('producing')}"
                ),
                "last_repair": last_repair["maintenance_id"] if last_repair else None,
                "last_repair_at": last_repair["started_at"] if last_repair else None,
                "note_count": len(well_notes),
            }
        )
    ranked.sort(key=lambda item: (-item["note_count"], item["note_at"]), reverse=False)
    ranked.sort(key=lambda item: (-item["note_count"], item["asset_id"]))
    return {"start": start, "end": end, "rows": ranked[:10]}


def recalled_pumps(conn: sqlite3.Connection, team_id: str | None = None) -> dict[str, Any]:
    _ensure(conn)
    wells = _well_map(conn)
    docs = conn.execute(
        "SELECT related_maintenance_id, asset_id, content, source_path FROM source_documents WHERE doc_type = 'maintenance_report'"
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for doc in docs:
        match = PARTS_RE.search(doc["content"] or "")
        if not match:
            continue
        line = match.group(1)
        lot_match = LOT_RE.search(line)
        if not lot_match:
            continue
        job = conn.execute(
            "SELECT maintenance_id, started_at, cost_total_usd FROM maintenance WHERE maintenance_id = ?",
            (doc["related_maintenance_id"],),
        ).fetchone()
        started = job["started_at"] if job else ""
        current = latest.get(doc["asset_id"])
        if current is None or started >= current["installed_at"]:
            serial = SERIAL_RE.search(line)
            latest[doc["asset_id"]] = {
                "asset_id": doc["asset_id"],
                "lot": lot_match.group(1).upper(),
                "serial": serial.group(1) if serial else None,
                "maintenance_id": doc["related_maintenance_id"],
                "installed_at": started,
                "cost_usd": job["cost_total_usd"] if job else None,
                "source_path": doc["source_path"],
            }
    users = {row["user_id"]: row for row in conn.execute("SELECT user_id, team_id, name FROM users")}
    shared = conn.execute("SELECT asset_id, text, note_id, author_user_id FROM shared_notes").fetchall()
    replacements = {}
    ignored_hearsay = []
    for row in shared:
        if "replac" not in (row["text"] or "").lower() and "swapped" not in (row["text"] or "").lower():
            continue
        well = wells.get(row["asset_id"])
        author = users.get(row["author_user_id"])
        well_team = well["team_id"] if well else None
        author_team = author["team_id"] if author else None
        if well_team and author_team and author_team != well_team:
            ignored_hearsay.append(
                {
                    "asset_id": row["asset_id"],
                    "note_id": row["note_id"],
                    "author_user_id": row["author_user_id"],
                    "reason": "Replacement note is from another team, so it is not treated as an operational fact for this well.",
                }
            )
            continue
        replacements[row["asset_id"]] = row
    rows = []
    for asset_id, item in latest.items():
        if item["lot"] not in RECALLED_LOTS:
            continue
        if asset_id in replacements:
            continue
        well = wells.get(asset_id)
        if team_id and well and well["team_id"] != team_id:
            continue
        status = current_production_status(conn, asset_id)
        last_repair = conn.execute(
            """
            SELECT maintenance_id, started_at, completed_at
            FROM maintenance
            WHERE asset_id = ?
            ORDER BY started_at DESC LIMIT 1
            """,
            (asset_id,),
        ).fetchone()
        rows.append(
            {
                **item,
                "area": _area_name(conn, well["area_id"] if well else None),
                "team_id": well["team_id"] if well else None,
                "producing": status.get("producing"),
                "status_now": "Producing oil" if status.get("producing") else "Not producing oil" if status.get("producing") is False else status.get("status"),
                "last_repair_id": last_repair["maintenance_id"] if last_repair else None,
                "last_repair_at": last_repair["started_at"] if last_repair else None,
                "source": f"Parts installed on {item['maintenance_id']}",
            }
        )
    rows.sort(key=lambda item: (item["area"] or "", item["asset_id"]))
    return {
        "recall": "HPC-RN-2026-07",
        "lots": sorted(RECALLED_LOTS),
        "rows": rows,
        "excluded_shared_replacements": [row["asset_id"] for row in replacements.values()],
        "ignored_hearsay": ignored_hearsay,
    }


def extra_shut_in(conn: sqlite3.Connection, start: str = "2026-01-01", end: str = "2026-12-31") -> dict[str, Any]:
    """Extra days = calendar span of the job minus downtime_hours/24. Reason from the report, else unknown."""
    _ensure(conn)
    wells = _well_map(conn)
    jobs = conn.execute(
        """
        SELECT * FROM maintenance
        WHERE started_at >= ? AND started_at <= ? AND downtime_hours IS NOT NULL
        """,
        (start, end + "T23:59:59Z"),
    ).fetchall()
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for job in jobs:
        start_d = parse_date(job["started_at"])
        end_raw = job["completed_at"]
        if not end_raw:
            continue
        end_d = parse_date(end_raw)
        if end_d > EVALUATION_TODAY:
            end_d = EVALUATION_TODAY
        calendar_days = max(0, (end_d - start_d).days)
        work_days = float(job["downtime_hours"] or 0) / 24.0
        extra = round(calendar_days - work_days, 2)
        if extra <= 0:
            continue
        reason = "unknown"
        if job["report_file"]:
            doc = conn.execute(
                "SELECT content FROM source_documents WHERE related_maintenance_id = ?",
                (job["maintenance_id"],),
            ).fetchone()
            text = (doc["content"] if doc else "") or ""
            for name, pattern in WAIT_PATTERNS:
                if pattern.search(text):
                    reason = name
                    break
        well = wells.get(job["asset_id"])
        area = _area_name(conn, well["area_id"] if well else None) or "Unknown"
        buckets[(area, reason)].append(extra)
    rows = []
    for (area, reason), extras in sorted(buckets.items()):
        rows.append(
            {
                "area": area,
                "reason": reason,
                "jobs": len(extras),
                "share": None,
                "typical_extra_days": round(median(extras), 2),
            }
        )
    by_area: dict[str, int] = defaultdict(int)
    for row in rows:
        by_area[row["area"]] += row["jobs"]
    for row in rows:
        total = by_area[row["area"]] or 1
        row["share"] = round(row["jobs"] / total, 3)
    return {
        "definition": (
            "Extra days = calendar days from started_at to completed_at (capped at evaluation today) "
            "minus downtime_hours/24. Reasons come from the repair report; otherwise unknown."
        ),
        "start": start,
        "end": end,
        "rows": rows,
    }


def region_temperatures(conn: sqlite3.Connection, start: str, end: str) -> dict[str, Any]:
    _ensure(conn)
    wells = _well_map(conn)
    rows = conn.execute(
        """
        SELECT asset_id, inspected_at, wellhead_temp_c, ambient_temp_c
        FROM inspections
        WHERE inspected_at >= ? AND inspected_at <= ?
          AND wellhead_temp_c IS NOT NULL AND ambient_temp_c IS NOT NULL
        """,
        (start, end + "T23:59:59Z"),
    ).fetchall()
    by_area: dict[str, list[float]] = defaultdict(list)
    by_area_wh: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        well = wells.get(row["asset_id"])
        area = _area_name(conn, well["area_id"] if well else None) or "Unknown"
        delta = float(row["wellhead_temp_c"]) - float(row["ambient_temp_c"])
        by_area[area].append(delta)
        by_area_wh[area].append(float(row["wellhead_temp_c"]))
    summary = []
    for area, deltas in by_area.items():
        summary.append(
            {
                "area": area,
                "readings": len(deltas),
                "median_delta_c": round(median(deltas), 2),
                "mean_delta_c": round(sum(deltas) / len(deltas), 2),
                "median_wellhead_c": round(median(by_area_wh[area]), 2),
            }
        )
    summary.sort(key=lambda item: (-item["median_delta_c"], item["area"]))
    return {
        "start": start,
        "end": end,
        "definition": (
            "Consistently hotter means the highest median wellhead-minus-ambient delta "
            "across all inspections in the stated window. Temperature is not oil volume."
        ),
        "rows": summary,
        "hottest_area": summary[0]["area"] if summary else None,
    }


def h2s_reading_for_note(conn: sqlite3.Connection, asset_id: str, day: str) -> dict[str, Any]:
    notes = conn.execute(
        """
        SELECT note_id, created_at, text FROM context_notes
        WHERE asset_id = ? AND created_at LIKE ?
        ORDER BY created_at
        """,
        (asset_id, f"{day}%"),
    ).fetchall()
    readings = []
    for note in notes:
        match = H2S_READING_RE.search(note["text"] or "")
        readings.append(
            {
                "note_id": note["note_id"],
                "created_at": note["created_at"],
                "text": note["text"],
                "ppm": float(match.group(1)) if match else None,
            }
        )
    return {"asset_id": asset_id, "day": day, "notes": readings}


def _well_type_on(well: sqlite3.Row | None, day: date) -> str | None:
    if well is None:
        return None
    try:
        raw = json.loads(well["raw_json"] or "{}")
    except json.JSONDecodeError:
        raw = {}
    for item in raw.get("type_history") or []:
        if not isinstance(item, dict):
            continue
        start = item.get("from")
        end = item.get("to")
        if start and day >= parse_date(start) and (not end or day <= parse_date(end)):
            return item.get("well_type") or well["well_type"]
    return well["well_type"]


def fleet_directory(conn: sqlite3.Connection, query: str | None = None) -> dict[str, Any]:
    _ensure(conn)
    users = [dict(row) for row in conn.execute("SELECT user_id, name, role, team_id FROM users ORDER BY user_id")]
    teams = []
    for team in conn.execute("SELECT team_id, name, lead_user_id FROM teams ORDER BY team_id"):
        lead = conn.execute("SELECT name FROM users WHERE user_id = ?", (team["lead_user_id"],)).fetchone()
        well_count = conn.execute("SELECT COUNT(*) FROM wells WHERE team_id = ?", (team["team_id"],)).fetchone()[0]
        teams.append(
            {
                "team_id": team["team_id"],
                "name": team["name"],
                "lead_user_id": team["lead_user_id"],
                "lead_name": lead["name"] if lead else None,
                "well_count": well_count,
            }
        )
    areas = [
        dict(row)
        for row in conn.execute("SELECT area_id, name, team_id FROM areas ORDER BY area_id")
    ]
    match = None
    if query:
        lowered = query.lower()
        for team in teams:
            if lowered in (team["name"] or "").lower() or lowered == (team["team_id"] or "").lower():
                match = team
                break
        if match is None:
            for user in users:
                if lowered in (user["name"] or "").lower() or lowered == (user["user_id"] or "").lower():
                    match = user
                    break
    return {"users": users, "teams": teams, "areas": areas, "match": match, "source_path": "fleet/users.json"}


def cold_weather_misses(conn: sqlite3.Connection, start: str = "2026-12-01", end: str = "2026-12-31") -> dict[str, Any]:
    """OPS-5: producing wells with ambient <= -5 C and no heat-trace check within 7 days."""
    _ensure(conn)
    wells = _well_map(conn)
    inspections = conn.execute(
        """
        SELECT inspection_id, asset_id, inspected_at, ambient_temp_c, wellhead_temp_c
        FROM inspections
        WHERE inspected_at >= ? AND inspected_at <= ? AND ambient_temp_c IS NOT NULL
        ORDER BY inspected_at
        """,
        (start, end + "T23:59:59Z"),
    ).fetchall()
    rows = []
    for row in inspections:
        if float(row["ambient_temp_c"]) > -5:
            continue
        well = wells.get(row["asset_id"])
        inspected = parse_date(row["inspected_at"])
        production = production_from_temps(
            _well_type_on(well, inspected),
            row["wellhead_temp_c"],
            row["ambient_temp_c"],
        )
        if production.get("producing") is not True:
            continue
        deadline = inspected + timedelta(days=7)
        job = conn.execute(
            """
            SELECT maintenance_id, started_at, issue
            FROM maintenance
            WHERE asset_id = ? AND started_at >= ? AND started_at <= ?
              AND IFNULL(issue, '') LIKE '%Heat trace check%'
            ORDER BY started_at LIMIT 1
            """,
            (row["asset_id"], row["inspected_at"], deadline.isoformat() + "T23:59:59Z"),
        ).fetchone()
        if job:
            continue
        rows.append(
            {
                "asset_id": row["asset_id"],
                "area": _area_name(conn, well["area_id"] if well else None),
                "inspection_id": row["inspection_id"],
                "inspected_at": row["inspected_at"],
                "ambient_temp_c": row["ambient_temp_c"],
                "deadline": deadline.isoformat(),
                "met": "Missed",
            }
        )
    sop = conn.execute(
        "SELECT source_path, content FROM source_documents WHERE source_path LIKE '%operations-sop-rev2.md'"
    ).fetchone()
    excerpt = ""
    if sop:
        match = re.search(r"## OPS-5.*?(?=\n## |\Z)", sop["content"] or "", re.S)
        excerpt = (match.group(0) if match else sop["content"][:400]).strip()
    return {
        "rule": "OPS-5 Freeze protection: 1 Dec–28 Feb, ambient <= -5 C on a producing well needs a Heat trace check job within 7 days.",
        "start": start,
        "end": end,
        "rows": rows,
        "example_miss": rows[0] if rows else None,
        "source_path": sop["source_path"] if sop else "fleet/sops/operations-sop-rev2.md",
        "excerpt": excerpt,
    }


def apply_display_preferences(payload: dict[str, Any], preferences: list[dict[str, Any]]) -> dict[str, Any]:
    prefs = {item["key"]: item["value"] for item in preferences}
    rows = list(payload.get("rows") or [])
    if prefs.get("sort") == "area_then_well":
        rows = sorted(rows, key=lambda item: (item.get("area") or "", item.get("asset_id") or ""))
    if prefs.get("cost_unit") == "thousands_usd":
        for row in rows:
            cost = row.get("cost_usd")
            if isinstance(cost, (int, float)):
                row["cost_thousands_usd"] = round(float(cost) / 1000.0, 3)
                row["cost_display"] = f"{row['cost_thousands_usd']} thousand USD"
        payload["cost_unit"] = "thousands_usd"
    payload["rows"] = rows
    payload["applied_preferences"] = prefs
    return payload
