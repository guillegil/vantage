```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:19ba6ac6aa17c436ed5ae058b4f0f9971071f77da171859024beb990ce4ceb5e
verdict: fail
blockers: 5
critical_findings: 5
requirements: 4/11
scenarios: 33/41
test_command: uv run --extra dev pytest
test_exit_code: 0
test_output_hash: sha256:4576d39f3b6209939c547d8f58782d24cd7c472d5822bbe1b91f4ce1449cc862
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:8797c60315242ee16057cb107ea1ec62e2678d358c86bc2be797e23d87e87578
```

## Verification Report

**Change**: session-lifecycle
**Branch / commit**: `ft/session-lifecycle-04-heartbeat` @ `1505068`
**Version**: N/A
**Mode**: Strict TDD

Every gate is green and every task is genuinely done. The change nevertheless
fails verification: five spec scenarios — including all three of the restored
criteria this change exists to restore — have no covering test. The defect is
upstream, in task decomposition, not in the apply phase.

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 61 |
| Tasks complete | 61 |
| Tasks incomplete | 0 |

Spot-checked the tasks hardest to fake: 1.10/1.11 (`_UPSERT_RUN` conflict
clause and the `SELECT 1` created-probe — both present and behaviourally
probed), 2.9/4.20 (`plugin.py:142-143` guard still the first statement of
`pytest_configure`), 4.10 (404 resolved via `get_execution`, probed live),
4.12 (`PRESENTATIONS` is a `frozenset`, no `Enum`), 4.19 (`accumulate` first,
`_last_beat_at` before the send, `time.monotonic()`). All hold.

### Build & Tests Execution

| Gate | Command | Exit | Result |
|---|---|---|---|
| Tests | `uv run --extra dev pytest` | 0 | **241 passed, 0 failed, 0 skipped, 0 warnings** in 24.57s |
| Tests (xdist) | `uv run --extra dev pytest -n auto` | 0 | **241 passed, 0 warnings** in 10.68s |
| Types | `uv run mypy .` | 0 | Success: no issues found in 58 source files |
| Lint | `uv run ruff check .` | 0 | All checks passed! |
| Deps | `uv run deptry .` | 0 | Success! No dependency issues found |

Warning count is **zero** — `rg -c -i 'warning'` over the captured pytest
output returns 0. No regression against the reported baseline.

**Coverage**: ➖ Not available — no `pytest-cov` in the dev extra;
`openspec/config.yaml` sets `coverage_threshold: 0` deliberately.

Architecture guards, both green and both non-vacuous:
`packages/vantage/tests/test_architecture.py` 4 passed (core resolves to the
standard library; `test_the_walk_is_not_vacuous` guards the walk itself),
`packages/pytest-vantage/tests/test_plugin_imports.py` 2 passed (plugin
imports resolve to stdlib or pytest only — no xdist).

Not run locally, left to CI, as task 4.22 requires be stated explicitly: the
3.10–3.13 matrix, the networking-disabled RQ-28 job, and the
clean-environment RQ-24 install check.

### Spec Compliance Matrix

41 scenarios across 11 requirements in five delta specs.

