from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from app.config import get_data_dir, get_database_path
from app.db import connect, init_schema, table_counts
from app.parsers import (
    as_optional_float,
    as_optional_int_bool,
    as_optional_str,
    extract_document_id,
    extract_maintenance_report_id,
    extract_title,
    generated_id,
    iter_jsonl_lines,
    nested_get,
    parse_json_text,
    parse_jsonl_object,
    records_from_json_value,
    relative_source,
)

class Ingestor:
    def __init__(self, conn: sqlite3.Connection, data_dir: Path) -> None:
        self.conn = conn
        self.data_dir = data_dir
        self.warnings: list[dict[str, Any]] = []

    def warn(
        self,
        warning_type: str,
        message: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        source_path: str | None = None,
        raw_content: str | None = None,
    ) -> None:
        record = {
            "warning_type": warning_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "source_path": source_path,
            "message": message,
            "raw_content": raw_content,
        }
        self.warnings.append(record)
        self.conn.execute(
            """
            INSERT INTO ingest_warnings (
                warning_type, entity_type, entity_id, source_path, message, raw_content
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                warning_type,
                entity_type,
                entity_id,
                source_path,
                message,
                raw_content,
            ),
        )

    def reset_run_state(self) -> None:
        self.conn.execute("DELETE FROM ingest_warnings")
        self.conn.execute("DELETE FROM unparsed_records")
        self.warnings.clear()

    def upsert(self, table: str, key: str, values: dict[str, Any]) -> None:
        columns = list(values.keys())
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f"{column}=excluded.{column}" for column in columns if column != key)
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT({key}) DO UPDATE SET {assignments}"
        )
        self.conn.execute(sql, [values[column] for column in columns])

    def store_unparsed(self, source_path: str, line_number: int, raw_content: str) -> None:
        self.conn.execute(
            """
            INSERT INTO unparsed_records (source_path, line_number, raw_content)
            VALUES (?, ?, ?)
            ON CONFLICT(source_path, line_number) DO UPDATE SET raw_content=excluded.raw_content
            """,
            (source_path, line_number, raw_content),
        )

    def ingest(self) -> dict[str, Any]:
        self.reset_run_state()
        if not self.data_dir.is_dir():
            self.warn(
                "missing_data_dir",
                f"Data directory does not exist: {self.data_dir}",
            )
            return self.summary()

        self.ingest_fleet()
        self.ingest_wells()
        self.check_references()
        return self.summary()

    def summary(self) -> dict[str, Any]:
        counts = table_counts(self.conn)
        return {
            "data_dir": str(self.data_dir),
            "counts": counts,
            "warning_count": len(self.warnings),
            "warnings": self.warnings,
        }

    def ingest_fleet(self) -> None:
        fleet_dir = self.data_dir / "fleet"
        if not fleet_dir.is_dir():
            self.warn("missing_file", "fleet/ directory was not found", source_path="fleet")
            return

        self.ingest_areas_file(fleet_dir / "areas.json")
        self.ingest_users_file(fleet_dir / "users.json")
        self.ingest_markdown_dir(fleet_dir / "well-types", doc_type="well_type_manual")
        self.ingest_markdown_dir(fleet_dir / "sops", doc_type="sop")

    def read_json_container(self, path: Path) -> Any | None:
        source_path = relative_source(self.data_dir, path)
        if not path.is_file():
            self.warn("missing_file", f"Expected file was not found: {source_path}", source_path=source_path)
            return None
        try:
            return parse_json_text(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raw = path.read_text(encoding="utf-8")
            self.store_unparsed(source_path, 0, raw)
            self.warn(
                "malformed_json",
                f"Could not parse JSON ({exc})",
                source_path=source_path,
                raw_content=raw,
            )
            return None

    def ingest_areas_file(self, path: Path) -> None:
        payload = self.read_json_container(path)
        if payload is None:
            return
        source_path = relative_source(self.data_dir, path)
        if isinstance(payload, dict):
            areas = records_from_json_value(payload, ("areas",))
            teams = records_from_json_value(payload.get("teams"), ("teams",)) if "teams" in payload else []
            if not isinstance(payload.get("areas"), list) and not isinstance(payload, list):
                if "area_id" in payload:
                    areas = [payload]
        else:
            areas = records_from_json_value(payload)
            teams = []

        for record in areas:
            self.ingest_area(record, source_path)
        for record in teams:
            self.ingest_team(record, source_path)

    def ingest_area(self, record: dict[str, Any], source_path: str) -> None:
        area_id = as_optional_str(record.get("area_id")) or generated_id("areas", source_path, None)
        if "area_id" not in record or record.get("area_id") in (None, ""):
            self.warn(
                "missing_identifier",
                "Area record is missing area_id; stored with a generated key",
                entity_type="areas",
                entity_id=area_id,
                source_path=source_path,
                raw_content=json.dumps(record),
            )
        self.upsert(
            "areas",
            "area_id",
            {
                "area_id": area_id,
                "name": as_optional_str(record.get("name")),
                "team_id": as_optional_str(record.get("team_id")),
                "center_lat": as_optional_float(nested_get(record, "center", "lat")),
                "center_lon": as_optional_float(nested_get(record, "center", "lon")),
                "source_path": source_path,
                "raw_json": json.dumps(record, ensure_ascii=False),
            },
        )
        self.conn.execute("DELETE FROM area_assets WHERE area_id = ?", (area_id,))
        asset_ids = record.get("asset_ids") or []
        if isinstance(asset_ids, list):
            for asset_id in asset_ids:
                if asset_id in (None, ""):
                    continue
                self.conn.execute(
                    "INSERT OR REPLACE INTO area_assets (area_id, asset_id) VALUES (?, ?)",
                    (area_id, str(asset_id)),
                )

    def ingest_team(self, record: dict[str, Any], source_path: str) -> None:
        team_id = as_optional_str(record.get("team_id")) or generated_id("teams", source_path, None)
        if "team_id" not in record or record.get("team_id") in (None, ""):
            self.warn(
                "missing_identifier",
                "Team record is missing team_id; stored with a generated key",
                entity_type="teams",
                entity_id=team_id,
                source_path=source_path,
                raw_content=json.dumps(record),
            )
        self.upsert(
            "teams",
            "team_id",
            {
                "team_id": team_id,
                "name": as_optional_str(record.get("name")),
                "lead_user_id": as_optional_str(record.get("lead_user_id")),
                "source_path": source_path,
                "raw_json": json.dumps(record, ensure_ascii=False),
            },
        )

    def ingest_users_file(self, path: Path) -> None:
        payload = self.read_json_container(path)
        if payload is None:
            return
        source_path = relative_source(self.data_dir, path)
        for record in records_from_json_value(payload, ("users",)):
            user_id = as_optional_str(record.get("user_id")) or generated_id("users", source_path, None)
            if "user_id" not in record or record.get("user_id") in (None, ""):
                self.warn(
                    "missing_identifier",
                    "User record is missing user_id; stored with a generated key",
                    entity_type="users",
                    entity_id=user_id,
                    source_path=source_path,
                    raw_content=json.dumps(record),
                )
            self.upsert(
                "users",
                "user_id",
                {
                    "user_id": user_id,
                    "name": as_optional_str(record.get("name")),
                    "role": as_optional_str(record.get("role")),
                    "team_id": as_optional_str(record.get("team_id")),
                    "source_path": source_path,
                    "raw_json": json.dumps(record, ensure_ascii=False),
                },
            )

    def ingest_markdown_dir(self, directory: Path, *, doc_type: str) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.md")):
            self.ingest_markdown(path, doc_type=doc_type)

    def ingest_markdown(
        self,
        path: Path,
        *,
        doc_type: str,
        asset_id: str | None = None,
        related_maintenance_id: str | None = None,
    ) -> None:
        source_path = relative_source(self.data_dir, path)
        content = path.read_text(encoding="utf-8")
        document_id = extract_document_id(content)
        title = extract_title(content)
        well_type = None
        if doc_type == "well_type_manual":
            well_type = path.stem
        if doc_type == "maintenance_report" and related_maintenance_id is None:
            related_maintenance_id = extract_maintenance_report_id(content, path.name)
        self.upsert(
            "source_documents",
            "source_path",
            {
                "source_path": source_path,
                "document_id": document_id,
                "doc_type": doc_type,
                "asset_id": asset_id,
                "well_type": well_type,
                "title": title,
                "related_maintenance_id": related_maintenance_id,
                "content": content,
            },
        )

    def ingest_wells(self) -> None:
        assets_dir = self.data_dir / "assets"
        if not assets_dir.is_dir():
            self.warn("missing_file", "assets/ directory was not found", source_path="assets")
            return
        for well_dir in sorted(path for path in assets_dir.iterdir() if path.is_dir()):
            self.ingest_well(well_dir)

    def ingest_well(self, well_dir: Path) -> None:
        folder_id = well_dir.name
        metadata_path = well_dir / "metadata.json"
        asset_id = folder_id
        if metadata_path.is_file():
            asset_id = self.ingest_metadata(metadata_path, folder_id)
        else:
            self.warn(
                "missing_file",
                f"Well folder {folder_id} has no metadata.json",
                entity_type="wells",
                entity_id=folder_id,
                source_path=relative_source(self.data_dir, well_dir),
            )

        self.ingest_jsonl(
            well_dir / "inspections.jsonl",
            entity_type="inspections",
            id_field="inspection_id",
            table="inspections",
            builder=self.inspection_row,
            default_asset_id=asset_id,
        )
        self.ingest_jsonl(
            well_dir / "maintenance.jsonl",
            entity_type="maintenance",
            id_field="maintenance_id",
            table="maintenance",
            builder=self.maintenance_row,
            default_asset_id=asset_id,
        )
        self.ingest_jsonl(
            well_dir / "regulatory-events.jsonl",
            entity_type="regulatory_events",
            id_field="event_id",
            table="regulatory_events",
            builder=self.regulatory_row,
            default_asset_id=asset_id,
        )
        self.ingest_jsonl(
            well_dir / "context.jsonl",
            entity_type="context_notes",
            id_field="note_id",
            table="context_notes",
            builder=self.note_row,
            default_asset_id=asset_id,
        )
        reports_dir = well_dir / "maintenance-reports"
        if reports_dir.is_dir():
            for path in sorted(reports_dir.glob("*.md")):
                related_id = extract_maintenance_report_id(path.read_text(encoding="utf-8"), path.name)
                self.ingest_markdown(
                    path,
                    doc_type="maintenance_report",
                    asset_id=asset_id,
                    related_maintenance_id=related_id,
                )

    def ingest_metadata(self, path: Path, folder_id: str) -> str:
        payload = self.read_json_container(path)
        source_path = relative_source(self.data_dir, path)
        if payload is None:
            return folder_id
        records = records_from_json_value(payload)
        if not records:
            self.warn("malformed_json", "metadata.json did not contain a JSON object", source_path=source_path)
            return folder_id
        record = records[0]
        asset_id = as_optional_str(record.get("asset_id")) or folder_id
        if record.get("asset_id") in (None, ""):
            self.warn(
                "missing_identifier",
                "Well metadata is missing asset_id; using folder name",
                entity_type="wells",
                entity_id=asset_id,
                source_path=source_path,
                raw_content=json.dumps(record),
            )
        self.upsert(
            "wells",
            "asset_id",
            {
                "asset_id": asset_id,
                "name": as_optional_str(record.get("name")),
                "asset_category": as_optional_str(record.get("asset_category")),
                "well_type": as_optional_str(record.get("well_type")),
                "area_id": as_optional_str(record.get("area_id")),
                "team_id": as_optional_str(record.get("team_id")),
                "in_charge_user_id": as_optional_str(record.get("in_charge_user_id")),
                "lat": as_optional_float(nested_get(record, "location", "lat")),
                "lon": as_optional_float(nested_get(record, "location", "lon")),
                "installed_on": as_optional_str(record.get("installed_on")),
                "recorded_status": as_optional_str(record.get("recorded_status")),
                "last_inspection_id": as_optional_str(nested_get(record, "last_inspection", "inspection_id")),
                "source_path": source_path,
                "raw_json": json.dumps(record, ensure_ascii=False),
            },
        )
        return asset_id

    def ingest_jsonl(
        self,
        path: Path,
        *,
        entity_type: str,
        id_field: str,
        table: str,
        builder,
        default_asset_id: str,
    ) -> None:
        if not path.is_file():
            self.warn(
                "missing_file",
                f"Expected file was not found: {relative_source(self.data_dir, path)}",
                entity_type=entity_type,
                entity_id=default_asset_id,
                source_path=relative_source(self.data_dir, path),
            )
            return
        source_path = relative_source(self.data_dir, path)
        # Some datasets may store a JSON array instead of JSONL.
        text = path.read_text(encoding="utf-8").strip()
        if text.startswith("["):
            try:
                payload = parse_json_text(text)
            except json.JSONDecodeError as exc:
                self.store_unparsed(source_path, 0, text)
                self.warn(
                    "malformed_json",
                    f"Could not parse JSON array ({exc})",
                    entity_type=entity_type,
                    source_path=source_path,
                    raw_content=text,
                )
                return
            for index, record in enumerate(records_from_json_value(payload), start=1):
                self.store_entity_record(
                    record,
                    entity_type=entity_type,
                    id_field=id_field,
                    table=table,
                    builder=builder,
                    source_path=source_path,
                    line_number=index,
                    default_asset_id=default_asset_id,
                )
            return

        for line_number, line in iter_jsonl_lines(path):
            try:
                record = parse_jsonl_object(line)
            except (json.JSONDecodeError, ValueError) as exc:
                self.store_unparsed(source_path, line_number, line)
                self.warn(
                    "malformed_json",
                    f"Line {line_number} is not valid JSON ({exc})",
                    entity_type=entity_type,
                    entity_id=default_asset_id,
                    source_path=source_path,
                    raw_content=line,
                )
                continue
            self.store_entity_record(
                record,
                entity_type=entity_type,
                id_field=id_field,
                table=table,
                builder=builder,
                source_path=source_path,
                line_number=line_number,
                default_asset_id=default_asset_id,
            )

    def store_entity_record(
        self,
        record: dict[str, Any],
        *,
        entity_type: str,
        id_field: str,
        table: str,
        builder,
        source_path: str,
        line_number: int,
        default_asset_id: str,
    ) -> None:
        entity_id = as_optional_str(record.get(id_field))
        if not entity_id:
            entity_id = generated_id(entity_type, source_path, line_number)
            self.warn(
                "missing_identifier",
                f"{entity_type} record is missing {id_field}; stored with a generated key",
                entity_type=entity_type,
                entity_id=entity_id,
                source_path=source_path,
                raw_content=json.dumps(record),
            )
        if record.get("asset_id") in (None, ""):
            record = {**record, "asset_id": default_asset_id}
        row = builder(record, entity_id, source_path)
        self.upsert(table, id_field, row)

    def inspection_row(self, record: dict[str, Any], entity_id: str, source_path: str) -> dict[str, Any]:
        return {
            "inspection_id": entity_id,
            "asset_id": as_optional_str(record.get("asset_id")),
            "inspected_at": as_optional_str(record.get("inspected_at")),
            "inspector_user_id": as_optional_str(record.get("inspector_user_id")),
            "method": as_optional_str(record.get("method")),
            "ambient_temp_c": as_optional_float(record.get("ambient_temp_c")),
            "wellhead_temp_c": as_optional_float(record.get("wellhead_temp_c")),
            "spill_detected": as_optional_int_bool(record.get("spill_detected")),
            "spill_area_pct": as_optional_float(record.get("spill_area_pct")),
            "spill_est_volume_bbl": as_optional_float(record.get("spill_est_volume_bbl")),
            "notes": as_optional_str(record.get("notes")),
            "source_path": source_path,
            "raw_json": json.dumps(record, ensure_ascii=False),
        }

    def maintenance_row(self, record: dict[str, Any], entity_id: str, source_path: str) -> dict[str, Any]:
        return {
            "maintenance_id": entity_id,
            "asset_id": as_optional_str(record.get("asset_id")),
            "started_at": as_optional_str(record.get("started_at")),
            "completed_at": as_optional_str(record.get("completed_at")),
            "maintenance_type": as_optional_str(record.get("maintenance_type")),
            "equipment_kind": as_optional_str(record.get("equipment_kind")),
            "issue": as_optional_str(record.get("issue")),
            "action_taken": as_optional_str(record.get("action_taken")),
            "technician_user_id": as_optional_str(record.get("technician_user_id")),
            "contractor": as_optional_str(record.get("contractor")),
            "downtime_hours": as_optional_float(record.get("downtime_hours")),
            "cost_total_usd": as_optional_float(nested_get(record, "cost_usd", "total")),
            "triggered_by_inspection_id": as_optional_str(record.get("triggered_by_inspection_id")),
            "report_file": as_optional_str(record.get("report_file")),
            "source_path": source_path,
            "raw_json": json.dumps(record, ensure_ascii=False),
        }

    def regulatory_row(self, record: dict[str, Any], entity_id: str, source_path: str) -> dict[str, Any]:
        return {
            "event_id": entity_id,
            "asset_id": as_optional_str(record.get("asset_id")),
            "date": as_optional_str(record.get("date")),
            "agency": as_optional_str(record.get("agency")),
            "event_type": as_optional_str(record.get("event_type")),
            "protocol_id": as_optional_str(record.get("protocol_id")),
            "related_inspection_id": as_optional_str(record.get("related_inspection_id")),
            "hours_to_report": as_optional_float(record.get("hours_to_report")),
            "detail": as_optional_str(record.get("detail")),
            "fine_usd": as_optional_float(record.get("fine_usd")),
            "status": as_optional_str(record.get("status")),
            "source_path": source_path,
            "raw_json": json.dumps(record, ensure_ascii=False),
        }

    def note_row(self, record: dict[str, Any], entity_id: str, source_path: str) -> dict[str, Any]:
        return {
            "note_id": entity_id,
            "asset_id": as_optional_str(record.get("asset_id")),
            "created_at": as_optional_str(record.get("created_at")),
            "author_user_id": as_optional_str(record.get("author_user_id")),
            "note_type": as_optional_str(record.get("note_type")),
            "inspection_id": as_optional_str(record.get("inspection_id")),
            "text": as_optional_str(record.get("text")),
            "valid_from": as_optional_str(record.get("valid_from")),
            "valid_to": as_optional_str(record.get("valid_to")),
            "source_path": source_path,
            "raw_json": json.dumps(record, ensure_ascii=False),
        }

    def existing_ids(self, table: str, column: str) -> set[str]:
        return {row[0] for row in self.conn.execute(f"SELECT {column} FROM {table}") if row[0]}

    def check_references(self) -> None:
        inspection_ids = self.existing_ids("inspections", "inspection_id")
        well_ids = self.existing_ids("wells", "asset_id")
        user_ids = self.existing_ids("users", "user_id")
        area_ids = self.existing_ids("areas", "area_id")
        team_ids = self.existing_ids("teams", "team_id")
        document_paths = self.existing_ids("source_documents", "source_path")

        for row in self.conn.execute(
            "SELECT maintenance_id, asset_id, triggered_by_inspection_id, report_file, source_path FROM maintenance"
        ):
            if row["triggered_by_inspection_id"] and row["triggered_by_inspection_id"] not in inspection_ids:
                self.warn(
                    "broken_reference",
                    f"triggered_by_inspection_id {row['triggered_by_inspection_id']} was not found",
                    entity_type="maintenance",
                    entity_id=row["maintenance_id"],
                    source_path=row["source_path"],
                )
            report_file = row["report_file"]
            if report_file:
                well_source = row["source_path"]
                well_rel = str(Path(well_source).parent.as_posix())
                expected = f"{well_rel}/{report_file.replace('\\', '/')}"
                report_on_disk = (self.data_dir / expected).is_file()
                if expected not in document_paths and not report_on_disk:
                    self.warn(
                        "broken_reference",
                        f"report_file {report_file} was not found at {expected}",
                        entity_type="maintenance",
                        entity_id=row["maintenance_id"],
                        source_path=row["source_path"],
                    )

        for row in self.conn.execute(
            "SELECT event_id, related_inspection_id, source_path FROM regulatory_events"
        ):
            if row["related_inspection_id"] and row["related_inspection_id"] not in inspection_ids:
                self.warn(
                    "broken_reference",
                    f"related_inspection_id {row['related_inspection_id']} was not found",
                    entity_type="regulatory_events",
                    entity_id=row["event_id"],
                    source_path=row["source_path"],
                )

        for row in self.conn.execute("SELECT note_id, inspection_id, source_path FROM context_notes"):
            if row["inspection_id"] and row["inspection_id"] not in inspection_ids:
                self.warn(
                    "broken_reference",
                    f"inspection_id {row['inspection_id']} was not found",
                    entity_type="context_notes",
                    entity_id=row["note_id"],
                    source_path=row["source_path"],
                )

        for row in self.conn.execute(
            """
            SELECT asset_id, area_id, team_id, in_charge_user_id, last_inspection_id, source_path
            FROM wells
            """
        ):
            if row["area_id"] and row["area_id"] not in area_ids:
                self.warn(
                    "broken_reference",
                    f"area_id {row['area_id']} was not found",
                    entity_type="wells",
                    entity_id=row["asset_id"],
                    source_path=row["source_path"],
                )
            if row["team_id"] and row["team_id"] not in team_ids:
                self.warn(
                    "broken_reference",
                    f"team_id {row['team_id']} was not found",
                    entity_type="wells",
                    entity_id=row["asset_id"],
                    source_path=row["source_path"],
                )
            if row["in_charge_user_id"] and row["in_charge_user_id"] not in user_ids:
                self.warn(
                    "broken_reference",
                    f"in_charge_user_id {row['in_charge_user_id']} was not found",
                    entity_type="wells",
                    entity_id=row["asset_id"],
                    source_path=row["source_path"],
                )
            if row["last_inspection_id"] and row["last_inspection_id"] not in inspection_ids:
                self.warn(
                    "broken_reference",
                    f"last_inspection_id {row['last_inspection_id']} was not found",
                    entity_type="wells",
                    entity_id=row["asset_id"],
                    source_path=row["source_path"],
                )

        for row in self.conn.execute("SELECT area_id, asset_id FROM area_assets"):
            if row["asset_id"] not in well_ids:
                self.warn(
                    "broken_reference",
                    f"area {row['area_id']} lists unknown asset_id {row['asset_id']}",
                    entity_type="areas",
                    entity_id=row["area_id"],
                    source_path="fleet/areas.json",
                )

        for row in self.conn.execute("SELECT team_id, lead_user_id, source_path FROM teams"):
            if row["lead_user_id"] and row["lead_user_id"] not in user_ids:
                self.warn(
                    "broken_reference",
                    f"lead_user_id {row['lead_user_id']} was not found",
                    entity_type="teams",
                    entity_id=row["team_id"],
                    source_path=row["source_path"],
                )

        for row in self.conn.execute("SELECT user_id, team_id, source_path FROM users"):
            if row["team_id"] and row["team_id"] not in team_ids:
                self.warn(
                    "broken_reference",
                    f"team_id {row['team_id']} was not found",
                    entity_type="users",
                    entity_id=row["user_id"],
                    source_path=row["source_path"],
                )


def ingest(
    data_dir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    resolved_data_dir = get_data_dir(data_dir)
    resolved_db_path = get_database_path(db_path)
    conn = connect(resolved_db_path)
    try:
        init_schema(conn)
        summary = Ingestor(conn, resolved_data_dir).ingest()
        summary["database_path"] = str(resolved_db_path)
        conn.commit()
        return summary
    finally:
        conn.close()


def format_summary(summary: dict[str, Any]) -> str:
    lines = [
        "Ingestion complete",
        f"  data_dir: {summary['data_dir']}",
        f"  database: {summary.get('database_path', '')}",
        "  counts:",
    ]
    for name, count in summary["counts"].items():
        lines.append(f"    {name}: {count}")
    lines.append(f"  warnings: {summary['warning_count']}")
    for warning in summary["warnings"]:
        location = warning.get("entity_id") or warning.get("source_path") or ""
        lines.append(f"    - [{warning['warning_type']}] {warning['message']} ({location})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest fleet and well records into SQLite.")
    parser.add_argument("--data-dir", help="Dataset root (default: sample-data or DATA_DIR)")
    parser.add_argument("--db", help="SQLite path (default: data/copilot.db or DATABASE_PATH)")
    args = parser.parse_args(argv)
    summary = ingest(args.data_dir, args.db)
    print(format_summary(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
