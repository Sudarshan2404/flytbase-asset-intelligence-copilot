from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Explicit compliance vocabulary used by every finding.
STATUS_NONCOMPLIANT = "noncompliant"
STATUS_COMPLIANT = "compliant"
STATUS_OVERDUE = "overdue"
STATUS_PENDING = "pending"
STATUS_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
STATUS_CONDITION_FLAGGED = "condition_flagged"

BREACH_STATUSES = frozenset({STATUS_NONCOMPLIANT, STATUS_OVERDUE})


@dataclass
class Finding:
    rule_id: str
    status: str
    reason: str
    asset_id: str | None = None
    inspection_id: str | None = None
    maintenance_id: str | None = None
    event_id: str | None = None
    document_path: str | None = None
    document_section: str | None = None
    excerpt: str | None = None
    field_values: dict[str, Any] = field(default_factory=dict)
    discovery_at: str | None = None
    deadline_at: str | None = None
    completed_at: str | None = None
    observed_hours: float | None = None
    limit_hours: float | None = None
    source: str = "documented_rule"

    @property
    def is_breach(self) -> bool:
        return self.status in BREACH_STATUSES

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["is_breach"] = self.is_breach
        return payload
