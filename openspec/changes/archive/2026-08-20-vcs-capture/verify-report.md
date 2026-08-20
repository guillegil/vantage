```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:33b4b12e32c682fcc203c3b8e909102ca78363e223ff18b470850c9f602346e0
verdict: pass
blockers: 0
critical_findings: 0
requirements: 5/5
scenarios: 18/18
test_command: uv run --extra dev pytest
test_exit_code: 0
test_output_hash: sha256:f32230a6f75119e9d135a07d8a235676fb3c17f442fe14d8c5595ee85651456a
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:e01191578d3a3bac202cef72aec747f3048def999e4a0906368a5b91b8849682
```

## Verification Report

**Change**: `vcs-capture`
**Version**: N/A (no spec version declared)
**Mode**: Strict TDD
**Branch**: `ft/vcs-capture-05-measurement` @ `a4fca4d` (tip of the five-slice chain)
**Verified**: 2026-08-20

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 54 |
| Tasks complete | 54 |
| Tasks incomplete | 0 |
| Requirements | 5 |
| Scenarios | 18 |

Task counts confirmed against `openspec/changes/vcs-capture/tasks.md` (Phase 1:
1.1–1.14 = 14; Phase 2: 2.1–2.8 = 8; Phase 3: 3.1–3.10 = 10; Phase 4: 4.1–4.13
= 13; Phase 5: 5.1–5.9 = 9). Requirement and scenario counts derived from the
three delta specs, not from the tasks file's own claim: `version-control-context`
3 requirements / 13 scenarios, `session-ingestion` 1 / 3,
`recording-fault-tolerance` 1 / 2.

### Build & Tests Execution

**Tests**: 326 passed, 0 failed, 0 skipped, **0 warnings**

```text
$ uv run --extra dev pytest
326 passed in 50.19s                                     (exit 0)

$ uv run --extra dev pytest -n auto
326 passed in 12.10s                                     (exit 0)

$ uv run --python 3.10 --extra dev pytest -q
326 passed in 50.71s                                     (exit 0)
```

**Build / static analysis**: all green

```text
$ uv run mypy .
Success: no issues found in 66 source files              (exit 0)

$ uv run ruff check .
All checks passed!                                       (exit 0)

$ uv run ruff format --check .
66 files already formatted                               (exit 0)

$ uv run deptry .
Scanning 65 files... Success! No dependency issues found. (exit 0)
```

**Coverage**: ➖ Not available. `pytest-cov` is absent from the dev extra and the
lockfile, and `openspec/config.yaml` sets `coverage_threshold: 0` deliberately
(CLAUDE.md, "Coverage is not measured"). Not a failure.

**Named regression checks**

| Check | Result | Evidence |
|-------|--------|----------|
| `packages/vantage/tests/test_architecture.py` | ✅ 4 passed | RQ-26 AST guard; `vantage.core` gains no import |
| `packages/pytest-vantage/tests/test_plugin_imports.py` | ✅ 2 passed | RQ-24 import guard |
| `schema.sql` byte-identical **to `main`** | ✅ | `sha256 bc55898f…49942` on both `main:packages/vantage/src/vantage/storage/schema.sql` and the working copy; `git diff main...HEAD -- schema.sql` is empty |
| `test_schema_manifest.py` | ✅ 9 passed | no new column/index counts needed — schema unchanged, only queries changed |
| RQ-24 clean-environment install | ✅ **run locally** | `uv build --package pytest-vantage` → fresh `uv venv --python 3.12` with pytest → before/after `uv pip list --format=freeze` diff adds exactly one line: `pytest-vantage==0.1.0`. The server is not dragged in. |
| RQ-28 networking-disabled job | ➖ **left to CI**, as stated | `.github/workflows/ci.yml:80–192` needs `sudo groupadd`/`iptables`; not attempted in this sandbox |
| 3.11 / 3.12 / 3.13 matrix legs | ➖ left to CI this round | 3.10 spot-checked locally and green; apply-progress's claim that all four ran is consistent with the 3.10 result |

