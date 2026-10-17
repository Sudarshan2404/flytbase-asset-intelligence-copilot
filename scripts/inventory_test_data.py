"""Pre-ingest inventory of test-data/dataset. Does not write a database."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "test-data" / "dataset"
OUT = ROOT / "evaluation-output"
OIL_KEYS = {
    "oil_volume_bbl",
    "oil_produced_bbl",
    "produced_oil_bbl",
    "production_volume_bbl",
    "production_bbl",
    "produced_bbl",
    "daily_oil_bbl",
    "oil_bbl",
    "production_total_bbl",
    "barrels_produced",
}


def count_jsonl(path: Path) -> tuple[int, list[dict]]:
    if not path.is_file():
        return 0, []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return len(rows), rows


def main() -> int:
    if not DATA.is_dir():
        print(f"MISSING dataset directory: {DATA}")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    fleet = sorted(str(path.relative_to(DATA)).replace("\\", "/") for path in DATA.joinpath("fleet").rglob("*") if path.is_file())
    assets = DATA / "assets"
    wells = sorted(path.name for path in assets.iterdir() if path.is_dir()) if assets.is_dir() else []
    missing_meta = [name for name in wells if not (assets / name / "metadata.json").is_file()]
    types: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    type_history = team_history = decommissioned = 0
    inspections = maintenance = context = regulatory = reports = 0
    late_insp = late_mnt = cost_revisions = data_corrections = 0
    note_types: Counter[str] = Counter()
    oil_hits = 0
    pressure_fields: Counter[str] = Counter()
    for name in wells:
        well_dir = assets / name
        meta = json.loads((well_dir / "metadata.json").read_text(encoding="utf-8")) if (well_dir / "metadata.json").is_file() else {}
        types[str(meta.get("well_type") or "missing")] += 1
        statuses[str(meta.get("recorded_status") or meta.get("status") or "missing")] += 1
        if meta.get("type_history"):
            type_history += 1
        if meta.get("team_history"):
            team_history += 1
        if meta.get("decommissioned_on") or meta.get("status") == "plugged_and_abandoned":
            decommissioned += 1
        n, rows = count_jsonl(well_dir / "inspections.jsonl")
        inspections += n
        for row in rows:
            if row.get("recorded_at") or str(row.get("inspection_id") or "").endswith(tuple(f"-B{i}" for i in range(10))):
                late_insp += 1
            for key in row:
                if key in OIL_KEYS:
                    oil_hits += 1
                if "pressure" in key.lower():
                    pressure_fields[key] += 1
        n, rows = count_jsonl(well_dir / "maintenance.jsonl")
        maintenance += n
        for row in rows:
            if row.get("recorded_at") or "-L" in str(row.get("maintenance_id") or ""):
                late_mnt += 1
            if row.get("cost_revision"):
                cost_revisions += 1
            for key in row:
                if key in OIL_KEYS:
                    oil_hits += 1
        n, rows = count_jsonl(well_dir / "context.jsonl")
        context += n
        for row in rows:
            note_types[str(row.get("note_type") or "missing")] += 1
            if row.get("note_type") == "data_correction":
                data_corrections += 1
        n, _ = count_jsonl(well_dir / "regulatory-events.jsonl")
        regulatory += n
        reports_dir = well_dir / "maintenance-reports"
        if reports_dir.is_dir():
            reports += sum(1 for path in reports_dir.glob("*.md"))

    questions = ROOT / "test-data" / "final-questions.md"
    if not questions.is_file():
        questions = ROOT / "final-questions.md"
    payload = {
        "dataset_dir": str(DATA),
        "fleet_files": fleet,
        "well_count": len(wells),
        "well_ids_first_last": [wells[0], wells[-1]] if wells else [],
        "missing_metadata": missing_meta,
        "unique_well_ids": len(set(wells)),
        "well_types": dict(types),
        "recorded_statuses": dict(statuses),
        "type_history_wells": type_history,
        "team_history_wells": team_history,
        "decommissioned_wells": decommissioned,
        "inspections": inspections,
        "maintenance": maintenance,
        "context_notes": context,
        "regulatory_events": regulatory,
        "maintenance_reports": reports,
        "late_inspections": late_insp,
        "late_maintenance": late_mnt,
        "cost_revisions": cost_revisions,
        "data_corrections": data_corrections,
        "note_types": dict(note_types),
        "oil_volume_field_hits": oil_hits,
        "pressure_source_fields": dict(pressure_fields),
        "final_questions_path": str(questions) if questions.is_file() else None,
        "final_questions_present": questions.is_file(),
    }
    out_path = OUT / "dataset-inventory.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