#### `run-recording` (3 requirements, 14 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| RQ-1 | First invocation against an empty database | `test_run_report.py::test_completed_session_writes_one_row_with_ordered_timestamps` | ✅ COMPLIANT |
| RQ-1 | Second invocation gets a distinct identifier | `test_run_report.py::test_second_invocation_gets_a_distinct_identifier` | ✅ COMPLIANT |
| RQ-1 | Zero-test collection still writes a row | `test_run_report.py::test_zero_test_collection_still_writes_one_row` | ✅ COMPLIANT |
| RQ-1 | Failed collection still writes a row | `test_run_report.py::test_failed_collection_still_writes_one_row` | ✅ COMPLIANT |
| RQ-1 | **A still-running session already has a run entry (RQ-1.5)** | **(none found)** | ❌ UNTESTED |
| RQ-1 | **A SIGKILL'd session's entry is present (RQ-1.6)** | **(none found)** | ❌ UNTESTED |
| RQ-31 | Completed session records both timestamps | `test_run_report.py::test_completed_session_writes_one_row_with_ordered_timestamps` | ✅ COMPLIANT |
| RQ-31 | Interrupted session leaves a null end time | `test_run_report.py::test_sigint_leaves_start_time_and_null_end_time` | ✅ COMPLIANT |
| RQ-31 | **SIGKILL'd session carries no interrupt reason (RQ-31.3)** | **(none found)** | ❌ UNTESTED |
| RQ-3 | Server killed mid-write (RQ-3.1) — declared Analysis | premises: `test_rejection.py::test_start_write_reaches_storage_in_one_commit`, `::test_finish_report_reaches_storage_in_one_commit` | ✅ COMPLIANT |
| RQ-3 | Report truncated in transit, no prior start-write (RQ-3.2) | `test_rejection.py::test_truncated_body_raw_socket` | ✅ COMPLIANT |
| RQ-3 | **Finish report truncated after an accepted start-write (RQ-3.2)** | **(none found)** | ❌ UNTESTED |
| RQ-3 | Normal report is fully present (RQ-3.3) | `test_rejection.py::test_finish_report_reaches_storage_in_one_commit` | ✅ COMPLIANT |
| RQ-3 | A reordered start-write never nulls a recorded finish | `test_rejection.py::test_reordered_start_write_never_nulls_a_recorded_finish`; `vantage_port_contract.py::test_reordered_start_after_finish_never_nulls_the_recorded_finish` | ✅ COMPLIANT |

#### `session-ingestion` (1 requirement, 6 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| RQ-42 | Missing required field (RQ-42.1) | `test_rejection.py::test_missing_field_is_422_naming_the_field` | ✅ COMPLIANT |
| RQ-42 | Invalid JSON (RQ-42.2) | `test_rejection.py::test_non_json_body_is_400` | ✅ COMPLIANT |
| RQ-42 | Body truncated midway, no prior report (RQ-42.3) | `test_rejection.py::test_truncated_body_raw_socket` | ✅ COMPLIANT |
| RQ-42 | **Finish report truncated after an accepted start-write (RQ-42.3, RQ-3.2)** | **(none found)** | ❌ UNTESTED |
| RQ-42 | One malformed result rejects the whole report | `test_rejection.py::test_one_malformed_result_among_five_hundred_rejects_the_whole_report` | ✅ COMPLIANT |
| RQ-42 | Rejection names the cause, safely (RQ-42.4) | `test_rejection.py::test_missing_field_is_422_naming_the_field`, `::test_422_response_never_echoes_input_or_pydantic_types`, `::test_duplicate_node_id_rejection_never_echoes_the_node_id_value` | ✅ COMPLIANT |

#### `recording-schema` (1 requirement, 3 scenarios) — Inspection

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| RQ-29 | Fresh database matches the column manifest | `test_schema_manifest.py::test_fresh_database_matches_the_manifest_in_both_directions`, `::test_fresh_database_matches_the_recorded_ground_truth` (10/126/14) | ✅ COMPLIANT |
| RQ-29 | Opening an existing database issues no schema-altering statement | `test_connection.py::test_reopening_an_existing_database_issues_no_ddl`, `::test_opening_a_database_with_the_current_schema_version_succeeds_and_applies_no_ddl` | ✅ COMPLIANT |
| RQ-29 | A database from an older schema version is refused, not altered | `test_connection.py::test_opening_a_database_with_an_older_schema_version_is_refused`, `::test_a_refusal_issues_no_ddl_and_closes_the_connection_before_raising` | ✅ COMPLIANT |

