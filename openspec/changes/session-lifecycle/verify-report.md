```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:9724b84d6cad13d4e0c88f6d8608b2a4d2981860c38d1b452853d9c3d1afdb0a
verdict: pass
blockers: 0
critical_findings: 0
requirements: 11/11
scenarios: 41/41
test_command: uv run --extra dev pytest
test_exit_code: 0
test_output_hash: sha256:bc072e569f937b2f252d2f85bafc44fa797a47679c2e72b277aadb24840d099a
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:8797c60315242ee16057cb107ea1ec62e2678d358c86bc2be797e23d87e87578
```

## Verification Report

**Change**: session-lifecycle — **ROUND FOUR**
**Branch**: `ft/session-lifecycle-06-heartbeat-wire` @ `e931c5d` (tip of the six-slice chain plus two guard tests and one mypy fix)
**Version**: N/A
**Mode**: Strict TDD
**Supersedes**: round three (@ `5fb8cd3`, 0 CRITICAL / 3 PARTIAL), round two (@ `683a177`, 1 CRITICAL), round one (@ `1505068`, 5 CRITICAL)

### Headline

**This is archive-ready.** 41/41 scenarios compliant, 11/11 requirements, 0 CRITICAL,
0 blockers, 70/70 tasks, every gate green. The verdict moves from `fail` to `pass`
because the last three PARTIAL rows now carry runtime evidence, not because the bar
moved: I re-ran both of the orchestrator's claimed mutations myself and both went red
for exactly the right reason and nothing else.

Round four ran **twelve** mutations. Ten went red. The two that stayed silent are the
same invariant seen twice, they map to no scenario, and the production code is correct —
they are recorded below as WARNINGs and they do not block.

### The orchestrator's two claims — verified, not believed

Both new tests were re-proven by re-applying the exact mutation each is supposed to kill.

| Mutation | Result | Failing test | Verdict |
|---|---|---|---|
| `_TOUCH_LAST_CONTACT` also writes `finished_at = '2099-01-01T00:00:00.000000+00:00'` | **1 failed, 247 passed** | `test_sqlite_store.py::TestSqliteExecutionStore::test_touch_last_contact_is_monotonic_and_reports_unknown_runs` → `assert after.finished_at is None` | claim TRUE |
| `grace_period_seconds=` dropped from `create_app` in `cli.py:107` | **1 failed, 247 passed** | `test_resolution.py::test_cli_main_carries_the_resolved_grace_period_into_the_app` → `assert 900.0 == 60.0` | claim TRUE |

The first failure lands on the **SQLite** parametrisation of the shared port contract —
the shipped adapter, the exact layer round three's M7 slipped through. The second
reproduces the silent-default failure round three predicted in words (`--grace-period 60`
running at 900) as a literal assertion diff. Neither test is decoration.

### Round three's three un-graded silent mutations — re-derived

Round three graded M2, M5 and M10 below the blocking bar and asked round four to
re-derive that judgement by mutation rather than by reasoning. I did.

**M2 (`_last_beat_at` assigned after the send) — genuinely acceptable, and round three
under-described it.** Re-applied: **248 passed, silent.** But this is not a coverage gap,
it is a **provably equivalent mutant**, and no test can ever kill it. Proof from
`boundary.py:99`: `_isolated`'s wrapper opens with `if getattr(self, flag): return None`.
On the success path both orderings assign the identical pre-captured `now` (captured at
`recorder.py:168`, before the send in either variant). On the failure path the assignment
is skipped, but `setattr(self, flag, True)` at `boundary.py:104` latches
`_liveness_disabled`, so `_maybe_beat`'s body is never entered again and `_last_beat_at`
is never read again. D30's "one stall per interval" is delivered by the latch, not by the
assignment order. Round three carried this as SUGGESTION S4; it should be **retired**,
not carried a fourth time.

**M5 (beat bounded by `_timeout`, not `_liveness_timeout`) — genuinely acceptable as a
scenario matter, but round three under-graded the asymmetry.** Re-applied: **248 passed,
silent.** No scenario asserts a per-beat timeout bound — the ~2.0 s figure appears only in
the *prose* of "Grace period is server-side, configurable, and measured from last
contact", never in a Given/When/Then. So it cannot make a scenario non-compliant and it
does not block. What round three called "an asymmetry" I confirmed is a real one: I
mutated the **start-write** sibling the same way (`send(..., timeout=self._timeout)`) and
got **1 failed** —
`test_run_report.py::test_start_write_uses_the_liveness_timeout_not_the_report_timeout`,
at `test_run_report.py:120`. The guard pattern already exists and asserts
`timeouts == [2.0, 5.0]`; the beat has no sibling. Stays WARNING (W9).

