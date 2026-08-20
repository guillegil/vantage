# Tasks: VCS capture — the plugin reads the repository once, the server records it

**Change:** `vcs-capture` · Strict TDD — every behavioural task names its
failing test first. Test command: `uv run --extra dev pytest`.

**No new `RQ-xx` identifiers are minted.** This change verifies RQ-10, RQ-23,
RQ-39 (new capability `version-control-context`), plus RQ-21, RQ-22, RQ-24,
RQ-25, RQ-26, RQ-29, RQ-30, RQ-41 already in force. Decisions D43–D52 in
`design.md` are settled and are not re-argued here — this file sequences
them and decomposes the design's test-layer plan (Testing Strategy table,
threat matrix) into tasks, not only its implementation decisions.

**Schema is unchanged.** All six `vcs_*` columns already exist, nullable,
correct defaults — no migration, no `schema_version` bump, ADR-0013's
refusal gate is not engaged.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,550 authored (design's own count: slice 1 ~430, slice 2 ~330, slice 3 ~260, slice 4 ~250, slice 5 ~280) |
| 400-line budget risk | Low per slice, High for the change as a whole |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 → PR 4 → PR 5 |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Low

Every slice is under the 500-line review budget; the change as a whole is
not, which is why it is chained. Slice boundaries and order are the
design's own (`Migration / Rollout`), not re-derived here. The design
forecasts **five** slices, not four — slice 3 came out at ~470 lines with
no headroom when tried at four, and `session-lifecycle`'s lesson was that
an under-forecast slice splits at apply time anyway.

### Suggested Work Units

Bases: PR 1 → tracker branch (`ft/vcs-capture`); PR *n* → PR *n−1* branch.

| Unit | Goal | PR | Forecast | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|----------|-----------------------|------------------|--------------------|
| 1 | `vcs.py` reader — argv, ordering, failure discrimination, environment (D43–D46) | PR 1 | ~430 | `uv run --extra dev pytest packages/pytest-vantage/tests/test_vcs.py` | N/A — `vcs.py` is standalone; nothing calls it yet, so there is no live-session scenario to run | Delete `vcs.py` and `test_vcs.py`; nothing else references the module |
| 2 | Recorder wiring — capture at `__init__`, non-latching isolation, both reports, exit-status both arms (D51) | PR 2 | ~330 | `uv run --extra dev pytest packages/pytest-vantage/tests/test_failure_paths.py packages/pytest-vantage/tests/test_run_report.py` | `uv run --extra dev pytest --vantage=<addr>` inside a real git repository against a live server — the start report already carries `vcs` | Revert removes the `vcs.capture` call and the `vcs` section from both reports; a report without the section is the pre-change shape and an unmodified server records exactly as today |
| 3 | Domain `VcsContext` + `merged_over`, `VcsReport`, `_to_execution` normalisation, `truncation.py` (D47, D49) | PR 3 | ~260 | `uv run --extra dev pytest packages/vantage/tests -k "execution or schemas or truncation"` | N/A — pure domain/wire unit tests; `_UPSERT_RUN` does not yet persist the six columns, so there is no end-to-end scenario until Unit 4 | Revert drops `VcsContext`, `VcsReport` and `truncation.py`; the route stops mapping a section that nothing persists yet |
| 4 | `_UPSERT_RUN` + `_row_to_execution` + memory adapter `merged_over` + port-contract COALESCE scenarios + ingestion endpoint acceptance (D48) | PR 4 | ~250 | `uv run --extra dev pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py packages/vantage/tests/test_ingestion.py` | `uv run --extra dev pytest --vantage=<addr>` against a live sqlite-backed server; POST a session report carrying `vcs` and read the six columns back via `get_execution` | Revert reverts `_UPSERT_RUN` to 8 columns and the memory adapter's per-object coalesce; a `vcs` section is still parsed (Unit 3) but silently dropped, matching Unit 3's end state |
| 5 | RQ-25 measurement, ADR-0014, schema-manifest, traceability (D52) | PR 5 | ~280 | `uv run --extra dev pytest` (full gate) | N/A — `scripts/measure_vcs_overhead.py` is a manual harness, not a pytest scenario; docs/ADR changes have no runtime harness | Revert drops the harness script, the ADR and the manifest edits; behaviour is unaffected, only documentation reverts |

---

## Phase 1: The reader — `vcs.py` (PR 1)

New files: `packages/pytest-vantage/src/pytest_vantage/vcs.py`,
`packages/pytest-vantage/tests/test_vcs.py`. Real repositories built via
`subprocess`/`tmp_path`, never mocked git output (spec's own verification
method for RQ-10 and RQ-39).

- [x] 1.1 RED `test_vcs.py::test_dirty_tracked_file_marks_run_dirty` — two
      arms in one repo family: a worktree-modified tracked file, and a
      staged-only change. *(Scenario: Dirty working tree is marked dirty,
      RQ-10.1)*
- [x] 1.2 RED `test_vcs.py::test_clean_tree_matches_independent_head_read` —
      assert `commit` equals `git rev-parse HEAD` read independently of the
      module under test. *(Scenario: Clean working tree matches an
      independent read, RQ-10.2)*
- [x] 1.3 RED `test_vcs.py::test_detached_head_records_commit_null_branch` —
      `git checkout <sha>`; assert commit recorded, branch null.
      *(Scenario: Detached HEAD records the commit with a null branch,
      RQ-10.3)*
- [x] 1.4 RED `test_vcs.py::test_no_commits_yet_stores_null_commit` —
      `git init` only; assert `commit is None` **and** invocation 4
      (`git show`) is never spawned (assert on the patched
      `subprocess.run` call count). *(Scenario: A repository with no
      commits yet stores a null commit, RQ-10.4)*
- [x] 1.5 RED
      `test_vcs.py::test_not_a_repository_records_nulls_and_no_warning` —
      bare `tmp_path`; assert all five fields `None` and `warning is None`.
      *(Scenarios: Not a git repository records nulls; Absent repository
      emits no warning — both RQ-23.1)*
- [x] 1.6 RED
      `test_vcs.py::test_corrupt_git_entry_records_nulls_and_warns_once` —
      two real fixtures, not a mock: a `.git` **file** containing garbage,
      and a `.git` **directory** with a truncated `HEAD`; assert all-null
      **and** exactly one warning naming the cause, discriminated from
      "not a repository" via `(rootpath / ".git").exists()`, never stderr
      text. *(Scenarios: A `.git` entry that is not a valid repository
      records nulls, RQ-39.1; A corrupt repository warns exactly once)*
- [x] 1.7 RED
      `test_vcs.py::test_missing_git_executable_records_nulls_silently` —
      `monkeypatch.setenv("PATH", str(tmp_path / "empty"))` for real;
      **never** `mock.patch("subprocess.run")` — a mock proves the mock,
      not `FileNotFoundError`. Assert nulls and no warning. *(Scenario: No
      git executable on PATH records nulls silently, RQ-39.2)*
- [x] 1.8 RED `test_vcs.py::test_permissions_restricted_repository` —
      **Inspection, `skipif(os.geteuid() == 0)`**, never counted as Test:
      `chmod 000` the `.git` directory; assert all-null. Skip, not pass
      vacuously, when running as root. *(Scenario: A permissions-restricted
      repository, skip-if-root)*
- [x] 1.9 RED `test_vcs.py::test_monorepo_subdirectory_records_toplevel` —
      rootdir inside a subdirectory records git's `--show-toplevel`, not
      `config.rootpath` (supplementary — Testing Strategy row "Monorepo
      root", not one of the 18 spec scenarios).
- [x] 1.10 RED `test_vcs.py::test_argv_discipline` — **Inspection**: every
      invocation is a literal list, `shell=False`, `cwd=rootpath`,
      `stdin=DEVNULL`; no value derived from the repository or the
      environment is ever an argv element (supplementary, Testing Strategy
      row "Argv discipline").
- [x] 1.11 RED `test_vcs.py::test_hung_git_bounded_at_capture_level` — a
      fake `git` shim on `PATH` that sleeps; assert `capture()` returns
      inside the 5 s budget with an all-null snapshot (supplementary,
      component-level precursor to the session-level scenario in Phase 2).
- [x] 1.12 RED
      `test_vcs.py::test_whole_capture_budget_not_per_invocation` — a shim
      sleeping 3 s on every invocation; assert **one** shared
      `time.monotonic()` deadline is consumed, not five independent 5 s
      timeouts (supplementary — proves D44's "budget for the whole
      capture, not per invocation").
- [x] 1.13 GREEN `vcs.py`: `VcsSnapshot` (frozen, slots dataclass);
      `capture(rootpath) -> VcsSnapshot`, never raises — the five
      invocations in order (D44), env override of the six hazardous keys
      (D46), the exhaustive exception table (D43), the `.git`-exists
      discriminator (D45). Stdlib only: `subprocess`, `shutil`, `os`,
      `time`, `dataclasses`, `pathlib`.
- [x] 1.14 GREEN gate:
      `uv run --extra dev pytest packages/pytest-vantage/tests/test_vcs.py`,
      `uv run mypy .` clean, confirm `vcs.py` imports nothing but stdlib
      (`rg -n '^import|^from' packages/pytest-vantage/src/pytest_vantage/vcs.py`,
      RQ-24).

## Phase 2: Recorder wiring — non-latching isolation, both reports (PR 2)

Modifies `packages/pytest-vantage/src/pytest_vantage/recorder.py`.
`plugin.py` and `boundary.py` are **unchanged** (D43, D51) — confirmed by
reading, not assumed.

- [x] 2.1 RED `test_run_report.py`: `_vcs_section()` serialises the
      **identical** snapshot on both `pytest_sessionstart` and
      `pytest_sessionfinish` reports — the snapshot is captured once in
      `__init__` and never re-read (precursor to D51's idempotent upsert).
- [x] 2.2 RED `test_failure_paths.py::test_git_failure_disables_nothing_else`
      — **mutation-shaped**: `vcs.capture` patched to raise (acceptable
      here per the spec's own verification note — the object under test is
      the isolation boundary, not git's behaviour). Assert pytest exits 0,
      every test result and heartbeat for the session is still recorded,
      `_disabled` and `_liveness_disabled` remain `False`, **and** the run
      row itself is stored with all six vcs fields null — the isolation
      boundary is proven by the session surviving with nulls, not merely by
      not crashing. *(Scenario: A git failure disables nothing else in the
      same session)*
- [x] 2.3 RED
      `test_failure_paths.py::test_hung_git_does_not_delay_session` — a
      fake `git` shim on `PATH` that sleeps past 5 s, wired through a real
      `Recorder`; assert the session completes without delay beyond the
      capture budget and the run is stored with all six vcs fields null.
      *(Scenario: A hung git is bounded at five seconds)*
- [x] 2.4 RED
      `test_run_report.py::test_passing_suite_exit_status_survives_unreadable_repository`
      — an unreadable-repository fixture (reuse Phase 1's corrupt-`.git`
      fixture) wired through a real `Recorder`; a suite of passing tests;
      assert pytest exits 0. *(Scenario: A passing suite's exit status
      survives an unreadable repository, RQ-39.3)*
- [x] 2.5 RED
      `test_run_report.py::test_failing_suite_exit_status_survives_unreadable_repository`
      — same fixture, one failing test; assert pytest exits 1. *(Scenario:
      A failing suite's exit status survives an unreadable repository,
      RQ-39.3)*
- [x] 2.6 GREEN `recorder.py`: `Recorder.__init__` calls
      `vcs.capture(Path(str(config.rootpath)))`, wrapped in its own
      non-latching `try/except Exception` — distinct from `boundary.py`'s
      `fault_isolated`/`liveness_isolated`, no `_disabled` flag, every call
      independent (the ADDED `recording-fault-tolerance` requirement's own
      wording) — so a capture that somehow escapes its internal handling
      still degrades to an empty snapshot and one warning rather than
      reaching pytest's `wrap_session`. Warns at most once via the existing
      `_warn` when `self._vcs.warning is not None`. One private
      `_vcs_section()` serialises the held snapshot for both reports (D51).
- [x] 2.7 Confirm, **by reading**, `plugin.py`'s xdist controller-only guard
      (`if hasattr(config, "workerinput"): return`) still precedes
      `Recorder` construction after this slice's hook additions — no
      worker ever spawns `git` (D36, D51). No edit expected; record that
      the invariant still holds.
- [x] 2.8 GREEN gate: `uv run --extra dev pytest packages/pytest-vantage`,
      confirm ≤5 `git` invocations per session and zero per test (RQ-25
      process-count check), confirm the plugin still imports nothing but
      pytest and the standard library (RQ-24).

## Phase 3: Domain and wire — `VcsContext`, `VcsReport`, truncation (PR 3)

Modifies `packages/vantage/src/vantage/core/domain/execution.py`,
`packages/vantage/src/vantage/service/schemas.py`,
`packages/vantage/src/vantage/service/routes/runs.py`. Creates
`packages/vantage/src/vantage/service/truncation.py`. No spec scenario is
owed by this phase alone — it lands the wire and the domain merge that
Phase 4's endpoint- and storage-level scenarios need.

- [x] 3.1 RED domain test module: `VcsContext.merged_over(previous)`
      coalesces **per field**, null → value only, never value → null; a
      partial incoming snapshot does not clobber a fuller previous one —
      the in-memory mirror of D48's SQL `COALESCE`, proven independently
      before the adapter uses it.
- [x] 3.2 RED `test_schemas.py`: `VcsReport` is `extra="forbid"` — an
      unknown field inside the `vcs` section is rejected; `commit:
      Field(max_length=64)` accepts a 64-hex SHA-256 value, never a
      40-hex pattern.
- [x] 3.3 RED `test_truncation.py` (create): `truncate()` — a 100 KiB
      subject truncates to ≤64 KiB of UTF-8 with the flag `True`; a 1 KiB
      subject stores whole with the flag `False`; a multi-byte character
      sitting on the 64 KiB boundary is never split.
- [x] 3.4 RED `test_truncation.py`: the plugin's own cap sits **above**
      the server's bound — a subject between 64 KiB and 64 KiB + 1 KiB
      still arrives long enough that the server sets the truncation flag;
      assert this directly (the D49 false-zero defect this change is
      built to avoid).
- [x] 3.5 RED routes test module: `_to_execution` maps `vcs=None` when the
      section is absent **or** every field in it is null (unit-level
      precursor to Phase 4's endpoint-level scenarios).
- [x] 3.6 GREEN `execution.py`: `VcsContext` frozen/slots dataclass +
      `merged_over`; `Execution.vcs: VcsContext | None = None` appended
      with a default — every existing construction site keeps working.
- [x] 3.7 GREEN `schemas.py`: `VcsReport(BaseModel)`,
      `extra="forbid"`, five required fields, `commit: str | None =
      Field(max_length=64)`; `SessionReport.vcs: VcsReport | None = None`.
- [x] 3.8 GREEN `truncation.py` (create): `MAX_TEXT_FIELD_BYTES = 64 *
      1024`; `truncate(value: str | None) -> tuple[str | None, bool]` —
      encode UTF-8, slice at the byte bound, `decode(errors="ignore")`,
      never split a multi-byte character.
- [x] 3.9 GREEN `routes/runs.py`: `_to_execution` normalises the `vcs`
      section per the all-null test (3.5) and applies `truncate()` to
      `commit_subject`, setting `commit_subject_truncated`.
- [x] 3.10 GREEN gate:
      `uv run --extra dev pytest packages/vantage/tests -k "execution or schemas or truncation"`,
      `uv run mypy .` clean, confirm `vantage.core` gains no new import
      (RQ-26 AST architecture test still passes).

## Phase 4: Storage, conflict branch, ingestion endpoint (PR 4)

Modifies `packages/vantage/src/vantage/storage/sqlite_store.py`,
`packages/vantage/src/vantage/storage/memory.py`,
`packages/vantage/tests/vantage_port_contract.py`. **Depends on Phase 3.**

- [x] 4.1 RED `vantage_port_contract.py`: a second report with a null
      `vcs` section leaves recorded vcs values intact (COALESCE semantics,
      both adapters).
- [x] 4.2 RED `vantage_port_contract.py`: a start-then-finish pair with
      identical vcs snapshots applies once (idempotent, both adapters).
- [x] 4.3 RED `vantage_port_contract.py`: a reordered start-write still
      changes nothing, vcs columns included (both adapters).
- [x] 4.4 RED `test_sqlite_store.py`: `typeof(vcs_branch) = 'null'` after a
      run recorded outside a repository — asserted via SQL `typeof`, never
      falsy-equality, which `''` would also satisfy.
- [x] 4.5 RED `vantage_port_contract.py`: `vcs=None` normalisation — an
      absent section and an all-null section both read back
      `execution.vcs is None`, in **both** adapters (D48's two
      normalisation rules, proven at the contract level so they cannot
      drift apart).
- [x] 4.6 RED
      `test_ingestion.py::test_report_with_vcs_section_persists_six_fields`
      — POST `/api/v1/runs` with a `vcs` section against an empty
      database; assert the run entry holds the reported commit, branch,
      commit subject and dirty flag. *(Scenario: A report carrying a vcs
      section persists its six fields)*
- [x] 4.7 RED
      `test_ingestion.py::test_report_without_vcs_section_still_records_run`
      — a well-formed report with no `vcs` key (the pre-change plugin
      shape); assert the run row holds all six vcs fields null and the
      response acknowledges it, rather than the report being rejected.
      *(Scenario: A report with no vcs section still records its run)*
- [x] 4.8 RED
      `test_ingestion.py::test_vcs_section_accepted_without_capability_check`
      — a running server that has never advertised a vcs-related
      capability still accepts and stores a `vcs` section, no prior probe
      required. *(Scenario: The endpoint accepts a vcs section without any
      capability check)*
- [x] 4.9 RED `vantage_port_contract.py` or `test_sqlite_store.py` —
      **Inspection, awaiting `read-api`**: a run recorded from a directory
      that is not a git repository is retrievable via `count_executions`
      and `get_execution` with all six vcs fields null; the test's own
      docstring/comment states this stands in for "appears in a run list"
      only, and is **not claimed as met** until `read-api` exposes an
      actual list. *(Scenario: Absent repository's run is retrievable in
      storage, pending a run list, RQ-23.2)*
- [x] 4.10 GREEN `sqlite_store.py`: `_UPSERT_RUN` 8 → 14 columns; insert
      branch writes all six `vcs_*`; conflict branch
      `COALESCE(excluded.vcs_*, run.vcs_*)` per column, `CASE` on
      `vcs_commit_subject_truncated` keyed to whether
      `excluded.vcs_commit_subject IS NOT NULL`, under the unchanged
      `exit_status` WHERE (D48).
- [x] 4.11 GREEN `sqlite_store.py`: `_SELECT_RUN`/`_row_to_execution` reads
      the six columns back into `VcsContext | None`, the same all-null
      test as `_to_execution` (3.5).
- [x] 4.12 GREEN `memory.py`: **own task, not folded into 4.10/4.11** — the
      conflict path calls `VcsContext.merged_over` instead of `stored.vcs
      if execution.vcs is None else execution.vcs`; the latter diverges
      from per-column `COALESCE` the moment one report carries a partial
      snapshot, and this was the exact bug class missing from the last
      change's affected-areas table.
- [x] 4.13 GREEN gate:
      `uv run --extra dev pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py packages/vantage/tests/test_ingestion.py`,
      `uv run mypy .` clean.

## Phase 5: Measurement, ADR, docs, traceability (PR 5)

- [x] 5.1 Write `scripts/measure_vcs_overhead.py` (create) — five paired
      runs interleaved (A/B/A/B…), medians not means; two profiles
      (1,000 × ~10 ms and 1,000 × ~1 ms, RQ-25's own profiles); two
      repositories (this repository, and a synthetic repository with
      ≥20,000 tracked files, generated — synthetic data only, per
      CLAUDE.md); reports the git cost separately from the report cost,
      and `--untracked-files=no` separately from default `git status`, so
      D44's flag choice is justified by a number.
- [x] 5.2 Run the harness by hand (not in CI); transcribe the **medians**,
      the machine, the git version, the Python version and the date as a
      **Measurements** paragraph into
      `openspec/changes/vcs-capture/specs/version-control-context/spec.md`
      — a number in the spec, not a `print()` — alongside the
      pre-measurement forecast (~10–60 ms once per session; ~0.6% of the
      10 s profile, ~6% of the 1 s profile) so the recorded result can
      disagree with it. State that a future change to the argv or
      invocation count MUST re-run it.
- [ ] 5.3 Write
      `docs/adr/0014-execute-git-from-the-plugin-as-a-bounded-fail-closed-subprocess.md`
      — Nygard, `Status: Proposed` in the PR, imperative title, linked to
      ADR-9 and to RQ-10/RQ-23/RQ-39; record the rejected `.git`-parser
      alternative and its reversal cost (D52).
- [ ] 5.4 Update `docs/schema-manifest.md`: move the five `vcs_*` rows from
      reserved/unpopulated to populated; confirm `vcs_commit_subject`
      keeps its `†` marking the `vcs_commit_subject_truncated` sibling, so
      five rows correctly cover six columns, matching the manifest's own
      existing dagger convention.
- [ ] 5.5 Run
      `test_schema_manifest.py::test_fresh_database_matches_the_recorded_ground_truth`
      and confirm it needs **no** new column/index counts — the schema is
      unchanged, only queries changed (D48).
- [ ] 5.6 RQ-24 regression: run the existing clean-environment install
      check; confirm `pytest-vantage` still depends on pytest and the
      standard library only (`subprocess`/`shutil` are stdlib) — no new
      distribution, installing the plugin still never drags the server in.
- [ ] 5.7 RQ-26 regression: run the existing AST architecture test;
      confirm `VcsContext` adds no import to `vantage.core`.
- [ ] 5.8 Traceability sweep:
      `rg "RQ-10|RQ-23|RQ-39|RQ-21|RQ-24|RQ-25|RQ-26|RQ-22|RQ-30|RQ-29|RQ-41"`
      across the new and modified test files; confirm every marker
      resolves to a real test and that no new `RQ-xx` identifier was
      minted anywhere in this change.
- [ ] 5.9 Final gate: `uv run ruff format . && uv run ruff check --fix .`,
      `uv run mypy .`, `uv run deptry .`; run `uv run --extra dev pytest`
      locally on the interpreter available in this environment and the
      `-n auto` xdist path; **state explicitly** which of the 3.10–3.13
      matrix legs, the networking-disabled RQ-28 job, and the
      clean-environment RQ-24 install check ran locally versus were left
      to CI — do not claim a matrix run that did not happen.

---

## Scenario coverage — all 18 spec scenarios

| # | Scenario | Capability | Task |
|---|----------|-----------|------|
| 1 | Dirty working tree is marked dirty (RQ-10.1) | version-control-context | 1.1 |
| 2 | Clean working tree matches an independent read (RQ-10.2) | version-control-context | 1.2 |
| 3 | Detached HEAD records the commit with a null branch (RQ-10.3) | version-control-context | 1.3 |
| 4 | A repository with no commits yet stores a null commit (RQ-10.4) | version-control-context | 1.4 |
| 5 | Not a git repository records nulls (RQ-23.1) | version-control-context | 1.5 |
| 6 | Absent repository emits no warning (RQ-23.1) | version-control-context | 1.5 |
| 7 | Absent repository's run is retrievable in storage, pending a run list (RQ-23.2, Inspection) | version-control-context | 4.9 |
| 8 | A `.git` entry that is not a valid repository records nulls (RQ-39.1) | version-control-context | 1.6 |
| 9 | A corrupt repository warns exactly once | version-control-context | 1.6 |
| 10 | No git executable on PATH records nulls silently (RQ-39.2) | version-control-context | 1.7 |
| 11 | A passing suite's exit status survives an unreadable repository (RQ-39.3) | version-control-context | 2.4 |
| 12 | A failing suite's exit status survives an unreadable repository (RQ-39.3) | version-control-context | 2.5 |
| 13 | A permissions-restricted repository, skip-if-root | version-control-context | 1.8 |
| 14 | A git failure disables nothing else in the same session | recording-fault-tolerance | 2.2 |
| 15 | A hung git is bounded at five seconds | recording-fault-tolerance | 2.3 |
| 16 | A report carrying a vcs section persists its six fields | session-ingestion | 4.6 |
| 17 | A report with no vcs section still records its run | session-ingestion | 4.7 |
| 18 | The endpoint accepts a vcs section without any capability check | session-ingestion | 4.8 |

**All 18 spec scenarios have a task that produces their test.** None are
missing. Scenario 7 (RQ-23.2) is intentionally Inspection, not Test, and its
task explicitly records that it is not claimed as met until `read-api`
lands — per the spec's own text, not a gap in this file.
