from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass, field
from math import isclose
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT, get_database_path
from app.copilot.visuals import build_visuals
from app.db import connect, init_schema
from app.domain.compliance import (
    evaluate_spill_compliance,
    evaluate_well_compliance,
    late_containment_spills,
    wells_requiring_rca,
)
from app.domain.corrections import apply_structured_correction, correction_history
from app.domain.dates import EVALUATION_TODAY, lookback_start
from app.domain.knowledge import add_shared_note, list_shared_notes, record_statement, spill_feedback_conflict
from app.domain.memory import resolve_discussed_wells
from app.domain.queries import (
    current_production_status,
    downtime_for_well,
    effective_spill_summary,
    latest_inspection,
    maintenance_costs,
    non_producing_in_area,
    production_history,
    spill_heatmap_by_area,
    spills_with_coordinates,
    top_wells_by_downtime,
)
from app.domain.compliance import apply_rule_override


@dataclass
class CheckResult:
    question_id: str
    expected: str
    actual: str
    passed: bool
    evidence: str
    visualization: str
    failure: str = ""
    fix_applied: str = ""
    regression_test: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _scratch(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Copy the open database so correction scenarios cannot leak into the source."""
    clone = sqlite3.connect(":memory:")
    clone.row_factory = sqlite3.Row
    conn.backup(clone)
    return clone


def _visuals_for(name: str, payload: Any) -> str:
    visuals = build_visuals([(name, payload)])
    if not visuals:
        return "none"
    return ", ".join(f"{item.type}:{item.title or ''}" for item in visuals)


def check_q1(conn) -> CheckResult:
    status = current_production_status(conn, "WELL-0001")
    latest = latest_inspection(conn, "WELL-0001")
    expected = "Not producing; INS-0001-0203; 15 Oct 2026; drone; Ryan Brooks; delta 3.2 C"
    actual = (
        f"producing={status.get('producing')} {status.get('inspection_id')} "
        f"{str(status.get('inspected_at') or '')[:10]} {status.get('method')} "
        f"{status.get('inspector_name')} delta={status.get('delta_c')}"
    )
    ok = (
        status.get("producing") is False
        and status.get("inspection_id") == "INS-0001-0203"
        and str(status.get("inspected_at") or "").startswith("2026-10-15")
        and status.get("method") == "drone"
        and status.get("inspector_name") == "Ryan Brooks"
        and isclose(float(status.get("delta_c") or 0), 3.2, abs_tol=0.05)
        and status.get("recorded_status") == "active"
        and status.get("recorded_status_authoritative") is False
    )
    note = conn.execute("SELECT text FROM context_notes WHERE note_id = 'CTX-0001-003'").fetchone()
    return CheckResult(
        "Q1",
        expected,
        actual,
        ok and note is not None,
        f"{status.get('inspection_id')}; CTX-0001-003; T200 delta rule",
        _visuals_for("get_well_status", status),
        "" if ok else "Latest WELL-0001 status did not match",
        "Inspector name lookup from users table",
        "tests/test_eval_harness.py::test_q1",
        {"notes": latest.get("notes") if latest else None},
    )


def check_q2(conn) -> CheckResult:
    payload = non_producing_in_area(conn, "Northeast Block 8")
    ids = [item["asset_id"] for item in payload["wells"]]
    expected = "WELL-0164, WELL-0170, WELL-0172, WELL-0175"
    actual = ", ".join(ids)
    ok = ids == ["WELL-0164", "WELL-0170", "WELL-0172", "WELL-0175"]
    reasons = {item["asset_id"]: item["has_recorded_reason"] for item in payload["wells"]}
    by_id = {item["asset_id"]: item for item in payload["wells"]}
    if ok:
        ok = reasons.get("WELL-0164") is False and reasons.get("WELL-0175") is False
        ok = ok and reasons.get("WELL-0170") is True and reasons.get("WELL-0172") is True
        ok = ok and isclose(float(by_id["WELL-0170"]["delta_c"] or 0), 2.9, abs_tol=0.05)
        ok = ok and isclose(float(by_id["WELL-0172"]["delta_c"] or 0), 5.7, abs_tol=0.05)
        jobs_170 = [job["maintenance_id"] for job in by_id["WELL-0170"]["open_maintenance"]]
        jobs_172 = [job["maintenance_id"] for job in by_id["WELL-0172"]["open_maintenance"]]
        ok = ok and "MNT-0170-031" in jobs_170 and "MNT-0172-028" in jobs_172
    return CheckResult(
        "Q2",
        expected,
        actual,
        ok,
        "Latest inspection per NE-08 well; open maintenance; inspector notes",
        _visuals_for("get_area_non_producing", payload),
        "" if ok else "Non-producing NE-08 set or reasons mismatch",
        "Open maintenance uses planned completed_at after today",
        "tests/test_eval_harness.py::test_q2",
    )


def check_q3(conn) -> CheckResult:
    history = production_history(conn, "WELL-0001", "2026-07-23", "2026-10-15")
    down = [item["inspected_at"][:10] for item in history["readings"] if item["producing"] is False]
    expected = "13 readings; not producing 2026-10-08 and 2026-10-15"
    actual = f"{len(history['readings'])} readings; down {down}"
    ok = len(history["readings"]) == 13 and down == ["2026-10-08", "2026-10-15"]
    return CheckResult(
        "Q3",
        expected,
        actual,
        ok,
        "production_history inclusive window",
        _visuals_for("get_production_history", history),
        "" if ok else "13-week series mismatch",
        "",
        "tests/test_domain.py::test_production_history_window_uses_inclusive_dates",
    )


def check_q4(conn) -> CheckResult:
    summary = effective_spill_summary(conn, asset_id="WELL-0001", start="2022-10-16", end="2026-10-16")
    expected = "6 spills, 15.1 bbl; INS-0001-0070 excluded"
    actual = f"{summary['spill_count']} spills, {summary['total_volume_bbl']} bbl"
    dates = [item["inspected_at"][:10] for item in summary["spills"]]
    volumes = [item.get("effective_volume_bbl") for item in summary["spills"]]
    ok = (
        summary["spill_count"] == 6
        and summary["total_volume_bbl"] == 15.1
        and dates == ["2023-03-16", "2023-04-27", "2023-09-28", "2023-10-19", "2026-06-11", "2026-10-01"]
        and volumes == [0.5, 2.6, 3.3, 0.3, 6.8, 1.6]
    )
    return CheckResult(
        "Q4",
        expected,
        actual,
        ok,
        "effective_spill_summary after formal void of INS-0001-0070",
        _visuals_for("get_effective_spills", summary),
        "" if ok else "Spill count/volume mismatch",
        "",
        "tests/test_domain.py::test_q4_well_0001_spills_last_four_years",
    )


def check_q5(conn) -> CheckResult:
    result = downtime_for_well(conn, "WELL-0001", "2025-01-01", "2025-12-31")
    expected = "21 days from 2025-07-24, 2025-10-16, 2025-10-23"
    dates = [item["inspected_at"][:10] for item in result["not_producing_inspections"]]
    actual = f"{result['days']} days {dates}"
    ok = result["days"] == 21 and dates == ["2025-07-24", "2025-10-16", "2025-10-23"]
    return CheckResult(
        "Q5",
        expected,
        actual,
        ok,
        "7 days per non-producing inspection",
        "none (short answer)",
        "" if ok else "2025 downtime mismatch",
        "",
        "tests/test_domain.py::test_q5_well_0001_downtime_in_2025",
    )


def check_q6(conn) -> CheckResult:
    ranked = top_wells_by_downtime(conn, "Northeast Block 7", "2026-06-16", "2026-10-16", limit=4)
    top = [(item["asset_id"], item["days"]) for item in ranked["wells"][:4]]
    expected = "WELL-0121 49; WELL-0138 and WELL-0107 42; WELL-0119 28"
    actual = str(top)
    ids = {top[0][0], top[1][0], top[2][0]}
    ok = top[0] == ("WELL-0121", 49) and ids == {"WELL-0121", "WELL-0138", "WELL-0107"} and top[3] == ("WELL-0119", 28)
    return CheckResult(
        "Q6",
        expected,
        actual,
        ok,
        "top_wells_by_downtime 2026-06-16 to 2026-10-16",
        _visuals_for("get_top_downtime_wells", ranked),
        "" if ok else "NE-07 ranking mismatch",
        "",
        "tests/test_domain.py::test_q6_northeast_block_7_downtime_last_four_months",
    )


def check_q7(conn) -> CheckResult:
    start = lookback_start(EVALUATION_TODAY, days=90)
    payload = spills_with_coordinates(conn, area="Northeast Block 8", start=start, end=EVALUATION_TODAY)
    expected = "WELL-0151 4.5; WELL-0165 1.2; WELL-0175 3.9; WELL-0186 0.6"
    pairs = {(item["asset_id"], item["effective_volume_bbl"]) for item in payload["markers"]}
    actual = str(sorted(pairs))
    ok = pairs == {("WELL-0151", 4.5), ("WELL-0165", 1.2), ("WELL-0175", 3.9), ("WELL-0186", 0.6)}
    coords = {item["asset_id"]: (item.get("lat"), item.get("lon")) for item in payload["markers"]}
    expected_coords = {
        "WELL-0151": (32.12246, -102.18445),
        "WELL-0165": (32.15665, -102.14522),
        "WELL-0175": (32.18020, -102.14790),
        "WELL-0186": (32.17552, -102.17775),
    }
    ok = ok and all(
        isclose(coords[asset][0], lat, abs_tol=1e-5) and isclose(coords[asset][1], lon, abs_tol=1e-5)
        for asset, (lat, lon) in expected_coords.items()
    )
    return CheckResult(
        "Q7",
        expected,
        actual,
        ok,
        f"effective spills {start} to {EVALUATION_TODAY} + stored well coordinates",
        _visuals_for("get_spills_map", payload),
        "" if ok else "NE-08 90-day spill map mismatch",
        "Added spills_with_coordinates(); never invents lat/lon",
        "tests/test_eval_harness.py::test_q7",
    )


def check_q8(conn) -> CheckResult:
    payload = spill_heatmap_by_area(conn, start="2026-01-01", end="2026-10-16")
    totals = {}
    for cell in payload["cells"]:
        totals[cell["row"]] = totals.get(cell["row"], 0) + int(cell["value"] or 0)
    expected = "SW-23 35; SW-24 43; NE-07 37; NE-08 26; NE-08 Aug 0"
    actual = str(totals)
    aug = next(cell for cell in payload["cells"] if "Northeast Block 8" in cell["row"] and cell["col"] == "2026-08")
    ok = (
        totals.get("Southwest Block 23") == 35
        and totals.get("Southwest Block 24") == 43
        and totals.get("Northeast Block 7") == 37
        and totals.get("Northeast Block 8") == 26
        and aug["value"] == 0
        and aug["missing"] is False
    )
    return CheckResult(
        "Q8",
        expected,
        actual,
        ok,
        "effective_spills grouped by area_id and month",
        _visuals_for("get_spill_heatmap", payload),
        "" if ok else "2026 spill heatmap totals mismatch",
        "Added spill_heatmap_by_area() distinct from inspection heatmap",
        "tests/test_eval_harness.py::test_q8",
    )


def check_q9(conn) -> CheckResult:
    start = lookback_start(EVALUATION_TODAY, years=1)
    findings = evaluate_well_compliance(conn, "WELL-0001", start, EVALUATION_TODAY, as_of=EVALUATION_TODAY)
    breaches = {item.rule_id for item in findings if item.is_breach}
    expected = "Breaches SR-01, SR-02, SR-03, RC-04; RCA pending; RC-02 met"
    actual = f"breaches={sorted(breaches)}"
    rc02 = [item for item in findings if item.rule_id == "RC-02"]
    rca = [item for item in findings if item.rule_id == "RCA"]
    ok = (
        breaches == {"SR-01", "SR-02", "SR-03", "RC-04"}
        and rc02
        and not any(item.is_breach for item in rc02)
        and rca
        and all(not item.is_breach for item in rca)
    )
    return CheckResult(
        "Q9",
        expected,
        actual,
        ok,
        "evaluate_well_compliance last 12 months",
        _visuals_for("get_well_compliance", [item.as_dict() for item in findings]),
        "" if ok else "Q9 breach set mismatch",
        "",
        "tests/test_compliance.py::test_q9_well_0001_last_12_months",
    )


def check_q10(conn) -> CheckResult:
    start = lookback_start(EVALUATION_TODAY, days=90)
    findings = late_containment_spills(conn, "Southwest Block 24", start, EVALUATION_TODAY)
    pairs = {(item.asset_id, item.observed_hours) for item in findings}
    expected = "WELL-0082 7.1h; WELL-0100 8.1h"
    actual = str(sorted(pairs))
    ok = pairs == {("WELL-0082", 7.1), ("WELL-0100", 8.1)}
    return CheckResult(
        "Q10",
        expected,
        actual,
        ok,
        "late_containment_spills from repair-report hours",
        _visuals_for("get_late_containment", [item.as_dict() for item in findings]),
        "" if ok else "SW-24 late containment mismatch",
        "",
        "tests/test_compliance.py::test_q10_southwest_block_24_late_containment",
    )


def check_q11(conn) -> CheckResult:
    costs = maintenance_costs(
        conn,
        asset_ids=["WELL-0121", "WELL-0138", "WELL-0107"],
        start="2026-01-01",
        end="2026-10-16",
    )
    by_id = {item["asset_id"]: item["cost_usd"] for item in costs["wells"]}
    expected = "WELL-0121 25794; WELL-0138 46942; WELL-0107 5176"
    actual = str(by_id)
    ok = by_id.get("WELL-0121") == 25794 and by_id.get("WELL-0138") == 46942 and by_id.get("WELL-0107") == 5176
    jessica = resolve_discussed_wells(conn, "U-202")
    ok = ok and jessica["found"] is False
    return CheckResult(
        "Q11",
        expected,
        actual,
        ok,
        "private conversation_entities for U-201 + SUM(cost_total_usd) by started_at",
        _visuals_for("get_maintenance_costs", costs),
        "" if ok else "Repair cost or memory resolution mismatch",
        "Added maintenance_costs(); uses discussed wells for the requesting user only",
        "tests/test_eval_harness.py::test_q11",
    )


def check_q12(conn) -> CheckResult:
    conn = _scratch(conn)
    try:
        apply_structured_correction(
            conn,
            inspection_id="INS-0164-0205",
            field_name="wellhead_temp_c",
            corrected_value=41.0,
            author_user_id="U-201",
            reason="faulty thermal gun; re-measured 41.0 C",
        )
        raw = conn.execute("SELECT wellhead_temp_c FROM inspections WHERE inspection_id = 'INS-0164-0205'").fetchone()
        status = current_production_status(conn, "WELL-0164")
        after = non_producing_in_area(conn, "Northeast Block 8")
        ids = [item["asset_id"] for item in after["wells"]]
        history = correction_history(conn, inspection_id="INS-0164-0205")
        expected = "Raw 24.0 remains; producing True; Q2 drops WELL-0164"
        actual = f"raw={raw['wellhead_temp_c']} producing={status['producing']} remaining={ids}"
        ok = (
            raw["wellhead_temp_c"] == 24.0
            and status["producing"] is True
            and status["delta_c"] == 21.1
            and "WELL-0164" not in ids
            and set(ids) == {"WELL-0170", "WELL-0172", "WELL-0175"}
            and history
        )
        return CheckResult(
            "Q12",
            expected,
            actual,
            ok,
            "INS-0164-0205 structured correction; original preserved",
            _visuals_for("get_well_status", status),
            "" if ok else "WELL-0164 correction did not propagate",
            "",
            "tests/test_memory.py::test_q15_shared_correction_on_baseline_well_0164",
        )
    finally:
        conn.close()


def check_q13(conn) -> CheckResult:
    conn = _scratch(conn)
    try:
        apply_structured_correction(
            conn,
            inspection_id="INS-0151-0201",
            field_name="spill_est_volume_bbl",
            corrected_value=5.4,
            author_user_id="U-201",
            reason="Field crew measured 5.4 bbl",
        )
        raw = conn.execute(
            "SELECT spill_est_volume_bbl FROM inspections WHERE inspection_id = 'INS-0151-0201'"
        ).fetchone()
        findings = {item.rule_id: item for item in evaluate_spill_compliance(conn, "INS-0151-0201", as_of=EVALUATION_TODAY)}
        expected = "Raw 4.5; SR-01 overdue reportable 5.4; SR-02 6.4h; SR-03 pending 23 Nov"
        actual = (
            f"raw={raw['spill_est_volume_bbl']} SR-01={findings['SR-01'].status} "
            f"class={findings['SR-01'].field_values.get('classification')} "
            f"SR-02={findings['SR-02'].observed_hours} SR-03={findings['SR-03'].deadline_at}"
        )
        ok = (
            raw["spill_est_volume_bbl"] == 4.5
            and findings["SR-01"].status == "overdue"
            and findings["SR-01"].field_values.get("classification") == "reportable"
            and findings["SR-02"].observed_hours == 6.4
            and str(findings["SR-03"].deadline_at or "").startswith("2026-11-23")
        )
        return CheckResult(
            "Q13",
            expected,
            actual,
            ok,
            "INS-0151-0201 volume correction + spill SOP rules",
            _visuals_for("get_well_compliance", [item.as_dict() for item in findings.values()]),
            "" if ok else "WELL-0151 volume correction compliance mismatch",
            "",
            "tests/test_compliance.py::test_q13_corrected_well_0151_volume",
        )
    finally:
        conn.close()


def check_q14(conn) -> CheckResult:
    conn = _scratch(conn)
    try:
        default = {item["asset_id"] for item in wells_requiring_rca(conn, area="Northeast Block 7", equipment_kind="pump_jack")}
        apply_rule_override(
            conn,
            rule_id="RCA",
            parameter="threshold",
            value=2,
            well_type="T350",
            equipment_kind="pump_jack",
            author_user_id="U-201",
            reason="T350 pump jack RCA after 2 repairs",
        )
        after = {item["asset_id"] for item in wells_requiring_rca(conn, area="Northeast Block 7", equipment_kind="pump_jack")}
        expected = "Default 0108/0121/0138; after T350@2 add 0120 not 0102"
        actual = f"default={sorted(default)} after={sorted(after)}"
        ok = default == {"WELL-0108", "WELL-0121", "WELL-0138"} and after == {
            "WELL-0108",
            "WELL-0120",
            "WELL-0121",
            "WELL-0138",
        }
        return CheckResult(
            "Q14",
            expected,
            actual,
            ok,
            "wells_requiring_rca + well_type rule override",
            _visuals_for("get_rca_required", list(after)),
            "" if ok else "RCA override scope mismatch",
            "",
            "tests/test_compliance.py::test_q14_northeast_block_7_pump_jack_rca",
        )
    finally:
        conn.close()


def check_q15(conn) -> CheckResult:
    conn = _scratch(conn)
    try:
        apply_structured_correction(
            conn,
            inspection_id="INS-0164-0205",
            field_name="wellhead_temp_c",
            corrected_value=41.0,
            author_user_id="U-201",
            reason="handheld re-measure",
        )
        apply_structured_correction(
            conn,
            inspection_id="INS-0151-0201",
            field_name="spill_est_volume_bbl",
            corrected_value=5.4,
            author_user_id="U-201",
            reason="field crew 5.4",
        )
        jessica = current_production_status(conn, "WELL-0164")
        start = lookback_start(EVALUATION_TODAY, days=90)
        spills = effective_spill_summary(conn, area="Northeast Block 8", start=start, end=EVALUATION_TODAY)
        reportable = [
            item for item in spills["spills"] if (item.get("effective_volume_bbl") or 0) >= 5
        ]
        expected = "Jessica sees WELL-0164 producing; only WELL-0151 reportable in NE-08 90d"
        actual = f"producing={jessica['producing']} reportable={[item['asset_id'] for item in reportable]}"
        ok = jessica["producing"] is True and [item["asset_id"] for item in reportable] == ["WELL-0151"]
        return CheckResult(
            "Q15",
            expected,
            actual,
            ok,
            "Shared structured corrections visible without user filter",
            _visuals_for("get_well_status", jessica),
            "" if ok else "Shared correction did not reach a second user/query",
            "",
            "tests/test_eval_harness.py::test_q15",
        )
    finally:
        conn.close()


def check_q16(conn) -> CheckResult:
    conn = _scratch(conn)
    note = add_shared_note(
        conn,
        asset_id="WELL-0007",
        author_user_id="U-102",
        text="WELL-0007 will be shut in from 17 Oct for a 10-day planned workover.",
        created_at="2026-10-16T08:00:00Z",
        valid_from="2026-10-17",
        valid_to="2026-10-27",
    )
    visible = list_shared_notes(conn, "WELL-0007", as_of="2026-10-18", requesting_user_id="U-201")
    jessica = resolve_discussed_wells(conn, "U-202")
    expected = "Shared Jake note visible to Michael; Jessica has no Q11 memory"
    actual = f"note_visible={any(item['note_id']==note['note_id'] for item in visible)} jessica_found={jessica['found']}"
    ok = any(item["note_id"] == note["note_id"] for item in visible) and jessica["found"] is False
    ok = ok and "no earlier conversation" in jessica["message"]
    result = CheckResult(
        "Q16",
        expected,
        actual,
        ok,
        "shared_notes vs conversation_entities",
        "none",
        "" if ok else "Shared note or private memory isolation failed",
        "",
        "tests/test_memory.py::test_private_memory_isolates_users_and_resolves_discussed_wells",
    )
    conn.close()
    return result


def check_q17(conn) -> CheckResult:
    conn = _scratch(conn)
    matching = record_statement(
        conn,
        author_user_id="U-201",
        asset_id="WELL-0175",
        text="WELL-0175 has been shut in for planned work since 14 Oct.",
        asserted_state="shut_in",
        created_at="2026-10-16T11:00:00Z",
    )
    conflict = record_statement(
        conn,
        author_user_id="U-204",
        asset_id="WELL-0175",
        text="WELL-0175 is running fine.",
        asserted_state="producing",
        created_at="2026-10-16T12:00:00Z",
    )
    jake = record_statement(
        conn,
        author_user_id="U-102",
        asset_id="WELL-0001",
        text="WELL-0001 is back online since 14 Oct.",
        asserted_state="producing",
        created_at="2026-10-16T13:00:00Z",
    )
    expected = "Turner note shared; Tyler and Jake claims unconfirmed"
    actual = f"turner={matching['kind']} tyler={conflict['kind']} jake={jake['kind']}"
    ok = (
        matching["kind"] == "shared_note"
        and conflict["needs_confirmation"] is True
        and jake["needs_confirmation"] is True
        and conflict["effective_production"]["producing"] is False
    )
    result = CheckResult(
        "Q17",
        expected,
        actual,
        ok,
        "record_statement vs latest inspection",
        "none",
        "" if ok else "Conflict handling failed",
        "",
        "tests/test_memory.py::test_conflicting_claim_stays_unconfirmed",
    )
    conn.close()
    return result


def check_q18(conn) -> CheckResult:
    result = spill_feedback_conflict(conn, "INS-0101-0197")
    ids = {item["note_id"] for item in result["operator_feedback"]}
    expected = "1.0 bbl stands; CTX-0101-002 and CTX-0101-003 disagree; no formal correction"
    actual = (
        f"detected={result['effective_spill_detected']} vol={result['effective_volume_bbl']} "
        f"notes={sorted(ids)} formal={result['formal_corrections']}"
    )
    ok = (
        result["effective_spill_detected"] is True
        and result["effective_volume_bbl"] == 1.0
        and {"CTX-0101-002", "CTX-0101-003"} <= ids
        and result["formal_corrections"] == []
        and result["keeps_established_detection"] is True
    )
    return CheckResult(
        "Q18",
        expected,
        actual,
        ok,
        "INS-0101-0197 + operator notes; detection stands",
        "none",
        "" if ok else "WELL-0101 spill conflict handling mismatch",
        "",
        "tests/test_memory.py::test_operator_feedback_conflict_without_formal_correction",
    )


CHECKS: list[Callable] = [
    check_q1,
    check_q2,
    check_q3,
    check_q4,
    check_q5,
    check_q6,
    check_q7,
    check_q8,
    check_q9,
    check_q10,
    check_q11,
    check_q12,
    check_q13,
    check_q14,
    check_q15,
    check_q16,
    check_q17,
    check_q18,
]


def run_evaluation(conn) -> list[CheckResult]:
    init_schema(conn)
    return [check(conn) for check in CHECKS]


def render_report(results: list[CheckResult]) -> str:
    passed = sum(1 for item in results if item.passed)
    lines = [
        "# Evaluation QA report",
        "",
        f"Checked {len(results)} example questions against deterministic domain queries on the sample dataset.",
        f"**{passed}/{len(results)} passed.** Live model wording was not used as the source of truth.",
        "",
        "| ID | Pass | Expected | Actual | Visual | Evidence | Failure | Fix | Regression |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        def cell(value: str) -> str:
            return str(value).replace("|", "/").replace("\n", " ")

        lines.append(
            f"| {item.question_id} | {'PASS' if item.passed else 'FAIL'} | {cell(item.expected)} | "
            f"{cell(item.actual)} | {cell(item.visualization)} | {cell(item.evidence)} | "
            f"{cell(item.failure) or '-'} | {cell(item.fix_applied) or '-'} | {cell(item.regression_test)} |"
        )
    lines.extend(
        [
            "",
            "## Method",
            "",
            "Each check calls existing domain functions (or the smallest new query wrapper) and compares",
            "IDs, counts, volumes, dates, and statuses to `03-evaluation-queries.md`.",
            "Application logic does not hardcode these expected answers.",
            "Mutating scenarios (Q12-Q17) run on `sqlite3.Connection.backup()` clones so corrections",
            "cannot leak into the source database or later checks.",
            "",
            "## Discovered during this QA",
            "",
            "- Q2 initially failed: `maintenance.completed_at` for in-progress jobs is a planned finish",
            "  (dataset guide). Treating any non-empty `completed_at` as closed hid MNT-0170-031 and",
            "  MNT-0172-028. Fix: a job is open when `completed_at` is empty or its calendar date is",
            "  after evaluation today (16 Oct 2026).",
            "- Session-scoped `baseline_db` was polluted by structured-correction tests; those tests",
            "  now clone first. Domain spill math was not changed.",
            "",
            "## Also exercised",
            "",
            "- Mini fixture ingest (`tests/fixtures/mini-data`) plus sample wells other than WELL-0001",
            "  (NE-07 vs NE-08 non-producing sets, Q7/Q10/Q11/Q14 asset IDs).",
            "- Multi-user isolation: Jessica (`U-202`) cannot resolve Michael Turner's discussed wells;",
            "  shared structured corrections and Jake's WELL-0007 note remain visible across users.",
            "",
            "## Not verified in this harness",
            "",
            "- Live Gemini/Gemma natural-language wording for each question",
            "- Unseen final evaluation dataset (not provided in this repo)",
            "- Browser screenshot of every generated visual from a live model turn",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Q1–Q18 evaluation")
    parser.add_argument("--db", default=str(get_database_path()))
    parser.add_argument("--out", default=str(PROJECT_ROOT / "reports" / "qa-eval.md"))
    args = parser.parse_args(argv)
    conn = connect(Path(args.db))
    try:
        results = run_evaluation(conn)
    finally:
        conn.close()
    report = render_report(results)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    return 0 if all(item.passed for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
