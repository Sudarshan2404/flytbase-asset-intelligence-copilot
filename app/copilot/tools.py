from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import Any

from app.domain.compliance import (
    evaluate_well_compliance,
    late_containment_spills,
    wells_requiring_rca,
)
from app.domain.dates import EVALUATION_TODAY, parse_date
from app.domain.findings import Finding
from app.domain.memory import NO_MEMORY_MESSAGE, resolve_discussed_wells
from app.domain.eval_ops import (
    apply_display_preferences,
    cold_weather_misses,
    extra_shut_in,
    fleet_directory,
    flowline_bypasses,
    gas_response_compliance,
    h2s_reading_for_note,
    operator_warning_wells,
    recalled_pumps,
    region_temperatures,
    regulator_letter_spills,
    reservoir_pressure_map,
    status_disagreements,
)
from app.domain.knowledge import (
    add_shared_note,
    list_claims,
    list_shared_notes,
    list_user_preferences,
    record_statement,
    set_user_preference,
    spill_feedback_conflict,
)
from app.domain.measurements import (
    available_measurements,
    infer_quantity_window,
    oil_production_totals,
)
from app.domain.queries import (
    current_production_status,
    downtime_for_well,
    effective_spill_summary,
    inspection_heatmap,
    latest_inspection,
    maintenance_costs,
    maintenance_started_in_window,
    non_producing_in_area,
    production_history,
    spill_heatmap_by_area,
    spills_with_coordinates,
    top_wells_by_downtime,
    wells_with_coordinates,
)

WELL_ID_RE = re.compile(r"^WELL-\d{4,}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_LIMIT = 20
MAX_DOC_CHARS = 400

Handler = Callable[[Any, dict[str, Any], dict[str, Any]], Any]


class ToolValidationError(ValueError):
    pass


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Finding):
        return value.as_dict()
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return str(value)


def _require_well(asset_id: Any) -> str:
    if not isinstance(asset_id, str) or not WELL_ID_RE.match(asset_id):
        raise ToolValidationError("asset_id must look like WELL-0001")
    return asset_id


def _optional_well(asset_id: Any) -> str | None:
    if asset_id in (None, ""):
        return None
    return _require_well(asset_id)


def _require_date(value: Any, name: str) -> str:
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise ToolValidationError(f"{name} must be YYYY-MM-DD")
    parse_date(value)
    return value


def _optional_date(value: Any, name: str) -> str | None:
    if value in (None, ""):
        return None
    return _require_date(value, name)


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolValidationError(f"{name} is required")
    return value.strip()


