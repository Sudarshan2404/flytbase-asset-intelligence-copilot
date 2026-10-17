from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Iterable
from uuid import uuid4

from app.db import init_schema
from app.domain.dates import (
    EVALUATION_TODAY,
    add_days,
    add_hours,
    in_date_window,
    lookback_start,
    parse_date,
    parse_datetime,
)
from app.domain.findings import (
    STATUS_COMPLIANT,
    STATUS_CONDITION_FLAGGED,
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_NONCOMPLIANT,
    STATUS_OVERDUE,
    STATUS_PENDING,
    Finding,
)
from app.domain.queries import (
    area_asset_ids,
    effective_inspection,
    effective_spill,
    effective_spills,
    production_history,
)
from app.domain.reports import report_for_inspection
from app.domain.spills import CLASS_MAJOR, CLASS_REPORTABLE

SR01_REPORTABLE_HOURS = 24.0
SR01_MAJOR_HOURS = 2.0
SR02_LIMIT_HOURS = 4.0
SR03_DAYS = 60
RC01_MAX_GAP_DAYS = 14
RC03_CONSECUTIVE_DAYS = 14
RC04_RESPONSE_DAYS = 30
RCA_DEFAULT_THRESHOLD = 3
FLOWLINE_TEST_ISSUE = "Annual flowline integrity test"
TEMP_ALARM_C = {"T200": 85.0, "T350": 95.0}
SPILL_SHUTIN_PAD_PCT = 5.0
SOURCE_SR = "fleet/sops/spill-response-sop.md"
SOURCE_RC = "fleet/sops/regulatory-compliance-sop.md"
SOURCE_T200 = "fleet/well-types/T200.md"
SOURCE_T350 = "fleet/well-types/T350.md"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _events_for_inspection(conn: sqlite3.Connection, inspection_id: str, event_type: str | None = None) -> list[sqlite3.Row]:
    if event_type:
        return list(
            conn.execute(
                """
                SELECT * FROM regulatory_events
                WHERE related_inspection_id = ? AND event_type = ?
                ORDER BY date, event_id
                """,
                (inspection_id, event_type),
            )
        )
    return list(
        conn.execute(
            """
            SELECT * FROM regulatory_events
            WHERE related_inspection_id = ?
            ORDER BY date, event_id
            """,
            (inspection_id,),
        )
    )


