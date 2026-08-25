```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:fc6374b195dfbffb06298450f142f99167badf1bed4b902d8ed04914793b0f6d
verdict: fail
blockers: 1
critical_findings: 1
requirements: 12/12
scenarios: 34/34
test_command: uv run pytest -p no:randomly
test_exit_code: 0
test_output_hash: sha256:f6dd1794dea1667d3813311d3fad107c2fb90d7aecb414dce3bbd1ceecd733b4
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:3bce484934e38822ff7a68a9eb1d47d2dd53c92386f38d0dd24152acfb6fab7c
```

## Verification Report

**Change**: failure-capture
**Version**: N/A (no numeric identifiers minted; traced by capability + scenario name)
**Mode**: Strict TDD

**Verdict is `fail` for routing, not for code quality.** Every delta-spec
scenario is discharged, all 534 tests pass, and all four static gates are
clean. The change is blocked from archive by an unresolved, measured
requirement breach (RQ-25) and one regression-exposure gap found by tampering.

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 109 |
| Tasks complete | 109 |
| Tasks incomplete | 0 |

### Build & Tests Execution
**Build**: PASS — `uv run mypy .` → `Success: no issues found in 81 source files`, exit 0
**Tests**: PASS — `uv run pytest -p no:randomly` → `534 passed, 12 warnings in 58.74s`, exit 0
**deptry**: PASS — `Success! No dependency issues found.` (80 files)
**ruff**: PASS — `81 files already formatted`, `All checks passed!`
**Coverage**: Not measured — this project has no `pytest-cov` by deliberate
configuration (`coverage_threshold: 0`). Not a gap.

All figures re-run independently in this phase, not taken from apply-progress.

### Spec Compliance Matrix

12 requirements / 34 scenarios across the five delta specs
(failure-evidence 7/16, history-read-api 2/7, session-ingestion 2/5,
run-recording 1/6, result-capture 0/0 — Purpose-text-only delta). Of the 34,
29 are new/modified (the tasks.md coverage table) and 5 are pre-existing
run-recording RQ-3 scenarios carried as regression gates.

