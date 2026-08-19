```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:4f062d10a94c71d1996ca56972558361577330cb38667f9005ead0da5ee24183
verdict: fail
blockers: 1
critical_findings: 1
requirements: 8/11
scenarios: 38/41
test_command: uv run --extra dev pytest
test_exit_code: 0
test_output_hash: sha256:30f4a1b6c538bbb9b80e79e7987a860027a2ab3edbcba73a8b8075cabfd8ee5d
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:8797c60315242ee16057cb107ea1ec62e2678d358c86bc2be797e23d87e87578
```

## Verification Report — Round 2

**Change**: session-lifecycle
**Branch**: `ft/session-lifecycle-05-missing-scenarios` @ `683a177`
**Version**: N/A
**Mode**: Strict TDD
**Supersedes**: round one (verified @ `1505068`, 5 CRITICAL / 9 WARNING / 2 SUGGESTION)

### Headline

Round one's five CRITICAL findings are **all genuinely closed**, and I proved
three of them load-bearing by my own mutation, not by trusting the report.
Every gate is green: **246 passed, zero warnings**.

But round one under-classified one of its own PARTIAL rows. `session-liveness`'s
"A long suite's last contact advances during execution" is not partially
covered — its `THEN` clause has **no covering test at all**. I broke
`send_heartbeat`'s URL and **all 246 tests still passed**. That is one new
CRITICAL, and it blocks archive.

---

### Phase 5 claim audit

Every claim the apply phase made was checked, not believed.

| Claim | Verdict | Evidence |
|---|---|---|
| No production file touched | ✅ **TRUE** | `git diff --name-status ft/session-lifecycle-04-heartbeat..HEAD` returns 7 paths: 3 openspec, 4 test files. **Zero `src/` paths.** The "no defect found" conclusion is therefore safe. |
| C1 still-running entry tested | ✅ TRUE, mutation-proven | Mutation C below |
| C2 SIGKILL'd entry present | ✅ TRUE, mutation-proven | Mutation C below |
| C3 SIGKILL carries no interrupt reason | ✅ TRUE | Asserts `interrupted is False` **and** `interrupt_reason is None`, the absence directly |
| C4/C5 truncated finish after accepted start | ✅ TRUE | `test_rejection.py:752`; asserts row survives, `finished_at is None`, `exit_status is None`, `count_results() == 0`, plus `assert completed` so the ASGI path provably ran — not vacuous |
| W1 heartbeat 200-not-404 | ✅ TRUE, **independently mutation-proven** | Mutation A below |
| W2 finish leaves 3 columns untouched | ✅ TRUE, **independently mutation-proven** | Mutation B below |
| W3 fast suite asserts warning-freedom | ✅ TRUE | `recwarn` added, `assert len(recwarn) == 0` |
| 5 new tests + 1 extended | ✅ TRUE | 241 → 246 |

### Mutation checks I ran myself (all reverted, revert proven)

| # | Mutation | Result |
|---|---|---|
| **A** (W1) | Heartbeat 404 derived from `touch_last_contact`'s boolean instead of `get_execution` | **1 failed, 245 passed** — only `test_heartbeat_for_a_known_run_with_a_later_recorded_contact_is_200_not_404`. Genuine guard, and no pre-existing test caught it. |
| **B** (W2) | `last_contact_at = excluded.last_contact_at` added to `_UPSERT_RUN`'s `DO UPDATE SET` | **1 failed, 245 passed** — only `test_finish_write_leaves_received_at_started_at_and_last_contact_at_untouched`. Genuine guard. |
| **C** (C1–C3) | `pytest_sessionstart`'s `send(...)` suppressed — i.e. exact pre-change behaviour | **4 failed** incl. `test_a_still_running_session_already_has_a_run_entry` and `test_sigkilled_session_leaves_a_start_time_null_end_time_and_no_interrupt_reason`. Both restored criteria genuinely depend on the start-write. |
| **D** (new) | `send_heartbeat`'s URL suffix replaced with `/HEARTBEAT-TYPO` | **246 passed — nothing failed.** See CRITICAL C6. |

Revert proof after each: `git status --porcelain` empty, `git diff HEAD --stat` empty,
`git diff --name-only HEAD | rg 'src/'` → no match. Working tree is clean at `683a177`.