### Spec Compliance Matrix

Every scenario was traced to a named test and that test was confirmed to pass at
runtime. The tasks file's 1:1 claim was **checked, not trusted** — each row below
was resolved to a concrete test function, then mutation-probed where the mapping
was load-bearing.

| # | Scenario | Capability | Covering test | Result |
|---|----------|-----------|---------------|--------|
| 1 | Dirty working tree is marked dirty (RQ-10.1) | version-control-context | `test_vcs.py::test_dirty_tracked_file_marks_run_dirty` | ✅ COMPLIANT |
| 2 | Clean working tree matches an independent read (RQ-10.2) | version-control-context | `test_vcs.py::test_clean_tree_matches_independent_head_read` | ✅ COMPLIANT |
| 3 | Detached HEAD records the commit with a null branch (RQ-10.3) | version-control-context | `test_vcs.py::test_detached_head_records_commit_null_branch` | ✅ COMPLIANT |
| 4 | A repository with no commits yet stores a null commit (RQ-10.4) | version-control-context | `test_vcs.py::test_no_commits_yet_stores_null_commit` | ✅ COMPLIANT |
| 5 | Not a git repository records nulls (RQ-23.1) | version-control-context | `test_vcs.py::test_not_a_repository_records_nulls_and_no_warning` | ✅ COMPLIANT |
| 6 | Absent repository emits no warning (RQ-23.1) | version-control-context | same test, `assert snapshot.warning is None` | ✅ COMPLIANT |
| 7 | Absent repository's run is retrievable in storage (RQ-23.2) | version-control-context | `vantage_port_contract.py::test_absent_repository_run_is_retrievable_in_storage_pending_a_run_list` (both adapters) | ✅ COMPLIANT (Inspection — **not claimed as met**, see below) |
| 8 | A `.git` entry that is not a valid repository records nulls (RQ-39.1) | version-control-context | `test_vcs.py::test_corrupt_git_entry_records_nulls_and_warns_once` | ✅ COMPLIANT |
| 9 | A corrupt repository warns exactly once | version-control-context | same test (snapshot level) **and** `test_run_report.py::test_passing_suite_exit_status_survives_unreadable_repository`, which asserts `output.count("VantageWarning:") == 1` at session level | ✅ COMPLIANT |
| 10 | No git executable on PATH records nulls silently (RQ-39.2) | version-control-context | `test_vcs.py::test_missing_git_executable_records_nulls_silently` — real `monkeypatch.setenv("PATH", …)`, no `mock.patch("subprocess.run")` | ✅ COMPLIANT |
| 11 | A passing suite's exit status survives an unreadable repository (RQ-39.3) | version-control-context | `test_run_report.py::test_passing_suite_exit_status_survives_unreadable_repository` | ✅ COMPLIANT |
| 12 | A failing suite's exit status survives an unreadable repository (RQ-39.3) | version-control-context | `test_run_report.py::test_failing_suite_exit_status_survives_unreadable_repository` | ✅ COMPLIANT |
| 13 | A permissions-restricted repository, skip-if-root | version-control-context | `test_vcs.py::test_permissions_restricted_repository` | ✅ COMPLIANT (Inspection — **it actually ran**, see below) |
| 14 | A git failure disables nothing else in the same session | recording-fault-tolerance | `test_failure_paths.py::test_git_failure_disables_nothing_else` | ✅ COMPLIANT |
| 15 | A hung git is bounded at five seconds | recording-fault-tolerance | `test_failure_paths.py::test_hung_git_does_not_delay_session` (+ `test_vcs.py::test_hung_git_bounded_at_capture_level` as component precursor) | ✅ COMPLIANT |
| 16 | A report carrying a vcs section persists its six fields | session-ingestion | `test_ingestion.py::test_report_with_vcs_section_persists_six_fields` | ✅ COMPLIANT |
| 17 | A report with no vcs section still records its run | session-ingestion | `test_ingestion.py::test_report_without_vcs_section_still_records_run` | ✅ COMPLIANT |
| 18 | The endpoint accepts a vcs section without any capability check | session-ingestion | `test_ingestion.py::test_vcs_section_accepted_without_capability_check` | ✅ COMPLIANT |

