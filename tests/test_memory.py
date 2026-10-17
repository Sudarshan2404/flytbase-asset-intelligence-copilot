from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import connect, init_schema
from app.domain.compliance import wells_requiring_rca
from app.domain.corrections import (
    apply_structured_correction,
    correction_history,
    effective_inspection,
    record_correction,
)
from app.domain.knowledge import add_shared_note, list_shared_notes, record_statement, spill_feedback_conflict
from app.domain.memory import add_message, resolve_discussed_wells, start_conversation
from app.domain.queries import current_production_status


def _connect(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    init_schema(conn)
    return conn


def _scratch(source_path: Path) -> sqlite3.Connection:
    source = _connect(source_path)
    clone = sqlite3.connect(":memory:")
    clone.row_factory = sqlite3.Row
    source.backup(clone)
    source.close()
    return clone


def _memory() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO wells (asset_id, name, well_type, area_id, recorded_status, source_path, raw_json)
        VALUES
            ('WELL-X', 'Test', 'T200', 'NE-7', 'active', 'w', '{}'),
            ('WELL-T350', 'Deep', 'T350', 'NE-7', 'active', 'w', '{}')
        """
    )
    conn.execute(
        "INSERT INTO areas (area_id, name, team_id, source_path, raw_json) VALUES ('NE-7', 'Northeast Block 7', 'TEAM-NE', 'a', '{}')"
    )
    conn.execute("INSERT INTO area_assets (area_id, asset_id) VALUES ('NE-7', 'WELL-X'), ('NE-7', 'WELL-T350')")
    conn.execute(
        """
        INSERT INTO inspections (
            inspection_id, asset_id, inspected_at, wellhead_temp_c, ambient_temp_c,
            spill_detected, spill_est_volume_bbl, spill_area_pct, notes, source_path, raw_json
        ) VALUES (
            'INS-X-1', 'WELL-X', '2026-10-15T09:45:00Z', 24.0, 19.9, 0, NULL, NULL,
            'Pump jack not moving.', 'i', '{}'
        )
        """
    )
    return conn


def test_correction_validation_rejects_bad_targets_and_types() -> None:
    conn = _memory()
    with pytest.raises(ValueError, match="Unknown inspection"):
        record_correction(conn, scope="inspection", target_id="INS-MISSING", field_name="wellhead_temp_c", corrected_value=41.0)
    with pytest.raises(ValueError, match="Unsupported correction field"):
        record_correction(conn, scope="inspection", target_id="INS-X-1", field_name="cost_usd", corrected_value=1)
    with pytest.raises(ValueError, match="must be numeric"):
        record_correction(conn, scope="inspection", target_id="INS-X-1", field_name="wellhead_temp_c", corrected_value="hot")
    with pytest.raises(ValueError, match="Unknown well_type"):
        record_correction(conn, scope="well_type", target_id="T999", rule_id="RCA", parameter="threshold", corrected_value=2, equipment_kind="pump_jack")
    conn.close()


def test_correction_history_is_append_only_and_raw_row_unchanged() -> None:
    conn = _memory()
    first = apply_structured_correction(
        conn,
        inspection_id="INS-X-1",
        field_name="wellhead_temp_c",
        corrected_value=30.0,
        author_user_id="U-201",
        reason="first pass",
        created_at="2026-10-16T10:00:00Z",
    )
    second = apply_structured_correction(
        conn,
        inspection_id="INS-X-1",
        field_name="wellhead_temp_c",
        corrected_value=41.0,
        author_user_id="U-201",
        reason="handheld re-measure",
        created_at="2026-10-16T11:00:00Z",
    )
    raw = conn.execute("SELECT wellhead_temp_c FROM inspections WHERE inspection_id = 'INS-X-1'").fetchone()
    history = correction_history(conn, inspection_id="INS-X-1")
    view = effective_inspection(conn, "INS-X-1")
    assert raw["wellhead_temp_c"] == 24.0
    assert [item["correction_id"] for item in history] == [first, second]
    assert history[0]["status"] == "superseded"
    assert history[1]["status"] == "active"
    assert history[0]["original_value"] == 24.0
    assert view.wellhead_temp_c == 41.0
    assert view.original["wellhead_temp_c"] == 24.0
    assert any(item["correction_id"] == second for item in view.provenance)
    conn.close()


def test_shared_correction_changes_status_for_every_user() -> None:
    conn = _memory()
    before = current_production_status(conn, "WELL-X")
    assert before["producing"] is False
    apply_structured_correction(
        conn,
        inspection_id="INS-X-1",
        field_name="wellhead_temp_c",
        corrected_value=41.0,
        author_user_id="U-201",
        reason="faulty thermal gun",
    )
    for _user in ("U-201", "U-202", "U-401"):
        status = current_production_status(conn, "WELL-X")
        assert status["producing"] is True
        assert status["original_wellhead_temp_c"] == 24.0
        assert status["delta_c"] == pytest.approx(21.1)
        assert status["provenance"][0]["author_user_id"] == "U-201"
    conn.close()


def test_well_type_rca_rule_is_recorded_through_correction_api() -> None:
    conn = _memory()
    for index, started in enumerate(["2026-01-01T08:00:00Z", "2026-04-01T08:00:00Z"], start=1):
        conn.execute(
            """
            INSERT INTO maintenance (
                maintenance_id, asset_id, started_at, maintenance_type, equipment_kind, source_path, raw_json
            ) VALUES (?, 'WELL-T350', ?, 'corrective', 'pump_jack', 'm', '{}')
            """,
            (f"MNT-T-{index}", started),
        )
    before = wells_requiring_rca(conn, area="Northeast Block 7", equipment_kind="pump_jack")
    assert "WELL-T350" not in {item["asset_id"] for item in before}
    record_correction(
        conn,
        scope="well_type",
        target_id="T350",
        rule_id="RCA",
        parameter="threshold",
        corrected_value=2,
        equipment_kind="pump_jack",
        author_user_id="U-201",
        reason="T350 pump jack RCA after 2 repairs",
    )
    after = {item["asset_id"]: item for item in wells_requiring_rca(conn, area="Northeast Block 7", equipment_kind="pump_jack")}
    assert after["WELL-T350"]["repair_count"] == 2
    assert after["WELL-T350"]["threshold"] == 2
    conn.close()


def test_shared_notes_are_visible_to_different_users_and_stay_unverified() -> None:
    conn = _memory()
    note = add_shared_note(
        conn,
        asset_id="WELL-X",
        author_user_id="U-102",
        text="WELL-X will be shut in from 17 Oct for a 10-day planned workover.",
        created_at="2026-10-16T08:00:00Z",
        valid_from="2026-10-17",
        valid_to="2026-10-27",
    )
    assert note["verification_status"] == "unverified"
    for user in ("U-201", "U-202"):
        visible = list_shared_notes(conn, "WELL-X", as_of="2026-10-18", requesting_user_id=user)
        assert any(item["note_id"] == note["note_id"] for item in visible)
        assert all(item["verification_status"] != "confirmed" for item in visible if item["note_id"] == note["note_id"])
    expired = list_shared_notes(conn, "WELL-X", as_of="2026-10-28")
    assert all(item["note_id"] != note["note_id"] for item in expired)
    conn.close()


def test_private_memory_isolates_users_and_resolves_discussed_wells() -> None:
    conn = _memory()
    michael = start_conversation(conn, "U-201", started_at="2026-10-15T09:00:00Z", title="downtime")
    add_message(
        conn,
        conversation_id=michael,
        user_id="U-201",
        role="user",
        content="Which 3 wells in Northeast Block 7 had the most downtime?",
        created_at="2026-10-15T09:00:00Z",
        entities=[("well", "WELL-0121"), ("well", "WELL-0138"), ("well", "WELL-0107")],
    )
    resolved = resolve_discussed_wells(conn, "U-201", limit=3)
    assert resolved["found"] is True
    assert set(resolved["wells"]) == {"WELL-0121", "WELL-0138", "WELL-0107"}
    jessica = resolve_discussed_wells(conn, "U-202", limit=3)
    assert jessica["found"] is False
    assert jessica["wells"] == []
    assert "no earlier conversation" in jessica["message"]
    conn.close()


def test_cannot_write_to_another_users_conversation() -> None:
    conn = _memory()
    conversation = start_conversation(conn, "U-201", started_at="2026-10-15T09:00:00Z")
    with pytest.raises(ValueError, match="another user's conversation"):
        add_message(
            conn,
            conversation_id=conversation,
            user_id="U-202",
            role="user",
            content="peek",
            created_at="2026-10-15T10:00:00Z",
        )
    conn.close()


def test_conflicting_claim_stays_unconfirmed() -> None:
    conn = _memory()
    result = record_statement(
        conn,
        author_user_id="U-204",
        asset_id="WELL-X",
        text="WELL-X is running fine.",
        asserted_state="producing",
        created_at="2026-10-16T12:00:00Z",
    )
    assert result["kind"] == "conflict"
    assert result["needs_confirmation"] is True
    assert result["status"] == "unconfirmed"
    assert result["conflicts_with"][0]["record_id"] == "INS-X-1"
    assert result["effective_production"]["producing"] is False
    raw = conn.execute("SELECT wellhead_temp_c FROM inspections WHERE inspection_id = 'INS-X-1'").fetchone()
    assert raw["wellhead_temp_c"] == 24.0
    conn.close()


def test_statement_matching_inspection_is_shared_but_unverified() -> None:
    conn = _memory()
    result = record_statement(
        conn,
        author_user_id="U-201",
        asset_id="WELL-X",
        text="WELL-X has been shut in for planned work since 14 Oct.",
        asserted_state="shut_in",
        created_at="2026-10-16T11:00:00Z",
    )
    assert result["kind"] == "shared_note"
    assert result["status"] == "unverified"
    assert result["needs_confirmation"] is False
    notes = list_shared_notes(conn, "WELL-X", requesting_user_id="U-202")
    assert any(item["note_id"] == result["note_id"] for item in notes)
    conn.close()


def test_operator_feedback_conflict_without_formal_correction(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    result = spill_feedback_conflict(conn, "INS-0101-0197")
    assert result["keeps_established_detection"] is True
    assert result["effective_spill_detected"] is True
    assert result["effective_volume_bbl"] == pytest.approx(1.0)
    ids = {item["note_id"] for item in result["operator_feedback"]}
    assert {"CTX-0101-002", "CTX-0101-003"} <= ids
    assert result["formal_corrections"] == []
    raw = conn.execute(
        "SELECT spill_detected, spill_est_volume_bbl FROM inspections WHERE inspection_id = 'INS-0101-0197'"
    ).fetchone()
    assert raw["spill_detected"] == 1
    assert raw["spill_est_volume_bbl"] == 1.0
    conn.close()


def test_regression_formal_void_then_structured_override(baseline_db: Path) -> None:
    conn = _scratch(baseline_db)
    view = effective_inspection(conn, "INS-0001-0070")
    assert view.original["spill_detected"] is True
    assert view.spill_detected is False
    apply_structured_correction(
        conn,
        inspection_id="INS-0001-0070",
        field_name="spill_detected",
        corrected_value=True,
        author_user_id="U-401",
        reason="HSE reconfirmed crude",
    )
    restored = effective_inspection(conn, "INS-0001-0070")
    raw = conn.execute("SELECT spill_detected FROM inspections WHERE inspection_id = 'INS-0001-0070'").fetchone()
    assert raw["spill_detected"] == 1
    assert restored.spill_detected is True
    assert restored.formal_correction_ids == ("CTX-0001-001",)
    conn.close()


def test_q15_shared_correction_on_baseline_well_0164(baseline_db: Path) -> None:
    source = _connect(baseline_db)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    source.backup(conn)
    source.close()
    apply_structured_correction(
        conn,
        inspection_id="INS-0164-0205",
        field_name="wellhead_temp_c",
        corrected_value=41.0,
        author_user_id="U-201",
        reason="handheld thermal gun re-measure",
    )
    raw = conn.execute("SELECT wellhead_temp_c FROM inspections WHERE inspection_id = 'INS-0164-0205'").fetchone()
    jessica = current_production_status(conn, "WELL-0164")
    assert raw["wellhead_temp_c"] == 24.0
    assert jessica["producing"] is True
    assert jessica["delta_c"] == pytest.approx(21.1)
    assert jessica["provenance"][0]["author_user_id"] == "U-201"
    conn.close()
