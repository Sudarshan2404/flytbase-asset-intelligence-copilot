# Evaluation QA report

Checked 18 example questions against deterministic domain queries on the sample dataset.
**18/18 passed.** Live model wording was not used as the source of truth.

| ID | Pass | Expected | Actual | Visual | Evidence | Failure | Fix | Regression |
|---|---|---|---|---|---|---|---|---|
| Q1 | PASS | Not producing; INS-0001-0203; 15 Oct 2026; drone; Ryan Brooks; delta 3.2 C | producing=False INS-0001-0203 2026-10-15 drone Ryan Brooks delta=3.200000000000003 | status_card:WELL-0001 status | INS-0001-0203; CTX-0001-003; T200 delta rule | - | Inspector name lookup from users table | tests/test_eval_harness.py::test_q1 |
| Q2 | PASS | WELL-0164, WELL-0170, WELL-0172, WELL-0175 | WELL-0164, WELL-0170, WELL-0172, WELL-0175 | table:Not producing in Northeast Block 8 | Latest inspection per NE-08 well; open maintenance; inspector notes | - | Open maintenance uses planned completed_at after today | tests/test_eval_harness.py::test_q2 |
| Q3 | PASS | 13 readings; not producing 2026-10-08 and 2026-10-15 | 13 readings; down ['2026-10-08', '2026-10-15'] | line_chart:WELL-0001 temperature history | production_history inclusive window | - | - | tests/test_domain.py::test_production_history_window_uses_inclusive_dates |
| Q4 | PASS | 6 spills, 15.1 bbl; INS-0001-0070 excluded | 6 spills, 15.1 bbl | bar_chart:Effective spills by classification, table:Spill records, status_card:Spill summary | effective_spill_summary after formal void of INS-0001-0070 | - | - | tests/test_domain.py::test_q4_well_0001_spills_last_four_years |
| Q5 | PASS | 21 days from 2025-07-24, 2025-10-16, 2025-10-23 | 21 days ['2025-07-24', '2025-10-16', '2025-10-23'] | none (short answer) | 7 days per non-producing inspection | - | - | tests/test_domain.py::test_q5_well_0001_downtime_in_2025 |
| Q6 | PASS | WELL-0121 49; WELL-0138 and WELL-0107 42; WELL-0119 28 | [('WELL-0121', 49), ('WELL-0107', 42), ('WELL-0138', 42), ('WELL-0119', 28)] | bar_chart:Downtime Northeast Block 7, table:Downtime detail | top_wells_by_downtime 2026-06-16 to 2026-10-16 | - | - | tests/test_domain.py::test_q6_northeast_block_7_downtime_last_four_months |
| Q7 | PASS | WELL-0151 4.5; WELL-0165 1.2; WELL-0175 3.9; WELL-0186 0.6 | [('WELL-0151', 4.5), ('WELL-0165', 1.2), ('WELL-0175', 3.9), ('WELL-0186', 0.6)] | map:Effective spills with stored coordinates | effective spills 2026-07-18 to 2026-10-16 + stored well coordinates | - | Added spills_with_coordinates(); never invents lat/lon | tests/test_eval_harness.py::test_q7 |
| Q8 | PASS | SW-23 35; SW-24 43; NE-07 37; NE-08 26; NE-08 Aug 0 | {'Northeast Block 7': 37, 'Northeast Block 8': 26, 'Southwest Block 23': 35, 'Southwest Block 24': 43} | heatmap:Effective spills by area and month | effective_spills grouped by area_id and month | - | Added spill_heatmap_by_area() distinct from inspection heatmap | tests/test_eval_harness.py::test_q8 |
| Q9 | PASS | Breaches SR-01, SR-02, SR-03, RC-04; RCA pending; RC-02 met | breaches=['RC-04', 'SR-01', 'SR-02', 'SR-03'] | status_card:Compliance, table:Compliance findings | evaluate_well_compliance last 12 months | - | - | tests/test_compliance.py::test_q9_well_0001_last_12_months |
| Q10 | PASS | WELL-0082 7.1h; WELL-0100 8.1h | [('WELL-0082', 7.1), ('WELL-0100', 8.1)] | status_card:Compliance, table:Compliance findings | late_containment_spills from repair-report hours | - | - | tests/test_compliance.py::test_q10_southwest_block_24_late_containment |
| Q11 | PASS | WELL-0121 25794; WELL-0138 46942; WELL-0107 5176 | {'WELL-0121': 25794, 'WELL-0138': 46942, 'WELL-0107': 5176} | bar_chart:Repair costs, table:Repair costs | private conversation_entities for U-201 + SUM(cost_total_usd) by started_at | - | Added maintenance_costs(); uses discussed wells for the requesting user only | tests/test_eval_harness.py::test_q11 |
| Q12 | PASS | Raw 24.0 remains; producing True; Q2 drops WELL-0164 | raw=24.0 producing=True remaining=['WELL-0170', 'WELL-0172', 'WELL-0175'] | status_card:WELL-0164 status | INS-0164-0205 structured correction; original preserved | - | - | tests/test_memory.py::test_q15_shared_correction_on_baseline_well_0164 |
| Q13 | PASS | Raw 4.5; SR-01 overdue reportable 5.4; SR-02 6.4h; SR-03 pending 23 Nov | raw=4.5 SR-01=overdue class=reportable SR-02=6.4 SR-03=2026-11-23T15:30:00Z | status_card:Compliance, table:Compliance findings | INS-0151-0201 volume correction + spill SOP rules | - | - | tests/test_compliance.py::test_q13_corrected_well_0151_volume |
| Q14 | PASS | Default 0108/0121/0138; after T350@2 add 0120 not 0102 | default=['WELL-0108', 'WELL-0121', 'WELL-0138'] after=['WELL-0108', 'WELL-0120', 'WELL-0121', 'WELL-0138'] | table:Wells requiring RCA | wells_requiring_rca + well_type rule override | - | - | tests/test_compliance.py::test_q14_northeast_block_7_pump_jack_rca |
| Q15 | PASS | Jessica sees WELL-0164 producing; only WELL-0151 reportable in NE-08 90d | producing=True reportable=['WELL-0151'] | status_card:WELL-0164 status | Shared structured corrections visible without user filter | - | - | tests/test_eval_harness.py::test_q15 |
| Q16 | PASS | Shared Jake note visible to Michael; Jessica has no Q11 memory | note_visible=True jessica_found=False | none | shared_notes vs conversation_entities | - | - | tests/test_memory.py::test_private_memory_isolates_users_and_resolves_discussed_wells |
| Q17 | PASS | Turner note shared; Tyler and Jake claims unconfirmed | turner=shared_note tyler=conflict jake=conflict | none | record_statement vs latest inspection | - | - | tests/test_memory.py::test_conflicting_claim_stays_unconfirmed |
| Q18 | PASS | 1.0 bbl stands; CTX-0101-002 and CTX-0101-003 disagree; no formal correction | detected=True vol=1.0 notes=['CTX-0101-002', 'CTX-0101-003'] formal=[] | none | INS-0101-0197 + operator notes; detection stands | - | - | tests/test_memory.py::test_operator_feedback_conflict_without_formal_correction |

