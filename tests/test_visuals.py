from __future__ import annotations

from pathlib import Path

from app.copilot.schemas import VisualSpec
from app.copilot.tools import execute_tool
from app.copilot.visuals import build_visuals, validate_visualization
from app.db import connect, init_schema


def test_unknown_visualization_type_is_rejected() -> None:
    assert validate_visualization({"type": "3d_globe", "title": "nope"}) is None


def test_malformed_payload_is_rejected() -> None:
    assert validate_visualization("not-a-spec") is None
    assert validate_visualization(None) is None


def test_status_visual_from_well_tool(mini_db: Path) -> None:
    conn = connect(mini_db)
    init_schema(conn)
    payload = execute_tool("get_well_status", {"asset_id": "WELL-0001"}, conn, {"user_id": "U-201"})
    visuals = build_visuals([("get_well_status", payload)], VisualSpec(type="status_card"))
    assert visuals
    assert visuals[0].type == "status_card"
    assert visuals[0].cards
    labels = {card.label: card.value for card in visuals[0].cards}
    assert labels["Wellhead"] is not None
    assert labels["Ambient"] is not None
    conn.close()


def test_line_chart_does_not_fabricate_missing_temps() -> None:
    visuals = build_visuals(
        [
            (
                "get_production_history",
                {
                    "asset_id": "WELL-0001",
                    "start": "2026-04-16",
                    "end": "2026-10-16",
                    "readings": [
                        {
                            "inspection_id": "INS-1",
                            "inspected_at": "2026-09-01T00:00:00Z",
                            "wellhead_temp_c": 40.0,
                            "ambient_temp_c": None,
                            "delta_c": None,
                        }
                    ],
                },
            )
        ]
    )
    assert visuals[0].type == "line_chart"
    point = visuals[0].points[0]
    assert point.values["wellhead_temp_c"] == 40.0
    assert point.values["ambient_temp_c"] is None
    assert point.values["delta_c"] is None


def test_heatmap_marks_missing_not_zero(mini_db: Path) -> None:
    conn = connect(mini_db)
    init_schema(conn)
    payload = execute_tool(
        "get_inspection_heatmap",
        {"start": "2026-09-01", "end": "2026-10-16", "limit": 5},
        conn,
        {"user_id": "U-201"},
    )
    visuals = build_visuals([("get_inspection_heatmap", payload)])
    assert visuals[0].type == "heatmap"
    assert any(cell.missing for cell in visuals[0].cells) or visuals[0].cells
    assert all(not (cell.missing and cell.value == 0) for cell in visuals[0].cells)
    conn.close()


def test_map_omits_invented_coordinates() -> None:
    visuals = build_visuals(
        [
            (
                "get_wells_map",
                {
                    "markers": [
                        {"asset_id": "WELL-0001", "lat": 31.85, "lon": -102.5, "label": "SW-23 Well 001"},
                        {"asset_id": "WELL-FAKE", "label": "no coords"},
                    ]
                },
            )
        ]
    )
    assert visuals[0].type == "map"
    assert len(visuals[0].markers) == 1
    assert visuals[0].markers[0].record_id == "WELL-0001"


def test_unsupported_hint_does_not_invent_chart() -> None:
    visuals = build_visuals([], VisualSpec(type="heatmap", title="wanted"))
    assert visuals[0].empty_reason
    assert visuals[0].cells == []


def test_serialized_chart_and_heatmap_match_frontend_contract(mini_db: Path) -> None:
    conn = connect(mini_db)
    init_schema(conn)
    downtime = execute_tool(
        "get_top_downtime_wells",
        {"area": "SW-23", "start": "2026-06-16", "end": "2026-10-16", "limit": 3},
        conn,
        {"user_id": "U-201"},
    )
    heatmap = execute_tool(
        "get_inspection_heatmap",
        {"start": "2026-09-01", "end": "2026-10-16", "limit": 3},
        conn,
        {"user_id": "U-201"},
    )
    visuals = [item.model_dump() for item in build_visuals(
        [("get_top_downtime_wells", downtime), ("get_inspection_heatmap", heatmap)]
    )]
    bar = next(item for item in visuals if item["type"] == "bar_chart")
    heat = next(item for item in visuals if item["type"] == "heatmap")
    assert bar["points"] and bar["series"] and bar["series"][0]["key"] in bar["points"][0]["values"]
    assert heat["rows_axis"] and heat["cols_axis"] and heat["cells"]
    conn.close()
