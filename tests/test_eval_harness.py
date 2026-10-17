from __future__ import annotations

import sqlite3
from pathlib import Path

from app.copilot.tools import execute_tool
from app.db import connect, init_schema
from app.eval.harness import run_evaluation
from app.domain.queries import (
    current_production_status,
    maintenance_costs,
    non_producing_in_area,
    spill_heatmap_by_area,
    spills_with_coordinates,
)


def _connect(path: Path):
    conn = connect(path)
    init_schema(conn)
    return conn


def test_all_eighteen_questions_against_sample_data(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    results = run_evaluation(conn)
    failed = [item.question_id for item in results if not item.passed]
    assert failed == [], {item.question_id: item.failure or item.actual for item in results if not item.passed}
    conn.close()


def test_q1(baseline_db: Path) -> None:
    from app.eval.harness import check_q1

    conn = _connect(baseline_db)
    assert check_q1(conn).passed
    conn.close()


def test_q2(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    payload = non_producing_in_area(conn, "Northeast Block 8")
    assert [item["asset_id"] for item in payload["wells"]] == ["WELL-0164", "WELL-0170", "WELL-0172", "WELL-0175"]
    by_id = {item["asset_id"]: item for item in payload["wells"]}
    assert by_id["WELL-0164"]["has_recorded_reason"] is False
    assert "Pump jack not moving" in (by_id["WELL-0164"]["notes"] or by_id["WELL-0164"]["reason"])
    assert by_id["WELL-0170"]["has_recorded_reason"] is True
    assert by_id["WELL-0172"]["has_recorded_reason"] is True
    assert "MNT-0170-031" in {job["maintenance_id"] for job in by_id["WELL-0170"]["open_maintenance"]}
    assert "MNT-0172-028" in {job["maintenance_id"] for job in by_id["WELL-0172"]["open_maintenance"]}
    conn.close()


def test_open_maintenance_treats_future_completed_at_as_in_progress(baseline_db: Path) -> None:
    from app.domain.queries import _open_maintenance

    conn = _connect(baseline_db)
    jobs = _open_maintenance(conn, "WELL-0170")
    assert [job["maintenance_id"] for job in jobs] == ["MNT-0170-031"]
    assert str(jobs[0]["completed_at"]).startswith("2026-10-28")
    conn.close()


def test_q11_private_memory_then_costs_from_dataset(baseline_db: Path) -> None:
    from app.domain.memory import add_message, resolve_discussed_wells, start_conversation

    source = _connect(baseline_db)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    source.backup(conn)
    source.close()
    conversation = start_conversation(conn, "U-201", started_at="2026-10-15T09:00:00Z")
    add_message(
        conn,
        conversation_id=conversation,
        user_id="U-201",
        role="user",
        content="Which 3 wells in Northeast Block 7 had the most downtime?",
        created_at="2026-10-15T09:00:00Z",
        entities=[("well", "WELL-0121"), ("well", "WELL-0138"), ("well", "WELL-0107")],
    )
    michael = resolve_discussed_wells(conn, "U-201")
    jessica = resolve_discussed_wells(conn, "U-202")
    assert michael["found"] is True
    assert set(michael["wells"]) == {"WELL-0121", "WELL-0138", "WELL-0107"}
    assert jessica["found"] is False
    costs = maintenance_costs(
        conn,
        asset_ids=michael["wells"],
        start="2026-01-01",
        end="2026-10-16",
    )
    by_id = {item["asset_id"]: item["cost_usd"] for item in costs["wells"]}
    assert by_id == {"WELL-0121": 25794, "WELL-0138": 46942, "WELL-0107": 5176}
    conn.close()


def test_non_producing_query_is_not_hardcoded_to_northeast_block_8(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    ne7 = non_producing_in_area(conn, "Northeast Block 7")
    ne8 = non_producing_in_area(conn, "Northeast Block 8")
    assert ne7["area_name"] == "Northeast Block 7"
    ne7_ids = {item["asset_id"] for item in ne7["wells"]}
    ne8_ids = {item["asset_id"] for item in ne8["wells"]}
    assert ne7_ids and ne8_ids
    assert ne7_ids != ne8_ids
    sample = next(iter(ne7_ids))
    assert current_production_status(conn, sample)["producing"] is False
    conn.close()


def test_q7(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    payload = spills_with_coordinates(conn, area="Northeast Block 8", start="2026-07-18", end="2026-10-16")
    ids = {item["asset_id"] for item in payload["markers"]}
    assert ids == {"WELL-0151", "WELL-0165", "WELL-0175", "WELL-0186"}
    assert payload["skipped_without_coordinates"] == 0
    conn.close()


def test_q8(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    payload = spill_heatmap_by_area(conn, start="2026-01-01", end="2026-10-16")
    totals = {}
    for cell in payload["cells"]:
        totals[cell["row"]] = totals.get(cell["row"], 0) + int(cell["value"] or 0)
    assert totals["Southwest Block 24"] == 43
    assert totals["Northeast Block 8"] == 26
    conn.close()


def test_q11(baseline_db: Path) -> None:
    conn = _connect(baseline_db)
    costs = maintenance_costs(
        conn,
        asset_ids=["WELL-0121", "WELL-0138", "WELL-0107"],
        start="2026-01-01",
        end="2026-10-16",
    )
    by_id = {item["asset_id"]: item["cost_usd"] for item in costs["wells"]}
    assert by_id == {"WELL-0121": 25794, "WELL-0138": 46942, "WELL-0107": 5176}
    conn.close()


def test_q15(baseline_db: Path) -> None:
    from app.eval.harness import check_q15

    conn = _connect(baseline_db)
    assert check_q15(conn).passed
    conn.close()


def test_cost_tool_uses_private_memory_not_other_user(mini_db: Path) -> None:
    from app.domain.memory import add_message, start_conversation

    conn = _connect(mini_db)
    conversation = start_conversation(conn, "U-201", started_at="2026-10-15T09:00:00Z")
    add_message(
        conn,
        conversation_id=conversation,
        user_id="U-201",
        role="user",
        content="discussed",
        created_at="2026-10-15T09:00:00Z",
        entities=[("well", "WELL-0001")],
    )
    jessica = execute_tool(
        "get_maintenance_costs",
        {"start": "2026-01-01", "end": "2026-10-16"},
        conn,
        {"user_id": "U-202"},
    )
    assert jessica["found"] is False
    assert jessica["wells"] == []
    conn.close()