| # | Scenario | Capability | Evidence | Result |
|---|---|---|---|---|
| 1 | Traceback complete under `--tb=no` | failure-evidence | `test_evidence.py::test_traceback_is_complete_under_tb_no` | COMPLIANT |
| 2 | Traceback complete under `--tb=line` | failure-evidence | `test_evidence.py::test_traceback_is_complete_under_tb_line` | COMPLIANT |
| 3 | Twenty tests at one line group as one | failure-evidence | `test_evidence.py::test_twenty_tests_failing_at_one_line_group_as_one` | COMPLIANT |
| 4 | Recorded location is the raising site | failure-evidence | `test_evidence.py::test_recorded_location_is_the_raising_helper_not_the_test_function` | COMPLIANT |
| 5 | Skipped test does not crash the recorder | failure-evidence | `test_evidence.py::test_skipped_test_records_skip_reason_not_failure_fields` | COMPLIANT |
| 6 | Silent test has empty output, not absent | failure-evidence | `test_evidence.py::test_silent_test_has_empty_captured_output_not_absent` | COMPLIANT |
| 7 | Capture disabled leaves output absent | failure-evidence | `test_evidence.py::test_capture_disabled_leaves_output_absent` | COMPLIANT |
| 8 | Oversized field stored truncated, flagged | failure-evidence | `test_routes_runs.py::test_to_result_bounds_a_64kib_oversized_traceback_and_flags_it` | COMPLIANT |
| 9 | Field within bound stored whole, unflagged | failure-evidence | `test_routes_runs.py::test_to_result_a_field_within_bound_is_stored_whole_unflagged` | COMPLIANT |
| 10 | Many large failures stay within size cap | failure-evidence | `test_run_report.py::test_a_session_of_many_large_failures_stays_within_the_report_size_cap` | COMPLIANT |
| 11 | Field dropped for budget is flagged | failure-evidence | `test_report_budget.py::test_a_dropped_field_is_null_with_its_truncated_flag_set` (tamper-confirmed) | COMPLIANT |
| 12 | Session within budget carries no flags | failure-evidence | `test_report_budget.py::test_a_session_within_budget_sets_no_exhaustion_flags` | COMPLIANT |
| 13 | Opt-out suppresses failure-text capture | failure-evidence | `test_evidence.py::test_opt_out_flag_means_evidencecollector_is_never_registered` | COMPLIANT |
| 14 | Committed config cannot enable capture | failure-evidence | `test_opt_in.py::test_failure_text_opt_out_ini_alone_cannot_enable_capture` — differential tree-snapshot form, correct | COMPLIANT |
| 15 | Opt-out does not suppress rest of result | failure-evidence | `test_evidence.py::test_opt_out_does_not_suppress_outcome_timings_or_identity` | COMPLIANT |
| 16 | Disclosure in capability spec and README | failure-evidence | **Inspection** — `README.md:66` + `specs/failure-evidence/spec.md:222-234`. Verified by inspection this phase, correctly not an assertion | COMPLIANT |
| 17 | List responses exclude traceback/output | history-read-api | `test_routes_read.py::test_results_route_response_excludes_traceback_and_captured_output_sentinel`; structurally enforced — `ResultListEntry` has no such field | COMPLIANT |
| 18 | Commit subject bounded in list responses | history-read-api | pre-existing `vantage_port_contract.py::test_list_runs_bounds_commit_subject_at_display_width` — claim verified, test real and passing | COMPLIANT |
| 19 | Truncation flag never surfaces alone | history-read-api | pre-existing `test_execution.py::test_vcs_context_merged_over_truncated_flag_travels_with_commit_subject`, `vantage_port_contract.py::test_list_runs_flags_capture_truncated_subject_even_when_short` | COMPLIANT |
| 20 | `vcs_root` in no list or detail response | history-read-api | pre-existing `test_routes_read.py::test_run_list_response_contains_no_vcs_root_anywhere`, `::test_run_detail_response_contains_no_vcs_root`, `::test_history_route_response_contains_no_vcs_root` | COMPLIANT |
| 21 | Full record reachable for a given result | history-read-api | `vantage_port_contract.py::test_get_result_returns_the_full_record_hit` (both adapters) + `test_routes_read.py::test_result_detail_route_returns_full_record[memory|sqlite]` (tamper-confirmed) | COMPLIANT |
| 22 | Truncation flag travels on single-item endpoint | history-read-api | `vantage_port_contract.py::test_get_result_truncation_flag_travels_with_the_field` + `test_routes_read.py::test_result_detail_truncation_flag_travels_with_the_field[memory|sqlite]` (tamper-confirmed) | COMPLIANT |
| 23 | Unknown result identifier changes nothing | history-read-api | `vantage_port_contract.py::test_get_result_returns_none_for_unknown_node_id_miss` + `test_routes_read.py::test_result_detail_unknown_node_id_is_404_unknown_result_error`, `::test_result_detail_unknown_identifier_leaves_stored_data_unchanged` | COMPLIANT |
| 24 | Older plugin omitting fields still stores | session-ingestion | `test_ingestion.py::test_an_older_plugin_omitting_failure_fields_still_stores_run_and_results` | COMPLIANT |
| 25 | Newer plugin's fields are persisted | session-ingestion | `test_ingestion.py::test_a_newer_plugins_failure_evidence_fields_are_persisted` (tamper-confirmed) | COMPLIANT |
| 26 | Older server tolerates newer fields | session-ingestion | `test_ingestion.py::test_an_older_server_tolerates_unrecognized_failure_evidence_keys` | COMPLIANT |
| 27 | Report exceeding size cap stores nothing | session-ingestion | `test_rejection.py::test_a_report_exceeding_the_size_cap_with_failure_evidence_stores_nothing` | COMPLIANT |
| 28 | Report within cap accepted normally | session-ingestion | `test_ingestion.py::test_a_report_carrying_failure_evidence_within_the_cap_is_accepted_normally` | COMPLIANT |
| 29 | Measurements re-run for the new column set | run-recording | **Analysis** — `scripts/measure_failure_capture_overhead.py` executed; six-cell table transcribed into `specs/failure-evidence/spec.md:151-158` and `specs/run-recording/spec.md:88-124`. Discharged as Analysis, arithmetic re-verified cell by cell this phase | COMPLIANT |
| 30-34 | RQ-3.1-3.4 + reordered-start-write | run-recording | pre-existing; `vantage_port_contract.py::test_reordered_start_after_finish_never_nulls_the_recorded_finish`, `test_rejection.py::test_finish_report_reaches_storage_in_one_commit` (31-column re-confirmation) | COMPLIANT |