---

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 67 |
| Tasks complete | 67 |
| Tasks incomplete | 0 |

Per phase: 14 / 10 / 11 / 23 / 9. No unchecked box anywhere.
(Note: `apply-progress` states "70 tasks"; the file holds 67. Bookkeeping drift, not a work gap — see S3.)

### Build & Tests Execution

**Build**: ✅ Passed
```text
uv run mypy .          -> Success: no issues found in 58 source files   (exit 0)
uv run ruff check .    -> All checks passed!                            (exit 0)
uv run ruff format --check .  -> 58 files already formatted             (exit 0)
uv run deptry .        -> Success! No dependency issues found           (exit 0)
```

**Tests**: ✅ 246 passed / 0 failed / 0 skipped / **0 warnings**
```text
uv run --extra dev pytest          -> 246 passed in 25.43s   (exit 0)
uv run --extra dev pytest -n auto  -> 246 passed in 10.80s   (exit 0)
test_architecture.py + test_plugin_imports.py -> 6 passed
```

Warning count is **zero**, matching round one. No regression.

Not run locally, left to CI (unchanged from round one): 3.10–3.13 × xdist matrix,
RQ-28 networking-disabled job, RQ-24 clean-environment install check.

**Coverage**: ➖ Not available by project decision (no `pytest-cov`, `coverage_threshold: 0`).

### Spec Compliance Matrix

41 scenarios across 11 requirements in 5 delta specs — identical totals to round one,
confirming Phase 5 changed no spec file.

#### run-recording (3 requirements, 14 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| RQ-1 | First invocation against an empty database | `test_run_report.py` | ✅ COMPLIANT |
| RQ-1 | Second invocation gets a distinct identifier | `test_run_report.py` | ✅ COMPLIANT |
| RQ-1 | Zero-test collection still writes a row | `test_run_report.py` | ✅ COMPLIANT |
| RQ-1 | Failed collection still writes a row | `test_run_report.py` | ✅ COMPLIANT |
| RQ-1 | **A still-running session already has a run entry (RQ-1.5)** | `test_run_report.py > test_a_still_running_session_already_has_a_run_entry` | ✅ **COMPLIANT (was UNTESTED)** |
| RQ-1 | **A SIGKILL'd session's entry is present (RQ-1.6)** | `test_run_report.py > test_sigkilled_session_leaves_a_start_time_null_end_time_and_no_interrupt_reason` | ✅ **COMPLIANT (was UNTESTED)** |
| RQ-31 | Completed session records both timestamps | `test_run_report.py` | ✅ COMPLIANT |
| RQ-31 | Interrupted session leaves a null end time | `test_sigint_leaves_start_time_and_null_end_time` | ✅ COMPLIANT |
| RQ-31 | **SIGKILL'd session carries no interrupt reason (RQ-31.3)** | same SIGKILL test | ✅ **COMPLIANT (was UNTESTED)** |
| RQ-3 | Server killed mid-write (RQ-3.1) | Analysis + `test_start_write_reaches_storage_in_one_commit`, `test_finish_report_reaches_storage_in_one_commit` | ✅ COMPLIANT (declared method: Analysis) |
| RQ-3 | Report truncated in transit, no prior start-write (RQ-3.2) | `test_truncated_body_raw_socket` | ✅ COMPLIANT |
| RQ-3 | **Finish report truncated after an accepted start-write (RQ-3.2)** | `test_rejection.py > test_finish_report_truncated_after_an_accepted_start_write_leaves_the_start_row_intact` | ✅ **COMPLIANT (was UNTESTED)** |
| RQ-3 | Normal report is fully present (RQ-3.3) | `test_rejection.py` | ✅ COMPLIANT |
| RQ-3 | A reordered start-write never nulls a recorded finish | `test_reordered_start_write_never_nulls_a_recorded_finish` | ✅ COMPLIANT |

