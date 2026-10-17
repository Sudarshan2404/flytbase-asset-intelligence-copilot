from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import connect, init_schema
from app.domain.compliance import (
    apply_rule_override,
    evaluate_spill_compliance,
    evaluate_well_compliance,
    late_containment_spills,
    wells_requiring_rca,
)
from app.domain.corrections import apply_structured_correction, effective_inspection
from app.domain.dates import EVALUATION_TODAY, lookback_start
from app.domain.reports import parse_containment_hours


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    init_schema(conn)
    return conn


def _memory() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO wells (asset_id, name, well_type, area_id, recorded_status, source_path, raw_json)
        VALUES ('WELL-X', 'Test', 'T200', 'SW-24', 'active', 'w', '{}')
        """
    )
    conn.execute(
        "INSERT INTO areas (area_id, name, team_id, source_path, raw_json) VALUES ('SW-24', 'Southwest Block 24', 'TEAM-SW', 'a', '{}')"
    )
    conn.execute("INSERT INTO area_assets (area_id, asset_id) VALUES ('SW-24', 'WELL-X')")
    return conn


def _inspection(
    conn: sqlite3.Connection,
    inspection_id: str,
    inspected_at: str,
    *,
    volume: float | None = 6.8,
    area: float | None = 9.4,
    wellhead: float = 40.0,
    ambient: float = 20.0,
    spill: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO inspections (
            inspection_id, asset_id, inspected_at, wellhead_temp_c, ambient_temp_c,
            spill_detected, spill_est_volume_bbl, spill_area_pct, source_path, raw_json
        ) VALUES (?, 'WELL-X', ?, ?, ?, ?, ?, ?, 'i', '{}')
        """,
        (inspection_id, inspected_at, wellhead, ambient, int(spill), volume, area),
    )


def test_containment_parser_matches_observed_report_formats() -> None:
    plain = "The release was contained about 7.1 hours after the inspection."
    bold = "the release was **contained about 7 hours after the inspection flight**."
    after = "The spill was contained after about 8.1 hours."
    hours, excerpt = parse_containment_hours(plain)
    assert hours == 7.1
    assert "7.1 hours" in excerpt
    assert parse_containment_hours(bold)[0] == 7.0
    assert parse_containment_hours(after)[0] == 8.1
    assert parse_containment_hours("No timing is given.")[0] is None


def test_sr01_deadline_boundaries() -> None:
    conn = _memory()
    _inspection(conn, "INS-LATE", "2026-06-11T14:30:00Z")
    conn.execute(
        """
        INSERT INTO regulatory_events (
            event_id, asset_id, date, event_type, related_inspection_id, hours_to_report,
            status, source_path, raw_json
        ) VALUES ('REG-LATE', 'WELL-X', '2026-06-13', 'spill_report_filed', 'INS-LATE', 24.1, 'closed', 'r', '{}')
        """
    )
    late = [item for item in evaluate_spill_compliance(conn, "INS-LATE") if item.rule_id == "SR-01"][0]
    assert late.status == "noncompliant"

    conn.execute("UPDATE regulatory_events SET hours_to_report = 24.0 WHERE event_id = 'REG-LATE'")
    on_time = [item for item in evaluate_spill_compliance(conn, "INS-LATE") if item.rule_id == "SR-01"][0]
    assert on_time.status == "compliant"
    conn.close()


def test_sr01_missing_report_is_pending_then_overdue() -> None:
    conn = _memory()
    _inspection(conn, "INS-WAIT", "2026-10-16T12:00:00Z")
    pending = [item for item in evaluate_spill_compliance(conn, "INS-WAIT", as_of="2026-10-16T20:00:00Z") if item.rule_id == "SR-01"][0]
    assert pending.status == "pending"
    overdue = [item for item in evaluate_spill_compliance(conn, "INS-WAIT", as_of="2026-10-18T12:00:00Z") if item.rule_id == "SR-01"][0]
    assert overdue.status == "overdue"
    conn.close()


def test_sr02_missing_report_is_insufficient_evidence() -> None:
    conn = _memory()
    _inspection(conn, "INS-NONE", "2026-06-11T14:30:00Z", volume=0.4, area=1.0)
    finding = [item for item in evaluate_spill_compliance(conn, "INS-NONE") if item.rule_id == "SR-02"][0]
    assert finding.status == "insufficient_evidence"
    conn.close()