**M10 (memory adapter `started_at` overwrite) — UNDER-GRADED. Round three was wrong
about it, and round four found why.** Re-applied: **248 passed, silent.** Round three
called it "W7 supporting (partly equivalent mutant)" and predicted the W7 fix would close
it. **The fix landed and did not close it** — the new read-back guards the *touch* path,
not the *finish-write* path. Worse, the SQLite twin is silent too, and there is a test
whose own docstring claims to guard exactly this. See W12 below. It maps to no scenario,
so it does not block, but it is a standalone gap, not a supporting detail.

### Round four's finding (W12) — a vacuous assertion in the test named for the invariant

`test_sqlite_store.py::test_finish_write_leaves_received_at_started_at_and_last_contact_at_untouched`
asserts three things (lines 60–62). Two of them bite. The `started_at` one cannot.

**Mutation M11**: added `started_at = excluded.started_at` to `_UPSERT_RUN`'s
`DO UPDATE SET` list — a finish-write that advances the recorded start time →
**248 passed, and that very test passed.**

The cause is the fixture, not the assertion. The test builds its finish-write with
`_execution(identity, finished=True, started=started)` at line 52 — the **same** `started`
literal the start-write used at line 46. Both sides of `assert after_started_at ==
before_started_at` therefore derive from one value, and no handling of `started_at` in the
UPSERT can make them differ. Its `received_at` and `last_contact_at` siblings *do* bite,
precisely because they use differing values (`received` vs `received + 1h`).

I proved the invariant is genuinely breakable with a scratch script outside the repo,
against unmutated and mutated production code in turn:

```text
unmutated: start-write 09:00:00 | finish reports 12:00:00 | stored 09:00:00 -> held
M11:       start-write 09:00:00 | finish reports 12:00:00 | stored 12:00:00 -> ADVANCED
           and the repo test that names this invariant: 1 passed
```

**No defect ships.** Both adapters are correct as written and deliberately so — SQLite's
`DO UPDATE SET` list simply omits `started_at`, and `memory.py:73` uses
`started_at=stored.started_at` with a comment saying why. This is a defence-in-depth gap
plus a mislabeled assertion, not a bug.

**Why it does not block any scenario.** The clause that could have depended on it —
RQ-44.4's "the start time it was recorded with is unchanged" — is scoped to *a run derived
as abandoned*, and an abandoned run has no finish-write by construction:
`derive_presentation` rule 1 returns `"finished"` the moment `finished_at is not None`. An
abandoned run's stored record is what the start-write wrote plus heartbeat touches, and
both are now guarded on both adapters by the new read-back. The reordered-start-write
scenario is guarded separately by `_UPSERT_RUN`'s `WHERE` (round three's M8: 3 failed).
RQ-31.1's "end later than start" survives M11 because the plugin sends one `_started_at`
in both writes.

### Full mutation log — twelve mutations, all reverted, tree proven clean

| # | Mutation | Result |
|---|---|---|
| A | `_TOUCH_LAST_CONTACT` fabricates `finished_at` | **1 failed** (SQLite contract) |
| B | `cli.py` drops `grace_period_seconds=` | **1 failed** (`900.0 == 60.0`) |
| M10 | memory finish branch overwrites `started_at` | 248 passed — silent (W12) |
| M11 | SQLite `DO UPDATE` advances `started_at` | 248 passed — silent (W12) |
| M2 | `_last_beat_at` assigned after the send | 248 passed — **equivalent mutant** |
| M5 | beat uses `_timeout` not `_liveness_timeout` | 248 passed — silent (W9) |
| M12 | start-write uses `_timeout` (asymmetry probe) | **1 failed** |
| M13 | `derive_presentation` ignores `last_contact_at` | **2 failed** |
| M14 | heartbeat route drops the unknown-run 404 | **1 failed** |
| M15 | abandonment checked before `interrupted` | **1 failed** |
| M16 | heartbeat route records a stale fixed time | **2 failed** |
| M17 | older-schema database accepted, not refused | **4 failed** |
| M18 | `_BEAT_INTERVAL_SECONDS` 30.0 → 0.0 | **2 failed** |
| M19 | isolation latch never set | **4 failed** |
| M20 | start-write never sent | **12 failed** |

