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
from app.domain.knowledge import list_claims, list_shared_notes, record_statement, spill_feedback_conflict
from app.domain.memory import resolve_discussed_wells
from app.domain.queries import (
    current_production_status,
    downtime_for_well,
    effective_spill_summary,
    latest_inspection,
    maintenance_started_in_window,
    production_history,
    top_wells_by_downtime,
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
    if not clauses:
        raise ToolValidationError("Specify asset_id, doc_type, related_maintenance_id, or document_id")
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
    "get_production_history": {
        "description": "Inspection-by-inspection production history for a well in an inclusive date window.",
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