def test_sr03_passing_versus_failed_agency_inspection() -> None:
    conn = _memory()
    _inspection(conn, "INS-PASS", "2026-01-01T12:00:00Z")
    conn.execute(
        """
        INSERT INTO regulatory_events (
            event_id, asset_id, date, event_type, related_inspection_id, status, detail, source_path, raw_json
        ) VALUES (
            'REG-PASS', 'WELL-X', '2026-02-01', 'site_inspection', 'INS-PASS', 'closed',
            'Remediation verified; site meets cleanup standard.', 'r', '{}'
        )
        """
    )
    passed = [item for item in evaluate_spill_compliance(conn, "INS-PASS") if item.rule_id == "SR-03"][0]
    assert passed.status == "compliant"

    _inspection(conn, "INS-FAIL", "2026-06-11T14:30:00Z")
    conn.execute(
        """
        INSERT INTO regulatory_events (
            event_id, asset_id, date, event_type, related_inspection_id, status, detail, source_path, raw_json
        ) VALUES (
            'REG-FAIL', 'WELL-X', '2026-07-14', 'site_inspection', 'INS-FAIL', 'open',
            'Follow-up soil sampling found residual hydrocarbons; remediation verification still pending.', 'r', '{}'
        )
        """
    )
    failed = [item for item in evaluate_spill_compliance(conn, "INS-FAIL", as_of="2026-10-16") if item.rule_id == "SR-03"][0]
    assert failed.status == "overdue"
    assert failed.event_id == "REG-FAIL"
    conn.close()


def test_sr04_requires_volume_and_area() -> None:
    conn = _memory()
    _inspection(conn, "INS-MISS", "2026-06-11T14:30:00Z", volume=None, area=None)
    finding = [item for item in evaluate_spill_compliance(conn, "INS-MISS") if item.rule_id == "SR-04"][0]
    assert finding.status == "noncompliant"
    assert "spill_est_volume_bbl" in finding.reason
    conn.close()


def test_rc01_overdue_inspection_gap() -> None:
    conn = _memory()
    _inspection(conn, "INS-A", "2026-01-01T12:00:00Z", spill=False, volume=None, area=None)
    _inspection(conn, "INS-B", "2026-01-16T12:00:00Z", spill=False, volume=None, area=None)
    findings = [item for item in evaluate_well_compliance(conn, "WELL-X", "2026-01-01", "2026-01-31") if item.rule_id == "RC-01"]
    assert findings[0].status == "noncompliant"
    assert findings[0].field_values["gap_days"] == 15
    conn.close()


def test_rc04_nov_response_deadlines() -> None:
    conn = _memory()
    _inspection(conn, "INS-NOV", "2026-06-11T14:30:00Z")
    conn.execute(
        """
        INSERT INTO regulatory_events (
            event_id, asset_id, date, event_type, related_inspection_id, status, fine_usd, source_path, raw_json
        ) VALUES ('REG-NOV', 'WELL-X', '2026-06-30', 'notice_of_violation', 'INS-NOV', 'open', 5000, 'r', '{}')
        """
    )
    missing = [item for item in evaluate_well_compliance(conn, "WELL-X", "2026-06-01", "2026-10-16") if item.rule_id == "RC-04"][0]
    assert missing.status == "overdue"

    conn.execute(
        """
        INSERT INTO regulatory_events (
            event_id, asset_id, date, event_type, related_inspection_id, status, detail, source_path, raw_json
        ) VALUES ('REG-RSP', 'WELL-X', '2026-07-15', 'nov_response_filed', 'INS-NOV', 'closed', 'Operator response submitted.', 'r', '{}')
        """
    )
    timely = [item for item in evaluate_well_compliance(conn, "WELL-X", "2026-06-01", "2026-10-16") if item.rule_id == "RC-04"][0]
    assert timely.status == "compliant"
    conn.close()