**Compliance summary**: 18/18 scenarios compliant. 0 UNTESTED, 0 FAILING, 0 PARTIAL.

### Adversarial Evidence — Mutation Log

Every mutation was applied to **production source**, the full suite was run under
`-n auto`, and the file was restored from an in-memory copy of the original bytes
with a byte-equality assertion. After all 34 mutations, `git status --porcelain`
is empty and `git diff HEAD --quiet` exits 0 — **the tree is identical to `a4fca4d`**.

**29 of 34 mutations died. 5 survived.**

| # | Mutation | Outcome | Killed by |
|---|----------|---------|-----------|
| M1 | Remove the `exit_status` WHERE guard from `_UPSERT_RUN` | ☠️ died | 4 tests, incl. `test_sqlite_store.py::…test_reordered_start_after_finish_never_nulls_vcs_columns` |
| M2 | Per-column `COALESCE` → unconditional `excluded.vcs_*` | ☠️ died | `…test_second_report_with_null_vcs_section_leaves_recorded_vcs_intact` |
| **M3** | **memory adapter `merged_over` → whole-object coalesce** | **🧟 SURVIVED** | — |
| M4 | Remove `_bounded_subject` | ☠️ died | **exactly one** test: `test_vcs.py::test_a_huge_commit_subject_is_bounded_before_it_reaches_the_wire` |
| M5 | Invocation 2 (`rev-parse HEAD`) failure aborts the whole capture | ☠️ died | `test_no_commits_yet_stores_null_commit`, `test_whole_capture_budget_not_per_invocation` |
| M6 | Drop `--untracked-files=no` | ☠️ died | `test_dirty_tracked_file_marks_run_dirty` (third arm) |
| M7 | sqlite `_row_to_vcs_context` never returns `None` | ☠️ died | 6 tests |
| M8 | memory `_normalized_vcs` never returns `None` | ☠️ died | `test_memory_store.py::…test_vcs_none_normalizes_the_same_whether_absent_or_all_null` |
| M9 | route `_to_vcs_context` never returns `None` for all-null | ☠️ died | `test_routes_runs.py::test_to_execution_maps_vcs_none_when_every_field_in_the_section_is_null` |
| M10 | Recorder `_capture_vcs` loses its `try/except` | ☠️ died | `test_git_failure_disables_nothing_else` |
| M11 | Recorder never emits the vcs warning | ☠️ died | 3 tests |
| M12 | Recorder re-reads vcs inside `_vcs_section()` | ☠️ died | `test_vcs_section_is_identical_on_both_reports`, `test_hung_git_does_not_delay_session` |
| M13 | `truncate()` slices characters, not bytes | ☠️ died | `test_truncate_never_splits_a_multi_byte_character_on_the_boundary` |
| M14 | `VcsReport` `extra="forbid"` → `"ignore"` | ☠️ died | `test_vcs_report_rejects_an_unknown_field_inside_the_section` |
| M15 | `commit` `max_length` 64 → 40 | ☠️ died | `test_vcs_report_accepts_a_sha256_commit_sixty_four_hex_characters` |
| **M16** | **`vcs_commit_subject_truncated` `CASE` → plain `excluded.…`** | **🧟 SURVIVED** | — |
| M17 | `merged_over` truncated flag decoupled from subject | ☠️ died | `test_vcs_context_merged_over_truncated_flag_travels_with_commit_subject` |
| M18 | `vcs_dirty` unknown written as `0`, not `NULL` | ☠️ died | `…test_vcs_none_normalizes_the_same_whether_absent_or_all_null` |
| M19 | Remove the `shutil.which` early return | ☠️ died | `test_missing_git_executable_records_nulls_silently` |
| M20 | Warn even when `.git` does not exist | ☠️ died | 11 tests |
| M21 | Plugin cap lowered to the server's 64 KiB bound (D49 false zero) | ☠️ died | `test_a_huge_commit_subject_is_bounded_before_it_reaches_the_wire` |
| M22 | Shared deadline → per-invocation budget | ☠️ died | `test_whole_capture_budget_not_per_invocation` |
| M23 | `vcs` dropped from the start report | ☠️ died | `test_vcs_section_is_identical_on_both_reports` |
| M24 | `vcs` dropped from the finish report | ☠️ died | 3 tests |
| M25 | `dirty` always `False` | ☠️ died | `test_dirty_tracked_file_marks_run_dirty` |
| M26 | `_field` ignores `returncode` (fails open) | ☠️ died | `test_detached_head_…`, `test_no_commits_yet_…` |
| M27 | `git show` gate removed — invocation 4 always spawned | ☠️ died | `test_no_commits_yet_stores_null_commit` |
| **M28** | **D46 environment overrides dropped entirely** | **🧟 SURVIVED** | — |
| **M29** | **Recorder captures from `Path(".")` instead of `config.rootpath`** | **🧟 SURVIVED** | — |
| **M30** | **`_vcs_columns(None)` writes truncated flag `1` instead of `0`** | **🧟 SURVIVED** | — |
| M31 | sqlite insert branch writes no vcs | ☠️ died | 3 tests |
| M32 | memory create branch drops vcs | ☠️ died | 4 tests |
| M33 | Capture budget 5 s → 300 s | ☠️ died | 3 tests |
| M34 | `SessionReport` drops the `vcs` field | ☠️ died | 43 tests |