#### session-ingestion (1 requirement, 6 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| RQ-42 | Missing required field (RQ-42.1) | `test_missing_field_is_422_naming_the_field` | ✅ COMPLIANT |
| RQ-42 | Invalid JSON (RQ-42.2) | `test_non_json_body_is_400` | ✅ COMPLIANT |
| RQ-42 | Body truncated midway, no prior report (RQ-42.3) | `test_truncated_body_raw_socket` | ✅ COMPLIANT |
| RQ-42 | **Finish report truncated after an accepted start-write (RQ-42.3, RQ-3.2)** | `test_finish_report_truncated_after_an_accepted_start_write_leaves_the_start_row_intact` | ✅ **COMPLIANT (was UNTESTED)** |
| RQ-42 | One malformed result rejects the whole report | `test_one_malformed_result_among_five_hundred_rejects_the_whole_report` | ✅ COMPLIANT |
| RQ-42 | Rejection names the cause, safely (RQ-42.4) | `test_422_response_never_echoes_input_or_pydantic_types`, `test_forbidden_extra_field_name_is_not_echoed` | ✅ COMPLIANT |

#### recording-schema (1 requirement, 3 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| RQ-29 | Fresh database matches the column manifest | `test_fresh_database_matches_the_manifest_in_both_directions` | ✅ COMPLIANT |
| RQ-29 | Opening an existing database issues no schema-altering statement | `test_reopening_an_existing_database_issues_no_ddl` | ✅ COMPLIANT |
| RQ-29 | A database from an older schema version is refused, not altered | `test_opening_a_database_with_an_older_schema_version_is_refused`, `test_a_refusal_issues_no_ddl_and_closes_the_connection_before_raising` | ✅ COMPLIANT |

#### recording-fault-tolerance (1 requirement, 7 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| RQ-21 | Passing suite survives an internal error | `test_reporting_error_preserves_passing_exit_status_and_warns_once` | ✅ COMPLIANT |
| RQ-21 | Failing suite still reports failure | `test_reporting_error_preserves_failing_exit_status_and_warns_once` | ✅ COMPLIANT |
| RQ-21 | Server accepts then closes without responding | `test_server_accepts_then_closes_without_responding` | ✅ COMPLIANT |
| RQ-21 | Server accepts and never answers | `test_server_accepts_and_never_answers_finishes_within_timeout_plus_five_seconds` | ✅ COMPLIANT |
| RQ-21 | Every hook is fault-isolated | `test_every_recorder_hook_is_fault_isolated` | ✅ COMPLIANT (see S2) |
| RQ-21 | A failed heartbeat does not stop result recording | `test_heartbeat_failing_on_every_attempt_warns_once_and_every_result_is_still_recorded` | ✅ COMPLIANT |
| RQ-21 | A failed heartbeat warns once, not once per beat | same, + `test_start_write_and_heartbeat_failure_share_one_flag_leaving_two_warnings_total` | ✅ COMPLIANT |

#### session-liveness (5 requirements, 11 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Heartbeat endpoint | A heartbeat advances last contact | `test_heartbeat_advances_last_contact_for_an_accepted_start_write` | ✅ COMPLIANT |
| Heartbeat endpoint | A heartbeat cannot touch finish fields | `test_heartbeat_cannot_touch_finish_fields` | ✅ COMPLIANT |
| Activity-driven tracking | **A long suite's last contact advances during execution** | `test_a_suite_exceeding_one_heartbeat_interval_sends_at_least_one_heartbeat` — **monkeypatches `send_heartbeat` away; never observes `last_contact_at`** | ❌ **UNTESTED (C6 — round one called this PARTIAL)** |
| Activity-driven tracking | A fast suite emits no heartbeat | `test_a_fast_suite_emits_no_heartbeat` (+`recwarn`) | ✅ COMPLIANT (strengthened by W3 fix) |
| Long-test limitation | A single very long test can read as abandoned while alive | `test_a_run_past_its_grace_period_with_no_finish_or_interrupt_derives_as_abandoned` | ✅ COMPLIANT (declared, accepted limitation) |
| RQ-44 | A run past its grace period derives as abandoned (RQ-44.1) | `test_a_run_past_its_grace_period_with_no_finish_or_interrupt_derives_as_abandoned` | ✅ COMPLIANT |
| RQ-44 | A run inside its grace period derives as running (RQ-44.2) | `test_a_run_inside_its_grace_period_derives_as_running` | ✅ COMPLIANT |
| RQ-44 | A Ctrl-C interrupted run derives as interrupted (RQ-44.3) | `test_an_interrupted_run_derives_as_interrupted_before_the_clock_is_consulted` | ✅ COMPLIANT |
| RQ-44 | Abandonment invents no stored field (RQ-44.4) | composition only (manifest + architecture tests); no test inspects a stored record for an abandoned run | ⚠️ PARTIAL (W6, unchanged) |
| Grace period | Grace is measured from last contact, not start | `test_grace_is_measured_from_last_contact_not_from_start` | ✅ COMPLIANT |
| Grace period | Grace period is configurable | `test_liveness.py` uses `_GRACE = timedelta(minutes=15)`, which **is** the 900 s default; no test derives with a non-default grace | ⚠️ PARTIAL (W4, unchanged) |