## Method

Each check calls existing domain functions (or the smallest new query wrapper) and compares
IDs, counts, volumes, dates, and statuses to `03-evaluation-queries.md`.
Application logic does not hardcode these expected answers.
Mutating scenarios (Q12-Q17) run on `sqlite3.Connection.backup()` clones so corrections
cannot leak into the source database or later checks.

## Discovered during this QA

- Q2 initially failed: `maintenance.completed_at` for in-progress jobs is a planned finish
  (dataset guide). Treating any non-empty `completed_at` as closed hid MNT-0170-031 and
  MNT-0172-028. Fix: a job is open when `completed_at` is empty or its calendar date is
  after evaluation today (16 Oct 2026).
- Session-scoped `baseline_db` was polluted by structured-correction tests; those tests
  now clone first. Domain spill math was not changed.

## Also exercised

- Mini fixture ingest (`tests/fixtures/mini-data`) plus sample wells other than WELL-0001
  (NE-07 vs NE-08 non-producing sets, Q7/Q10/Q11/Q14 asset IDs).
- Multi-user isolation: Jessica (`U-202`) cannot resolve Michael Turner's discussed wells;
  shared structured corrections and Jake's WELL-0007 note remain visible across users.

## Not verified in this harness

- Live Gemini/Gemma natural-language wording for each question
- Unseen final evaluation dataset (not provided in this repo)
- Browser screenshot of every generated visual from a live model turn

