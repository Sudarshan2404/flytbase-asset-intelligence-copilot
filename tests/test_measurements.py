from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.copilot.loop import run_tool_loop
from app.copilot.provider import FunctionCall, ModelTurn
from app.copilot.schemas import ChatRequest
from app.copilot.service import handle_chat
from app.copilot.tools import execute_tool
from app.db import connect, init_schema
from app.domain.measurements import (
    available_measurements,
    infer_quantity_window,
    is_oil_quantity_question,
    oil_production_totals,
)


class ScriptedProvider:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.seen_tools: list[list[str]] = []

    def generate(self, contents, tools) -> ModelTurn:
        self.seen_tools.append([item["name"] for item in tools])
        if not self.turns:
            raise AssertionError("No scripted model turns remain")
        return self.turns.pop(0)


def _memory() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    for asset_id, name in (("WELL-0001", "One"), ("WELL-0002", "Two")):
        conn.execute(
            """
            INSERT INTO wells (asset_id, name, well_type, area_id, recorded_status, source_path, raw_json)
            VALUES (?, ?, 'T200', 'SW-23', 'active', 'w', '{}')
            """,
            (asset_id, name),
        )
    return conn


def _insert(conn: sqlite3.Connection, inspection_id: str, asset_id: str, inspected_at: str, raw: dict) -> None:
    conn.execute(
        """
        INSERT INTO inspections (
            inspection_id, asset_id, inspected_at, wellhead_temp_c, ambient_temp_c,
            spill_detected, spill_est_volume_bbl, source_path, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'inspections', ?)
        """,
        (
            inspection_id,
            asset_id,
            inspected_at,
            raw.get("wellhead_temp_c"),
            raw.get("ambient_temp_c"),
            int(bool(raw.get("spill_detected"))),
            raw.get("spill_est_volume_bbl"),
            json.dumps(raw),
        ),
    )


def test_quantity_intent_does_not_match_status_or_spills() -> None:
    assert is_oil_quantity_question("Which well produced the most oil in 2026?")
    assert is_oil_quantity_question("How much oil did WELL-0001 produce in 2025?")
    assert not is_oil_quantity_question("Is WELL-0001 producing oil right now?")
    assert not is_oil_quantity_question("How many oil spills has WELL-0001 had in the last 4 years?")
    assert infer_quantity_window("Which well produced the most oil in 2026?") == ("2026-01-01", "2026-12-31")
    assert infer_quantity_window("totals from 2025-03-01 to 2025-06-30") == ("2025-03-01", "2025-06-30")


def test_oil_volume_unavailable_on_sample(mini_db: Path) -> None:
    conn = connect(mini_db)
    init_schema(conn)
    catalog = available_measurements(conn)
    assert catalog["metric_available"] is False
    assert catalog["production_volume_fields"] == []
    assert any(item["field"] == "spill_est_volume_bbl" for item in catalog["spill_volume_fields"])
    result = oil_production_totals(conn, "2026-01-01", "2026-12-31")
    assert result["metric_available"] is False
    assert result["wells"] == []
    assert result["start"] == "2026-01-01"
    assert result["end"] == "2026-12-31"
    assert "not recorded" in result["message"].lower()
    assert "WELL-" not in result["message"] or "no well" in result["message"].lower()
    conn.close()


def test_oil_volume_available_ranks_requested_year_only() -> None:
    conn = _memory()
    _insert(
        conn,
        "INS-0001-2025",
        "WELL-0001",
        "2025-06-01T12:00:00Z",
        {"oil_volume_bbl": 500, "wellhead_temp_c": 40, "ambient_temp_c": 20, "spill_est_volume_bbl": 9.0},
    )
    _insert(
        conn,
        "INS-0001-2026A",
        "WELL-0001",
        "2026-02-01T12:00:00Z",
        {"oil_volume_bbl": 10, "wellhead_temp_c": 40, "ambient_temp_c": 20, "spill_est_volume_bbl": 8.0},
    )
    _insert(
        conn,
        "INS-0001-2026B",
        "WELL-0001",
        "2026-08-01T12:00:00Z",
        {"oil_volume_bbl": 15, "wellhead_temp_c": 40, "ambient_temp_c": 20},
    )
    _insert(
        conn,
        "INS-0002-2026",
        "WELL-0002",
        "2026-07-01T12:00:00Z",
        {"oil_volume_bbl": 40, "wellhead_temp_c": 22, "ambient_temp_c": 20, "spill_detected": True, "spill_est_volume_bbl": 100},
    )
    result = oil_production_totals(conn, "2026-01-01", "2026-12-31")
    assert result["metric_available"] is True
    assert result["field"] == "oil_volume_bbl"
    assert result["start"] == "2026-01-01"
    assert result["end"] == "2026-12-31"
    assert [item["asset_id"] for item in result["wells"]] == ["WELL-0002", "WELL-0001"]
    assert result["wells"][0]["total_bbl"] == 40
    assert result["wells"][1]["total_bbl"] == 25
    assert result["top_asset_id"] == "WELL-0002"
    assert 500 not in {item["total_bbl"] for item in result["wells"]}
    assert 100 not in {item["total_bbl"] for item in result["wells"]}
    conn.close()


