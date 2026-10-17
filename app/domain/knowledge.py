from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any
from uuid import uuid4

from app.db import init_schema
from app.domain.dates import EVALUATION_TODAY, parse_date
from app.domain.queries import current_production_status
from app.domain.corrections import effective_inspection, effective_spill

NOTE_UNVERIFIED = "unverified"
NOTE_RECORDED = "recorded"
NOTE_CONFIRMED = "confirmed"
CLAIM_UNCONFIRMED = "unconfirmed"
CLAIM_CONFIRMED = "confirmed"


def _note_valid_on(valid_from: str | None, valid_to: str | None, as_of: date) -> bool:
    if valid_from and as_of < parse_date(valid_from):
        return False
    if valid_to and as_of > parse_date(valid_to):
        return False
    return True


def add_shared_note(
    conn: sqlite3.Connection,
    *,
    asset_id: str,
    author_user_id: str,
    text: str,
    created_at: str,
    referenced_record_type: str | None = None,
    referenced_record_id: str | None = None,
    verification_status: str = NOTE_UNVERIFIED,
    valid_from: str | None = None,
    valid_to: str | None = None,
    note_kind: str = "operator_note",
    note_id: str | None = None,
) -> dict[str, Any]:
    """Store a shared well note. Runtime notes default to unverified."""
    init_schema(conn)
    if verification_status == NOTE_CONFIRMED:
        raise ValueError("Shared notes cannot be created as confirmed operational facts")
    well = conn.execute("SELECT asset_id FROM wells WHERE asset_id = ?", (asset_id,)).fetchone()
    if well is None:
        raise ValueError(f"Unknown well: {asset_id}")
    assigned = note_id or f"SNT-{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO shared_notes (
            note_id, asset_id, author_user_id, created_at, text, referenced_record_type,
            referenced_record_id, verification_status, valid_from, valid_to, note_kind
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assigned,
            asset_id,
            author_user_id,
            created_at,
            text,
            referenced_record_type,
            referenced_record_id,
            verification_status,
            valid_from,
            valid_to,
            note_kind,
        ),
    )
    return {
        "note_id": assigned,
        "asset_id": asset_id,
        "author_user_id": author_user_id,
        "created_at": created_at,
        "text": text,
        "verification_status": verification_status,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "source": "runtime",
    }