Revert proof after every mutation: `git checkout -- <file>` then
`git status --porcelain | wc -l` → `0`. Final state: `git status --porcelain` empty at
`e931c5d`, `uv run mypy .` output hash byte-identical to round three's
(`sha256:8797c603…`), confirming not one source byte changed.

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 70 |
| Tasks complete | 70 |
| Tasks incomplete | 0 |

### Build & Tests Execution

**Build**: ✅ Passed — `uv run mypy .` → `Success: no issues found in 58 source files`, exit 0

**Tests**: ✅ **248 passed**, 0 failed, 0 skipped, **0 warnings**, exit 0

```text
uv run --extra dev pytest        -> 248 passed in 25.98s   exit 0
uv run --extra dev pytest -n auto -> 248 passed in 11.02s   exit 0
uv run mypy .                    -> Success, 58 source files exit 0
uv run ruff check .              -> All checks passed!      exit 0
uv run ruff format --check .     -> 58 files already formatted
uv run deptry .                  -> No dependency issues found
test_architecture.py + test_plugin_imports.py -> 6 passed
```

248 is round three's 247 plus the one new `test_resolution.py` test; the second new guard
is an assertion block appended to an existing contract test, so it adds no test id.

**Coverage**: ➖ Not measured — by project decision (`coverage_threshold: 0`, no
`pytest-cov` in the dev extra). Not a gap.

### No production file moved — CONFIRMED

`git diff --name-status ft/session-lifecycle-04-heartbeat..HEAD` → 9 paths, **zero
`src/`**: 3 openspec files and 6 test files. Phases 5 and 6 and the orchestrator's two
guard commits closed coverage without touching shipped code, so none of them could have
introduced a regression. The identical mypy output hash across rounds three and four is
independent corroboration.

### Spec Compliance Matrix

#### run-recording (3 requirements, 14 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Run entry per invocation (RQ-1) | First invocation against an empty database | `test_run_report.py` | ✅ COMPLIANT |
| Run entry per invocation (RQ-1) | Second invocation gets a distinct identifier | `test_run_report.py` | ✅ COMPLIANT |
| Run entry per invocation (RQ-1) | Zero-test collection still writes a row | `test_run_report.py` | ✅ COMPLIANT |
| Run entry per invocation (RQ-1) | Failed collection still writes a row | `test_run_report.py` | ✅ COMPLIANT |
| Run entry per invocation (RQ-1) | A still-running session already has a run entry (RQ-1.5) | `test_run_report.py::test_a_still_running_session_already_has_a_run_entry` | ✅ COMPLIANT (M20) |
| Run entry per invocation (RQ-1) | A SIGKILL'd session's entry is present (RQ-1.6) | `test_run_report.py::test_sigkilled_session_leaves_a_start_time_null_end_time_and_no_interrupt_reason` | ✅ COMPLIANT (M20) |
| Run timestamps (RQ-31) | Completed session records both timestamps | `test_run_report.py::test_completed_session_writes_one_row_with_ordered_timestamps` | ✅ COMPLIANT (M6) |
| Run timestamps (RQ-31) | Interrupted session leaves a null end time | `test_run_report.py::test_sigint_leaves_start_time_and_null_end_time` | ✅ COMPLIANT (M6) |
| Run timestamps (RQ-31) | SIGKILL'd session carries no interrupt reason (RQ-31.3) | `test_run_report.py::test_sigkilled_session_…no_interrupt_reason` | ✅ COMPLIANT (M20) |
| Run atomicity (RQ-3) | Server killed mid-write (RQ-3.1) | Analysis + `test_start_write_reaches_storage_in_one_commit`, `test_finish_report_reaches_storage_in_one_commit` | ✅ COMPLIANT |
| Run atomicity (RQ-3) | Report truncated in transit, no prior start-write (RQ-3.2) | `test_rejection.py::test_truncated_body_raw_socket` | ✅ COMPLIANT |
| Run atomicity (RQ-3) | Finish report truncated after an accepted start-write (RQ-3.2) | `test_rejection.py::test_finish_report_truncated_after_an_accepted_start_write_leaves_the_start_row_intact` | ✅ COMPLIANT |
| Run atomicity (RQ-3) | Normal report is fully present (RQ-3.3) | `test_ingestion.py` | ✅ COMPLIANT |
| Run atomicity (RQ-3) | A reordered start-write never nulls a recorded finish | `test_rejection.py::test_reordered_start_write_never_nulls_a_recorded_finish` + both store twins | ✅ COMPLIANT (M8) |