#### `recording-fault-tolerance` (1 requirement, 7 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| RQ-21 | Passing suite survives an internal error | `test_failure_paths.py::test_reporting_error_preserves_passing_exit_status_and_warns_once` | ✅ COMPLIANT |
| RQ-21 | Failing suite still reports failure | `test_failure_paths.py::test_reporting_error_preserves_failing_exit_status_and_warns_once` | ✅ COMPLIANT |
| RQ-21 | Server accepts then closes without responding | `test_failure_paths.py::test_server_accepts_then_closes_without_responding` | ✅ COMPLIANT |
| RQ-21 | Server accepts and never answers | `test_failure_paths.py::test_server_accepts_and_never_answers_finishes_within_timeout_plus_five_seconds` | ✅ COMPLIANT |
| RQ-21 | Every hook is fault-isolated | `test_failure_paths.py::test_every_recorder_hook_is_fault_isolated` | ✅ COMPLIANT |
| RQ-21 | A failed heartbeat does not stop result recording | `test_failure_paths.py::test_heartbeat_failing_on_every_attempt_warns_once_and_every_result_is_still_recorded` | ✅ COMPLIANT |
| RQ-21 | A failed heartbeat warns once, not once per beat | same test (5 forced beat opportunities, exactly 1 `VantageWarning:`) | ✅ COMPLIANT |

#### `session-liveness` (5 requirements, 11 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Heartbeat endpoint | A heartbeat advances last contact | `test_ingestion.py::test_heartbeat_advances_last_contact_for_an_accepted_start_write` | ✅ COMPLIANT |
| Heartbeat endpoint | A heartbeat cannot touch finish fields | `test_ingestion.py::test_heartbeat_cannot_touch_finish_fields` | ✅ COMPLIANT |
| Activity-driven tracking | A long suite's last contact advances during execution | `test_run_report.py::test_a_suite_exceeding_one_heartbeat_interval_sends_at_least_one_heartbeat` | ⚠️ PARTIAL |
| Activity-driven tracking | A fast suite emits no heartbeat | `test_run_report.py::test_a_fast_suite_emits_no_heartbeat` | ⚠️ PARTIAL |
| Single long test (documented limitation) | A single very long test can read as abandoned while alive | (none — RFC 2119 `MAY`, an accepted-behaviour statement, not a testable obligation) | ✅ COMPLIANT (documentation) |
| RQ-44 | A run past its grace period derives as abandoned (RQ-44.1) | `test_liveness.py::test_a_run_past_its_grace_period_with_no_finish_or_interrupt_derives_as_abandoned` | ✅ COMPLIANT |
| RQ-44 | A run inside its grace period derives as running (RQ-44.2) | `test_liveness.py::test_a_run_inside_its_grace_period_derives_as_running` | ✅ COMPLIANT |
| RQ-44 | A Ctrl-C interrupted run derives as interrupted (RQ-44.3) | `test_liveness.py::test_an_interrupted_run_derives_as_interrupted_before_the_clock_is_consulted` | ✅ COMPLIANT |
| RQ-44 | Abandonment invents no stored field (RQ-44.4) | `test_schema_manifest.py` (no such column) + `test_architecture.py` (core cannot reach storage) | ⚠️ PARTIAL |
| Grace period | Grace is measured from last contact, not start | `test_liveness.py::test_grace_is_measured_from_last_contact_not_from_start` | ✅ COMPLIANT |
| Grace period | Grace period is configurable | `test_resolution.py::test_cli_grace_period_overrides_the_default`; `test_ingestion.py::test_create_app_exposes_the_configured_grace_period` | ⚠️ PARTIAL |

**Compliance summary**: 33/41 scenarios compliant, 3 partial, **5 untested**.

### Correctness (verified by execution, not by reading)

Each of the following was probed against the real code on this commit, not
inferred from source inspection.