**Compliance summary**: 34/34 scenarios discharged at their declared
verification level (31 Test, 1 Inspection, 1 Analysis, plus the carried
regression gates).

**Verification-method discipline observed**: scenario 16 is Inspection and
scenario 29 is Analysis. Neither is reported as discharged by an assertion.
RQ-25's obligation is Analysis — a measurement with a method — and is
assessed as such below, not as a test result.

### RQ-25 compliance — explicit statement

**This change is NOT RQ-25-compliant, and must not be archived as if it
were.**

The measurement method is sound. `scripts/measure_failure_capture_overhead.py`
follows the already-accepted shape of `scripts/measure_vcs_overhead.py`: five
interleaved A/B/A/B pairs per cell, **medians never means**
(`statistics.median`, script L235-237), real pytest subprocesses against a
real HTTP server, three recording-off context samples per cell, six cells =
3 densities x 2 `--tb` flags. The A column (recording on, capture opted out)
isolates the failure-capture cost from the recording cost.

The transcription is faithful. I re-derived every cell of the table at
`specs/failure-evidence/spec.md:151-158` from its own OFF/A/B columns:

| Density | `--tb` | (B-OFF)/OFF stated | recomputed | per-failed-test stated | recomputed |
|---|---|---|---|---|---|
| 1% | auto | 3.45% | 3.454% | 31.72 ms | 31.7 ms |
| 1% | no | 3.71% | 3.713% | 37.10 ms | 37.1 ms |
| 10% | auto | 24.43% | 24.43% | 34.60 ms | 34.60 ms |
| 10% | no | 31.52% | 31.51% | 35.03 ms | 35.03 ms |
| 100% | auto | 80.23% | 80.23% | 48.37 ms | 48.37 ms |
| 100% | no | 428.94% | 428.9% | 48.62 ms | 48.62 ms |

Every cell reproduces. The figures also match observation #109 and
apply-progress #100 exactly — no drift between the three records.

**The breach is robust to the choice of denominator.** The spec table reports
B-vs-OFF. The script's own printed column is B-vs-A (L267-268). Under
B-vs-A the 1% cells are 2.80% (auto) and 3.37% (no) — still over 2%. There is
no denominator under which any measured cell fits the budget.

Two forecast assumptions in design.md D79 are falsified and correctly
recorded as such: (1) the ~55 ms headroom was already spent by
`version-control-context`, so a per-change budget measured against a fresh
baseline double-spends; (2) per-failed-test cost is not constant — it rises
with density (31.7 -> 34.6 -> 48.4 ms), so D79's `~55 ms / N` model is wrong
in shape, not only in magnitude.

**Archiving requires an explicit accepted exception, and there is nowhere to
record one.** RQ-25 has **no migrated requirement record anywhere in
`openspec/specs/`** — it survives only as citations from
`version-control-context/spec.md:125,149,155,157`,
`result-capture/spec.md:17` and `session-liveness/spec.md:43,55`. It is one of
the 27 Notion requirements never migrated, and `docs/legacy/` is frozen and
authoritative of nothing. So the authoritative corpus contains an active,
cited 2% budget with no requirement text and no place to attach an accepted
exception. OQ-11 (`docs/open-questions.md:116-149`) records the breach
honestly and explicitly leaves the decision open — that is correct
documentation, but an open question is not an accepted exception.

### Issues Found

**BLOCKER**