**Answers to the specific mutations requested**

- **The `exit_status` WHERE guard (M1)** is proven for the vcs columns. Removing
  it fails `test_reordered_start_after_finish_never_nulls_vcs_columns` by name,
  alongside three pre-existing guards. The guard is not unproven.
- **The per-column `COALESCE` (M2)** is proven. Replacing it with
  `excluded.vcs_*` unconditionally fails
  `test_second_report_with_null_vcs_section_leaves_recorded_vcs_intact`: a second
  report with a null section does not clobber recorded values.
- **The in-memory adapter's `merged_over` call (M3) is present but NOT guarded.**
  See CRITICAL/WARNING section below — this is the headline finding.
- **The plugin's subject bound (M4)** — removing `_bounded_subject` fails
  **exactly one** test, as predicted:
  `test_a_huge_commit_subject_is_bounded_before_it_reaches_the_wire`.
- **All-or-nothing gate vs field-by-field (M5)** — making invocation 2's failure
  abort the whole capture **is** caught by the "repository with no commits"
  scenario (`test_no_commits_yet_stores_null_commit`), plus one more.
- **`--untracked-files=no` (M6)** — changing it to the default **is** noticed.
  `test_dirty_tracked_file_marks_run_dirty`'s third arm creates an untracked-only
  file and asserts `dirty is False`; the default `git status --porcelain` reports
  `?? untracked.txt`, so `dirty` flips to `True` and the test fails. **The flag
  choice is justified by a test, not only by a measurement** — contrary to the
  hypothesis in the brief. The measurement additionally justifies it on cost.

### Issues Found

**CRITICAL**: None.

**WARNING**

