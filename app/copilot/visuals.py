from __future__ import annotations

from typing import Any

from app.copilot.schemas import (
    SUPPORTED_VISUAL_TYPES,
    ChartPoint,
    HeatCell,
    MapMarker,
    MetricCard,
    SeriesSpec,
    TableColumn,
    Visualization,
    VisualSpec,
)


def validate_visualization(payload: Any) -> Visualization | None:
    if not isinstance(payload, dict):
        return None
    try:
        visual = Visualization.model_validate(payload)
    except Exception:
        return None
    if visual.type not in SUPPORTED_VISUAL_TYPES:
        return None
    if visual.type == "map" and not visual.markers:
        visual.empty_reason = visual.empty_reason or "No valid coordinates were returned."
    if visual.type == "heatmap" and not visual.cells:
        visual.empty_reason = visual.empty_reason or "No heatmap cells were returned."
    if visual.type in {"line_chart", "bar_chart"} and not visual.points:
        visual.empty_reason = visual.empty_reason or "No chart points were returned."
    return visual


def _tone_for_producing(producing: Any) -> str:
    if producing is True:
        return "ok"
    if producing is False:
        return "alert"
    return "unknown"


def _finite_coord(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _status_from_well(payload: dict[str, Any]) -> Visualization:
    producing = payload.get("producing")
    cards = [
        MetricCard(
            label="Production",
            value="producing" if producing is True else "not producing" if producing is False else payload.get("status"),
            tone=_tone_for_producing(producing),
            missing=producing is None,
            record_id=payload.get("inspection_id"),
        ),
        MetricCard(
            label="Latest inspection",
            value=payload.get("inspected_at"),
            missing=not payload.get("inspected_at"),
            record_id=payload.get("inspection_id"),
        ),
        MetricCard(
            label="Wellhead",
            value=payload.get("wellhead_temp_c"),
            unit="°C",
            missing=payload.get("wellhead_temp_c") is None,
        ),
        MetricCard(
            label="Ambient",
            value=payload.get("ambient_temp_c"),
            unit="°C",
            missing=payload.get("ambient_temp_c") is None,
        ),
        MetricCard(
            label="Delta",
            value=payload.get("delta_c"),
            unit="°C",
            missing=payload.get("delta_c") is None,
        ),
    ]
    return Visualization(
        type="status_card",
        title=f"{payload.get('asset_id') or 'Well'} status",
        description=payload.get("message"),
        cards=cards,
    )


def _line_from_history(payload: dict[str, Any]) -> Visualization | None:
    readings = payload.get("readings") or []
    if not isinstance(readings, list) or not readings:
        return Visualization(
            type="line_chart",
            title=f"{payload.get('asset_id') or 'Well'} temperature history",
            empty_reason="No inspection measurements were returned for that window.",
        )
    points = []
    for row in readings:
        if not isinstance(row, dict) or not row.get("inspected_at"):
            continue
        values = {
            "wellhead_temp_c": row.get("wellhead_temp_c"),
            "ambient_temp_c": row.get("ambient_temp_c"),
            "delta_c": row.get("delta_c"),
        }
        points.append(
            ChartPoint(
                x=str(row["inspected_at"]),
                values={key: (float(value) if isinstance(value, (int, float)) else None) for key, value in values.items()},
                record_id=row.get("inspection_id"),
            )
        )
    return Visualization(
        type="line_chart",
        title=f"{payload.get('asset_id') or 'Well'} temperature history",
        description=f"{payload.get('start')} to {payload.get('end')}",
        x_key="inspected_at",
        x_label="Inspection date",
        y_label="Temperature",
        units="°C",
        series=[
            SeriesSpec(key="wellhead_temp_c", label="Wellhead", unit="°C"),
            SeriesSpec(key="ambient_temp_c", label="Ambient", unit="°C"),
            SeriesSpec(key="delta_c", label="Delta", unit="°C"),
        ],
        points=points,
        empty_reason=None if points else "No dated measurements were available.",
    )


def _bar_from_downtime(payload: dict[str, Any]) -> list[Visualization]:
    wells = payload.get("wells") or []
    points = []
    rows = []
    for item in wells:
        if not isinstance(item, dict):
            continue
        days = item.get("days")
        points.append(
            ChartPoint(
                x=str(item.get("asset_id")),
                values={"days": float(days) if isinstance(days, (int, float)) else None},
            )
        )
        rows.append(
            {
                "asset_id": item.get("asset_id"),
                "days": item.get("days"),
                "not_producing_count": item.get("not_producing_count"),
            }
        )
    title = f"Downtime {payload.get('area_name') or payload.get('area_id') or ''}".strip()
    return [
        Visualization(
            type="bar_chart",
            title=title or "Downtime comparison",
            description="Seven downtime days are assigned per in-window non-producing inspection.",
            x_key="asset_id",
            x_label="Well",
            y_label="Downtime days",
            units="days",
            series=[SeriesSpec(key="days", label="Downtime days", unit="days")],
            points=points,
            empty_reason=None if points else "No downtime rankings were returned.",
        ),
        Visualization(
            type="table",
            title="Downtime detail",
            columns=[
                TableColumn(key="asset_id", label="Well"),
                TableColumn(key="days", label="Days", unit="days"),
                TableColumn(key="not_producing_count", label="Non-producing inspections"),
            ],
            rows=rows,
        ),
    ]


def _from_single_downtime(payload: dict[str, Any]) -> Visualization:
    return Visualization(
        type="status_card",
        title=f"{payload.get('asset_id')} downtime",
        description=f"{payload.get('start')} to {payload.get('end')}",
        cards=[
            MetricCard(label="Downtime days", value=payload.get("days"), unit="days"),
            MetricCard(label="Non-producing inspections", value=payload.get("not_producing_count")),
            MetricCard(label="Inspections in window", value=payload.get("inspection_count")),
        ],
    )


def _from_spills(payload: dict[str, Any]) -> list[Visualization]:
    classes = payload.get("classifications") or {}
    points = [
        ChartPoint(x=str(name), values={"count": float(count)})
        for name, count in classes.items()
        if isinstance(count, (int, float))
    ]
    spills = payload.get("spills") or []
    rows = []
    for item in spills:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "inspection_id": item.get("inspection_id"),
                "asset_id": item.get("asset_id"),
                "effective_volume_bbl": item.get("effective_volume_bbl"),
                "classification": item.get("classification"),
                "inspected_at": item.get("inspected_at"),
            }
        )
    return [
        Visualization(
            type="bar_chart",
            title="Effective spills by classification",
            description="Volumes and classes after formal corrections.",
            x_key="classification",
            x_label="Classification",
            y_label="Spill count",
            series=[SeriesSpec(key="count", label="Spills")],
            points=points,
            empty_reason=None if points else "No effective spills were returned for that window.",
        ),
        Visualization(
            type="table",
            title="Spill records",
            columns=[
                TableColumn(key="inspection_id", label="Inspection"),
                TableColumn(key="asset_id", label="Well"),
                TableColumn(key="effective_volume_bbl", label="Volume", unit="bbl"),
                TableColumn(key="classification", label="Class"),
                TableColumn(key="inspected_at", label="Inspected"),
            ],
            rows=rows,
        ),
        Visualization(
            type="status_card",
            title="Spill summary",
            cards=[
                MetricCard(label="Spill count", value=payload.get("spill_count")),
                MetricCard(label="Total volume", value=payload.get("total_volume_bbl"), unit="bbl"),
                MetricCard(label="Unknown volume", value=payload.get("unknown_volume_count")),
            ],
        ),
    ]