1. **RQ-25's 2% overhead budget is breached at every measured density, with no
   accepted exception recorded.** Evidence:
   `openspec/changes/failure-capture/specs/failure-evidence/spec.md:151-158`
   (measured table), `docs/open-questions.md:131-145` (deferred to OQ-11).
   Ten failing tests in a thousand already cost 3.45%. Archive would record an
   unresolved requirement breach as complete. Resolving it is a product
   decision (cap / off-by-default / accepted exception), not an apply-phase
   fix. Compounding: RQ-25 has no requirement record in `openspec/specs/`
   against which an exception could be written.

**CRITICAL**

2. **The D75 truncation-flag disjunction is defended at only 1 of 7 fields —
   and it is the wrong one.** Tamper-verified this phase. Replacing
   `bool(item.<field>_truncated) or <field>_cut` with the naive server-only
   assignment `<field>_cut` in
   `packages/vantage/src/vantage/service/routes/runs.py`:

   | Field | Line | Naive assignment caught? |
   |---|---|---|
   | `failure_message_truncated` | runs.py:165 | YES — 1 failed |
   | `failure_repr_truncated` | runs.py:167 | **NO — 356 passed** |
   | `traceback_truncated` | runs.py:169 | **NO — 356 passed** |
   | `skip_reason_truncated` | runs.py:171 | **NO — 356 passed** |
   | `xfail_reason_truncated` | runs.py:173 | **NO — 356 passed** |
   | `stdout_truncated` | runs.py:221 | **NO — 356 passed** |
   | `stderr_truncated` | runs.py:223 | **NO — 356 passed** |

   The one protected field is the one where the bug matters least.
   `budget.py:47-53` sets priority order `(failure_message, failure_repr,
   traceback, captured_stdout, captured_stderr)` — `failure_message` is
   dropped **last**, so the fields the budget most often drops
   (`traceback`, `captured_stdout`, `captured_stderr`) are exactly three of
   the six unprotected ones.

   The behaviour is correct today; the invariant is undefended against
   regression. Task 6.5's test (`test_routes_runs.py:169-181`) uses
   `failure_message` only. Task 6.6
   (`test_to_result_disjunction_other_direction_server_flag_still_wins`,
   `test_routes_runs.py:184-192`) does use `traceback`, but sends
   `traceback_truncated=False` — it exercises only the server half of the OR,
   which is why it survives the tamper. This is the exact failure mode the
   *A field dropped for budget is flagged, not missing* scenario exists to
   prevent, at the exact fields it most concerns. Fixable by `sdd-apply`:
   six parametrised cases over the existing test.

**WARNING**

3. **Three design.md sites still carry the errors Phase 9 corrected
   elsewhere.** The four corrections themselves are accurate — I re-probed
   each — but were applied incompletely:
   - `design.md:847` still reads `str(item.repr_failure(excinfo,"long"))` in
     the D69 architecture diagram. Corrected at `design.md:140,150` and
     `docs/adr/0016-...md:62`. Verified against installed pytest 9.1.1:
     `Function.repr_failure(self, excinfo)` takes no `style`;
     `_repr_failure_py(self, excinfo, style=None)` does.
   - `design.md:906` still reads "27-column insert, 29-column select".
   - `design.md:988` still reads "27-column insert".
     Corrected at `design.md:751,766`. Re-probed: `FailureEvidence` has 13
     fields, `CapturedOutput` 4, total 17 new; `_INSERT_RESULT` has 31 columns
     and 31 placeholders. The 31/33 correction is right; two sites missed it.

4. **`tasks.md:179` (task 5.3) still specifies `ensure_ascii=False`** — the
   exact D74 claim commit `cf008f7` corrected. The test's own docstring
   (`test_report_budget.py:48,56`) repeats it, and its value `'a"b\nc'` is
   ASCII-only, so that test cannot distinguish the two encodings.
   **Not a coverage gap**: I tampered `budget.py:73` back to
   `ensure_ascii=False` and
   `test_report_budget.py::test_the_budget_charges_exactly_what_transport_will_put_on_the_wire`
   failed. The fix is genuinely defended; only the documentation drifted.