#### session-ingestion (1 requirement, 6 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Malformed report rejection (RQ-42) | Missing required field (RQ-42.1) | `test_rejection.py` | ✅ COMPLIANT |
| Malformed report rejection (RQ-42) | Invalid JSON (RQ-42.2) | `test_rejection.py` | ✅ COMPLIANT |
| Malformed report rejection (RQ-42) | Body truncated midway, no prior report (RQ-42.3) | `test_rejection.py::test_truncated_body_raw_socket` | ✅ COMPLIANT |
| Malformed report rejection (RQ-42) | Finish report truncated after an accepted start-write | `test_rejection.py::test_finish_report_truncated_after_an_accepted_start_write_leaves_the_start_row_intact` | ✅ COMPLIANT |
| Malformed report rejection (RQ-42) | One malformed result rejects the whole report | `test_rejection.py` | ✅ COMPLIANT |
| Malformed report rejection (RQ-42) | Rejection names the cause, safely (RQ-42.4) | `test_rejection.py` | ✅ COMPLIANT |

#### session-liveness (5 requirements, 11 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Heartbeat endpoint | A heartbeat advances last contact | `test_ingestion.py::test_heartbeat_advances_last_contact_for_an_accepted_start_write` | ✅ COMPLIANT (M16) |
| Heartbeat endpoint | A heartbeat cannot touch finish fields | `test_ingestion.py::test_heartbeat_cannot_touch_finish_fields` **+ `vantage_port_contract.py` read-back, both adapters** | ✅ **COMPLIANT (was PARTIAL W7 — closed, mutation A)** |
| Activity-driven last-contact (RQ-25.2) | A long suite's last contact advances during execution | `test_run_report.py::test_a_suite_exceeding_one_heartbeat_interval_advances_the_servers_last_contact` | ✅ COMPLIANT (M9, M16) |
| Activity-driven last-contact (RQ-25.2) | A fast suite emits no heartbeat | `test_run_report.py::test_a_fast_suite_emits_no_heartbeat` | ✅ COMPLIANT (M18) |
| Single long test not observed mid-body | A single very long test can read as abandoned while alive | `test_liveness.py::test_a_run_past_its_grace_period_…` | ✅ COMPLIANT |
| Abandoned run is observable (RQ-44) | A run past its grace period derives as abandoned (RQ-44.1) | `test_liveness.py::test_a_run_past_its_grace_period_…` | ✅ COMPLIANT |
| Abandoned run is observable (RQ-44) | A run inside its grace period derives as running (RQ-44.2) | `test_liveness.py::test_a_run_inside_its_grace_period_derives_as_running` | ✅ COMPLIANT (M13) |
| Abandoned run is observable (RQ-44) | A Ctrl-C interrupted run derives as interrupted (RQ-44.3) | `test_liveness.py::test_an_interrupted_run_derives_as_interrupted_before_the_clock_is_consulted` | ✅ COMPLIANT (M15) |
| Abandoned run is observable (RQ-44) | Abandonment invents no stored field (RQ-44.4) | `vantage_port_contract.py` read-back (both adapters) + `test_schema_manifest.py` | ✅ **COMPLIANT (was PARTIAL W6 — closed, mutation A)** |
| Grace period server-side and configurable | Grace is measured from last contact, not start | `test_liveness.py::test_grace_is_measured_from_last_contact_not_from_start` | ✅ COMPLIANT (M13) |
| Grace period server-side and configurable | Grace period is configurable | `test_resolution.py::test_cli_grace_period_overrides_the_default` + `test_ingestion.py::test_create_app_exposes_the_configured_grace_period` + **`test_resolution.py::test_cli_main_carries_the_resolved_grace_period_into_the_app`** | ✅ **COMPLIANT (was PARTIAL W4 — closed, mutation B)** |

#### recording-fault-tolerance (1 requirement, 7 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Non-disruptive failure (RQ-21) | Passing suite survives an internal error | `test_failure_paths.py` | ✅ COMPLIANT |
| Non-disruptive failure (RQ-21) | Failing suite still reports failure | `test_failure_paths.py` | ✅ COMPLIANT |
| Non-disruptive failure (RQ-21) | Server accepts then closes without responding | `test_failure_paths.py` | ✅ COMPLIANT |
| Non-disruptive failure (RQ-21) | Server accepts and never answers | `test_failure_paths.py` | ✅ COMPLIANT |
| Non-disruptive failure (RQ-21) | Every hook is fault-isolated | `test_failure_paths.py::test_every_recorder_hook_is_fault_isolated` | ✅ COMPLIANT |
| Non-disruptive failure (RQ-21) | A failed heartbeat does not stop result recording | `test_failure_paths.py::test_heartbeat_failing_on_every_attempt_warns_once_and_every_result_is_still_recorded` | ✅ COMPLIANT (M3) |
| Non-disruptive failure (RQ-21) | A failed heartbeat warns once, not once per beat | same test + `test_start_write_and_heartbeat_failure_share_one_flag_leaving_two_warnings_total` | ✅ COMPLIANT (M19) |