def _limit(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolValidationError("limit must be an integer") from exc
    if parsed < 1 or parsed > MAX_LIMIT:
        raise ToolValidationError(f"limit must be between 1 and {MAX_LIMIT}")
    return parsed


def _get_well_status(conn, args, _ctx):
    return json_safe(current_production_status(conn, _require_well(args.get("asset_id"))))


def _get_latest_inspection(conn, args, _ctx):
    return json_safe(latest_inspection(conn, _require_well(args.get("asset_id"))))


def _get_production_history(conn, args, _ctx):
    return json_safe(
        production_history(
            conn,
            _require_well(args.get("asset_id")),
            _require_date(args.get("start"), "start"),
            _require_date(args.get("end"), "end"),
        )
    )


def _get_downtime(conn, args, _ctx):
    return json_safe(
        downtime_for_well(
            conn,
            _require_well(args.get("asset_id")),
            _require_date(args.get("start"), "start"),
            _require_date(args.get("end"), "end"),
        )
    )


def _get_top_downtime(conn, args, _ctx):
    start = _require_date(args.get("start"), "start")
    end = _require_date(args.get("end"), "end")
    limit = _limit(args.get("limit"), 3)
    area = args.get("area")
    if area:
        return json_safe(
            top_wells_by_downtime(
                conn,
                _require_text(area, "area"),
                start,
                end,
                limit=limit,
            )
        )
    rows = conn.execute("SELECT asset_id FROM wells ORDER BY asset_id").fetchall()
    ranked = [downtime_for_well(conn, row["asset_id"], start, end) for row in rows]
    ranked.sort(key=lambda item: (-item["days"], item["asset_id"]))
    return json_safe(
        {
            "area_id": None,
            "area_name": None,
            "start": start,
            "end": end,
            "wells": ranked[:limit],
        }
    )


def _get_spills(conn, args, _ctx):
    start = _require_date(args.get("start"), "start")
    end = _require_date(args.get("end"), "end")
    return json_safe(
        effective_spill_summary(
            conn,
            asset_id=_optional_well(args.get("asset_id")),
            area=args.get("area") or None,
            start=start,
            end=end,
        )
    )


def _get_compliance(conn, args, _ctx):
    findings = evaluate_well_compliance(
        conn,
        _require_well(args.get("asset_id")),
        _require_date(args.get("start"), "start"),
        _require_date(args.get("end"), "end"),
        as_of=args.get("as_of") or EVALUATION_TODAY,
    )
    return json_safe([item.as_dict() for item in findings])


def _get_late_containment(conn, args, _ctx):
    findings = late_containment_spills(
        conn,
        _require_text(args.get("area"), "area"),
        _require_date(args.get("start"), "start"),
        _require_date(args.get("end"), "end"),
    )
    return json_safe([item.as_dict() for item in findings])


def _get_rca(conn, args, _ctx):
    return json_safe(
        wells_requiring_rca(
            conn,
            area=_require_text(args.get("area"), "area"),
            equipment_kind=_require_text(args.get("equipment_kind"), "equipment_kind"),
        )
    )


def _get_maintenance(conn, args, _ctx):
    return json_safe(
        maintenance_started_in_window(
            conn,
            asset_id=_optional_well(args.get("asset_id")),
            area=args.get("area") or None,
            start=_require_date(args.get("start"), "start"),
            end=_require_date(args.get("end"), "end"),
        )
    )


def _get_documents(conn, args, _ctx):
    clauses = []
    params: list[Any] = []
    asset_id = _optional_well(args.get("asset_id"))
    if asset_id:
        clauses.append("asset_id = ?")
        params.append(asset_id)
    if args.get("doc_type"):
        clauses.append("doc_type = ?")
        params.append(str(args["doc_type"]))
    if args.get("related_maintenance_id"):
        clauses.append("related_maintenance_id = ?")
        params.append(str(args["related_maintenance_id"]))
    if args.get("document_id"):
        clauses.append("document_id = ?")
        params.append(str(args["document_id"]))
    if args.get("contains"):
        clauses.append("(content LIKE ? OR title LIKE ? OR source_path LIKE ?)")
        needle = f"%{args['contains']}%"
        params.extend([needle, needle, needle])
    if not clauses:
        raise ToolValidationError("Specify asset_id, doc_type, related_maintenance_id, document_id, or contains")
    limit = _limit(args.get("limit"), 3)
    rows = conn.execute(
        f"""
        SELECT source_path, document_id, doc_type, title, related_maintenance_id, asset_id, content
        FROM source_documents
        WHERE {' AND '.join(clauses)}
        ORDER BY source_path
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    documents = []
    for row in rows:
        content = row["content"] or ""
        documents.append(
            {
                "source_path": row["source_path"],
                "document_id": row["document_id"],
                "doc_type": row["doc_type"],
                "title": row["title"],
                "related_maintenance_id": row["related_maintenance_id"],
                "asset_id": row["asset_id"],
                "excerpt": content[:MAX_DOC_CHARS],
            }
        )
    return documents


def _get_notes(conn, args, ctx):
    return json_safe(
        list_shared_notes(
            conn,
            _require_well(args.get("asset_id")),
            requesting_user_id=ctx["user_id"],
        )
    )


def _get_discussed(conn, _args, ctx):
    return json_safe(resolve_discussed_wells(conn, ctx["user_id"]))


def _get_claims(conn, args, _ctx):
    return json_safe(list_claims(conn, _require_well(args.get("asset_id"))))


def _get_spill_feedback(conn, args, _ctx):
    inspection_id = _require_text(args.get("inspection_id"), "inspection_id")
    return json_safe(spill_feedback_conflict(conn, inspection_id))


def _get_heatmap(conn, args, _ctx):
    return json_safe(
        inspection_heatmap(
            conn,
            area=args.get("area") or None,
            start=_require_date(args.get("start"), "start"),
            end=_require_date(args.get("end"), "end"),
            well_limit=_limit(args.get("limit"), 12),
        )
    )


def _get_area_non_producing(conn, args, _ctx):
    return json_safe(non_producing_in_area(conn, _require_text(args.get("area"), "area")))


def _get_spills_map(conn, args, _ctx):
    return json_safe(
        spills_with_coordinates(
            conn,
            area=_require_text(args.get("area"), "area") if args.get("area") else None,
            start=_require_date(args.get("start"), "start"),
            end=_require_date(args.get("end"), "end"),
        )
    )


def _get_spill_heatmap(conn, args, _ctx):
    return json_safe(
        spill_heatmap_by_area(
            conn,
            start=_require_date(args.get("start"), "start"),
            end=_require_date(args.get("end"), "end"),
        )
    )


def _get_maintenance_costs(conn, args, ctx):
    raw_ids = args.get("asset_ids")
    if isinstance(raw_ids, str):
        asset_ids = [part.strip() for part in raw_ids.split(",") if WELL_ID_RE.match(part.strip())]
    elif isinstance(raw_ids, list):
        asset_ids = [str(item) for item in raw_ids if WELL_ID_RE.match(str(item))]
    else:
        asset_ids = []
    if not asset_ids:
        memory = resolve_discussed_wells(conn, ctx["user_id"])
        if not memory["found"]:
            return {"found": False, "message": NO_MEMORY_MESSAGE, "wells": []}
        asset_ids = list(memory["wells"])
    return json_safe(
        {
            "found": True,
            **maintenance_costs(
                conn,
                asset_ids=asset_ids,
                start=_require_date(args.get("start"), "start"),
                end=_require_date(args.get("end"), "end"),
            ),
        }
    )


def _get_wells_map(conn, args, _ctx):
    return json_safe(
        wells_with_coordinates(
            conn,
            area=args.get("area") or None,
            asset_id=_optional_well(args.get("asset_id")),
            limit=_limit(args.get("limit"), 20),
        )
    )


def _quantity_window(args, ctx) -> tuple[str, str]:
    if args.get("start") and args.get("end"):
        return _require_date(args.get("start"), "start"), _require_date(args.get("end"), "end")
    return infer_quantity_window(ctx.get("user_message"))


def _get_available_measurements(conn, _args, _ctx):
    return json_safe(available_measurements(conn))


def _get_oil_production_totals(conn, args, ctx):
    start, end = _quantity_window(args, ctx)
    return json_safe(oil_production_totals(conn, start, end))


def _get_reservoir_pressure(conn, _args, _ctx):
    return json_safe(reservoir_pressure_map(conn))


def _get_gas_response(conn, args, _ctx):
    return json_safe(gas_response_compliance(conn, args.get("start") or "2026-11-01"))


def _get_bypasses(conn, _args, _ctx):
    return json_safe(flowline_bypasses(conn))


def _get_letter_spills(conn, _args, _ctx):
    return json_safe(regulator_letter_spills(conn))


def _get_status_disagreements(conn, _args, _ctx):
    return json_safe(status_disagreements(conn))


def _get_operator_warnings(conn, args, _ctx):
    start = args.get("start") or "2026-12-15"
    end = args.get("end") or EVALUATION_TODAY.isoformat()
    return json_safe(operator_warning_wells(conn, start, end))


def _get_recalled_pumps(conn, args, ctx):
    team_id = args.get("team_id") or None
    message = (ctx.get("user_message") or "").lower()
    if not team_id and "my area" in message:
        user = conn.execute("SELECT team_id FROM users WHERE user_id = ?", (ctx["user_id"],)).fetchone()
        team_id = user["team_id"] if user else None
    payload = recalled_pumps(conn, team_id)
    prefs = list_user_preferences(conn, ctx["user_id"])
    if prefs:
        payload = apply_display_preferences(payload, prefs)
    return json_safe(payload)


def _get_extra_shut_in(conn, args, _ctx):
    return json_safe(
        extra_shut_in(
            conn,
            args.get("start") or "2026-01-01",
            args.get("end") or "2026-12-31",
        )
    )


def _get_region_temps(conn, args, _ctx):
    start = args.get("start") or "2026-10-15"
    end = args.get("end") or EVALUATION_TODAY.isoformat()
    return json_safe(region_temperatures(conn, start, end))


def _get_h2s(conn, args, _ctx):
    return json_safe(
        h2s_reading_for_note(
            conn,
            _require_well(args.get("asset_id")),
            _require_date(args.get("day"), "day"),
        )
    )


def _get_fleet(conn, args, _ctx):
    return json_safe(fleet_directory(conn, args.get("query") or None))


def _get_cold_weather(conn, args, _ctx):
    return json_safe(
        cold_weather_misses(
            conn,
            args.get("start") or "2026-12-01",
            args.get("end") or "2026-12-31",
        )
    )


def _get_prefs(conn, _args, ctx):
    return json_safe({"user_id": ctx["user_id"], "preferences": list_user_preferences(conn, ctx["user_id"])})


def _record_pref(conn, args, ctx):
    return json_safe(
        set_user_preference(
            conn,
            user_id=ctx["user_id"],
            key=_require_text(args.get("key"), "key"),
            value=_require_text(args.get("value"), "value"),
            created_at=args.get("created_at") or f"{EVALUATION_TODAY.isoformat()}T12:00:00Z",
        )
    )


def _record_shared_note(conn, args, ctx):
    return json_safe(
        add_shared_note(
            conn,
            asset_id=_require_well(args.get("asset_id")),
            author_user_id=ctx["user_id"],
            text=_require_text(args.get("text"), "text"),
            created_at=args.get("created_at") or f"{EVALUATION_TODAY.isoformat()}T12:00:00Z",
        )
    )


def _record_claim(conn, args, ctx):
    return json_safe(
        record_statement(
            conn,
            author_user_id=ctx["user_id"],
            asset_id=_require_well(args.get("asset_id")),
            text=_require_text(args.get("text"), "text"),
            asserted_state=_require_text(args.get("asserted_state"), "asserted_state"),
            created_at=_require_text(args.get("created_at"), "created_at")
            if args.get("created_at")
            else f"{EVALUATION_TODAY.isoformat()}T12:00:00Z",
        )
    )


TOOLS: dict[str, dict[str, Any]] = {
    "get_well_status": {
        "description": "Current production status and latest inspection for one well.",
        "parameters": {
            "type": "object",
            "properties": {"asset_id": {"type": "string", "description": "Well ID such as WELL-0001"}},
            "required": ["asset_id"],
        },
        "handler": _get_well_status,
    },
    "get_latest_inspection": {
        "description": "Latest inspection record for one well, including effective corrected fields.",
        "parameters": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
        },
        "handler": _get_latest_inspection,
    },
    "get_available_measurements": {
        "description": (
            "Inspect stored schema and source-record keys. Use this before concluding a "
            "metric is missing. Lists temperature, spill-volume, and any production-volume fields."
        ),
        "parameters": {"type": "object", "properties": {}},
        "handler": _get_available_measurements,
    },
    "get_oil_production_totals": {
        "description": (
            "Rank wells by produced-oil volume in an inclusive date window. Inspects schema "
            "and source records first. Returns metric_available=false when no production-volume "
            "field exists. Never uses spill_est_volume_bbl, temperature, or producing status as oil volume."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Inclusive start YYYY-MM-DD. Inferred from the question if omitted."},
                "end": {"type": "string", "description": "Inclusive end YYYY-MM-DD. Inferred from the question if omitted."},
            },
        },
        "handler": _get_oil_production_totals,
    },
    "get_production_history": {
        "description": "Inspection-by-inspection temperature and producing status for a well. Not oil volume.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["asset_id", "start", "end"],
        },
        "handler": _get_production_history,
    },
    "get_downtime": {
        "description": "Downtime days for one well in an inclusive date window.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["asset_id", "start", "end"],
        },
        "handler": _get_downtime,
    },
    "get_top_downtime_wells": {
        "description": "Rank wells by downtime for an inclusive date window. Area is optional.",
        "parameters": {
            "type": "object",
            "properties": {
                "area": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["start", "end"],
        },
        "handler": _get_top_downtime,
    },
    "get_effective_spills": {
        "description": "Correction-aware spill counts, volumes, and classifications.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "area": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["start", "end"],
        },
        "handler": _get_spills,
    },
    "get_well_compliance": {
        "description": "Deterministic compliance findings for one well and date window.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["asset_id", "start", "end"],
        },
        "handler": _get_compliance,
    },
    "get_late_containment": {
        "description": "Spills in an area that were not contained within the SR-02 limit.",
        "parameters": {
            "type": "object",
            "properties": {
                "area": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["area", "start", "end"],
        },
        "handler": _get_late_containment,
    },
    "get_rca_required": {
        "description": "Wells that meet the RCA repair threshold for an equipment kind.",
        "parameters": {
            "type": "object",
            "properties": {
                "area": {"type": "string"},
                "equipment_kind": {"type": "string"},
            },
            "required": ["area", "equipment_kind"],
        },
        "handler": _get_rca,
    },
    "get_maintenance": {
        "description": "Maintenance jobs assigned to a window by started_at.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "area": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["start", "end"],
        },
        "handler": _get_maintenance,
    },
    "get_source_documents": {
        "description": "Short excerpts from manuals, SOPs, or maintenance reports. Always filter; never request the whole corpus.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "doc_type": {"type": "string"},
                "related_maintenance_id": {"type": "string"},
                "document_id": {"type": "string"},
                "contains": {"type": "string", "description": "Substring to search in title, path, or content"},
                "limit": {"type": "integer"},
            },
        },
        "handler": _get_documents,
    },
    "get_shared_notes": {
        "description": "Shared well notes visible to every user.",
        "parameters": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
        },
        "handler": _get_notes,
    },
    "get_discussed_wells": {
        "description": "Wells previously discussed by the requesting user only.",
        "parameters": {"type": "object", "properties": {}},
        "handler": _get_discussed,
    },
    "get_claims": {
        "description": "Recorded user claims for a well, including unconfirmed conflicts.",
        "parameters": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}},
            "required": ["asset_id"],
        },
        "handler": _get_claims,
    },
    "get_spill_feedback": {
        "description": "Operator feedback about a spill inspection when there is no formal correction.",
        "parameters": {
            "type": "object",
            "properties": {"inspection_id": {"type": "string"}},
            "required": ["inspection_id"],
        },
        "handler": _get_spill_feedback,
    },
    "get_area_non_producing": {
        "description": "Wells in an area whose latest inspection shows they are not producing oil, with recorded reasons when present.",
        "parameters": {
            "type": "object",
            "properties": {"area": {"type": "string"}},
            "required": ["area"],
        },
        "handler": _get_area_non_producing,
    },
    "get_spills_map": {
        "description": "Effective spills in a window, plotted only when the well already has stored coordinates.",
        "parameters": {
            "type": "object",
            "properties": {
                "area": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["start", "end"],
        },
        "handler": _get_spills_map,
    },
    "get_spill_heatmap": {
        "description": "Effective spill counts by geographic area and month.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
            "required": ["start", "end"],
        },
        "handler": _get_spill_heatmap,
    },
    "get_maintenance_costs": {
        "description": "Repair cost totals by started_at. Uses the requesting user's discussed wells if asset_ids is omitted.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_ids": {"type": "string", "description": "Comma-separated well IDs"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["start", "end"],
        },
        "handler": _get_maintenance_costs,
    },
    "get_inspection_heatmap": {
        "description": "Inspection counts by well and month. Missing months are marked missing, not zero.",
        "parameters": {
            "type": "object",
            "properties": {
                "area": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["start", "end"],
        },
        "handler": _get_heatmap,
    },
    "get_wells_map": {
        "description": "Wells that already have stored latitude and longitude. Never invent coordinates.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "area": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
        "handler": _get_wells_map,
    },
    "get_reservoir_pressure": {
        "description": "Inspect whether reservoir pressure measurements exist and map them if they do. Do not substitute temperature.",
        "parameters": {"type": "object", "properties": {}},
        "handler": _get_reservoir_pressure,
    },
    "get_gas_response_compliance": {
        "description": "OPS-1: operator gas-odor notes since a date and whether a manual inspection or job met the deadline.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}},
        },
        "handler": _get_gas_response,
    },
    "get_flowline_bypasses": {
        "description": "OPS-2: temporary flowline bypasses from operator notes vs MOC approvals and expiry.",
        "parameters": {"type": "object", "properties": {}},
        "handler": _get_bypasses,
    },
    "get_regulator_letter_spills": {
        "description": "Reportable NE-07/NE-08 spills since 2026-06-01 against the 20 Nov 2026 regulator letter.",
        "parameters": {"type": "object", "properties": {}},
        "handler": _get_letter_spills,
    },
    "get_status_disagreements": {
        "description": "OPS-3: wells where latest inspection and latest shut-in/restart operator note disagree.",
        "parameters": {"type": "object", "properties": {}},
        "handler": _get_status_disagreements,
    },
    "get_operator_warning_wells": {
        "description": "Operator notes about abnormal well behaviour in a window, excluding wells already repaired after the note.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
        },
        "handler": _get_operator_warnings,
    },
    "get_recalled_pumps": {
        "description": "Downhole pumps still in the ground from recalled Highline lots, using the latest Parts installed line.",
        "parameters": {
            "type": "object",
            "properties": {"team_id": {"type": "string"}},
        },
        "handler": _get_recalled_pumps,
    },
    "get_extra_shut_in": {
        "description": "Jobs started in a window whose calendar span exceeds downtime_hours/24, grouped by area and wait reason.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
        },
        "handler": _get_extra_shut_in,
    },
    "get_region_temperatures": {
        "description": "Median wellhead-minus-ambient temperature by area. Not oil volume or reservoir pressure.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
        },
        "handler": _get_region_temps,
    },
    "get_h2s_reading": {
        "description": "Operator notes on a well for one calendar day, including any H2S ppm figure in the text.",
        "parameters": {
            "type": "object",
            "properties": {"asset_id": {"type": "string"}, "day": {"type": "string"}},
            "required": ["asset_id", "day"],
        },
        "handler": _get_h2s,
    },
    "get_fleet_directory": {
        "description": "Users, teams, team leads, and current well counts. Use for 'who leads' questions.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        "handler": _get_fleet,
    },
    "get_cold_weather_checks": {
        "description": "OPS-5 freeze-protection misses: producing wells at or below -5 C ambient without a Heat trace check in 7 days.",
        "parameters": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
        },
        "handler": _get_cold_weather,
    },
    "get_user_preferences": {
        "description": "Private display preferences for the requesting user only.",
        "parameters": {"type": "object", "properties": {}},
        "handler": _get_prefs,
    },
    "record_user_preference": {
        "description": "Store a private preference for the requesting user, such as sort order or cost units. Not shared.",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
            "required": ["key", "value"],
        },
        "handler": _record_pref,
    },
    "record_shared_note": {
        "description": "Record a shared well note visible to every user, for example a pump replacement not yet in maintenance.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "text": {"type": "string"},
                "created_at": {"type": "string"},
            },
            "required": ["asset_id", "text"],
        },
        "handler": _record_shared_note,
    },
    "record_user_claim": {
        "description": "Record a user statement as shared unverified knowledge or an unconfirmed conflict.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "text": {"type": "string"},
                "asserted_state": {"type": "string"},
                "created_at": {"type": "string"},
            },
            "required": ["asset_id", "text", "asserted_state"],
        },
        "handler": _record_claim,
    },
}


def tool_declarations() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
        }
        for name, spec in TOOLS.items()
    ]


def execute_tool(name: str, args: dict[str, Any], conn, ctx: dict[str, Any]) -> Any:
    if name not in TOOLS:
        raise ToolValidationError(f"Unknown tool: {name}")
    if args.get("user_id") and args["user_id"] != ctx["user_id"]:
        raise ToolValidationError("Tools cannot select or override the requesting user_id")
    cleaned = {key: value for key, value in args.items() if key != "user_id"}
    return TOOLS[name]["handler"](conn, cleaned, ctx)
