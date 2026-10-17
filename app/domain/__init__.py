"""Deterministic production, downtime, and spill calculations."""

from app.domain.corrections import (
    CORRECTABLE_FIELDS,
    apply_structured_correction,
    correction_history,
    effective_inspection,
    effective_spill,
    record_correction,
)
from app.domain.dates import EVALUATION_TODAY, in_date_window, parse_date
from app.domain.production import PRODUCTION_DELTA_MIN_C, production_from_temps, temperature_delta
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
from app.domain.compliance import (
    apply_rule_override,
    evaluate_spill_compliance,
    evaluate_well_compliance,
    late_containment_spills,
    wells_requiring_rca,
)
from app.domain.findings import BREACH_STATUSES
from app.domain.reports import parse_containment_hours
from app.domain.knowledge import (
    add_shared_note,
    list_claims,
    list_shared_notes,
    record_statement,
    spill_feedback_conflict,
)
from app.domain.memory import (
    add_message,
    conversation_history,
    resolve_discussed_wells,
    start_conversation,
)

__all__ = [
    "CORRECTABLE_FIELDS",
    "EVALUATION_TODAY",
    "PRODUCTION_DELTA_MIN_C",
    "apply_structured_correction",
    "classify_spill_volume",
    "current_production_status",
    "downtime_for_well",
    "effective_inspection",
    "effective_spill",
    "effective_spill_summary",
    "effective_spills",
    "in_date_window",
    "latest_inspection",
    "maintenance_started_in_window",
    "parse_date",
    "production_from_temps",
    "production_history",
    "temperature_delta",
    "top_wells_by_downtime",
    "apply_rule_override",
    "evaluate_spill_compliance",
    "evaluate_well_compliance",
    "late_containment_spills",
    "wells_requiring_rca",
    "BREACH_STATUSES",
    "parse_containment_hours",
    "correction_history",
    "record_correction",
    "add_shared_note",
    "list_claims",
    "list_shared_notes",
    "record_statement",
    "spill_feedback_conflict",
    "add_message",
    "conversation_history",
    "resolve_discussed_wells",
    "start_conversation",
]