**Compliance summary**: **38/41 scenarios compliant**, 1 UNTESTED, 2 PARTIAL.
**Requirements complete**: **8/11** (incomplete: Activity-driven tracking, RQ-44, Grace period).

**Round one's 8 non-compliant rows, resolved:**

| Round 1 | Scenario | Round 2 |
|---|---|---|
| C1 UNTESTED | Still-running session has a run entry | ✅ **CLOSED**, mutation-proven |
| C2 UNTESTED | SIGKILL'd session's entry present | ✅ **CLOSED**, mutation-proven |
| C3 UNTESTED | SIGKILL carries no interrupt reason | ✅ **CLOSED** |
| C4 UNTESTED | RQ-3.2 truncated finish after start-write | ✅ **CLOSED** |
| C5 UNTESTED | RQ-42.3 same | ✅ **CLOSED** |
| W4 PARTIAL | Grace period is configurable | ⚠️ **still PARTIAL** — not addressed by Phase 5 |
| W5 PARTIAL | Long suite's last contact advances | ❌ **DOWNGRADED to UNTESTED (C6)** — mutation shows the gap is total |
| W6 PARTIAL | Abandonment invents no stored field | ⚠️ **still PARTIAL** — not addressed by Phase 5 |

### Correctness (Static Evidence)

| Requirement | Status | Notes |
|---|---|---|
| RQ-1 / RQ-31 start-write | ✅ Implemented | `Recorder.pytest_sessionstart`, `@liveness_isolated` |
| RQ-3 per-report atomicity | ✅ Implemented | `_UPSERT_RUN` conflict clause guarded by `WHERE run.exit_status IS NULL AND excluded.exit_status IS NOT NULL` |
| RQ-29 schema refusal | ✅ Implemented | version-row checks, refusal issues no DDL |
| RQ-44 derivation | ✅ Implemented | pure `derive_presentation`, no stored column |
| Heartbeat endpoint | ✅ Implemented | 404 resolved by `get_execution`, not rowcount — verified by Mutation A |

### Coherence (Design)

| Decision | Followed? | Notes |
|---|---|---|
| D25 upsert insert branch | ✅ Yes | |
| D27 finished run is not stale | ✅ Yes | proven by Mutation B |
| D29 two independent latches | ✅ Yes | `test_liveness_isolated_and_fault_isolated_flags_never_read_or_set_each_other` |
| D30 activity-driven beats | ⚠️ Partly | wiring proven in-process; **the wire call itself is never exercised** (C6) |
| D32 start-write shares `_started_at` | ✅ Yes | |
| D33 404 from `get_execution` | ✅ Yes | Mutation A |
| Design's Integration/E2E test-layer plan (`design.md:556-557`) | ✅ Yes | now decomposed as tasks 5.1–5.9 — round one's W7 is closed |

### Issues Found

**CRITICAL** (1):