#### recording-schema (1 requirement, 3 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Complete schema from first use (RQ-29) | Fresh database matches the column manifest | `test_schema_manifest.py` (9 tests) | ✅ COMPLIANT |
| Complete schema from first use (RQ-29) | Opening an existing database issues no schema-altering statement | `test_connection.py::test_opening_a_database_with_the_current_schema_version_succeeds_and_applies_no_ddl` | ✅ COMPLIANT (M17) |
| Complete schema from first use (RQ-29) | A database from an older schema version is refused, not altered | `test_connection.py::test_opening_a_database_with_an_older_schema_version_is_refused` + no-row and newer-version siblings | ✅ COMPLIANT (M17) |

**Compliance summary**: **41/41 scenarios compliant, 0 PARTIAL, 0 UNTESTED, 0 FAILING.**
**Requirements**: **11/11.**

Against round three's 38 / 3 PARTIAL / 0 UNTESTED: all three PARTIALs closed, each by a
mutation I re-ran myself rather than by a re-reading of the same evidence.

### TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | ✅ | apply-progress carries the cycle table for all six phases |
| All tasks have tests | ✅ | 70/70 |
| RED confirmed (tests exist) | ✅ | every named test file present and collected |
| GREEN confirmed (tests pass) | ✅ | 248/248 at runtime |
| Triangulation adequate | ✅ | the port contract runs both adapters; liveness has 7 cases across 4 precedence rules |
| Safety Net for modified files | ✅ | Phases 5/6 and both guard commits ran the full suite before landing |

**TDD Compliance**: 6/6. The two guard tests were substantiated by mutation before commit
and I independently reproduced both — the correct discipline for coverage-closing work
that has no natural RED.

### Test Layer Distribution

| Layer | Tests | Files | Tools |
|---|---|---|---|
| Unit | ~150 | 14 | pytest |
| Integration (in-process HTTP / real subprocess pytest) | ~98 | 8 | pytest, `pytester`, `httpx`, a real uvicorn server |
| E2E (browser) | 0 | 0 | not installed — not applicable to this project |
| **Total** | **248** | **22** | |

### Assertion Quality

| File | Line | Assertion | Issue | Severity |
|---|---|---|---|---|
| `packages/vantage/tests/test_sqlite_store.py` | 61 | `assert after_started_at == before_started_at` | Vacuous — the fixture reuses one `started` literal for both writes, so no `started_at` handling in `_UPSERT_RUN` can make the two differ (proven by M11) | WARNING |

**Assertion quality**: 0 CRITICAL, 1 WARNING. No tautologies, no ghost loops, no
smoke-tests-only, no assertions that never call production code. The new read-back block
in `vantage_port_contract.py` was checked specifically against the swallow-exception trap:
it reads back through `store.get_execution` outside any `try`, and its
`assert after is not None` is a real non-vacuity guard, not decoration.

### Quality Metrics

**Linter**: ✅ `ruff check` — all checks passed; `ruff format --check` — 58 files already formatted
**Type Checker**: ✅ `mypy` strict — no issues in 58 source files

### Coherence (Design)

| Decision | Followed? | Notes |
|---|---|---|
| D25 monotonic run upsert | ✅ | `WHERE run.exit_status IS NULL AND excluded.exit_status IS NOT NULL`, proven by M8 |
| D27 last contact from `received_at`, never advanced on finish | ✅ | proven by the `last_contact_at` third of the sqlite test |
| D29 two independent isolation latches | ✅ | proven by M19 (4 failed) |
| D30 activity-driven beat, one stall per interval | ✅ | delivered by the latch; the assignment order is an equivalent mutant (M2) |
| D31 liveness timeout `min(2.0, report_timeout)` | ⚠️ | honoured in code; guarded on the start-write (M12 red), unguarded on the beat (M5 silent) — W9 |
| D32 one `_started_at` for both writes | ✅ | proven by M20 |
| D33 heartbeat is its own monotonic UPDATE, 404 decided by `get_execution` | ✅ | proven by M14, M16, mutation A |
| D34 `app.state.grace_period` seam | ✅ | now proven end-to-end by mutation B |
| ADR-0013 refuse an older schema, never alter | ✅ | proven by M17 (4 failed) |