| Claim under test | Method | Result |
|---|---|---|
| `derive_presentation` checks interrupted before the clock | called the real function with `interrupted=True`, `now = start + 30 days`, `grace = 15 min` | returns `"interrupted"` — the clock is not consulted ✅ |
| Interrupted is not shadowed by the finished check | called with `interrupted=True` **and** `finished_at` set | returns `"finished"`. This is D34's specified precedence, and unreachable in practice: `recorder.py` maps exit status 2 into `_NULL_FINISH_EXIT_STATUSES`, so a Ctrl-C run always carries `finished_at is None`. Not a defect ✅ |
| Grace boundary | `now - contact == grace` → `"running"`; `+1 µs` → `"abandoned"` | strict `>`, matches "for longer than the configured grace period" ✅ |
| Heartbeat 404 comes from `get_execution`, not a zero-`rowcount` update | forced `last_contact_at` an hour into the future, then beat: `touch_last_contact` returned `False` (zero rows) while `POST /heartbeat` returned **200** | production is correct ✅ — but see WARNING W1: no test covers it |
| `_UPSERT_RUN` does not advance `last_contact_at` on the conflict path; `received_at` and `started_at` never move | direct `sqlite3` read of the `run` row after start → beat → finish | `received_at` `09:00:01`, `last_contact_at` `09:30:00.000000`, `started_at` `09:00:00` — all three unchanged by the finish ✅ — but see WARNING W2 |
| Lexicographic ordering of `last_contact_at` | both writers pass through `_fixed_width_isoformat` (insert branch at `sqlite_store.py:357`, touch at `:389`); probed both widths and the mixed-width comparison | 32 chars in every case. The one mixed-width comparison that could invert (`'…09:00:00.123456+00:00' < '…09:00:00+00:00'` → `False`) rejects the earlier timestamp correctly. **No ordering defect found.** The pre-existing variable-width hazard on `test_case.last_seen_at` is recorded as an out-of-scope open question in `design.md:600-602` ✅ |

### Coherence (Design D25–D37)

| Decision | Followed? | Notes |
|---|---|---|
| D25 `_UPSERT_RUN` conflict clause | ✅ Yes | `DO UPDATE SET` names only the four finish fields, guarded by `run.exit_status IS NULL AND excluded.exit_status IS NOT NULL` |
| D26 created-detection by `SELECT 1` probe | ✅ Yes | `_PROBE_RUN_EXISTS`, inside the same `BEGIN IMMEDIATE`; never `rowcount`, never `last_insert_rowid()` |
| D27 `last_contact_at` written on insert only, fixed width | ✅ Yes | verified by direct SQL probe |
| D28 schema-version superset refusal | ✅ Yes | absent / `<2` / `>2` all refused, `==2` opens; refusal issues no DDL |
| D29 two independent isolation latches | ✅ Yes | `test_liveness_isolated_and_fault_isolated_flags_never_read_or_set_each_other` |
| D30 accumulate first, `_last_beat_at` before the send, `time.monotonic()` | ✅ Yes | `recorder.py:151-172` |
| D31 distinct `send_heartbeat`, `_MAX_SHORT_TIMEOUT = 2.0` | ✅ Yes | `test_start_write_uses_the_liveness_timeout_not_the_report_timeout` asserts `[2.0, 5.0]` |
| D32 one `_started_at`, shared by both writes | ✅ Yes | `test_session_start_sends_a_report_with_no_results_matching_the_finish_writes_started_at` |
| D33 monotonic touch, 404 via `get_execution` | ✅ Yes (probed) | see Correctness table |
| D34 `PRESENTATIONS` a `frozenset`, never an `Enum` | ✅ Yes | `test_derive_presentation_returns_a_plain_str_never_an_enum` |
| D35 per-report commit-count premise tests | ✅ Yes | start-write and finish-write both counted, with applied-field assertions |
| D36 controller-only xdist guard precedes every new hook | ✅ Yes | `plugin.py:142-143`; `test_xdist_guard.py` asserts `pluginmanager.register` is never called |
| D37 refuse rather than migrate, recorded in an ADR | ✅ Yes | `docs/adr/0013-refuse-databases-from-an-older-schema-version.md` |
| **Test-layer plan, `design.md:556` (Integration row)** | ❌ **No** | "truncated finish after an accepted start-write leaves the start row exactly as written (RQ-42.3, RQ-3.2)" — never implemented |
| **Test-layer plan, `design.md:557` (E2E row)** | ❌ **No** | "a killed session leaves start time, null end, no interrupt reason (RQ-31.3, RQ-1.6); a running session already has a row (RQ-1.5)" — never implemented |

### TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | ✅ | Full cycle table in `sdd/session-lifecycle/apply-progress` (Engram #71) |
| All tasks have tests | ✅ | Every behavioural task names a test file that exists |
| RED confirmed (tests exist) | ✅ | Every named test file present; RED failure modes recorded per task |
| GREEN confirmed (tests pass) | ✅ | 241/241 on re-execution at this commit |
| Triangulation adequate | ⚠️ | Adequate throughout except the two flagged below (W3, W4) |
| Safety Net for modified files | ✅ | Baselines recorded per phase (220 → 222 → 226 → 237 → 241) |

**TDD Compliance**: 5/6 checks passed.

### Test Layer Distribution

| Layer | Tests | Files | Tools |
|---|---|---|---|
| Unit | ~150 | 14 | pytest |
| Contract (both adapters) | 34 (17 × 2) | 1 | pytest parametrized fixture |
| Integration (service) | ~39 | 2 | `fastapi.testclient.TestClient` |
| E2E (subprocess + real server) | ~18 | 2 | `pytester.runpytest_subprocess`, `VantageTestServer`, raw sockets |
| **Total** | **241** | **27** | |

### Changed File Coverage

Coverage analysis skipped — no coverage tool detected (`pytest-cov` is absent
from the dev extra and the lockfile by deliberate project decision).

### Assertion Quality

| File | Test | Issue | Severity |
|---|---|---|---|
| `packages/pytest-vantage/tests/test_run_report.py` | `test_a_fast_suite_emits_no_heartbeat` | Orphan empty-collection assertion (`beats == []`) inside a code path (`_maybe_beat`, `@liveness_isolated`) that swallows exceptions. Proven vacuous-pass: deleting `_last_beat_at` makes 1,000 calls raise and be swallowed, and the sole assertion still holds. Its companion non-empty case lives in a different harness (`pytester`), so it is not a control for this setup. | WARNING |
| `packages/pytest-vantage/tests/test_failure_paths.py` | `test_every_recorder_hook_is_fault_isolated` | Checks only for `__wrapped__`, which `liveness_isolated` also sets — cannot distinguish the two decorators. The docstring is honest about the check's scope. | SUGGESTION |

**Assertion quality**: 0 CRITICAL, 1 WARNING, 1 SUGGESTION. No tautologies,
no ghost loops, no assertion without a production call, no mock-heavy tests.
The specific defect class from the previous round — a warning asserted on
inner output while escaping to the outer suite — was correctly avoided:
both new heartbeat failure tests use `runpytest_subprocess` with a
`conftest.py`-injected patch, and their docstrings name that exact reason.

### Issues Found

**CRITICAL** (5 — all of the same kind: a spec scenario with no covering test)

- **C1 — RQ-1.5 has no test.** `run-recording`: "A still-running session
  already has a run entry." No test queries storage while a session is still
  alive. `test_session_start_sends_a_report_with_no_results_…` patches
  `send`, so nothing reaches storage; `test_start_write_reaches_storage_in_one_commit`
  drives the adapter directly with no live session. The two halves exist; the
  scenario is the composition of them, and nothing composes them.
- **C2 — RQ-1.6 has no test.** "A SIGKILL'd session's entry is present with a
  start time and a null end time." Evidence:
  `rg -ni 'sigkill|\.kill\(|signal\.SIGKILL|terminate\('` over `packages/`
  and `tests/` returns **no match**. The only signal test in the tree is
  `test_sigint_leaves_start_time_and_null_end_time`, which is SIGINT
  (RQ-31.2) — a signal Python *can* observe, and therefore the opposite of
  the case RQ-1.6 specifies.
