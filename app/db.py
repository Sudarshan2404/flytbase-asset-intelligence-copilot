from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS areas (
    area_id TEXT PRIMARY KEY,
    name TEXT,
    team_id TEXT,
    center_lat REAL,
    center_lon REAL,
    source_path TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS area_assets (
    area_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    PRIMARY KEY (area_id, asset_id)
);

CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT PRIMARY KEY,
    name TEXT,
    lead_user_id TEXT,
    source_path TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    role TEXT,
    team_id TEXT,
    source_path TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wells (
    asset_id TEXT PRIMARY KEY,
    name TEXT,
    asset_category TEXT,
    well_type TEXT,
    area_id TEXT,
    team_id TEXT,
    in_charge_user_id TEXT,
    lat REAL,
    lon REAL,
    installed_on TEXT,
    recorded_status TEXT,
    last_inspection_id TEXT,
    source_path TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inspections (
    inspection_id TEXT PRIMARY KEY,
    asset_id TEXT,
    inspected_at TEXT,
    inspector_user_id TEXT,
    method TEXT,
    ambient_temp_c REAL,
    wellhead_temp_c REAL,
    spill_detected INTEGER,
    spill_area_pct REAL,
    spill_est_volume_bbl REAL,
    notes TEXT,
    source_path TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS maintenance (
    maintenance_id TEXT PRIMARY KEY,
    asset_id TEXT,
    started_at TEXT,
    completed_at TEXT,
    maintenance_type TEXT,
    equipment_kind TEXT,
    issue TEXT,
    action_taken TEXT,
    technician_user_id TEXT,
    contractor TEXT,
    downtime_hours REAL,
    cost_total_usd REAL,
    triggered_by_inspection_id TEXT,
    report_file TEXT,
    source_path TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS regulatory_events (
    event_id TEXT PRIMARY KEY,
    asset_id TEXT,
    date TEXT,
    agency TEXT,
    event_type TEXT,
    protocol_id TEXT,
    related_inspection_id TEXT,
    hours_to_report REAL,
    detail TEXT,
    fine_usd REAL,
    status TEXT,
    source_path TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_notes (
    note_id TEXT PRIMARY KEY,
    asset_id TEXT,
    created_at TEXT,
    author_user_id TEXT,
    note_type TEXT,
    inspection_id TEXT,
    text TEXT,
    valid_from TEXT,
    valid_to TEXT,
    source_path TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_documents (
    source_path TEXT PRIMARY KEY,
    document_id TEXT,
    doc_type TEXT NOT NULL,
    asset_id TEXT,
    well_type TEXT,
    title TEXT,
    related_maintenance_id TEXT,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unparsed_records (
    source_path TEXT NOT NULL,
    line_number INTEGER NOT NULL,
    raw_content TEXT NOT NULL,
    PRIMARY KEY (source_path, line_number)
);

CREATE TABLE IF NOT EXISTS ingest_warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    warning_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    source_path TEXT,
    message TEXT NOT NULL,
    raw_content TEXT
);

CREATE TABLE IF NOT EXISTS structured_corrections (
    correction_id TEXT PRIMARY KEY,
    inspection_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    corrected_value_json TEXT NOT NULL,
    author_user_id TEXT,
    reason TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_wells_area ON wells(area_id);
CREATE INDEX IF NOT EXISTS idx_wells_type ON wells(well_type);
CREATE INDEX IF NOT EXISTS idx_wells_team ON wells(team_id);
CREATE INDEX IF NOT EXISTS idx_inspections_asset_time ON inspections(asset_id, inspected_at);
CREATE INDEX IF NOT EXISTS idx_inspections_inspector ON inspections(inspector_user_id);
CREATE INDEX IF NOT EXISTS idx_maintenance_asset_time ON maintenance(asset_id, started_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_triggered ON maintenance(triggered_by_inspection_id);
CREATE INDEX IF NOT EXISTS idx_regulatory_asset ON regulatory_events(asset_id);
CREATE INDEX IF NOT EXISTS idx_regulatory_inspection ON regulatory_events(related_inspection_id);
CREATE INDEX IF NOT EXISTS idx_notes_asset ON context_notes(asset_id);
CREATE INDEX IF NOT EXISTS idx_notes_inspection ON context_notes(inspection_id);
CREATE INDEX IF NOT EXISTS idx_docs_type ON source_documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_docs_asset ON source_documents(asset_id);
CREATE INDEX IF NOT EXISTS idx_docs_document_id ON source_documents(document_id);
CREATE INDEX IF NOT EXISTS idx_structured_corrections_inspection
    ON structured_corrections(inspection_id);
"""

COUNT_TABLES = (
    "areas",
    "teams",
    "users",
    "wells",
    "inspections",
    "maintenance",
    "regulatory_events",
    "context_notes",
    "source_documents",
    "unparsed_records",
    "ingest_warnings",
    "area_assets",
    "structured_corrections",
)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in COUNT_TABLES:
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return counts