def _well(conn: sqlite3.Connection, asset_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM wells WHERE asset_id = ?", (asset_id,)).fetchone()


def _deadline_status(as_of: datetime, deadline: datetime, *, missing: bool) -> str:
    if not missing:
        return STATUS_COMPLIANT
    if as_of <= deadline:
        return STATUS_PENDING
    return STATUS_OVERDUE


def evaluate_sr01(spill: dict[str, Any], events: Iterable[sqlite3.Row], as_of: datetime) -> Finding | None:
    classification = spill.get("classification")
    if classification not in {CLASS_REPORTABLE, CLASS_MAJOR}:
        return None
    limit = SR01_MAJOR_HOURS if classification == CLASS_MAJOR else SR01_REPORTABLE_HOURS
    discovery = parse_datetime(spill["inspected_at"])
    deadline = add_hours(discovery, limit)
    reports = [row for row in events if row["event_type"] == "spill_report_filed"]
    hours = reports[0]["hours_to_report"] if reports else None
    event_id = reports[0]["event_id"] if reports else None
    completed = reports[0]["date"] if reports else None
    if hours is not None and float(hours) > limit:
        status = STATUS_NONCOMPLIANT
        reason = f"Written notice filed after {hours} hours; {classification} limit is {limit:g} hours."
    elif hours is not None:
        status = STATUS_COMPLIANT
        reason = f"Written notice filed after {hours} hours, within the {limit:g}-hour {classification} limit."
    else:
        status = _deadline_status(as_of, deadline, missing=True)
        if status == STATUS_OVERDUE:
            reason = f"No spill_report_filed event is recorded and the {limit:g}-hour written-notice deadline has passed."
        else:
            reason = f"No spill_report_filed event is recorded; written notice is still due by {_iso(deadline)}."
    return Finding(
        rule_id="SR-01",
        status=status,
        reason=reason,
        asset_id=spill["asset_id"],
        inspection_id=spill["inspection_id"],
        event_id=event_id,
        document_path=SOURCE_SR,
        document_section="4. Protocols / SR-01",
        excerpt="written notice within 24 hours of discovery, or within 2 hours for a major spill",
        field_values={
            "classification": classification,
            "hours_to_report": hours,
            "effective_volume_bbl": spill.get("effective_volume_bbl"),
        },
        discovery_at=spill["inspected_at"],
        deadline_at=_iso(deadline),
        completed_at=completed,
        observed_hours=float(hours) if hours is not None else None,
        limit_hours=limit,
    )


def evaluate_sr02(spill: dict[str, Any], report: dict[str, Any] | None) -> Finding:
    hours = report["containment_hours"] if report else None
    if hours is None:
        return Finding(
            rule_id="SR-02",
            status=STATUS_INSUFFICIENT_EVIDENCE,
            reason="Containment timing is not present in a linked maintenance report using the observed report phrasing.",
            asset_id=spill["asset_id"],
            inspection_id=spill["inspection_id"],
            maintenance_id=report["maintenance_id"] if report else None,
            document_path=report["document_path"] if report else SOURCE_SR,
            document_section="4. Protocols / SR-02",
            excerpt="The source must be stopped and the spill contained within 4 hours of discovery.",
            field_values={},
            discovery_at=spill["inspected_at"],
            limit_hours=SR02_LIMIT_HOURS,
        )
    status = STATUS_COMPLIANT if hours <= SR02_LIMIT_HOURS else STATUS_NONCOMPLIANT
    return Finding(
        rule_id="SR-02",
        status=status,
        reason=f"Report text states containment after about {hours} hours; limit is {SR02_LIMIT_HOURS:g} hours.",
        asset_id=spill["asset_id"],
        inspection_id=spill["inspection_id"],
        maintenance_id=report["maintenance_id"] if report else None,
        document_path=report["document_path"] if report else SOURCE_SR,
        document_section="What happened",
        excerpt=report.get("excerpt") if report else None,
        field_values={"containment_hours": hours},
        discovery_at=spill["inspected_at"],
        observed_hours=hours,
        limit_hours=SR02_LIMIT_HOURS,
    )


def _site_inspection_passed(event: sqlite3.Row) -> bool | None:
    detail = (event["detail"] or "").lower()
    if "verified" in detail and "meets cleanup" in detail and event["status"] == "closed":
        return True
    if event["status"] == "open" or "pending" in detail or "residual" in detail:
        return False
    if event["status"] == "closed":
        return None
    return False


def evaluate_sr03(spill: dict[str, Any], events: Iterable[sqlite3.Row], as_of: datetime) -> Finding | None:
    if spill.get("classification") not in {CLASS_REPORTABLE, CLASS_MAJOR}:
        return None
    discovery = parse_datetime(spill["inspected_at"])
    deadline = add_days(discovery, SR03_DAYS)
    inspections = [row for row in events if row["event_type"] == "site_inspection"]
    passing = [row for row in inspections if _site_inspection_passed(row) is True]
    failing = [row for row in inspections if _site_inspection_passed(row) is False]
    if passing:
        event = passing[0]
        return Finding(
            rule_id="SR-03",
            status=STATUS_COMPLIANT,
            reason="A closed agency site_inspection records that remediation was verified and meets the cleanup standard.",
            asset_id=spill["asset_id"],
            inspection_id=spill["inspection_id"],
            event_id=event["event_id"],
            document_path=SOURCE_SR,
            document_section="4. Protocols / SR-03",
            excerpt=event["detail"],
            field_values={"site_inspection_status": event["status"]},
            discovery_at=spill["inspected_at"],
            deadline_at=_iso(deadline),
            completed_at=event["date"],
        )
    if not inspections:
        status = _deadline_status(as_of, deadline, missing=True)
        reason = (
            "No agency site_inspection is recorded for this reportable spill."
            if status != STATUS_PENDING
            else "Agency verification is still due; no site_inspection is recorded yet."
        )
        return Finding(
            rule_id="SR-03",
            status=status,
            reason=reason,
            asset_id=spill["asset_id"],
            inspection_id=spill["inspection_id"],
            document_path=SOURCE_SR,
            document_section="4. Protocols / SR-03",
            excerpt="verification is closed only by a passing site inspection from the environmental agency",
            discovery_at=spill["inspected_at"],
            deadline_at=_iso(deadline),
        )
    event = failing[0] if failing else inspections[0]
    passed = _site_inspection_passed(event)
    if passed is None:
        return Finding(
            rule_id="SR-03",
            status=STATUS_INSUFFICIENT_EVIDENCE,
            reason="A site_inspection exists but its detail does not match the observed passing or pending phrases.",
            asset_id=spill["asset_id"],
            inspection_id=spill["inspection_id"],
            event_id=event["event_id"],
            document_path=SOURCE_SR,
            document_section="4. Protocols / SR-03",
            excerpt=event["detail"],
            field_values={"site_inspection_status": event["status"]},
            discovery_at=spill["inspected_at"],
            deadline_at=_iso(deadline),
            completed_at=event["date"],
        )
    status = STATUS_OVERDUE if as_of > deadline else STATUS_PENDING
    return Finding(
        rule_id="SR-03",
        status=status,
        reason=event["detail"] or "Agency verification is not closed by a passing site inspection.",
        asset_id=spill["asset_id"],
        inspection_id=spill["inspection_id"],
        event_id=event["event_id"],
        document_path=SOURCE_SR,
        document_section="4. Protocols / SR-03",
        excerpt=event["detail"],
        field_values={"site_inspection_status": event["status"]},
        discovery_at=spill["inspected_at"],
        deadline_at=_iso(deadline),
        completed_at=event["date"],
    )


def evaluate_sr04(spill: dict[str, Any], view) -> Finding:
    volume = view.spill_est_volume_bbl
    area = view.spill_area_pct
    missing = []
    if volume is None:
        missing.append("spill_est_volume_bbl")
    if area is None:
        missing.append("spill_area_pct")
    status = STATUS_COMPLIANT if not missing else STATUS_NONCOMPLIANT
    reason = (
        "Spill is recorded with both estimated volume and affected pad area."
        if status == STATUS_COMPLIANT
        else f"Effective spill is missing required field(s): {', '.join(missing)}."
    )
    return Finding(
        rule_id="SR-04",
        status=status,
        reason=reason,
        asset_id=spill["asset_id"],
        inspection_id=spill["inspection_id"],
        document_path=SOURCE_SR,
        document_section="4. Protocols / SR-04",
        excerpt="Every spill, including minor ones, must be recorded against the asset with an estimated volume and affected area.",
        field_values={"spill_est_volume_bbl": volume, "spill_area_pct": area},
        discovery_at=spill["inspected_at"],
    )


def evaluate_rc01(conn: sqlite3.Connection, asset_id: str, start, end) -> list[Finding]:
    rows = list(
        conn.execute(
            """
            SELECT inspection_id, inspected_at FROM inspections
            WHERE asset_id = ?
            ORDER BY inspected_at, inspection_id
            """,
            (asset_id,),
        )
    )
    findings = []
    for previous, current in zip(rows, rows[1:]):
        if not in_date_window(current["inspected_at"], start, end):
            continue
        gap = (parse_datetime(current["inspected_at"]) - parse_datetime(previous["inspected_at"])).days
        if gap > RC01_MAX_GAP_DAYS:
            findings.append(
                Finding(
                    rule_id="RC-01",
                    status=STATUS_NONCOMPLIANT,
                    reason=f"Inspections {previous['inspection_id']} and {current['inspection_id']} are {gap} days apart; the limit is {RC01_MAX_GAP_DAYS} days.",
                    asset_id=asset_id,
                    inspection_id=current["inspection_id"],
                    document_path=SOURCE_RC,
                    document_section="2. Protocols / RC-01",
                    excerpt="Every well must be inspected at least once every 14 days.",
                    field_values={
                        "previous_inspection_id": previous["inspection_id"],
                        "previous_inspected_at": previous["inspected_at"],
                        "gap_days": gap,
                    },
                    discovery_at=current["inspected_at"],
                )
            )
    return findings


def evaluate_rc02(conn: sqlite3.Connection, asset_id: str, start, end, as_of) -> list[Finding]:
    as_of_date = parse_date(as_of)
    years = range(parse_date(start).year, parse_date(end).year + 1)
    findings = []
    for year in years:
        if year > as_of_date.year:
            continue
        row = conn.execute(
            """
            SELECT maintenance_id, started_at, issue, action_taken
            FROM maintenance
            WHERE asset_id = ?
              AND issue = ?
              AND equipment_kind = 'flowline'
              AND CAST(strftime('%Y', started_at) AS INTEGER) = ?
            ORDER BY started_at
            """,
            (asset_id, FLOWLINE_TEST_ISSUE, year),
        ).fetchone()
        year_end = datetime(year, 12, 31, tzinfo=parse_datetime(as_of).tzinfo)
        if row:
            findings.append(
                Finding(
                    rule_id="RC-02",
                    status=STATUS_COMPLIANT,
                    reason=f"Annual flowline integrity test {row['maintenance_id']} is recorded for {year}.",
                    asset_id=asset_id,
                    maintenance_id=row["maintenance_id"],
                    document_path=SOURCE_RC,
                    document_section="2. Protocols / RC-02",
                    excerpt='maintenance.jsonl entries with issue "Annual flowline integrity test"',
                    field_values={"issue": row["issue"], "action_taken": row["action_taken"], "year": year},
                    completed_at=row["started_at"],
                )
            )
        elif year < as_of_date.year:
            findings.append(
                Finding(
                    rule_id="RC-02",
                    status=STATUS_NONCOMPLIANT,
                    reason=f"No Annual flowline integrity test is recorded for calendar year {year}.",
                    asset_id=asset_id,
                    document_path=SOURCE_RC,
                    document_section="2. Protocols / RC-02",
                    excerpt="Every flowline must pass an integrity test once per calendar year.",
                    field_values={"year": year},
                    deadline_at=_iso(year_end),
                )
            )
        else:
            findings.append(
                Finding(
                    rule_id="RC-02",
                    status=STATUS_PENDING,
                    reason=f"No Annual flowline integrity test is recorded yet for {year}; the calendar year has not ended.",
                    asset_id=asset_id,
                    document_path=SOURCE_RC,
                    document_section="2. Protocols / RC-02",
                    excerpt="Every flowline must pass an integrity test once per calendar year.",
                    field_values={"year": year},
                    deadline_at=_iso(year_end),
                )
            )
    return findings


def evaluate_rc03(conn: sqlite3.Connection, asset_id: str, start, end, well_type: str | None) -> list[Finding]:
    history = production_history(conn, asset_id, start, end)
    findings = []
    streak: list[dict[str, Any]] = []
    emitted = False
    for reading in history["readings"]:
        if reading["status"] == "not_producing":
            if streak:
                gap = (parse_datetime(reading["inspected_at"]) - parse_datetime(streak[-1]["inspected_at"])).days
                if gap > 14:
                    streak = []
                    emitted = False
            streak.append(reading)
            days = len(streak) * 7
            if days > RC03_CONSECUTIVE_DAYS and not emitted:
                findings.append(
                    Finding(
                        rule_id="RC-03",
                        status=STATUS_INSUFFICIENT_EVIDENCE,
                        reason=(
                            f"{days} consecutive downtime days are established from inspections, which exceeds "
                            f"{RC03_CONSECUTIVE_DAYS} days. The records do not contain a structured supervisor-report event."
                        ),
                        asset_id=asset_id,
                        inspection_id=reading["inspection_id"],
                        document_path=SOURCE_RC,
                        document_section="2. Protocols / RC-03",
                        excerpt="Any well down for more than 14 consecutive days must be reported to the area production supervisor.",
                        field_values={
                            "consecutive_days": days,
                            "inspection_ids": [item["inspection_id"] for item in streak],
                            "well_type": well_type,
                        },
                        discovery_at=streak[0]["inspected_at"],
                    )
                )
                emitted = True
        else:
            streak = []
            emitted = False
    return findings


def evaluate_rc04(conn: sqlite3.Connection, asset_id: str, start, end, as_of: datetime) -> list[Finding]:
    novs = conn.execute(
        """
        SELECT * FROM regulatory_events
        WHERE asset_id = ? AND event_type = 'notice_of_violation'
        ORDER BY date, event_id
        """,
        (asset_id,),
    ).fetchall()
    findings = []
    for nov in novs:
        related_in_window = False
        if nov["related_inspection_id"]:
            inspection = conn.execute(
                "SELECT inspected_at FROM inspections WHERE inspection_id = ?",
                (nov["related_inspection_id"],),
            ).fetchone()
            if inspection and in_date_window(inspection["inspected_at"], start, end):
                related_in_window = True
        if not in_date_window(nov["date"], start, end) and not related_in_window:
            continue
        deadline = add_days(nov["date"], RC04_RESPONSE_DAYS)
        responses = [
            row
            for row in _events_for_inspection(conn, nov["related_inspection_id"], "nov_response_filed")
            if nov["related_inspection_id"]
        ]
        if not nov["related_inspection_id"]:
            responses = list(
                conn.execute(
                    """
                    SELECT * FROM regulatory_events
                    WHERE asset_id = ? AND event_type = 'nov_response_filed' AND date >= ?
                    ORDER BY date
                    """,
                    (asset_id, nov["date"]),
                )
            )
        if responses:
            response = responses[0]
            late = parse_date(response["date"]) > parse_date(deadline)
            findings.append(
                Finding(
                    rule_id="RC-04",
                    status=STATUS_NONCOMPLIANT if late else STATUS_COMPLIANT,
                    reason=(
                        f"nov_response_filed {response['event_id']} is dated {response['date']}."
                        + (" That is after the 30-day deadline." if late else " That is within 30 days of the NOV.")
                    ),
                    asset_id=asset_id,
                    inspection_id=nov["related_inspection_id"],
                    event_id=nov["event_id"],
                    document_path=SOURCE_RC,
                    document_section="2. Protocols / RC-04",
                    excerpt=response["detail"],
                    field_values={"nov_id": nov["event_id"], "response_id": response["event_id"]},
                    discovery_at=nov["date"],
                    deadline_at=_iso(deadline),
                    completed_at=response["date"],
                )
            )
        else:
            status = _deadline_status(as_of, deadline, missing=True)
            findings.append(
                Finding(
                    rule_id="RC-04",
                    status=status,
                    reason=(
                        "No nov_response_filed event is recorded for this notice of violation."
                        if status != STATUS_PENDING
                        else "NOV response is still within the 30-day window; none is recorded yet."
                    ),
                    asset_id=asset_id,
                    inspection_id=nov["related_inspection_id"],
                    event_id=nov["event_id"],
                    document_path=SOURCE_RC,
                    document_section="2. Protocols / RC-04",
                    excerpt="Agency notices of violation must be answered within 30 days and tracked until closed.",
                    field_values={"nov_status": nov["status"], "fine_usd": nov["fine_usd"]},
                    discovery_at=nov["date"],
                    deadline_at=_iso(deadline),
                )
            )
    return findings


def evaluate_rc05(conn: sqlite3.Connection, asset_id: str, start, end, spills: list[dict[str, Any]]) -> list[Finding]:
    findings = []
    inspection_count = conn.execute(
        """
        SELECT COUNT(*) FROM inspections
        WHERE asset_id = ? AND date(inspected_at) >= date(?) AND date(inspected_at) <= date(?)
        """,
        (asset_id, parse_date(start).isoformat(), parse_date(end).isoformat()),
    ).fetchone()[0]
    if inspection_count == 0:
        findings.append(
            Finding(
                rule_id="RC-05",
                status=STATUS_INSUFFICIENT_EVIDENCE,
                reason="No inspections are recorded in the window, so the weekly visual-inspection limb of the flowline program cannot be evidenced.",
                asset_id=asset_id,
                document_path=SOURCE_RC,
                document_section="2. Protocols / RC-05",
            )
        )
        return findings
    for spill in spills:
        repair = conn.execute(
            """
            SELECT maintenance_id, maintenance_type, equipment_kind, started_at
            FROM maintenance
            WHERE triggered_by_inspection_id = ? AND maintenance_type = 'corrective'
            """,
            (spill["inspection_id"],),
        ).fetchone()
        if repair is None:
            findings.append(
                Finding(
                    rule_id="RC-05",
                    status=STATUS_NONCOMPLIANT,
                    reason="An effective spill has no linked corrective maintenance record.",
                    asset_id=asset_id,
                    inspection_id=spill["inspection_id"],
                    document_path=SOURCE_RC,
                    document_section="2. Protocols / RC-05",
                    excerpt="corrective repair of any leak found, and prompt cleanup",
                    discovery_at=spill["inspected_at"],
                )
            )
        else:
            findings.append(
                Finding(
                    rule_id="RC-05",
                    status=STATUS_COMPLIANT,
                    reason=f"Corrective repair {repair['maintenance_id']} is linked to the spill inspection.",
                    asset_id=asset_id,
                    inspection_id=spill["inspection_id"],
                    maintenance_id=repair["maintenance_id"],
                    document_path=SOURCE_RC,
                    document_section="2. Protocols / RC-05",
                    field_values={"equipment_kind": repair["equipment_kind"]},
                    discovery_at=spill["inspected_at"],
                    completed_at=repair["started_at"],
                )
            )
    if not spills:
        findings.append(
            Finding(
                rule_id="RC-05",
                status=STATUS_INSUFFICIENT_EVIDENCE,
                reason=(
                    f"{inspection_count} inspections are present, which can evidence weekly visual checks, "
                    "but there were no effective spills in the window to test the leak-repair limb."
                ),
                asset_id=asset_id,
                document_path=SOURCE_RC,
                document_section="2. Protocols / RC-05",
                field_values={"inspection_count": inspection_count},
            )
        )
    return findings


def evaluate_temperature_alarm(conn: sqlite3.Connection, asset_id: str, well_type: str | None, start, end) -> list[Finding]:
    threshold = TEMP_ALARM_C.get(well_type or "")
    if threshold is None:
        return []
    findings = []
    for row in conn.execute(
        """
        SELECT inspection_id, inspected_at, wellhead_temp_c
        FROM inspections
        WHERE asset_id = ?
        ORDER BY inspected_at
        """,
        (asset_id,),
    ):
        if not in_date_window(row["inspected_at"], start, end):
            continue
        view = effective_inspection(conn, row["inspection_id"])
        if view.wellhead_temp_c is None or view.wellhead_temp_c <= threshold:
            continue
        findings.append(
            Finding(
                rule_id="TEMP-ALARM",
                status=STATUS_CONDITION_FLAGGED,
                reason=(
                    f"Wellhead temperature {view.wellhead_temp_c} °C exceeds the {well_type} alarm of {threshold:g} °C. "
                    "The required shut-in and inspection within 24 hours is not a structured field, so action compliance cannot be scored."
                ),
                asset_id=asset_id,
                inspection_id=view.inspection_id,
                document_path=SOURCE_T200 if well_type == "T200" else SOURCE_T350,
                document_section="Temperature alarm",
                field_values={
                    "wellhead_temp_c": view.wellhead_temp_c,
                    "original_wellhead_temp_c": view.original["wellhead_temp_c"],
                    "threshold_c": threshold,
                },
                discovery_at=view.inspected_at,
                deadline_at=_iso(add_hours(view.inspected_at, 24)),
            )
        )
    return findings


def evaluate_spill_shutin(spill: dict[str, Any], view, well_type: str | None) -> Finding | None:
    if view.spill_area_pct is None:
        return None
    if view.spill_area_pct <= SPILL_SHUTIN_PAD_PCT:
        return None
    return Finding(
        rule_id="SPILL-SHUTIN",
        status=STATUS_CONDITION_FLAGGED,
        reason=(
            f"Effective pad coverage is {view.spill_area_pct}% , which is more than {SPILL_SHUTIN_PAD_PCT:g}%. "
            "Immediate shut-in is required by the well-type manual, but shut-in is not a structured inspection field."
        ),
        asset_id=spill["asset_id"],
        inspection_id=spill["inspection_id"],
        document_path=SOURCE_T200 if well_type == "T200" else SOURCE_T350,
        document_section="Spill-related shut-in",
        field_values={"spill_area_pct": view.spill_area_pct, "well_type": well_type},
        discovery_at=spill["inspected_at"],
    )


def apply_rule_override(
    conn: sqlite3.Connection,
    *,
    rule_id: str,
    parameter: str,
    value: Any,
    well_type: str | None = None,
    equipment_kind: str | None = None,
    author_user_id: str | None = None,
    reason: str | None = None,
    created_at: str | None = None,
    override_id: str | None = None,
) -> str:
    init_schema(conn)
    assigned = override_id or f"ROV-{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO rule_overrides (
            override_id, rule_id, well_type, equipment_kind, parameter, value_json,
            author_user_id, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assigned,
            rule_id,
            well_type,
            equipment_kind,
            parameter,
            json.dumps(value),
            author_user_id,
            reason,
            created_at,
        ),
    )
    return assigned


