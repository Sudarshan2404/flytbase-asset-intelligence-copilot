from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.config import PROJECT_ROOT
from app.ingest import ingest
from tests.conftest import FIXTURE_DIR


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_ingests_every_discovered_format(mini_db: Path) -> None:
    conn = _connect(mini_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM wells").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM inspections").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM maintenance").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM regulatory_events").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM context_notes").fetchone()[0] == 3
        docs = conn.execute("SELECT doc_type, COUNT(*) AS n FROM source_documents GROUP BY doc_type").fetchall()
        counts = {row["doc_type"]: row["n"] for row in docs}
        assert counts["well_type_manual"] == 1
        assert counts["sop"] == 1
        assert counts["maintenance_report"] == 1
        report = conn.execute(
            "SELECT related_maintenance_id, content FROM source_documents WHERE doc_type = 'maintenance_report'"
        ).fetchone()
        assert report["related_maintenance_id"] == "MNT-0001-002"
        assert "contained about 5 hours" in report["content"]
    finally:
        conn.close()


def test_preserves_source_paths_and_raw_json(mini_db: Path) -> None:
    conn = _connect(mini_db)
    try:
        inspection = conn.execute(
            "SELECT source_path, raw_json FROM inspections WHERE inspection_id = 'INS-0001-0002'"
        ).fetchone()
        assert inspection["source_path"] == "assets/WELL-0001/inspections.jsonl"
        raw = json.loads(inspection["raw_json"])
        assert raw["spill_est_volume_bbl"] == 1.6
        assert raw["evidence_images"][0].endswith("spill.jpg")

        well = conn.execute("SELECT raw_json FROM wells WHERE asset_id = 'WELL-0001'").fetchone()
        assert json.loads(well["raw_json"])["equipment"][0]["equipment_id"] == "WELL-0001-WH"

        user = conn.execute("SELECT source_path FROM users WHERE user_id = 'U-101'").fetchone()
        assert user["source_path"] == "fleet/users.json"
    finally:
        conn.close()


def test_handles_missing_and_null_fields(mini_db: Path) -> None:
    conn = _connect(mini_db)
    try:
        well = conn.execute("SELECT * FROM wells WHERE asset_id = 'WELL-0002'").fetchone()
        assert well["in_charge_user_id"] is None
        assert well["lat"] is None
        assert well["lon"] is None
        assert well["installed_on"] is None
        assert well["last_inspection_id"] is None

        inspection = conn.execute(
            "SELECT spill_area_pct, spill_est_volume_bbl, spill_detected FROM inspections WHERE inspection_id = 'INS-0001-0001'"
        ).fetchone()
        assert inspection["spill_area_pct"] is None
        assert inspection["spill_est_volume_bbl"] is None
        assert inspection["spill_detected"] == 0

        note = conn.execute("SELECT inspection_id, valid_to FROM context_notes WHERE note_id = 'CTX-0001-002'").fetchone()
        assert note["inspection_id"] is None
        assert note["valid_to"] == "2026-10-20"

        job = conn.execute(
            "SELECT triggered_by_inspection_id, report_file, contractor FROM maintenance WHERE maintenance_id = 'MNT-0001-001'"
        ).fetchone()
        assert job["triggered_by_inspection_id"] is None
        assert job["report_file"] is None
        assert job["contractor"] is None
    finally:
        conn.close()


def test_keeps_malformed_lines_and_does_not_discard_them(mini_db: Path) -> None:
    conn = _connect(mini_db)
    try:
        unparsed = conn.execute("SELECT source_path, line_number, raw_content FROM unparsed_records").fetchall()
        assert len(unparsed) == 1
        assert unparsed[0]["source_path"] == "assets/WELL-0001/inspections.jsonl"
        assert "this is not json" in unparsed[0]["raw_content"]

        generated = conn.execute(
            "SELECT inspection_id, notes FROM inspections WHERE inspection_id LIKE 'generated:%'"
        ).fetchone()
        assert generated is not None
        assert "Missing inspection_id" in generated["notes"]
    finally:
        conn.close()