- **C3 — RQ-31.3 has no test.** "A SIGKILL'd session carries **no interrupt
  reason**." This is the criterion that asserts an absence, and it is the one
  the brief singled out as easy to write and easy to not test. It was written
  and not tested. Same evidence as C2.
- **C4 — `run-recording` RQ-3.2 finish-truncated-after-start has no test.**
  `test_truncated_body_raw_socket` asserts `store.count_executions() == 0`,
  i.e. only the no-prior-report case. Nothing exercises a truncated finish
  arriving after an accepted start-write, and nothing asserts the start row
  survives it intact.
- **C5 — `session-ingestion` RQ-42.3 finish-truncated-after-start has no
  test.** Same behaviour, separately specified. This is the scenario the
  entire `session-ingestion` delta was written to introduce: RQ-42.3's old
  wording ("the run table stays empty") was corrected precisely because a
  start-write can legitimately leave a row behind. The correction shipped in
  the spec and never reached a test.

**Root cause, stated precisely.** This is not an apply-phase failure. All 61
tasks are genuinely complete and faithfully executed. `tasks.md` contains no
task for either behaviour — `rg -ni 'truncat|killed|kill'` over
`openspec/changes/session-lifecycle/tasks.md` returns **no match** — even
though `design.md:556` and `design.md:557` both name them as required test
rows. The gap was introduced by `sdd-tasks`, which decomposed the design's
implementation decisions (D25–D37) but not its test-layer plan, and no later
phase re-derived coverage from the specs.

**WARNING**

- **W1 — the heartbeat's 200-on-zero-rowcount is unverified.** `tasks.md` 4.10
  singles out "an out-of-order beat on a known run is `200`, not `404`". The
  production code is correct (probed: `touch_last_contact` returned `False`
  and the endpoint still returned `200`), but **all four heartbeat tests pass
  unchanged against a `rowcount`-based implementation**: in
  `test_heartbeat_advances_last_contact_…` and
  `test_heartbeat_cannot_touch_finish_fields` the incoming beat is strictly
  later than the stored contact, so `rowcount` is 1; in
  `test_heartbeat_for_unknown_run_is_404` both implementations answer 404.
  The distinguishing case has no test.
- **W2 — `_UPSERT_RUN`'s conflict-path column stability is unverified.**
  Correct in production (probed), and `test_duplicate_start_after_start_is_a_no_op`
  proves `started_at` does not move. But `received_at` and `last_contact_at`
  are not fields on `Execution`, so no contract test can observe them, and no
  SQL-level test does. Adding `last_contact_at = excluded.last_contact_at` to
  the `DO UPDATE SET` list would leave all 241 tests green while making a
  finished run's last contact jump — exactly the "a finished run is not
  stale" invariant.
- **W3 — `test_a_fast_suite_emits_no_heartbeat` can pass for the wrong
  reason.** See Assertion Quality. It never asserts the session was
  warning-free, so a swallowed exception inside `_maybe_beat` is
  indistinguishable from a correctly suppressed beat.
- **W4 — "Grace period is configurable" is PARTIAL.** `test_liveness.py` uses
  `_GRACE = timedelta(minutes=15)` in every case, which is exactly the 900 s
  default. No test invokes `derive_presentation` with a non-default grace
  against a run whose last contact is older than the configured value but
  younger than the default — the scenario's own wording. Configurability of
  the *value* is proven at the config layer; its *effect on the derivation*
  is not.
- **W5 — "A long suite's last contact advances during execution" is
  PARTIAL.** `test_a_suite_exceeding_one_heartbeat_interval_sends_at_least_one_heartbeat`
  patches `send_heartbeat` and asserts `len(beats) >= 1`. It proves a beat was
  *sent*; the scenario says `last_contact_at` *advances*. The other half is
  proven separately at the route level. Nothing joins them end-to-end.
