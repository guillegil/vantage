```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:532b7299d8504ec0fe31670feccb8f44479e1141e65c7dfa4faa843a0a0d90b7
verdict: fail
blockers: 0
critical_findings: 0
requirements: 8/11
scenarios: 38/41
test_command: uv run --extra dev pytest
test_exit_code: 0
test_output_hash: sha256:5b1ac0b5c8a28206cde90331c2f15d6e3d08f452cb64b854d76a106abf5d4289
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:8797c60315242ee16057cb107ea1ec62e2678d358c86bc2be797e23d87e87578
```

## Verification Report

**Change**: session-lifecycle — **ROUND THREE**
**Branch**: `ft/session-lifecycle-06-heartbeat-wire` @ `5fb8cd3` (tip of the six-slice chain)
**Version**: N/A
**Mode**: Strict TDD

Supersedes round two (`683a177`, verdict `fail`, 1 CRITICAL) and round one
(`1505068`, verdict `fail`, 5 CRITICAL).

**Not yet archive-ready — but nothing defective blocks it.** Round two's single
CRITICAL is genuinely closed, and I proved the fix precise by reproducing round
two's own mutation. **Zero CRITICAL findings, zero failing gates, zero defects.**

What blocks archive is the verify contract, not the code: 3 of 41 scenarios
carry only PARTIAL evidence, and `gentle-ai sdd-verify-validate` refuses a
`pass` verdict on incomplete scenario evidence. I did not adjust the grades to
clear that bar. In all three cases I read the production path and confirmed the
shipped behaviour is correct today; what is missing is the regression guard,
and mutation proves each gap is real rather than theoretical.

**The remaining work is two tests, roughly ten lines**, specified exactly in
W7 and W4 below. That takes the count to 41/41 and a clean pass.

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 70 |
| Tasks complete | 70 |
| Tasks incomplete | 0 |

Round two's S3 (apply-progress claimed 70, `tasks.md` held 67) is resolved:
Phase 6 added tasks 6.1–6.3 and `tasks.md` now holds 70, matching
apply-progress. The bookkeeping drift was real and is closed.

### Build & Tests Execution

**Build**: PASSED

```text
uv run mypy .            -> Success: no issues found in 58 source files   (exit 0)
uv run ruff check .      -> All checks passed!                            (exit 0)
uv run ruff format --check . -> 58 files already formatted                (exit 0)
uv run deptry .          -> Success! No dependency issues found            (exit 0)
```

**Tests**: 247 passed / 0 failed / 0 skipped / **0 warnings**

```text
uv run --extra dev pytest          -> 247 passed in 25.91s, 0 warnings   (exit 0)
uv run --extra dev pytest -n auto  -> 247 passed in 10.93s               (exit 0)
test_architecture.py + test_plugin_imports.py -> 6 passed                (exit 0)
```

247 passed against round two's 246 — exactly the one test Phase 6 added.
Warning count is genuinely zero: the summary line carries no warnings section
and a search of the full output for `warning` matches nothing.

Left to CI, not run here: the 3.10–3.13 x xdist matrix, the RQ-28
networking-disabled job, and the RQ-24 clean-environment install check.

**Coverage**: not available — no `pytest-cov` in the dev extra or the
lockfile, and `openspec/config.yaml` sets `coverage_threshold: 0` by project
decision (CLAUDE.md, "Coverage is not measured"). Not a failure.

### No production file moved in Phases 5 and 6 — CONFIRMED

`git diff --name-status ft/session-lifecycle-04-heartbeat..HEAD` returns seven
paths and **zero `src/` paths**:

```text
M  openspec/changes/session-lifecycle/proposal.md
M  openspec/changes/session-lifecycle/tasks.md
A  openspec/changes/session-lifecycle/verify-report.md
M  packages/pytest-vantage/tests/test_run_report.py
M  packages/vantage/tests/test_ingestion.py
M  packages/vantage/tests/test_rejection.py
M  packages/vantage/tests/test_sqlite_store.py
```

Both remediation phases closed coverage gaps without touching shipped code.
That is the correct shape for a phase whose finding was "this is untested",
not "this is wrong", and it means neither phase could have introduced a
regression.