def _table(title: str, columns: list[TableColumn], rows: list[dict[str, Any]], empty: str) -> Visualization:
    return Visualization(type="table", title=title, columns=columns, rows=rows, empty_reason=None if rows else empty)


def _from_findings(rows: list[Any], title: str) -> list[Visualization]:
    normalized = [item for item in rows if isinstance(item, dict)]
    breaches = sum(1 for item in normalized if item.get("is_breach") or item.get("status") in {"noncompliant", "overdue"})
    return [
        Visualization(
            type="status_card",
            title=title,
            cards=[
                MetricCard(label="Findings", value=len(normalized)),
                MetricCard(label="Breaches", value=breaches, tone="alert" if breaches else "ok"),
            ],
        ),
        _table(
            "Compliance findings",
            [
                TableColumn(key="rule_id", label="Rule"),
                TableColumn(key="status", label="Status"),
                TableColumn(key="asset_id", label="Well"),
                TableColumn(key="inspection_id", label="Inspection"),
                TableColumn(key="reason", label="Reason"),
            ],
            [
                {
                    "rule_id": item.get("rule_id"),
                    "status": item.get("status"),
                    "asset_id": item.get("asset_id"),
                    "inspection_id": item.get("inspection_id"),
                    "reason": item.get("reason"),
                }
                for item in normalized
            ],
            "No compliance findings were returned.",
        ),
    ]