5. **The interface document does not declare the 17 new ingestion fields.**
   `openapi/v1.yaml:123` types `SessionReport.results` items as
   `{type: object}` — untyped. This change added 17 optional
   failure-evidence fields to `ResultReport` without documenting any of them.
   Pre-existing shape, widened by this change. The read-side is correctly
   declared (`v1.yaml:69-79,188-203`: new `/runs/{run_id}/result` path,
   `FailureProjection`, `ResultListItem`, `ResultDetailResponse`, all `$ref`ed).
   Carried `read-api` WARNING; not newly violated, but the undocumented
   surface grew.

6. **`request.app.state.store` is `Any` at all six route call sites**
   (observation #107) — untouched by this change. **Assessed blast radius:
   nothing in this change is wrong because of it; it is unprotected, not
   broken.** The mitigation is genuinely in place and stronger than in
   `read-api`: `test_routes_read.py:115` parametrises the `store` fixture
   over `["memory", "sqlite"]`, so every route-level value assertion runs
   against `SqliteExecutionStore`'s real row mappers. Tamper-confirmed —
   blanking the detail route's traceback failed
   `test_result_detail_route_returns_full_record` and
   `test_result_detail_truncation_flag_travels_with_the_field` in **both**
   `[memory]` and `[sqlite]` parametrisations. `vantage_port_contract.py:184`
   is inherited by `TestInMemoryExecutionStore` and
   `TestSqliteExecutionStore`, and covers `get_result` hit/miss, the
   truncation flag, and `""`-vs-`None` on both. **The `read-api` bug cannot
   recur in this shape.**

7. **A third slice is over the review budget — the brief names only two.**
   Independently measured against each slice's own predecessor:
   `ft/failure-capture-06-ingestion` is **429 changed lines (410+19), 7.25%
   over**, not the 399 that slice reported. This is observation #106's
   finding (measured against the code commit alone, excluding the `tasks.md`
   commit), and it did not reach the verify brief. Full set:
   #65 = 429 (7.25% over), #66 = 527 (31.75% over), #70 = 409 (2.25% over).
   The other twelve slices are within budget.
   **Both stated justifications hold.** #66: task 7.3's test requires failure
   data actually persisted to prove the `list_results` projection, so the
   write-path widening cannot be deferred from that half — a real dependency,
   not convenience. #70: 2.25% is de minimis over four files that are one
   measurement bucket.

8. **apply-progress reports "61/61 tasks" while `tasks.md` carries 109.**
   Observation #100's own header says "ALL COMPLETE — 61/61 tasks" and
   "all 61 tasks across 9 phases are [x]". The file has 109 `[x]` and 0
   `[ ]`, independently counted. Stale internal figure in the artifact, not a
   completeness problem — task completion is genuinely 109/109.

9. **Whole-change size ran 42% over forecast.** `tasks.md:27` forecast ~3,160
   changed lines across nine slices; measured `git diff --shortstat` from
   tracker base `51996b4` to `ft/failure-capture-09b-docs` is **44 files,
   4,225 insertions, 262 deletions = 4,487 changed lines** across fifteen
   slices, not nine. Six of the nine planned slices self-split. Rooted in
   D80's column undercount (observation #108).

**SUGGESTION**

10. `packages/vantage/src/vantage/core/domain/liveness.py:4` still says "A
    pure function with no caller yet" — carried `read-api` stale docstring,
    untouched here. The second one named in that round appears already
    resolved; only this one remains.
11. `test_report_budget.py:48,56` should use a non-ASCII value so the test's
    stated `ensure_ascii` contract is the one it actually pins (see WARNING 4).
12. `vantage_port_contract.py::test_list_results_projects_failure_evidence_via_failure_projection`
    is an *agreement* test between `list_results` and `project_failure`.
    Tampering `LIST_FAILURE_MESSAGE_CHARS` 200 -> 100000 failed only the
    domain unit test, not the contract test — both sides move together by
    design. Correct architecture (`sqlite_store.py:978-979` binds the shared
    constant into the SQL rather than hardcoding 200), and
    `test_projection.py::test_project_failure_bounds_message_to_200_chars_and_flags`
    pins the constant. Worth naming so the agreement test is not mistaken for
    a bound check.

### Correctness (Static Evidence)
| Requirement | Status | Notes |
|---|---|---|
| `schema.sql` byte-unchanged across the whole change | VERIFIED | `git diff 51996b4 HEAD -- .../schema.sql` returns empty. Claim independently confirmed. |
| `meta.schema_version` stays `'2'` | VERIFIED | No schema statement in any slice. |
| RQ-24 — `pytest-vantage` third-party-free | VERIFIED | `pyproject.toml:17` declares `["pytest>=8.0"]` only. No `vantage` import in `src/pytest_vantage/`; the `MAX_REPORT_BYTES` cross-package import is test-only (`budget.py:18` docstring, honoured). `deptry` clean. |
| RQ-26 — core imports nothing | VERIFIED | Architecture test passing in the 534. |
| RQ-2 — opt-in is differential | VERIFIED | `test_opt_in.py:86-118` runs the suite with and without the ini file and compares `_tree_snapshot` dicts, excluding only `pytest.ini`. Correct differential form; the unsatisfiable absolute form is not used. |
| RQ-12 — xdist worker registration | VERIFIED | Tamper-confirmed: removing the worker-branch registration in `plugin.py:216-219` failed `test_xdist_guard.py::test_worker_registers_exactly_one_evidencecollector_when_activated` **and** the real `-n 2` end-to-end `test_evidence.py::test_report_vantage_evidence_attribute_survives_the_xdist_wire`. Genuinely exercised, not merely present. |
| No domain class starts with `Test` | VERIFIED | New types are `FailureEvidence`, `CapturedOutput`, `FailureProjection`, `EvidenceCollector`, `ResultListEntry`. |
| No new `RQ-xx` minted | VERIFIED | `tasks.md:6-11` states it; no new `req` marker in the diff. |

### Coherence (Design)
| Decision | Followed? | Notes |
|---|---|---|
| D68 worker-side collection | YES | Tamper-confirmed both levels. |
| D69 rendering via `_repr_failure_py` | YES | Implemented at `evidence.py:113`; design corrected at two of three sites (WARNING 3). |
| D70 non-exception branch table | YES | Covered by 3.6-3.8. |
| D71 empty-vs-absent | YES | Tamper-confirmed at ingestion (`runs.py`) and at the SQLite row mapper. |
| D72 opt-out monotonicity, no env surface | YES | `test_config.py` property over all eight combinations. |
| D73/D74 budget, encoded-byte cost | YES | Tamper-confirmed; `ensure_ascii` fix genuinely defended (WARNING 4). |
| D75 flag disjunction | PARTIAL | Implemented correctly at all 7 fields; defended at 1 (CRITICAL 2). |
| D76 lean projection | YES | Structural exclusion; shared constant in SQL. |
| D77 `failure` nullable / `captured` never null | YES | Tamper-confirmed. |
| D78 single-result route | YES | Tamper-confirmed on both adapters. |
| D79 overhead forecast | FALSIFIED, CORRECTLY RECORDED | See RQ-25 section. |
| D80 column arithmetic / body bound | CORRECTED | 31/33 and 794,291 verified; two stale sites remain (WARNING 3). |
| D81 ADR status | YES | ADR-0016 correctly left `Proposed`; flip belongs at merge. |

### TDD Compliance
| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | PARTIAL | apply-progress #100 narrates RED/GREEN per phase but has no formal "TDD Cycle Evidence" table. Per-phase RED/GREEN pairing is explicit in `tasks.md` for all 109 tasks. |
| All tasks have tests | PASS | Every GREEN task is preceded by its RED task in `tasks.md`. |
| RED confirmed (test files exist) | PASS | Every named test file exists and is collected. |
| GREEN confirmed (tests pass) | PASS | 534/534 pass on independent execution. |
| Triangulation adequate | PARTIAL | Strong for the budget (5 cases), the D70 branch table (3), and phase selection (9 rows). **Inadequate for D75**: one field of seven (CRITICAL 2). |
| Safety Net for modified files | PASS | Each phase gate re-ran the growing full suite (478 -> 491 -> 504 -> 534). |

**TDD Compliance**: 4/6 checks fully passed, 2 partial.

### Test Layer Distribution
| Layer | Tests | Files | Tools |
|-------|-------|-------|-------|
| Unit (domain, budget, projection, config doubles) | ~120 | 8 | pytest |
| Integration (ASGI `TestClient`, both storage adapters, port contract) | ~330 | 9 | pytest + FastAPI `TestClient` |
| Subprocess/E2E (`pytester`, `runpytest_subprocess`, real `-n 2` xdist, live HTTP server) | ~84 | 6 | `pytester`, `vantage_test_server` |
| **Total** | **534** | **23** | |

No browser E2E layer exists or is needed — this is a plugin plus an HTTP
service. The xdist and live-server layers are the real end-to-end proof.

### Assertion Quality
No tautologies, no ghost loops, no assertions that never call production
code, no smoke-test-only tests, no mock-heavy files found in the change's
test files. Empty-collection assertions
(`test_list_results_empty_for_a_run_with_no_results`) have companion
non-empty tests in the same contract class. Type-only assertions are paired
with value assertions.

**Assertion quality**: 0 CRITICAL, 0 WARNING at the assertion level. The one
coverage defect is a missing-case problem (CRITICAL 2), not a trivial-
assertion problem.

### Quality Metrics
**Linter**: PASS — `ruff format --check` and `ruff check` both clean, 81 files.
**Type Checker**: PASS — `mypy .` clean, 81 source files. Note WARNING 6: the
route/store seam is `Any`, so mypy's cleanliness does not extend to the six
`store.*` call sites.
**Dependency check**: PASS — `deptry` clean, 80 files.

### Tamper Log

Every tamper was reverted and the tree re-verified byte-clean after each.

| # | Tamper | Result |
|---|---|---|
| 1-7 | D75 disjunction -> naive server assignment, one field at a time | 1 caught (`failure_message`), **6 not caught** — CRITICAL 2 |
| 8 | xdist worker branch never registers `EvidenceCollector` | 2 failed (unit double + real `-n 2`) — RQ-12 defended |
| 9 | `_to_captured_output` collapses `""` -> `None` | 2 failed — empty-vs-absent defended at ingestion |
| 10 | SQLite `_row_to_captured_output` collapses `""` -> `None` | 1 failed (`[sqlite]` contract) — defended at storage |
| 11 | `memory.list_results` returns `failure=None` | 2 failed, incl. `test_routes_read.py::...[memory]` — projection defended through the route |
| 12 | `LIST_FAILURE_MESSAGE_CHARS` 200 -> 100000 | 1 failed (domain unit test) — see SUGGESTION 12 |
| 13 | budget drops field without setting `*_truncated` | **5 failed** — scenario 11 strongly defended |
| 14 | revert `cf008f7`: charge `ensure_ascii=False` | 1 failed (`test_the_budget_charges_exactly_what_transport_will_put_on_the_wire`) — fix defended |
| 15 | detail route returns `traceback=None` | 4 failed — **both** `[memory]` and `[sqlite]` — read-api bug shape cannot recur |

### Working Tree

**Confirmed clean.** `git status --porcelain` empty, `git diff --quiet` and
`git diff --cached --quiet` both succeed after all fifteen tampers.
HEAD = `ft/failure-capture-09b-docs`. Nothing merged, pushed, or opened.

### Verdict

**FAIL** — not archive-ready. The implementation is sound and every delta-spec
scenario is discharged with passing runtime evidence, but the change carries a
measured, unresolved RQ-25 breach with no accepted exception recordable
anywhere in `openspec/specs/`, and one regression-exposure gap where the D75
disjunction is defended at one of seven fields — and not the three the budget
most often drops.