### Round two's CRITICAL — closed, and the fix is precise

Round two's C6 was found by mutation: replacing `_HEARTBEAT_PATH_SUFFIX` with
`/HEARTBEAT-TYPO` left **246 passed, nothing failed** — `send_heartbeat` was
never invoked against a real server anywhere in the suite.

I re-ran that exact mutation (M9). Result: **1 failed, 246 passed**, and the
one failure is
`test_a_suite_exceeding_one_heartbeat_interval_advances_the_servers_last_contact`
— the test Phase 6 added, and nothing else. The guard is genuine and it is
scoped: it catches the wire breaking without any collateral failure noise.

The test is well built. `send_heartbeat` is left unpatched, only
`_BEAT_INTERVAL_SECONDS` is driven down (the production default 30.0 is never
touched), and the server's `touch_last_contact` is *wrapped*, not replaced, so
the assertion compares two real reads of store state rather than a stub's
argument list. If the wire breaks, the wrapper is never called and
`baseline_by_run` stays empty, which the test asserts against explicitly with
a named message.

### The two carried PARTIALs — resolved, not carried a third time

#### W4 — "Grace period is configurable"

Round two's stated reason was that `test_liveness.py`'s
`_GRACE = timedelta(minutes=15)` equals the 900 s production default, so the
discriminating condition is never exercised. **That reason is answerable, but
it points at the wrong half of the scenario.**

- A non-default value *is* genuinely proven, twice, and round two missed both:
  `test_cli_grace_period_overrides_the_default` asserts `60.0` with
  `ConfigSource.CLI`, and `test_create_app_exposes_the_configured_grace_period`
  asserts `app.state.grace_period == 123.0`. Neither is the default.
- `derive_presentation` cannot prefer a default over a configured value,
  because it *has* no default: `grace: timedelta` is a required keyword-only
  parameter. That half is satisfied by construction, soundly.
- What is genuinely unproven is the seam between those two tested halves —
  `cli.py:107`, `create_app(store, grace_period_seconds=config.grace_period_seconds)`.
  **Mutation M1** dropped that keyword argument, so a server started with
  `--grace-period 60` would silently run at 900. Result: **247 passed, nothing
  failed.**

This is the same three-link shape as round two's CRITICAL — links 1 and 3
tested, link 2 unproven — one layer up. It stays **PARTIAL**, now for a
better-evidenced reason. It does **not** block: `app.state.grace_period` has
no production reader (the design names it a seam for a read API this
write-side change does not add), so a broken wire has no observable effect
today. It would become a real defect the moment the read API lands.

**Verdict on W4: a gap worth one test, not a scenario satisfied by
construction.** One test asserting `main()` propagates `--grace-period` into
`app.state.grace_period` closes it.

#### W6 — RQ-44.4 "Abandonment invents no stored field"

Construction is **most of the way** sufficient here, but not all of it, and
the manifest test does not cover the part that is missing.

Sufficient by construction: `derive_presentation` is a pure function returning
a `str` that is never persisted, so no derived value can reach a column, and
`test_schema_manifest.py` independently pins the column set so a new column
cannot appear unnoticed.

Not covered: the scenario's other clause — "the start time it was recorded
with is unchanged, and no column represents an end that never happened". See
W7 below; **mutation M7** shows the SQLite heartbeat path can be made to
invent a `finished_at` with the whole suite still green. So the "no column
represents an end that never happened" clause has no runtime guard against the
shipped adapter.

**Verdict on W6: construction covers the "invents no field" half; the
"unchanged start / no invented end" half needs the assertion described in W7.
It stays PARTIAL and does not block.**

### Round three's finding: a double silencing the real adapter (W7)

This round's mutation hunt found one thing 247 green tests hide, and it is the
same failure mode as round two's CRITICAL, one layer down.

**`session-liveness`'s "A heartbeat cannot touch finish fields" is proven only
against `InMemoryExecutionStore`, never against the adapter that ships.**

`test_heartbeat_cannot_touch_finish_fields` (`test_ingestion.py:345`) is a
good test, but its `client`/`store` fixtures wire the app to
`InMemoryExecutionStore`. The production adapter is `SqliteExecutionStore`, and
its heartbeat path is a hand-written `UPDATE` statement — `_TOUCH_LAST_CONTACT`
— that no test guards for this property.