def list_shared_notes(
    conn: sqlite3.Connection,
    asset_id: str | None = None,
    *,
    as_of=EVALUATION_TODAY,
    requesting_user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Notes visible to every user. requesting_user_id is accepted and ignored."""
    init_schema(conn)
    as_of_date = parse_date(as_of)
    notes: list[dict[str, Any]] = []
    params: list[Any] = []
    where = ""
    if asset_id:
        where = "WHERE asset_id = ?"
        params.append(asset_id)
    for row in conn.execute(
        f"""
        SELECT note_id, asset_id, author_user_id, created_at, text, referenced_record_type,
               referenced_record_id, verification_status, valid_from, valid_to, note_kind
        FROM shared_notes {where}
        ORDER BY created_at, note_id
        """,
        params,
    ):
        if _note_valid_on(row["valid_from"], row["valid_to"], as_of_date):
            item = dict(row)
            item["source"] = "runtime"
            notes.append(item)
    context_sql = """
        SELECT note_id, asset_id, author_user_id, created_at, text, note_type,
               inspection_id, valid_from, valid_to
        FROM context_notes
        WHERE note_type IN ('operator_note', 'operator_feedback')
    """
    context_params: list[Any] = []
    if asset_id:
        context_sql += " AND asset_id = ?"
        context_params.append(asset_id)
    context_sql += " ORDER BY created_at, note_id"
    for row in conn.execute(context_sql, context_params):
        if _note_valid_on(row["valid_from"], row["valid_to"], as_of_date):
            notes.append(
                {
                    "note_id": row["note_id"],
                    "asset_id": row["asset_id"],
                    "author_user_id": row["author_user_id"],
                    "created_at": row["created_at"],
                    "text": row["text"],
                    "referenced_record_type": "inspection" if row["inspection_id"] else None,
                    "referenced_record_id": row["inspection_id"],
                    "verification_status": NOTE_RECORDED,
                    "valid_from": row["valid_from"],
                    "valid_to": row["valid_to"],
                    "note_kind": row["note_type"],
                    "source": "dataset",
                }
            )
    notes.sort(key=lambda item: (item["created_at"] or "", item["note_id"]))
    return notes


def record_statement(
    conn: sqlite3.Connection,
    *,
    author_user_id: str,
    asset_id: str,
    text: str,
    asserted_state: str,
    created_at: str,
) -> dict[str, Any]:
    """Record a user statement. Conflicting claims stay unconfirmed and ask for confirmation."""
    init_schema(conn)
    status = current_production_status(conn, asset_id)
    producing = status.get("producing")
    conflicts = []
    if asserted_state == "producing" and producing is False:
        conflicts.append(
            {
                "record_type": "inspection",
                "record_id": status.get("inspection_id"),
                "inspected_at": status.get("inspected_at"),
                "producing": False,
                "delta_c": status.get("delta_c"),
                "notes": status.get("inspection", {}).get("notes") if status.get("inspection") else None,
                "source": "latest_inspection",
            }
        )
    if asserted_state in {"not_producing", "shut_in"} and producing is True:
        conflicts.append(
            {
                "record_type": "inspection",
                "record_id": status.get("inspection_id"),
                "inspected_at": status.get("inspected_at"),
                "producing": True,
                "delta_c": status.get("delta_c"),
                "notes": status.get("inspection", {}).get("notes") if status.get("inspection") else None,
                "source": "latest_inspection",
            }
        )
    supporting_notes = []
    for note in list_shared_notes(conn, asset_id, as_of=created_at[:10] if created_at else EVALUATION_TODAY):
        lowered = note["text"].lower()
        if asserted_state == "producing" and any(token in lowered for token in ("running", "back online", "producing")):
            supporting_notes.append(note)
        if asserted_state in {"not_producing", "shut_in"} and "shut in" in lowered:
            supporting_notes.append(note)

    claim_id = f"CLM-{uuid4().hex[:12]}"
    claim_status = CLAIM_UNCONFIRMED if conflicts else CLAIM_UNCONFIRMED
    conn.execute(
        """
        INSERT INTO claims (
            claim_id, author_user_id, asset_id, created_at, text, asserted_state,
            status, conflict_type, conflicting_record_type, conflicting_record_id, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            claim_id,
            author_user_id,
            asset_id,
            created_at,
            text,
            asserted_state,
            claim_status,
            "inspection_disagreement" if conflicts else None,
            conflicts[0]["record_type"] if conflicts else None,
            conflicts[0]["record_id"] if conflicts else None,
            json.dumps({"conflicts": conflicts, "supporting_notes": [note["note_id"] for note in supporting_notes]}),
        ),
    )
    note = add_shared_note(
        conn,
        asset_id=asset_id,
        author_user_id=author_user_id,
        text=text,
        created_at=created_at,
        verification_status=NOTE_UNVERIFIED,
        note_kind="user_statement",
    )
    if conflicts:
        return {
            "kind": "conflict",
            "needs_confirmation": True,
            "status": CLAIM_UNCONFIRMED,
            "claim_id": claim_id,
            "note_id": note["note_id"],
            "author_user_id": author_user_id,
            "asset_id": asset_id,
            "asserted_state": asserted_state,
            "text": text,
            "conflicts_with": conflicts,
            "supporting_notes": supporting_notes,
            "effective_production": {
                "producing": producing,
                "status": status.get("status"),
                "inspection_id": status.get("inspection_id"),
                "delta_c": status.get("delta_c"),
            },
            "message": (
                "This statement conflicts with the latest inspection. "
                "The claim is recorded as unconfirmed. Please confirm before it is treated as a fact."
            ),
        }
    return {
        "kind": "shared_note",
        "needs_confirmation": False,
        "status": NOTE_UNVERIFIED,
        "claim_id": claim_id,
        "note_id": note["note_id"],
        "author_user_id": author_user_id,
        "asset_id": asset_id,
        "asserted_state": asserted_state,
        "text": text,
        "conflicts_with": [],
        "supporting_notes": supporting_notes,
        "effective_production": {
            "producing": producing,
            "status": status.get("status"),
            "inspection_id": status.get("inspection_id"),
        },
        "message": "Recorded as shared well knowledge. It is not a verified operational fact.",
    }