- **C6 — `session-liveness` "A long suite's last contact advances during execution" has no covering test, and the plugin→server heartbeat wire contract is entirely unverified.**
  The scenario's `THEN` is *"`last_contact_at` for its run advances at least once before the session finishes."* The only test for it,
  `test_a_suite_exceeding_one_heartbeat_interval_sends_at_least_one_heartbeat`,
  monkeypatches `pytest_vantage.recorder.send_heartbeat` with a list-appending stub
  and asserts `len(beats) >= 1`. It never contacts the server and never reads
  `last_contact_at`.
  **`send_heartbeat` is never invoked against a real server anywhere in the suite** —
  every one of its two test references replaces it (one stub, one failure injection).
  **Proof (Mutation D)**: replacing its URL suffix with `/HEARTBEAT-TYPO` leaves
  **all 246 tests passing**. A typo in the plugin's heartbeat path, method or body
  would ship green. The three-link chain this change depends on — plugin decides to
  beat → `send_heartbeat` posts → route advances contact — has links 1 and 3 tested
  and link 2 untested.
  Round one recorded this as PARTIAL (W5) on the weaker observation that the test
  "asserts a beat was sent, not that `last_contact_at` advanced." The mutation shows
  the gap is not partial: the `THEN` clause is unobserved.
  **Fix**: one end-to-end test — a suite exceeding a shrunk `_BEAT_INTERVAL_SECONDS`
  against the real `vantage_server` fixture with `send_heartbeat` *not* patched,
  asserting the server's `last_contact_at` for that run advances before finish.

**WARNING** (4):

- **W4** (carried, unchanged) "Grace period is configurable" is PARTIAL. `test_liveness.py`'s `_GRACE = timedelta(minutes=15)` **is** the 900 s default, so the scenario's discriminating condition — last contact older than the *configured* value but younger than the *default* — is never exercised. `test_create_app_exposes_the_configured_grace_period` tests app wiring, not derivation.
- **W6** (carried, unchanged) RQ-44.4 "Abandonment invents no stored field" is PARTIAL — true by construction (`derive_presentation` is pure; no such column exists in `schema.sql` or the manifest) and guarded indirectly by the manifest test, but no test states the scenario.
- **W8** (carried, **must survive into the archive record**) **The Phase 4 accounting hole is a recorded gap, not a passing check.** That attempt was acquired retroactively, after the code was written and committed, so the ledger recorded **0 changed lines**. That 0 is an artefact. The real figure is **770 changed lines against a 500-line budget**. Do not read the ledger's 0 as a measurement. Phase 5's own figure (361 lines) is within budget and was measured normally.
- **W10** (new, low) `test_a_still_running_session_already_has_a_run_entry` depends on the child's 5 s sleep outlasting the poll-plus-assert window. Bounded and generous, but it is a wall-clock race on a heavily loaded CI runner. Watch it in the 3.10–3.13 × xdist matrix.

**SUGGESTION** (3):

- **S1** (carried) `_fixed_width_isoformat` still has no test and still trusts its caller: a UTC+2 11:00 datetime formats as `...T11:00:00.000000+00:00`, and a naive datetime formats as if UTC. Latent only — both call sites pass `datetime.now(timezone.utc)`. Its plugin-side twin `recorder.isoformat_utc` has two width tests.
- **S2** (carried) `test_every_recorder_hook_is_fault_isolated` checks only for `__wrapped__`, which `liveness_isolated` also sets, so it cannot tell the two decorators apart — a hook wrongly given `liveness_isolated` passes. It did gain a vacuity guard (`assert hooks`), which is an improvement.
- **S3** (new) `apply-progress` reports "70 tasks"; `tasks.md` holds 67 (14/10/11/23/9). Bookkeeping drift only — 0 unchecked either way.

### Success Criteria audit (proposal.md)

Phase 5 ticked 8 of 11 and left 3 unticked. **All three are honestly unticked.**

| Criterion | Unticked correctly? | Genuinely open? |
|---|---|---|
| "`last_contact_at` advances while a suite runs and stops when the process dies" | ✅ Honest | **Genuinely open — and worse than stated.** This is C6. Correctly not ticked. |
| "no scenario in `openspec/specs/` contradicts shipped behaviour" | ✅ Honest | **Genuinely open, and only closable at archive.** Verified: `openspec/specs/session-ingestion/spec.md:74,79,84,90` still say "the run table stays empty" and `openspec/specs/run-recording/spec.md:98` still says "no run entry for that session is present either". The `MODIFIED` deltas exist but are unmerged. Not a Phase 5 failure. |
| "Every slice lands under 500 changed lines" | ✅ Honest | **Genuinely open.** Phase 4 was 770 against 500 (W8). Correctly not ticked. |

None was closed-and-missed.

### Still open by design — confirmed, not closed