def test_rca_threshold_and_well_type_override() -> None:
    conn = _memory()
    conn.execute(
        """
        INSERT INTO wells (asset_id, name, well_type, area_id, recorded_status, source_path, raw_json)
        VALUES ('WELL-T350', 'Deep', 'T350', 'SW-24', 'active', 'w', '{}')
        """
    )
    conn.execute("INSERT INTO area_assets (area_id, asset_id) VALUES ('SW-24', 'WELL-T350')")
    for index, started in enumerate(["2026-01-01T08:00:00Z", "2026-03-01T08:00:00Z", "2026-05-01T08:00:00Z"], start=1):
        conn.execute(
            """
            INSERT INTO maintenance (
                maintenance_id, asset_id, started_at, maintenance_type, equipment_kind, source_path, raw_json
            ) VALUES (?, 'WELL-X', ?, 'corrective', 'pump_jack', 'm', '{}')
            """,
            (f"MNT-X-{index}", started),
        )
    conn.execute(
        """
        INSERT INTO maintenance (
            maintenance_id, asset_id, started_at, maintenance_type, equipment_kind, source_path, raw_json
        ) VALUES
            ('MNT-T-1', 'WELL-T350', '2026-02-01T08:00:00Z', 'corrective', 'pump_jack', 'm', '{}'),
            ('MNT-T-2', 'WELL-T350', '2026-04-01T08:00:00Z', 'corrective', 'pump_jack', 'm', '{}')
        """
    )
    default = {item["asset_id"] for item in wells_requiring_rca(conn, area="Southwest Block 24", equipment_kind="pump_jack")}
    assert default == {"WELL-X"}
    apply_rule_override(
        conn,
        rule_id="RCA",
        parameter="threshold",
        value=2,
        well_type="T350",
        equipment_kind="pump_jack",
        author_user_id="U-201",
        reason="Michael Turner T350 pump jack rule",
    )
    overridden = {item["asset_id"] for item in wells_requiring_rca(conn, area="Southwest Block 24", equipment_kind="pump_jack")}
    assert overridden == {"WELL-X", "WELL-T350"}
    conn.close()


def test_formal_correction_still_voids_spill_and_skips_protocols() -> None:
    conn = _memory()
    _inspection(conn, "INS-VOID", "2024-03-07T09:15:00Z", volume=0.9, area=3.1)
    conn.execute(
        """
        INSERT INTO context_notes (
            note_id, asset_id, created_at, note_type, inspection_id, text,
            valid_from, valid_to, source_path, raw_json
        ) VALUES (
            'CTX-OLD', 'WELL-X', '2024-03-07T16:05:00Z', 'correction', 'INS-VOID',
            'Rainwater, not crude.', '2024-03-07', '2024-06-01', 'n', '{}'
        )
        """
    )
    raw = conn.execute("SELECT spill_detected FROM inspections WHERE inspection_id = 'INS-VOID'").fetchone()
    findings = evaluate_spill_compliance(conn, "INS-VOID")
    assert raw["spill_detected"] == 1
    assert findings[0].rule_id == "SPILL"
    assert findings[0].status == "insufficient_evidence"
    view = effective_inspection(conn, "INS-VOID")
    assert view.original["spill_detected"] is True
    assert view.spill_detected is False
    conn.close()


def test_structured_volume_correction_changes_sr01_without_mutating_raw() -> None:
    conn = _memory()
    _inspection(conn, "INS-VOL", "2026-09-24T15:30:00Z", volume=4.5, area=10.5)
    apply_structured_correction(
        conn,
        inspection_id="INS-VOL",
        field_name="spill_est_volume_bbl",
        corrected_value=5.4,
        author_user_id="U-201",
        reason="Field crew measured 5.4 bbl",
    )
    raw = conn.execute("SELECT spill_est_volume_bbl FROM inspections WHERE inspection_id = 'INS-VOL'").fetchone()
    findings = {item.rule_id: item for item in evaluate_spill_compliance(conn, "INS-VOL", as_of="2026-10-16")}
    assert raw["spill_est_volume_bbl"] == 4.5
    assert findings["SR-01"].status == "overdue"
    assert findings["SR-01"].field_values["effective_volume_bbl"] == 5.4
    conn.close()