def _from_heatmap(payload: dict[str, Any]) -> Visualization:
    cells = []
    for item in payload.get("cells") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        missing = bool(item.get("missing")) or value is None
        cells.append(
            HeatCell(
                row=str(item.get("row")),
                col=str(item.get("col")),
                value=None if missing else float(value),
                missing=missing,
            )
        )
    return Visualization(
        type="heatmap",
        title=payload.get("title") or "Inspection activity",
        description=payload.get("description"),
        rows_axis=list(payload.get("rows_axis") or []),
        cols_axis=list(payload.get("cols_axis") or []),
        cells=cells,
        cell_meaning=payload.get("cell_meaning") or "Inspection count for that well and month. Missing is not zero.",
        color_scale="teal",
        empty_reason=None if cells else "No inspection activity cells were returned.",
    )


def _from_map(payload: dict[str, Any]) -> Visualization:
    markers = []
    for item in payload.get("markers") or payload.get("wells") or []:
        if not isinstance(item, dict):
            continue
        lat = _finite_coord(item.get("lat"))
        lon = _finite_coord(item.get("lon"))
        if lat is None or lon is None:
            continue
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            continue
        markers.append(
            MapMarker(
                lat=lat,
                lon=lon,
                label=str(item.get("label") or item.get("asset_id") or item.get("name") or "Well"),
                record_id=item.get("asset_id") or item.get("record_id"),
                detail=item.get("detail") or item.get("area_id"),
            )
        )
    return Visualization(
        type="map",
        title=payload.get("title") or "Well locations",
        description="Only wells with stored coordinates are plotted.",
        markers=markers,
        empty_reason=None if markers else "Location data is unavailable for the requested wells.",
    )