1. **W1 — `memory.py`'s `merged_over` call is unguarded (M3 survived).**
   Replacing `execution.vcs.merged_over(stored.vcs)` with the whole-object
   coalesce `stored.vcs if execution.vcs is None else execution.vcs` — the exact
   defect `tasks.md` 4.12 says it exists to prevent, quoting it as *"the exact bug
   class missing from the last change's affected-areas table"* — leaves all 326
   tests green.

   The production code is **correct**; only the proof is missing. Verified
   directly against both adapters with a start-write carrying a full snapshot and
   a finish-write carrying a *partial* one (`commit` set, the other four null):

   | Tree | memory | sqlite | |
   |---|---|---|---|
   | current `a4fca4d` | `branch='main', commit_subject='Long subject', dirty=False, root='/repo'` | identical | **AGREE** |
   | with M3 applied | `branch=None, commit_subject=None, dirty=None, root=None` | `branch='main', …` | **DIVERGE** |

   Root cause: all five vcs port-contract tests use either an *identical* snapshot
   or `vcs=None` on the finish-write. Neither discriminates a per-field merge from
   a whole-object swap. **One added contract test — a partial incoming snapshot
   over a fuller stored one — kills M3.** Under Strict TDD, task 4.12 is a GREEN
   step with no RED partner that pins its behaviour.

2. **W2 — the `vcs_commit_subject_truncated` `CASE` is unguarded (M16 survived),
   and it hides a real false zero.** Replacing the `CASE` with a plain
   `excluded.vcs_commit_subject_truncated` leaves all 326 tests green, yet
   produces exactly the D49 defect class the design is written to avoid: a
   finish-write carrying no `vcs` section keeps the long `commit_subject` via
   `COALESCE` while resetting its flag to `0`.

   Measured with M16 applied, start snapshot `commit_subject='Long subject',
   commit_subject_truncated=True`, finish `vcs=None`:
   `sqlite → commit_subject='Long subject', commit_subject_truncated=False`.
   A stored truncated value reporting `truncated = 0`.

   Root cause is the same fixture weakness: `vantage_port_contract.py`'s `_vcs()`
   helper defaults `commit_subject_truncated=False`, so both branches of the
   `CASE` return the same value.
   **Changing that one fixture default to `True` in the existing
   `test_second_report_with_null_vcs_section_leaves_recorded_vcs_intact` kills M16.**

   Neither W1 nor W2 is reachable through today's plugin, which captures once and
   sends the *identical* snapshot on both reports (D51). Both are reachable the
   moment any writer sends a partial or asymmetric snapshot — which is precisely
   what the port contract exists to keep safe.

3. **W3 — `Recorder`'s capture root is unproven (M29 survived).** D51 names
   `config.rootpath` explicitly, but changing `_capture_vcs(Path(str(config.rootpath)))`
   to `_capture_vcs(Path("."))` leaves all 326 tests green, because `pytester`
   always runs with `cwd == rootpath`. `test_monorepo_subdirectory_records_toplevel`
   proves `capture()` resolves `--show-toplevel` correctly, but it calls the module
   directly and never exercises the Recorder's choice of argument. Low practical
   impact — both paths usually resolve to the same toplevel — but the wiring
   decision is asserted nowhere.

**SUGGESTION**

1. **S1 — D46's environment overrides have no covering test (M28 survived).**
   Dropping `env.update(_ENV_OVERRIDES)` entirely changes no test outcome. Most of
   the six keys are genuinely hard to test (they prevent prompting and paging),
   but `GIT_OPTIONAL_LOCKS=0` is cheaply testable: assert `.git/index` is not
   rewritten by a `capture()` on a clean repository.

2. **S2 — one assertion is weaker than it looks (M30 survived).**
   `test_sqlite_store.py::test_vcs_branch_is_sql_null_not_empty_string_for_a_run_outside_a_repository`
   asserts `typeof(vcs_commit_subject_truncated) == "integer"` but never its
   *value*, so writing `1` instead of `0` for a run outside a repository passes.
   Currently unobservable through the public API (all-null normalisation returns
   `None` before the flag is read), so impact is confined to direct SQL readers —
   which `read-api` will be. Add `assert truncated_value == 0`.

3. **S3 — duplicated decorator.** `@pytest.mark.slow` is applied twice on
   `packages/pytest-vantage/tests/test_vcs.py:279–280` and `:297–298`. Harmless
   and lint-clean, but unintended.