def load_rca_threshold(
    conn: sqlite3.Connection,
    well_type: str | None,
    equipment_kind: str,
    asset_id: str | None = None,
) -> tuple[int, str]:
    init_schema(conn)
    rows = conn.execute(
        """
        SELECT well_type, equipment_kind, asset_id, value_json, reason
        FROM rule_overrides
        WHERE rule_id = 'RCA' AND parameter = 'threshold'
          AND IFNULL(status, 'active') = 'active'
        ORDER BY created_at, override_id
        """
    ).fetchall()
    threshold = RCA_DEFAULT_THRESHOLD
    source = "T200/T350 manual section 10"
    for row in rows:
        if row["equipment_kind"] not in (None, equipment_kind):
            continue
        if row["well_type"] not in (None, well_type):
            continue
        if "asset_id" in row.keys() and row["asset_id"] not in (None, "", asset_id):
            continue
        threshold = int(json.loads(row["value_json"]))
        source = row["reason"] or "rule_override"
    return threshold, source


def rca_candidates(
    conn: sqlite3.Connection,
    *,
    equipment_kind: str,
    start,
    end,
    area: str | None = None,
    asset_id: str | None = None,
) -> list[dict[str, Any]]:
    init_schema(conn)
    asset_ids = [asset_id] if asset_id else (area_asset_ids(conn, area) if area else [
        row["asset_id"] for row in conn.execute("SELECT asset_id FROM wells ORDER BY asset_id")
    ])
    results = []
    for current_id in asset_ids:
        well = _well(conn, current_id)
        if well is None:
            continue
        threshold, source = load_rca_threshold(conn, well["well_type"], equipment_kind, current_id)
        jobs = list(
            conn.execute(
                """
                SELECT maintenance_id, started_at, equipment_kind, maintenance_type
                FROM maintenance
                WHERE asset_id = ? AND maintenance_type = 'corrective' AND equipment_kind = ?
                ORDER BY started_at
                """,
                (current_id, equipment_kind),
            )
        )
        in_window = [job for job in jobs if in_date_window(job["started_at"], start, end)]
        required = len(in_window) >= threshold
        results.append(
            {
                "asset_id": current_id,
                "well_type": well["well_type"],
                "equipment_kind": equipment_kind,
                "threshold": threshold,
                "repair_count": len(in_window),
                "maintenance_ids": [job["maintenance_id"] for job in in_window],
                "required": required,
                "source": source,
            }
        )
    return results