- **Single very long test produces no beat**: accepted, documented limitation. It is in the **spec**, not merely the proposal — `specs/session-liveness/spec.md:57-69` gives it its own Requirement heading plus a scenario declaring the behaviour accepted. Confirmed honestly represented.
- **Write side only**: `specs/session-liveness/spec.md` Purpose states RQ-44's read-back criteria are **Analysis** against the derivation helper, not Demonstration through a live read path. Confirmed.
- **One liveness latch covering both the start-write and the beat**: deliberate, specified in `recording-fault-tolerance`, proven by `test_start_write_and_heartbeat_failure_share_one_flag_leaving_two_warnings_total` (two warnings, not three).
- **Phase 4 accounting hole**: recorded above as W8. This report still says so.

### TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | ✅ | Full cycle table in `apply-progress` for Phase 5 |
| All tasks have tests | ✅ | 9/9 Phase 5 tasks map to a test or a docs action |
| RED confirmed (tests exist) | ✅ | All 5 new tests + 1 extension exist and were read |
| GREEN confirmed (tests pass) | ✅ | 246/246 on execution |
| Honesty of RED reporting | ✅ | Apply declared "passed on first run, no fabricated RED" for all 6 — accurate. These are regression guards, not drivers, which is the correct outcome for a coverage-closing phase. |
| Guard strength verified by injection | ✅ | Apply claimed injection for W1/W2/W3; I independently reproduced **A (W1)** and **B (W2)**, plus **C (C1–C3)** which apply did not run. All behaved exactly as claimed. |
| Triangulation adequate | ✅ | SIGKILL test is the deliberate contrast case to the existing SIGINT test |
| Safety Net for modified files | ✅ | 245/246 unaffected under every mutation |

**TDD Compliance**: 8/8 checks passed.

### Test Layer Distribution (Phase 5 additions)

| Layer | Tests | Files | Tools |
|---|---|---|---|
| Unit | 2 | `test_sqlite_store.py`, `test_run_report.py` | pytest |
| Integration | 1 | `test_ingestion.py` | starlette `TestClient` |
| E2E | 3 | `test_run_report.py` (×2 real subprocess), `test_rejection.py` (raw socket + uvicorn) | `pytester.popen`, `os.kill`, raw sockets |
| **Total (suite)** | **246** | | |

### Changed File Coverage

➖ Coverage analysis skipped — no coverage tool detected. `pytest-cov` is absent
from the dev extra and the lockfile, and `openspec/config.yaml` sets
`coverage_threshold: 0` deliberately. Not a failure.

### Assertion Quality

| File | Line | Assertion | Issue | Severity |
|---|---|---|---|---|
| `packages/pytest-vantage/tests/test_run_report.py` | 411 | `assert len(beats) >= 1` | Asserts a stub list was appended to; `send_heartbeat` is patched out, so the scenario's `last_contact_at` clause is never observed | CRITICAL (C6) |
| `packages/pytest-vantage/tests/test_failure_paths.py` | 793 | `hasattr(..., "__wrapped__")` | Cannot distinguish `fault_isolated` from `liveness_isolated` | SUGGESTION (S2) |

**Assertion quality**: 1 CRITICAL, 0 WARNING, 1 SUGGESTION.
No tautologies, no ghost loops, no assertions that never call production code.
The five new Phase 5 tests are all substantive: each asserts a concrete value or
absence, and three were proven load-bearing by mutation.

### Quality Metrics

**Linter**: ✅ No errors (`ruff check`, `ruff format --check`)
**Type Checker**: ✅ No errors (`mypy .`, 58 source files)
**Dependencies**: ✅ No issues (`deptry`)

### Verdict

**FAIL — not archive-ready.**

Phase 5 did exactly what it claimed: it closed all five of round one's CRITICAL
findings with real, mutation-resistant tests, touched no production code, and kept
every gate green at 246 passed / zero warnings. Its three unticked Success Criteria
are honestly unticked.

One CRITICAL blocks archive, and it is one round one owned but mis-graded: the
plugin's heartbeat never reaches a real server in any test, so
`session-liveness`'s "A long suite's last contact advances during execution"
has no covering test at all. Breaking `send_heartbeat`'s URL leaves all 246 tests
green.

**Exactly what blocks archive**: one end-to-end test joining `send_heartbeat`
to the real route and asserting the server's `last_contact_at` advances during
a running suite. W4 and W6 remain recorded PARTIALs and do not block.
