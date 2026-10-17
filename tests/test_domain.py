from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import connect, init_schema
from app.domain.corrections import apply_structured_correction, effective_inspection, effective_spill
from app.domain.dates import in_date_window
from app.domain.production import production_from_temps, temperature_delta
from app.domain.queries import (
    current_production_status,
    downtime_for_well,
    effective_spill_summary,
    effective_spills,
    latest_inspection,
    maintenance_started_in_window,
    production_history,
    top_wells_by_downtime,
)
from app.domain.spills import classify_spill_volume


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
        INSERT INTO wells (
            asset_id, name, well_type, area_id, recorded_status, source_path, raw_json
        ) VALUES ('WELL-X', 'Test well', 'T200', 'SW-23', 'active', 'wells', '{}')
        """
    )
    conn.execute(
        """
        INSERT INTO areas (area_id, name, team_id, source_path, raw_json)
        VALUES ('SW-23', 'Southwest Block 23', 'TEAM-SW', 'areas', '{}')
        """
    )
    conn.execute("INSERT INTO area_assets (area_id, asset_id) VALUES ('SW-23', 'WELL-X')")
    return conn


def _insert_inspection(
    conn: sqlite3.Connection,
    inspection_id: str,
    inspected_at: str,
    *,
    wellhead: float | None,
    ambient: float | None,
    spill: bool = False,
    volume: float | None = None,
    asset_id: str = "WELL-X",
) -> None:
    conn.execute(
        """
        INSERT INTO inspections (
            inspection_id, asset_id, inspected_at, wellhead_temp_c, ambient_temp_c,
            spill_detected, spill_est_volume_bbl, source_path, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'inspections', '{}')
        """,
        (inspection_id, asset_id, inspected_at, wellhead, ambient, int(spill) if spill is not None else None, volume),
    )


@pytest.mark.parametrize(
    ("well_type", "delta", "producing"),
    [
        ("T200", 15.0, True),
        ("T200", 14.999, False),
        ("T200", 15.1, True),
        ("T350", 20.0, True),
        ("T350", 19.999, False),
        ("T350", 20.1, True),
    ],
)
def test_temperature_thresholds_include_exact_boundaries(well_type: str, delta: float, producing: bool) -> None:
    result = production_from_temps(well_type, 40.0, 40.0 - delta)
    assert result["producing"] is producing
    assert result["delta_c"] == pytest.approx(delta)


def test_production_never_uses_absolute_temperature() -> None:
    assert temperature_delta(51.6, 43.1) == pytest.approx(8.5)
    hot_idle = production_from_temps("T200", 51.6, 43.1)
    assert hot_idle["producing"] is False


def test_missing_and_null_temperatures_are_unknown() -> None:
    assert production_from_temps("T200", None, 20.0)["status"] == "missing_measurements"
    assert production_from_temps("T200", 30.0, None)["producing"] is None
    assert production_from_temps("T200", "", "")["status"] == "missing_measurements"


def test_unknown_well_type_is_explicit() -> None:
    result = production_from_temps("T999", 50.0, 10.0)
    assert result["status"] == "unknown_well_type"
    assert result["producing"] is None
    assert result["threshold_c"] is None


@pytest.mark.parametrize(
    ("volume", "label"),
    [
        (0.9, "minor"),
        (1.0, "moderate"),
        (4.999, "moderate"),
        (5.0, "reportable"),
        (24.999, "reportable"),
        (25.0, "major"),
        (None, "unknown_volume"),
    ],
)
def test_spill_classification_boundaries(volume, label) -> None:
    assert classify_spill_volume(volume) == label


def test_date_window_is_inclusive_on_both_boundaries() -> None:
    assert in_date_window("2025-01-01T23:59:00Z", "2025-01-01", "2025-12-31")
    assert in_date_window("2025-12-31T00:00:00Z", "2025-01-01", "2025-12-31")
    assert not in_date_window("2024-12-31T23:00:00Z", "2025-01-01", "2025-12-31")
    assert not in_date_window("2026-01-01T00:00:00Z", "2025-01-01", "2025-12-31")


def test_missed_inspections_add_zero_downtime() -> None:
    conn = _memory()
    _insert_inspection(conn, "INS-A", "2025-01-02T12:00:00Z", wellhead=20.0, ambient=20.0)
    _insert_inspection(conn, "INS-B", "2025-01-23T12:00:00Z", wellhead=20.0, ambient=20.0)
    result = downtime_for_well(conn, "WELL-X", "2025-01-01", "2025-01-31")
    assert result["inspection_count"] == 2
    assert result["days"] == 14
    conn.close()


def test_missing_measurements_do_not_count_as_downtime() -> None:
    conn = _memory()
    _insert_inspection(conn, "INS-A", "2025-01-02T12:00:00Z", wellhead=None, ambient=20.0)
    result = downtime_for_well(conn, "WELL-X", "2025-01-01", "2025-01-31")
    assert result["days"] == 0
    assert result["unknown_inspections"][0]["inspection_id"] == "INS-A"
    conn.close()


def test_seven_day_downtime_from_not_producing_inspections() -> None:
    conn = _memory()
    _insert_inspection(conn, "INS-A", "2025-07-24T15:30:00Z", wellhead=51.6, ambient=43.1)
    _insert_inspection(conn, "INS-B", "2025-07-31T15:30:00Z", wellhead=40.0, ambient=20.0)
    result = downtime_for_well(conn, "WELL-X", "2025-07-01", "2025-07-31")
    assert result["days"] == 7
    assert [item["inspection_id"] for item in result["not_producing_inspections"]] == ["INS-A"]
    conn.close()


def test_maintenance_is_filtered_by_started_at_not_completed_at() -> None:
    conn = _memory()
    conn.execute(
        """
        INSERT INTO maintenance (
            maintenance_id, asset_id, started_at, completed_at, downtime_hours,
            source_path, raw_json
        ) VALUES
            ('MNT-OUT', 'WELL-X', '2024-12-31T08:00:00Z', '2025-01-10T08:00:00Z', 999, 'm', '{}'),
            ('MNT-IN', 'WELL-X', '2025-01-02T08:00:00Z', '2025-02-01T08:00:00Z', 3, 'm', '{}')
        """
    )
    rows = maintenance_started_in_window(conn, asset_id="WELL-X", start="2025-01-01", end="2025-01-31")
    assert [row["maintenance_id"] for row in rows] == ["MNT-IN"]
    conn.close()


def test_correction_excludes_spill_and_leaves_raw_row_unchanged() -> None:
    conn = _memory()
    _insert_inspection(
        conn,
        "INS-SPILL",
        "2024-03-07T09:15:00Z",
        wellhead=32.1,
        ambient=11.3,
        spill=True,
        volume=0.9,
    )
    conn.execute(
        """
        INSERT INTO context_notes (
            note_id, asset_id, created_at, note_type, inspection_id, text, source_path, raw_json
        ) VALUES (
            'CTX-1', 'WELL-X', '2024-03-07T16:05:00Z', 'correction', 'INS-SPILL',
            'Rainwater, not crude.', 'notes', '{}'
        )
        """
    )
    raw_before = conn.execute(
        "SELECT spill_detected, spill_est_volume_bbl FROM inspections WHERE inspection_id = 'INS-SPILL'"
    ).fetchone()
    view = effective_inspection(conn, "INS-SPILL")
    spill = effective_spill(view)
    raw_after = conn.execute(
        "SELECT spill_detected, spill_est_volume_bbl FROM inspections WHERE inspection_id = 'INS-SPILL'"
    ).fetchone()

    assert raw_before["spill_detected"] == 1
    assert raw_before["spill_est_volume_bbl"] == 0.9
    assert dict(raw_after) == dict(raw_before)
    assert view.original["spill_detected"] is True
    assert spill["effective_spill_detected"] is False
    assert spill["excluded_by_correction"] is True
    assert effective_spills(conn, asset_id="WELL-X") == []
    summary = effective_spill_summary(conn, asset_id="WELL-X")
    assert summary["spill_count"] == 0
    conn.close()


def test_every_spill_query_uses_the_same_effective_logic() -> None:
    conn = _memory()
    _insert_inspection(conn, "INS-KEEP", "2026-06-11T14:30:00Z", wellhead=40.0, ambient=20.0, spill=True, volume=6.8)
    _insert_inspection(conn, "INS-DROP", "2024-03-07T09:15:00Z", wellhead=32.1, ambient=11.3, spill=True, volume=0.9)
    conn.execute(
        """
        INSERT INTO context_notes (
            note_id, asset_id, created_at, note_type, inspection_id, text, source_path, raw_json
        ) VALUES (
            'CTX-1', 'WELL-X', '2024-03-07T16:05:00Z', 'correction', 'INS-DROP',
            'Not a spill.', 'notes', '{}'
        )
        """
    )
    listed = {item["inspection_id"] for item in effective_spills(conn, asset_id="WELL-X")}
    summarized = {item["inspection_id"] for item in effective_spill_summary(conn, asset_id="WELL-X")["spills"]}
    assert listed == summarized == {"INS-KEEP"}
    conn.close()


def test_structured_correction_overrides_volume_without_mutating_original() -> None:
    conn = _memory()
    _insert_inspection(conn, "INS-VOL", "2026-09-24T15:30:00Z", wellhead=40.0, ambient=20.0, spill=True, volume=4.5)
    apply_structured_correction(
        conn,
        inspection_id="INS-VOL",
        field_name="spill_est_volume_bbl",
        corrected_value=5.4,
        author_user_id="U-201",
        reason="Field crew measured 5.4 bbl",
    )
    raw = conn.execute("SELECT spill_est_volume_bbl FROM inspections WHERE inspection_id = 'INS-VOL'").fetchone()
    spill = effective_spill(effective_inspection(conn, "INS-VOL"))
    assert raw["spill_est_volume_bbl"] == 4.5
    assert spill["original_volume_bbl"] == 4.5
    assert spill["effective_volume_bbl"] == 5.4
    assert spill["classification"] == "reportable"
    conn.close()


def test_unknown_well_and_missing_inspection_are_explicit(mini_db: Path) -> None:
    conn = _connect(mini_db)
    missing_well = current_production_status(conn, "WELL-DOES-NOT-EXIST")
    assert missing_well["status"] == "unknown_well"
    assert missing_well["producing"] is None

    no_inspection = current_production_status(conn, "WELL-0002")
    assert no_inspection["status"] == "no_inspection"
    assert no_inspection["recorded_status"] == "active"
    assert no_inspection["recorded_status_authoritative"] is False
    conn.close()


def test_mini_dataset_correction_and_latest_status(mini_db: Path) -> None:
    conn = _connect(mini_db)
    latest = latest_inspection(conn, "WELL-0001")
    assert latest is not None
    status = current_production_status(conn, "WELL-0001")
    assert status["producing"] is False
    assert status["recorded_status"] == "active"
    assert status["recorded_status_authoritative"] is False

    raw = conn.execute(
        "SELECT spill_detected FROM inspections WHERE inspection_id = 'INS-0001-0002'"
    ).fetchone()
    assert raw["spill_detected"] == 1
    summary = effective_spill_summary(conn, asset_id="WELL-0001")
    assert summary["spill_count"] == 0
    conn.close()


def test_q4_well_0001_spills_last_four_years(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    summary = effective_spill_summary(
        conn,
        asset_id="WELL-0001",
        start="2022-10-16",
        end="2026-10-16",
    )
    raw_detections = conn.execute(
        """
        SELECT COUNT(*) FROM inspections
        WHERE asset_id = 'WELL-0001' AND spill_detected = 1
          AND date(inspected_at) >= date('2022-10-16')
          AND date(inspected_at) <= date('2026-10-16')
        """
    ).fetchone()[0]
    assert raw_detections == 7
    assert summary["spill_count"] == 6
    assert summary["total_volume_bbl"] == pytest.approx(15.1)
    dates = [item["inspected_at"][:10] for item in summary["spills"]]
    assert dates == [
        "2023-03-16",
        "2023-04-27",
        "2023-09-28",
        "2023-10-19",
        "2026-06-11",
        "2026-10-01",
    ]
    excluded = effective_inspection(conn, "INS-0001-0070")
    assert excluded.original["spill_detected"] is True
    assert excluded.spill_detected is False
    conn.close()


def test_q5_well_0001_downtime_in_2025(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    result = downtime_for_well(conn, "WELL-0001", "2025-01-01", "2025-12-31")
    assert result["days"] == 21
    dates = [item["inspected_at"][:10] for item in result["not_producing_inspections"]]
    assert dates == ["2025-07-24", "2025-10-16", "2025-10-23"]
    july = result["not_producing_inspections"][0]
    assert july["delta_c"] == pytest.approx(8.5)
    assert july["wellhead_temp_c"] == pytest.approx(51.6)
    conn.close()


def test_q6_northeast_block_7_downtime_last_four_months(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    ranked = top_wells_by_downtime(
        conn,
        "Northeast Block 7",
        "2026-06-16",
        "2026-10-16",
    )
    top = [(item["asset_id"], item["days"], item["well_type"]) for item in ranked["wells"][:4]]
    assert top[0] == ("WELL-0121", 49, "T200")
    assert top[1][0] in {"WELL-0138", "WELL-0107"}
    assert top[1][1] == 42
    assert top[2][0] in {"WELL-0138", "WELL-0107"}
    assert top[2][1] == 42
    assert {top[1][0], top[2][0]} == {"WELL-0138", "WELL-0107"}
    assert top[3] == ("WELL-0119", 28, top[3][2])
    conn.close()


def test_production_history_window_uses_inclusive_dates(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    history = production_history(conn, "WELL-0001", "2026-07-23", "2026-10-15")
    assert len(history["readings"]) == 13
    down = [item["inspected_at"][:10] for item in history["readings"] if item["producing"] is False]
    assert down == ["2026-10-08", "2026-10-15"]
    conn.close()