def evaluate_rca(
    conn: sqlite3.Connection,
    asset_id: str,
    start,
    end,
    *,
    equipment_kinds: Iterable[str] | None = None,
) -> list[Finding]:
    kinds = list(equipment_kinds) if equipment_kinds else [
        row["equipment_kind"]
        for row in conn.execute(
            """
            SELECT DISTINCT equipment_kind FROM maintenance
            WHERE asset_id = ? AND maintenance_type = 'corrective' AND equipment_kind IS NOT NULL
            """,
            (asset_id,),
        )
    ]
    findings = []
    for kind in kinds:
        info = rca_candidates(conn, equipment_kind=kind, start=start, end=end, asset_id=asset_id)[0]
        if not info["required"]:
            continue
        findings.append(
            Finding(
                rule_id="RCA",
                status=STATUS_PENDING,
                reason=(
                    f"{info['repair_count']} corrective {kind} repairs in the window meet the threshold of "
                    f"{info['threshold']} ({info['source']}). RCA is required before the next repair is closed."
                ),
                asset_id=asset_id,
                maintenance_id=info["maintenance_ids"][-1] if info["maintenance_ids"] else None,
                document_path=SOURCE_T200,
                document_section="10. Maintenance escalation",
                excerpt="Three or more corrective maintenance events on the same equipment kind in the 12 months before the current date require a root-cause analysis",
                field_values=info,
                source=info["source"],
            )
        )
    return findings