4. **S4 — spec wording overstates what the schema permits.** Four places say the
   run is stored with *"all six vcs fields null"*
   (`version-control-context/spec.md` RQ-23 and RQ-39.1/39.2/permissions;
   `session-ingestion/spec.md` scenario 2). `vcs_commit_subject_truncated` is
   `INTEGER NOT NULL DEFAULT 0` in the unchanged `schema.sql`, so it can never be
   SQL `NULL`. The tests are **honest about this** —
   `test_vcs_branch_is_sql_null_…` asserts five `"null"` types and comments that
   the sixth *"is `INTEGER NOT NULL DEFAULT 0` — unlike its five siblings it is
   never SQL NULL"*. Only the prose is imprecise; "five value fields null" would
   be exact.

5. **S5 — the brief's count of new `slow` tests is off by one.** This change adds
   **three** slow-marked tests (`test_hung_git_bounded_at_capture_level`,
   `test_whole_capture_budget_not_per_invocation`,
   `test_hung_git_does_not_delay_session`), not two. The fourth slow-marked test
   in the tree, `test_setup_and_call_durations_are_measured_independently`,
   pre-exists on `main`.

### Known-and-accepted items — confirmed represented honestly

| Item | Confirmed |
|---|---|
| **RQ-23 criterion 2 cannot be verified here** | ✅ The spec says so in its own body (`version-control-context/spec.md:53–59`, *"cannot be demonstrated by this change… is not claimed as met"*), and the test's **own docstring** repeats it verbatim: *"**Inspection, awaiting `read-api`**: this stands in for 'the run appears in a run list' only… It is NOT claimed as met for RQ-23.2 until `read-api` exposes an actual list."* Not overclaimed anywhere. |
| **Permissions case is Inspection with skip-if-root** | ✅ Guarded by `@pytest.mark.skipif(os.geteuid() == 0 …, reason="chmod 000 is a no-op as root; skip rather than pass vacuously")`. **It did not skip in this run** — this machine's EUID is 1000, the pytest output shows 14 dots and zero `s` for `test_vcs.py`, and the whole suite reported 0 skipped. Proven non-vacuous by direct probe: the same repository yields `commit='49de6948…', branch='main', dirty=False` before `chmod 000` and all-nulls with `warning='could not read the git repository'` after. |
| **RQ-28 networking-disabled job left to CI** | ✅ Stated, not hidden, in apply-progress. The job exists at `.github/workflows/ci.yml:80–192` and needs `sudo groupadd`/`iptables`. Not run locally; not claimed. |
| **Measurement partly contradicted its own forecast** | ✅ The spec carries **both** numbers. Forecast (`~10–60 ms`, `~0.6%` of the 10 s profile, `~6%` of the 1 s profile) at `spec.md:145–147`; the measured `1.50%` synthetic 10 ms result and both sub-forecast 1 ms results (`4.11%`, `4.17%`) in the table at `:138–143`; and an explicit paragraph at `:149–158` headed *"The result disagrees with the forecast in both directions, and is recorded as measured, not adjusted to match it."* The re-run obligation is stated at `:160–163`: *"A future change to `vcs.py`'s argv or invocation count MUST re-run `scripts/measure_vcs_overhead.py` and update this paragraph."* All four measured cases remain inside RQ-25's 2% budget. |

### Also-check items