**Mutation M7**: rewrote `_TOUCH_LAST_CONTACT` to
`SET last_contact_at = ?, finished_at = '2099-01-01T00:00:00+00:00'` — a
heartbeat that fabricates a finish for a still-running run, violating both the
heartbeat-endpoint requirement and RQ-44.4. Result: **247 passed, nothing
failed.**

**Mutation M10** (supporting): in `memory.py`'s finish branch, replaced
`started_at=stored.started_at` with `started_at=execution.started_at`, so a
later report could move a run's recorded start. Result: **247 passed.** This
one is partly an equivalent mutant — no fixture ever sends a finish whose
`started_at` differs from the start-write's — which is itself the point:
RQ-44.4's "start time is unchanged" clause is never exercised against a
divergent value.

The shipped behaviour is correct. I read `_TOUCH_LAST_CONTACT`: it sets
exactly one column, and `_UPSERT_RUN`'s `DO UPDATE SET` list omits `started_at`
entirely. Nothing is broken. What is missing is the regression guard.

**Why this is a WARNING and not a CRITICAL.** The scenario has a covering test
that exercises the behaviour and passes at runtime, so the skill's CRITICAL
gate ("no passing covering test") is not met. Round two's C6 was categorically
different: `send_heartbeat` was invoked against a real server *nowhere in the
suite*, so the behaviour had zero runtime evidence. Here the behaviour has
runtime evidence; it is the shipped implementation of it that is unguarded. I
am not inflating this to CRITICAL to make the round look productive.

**The fix is one assertion, and it covers both adapters at once.** The shared
port-contract suite `vantage_port_contract.py` already runs against both
stores (`TestInMemoryExecutionStore` and `TestSqliteExecutionStore`), and it
already has the right test —
`test_touch_last_contact_is_monotonic_and_reports_unknown_runs` at line 399 —
which asserts only the returned boolean and never re-reads `get_execution`.
Adding a `get_execution` read-back asserting `started_at`, `finished_at`,
`exit_status`, `interrupted` and `interrupt_reason` survive a touch would kill
M7 and M10 together, close W6's open half, and cost roughly five lines.

### Mutation log — 10 mutations, every one reverted

Each mutation was applied to production source, the full suite run, then the
file restored with `git checkout --`. Tree state was re-proven clean after
every batch.