def test_q9_well_0001_last_12_months(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    start = lookback_start(EVALUATION_TODAY, years=1)
    findings = evaluate_well_compliance(conn, "WELL-0001", start, EVALUATION_TODAY, as_of=EVALUATION_TODAY)
    breaches = {(item.rule_id, item.inspection_id or item.event_id) for item in findings if item.is_breach}
    assert ("SR-01", "INS-0001-0185") in {(item.rule_id, item.inspection_id) for item in findings if item.is_breach}
    sr01 = next(item for item in findings if item.rule_id == "SR-01" and item.inspection_id == "INS-0001-0185")
    sr02 = next(item for item in findings if item.rule_id == "SR-02" and item.inspection_id == "INS-0001-0185")
    sr03 = next(item for item in findings if item.rule_id == "SR-03" and item.inspection_id == "INS-0001-0185")
    rc04 = next(item for item in findings if item.rule_id == "RC-04")
    rc02 = [item for item in findings if item.rule_id == "RC-02"]
    rca = next(item for item in findings if item.rule_id == "RCA" and item.field_values.get("equipment_kind") == "flowline")

    assert sr01.status == "noncompliant"
    assert sr01.observed_hours == 41
    assert sr01.event_id == "REG-0001-001"
    assert sr02.status == "noncompliant"
    assert sr02.observed_hours == 7
    assert sr02.maintenance_id == "MNT-0001-029"
    assert sr03.status == "overdue"
    assert sr03.deadline_at.startswith("2026-08-10")
    assert sr03.event_id == "REG-0001-003"
    assert rc04.status == "overdue"
    assert rc04.event_id == "REG-0001-002"
    assert all(item.status == "compliant" for item in rc02)
    assert rca.status == "pending"
    assert set(rca.field_values["maintenance_ids"]) >= {"MNT-0001-028", "MNT-0001-029", "MNT-0001-032"}
    assert {item.rule_id for item in findings if item.is_breach} == {"SR-01", "SR-02", "SR-03", "RC-04"}
    conn.close()


def test_q10_southwest_block_24_late_containment(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    start = lookback_start(EVALUATION_TODAY, days=90)
    findings = late_containment_spills(conn, "Southwest Block 24", start, EVALUATION_TODAY)
    pairs = {(item.asset_id, item.inspection_id, item.observed_hours, item.maintenance_id) for item in findings}
    assert pairs == {
        ("WELL-0082", "INS-0082-0198", 7.1, "MNT-0082-028"),
        ("WELL-0100", "INS-0100-0196", 8.1, "MNT-0100-034"),
    }
    conn.close()


def test_q13_corrected_well_0151_volume(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    apply_structured_correction(
        conn,
        inspection_id="INS-0151-0201",
        field_name="spill_est_volume_bbl",
        corrected_value=5.4,
        author_user_id="U-201",
        reason="Field crew measured 5.4 bbl",
    )
    raw = conn.execute(
        "SELECT spill_est_volume_bbl FROM inspections WHERE inspection_id = 'INS-0151-0201'"
    ).fetchone()
    findings = {item.rule_id: item for item in evaluate_spill_compliance(conn, "INS-0151-0201", as_of=EVALUATION_TODAY)}
    assert raw["spill_est_volume_bbl"] == 4.5
    assert findings["SR-01"].status == "overdue"
    assert findings["SR-01"].field_values["classification"] == "reportable"
    assert findings["SR-02"].status == "noncompliant"
    assert findings["SR-02"].observed_hours == 6.4
    assert findings["SR-03"].status == "pending"
    assert findings["SR-03"].deadline_at.startswith("2026-11-23")
    conn.close()


def test_q14_northeast_block_7_pump_jack_rca(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    default = {item["asset_id"] for item in wells_requiring_rca(conn, area="Northeast Block 7", equipment_kind="pump_jack")}
    assert default == {"WELL-0108", "WELL-0121", "WELL-0138"}
    apply_rule_override(
        conn,
        rule_id="RCA",
        parameter="threshold",
        value=2,
        well_type="T350",
        equipment_kind="pump_jack",
        author_user_id="U-201",
        reason="T350 pump jack RCA after 2 repairs",
    )
    overridden = {item["asset_id"]: item for item in wells_requiring_rca(conn, area="Northeast Block 7", equipment_kind="pump_jack")}
    assert set(overridden) == {"WELL-0108", "WELL-0120", "WELL-0121", "WELL-0138"}
    assert "WELL-0102" not in overridden
    assert overridden["WELL-0120"]["well_type"] == "T350"
    assert overridden["WELL-0120"]["repair_count"] == 2
    conn.close()