def visuals_from_tool(name: str, payload: Any) -> list[Visualization]:
    if payload is None:
        return []
    if name in {"get_well_status", "get_latest_inspection"} and isinstance(payload, dict):
        if payload.get("asset_id"):
            return [_status_from_well(payload)]
    if name == "get_available_measurements" and isinstance(payload, dict):
        rows = [item for item in payload.get("production_volume_fields") or payload.get("fields") or [] if isinstance(item, dict)]
        if payload.get("metric_available"):
            rows = [item for item in payload.get("production_volume_fields") or [] if isinstance(item, dict)]
        else:
            rows = [
                item
                for item in payload.get("fields") or []
                if isinstance(item, dict) and item.get("table") == "inspections" and item.get("kind") in {"temperature", "spill"}
            ]
        return [
            _table(
                "Available measurements",
                [
                    TableColumn(key="table", label="Table"),
                    TableColumn(key="field", label="Field"),
                    TableColumn(key="kind", label="Kind"),
                    TableColumn(key="source", label="Source"),
                ],
                rows,
                payload.get("message") or "No measurement catalog was returned.",
            )
        ]
    if name == "get_oil_production_totals" and isinstance(payload, dict):
        if not payload.get("metric_available"):
            return [
                Visualization(
                    type="status_card",
                    title="Oil production volume",
                    description=payload.get("message"),
                    cards=[
                        MetricCard(
                            label="Oil produced",
                            value=None,
                            unit="bbl",
                            missing=True,
                            tone="unknown",
                        )
                    ],
                    empty_reason=payload.get("message"),
                )
            ]
        rows = [item for item in payload.get("wells") or [] if isinstance(item, dict)]
        return [
            Visualization(
                type="bar_chart",
                title=f"Oil produced {payload.get('start')} to {payload.get('end')}",
                x_key="asset_id",
                x_label="Well",
                y_label="Barrels",
                series=[SeriesSpec(key="total_bbl", label="Oil produced", unit="bbl")],
                points=[
                    ChartPoint(
                        x=str(item.get("asset_id")),
                        values={"total_bbl": float(item["total_bbl"]) if item.get("total_bbl") is not None else None},
                    )
                    for item in rows
                ],
                empty_reason=None if rows else payload.get("message"),
            ),
            _table(
                "Oil produced",
                [
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="total_bbl", label="Oil produced", unit="bbl"),
                ],
                rows,
                payload.get("message") or "No oil-volume totals were returned.",
            ),
        ]
    if name == "get_production_history" and isinstance(payload, dict):
        visual = _line_from_history(payload)
        return [visual] if visual else []
    if name == "get_downtime" and isinstance(payload, dict):
        return [_from_single_downtime(payload)]
    if name == "get_top_downtime_wells" and isinstance(payload, dict):
        return _bar_from_downtime(payload)
    if name == "get_effective_spills" and isinstance(payload, dict):
        return _from_spills(payload)
    if name in {"get_well_compliance", "get_late_containment"} and isinstance(payload, list):
        return _from_findings(payload, "Compliance")
    if name == "get_rca_required" and isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
        return [
            _table(
                "Wells requiring RCA",
                [
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="equipment_kind", label="Equipment"),
                    TableColumn(key="repair_count", label="Repairs"),
                    TableColumn(key="required", label="Required"),
                ],
                rows,
                "No RCA-required wells were returned.",
            )
        ]
    if name == "get_maintenance" and isinstance(payload, list):
        return [
            _table(
                "Maintenance",
                [
                    TableColumn(key="maintenance_id", label="Job"),
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="started_at", label="Started"),
                    TableColumn(key="downtime_hours", label="Downtime hours"),
                ],
                [item for item in payload if isinstance(item, dict)],
                "No maintenance records were returned.",
            )
        ]
    if name == "get_source_documents" and isinstance(payload, list):
        return [
            _table(
                "Source documents",
                [
                    TableColumn(key="document_id", label="Document"),
                    TableColumn(key="title", label="Title"),
                    TableColumn(key="source_path", label="Path"),
                    TableColumn(key="excerpt", label="Excerpt"),
                ],
                [item for item in payload if isinstance(item, dict)],
                "No source documents were returned.",
            )
        ]
    if name in {"get_shared_notes", "get_claims"} and isinstance(payload, list):
        return [
            _table(
                "Shared knowledge" if name == "get_shared_notes" else "Claims",
                [
                    TableColumn(key="note_id" if name == "get_shared_notes" else "claim_id", label="ID"),
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="text", label="Text"),
                    TableColumn(key="status" if name == "get_claims" else "verification_status", label="Status"),
                ],
                [item for item in payload if isinstance(item, dict)],
                "No notes or claims were returned.",
            )
        ]
    if name == "get_area_non_producing" and isinstance(payload, dict):
        rows = payload.get("wells") or []
        return [
            _table(
                f"Not producing in {payload.get('area_name') or 'area'}",
                [
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="delta_c", label="Wellhead above air", unit="°C"),
                    TableColumn(key="recorded_status", label="Recorded status"),
                    TableColumn(key="reason", label="Reason"),
                ],
                [item for item in rows if isinstance(item, dict)],
                "No non-producing wells were returned.",
            )
        ]
    if name == "get_spills_map" and isinstance(payload, dict):
        return [_from_map(payload)]
    if name == "get_spill_heatmap" and isinstance(payload, dict):
        return [_from_heatmap(payload)]
    if name == "get_maintenance_costs" and isinstance(payload, dict):
        rows = [item for item in payload.get("wells") or [] if isinstance(item, dict)]
        return [
            Visualization(
                type="bar_chart",
                title="Repair costs",
                x_key="asset_id",
                x_label="Well",
                y_label="USD",
                series=[SeriesSpec(key="cost_usd", label="Cost", unit="USD")],
                points=[
                    ChartPoint(x=str(item.get("asset_id")), values={"cost_usd": float(item["cost_usd"]) if item.get("cost_usd") is not None else None})
                    for item in rows
                ],
            ),
            _table(
                "Repair costs",
                [
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="cost_usd", label="Cost", unit="USD"),
                ],
                rows,
                payload.get("message") or "No repair costs were returned.",
            ),
        ]
    if name == "get_inspection_heatmap" and isinstance(payload, dict):
        return [_from_heatmap(payload)]
    if name == "get_wells_map" and isinstance(payload, dict):
        return [_from_map(payload)]
    if name == "get_reservoir_pressure" and isinstance(payload, dict):
        return [
            Visualization(
                type="status_card",
                title="Reservoir pressure",
                description=payload.get("message"),
                cards=[
                    MetricCard(
                        label="Reservoir pressure",
                        value=None,
                        missing=True,
                        tone="unknown",
                    )
                ],
                empty_reason=payload.get("message"),
            )
        ]
    if name == "get_gas_response_compliance" and isinstance(payload, dict):
        rows = [item for item in payload.get("rows") or [] if isinstance(item, dict)]
        return [
            _table(
                "Gas reports and response",
                [
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="area", label="Area"),
                    TableColumn(key="reported_at", label="Reported at"),
                    TableColumn(key="response_record_id", label="Response"),
                    TableColumn(key="response_at", label="Response time"),
                    TableColumn(key="hours_to_response", label="Hours to response"),
                    TableColumn(key="deadline_at", label="Deadline"),
                    TableColumn(key="met", label="Met / Missed"),
                ],
                rows,
                "No operator gas reports were found in that window.",
            )
        ]
    if name == "get_flowline_bypasses" and isinstance(payload, dict):
        rows = [item for item in payload.get("all") or payload.get("current") or [] if isinstance(item, dict)]
        return [
            _table(
                "Temporary flowline bypasses",
                [
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="area", label="Area"),
                    TableColumn(key="bypass_in", label="Bypass in"),
                    TableColumn(key="bypass_out", label="Bypass out"),
                    TableColumn(key="approval", label="Approval"),
                    TableColumn(key="approval_valid_until", label="Approval valid until"),
                    TableColumn(key="status", label="Status"),
                ],
                rows,
                "No flowline bypass notes were found.",
            )
        ]
    if name == "get_regulator_letter_spills" and isinstance(payload, dict):
        rows = [item for item in payload.get("rows") or [] if isinstance(item, dict)]
        return [
            _table(
                "Regulator letter spills",
                [
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="area", label="Area"),
                    TableColumn(key="inspection_id", label="Inspection"),
                    TableColumn(key="inspected_at", label="Inspection date"),
                    TableColumn(key="volume_bbl", label="Volume", unit="bbl"),
                    TableColumn(key="clean_up_job", label="Clean-up job"),
                    TableColumn(key="compliant", label="Compliant?"),
                    TableColumn(key="reason", label="Reason"),
                ],
                rows,
                "No reportable spills in the letter's scope were found.",
            )
        ]
    if name == "get_status_disagreements" and isinstance(payload, dict):
        rows = [item for item in payload.get("rows") or [] if isinstance(item, dict)]
        return [
            _table(
                "Inspection vs operator-note status",
                [
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="area", label="Area"),
                    TableColumn(key="inspection_id", label="Latest inspection"),
                    TableColumn(key="inspection_at", label="Inspection date"),
                    TableColumn(key="inspection_shows", label="Inspection shows"),
                    TableColumn(key="note_at", label="Operator note"),
                    TableColumn(key="note_text", label="Note text"),
                    TableColumn(key="status_now", label="Status now"),
                ],
                rows,
                "No inspection/operator-note disagreements were found.",
            )
        ]
    if name == "get_operator_warning_wells" and isinstance(payload, dict):
        rows = []
        for index, item in enumerate(payload.get("rows") or [], start=1):
            if not isinstance(item, dict):
                continue
            rows.append({"rank": index, **item})
        return [
            _table(
                "Operator-warning wells",
                [
                    TableColumn(key="rank", label="Rank"),
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="area", label="Area"),
                    TableColumn(key="note_at", label="Note date"),
                    TableColumn(key="quote", label="Operator-note evidence"),
                    TableColumn(key="other_signs", label="Other warning signs"),
                    TableColumn(key="last_repair", label="Last repair"),
                ],
                rows,
                "No unrepaired operator-warning wells were found.",
            )
        ]
    if name == "get_recalled_pumps" and isinstance(payload, dict):
        rows = [item for item in payload.get("rows") or [] if isinstance(item, dict)]
        cost_key = "cost_display" if payload.get("cost_unit") == "thousands_usd" else "cost_usd"
        return [
            _table(
                "Recalled pumps still in the ground",
                [
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="area", label="Area"),
                    TableColumn(key="lot", label="Lot"),
                    TableColumn(key="serial", label="Serial"),
                    TableColumn(key="maintenance_id", label="Install job"),
                    TableColumn(key=cost_key, label="Install cost"),
                    TableColumn(key="source", label="Source"),
                ],
                rows,
                "No recalled pumps remain after applying replacements and lot filters.",
            )
        ]
    if name == "get_extra_shut_in" and isinstance(payload, dict):
        rows = [item for item in payload.get("rows") or [] if isinstance(item, dict)]
        reasons = sorted({str(item.get("reason") or "unknown") for item in rows})
        by_area: dict[str, dict[str, float]] = {}
        for item in rows:
            area = str(item.get("area") or "Unknown")
            reason = str(item.get("reason") or "unknown")
            by_area.setdefault(area, {})
            by_area[area][reason] = float(item.get("typical_extra_days") or 0)
        points = [
            ChartPoint(x=area, values={reason: values.get(reason) for reason in reasons})
            for area, values in by_area.items()
        ]
        return [
            Visualization(
                type="bar_chart",
                title="Typical extra shut-in days by area",
                description=payload.get("definition"),
                x_key="area",
                x_label="Area",
                y_label="Typical extra days",
                units="days",
                series=[SeriesSpec(key=reason, label=reason, unit="days") for reason in reasons],
                points=points,
                empty_reason=None if points else "No extra shut-in days were found.",
            ),
            _table(
                "Extra shut-in by area and reason",
                [
                    TableColumn(key="area", label="Area"),
                    TableColumn(key="reason", label="Reason"),
                    TableColumn(key="jobs", label="Jobs"),
                    TableColumn(key="share", label="Share"),
                    TableColumn(key="typical_extra_days", label="Typical extra days", unit="days"),
                ],
                rows,
                "No extra shut-in days were found.",
            ),
        ]
    if name == "get_region_temperatures" and isinstance(payload, dict):
        rows = [item for item in payload.get("rows") or [] if isinstance(item, dict)]
        return [
            Visualization(
                type="bar_chart",
                title="Median wellhead-minus-ambient by area",
                description=payload.get("definition"),
                x_key="area",
                x_label="Area",
                y_label="Median delta",
                units="°C",
                series=[SeriesSpec(key="median_delta_c", label="Median delta", unit="°C")],
                points=[
                    ChartPoint(
                        x=str(item.get("area")),
                        values={"median_delta_c": float(item["median_delta_c"]) if item.get("median_delta_c") is not None else None},
                    )
                    for item in rows
                ],
                empty_reason=None if rows else "No temperature readings were found.",
            ),
            _table(
                "Region temperatures",
                [
                    TableColumn(key="area", label="Area"),
                    TableColumn(key="readings", label="Readings"),
                    TableColumn(key="median_delta_c", label="Median delta", unit="°C"),
                    TableColumn(key="mean_delta_c", label="Mean delta", unit="°C"),
                ],
                rows,
                "No temperature readings were found.",
            ),
        ]
    if name == "get_cold_weather_checks" and isinstance(payload, dict):
        rows = [item for item in payload.get("rows") or [] if isinstance(item, dict)]
        return [
            _table(
                "OPS-5 heat-trace misses",
                [
                    TableColumn(key="asset_id", label="Well"),
                    TableColumn(key="area", label="Area"),
                    TableColumn(key="inspection_id", label="Inspection"),
                    TableColumn(key="inspected_at", label="Inspected"),
                    TableColumn(key="ambient_temp_c", label="Ambient", unit="°C"),
                    TableColumn(key="met", label="Met / Missed"),
                ],
                rows,
                "No freeze-protection misses were found.",
            )
        ]
    if name == "get_h2s_reading" and isinstance(payload, dict):
        rows = [item for item in payload.get("notes") or [] if isinstance(item, dict)]
        return [
            _table(
                f"H2S notes {payload.get('asset_id')} {payload.get('day')}",
                [
                    TableColumn(key="note_id", label="Note"),
                    TableColumn(key="created_at", label="Time"),
                    TableColumn(key="ppm", label="H2S", unit="ppm"),
                    TableColumn(key="text", label="Text"),
                ],
                rows,
                "No operator notes were found for that well and day.",
            )
        ]
    if name == "get_fleet_directory" and isinstance(payload, dict):
        rows = [item for item in payload.get("teams") or [] if isinstance(item, dict)]
        return [
            _table(
                "Teams",
                [
                    TableColumn(key="team_id", label="Team"),
                    TableColumn(key="name", label="Name"),
                    TableColumn(key="lead_name", label="Lead"),
                    TableColumn(key="well_count", label="Wells"),
                ],
                rows,
                "No teams were found.",
            )
        ]
    return []


def build_visuals(
    named_results: list[tuple[str, Any]],
    hint: VisualSpec | None = None,
) -> list[Visualization]:
    built: list[Visualization] = []
    for name, payload in named_results:
        for visual in visuals_from_tool(name, payload):
            validated = validate_visualization(visual.model_dump())
            if validated:
                built.append(validated)
    if hint and hint.type and hint.type not in {item.type for item in built}:
        # The model asked for a type we cannot ground in tool data.
        built.append(
            Visualization(
                type="status_card",
                title=hint.title or "Visualization unavailable",
                empty_reason=(
                    f"A {hint.type} was requested, but no tool result contained "
                    "the data needed to render it. No values were fabricated."
                ),
                cards=[],
            )
        )
    if hint and hint.type:
        built.sort(key=lambda item: 0 if item.type == hint.type else 1)
    return built