def test_reports_broken_references(mini_db: Path) -> None:
    conn = _connect(mini_db)
    try:
        warnings = conn.execute(
            "SELECT warning_type, entity_type, entity_id, message FROM ingest_warnings"
        ).fetchall()
        messages = [row["message"] for row in warnings]
        assert any("INS-DOES-NOT-EXIST" in message for message in messages)
        assert any("missing-report.md" in message for message in messages)
        assert any("INS-MISSING-REG" in message for message in messages)
        assert any("INS-GONE" in message for message in messages)
        assert any("WELL-MISSING" in message for message in messages)
        assert all(row["warning_type"] in {"broken_reference", "missing_identifier", "malformed_json"} for row in warnings)
        assert conn.execute(
            "SELECT COUNT(*) FROM maintenance WHERE maintenance_id = 'MNT-0001-003'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_ingest_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "repeat.db"
    first = ingest(FIXTURE_DIR, db_path)
    second = ingest(FIXTURE_DIR, db_path)
    assert first["counts"] == second["counts"]
    conn = _connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM inspections").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM ingest_warnings").fetchone()[0] == first["warning_count"]
    finally:
        conn.close()


def test_alternate_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "alt-data"
    (data_dir / "fleet").mkdir(parents=True)
    (data_dir / "assets" / "WELL-0099").mkdir(parents=True)
    (data_dir / "fleet" / "areas.json").write_text(
        json.dumps(
            {
                "areas": [
                    {
                        "area_id": "NE-8",
                        "name": "Northeast Block 8",
                        "team_id": "TEAM-NE",
                        "center": {"lat": 1.0, "lon": 2.0},
                        "asset_ids": ["WELL-0099"],
                    }
                ],
                "teams": [{"team_id": "TEAM-NE", "name": "Northeast Production", "lead_user_id": "U-201"}],
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "fleet" / "users.json").write_text(
        json.dumps([{"user_id": "U-201", "name": "Michael Turner", "role": "production_supervisor", "team_id": "TEAM-NE"}]),
        encoding="utf-8",
    )
    (data_dir / "assets" / "WELL-0099" / "metadata.json").write_text(
        json.dumps(
            {
                "asset_id": "WELL-0099",
                "name": "Alt Well",
                "well_type": "T350",
                "area_id": "NE-8",
                "team_id": "TEAM-NE",
                "in_charge_user_id": "U-201",
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "assets" / "WELL-0099" / "inspections.jsonl").write_text(
        json.dumps(
            {
                "inspection_id": "INS-0099-0001",
                "asset_id": "WELL-0099",
                "inspected_at": "2026-10-15T00:00:00Z",
                "inspector_user_id": "U-201",
                "method": "manual",
                "ambient_temp_c": 10,
                "wellhead_temp_c": 20,
                "spill_detected": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    for name in ("maintenance.jsonl", "regulatory-events.jsonl", "context.jsonl"):
        (data_dir / "assets" / "WELL-0099" / name).write_text("", encoding="utf-8")

    summary = ingest(data_dir, tmp_path / "alt.db")
    assert summary["counts"]["wells"] == 1
    assert summary["counts"]["users"] == 1
    assert summary["counts"]["inspections"] == 1
    assert summary["data_dir"] == str(data_dir.resolve())
    conn = _connect(tmp_path / "alt.db")
    try:
        well = conn.execute("SELECT name, well_type FROM wells WHERE asset_id = 'WELL-0099'").fetchone()
        assert well["name"] == "Alt Well"
        assert well["well_type"] == "T350"
    finally:
        conn.close()


def test_well_0001_has_203_inspections_in_baseline_and_database(baseline_db: Path) -> None:
    source = PROJECT_ROOT / "sample-data" / "assets" / "WELL-0001" / "inspections.jsonl"
    source_count = sum(1 for line in source.read_text(encoding="utf-8").splitlines() if line.strip())
    assert source_count == 203

    conn = _connect(baseline_db)
    try:
        stored = conn.execute(
            "SELECT COUNT(*) FROM inspections WHERE asset_id = 'WELL-0001'"
        ).fetchone()[0]
        assert stored == 203
        assert conn.execute("SELECT COUNT(*) FROM wells").fetchone()[0] == 200
    finally:
        conn.close()
