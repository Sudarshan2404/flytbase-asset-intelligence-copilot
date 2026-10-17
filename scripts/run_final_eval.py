"""Live final-evaluation runner for test-data/final-questions.md.

Requires EVALUATION_TODAY=2027-01-15 and DATABASE_PATH=data/eval.db in the process
environment before this module imports application code. Do not point at data/copilot.db.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("EVALUATION_TODAY", "2027-01-15")
os.environ.setdefault("DATABASE_PATH", str(ROOT / "data" / "eval.db"))

from app.config import get_database_path, load_env_file  # noqa: E402
from app.copilot.schemas import ChatRequest  # noqa: E402
from app.copilot.service import handle_chat  # noqa: E402
from app.db import connect, init_schema, table_counts  # noqa: E402
from app.domain.eval_ops import (  # noqa: E402
    cold_weather_misses,
    extra_shut_in,
    fleet_directory,
    flowline_bypasses,
    gas_response_compliance,
    operator_warning_wells,
    recalled_pumps,
    region_temperatures,
    regulator_letter_spills,
    reservoir_pressure_map,
    status_disagreements,
)
from app.domain.measurements import oil_production_totals  # noqa: E402

USERS = {
    "Michael Turner": "U-201",
    "Robert Hayes": "U-301",
    "Laura Simmons": "U-401",
    "Chris Dalton": "U-402",
    "Olivia Grant": "U-501",
    "Nathan Pierce": "U-601",
}

TURNS = [
    {"qid": "Q1", "session": "new", "user": "Michael Turner", "message": "All our wells feed one reservoir. Map the reservoir pressure across the field from the pressure data."},
    {"qid": "Q2", "session": "new", "user": "Robert Hayes", "message": "Which well produced the most oil in 2026?"},
    {"qid": "Q3", "session": "new", "user": "Laura Simmons", "message": "Since 1 Nov 2026, every time an operator note reports gas at a well, did we get someone out to that well in time under our operating procedures? List every report and whether the response met the deadline."},
    {"qid": "Q4", "session": "new", "user": "Chris Dalton", "message": "Across all areas, which temporary flowline bypasses are in place right now, and is each one allowed? Also list any bypass, past or present, that stayed in after its approval ran out or was never approved at all."},
    {"qid": "Q5", "session": "new", "user": "Michael Turner", "message": "The State Oil & Gas Commission wrote to us in November 2026 about spills. Which of our spills does the letter cover, and on which ones are we not compliant? Show me a table."},
    {"qid": "Q6", "session": "new", "user": "Nathan Pierce", "message": "Across the whole field, not just my area, for which wells do the latest inspection and the operator notes disagree about whether the well is producing oil? For each one, what is its real status right now, and why?"},
    {"qid": "Q7T1", "session": "new", "user": "Robert Hayes", "message": "Over the last month the operators have written notes about wells that sound or behave wrong. Which of those wells haven't had a repair since? Rank the 10 most likely to need a repair in the next 90 days, other than a spill clean-up, and quote the notes as evidence."},
    {"qid": "Q7T2", "session": "same", "user": "Robert Hayes", "message": "Which three would you send a crew to first this week, and why?"},
    {"qid": "Q8T1", "session": "new", "user": "Nathan Pierce", "message": "WELL-0333's recalled pump was swapped out yesterday. The work order isn't in the system yet. Please count it as replaced from now on."},
    {"qid": "Q8T2", "session": "same", "user": "Nathan Pierce", "message": "Also, just for me: whenever you list wells, sort them by area and then by well ID, and show costs in thousands of dollars."},
    {"qid": "Q8T3", "session": "new", "user": "Robert Hayes", "message": "Which recalled pumps are still in the ground right now?"},
    {"qid": "Q8T4", "session": "new", "user": "Olivia Grant", "message": "Nathan told me the recalled pump on WELL-0577 was swapped as well. Take it off the recall list."},
    {"qid": "Q8T5", "session": "new", "user": "Nathan Pierce", "message": "Show me the recalled pumps that are still in the ground in my area, and what it cost to install each one."},
    {"qid": "Q9T1", "session": "new", "user": "Robert Hayes", "message": "How many recalled pumps are still in the ground, and in which areas?"},
    {"qid": "Q9T2", "session": "same", "user": "Robert Hayes", "message": "Different topic. Who leads Northwest Production, and how many wells does that team run?"},
    {"qid": "Q9T3", "session": "same", "user": "Robert Hayes", "message": "What does our operations SOP say about cold weather? Name one well that missed the check in December."},
    {"qid": "Q9T4", "session": "same", "user": "Robert Hayes", "message": "An operator reported a gas smell at WELL-0332 on 3 Dec. What was the H2S reading?"},
    {"qid": "Q9T5", "session": "same", "user": "Robert Hayes", "message": "Back to the pumps from my first question: which of those wells are not producing oil right now, and when was each one last repaired?"},
    {"qid": "Q9T6", "session": "same", "user": "Robert Hayes", "message": "Of all the wells we've talked about today, which one would you deal with first, and why?"},
    {"qid": "Q10", "session": "new", "user": "Michael Turner", "message": "When a well stays shut in longer than the repair itself, what are we usually waiting on: parts, crews, contractors or weather? Break it down by area for jobs started in 2026, with the typical number of extra days, and tell me if any area stands out."},
    {"qid": "Q11", "session": "new", "user": "Olivia Grant", "message": "Which region of the field consistently runs hotter?"},
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _score(turn: dict, response) -> dict:
    tools = [item.name for item in response.tool_trace]
    visuals = [item.type for item in response.visuals]
    answer = response.answer or ""
    error = response.error
    result = "PASS"
    reasons = []
    if error:
        lowered = str(error).lower()
        if "429" in lowered or "resource exhausted" in lowered or "rate" in lowered:
            return {"verdict": "BLOCKED", "reasons": [str(error)], "tools": tools, "visuals": visuals}
        result = "FAIL"
        reasons.append(str(error))
    qid = turn["qid"]
    if response.user_id != USERS[turn["user"]]:
        result = "FAIL"
        reasons.append("user_id changed")
    if qid == "Q1":
        if "get_reservoir_pressure" not in tools and "get_available_measurements" not in tools:
            result = "FAIL"
            reasons.append("did not inspect reservoir-pressure availability")
        if any(token in answer.lower() for token in ("highest pressure", "psi at well")):
            result = "FAIL"
            reasons.append("invented a pressure ranking")
    if qid == "Q2":
        if "get_oil_production_totals" not in tools:
            result = "FAIL"
            reasons.append("did not call oil-volume totals")
        if "WELL-" in answer and "not recorded" not in answer.lower() and "not available" not in answer.lower():
            result = "FAIL"
            reasons.append("named a winning well without an oil-volume field")
    if qid in {"Q3", "Q4", "Q5", "Q6", "Q7T1", "Q8T3", "Q8T5", "Q10"}:
        if "table" not in visuals:
            result = "FAIL"
            reasons.append("required table visual missing")
    if qid == "Q10" and "bar_chart" not in visuals:
        result = "FAIL"
        reasons.append("required bar chart missing")
    if qid == "Q8T1" and "record_shared_note" not in tools:
        reasons.append("did not record a shared replacement note")
        result = "FAIL"
    if qid == "Q8T2" and "record_user_preference" not in tools:
        reasons.append("did not store the private preference")
        result = "FAIL"
    if qid == "Q11" and "get_region_temperatures" not in tools:
        result = "FAIL"
        reasons.append("did not call region temperatures")
    if not reasons and result == "PASS":
        reasons.append("required tools/visuals present; no provider error")
    return {"verdict": result, "reasons": reasons, "tools": tools, "visuals": visuals}


def _dump_response(response) -> dict:
    return {
        "answer": response.answer,
        "user_id": response.user_id,
        "conversation_id": response.conversation_id,
        "error": response.error,
        "citations": [item.model_dump() for item in response.citations],
        "visual": response.visual.model_dump() if response.visual else None,
        "visuals": [item.model_dump() for item in response.visuals],
        "conflicts": [item.model_dump() for item in response.conflicts],
        "pending_confirmation": [item.model_dump() for item in response.pending_confirmation],
        "tool_trace": [item.model_dump() for item in response.tool_trace],
    }


def main() -> int:
    load_env_file()
    db_path = get_database_path()
    if db_path.name == "copilot.db":
        print("Refusing to run against data/copilot.db", file=sys.stderr)
        return 2
    if not db_path.is_file():
        print(f"Eval database missing: {db_path}", file=sys.stderr)
        return 2
    out_dir = ROOT / "evaluation-output"
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    init_schema(conn)
    reference = {
        "as_of": os.environ.get("EVALUATION_TODAY"),
        "database": str(db_path),
        "counts": table_counts(conn),
        "reservoir_pressure": reservoir_pressure_map(conn),
        "oil_2026": oil_production_totals(conn, "2026-01-01", "2026-12-31"),
        "gas_response": gas_response_compliance(conn, "2026-11-01"),
        "bypasses": flowline_bypasses(conn),
        "letter_spills": regulator_letter_spills(conn),
        "status_disagreements": status_disagreements(conn),
        "operator_warnings": operator_warning_wells(conn, "2026-12-15", "2027-01-15"),
        "recalled_pumps": recalled_pumps(conn),
        "extra_shut_in": extra_shut_in(conn, "2026-01-01", "2026-12-31"),
        "region_temperatures": region_temperatures(conn, "2026-10-15", "2027-01-15"),
        "cold_weather_dec": cold_weather_misses(conn, "2026-12-01", "2026-12-31"),
        "northwest": fleet_directory(conn, "Northwest Production"),
    }
    (out_dir / "deterministic-reference.json").write_text(json.dumps(reference, indent=2, default=str), encoding="utf-8")

    sessions: dict[str, str | None] = {}
    rows = []
    jsonl_path = out_dir / "final-eval.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for turn in TURNS:
            user_id = USERS[turn["user"]]
            conversation_id = None if turn["session"] == "new" else sessions.get(user_id)
            request = ChatRequest(message=turn["message"], user_id=user_id, conversation_id=conversation_id)
            response = handle_chat(conn, request)
            conn.commit()
            sessions[user_id] = response.conversation_id
            score = _score(turn, response)
            record = {
                "qid": turn["qid"],
                "user": turn["user"],
                "user_id": user_id,
                "session": turn["session"],
                "message": turn["message"],
                "asked_at": _now(),
                "score": score,
                "response": _dump_response(response),
            }
            handle.write(json.dumps(record, default=str) + "\n")
            handle.flush()
            rows.append(record)
            print(f"{turn['qid']} {score['verdict']} user={user_id} conv={response.conversation_id}", flush=True)

    report = ["# Final evaluation report", "", f"- Database: `{db_path}`", f"- Today: `{os.environ.get('EVALUATION_TODAY')}`", f"- Turns: {len(rows)}", ""]
    counts = {"PASS": 0, "FAIL": 0, "BLOCKED": 0}
    for record in rows:
        verdict = record["score"]["verdict"]
        counts[verdict] = counts.get(verdict, 0) + 1
        report.append(f"## {record['qid']} — {record['user']} ({record['user_id']}) — {verdict}")
        report.append("")
        report.append(f"**Session:** {record['session']}  ")
        report.append(f"**Question:** {record['message']}")
        report.append("")
        report.append(record["response"]["answer"] or "(empty)")
        report.append("")
        report.append(f"- Tools: {', '.join(record['score']['tools']) or 'none'}")
        report.append(f"- Visuals: {', '.join(record['score']['visuals']) or 'none'}")
        report.append(f"- Reasons: {'; '.join(record['score']['reasons'])}")
        if record["response"]["error"]:
            report.append(f"- Error: {record['response']['error']}")
        report.append("")
    report.insert(5, f"- PASS {counts['PASS']} / FAIL {counts['FAIL']} / BLOCKED {counts['BLOCKED']}")
    report.insert(6, "")
    oil = reference["oil_2026"]
    report.append("## Oil-volume limitation")
    report.append("")
    report.append(oil.get("message") or json.dumps(oil, default=str)[:1000])
    report.append("")
    (out_dir / "final-eval-report.md").write_text("\n".join(report), encoding="utf-8")
    (out_dir / "final-eval-summary.json").write_text(json.dumps({"counts": counts, "rows": rows}, indent=2, default=str), encoding="utf-8")
    conn.close()
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