def evaluate_spill_compliance(
    conn: sqlite3.Connection,
    inspection_id: str,
    *,
    as_of=EVALUATION_TODAY,
) -> list[Finding]:
    init_schema(conn)
    view = effective_inspection(conn, inspection_id)
    spill = effective_spill(view)
    as_of_dt = parse_datetime(as_of)
    if not spill["effective_spill_detected"]:
        return [
            Finding(
                rule_id="SPILL",
                status=STATUS_INSUFFICIENT_EVIDENCE,
                reason="Inspection is not an effective spill after corrections; spill protocols are not applied.",
                asset_id=view.asset_id,
                inspection_id=inspection_id,
                field_values={
                    "excluded_by_correction": spill["excluded_by_correction"],
                    "formal_correction_ids": list(view.formal_correction_ids),
                    "structured_correction_ids": list(view.structured_correction_ids),
                },
            )
        ]
    events = _events_for_inspection(conn, inspection_id)
    report = report_for_inspection(conn, inspection_id)
    well = _well(conn, view.asset_id) if view.asset_id else None
    findings = [
        evaluate_sr01(spill, events, as_of_dt),
        evaluate_sr02(spill, report),
        evaluate_sr03(spill, events, as_of_dt),
        evaluate_sr04(spill, view),
        evaluate_spill_shutin(spill, view, well["well_type"] if well else None),
    ]
    return [item for item in findings if item is not None]