### Issues Found

**CRITICAL**: None.

**WARNING**

- **W12 (new)** — `test_sqlite_store.py::test_finish_write_leaves_received_at_started_at_and_last_contact_at_untouched`'s
  `started_at` assertion is vacuous; the "a finish-write never advances the recorded start
  time" invariant is unguarded on **both** adapters (M10, M11 both silent). Production is
  correct on both. Maps to no scenario. Closable by giving the finish-write in that test
  (and its memory twin) a different `started_at` from the start-write — roughly two lines.
- **W9 (carried)** — the beat's `_liveness_timeout` choice is unguarded (M5 silent) while
  the start-write's identical choice is guarded (M12 red). Requirement prose, not a
  scenario. One test away from symmetric.
- **W10 (carried, low)** — `test_a_still_running_session_already_has_a_run_entry` races a
  child process's 5 s sleep. Bounded and generous; watch it on loaded CI.
- **W8 (accounting, carried — must survive into the archive record)** — Phase 4's attempt
  was acquired retroactively, so the ledger recorded **0** changed lines. The 0 is an
  artefact, not a measurement. The real figure is **770 changed lines against a 500
  budget**. A recorded gap, not a passing check.
- **W11 (accounting, carried)** — round two's 576-line charge is the **orchestrator's
  budgeting error**, not an actor overrun: verify round two was given a 100-line budget
  while also required to write `verify-report.md`, and the orchestrator committed the
  Phase 6 task addendum while that attempt was still open.
- **W13 (process, new — recorded because a log containing only other people's mistakes is
  not a log)** — the orchestrator committed once with **`mypy` red**: an `&&` chain ending
  in `tail -1` swallowed the non-zero exit. It was caught on the next run and fixed in the
  following commit (`e931c5d`, "patch uvicorn.run by dotted path, not through the
  module"). The tree is green now and the defect never reached a verified state, but the
  gate was briefly trusted when it had not run.

**SUGGESTION**

- **S1 (carried)** — `_fixed_width_isoformat` has no direct test; it still trusts its
  caller for tz-awareness.
- **S4 (RETIRE, do not carry)** — round three logged M2 as a docstring-only claim. It is
  strictly stronger than that: a **provably equivalent mutant** no test can kill. Carrying
  it again would be inventing a concern.
- **S2 (carried, narrowed)** — the `__wrapped__` check in the fault-isolation test is
  cosmetic; the property that matters is guarded by M3 and M19.

### Success Criteria audit

1. **"`last_contact_at` advances and stops"** — ✅ **CLOSED.** Guarded end to end: M16 (the
   route records request time), M9 (the wire), mutation A (the touch alters nothing else).
2. **"no scenario in `openspec/specs/` contradicts shipped behaviour"** — open, and
   **closable only at archive, by design**. `openspec/specs/session-ingestion/spec.md:74,79,84,90`
   still says "the run table stays empty" and `openspec/specs/run-recording/spec.md:98`
   still says "no run entry … is present either". The MODIFIED deltas that correct both
   exist and are unmerged. Merging them **is** archive's job — this is not a verify defect
   and must not be treated as one.
3. **"Every slice under 500 changed lines"** — open. Phase 4 was 770. See W8.

### Still open by design — restated, not re-litigated

- A single very-long test produces no beat: accepted, documented, and carried in the spec
  with its own requirement heading and scenario (`specs/session-liveness/spec.md:57-69`).
- Write side only: RQ-44's read-back criteria are Analysis against the derivation helper,
  not Demonstration through a read API that does not exist yet.
- One liveness latch covers both the start-write and the beat: deliberate, proven by
  `test_start_write_and_heartbeat_failure_share_one_flag_leaving_two_warnings_total`.

### Verdict

**PASS WITH WARNINGS — archive-ready.**

41/41 scenarios carry runtime evidence, 11/11 requirements, 70/70 tasks, 0 CRITICAL, 0
blockers, every gate green at 248 passed with zero warnings, and not one production byte
changed since Phase 4. The six WARNINGs are two accounting records, one process record,
one flake watch, and two coverage gaps in defence-in-depth invariants whose production
code I read and mutated and found correct. None of them is a defect and none of them
blocks the merge.

Next: `sdd-archive` — which also discharges Success Criterion 2 by merging the five
MODIFIED deltas into `openspec/specs/`.