| Item | Result |
|---|---|
| No new `RQ-xx` identifier minted | ✅ The highest identifier anywhere in `git diff main...HEAD` is **RQ-44**. Repo-wide, `RQ-45` occurs once — in CLAUDE.md's own *"do not invent `RQ-45`"* warning — and `RQ-9999` once, in a previously archived verify report's `-m` selector example. Neither is a minted requirement. |
| Measurement harness not collected by pytest | ✅ `scripts/measure_vcs_overhead.py` contains no `test_*` function and no `Test*` class; `uv run --extra dev pytest scripts/ --collect-only` reports *"no tests collected"*, and the full `--collect-only` has zero `measure_vcs` matches. |
| `slow` markers keep the default run complete | ✅ `slow` is registered in the single root `[tool.pytest.ini_options]` with `--strict-markers` on, documented *"Opt OUT with `-m 'not slow'`, never opt in."* Default run collects **326/326**; `-m 'not slow'` deselects 4; `-m slow` selects 4. Nothing is opt-in. |

### Correctness (Static Evidence)

| Requirement | Status | Notes |
|------------|--------|-------|
| Readable repository context (RQ-10) | ✅ Implemented | `vcs.py::capture` — five invocations in D44's order; real-repository fixtures, no mocked git stdout |
| Absent repository (RQ-23) | ✅ Implemented | Gate failure + `.git` non-existence → `_EMPTY`, silent. Criterion 2 explicitly deferred, not claimed |
| Unreadable repository (RQ-39) | ✅ Implemented | `.git`-exists discriminator (D45), never stderr text; exit status untouched on both arms |
| Optional VCS section acceptance | ✅ Implemented | `SessionReport.vcs: VcsReport | None = None`, `extra="ignore"` envelope; no capability gate exists or is required |
| VCS capture isolation | ✅ Implemented | `_capture_vcs` is a third, non-latching path; `boundary.py` and `plugin.py` are **byte-unchanged** (`git diff main...HEAD` on both is empty) |

### Coherence (Design D43–D52)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| D43 — `vcs.py` is its own fail-closed boundary | ✅ Yes | Exhaustive exception table; `boundary.py` untouched |
| D44 — five invocations, one 5 s shared budget | ✅ Yes | Single `time.monotonic()` deadline; M22 and M33 both die |
| D45 — `.git`-exists discriminator, never stderr | ✅ Yes | M20 dies against 11 tests |
| D46 — inherit env, override hazardous keys | ⚠️ Implemented, untested | See S1 (M28 survived) |
| D47 — five fields on the wire, six columns in the row | ✅ Yes | `VcsReport` five required fields; server owns the flag |
| D48 — per-column coalesce, nulls never clobber | ⚠️ Implemented, partly untested | SQL side proven (M2 dies); memory side and the `CASE` unproven (W1, W2) |
| D49 — 64 KiB on bytes at the server, plugin cap above it | ✅ Yes | M13 and M21 both die |
| D50 — container / NFS / other Python / prompting git | ➖ Narrative | No testable assertion claimed |
| D51 — capture at `Recorder.__init__`, plugin.py unchanged | ⚠️ Mostly | Single capture and identical sections proven (M12, M23, M24 die); the `config.rootpath` argument itself is unproven (W3). xdist controller-only guard confirmed by reading: `if hasattr(config, "workerinput"): return` is the first statement of `plugin.py:164`, still preceding `Recorder` construction |
| D52 — exactly one ADR | ✅ Yes | `docs/adr/0014-…md` exists, Nygard + `Alternatives rejected`, `Status: Proposed`, imperative title, links ADR-4/ADR-9 and RQ-10/RQ-23/RQ-39, records the `.git`-parser alternative with its (unlabelled but stated) growing-cost argument. Authored in the design commit `a4199f4` rather than by task 5.3; apply-progress reports this honestly and no second ADR was created |

### TDD Compliance