def evaluate_well_compliance(
    conn: sqlite3.Connection,
    asset_id: str,
    start,
    end,
    *,
    as_of=EVALUATION_TODAY,
) -> list[Finding]:
    init_schema(conn)
    as_of_dt = parse_datetime(as_of)
    well = _well(conn, asset_id)
    well_type = well["well_type"] if well else None
    spills = effective_spills(conn, asset_id=asset_id, start=start, end=end)
    findings: list[Finding] = []
    for spill in spills:
        findings.extend(evaluate_spill_compliance(conn, spill["inspection_id"], as_of=as_of))
    findings.extend(evaluate_rc01(conn, asset_id, start, end))
    findings.extend(evaluate_rc02(conn, asset_id, start, end, as_of))
    findings.extend(evaluate_rc03(conn, asset_id, start, end, well_type))
    findings.extend(evaluate_rc04(conn, asset_id, start, end, as_of_dt))
    findings.extend(evaluate_rc05(conn, asset_id, start, end, spills))
    findings.extend(evaluate_rca(conn, asset_id, start, end))
    findings.extend(evaluate_temperature_alarm(conn, asset_id, well_type, start, end))
    return findings


def late_containment_spills(
    conn: sqlite3.Connection,
    area: str,
    start,
    end,
) -> list[Finding]:
    init_schema(conn)
    findings = []
    for spill in effective_spills(conn, area=area, start=start, end=end):
        report = report_for_inspection(conn, spill["inspection_id"])
        finding = evaluate_sr02(spill, report)
        if finding.status == STATUS_NONCOMPLIANT:
            findings.append(finding)
    return findings


def wells_requiring_rca(
    conn: sqlite3.Connection,
    *,
    area: str,
    equipment_kind: str,
    as_of=EVALUATION_TODAY,
    lookback_years: int = 1,
) -> list[dict[str, Any]]:
    start = lookback_start(as_of, years=lookback_years)
    end = parse_date(as_of)
    return [
        item
        for item in rca_candidates(
            conn,
            equipment_kind=equipment_kind,
            start=start,
            end=end,
            area=area,
        )
        if item["required"]
    ]