def test_oil_volume_column_is_used_when_present() -> None:
    conn = _memory()
    conn.execute("ALTER TABLE inspections ADD COLUMN oil_produced_bbl REAL")
    conn.execute(
        """
        INSERT INTO inspections (
            inspection_id, asset_id, inspected_at, oil_produced_bbl, source_path, raw_json
        ) VALUES
            ('INS-A', 'WELL-0001', '2026-04-01T00:00:00Z', 12, 'i', '{}'),
            ('INS-B', 'WELL-0002', '2026-04-08T00:00:00Z', 18, 'i', '{}')
        """
    )
    result = oil_production_totals(conn, "2026-01-01", "2026-12-31")
    assert result["metric_available"] is True
    assert result["field"] == "oil_produced_bbl"
    assert result["top_asset_id"] == "WELL-0002"
    assert result["wells"][0]["total_bbl"] == 18
    conn.close()


def test_quantity_question_rejects_unrelated_tools_and_stays_concise(mini_db: Path) -> None:
    conn = connect(mini_db)
    init_schema(conn)
    provider = ScriptedProvider(
        [
            ModelTurn(
                function_calls=[
                    FunctionCall(name="get_effective_spills", arguments={"start": "2026-01-01", "end": "2026-10-16"}),
                    FunctionCall(name="get_top_downtime_wells", arguments={"start": "2026-01-01", "end": "2026-10-16"}),
                ]
            ),
            ModelTurn(text='{"answer":"I investigated spills and downtime and WELL-0001 might have produced the most oil."}'),
        ]
    )
    response = run_tool_loop(
        provider,
        conn,
        user_id="U-201",
        user_message="Which well produced the most oil in 2026?",
        history=[],
    )
    assert provider.seen_tools
    assert set(provider.seen_tools[0]) <= {"get_available_measurements", "get_oil_production_totals"}
    names = [item.name for item in response.tool_trace]
    assert "get_effective_spills" not in {item.name for item in response.tool_trace if item.ok}
    assert "get_top_downtime_wells" not in {item.name for item in response.tool_trace if item.ok}
    assert "get_oil_production_totals" in names
    assert "not recorded" in response.answer.lower()
    assert "investigated" not in response.answer.lower()
    assert "WELL-0001 might" not in response.answer
    assert response.answer.count("\n") <= 3
    conn.close()


def test_quantity_question_without_tools_still_inspects_schema(mini_db: Path) -> None:
    conn = connect(mini_db)
    init_schema(conn)
    response = run_tool_loop(
        ScriptedProvider([ModelTurn(text="Let me think through every table and then guess WELL-0001.")]),
        conn,
        user_id="U-201",
        user_message="Which well produced the most oil in 2026?",
        history=[],
    )
    assert "not recorded" in response.answer.lower()
    assert "guess" not in response.answer.lower()
    assert any(item.name == "get_oil_production_totals" for item in response.tool_trace)
    conn.close()


def test_available_volume_chat_uses_ranked_totals() -> None:
    conn = _memory()
    _insert(
        conn,
        "INS-0001-2026",
        "WELL-0001",
        "2026-03-01T12:00:00Z",
        {"oil_volume_bbl": 11, "wellhead_temp_c": 50, "ambient_temp_c": 10},
    )
    _insert(
        conn,
        "INS-0002-2026",
        "WELL-0002",
        "2026-03-08T12:00:00Z",
        {"oil_volume_bbl": 33, "wellhead_temp_c": 21, "ambient_temp_c": 20},
    )
    response = handle_chat(
        conn,
        ChatRequest(message="Which well produced the most oil in 2026?", user_id="U-201"),
        provider=ScriptedProvider(
            [
                ModelTurn(
                    function_calls=[
                        FunctionCall(
                            name="get_oil_production_totals",
                            arguments={"start": "2026-01-01", "end": "2026-12-31"},
                        )
                    ]
                )
            ]
        ),
    )
    assert "WELL-0002" in response.answer
    assert "33" in response.answer
    assert response.visuals
    assert {item.type for item in response.visuals} >= {"bar_chart", "table"}
    assert response.citations
    conn.close()


def test_status_question_still_has_full_tool_set(mini_db: Path) -> None:
    conn = connect(mini_db)
    init_schema(conn)
    provider = ScriptedProvider(
        [
            ModelTurn(function_calls=[FunctionCall(name="get_well_status", arguments={"asset_id": "WELL-0001"})]),
            ModelTurn(text='{"answer":"WELL-0001 is not producing oil.","visual":{"type":"status_card"}}'),
        ]
    )
    response = run_tool_loop(
        provider,
        conn,
        user_id="U-201",
        user_message="Is WELL-0001 producing oil right now?",
        history=[],
    )
    assert "get_well_status" in provider.seen_tools[0]
    assert "get_effective_spills" in provider.seen_tools[0]
    assert response.answer.startswith("WELL-0001")
    conn.close()


def test_volume_tool_does_not_treat_spill_as_production(mini_db: Path) -> None:
    conn = connect(mini_db)
    init_schema(conn)
    payload = execute_tool(
        "get_oil_production_totals",
        {"start": "2026-01-01", "end": "2026-12-31"},
        conn,
        {"user_id": "U-201", "user_message": "Which well produced the most oil in 2026?"},
    )
    assert payload["metric_available"] is False
    assert payload["wells"] == []
    conn.close()