| Check | Result | Details |
|-------|--------|---------|
| TDD evidence reported | ✅ | apply-progress `#80` carries per-phase RED/GREEN narrative and gate results |
| All behavioural tasks have tests | ✅ | 45/45 RED-or-GREEN behavioural tasks resolve to real test functions; Phases 1–4 |
| RED confirmed (test files exist) | ✅ | 4 new test modules + 4 modified, all present and collected |
| GREEN confirmed (tests pass now) | ✅ | 326/326 at runtime, three interpreters/execution modes |
| Triangulation adequate | ✅ | e.g. `test_dirty_tracked_file_marks_run_dirty` carries 3 arms; `truncate` is parametrised across the server bound and the plugin cap |
| Discriminating RED for every GREEN | ⚠️ | Task 4.12's GREEN (`merged_over` in `memory.py`) has no RED that discriminates it — W1 |
| Phase 5 TDD | ➖ N/A, declared | `tasks.md`'s own work-unit table states "Runtime harness: N/A" for PR 5; the harness is a manual script proven by execution, and apply-progress labels this "Standard-mode evidence, not Strict TDD" rather than claiming a cycle |

**TDD compliance**: 6/7 checks passed.

### Test Layer Distribution (this change's new tests)

| Layer | Tests | Files | Tools |
|-------|-------|-------|-------|
| Unit (pure domain / wire / helper) | 26 | `test_execution.py`, `test_schemas.py`, `test_truncation.py`, `test_routes_runs.py` | pytest |
| Integration (real git subprocess, real adapters, TestClient) | 24 | `test_vcs.py`, `test_ingestion.py`, `test_sqlite_store.py`, `vantage_port_contract.py` (×2 adapters) | pytest, `tmp_path`, real `git` |
| End-to-end (`pytester` session against a live server) | 7 | `test_run_report.py`, `test_failure_paths.py` | `pytest.Pytester`, `VantageTestServer` |

No browser/E2E tooling is applicable to this project.

### Assertion Quality

No tautologies, no ghost loops, no assertion-without-production-call, and no
smoke-test-only cases were found. Two weaknesses, both already recorded above:

| File | Line | Assertion | Issue | Severity |
|------|------|-----------|-------|----------|
| `packages/vantage/tests/test_sqlite_store.py` | ~140 | `assert truncated_type == "integer"` | Type-only; never asserts the value, so `1` passes where `0` is meant | SUGGESTION (S2) |
| `packages/vantage/tests/vantage_port_contract.py` | `_vcs()` helper | `commit_subject_truncated: bool = False` | Fixture default makes both `CASE` branches identical, so the branch is never discriminated | WARNING (W2) |

Positive notes: `test_missing_git_executable_records_nulls_silently` scrubs `PATH`
for real instead of patching `subprocess.run`, exactly as the spec's verification
note demands; `_CallRecorder` forwards to the real `subprocess.run` rather than
fabricating stdout; `test_argv_discipline` opens with
`assert recorder.calls, "no subprocess.run calls recorded — the inspection would
pass vacuously"`, closing its own vacuity hole; and
`test_passing_suite_exit_status_survives_unreadable_repository` asserts
`output.count("VantageWarning:") == 1` specifically so a vacuous pass (git never
invoked) cannot masquerade as a real recovery.

### Quality Metrics

**Linter**: ✅ `ruff check` — all checks passed; `ruff format --check` — 66 files already formatted
**Type checker**: ✅ `mypy` — no issues in 66 source files
**Dependency hygiene**: ✅ `deptry` — no issues across 65 files

### Scope note

`git diff main...HEAD` includes `openspec/changes/read-api/proposal.md` (commit
`8e66851`). That is **not** `vcs-capture` scope creep: this chain branches off
`ft/read-api-proposal`, as `proposal.md:232` states, so the read-api proposal is
an ancestor that has not yet reached `main`. Expected for a feature-branch chain.

### Verdict

**PASS WITH WARNINGS** — all 54 tasks are genuinely complete, all 18 spec
scenarios trace to a real covering test that passed at runtime, every gate is
green (326 passed / 0 failed / 0 skipped / **0 warnings**, mypy, ruff, deptry),
`schema.sql` is byte-identical to `main`, and 29 of 34 adversarial mutations were
killed. The three warnings are **test-coverage gaps, not defects**: the
production code was independently confirmed correct in each case. Nothing blocks
archive.

**This change is archive-ready.**