- **W6 — RQ-44.4 "Abandonment invents no stored field" is PARTIAL.** Satisfied
  by composition (the manifest tests prove no such column exists; the
  architecture test proves `vantage.core` cannot reach storage, so a pure
  derivation cannot persist), but no test states the scenario directly.
- **W7 — the design's test-layer plan was never decomposed into tasks.** See
  the root cause above. This is the single upstream defect behind C1–C5, and
  it should be fixed in `tasks.md` before those tests are written, so the
  same class of omission is visible next time.
- **W8 — review-budget accounting gap for Phase 4.** The native runtime
  attempt was acquired **retroactively**, after the code was written and
  committed, so the ledger recorded **0 changed lines** for that attempt. That
  0 is an artefact of the late acquisition, not a measurement. The figure the
  actor reported is **770 changed lines against a 500-line budget** —
  production ~343 (+319/−24), tests ~427 (+426/−1). Recorded here as an
  accounting gap; **this is not a passing budget check** and must not be read
  as one.
- **W9 — `proposal.md`'s Success Criteria are all still unchecked (`[ ]`)**,
  including the three at lines 239–241 that name exactly C1–C3. Nothing
  reconciled them against the delivered state.

**SUGGESTION**

- **S1 — `_fixed_width_isoformat` has no test of its own and trusts its
  caller.** `sqlite_store.py:156` hard-codes the `+00:00` suffix without
  normalizing. Probed: a `UTC+2` 11:00 datetime (= 09:00Z) formats as
  `2026-08-15T11:00:00.000000+00:00`, and a naive datetime formats as if it
  were UTC. Both call sites currently pass `datetime.now(timezone.utc)`, so
  the hazard is latent, and the docstring states the invariant — but nothing
  enforces it, unlike its twin `pytest_vantage.recorder.isoformat_utc`, which
  has two dedicated width tests. RQ-24 forbids sharing the helper across the
  two distributions, so the duplication is forced; the missing test is not.
- **S2 — `test_every_recorder_hook_is_fault_isolated` cannot tell the two
  decorators apart.** See Assertion Quality.

### Known and accepted — confirmed honestly represented

- **Single very long test produces no beat.** Present in the **spec**, not
  only the proposal: `specs/session-liveness/spec.md:57-69` gives it its own
  `Requirement:` heading ("A single long test is not observed mid-body
  (documented limitation)") plus a scenario that states the behaviour is
  "the stated, accepted behavior of this requirement, not a bug", and names
  the generous configurable grace period as the mitigation. ✅ Correctly
  represented; not a finding.
- **Write side only.** `specs/session-liveness/spec.md:6-12` states it in the
  Purpose section: "**Write side only** … presenting the derived state waits
  for a read API that does not exist yet, so RQ-44's read-back criteria are
  Analysis against the derivation helper here, not Demonstration through a
  live read path." ✅ The spec claims Analysis, not Demonstration. Correctly
  represented; not a finding.
- **One liveness latch covers both the start-write and the beat.**
  Deliberate, specified in `recording-fault-tolerance`, implemented as the
  shared `_liveness_disabled` flag, and tested by
  `test_start_write_and_heartbeat_failure_share_one_flag_leaving_two_warnings_total`
  (two warnings, not three) together with
  `test_heartbeat_failing_on_every_attempt_warns_once_and_every_result_is_still_recorded`
  (`len(vantage_server.results()) == 5` — results still record). ✅ Correctly
  represented and genuinely tested.

### Verdict

**FAIL** — every gate is green (241 passed, zero warnings, mypy/ruff/deptry
clean) and all 61 tasks are genuinely complete, but five spec scenarios have
no covering test, including all three of the restored criteria (RQ-1.5,
RQ-1.6, RQ-31.3) that motivated this change. Not archive-ready: return to
`sdd-tasks` to decompose `design.md:556-557`, then to `sdd-apply` to write
the missing tests.
