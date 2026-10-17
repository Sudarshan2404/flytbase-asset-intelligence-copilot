from __future__ import annotations

import re
import sqlite3
from typing import Any

# Observed maintenance-report phrasings in the sample dataset.
_CONTAINED_RE = re.compile(
    r"(?:\*\*)?contained(?:\s+\*\*)?\s+(?:after\s+)?about\s+(\d+(?:\.\d+)?)\s+hours",
    re.IGNORECASE,
)


def parse_containment_hours(text: str | None) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    match = _CONTAINED_RE.search(text)
    if not match:
        return None, None
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + 40)
    excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
    return float(match.group(1)), excerpt


def report_for_inspection(conn: sqlite3.Connection, inspection_id: str) -> dict[str, Any] | None:
    maintenance = conn.execute(
        """
        SELECT maintenance_id, report_file, source_path, triggered_by_inspection_id
        FROM maintenance
        WHERE triggered_by_inspection_id = ?
        ORDER BY started_at, maintenance_id
        """,
        (inspection_id,),
    ).fetchone()
    if maintenance is None:
        return None
    document = conn.execute(
        """
        SELECT source_path, content, related_maintenance_id, title
        FROM source_documents
        WHERE related_maintenance_id = ? OR source_path LIKE ?
        ORDER BY source_path
        """,
        (maintenance["maintenance_id"], f"%/{maintenance['report_file']}" if maintenance["report_file"] else ""),
    ).fetchone()
    if document is None and maintenance["report_file"]:
        document = conn.execute(
            """
            SELECT source_path, content, related_maintenance_id, title
            FROM source_documents
            WHERE source_path LIKE ?
            """,
            (f"%{maintenance['report_file']}",),
        ).fetchone()
    hours, excerpt = parse_containment_hours(document["content"] if document else None)
    return {
        "maintenance_id": maintenance["maintenance_id"],
        "report_file": maintenance["report_file"],
        "document_path": document["source_path"] if document else None,
        "content": document["content"] if document else None,
        "containment_hours": hours,
        "excerpt": excerpt,
    }