| # | Mutation | Result | Reading |
|---|----------|--------|---------|
| M1 | `cli.py` drops `grace_period_seconds=` from `create_app` | **247 passed** | GAP — W4 |
| M2 | `_maybe_beat` assigns `_last_beat_at` *after* the send | **247 passed** | GAP — S4 |
| M3 | `_maybe_beat` decorated `fault_isolated` not `liveness_isolated` | 1 failed | genuine guard |
| M4 | `memory.py` `touch_last_contact` monotonic guard removed | 2 failed | genuine guard |
| M5 | beat uses `_timeout` not `_liveness_timeout` | **247 passed** | GAP — W9 |
| M6 | start-write sends `exit_status: 0` instead of `None` | 2 failed | genuine guard |
| M7 | `_TOUCH_LAST_CONTACT` also writes `finished_at` | **247 passed** | GAP — W7 |
| M8 | `_UPSERT_RUN` `DO UPDATE` loses its `WHERE` guard | 3 failed | genuine guard |
| M9 | `_HEARTBEAT_PATH_SUFFIX` -> `/HEARTBEAT-TYPO` (round two's D) | 1 failed | **round two's CRITICAL closed** |
| M10 | `memory.py` finish branch overwrites `started_at` | **247 passed** | GAP — W7 (supporting) |

**Revert proof.** After every batch: `git status --porcelain` empty,
`git diff HEAD --stat` empty. Final state of the tree at report time: clean at
`5fb8cd3`, with only this report file written.

M3 partly answers round two's S2: the *behavioural* distinction between
`liveness_isolated` and `fault_isolated` on `_maybe_beat` **is** caught, by
`test_heartbeat_failing_on_every_attempt_warns_once_and_every_result_is_still_recorded`.
S2's narrower complaint — that
`test_every_recorder_hook_is_fault_isolated` inspects only `__wrapped__`,
which both decorators set — still stands, but the property that matters is
guarded elsewhere.

### Spec Compliance Matrix

41 scenarios across 11 requirements in 5 delta specs.

#### `run-recording` (3 requirements, 14 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Run entry per invocation (RQ-1) | First invocation against an empty database | `test_run_report.py` | COMPLIANT |
| Run entry per invocation (RQ-1) | Second invocation gets a distinct identifier | `test_run_report.py` | COMPLIANT |
| Run entry per invocation (RQ-1) | Zero-test collection still writes a row | `test_run_report.py` | COMPLIANT |
| Run entry per invocation (RQ-1) | Failed collection still writes a row | `test_run_report.py` | COMPLIANT |
| Run entry per invocation (RQ-1) | A still-running session already has a run entry (RQ-1.5) | `test_run_report.py::test_a_still_running_session_already_has_a_run_entry` | COMPLIANT |
| Run entry per invocation (RQ-1) | A SIGKILL'd session's entry is present (RQ-1.6) | `test_run_report.py::test_sigkilled_session_leaves_a_start_time_null_end_time_and_no_interrupt_reason` | COMPLIANT |
| Run timestamps (RQ-31) | Completed session records both timestamps | `test_run_report.py::test_completed_session_writes_one_row_with_ordered_timestamps` | COMPLIANT (M6) |
| Run timestamps (RQ-31) | Interrupted session leaves a null end time | `test_run_report.py::test_sigint_leaves_start_time_and_null_end_time` | COMPLIANT (M6) |
| Run timestamps (RQ-31) | SIGKILL'd session carries no interrupt reason (RQ-31.3) | `test_run_report.py::test_sigkilled_session_...no_interrupt_reason` | COMPLIANT |
| Run atomicity (RQ-3) | Server killed mid-write (RQ-3.1) | Analysis + `test_start_write_reaches_storage_in_one_commit`, `test_finish_report_reaches_storage_in_one_commit` | COMPLIANT |
| Run atomicity (RQ-3) | Report truncated in transit, no prior start-write (RQ-3.2) | `test_rejection.py` | COMPLIANT |
| Run atomicity (RQ-3) | Finish report truncated after an accepted start-write (RQ-3.2) | `test_rejection.py:752` | COMPLIANT |
| Run atomicity (RQ-3) | Normal report is fully present (RQ-3.3) | `test_ingestion.py` | COMPLIANT |
| Run atomicity (RQ-3) | A reordered start-write never nulls a recorded finish | `test_rejection.py::test_reordered_start_write_never_nulls_a_recorded_finish` + 2 store tests | COMPLIANT (M8) |

#### `session-ingestion` (1 requirement, 6 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Malformed report rejection (RQ-42) | Missing required field (RQ-42.1) | `test_rejection.py` | COMPLIANT |
| Malformed report rejection (RQ-42) | Invalid JSON (RQ-42.2) | `test_rejection.py` | COMPLIANT |
| Malformed report rejection (RQ-42) | Body truncated midway, no prior report (RQ-42.3) | `test_rejection.py` | COMPLIANT |
| Malformed report rejection (RQ-42) | Finish report truncated after an accepted start-write | `test_rejection.py:752` | COMPLIANT |
| Malformed report rejection (RQ-42) | One malformed result rejects the whole report | `test_rejection.py` | COMPLIANT |
| Malformed report rejection (RQ-42) | Rejection names the cause, safely (RQ-42.4) | `test_rejection.py` | COMPLIANT |

#### `session-liveness` (5 requirements, 11 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Heartbeat endpoint | A heartbeat advances last contact | `test_ingestion.py::test_heartbeat_advances_last_contact_for_an_accepted_start_write` | COMPLIANT |
| Heartbeat endpoint | A heartbeat cannot touch finish fields | `test_ingestion.py::test_heartbeat_cannot_touch_finish_fields` — in-memory store only | **PARTIAL (W7)** |
| Activity-driven last-contact (RQ-25.2) | A long suite's last contact advances during execution | `test_run_report.py::test_a_suite_exceeding_one_heartbeat_interval_advances_the_servers_last_contact` | **COMPLIANT (M9 — was UNTESTED in round two)** |
| Activity-driven last-contact (RQ-25.2) | A fast suite emits no heartbeat | `test_run_report.py::test_a_fast_suite_emits_no_heartbeat` | COMPLIANT |
| Single long test not observed mid-body | A single very long test can read as abandoned while alive | `test_liveness.py::test_a_run_past_its_grace_period_...` | COMPLIANT |
| Abandoned run is observable (RQ-44) | A run past its grace period derives as abandoned (RQ-44.1) | `test_liveness.py::test_a_run_past_its_grace_period_...` | COMPLIANT |
| Abandoned run is observable (RQ-44) | A run inside its grace period derives as running (RQ-44.2) | `test_liveness.py::test_a_run_inside_its_grace_period_derives_as_running` | COMPLIANT |
| Abandoned run is observable (RQ-44) | A Ctrl-C interrupted run derives as interrupted (RQ-44.3) | `test_liveness.py::test_an_interrupted_run_derives_as_interrupted_...` | COMPLIANT |
| Abandoned run is observable (RQ-44) | Abandonment invents no stored field (RQ-44.4) | construction + `test_schema_manifest.py`; no runtime guard on the "unchanged start / no invented end" clause | **PARTIAL (W6)** |
| Grace period server-side and configurable | Grace is measured from last contact, not start | `test_liveness.py::test_grace_is_measured_from_last_contact_not_from_start` | COMPLIANT |
| Grace period server-side and configurable | Grace period is configurable | `test_resolution.py::test_cli_grace_period_overrides_the_default` + `test_ingestion.py::test_create_app_exposes_the_configured_grace_period`; `cli.py` seam unproven | **PARTIAL (W4)** |

#### `recording-fault-tolerance` (1 requirement, 7 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Non-disruptive failure (RQ-21) | Passing suite survives an internal error | `test_failure_paths.py` | COMPLIANT |
| Non-disruptive failure (RQ-21) | Failing suite still reports failure | `test_failure_paths.py` | COMPLIANT |
| Non-disruptive failure (RQ-21) | Server accepts then closes without responding | `test_failure_paths.py` | COMPLIANT |
| Non-disruptive failure (RQ-21) | Server accepts and never answers | `test_failure_paths.py` | COMPLIANT |
| Non-disruptive failure (RQ-21) | Every hook is fault-isolated | `test_failure_paths.py::test_every_recorder_hook_is_fault_isolated` | COMPLIANT (S2 caveat) |
| Non-disruptive failure (RQ-21) | A failed heartbeat does not stop result recording | `test_failure_paths.py::test_heartbeat_failing_on_every_attempt_warns_once_and_every_result_is_still_recorded` | COMPLIANT (M3) |
| Non-disruptive failure (RQ-21) | A failed heartbeat warns once, not once per beat | same test + `test_start_write_and_heartbeat_failure_share_one_flag_...` | COMPLIANT |

#### `recording-schema` (1 requirement, 3 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Complete schema from first use (RQ-29) | Fresh database matches the column manifest | `test_schema_manifest.py` (9 tests) | COMPLIANT |
| Complete schema from first use (RQ-29) | Opening an existing database issues no schema-altering statement | `test_connection.py::test_opening_a_database_with_the_current_schema_version_succeeds_and_applies_no_ddl` | COMPLIANT |
| Complete schema from first use (RQ-29) | A database from an older schema version is refused, not altered | `test_connection.py::test_opening_a_database_with_an_older_schema_version_is_refused` (+ no-row and newer-version siblings) | COMPLIANT |

**Compliance summary**: **38/41 scenarios compliant, 3 PARTIAL, 0 UNTESTED, 0 FAILING.**
**Requirements**: **8/11 fully compliant.**

Against round two's 38 / 2 PARTIAL / 1 UNTESTED: the count is unchanged at 38,
but the composition improved in the way that matters. The UNTESTED — the only
row that blocked archive — is gone, and one previously-COMPLIANT row was
regraded PARTIAL on new mutation evidence. Requirements hold at 8/11; the same
three requirements (Heartbeat endpoint, RQ-44, Grace period) each carry
exactly one PARTIAL scenario.

### Correctness (Static Evidence)

| Requirement | Status | Notes |
|---|---|---|
| Run entry per invocation (RQ-1) | Implemented | Start-write from `pytest_sessionstart`; `_UPSERT_RUN` insert branch |
| Run timestamps (RQ-31) | Implemented | `_NULL_FINISH_EXIT_STATUSES` decides the null end time |
| Run atomicity (RQ-3) | Implemented | One `BEGIN IMMEDIATE`/`COMMIT` per report; guarded `DO UPDATE` |
| Malformed report rejection (RQ-42) | Implemented | Whole-report rejection; prior accepted writes untouched |
| Heartbeat endpoint | Implemented | Own route; body `{}`; `get_execution` decides the 404 |
| Activity-driven tracking (RQ-25.2) | Implemented | `_maybe_beat` off `pytest_runtest_logreport`, wall-clock gated |
| Single long test limitation | Documented | Own requirement heading and scenario in the spec |
| Abandoned run observable (RQ-44) | Implemented | `derive_presentation`, pure, stdlib-only, in `vantage.core` |
| Grace period configurable | Implemented | CLI -> `ServerConfig` -> `create_app` -> `app.state.grace_period` |
| Non-disruptive failure (RQ-21) | Implemented | Two independent latches from one `_isolated` factory |
| Complete schema from first use (RQ-29) | Implemented | `schema_version` bumped to 2; older databases refused |

### Coherence (Design)

| Decision | Followed? | Notes |
|---|---|---|
| D25 monotonic run upsert | Yes | `DO UPDATE ... WHERE run.exit_status IS NULL` — proven by M8 |
| D27 `last_contact_at` seeded from `received_at` | Yes | Insert branch only; mirrored in both adapters |
| D29 two independent isolation latches | Yes | `_disabled` / `_liveness_disabled` — proven by M3 |
| D30 activity-driven beat, one stall per interval | Partly | `_last_beat_at` assigned before the send as designed, but unguarded — S4 |
| D31/D33 heartbeat is its own endpoint, body read by nothing | Yes | `POST /runs/{id}/heartbeat`, `HeartbeatAcknowledgement` |
| D32 start-write before the first test | Yes | `pytest_sessionstart`, `liveness_isolated` |
| D33 `get_execution` decides the 404, not rowcount | Yes | Proven by round two's mutation A and `test_heartbeat_for_a_known_run_with_a_later_recorded_contact_is_200_not_404` |
| D34 `PRESENTATIONS` a frozenset, never an Enum | Yes | `test_derive_presentation_returns_a_plain_str_never_an_enum` |
| ADR-13 refuse databases from an older schema version | Yes | `SchemaVersionError`, three refusal tests |
| RQ-24 plugin depends on pytest and stdlib only | Yes | `deptry` clean; `test_plugin_imports.py` green |
| RQ-26 core imports no infrastructure | Yes | `test_architecture.py` green (AST check) |

### TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | Yes | apply-progress carries the cycle table for Phases 1–6 |
| All tasks have tests | Yes | 70/70 tasks complete; every behavioural task names a test file |
| RED confirmed (tests exist) | Yes | Every named test file exists and was executed |
| GREEN confirmed (tests pass) | Yes | 247/247 pass on execution, serial and `-n auto` |
| Triangulation adequate | Yes | RQ-44 has 7 table-driven cases; RQ-21 has 7 scenarios |
| Safety Net for modified files | Yes | Phases 5 and 6 modified test files only; full suite run before and after |

**TDD Compliance: 6/6 checks passed.**

Phase 6 declared its test "passed on first run" rather than fabricating a RED,
and then substantiated it the honest way — by mutation, three separate ones
(path suffix, HTTP method, run-id/suffix ordering), all reverted before
landing. That is the correct discipline for a coverage-closing phase: the
guard is proven load-bearing, which is what a RED would have demonstrated. I
independently reproduced the path-suffix mutation (M9) and confirm the claim.

### Test Layer Distribution

| Layer | Tests | Files | Tools |
|---|---|---|---|
| Unit (pure functions, in-memory stores) | ~150 | 12 | pytest |
| Integration (`TestClient`, real SQLite, `pytester`) | ~90 | 9 | pytest, starlette `TestClient` |
| E2E (real subprocess + real HTTP + real server) | ~7 | 2 | `pytester.popen`, `VantageTestServer` |
| **Total** | **247** | **23** | |

The Phase 6 addition sits in the E2E layer, which is where it belonged — it is
the only layer that could have caught the defect round two found.

### Assertion Quality

No tautologies, no ghost loops, no assertions that never call production code,
no smoke-tests-only, no mock-heavy files. Absence assertions were checked
specifically for the swallow-exception trap: `test_sigkilled_session_...` and
`test_a_fast_suite_emits_no_heartbeat` both assert absences, but both read
back through the server or a `recwarn` recorder rather than inside a
`try/except`, and both carry a non-vacuity guard (`len(executions) == 1`,
`result.assert_outcomes`).

| File | Line | Assertion | Issue | Severity |
|---|---|---|---|---|
| `test_ingestion.py` | 363–366 | finish fields unchanged after a heartbeat | Exercises `InMemoryExecutionStore` only; the shipped `SqliteExecutionStore` path is unguarded (M7) | WARNING |
| `test_run_report.py` | 411 | `assert len(beats) >= 1` | Stubbed list, never touches the server — but no longer the only evidence: the E2E sibling now covers the wire | SUGGESTION |
| `test_failure_paths.py` | — | `test_every_recorder_hook_is_fault_isolated` checks `__wrapped__` | Cannot distinguish the two decorators; the property is guarded elsewhere (M3) | SUGGESTION |

**Assertion quality: 0 CRITICAL, 1 WARNING, 2 SUGGESTION.**

### Quality Metrics

**Linter**: `ruff check` — no errors. `ruff format --check` — 58 files already formatted.
**Type Checker**: `mypy .` — no errors in 58 source files (strict).
**Dependencies**: `deptry .` — no issues.

### Issues Found

**CRITICAL**: None.

**WARNING**:

- **W7 (new, this round's finding)** — `session-liveness`'s "A heartbeat cannot
  touch finish fields" and RQ-44.4's "no invented end" clause are proven only
  against `InMemoryExecutionStore`. Mutation M7 made `_TOUCH_LAST_CONTACT`
  fabricate a `finished_at` and the whole suite stayed green. Shipped
  behaviour is correct; the regression guard is missing. Fix: add a
  `get_execution` read-back to
  `vantage_port_contract.py::test_touch_last_contact_is_monotonic_and_reports_unknown_runs`,
  which already runs against both adapters. Roughly five lines; also closes W6.
- **W4 (carried, re-evidenced)** — the `cli.py:107` seam carrying
  `config.grace_period_seconds` into `create_app` is unproven (M1: 247 passed).
  Both halves either side of it are tested with genuinely non-default values.
  Inert today because nothing reads `app.state.grace_period`; a real defect
  once the read API lands.
- **W6 (carried, narrowed)** — RQ-44.4 is sufficient by construction for
  "invents no stored field", but its "unchanged start / no invented end"
  clause has no runtime guard (M7, M10). Same one-assertion fix as W7.
- **W9 (new, low)** — the heartbeat's use of `_liveness_timeout` rather than
  `_timeout` is unguarded (M5: 247 passed), although the sibling property for
  the start-write *is* guarded by
  `test_start_write_uses_the_liveness_timeout_not_the_report_timeout`. An
  asymmetry in an otherwise careful test suite. Spec prose, not a scenario, so
  it does not affect the matrix.
- **W8 (carried, MUST survive into the archive record) — the Phase 4
  accounting hole.** That attempt was acquired retroactively, so the ledger
  recorded 0 changed lines. The 0 is an artefact, not a measurement. The real
  figure is **770 changed lines against a 500-line budget**. This is a recorded
  gap, not a passing check, and it must not be read as one.
- **W10 (carried, low)** — `test_a_still_running_session_already_has_a_run_entry`
  races the child's 5 s sleep against the poll-and-assert window. Bounded and
  generous; watch on loaded CI.
- **W11 (new) — a round-two accounting artefact that is the orchestrator's
  error, not any actor's.** Verify round two was given a 100-line budget while
  it was also required to write `verify-report.md`, and the orchestrator
  committed the Phase 6 task addendum while that attempt was still open. The
  ledger consequently charged the attempt 576 lines. Recorded here so the
  accounting stays honest: the overage is a budgeting mistake by the
  orchestrator, not oversized work by the verify actor.

**SUGGESTION**:

- **S1 (carried)** — `_fixed_width_isoformat` is still untested and still
  trusts its caller: a UTC+2 11:00 datetime formats as
  `...T11:00:00.000000+00:00`. Latent only — every current caller passes UTC.
- **S2 (carried, narrowed)** — `test_every_recorder_hook_is_fault_isolated`
  checks only `__wrapped__`, which `liveness_isolated` also sets, so it cannot
  tell the two decorators apart. The behavioural distinction is guarded
  elsewhere (M3), so this is cosmetic.
- **S4 (new)** — `_maybe_beat` assigning `_last_beat_at` *before* the send
  ("one stall per interval rather than one per test", D30) is a docstring-only
  claim: M2 inverted the order and 247 tests stayed green. Low impact — after
  the first failure the liveness latch makes `_maybe_beat` a no-op, so the
  ordering is observable only for a slow-but-succeeding server.

### Success Criteria audit

1. **"`last_contact_at` advances/stops"** — **now closable.** This was round
   two's C6. Phase 6's E2E test proves the advance end to end, and M9 proves
   the guard load-bearing.
2. **"no scenario in `openspec/specs/` contradicts shipped behaviour"** —
   genuinely open, and closable only **at archive**. `openspec/specs/session-ingestion/spec.md`
   (lines 74, 79, 84, 90) still says "the run table stays empty", and
   `openspec/specs/run-recording/spec.md:98` still says "no run entry for that
   session is present either". The MODIFIED deltas that correct both exist and
   are unmerged. This is the archive step's job, not an implementation gap.
3. **"Every slice under 500 changed lines"** — genuinely open; Phase 4 was 770
   (W8). Recorded, not closed.

### Still open by design — restated, not re-litigated

- The single very-long test produces no beat: accepted and documented, with
  its own requirement heading and scenario in
  `specs/session-liveness/spec.md:57-69`.
- Write side only: RQ-44's read-back is Analysis against the derivation
  helper, not Demonstration through a live read path.
- One liveness latch covers both the start-write and the beat: deliberate,
  proven by `test_start_write_and_heartbeat_failure_share_one_flag_leaving_two_warnings_total`.

### Verdict

**FAIL on incomplete scenario evidence — 0 CRITICAL, 0 blockers, 0 defects.**

This is not the same kind of failure as rounds one and two. Both of those found
behaviour with no runtime evidence at all. This round found none: every gate is
green (247 passed, zero failures, zero skips, **zero warnings**, serial and
under `-n auto`), every one of the 41 scenarios has runtime evidence, and every
production path behind the three PARTIAL rows is correct as shipped — I read
each one.

The verdict is `fail` because the verify contract does not admit a `pass` while
scenario evidence is incomplete, and 3 of 41 scenarios are PARTIAL. I kept those
grades rather than rounding them up to clear the validator: each is backed by a
mutation that left all 247 tests green, so each gap is demonstrably real.

**What it takes to reach 41/41 and archive — two tests:**

1. Add a `get_execution` read-back to
   `vantage_port_contract.py::test_touch_last_contact_is_monotonic_and_reports_unknown_runs`,
   asserting `started_at`, `finished_at`, `exit_status`, `interrupted` and
   `interrupt_reason` survive a touch. It already runs against both adapters,
   so this closes **W7 and W6 together** and kills mutations M7 and M10. ~5 lines.
2. Add one test that `cli.main()` propagates `--grace-period` into
   `app.state.grace_period`, closing **W4** and killing mutation M1. ~5 lines.

Neither touches production code, exactly as Phases 5 and 6 did not. Route:
`sdd-apply` for that narrow work, then re-verify, then archive.

No code was changed by this phase. All ten mutations were reverted and the tree
proven clean at `5fb8cd3`.

