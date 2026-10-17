from __future__ import annotations

from typing import Any

from app.copilot.schemas import Citation, ConflictItem, PendingConfirmation

_ID_FIELDS = {
    "inspection_id": "inspection",
    "maintenance_id": "maintenance",
    "event_id": "regulatory_event",
    "note_id": "note",
    "claim_id": "claim",
    "correction_id": "correction",
    "document_id": "document",
}


def collect_citations(payload: Any) -> list[Citation]:
    found: dict[tuple[str, str | None, str | None], Citation] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            path = node.get("source_path") or node.get("document_path") or node.get("path")
            excerpt = node.get("excerpt")
            for key, kind in _ID_FIELDS.items():
                record_id = node.get(key)
                if record_id:
                    citation = Citation(kind=kind, record_id=str(record_id), path=path, excerpt=excerpt)
                    found[(kind, citation.record_id, citation.path)] = citation
            if path and not any(node.get(key) for key in _ID_FIELDS):
                found[("document", None, str(path))] = Citation(kind="document", path=str(path), excerpt=excerpt)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return list(found.values())


def collect_conflicts(payload: Any) -> list[ConflictItem]:
    items: list[ConflictItem] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kind = node.get("kind")
            if kind in {"conflict", "spill_feedback_conflict"}:
                record_ids = []
                for key in ("claim_id", "inspection_id", "note_id"):
                    if node.get(key):
                        record_ids.append(str(node[key]))
                for note in node.get("operator_feedback") or []:
                    if isinstance(note, dict) and note.get("note_id"):
                        record_ids.append(str(note["note_id"]))
                items.append(
                    ConflictItem(
                        kind=str(kind),
                        summary=str(node.get("message") or node.get("text") or kind),
                        record_ids=record_ids,
                    )
                )
            if node.get("status") == "unconfirmed" and node.get("claim_id"):
                items.append(
                    ConflictItem(
                        kind="unconfirmed_claim",
                        summary=str(node.get("text") or node.get("message") or "Unconfirmed claim"),
                        record_ids=[str(node["claim_id"])],
                    )
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return items


def collect_pending(payload: Any) -> list[PendingConfirmation]:
    pending: list[PendingConfirmation] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("needs_confirmation"):
                pending.append(
                    PendingConfirmation(
                        claim_id=node.get("claim_id"),
                        asset_id=node.get("asset_id"),
                        text=node.get("text"),
                        message=node.get("message"),
                    )
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return pending