def list_claims(conn: sqlite3.Connection, asset_id: str | None = None) -> list[dict[str, Any]]:
    init_schema(conn)
    if asset_id:
        rows = conn.execute("SELECT * FROM claims WHERE asset_id = ? ORDER BY created_at", (asset_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM claims ORDER BY created_at").fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json.loads(row["evidence_json"]) if row["evidence_json"] else {}
        results.append(item)
    return results


def spill_feedback_conflict(conn: sqlite3.Connection, inspection_id: str) -> dict[str, Any]:
    """When operator feedback disagrees and there is no formal correction, keep the effective detection."""
    init_schema(conn)
    view = effective_inspection(conn, inspection_id)
    spill = effective_spill(view)
    notes = list(
        conn.execute(
            """
            SELECT note_id, author_user_id, created_at, note_type, text
            FROM context_notes
            WHERE inspection_id = ? OR (inspection_id IS NULL AND text LIKE ?)
            ORDER BY created_at, note_id
            """,
            (inspection_id, f"%{inspection_id}%"),
        )
    )
    feedback = [dict(row) for row in notes if row["note_type"] in {"operator_feedback", "operator_note"}]
    formal = [dict(row) for row in notes if row["note_type"] == "correction"]
    return {
        "kind": "spill_feedback_conflict" if len(feedback) > 1 and not formal else "spill_explanation",
        "inspection_id": inspection_id,
        "effective_spill_detected": spill["effective_spill_detected"],
        "effective_volume_bbl": spill["effective_volume_bbl"],
        "original_spill_detected": spill["original_spill_detected"],
        "formal_corrections": formal,
        "operator_feedback": feedback,
        "keeps_established_detection": not formal,
        "message": (
            "Operator feedback disagrees and neither note is a formal correction. "
            "The established inspection detection stands."
            if len(feedback) > 1 and not formal
            else "Spill explanation for the inspection."
        ),
    }


def set_user_preference(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    key: str,
    value: str,
    created_at: str,
) -> dict[str, Any]:
    """Store a private display or workflow preference for one user only."""
    init_schema(conn)
    assigned = f"PREF-{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO user_preferences (preference_id, user_id, pref_key, pref_value, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, pref_key) DO UPDATE SET
            pref_value=excluded.pref_value,
            created_at=excluded.created_at
        """,
        (assigned, user_id, key, value, created_at),
    )
    return {
        "preference_id": assigned,
        "user_id": user_id,
        "key": key,
        "value": value,
        "scope": "private_to_user",
        "message": "This preference applies only to the requesting user. It is not shared.",
    }


def list_user_preferences(conn: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    init_schema(conn)
    rows = conn.execute(
        """
        SELECT preference_id, user_id, pref_key, pref_value, created_at
        FROM user_preferences
        WHERE user_id = ?
        ORDER BY pref_key
        """,
        (user_id,),
    ).fetchall()
    return [
        {
            "preference_id": row["preference_id"],
            "user_id": row["user_id"],
            "key": row["pref_key"],
            "value": row["pref_value"],
            "created_at": row["created_at"],
            "scope": "private_to_user",
        }
        for row in rows
    ]
