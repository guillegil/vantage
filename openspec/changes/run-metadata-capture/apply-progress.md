# Apply Progress: run-metadata-capture

**Mode**: Strict TDD from Phase 2 onward (Phase 1 was the documented
Strict-TDD exception — doc-only, no code path existed yet, per tasks.md's
own work-unit table). Phases 2, 3 and 4 are implemented under Strict TDD
in full.

## Scope covered so far

- Phase 1 (PR1 → tracker `ft/run-metadata-capture`) — complete.
- Phase 2 (PR2 → PR1) — complete.
- Phase 3 (PR3 → PR2) — complete.
- Phase 4 (PR4 → PR3) — complete.
- Phase 5 (PR5 → PR4) — complete, this batch, **re-sliced into PR5a and
  PR5b** (see Phase 5's own note in `tasks.md`): PR5a (flag + resolver,
  → PR4) and PR5b (C1/C2/C3 + Q3, → PR5a) are both landed.
- Phases 6-11 are untouched and remain for future `sdd-apply` batches.

## Completed Tasks

### Phase 1 (PR1)

- [x] 1.1 Write `docs/adr/0017-store-user-declared-configuration-values-read-from-the-test-repository.md`. Nygard + `Alternatives rejected` (ADR-0016's shape). `Status: Proposed`.
- [x] 1.2 Decision section: what is authorised (declared top-level scalars only, never file bodies, D-k deferred); the five conditions C1-C5 held together, inheriting ADR-0016's register; the EAV justification (D-e); the must-not-fail-the-run rule (D97); what is not authorised (host env, arbitrary bodies, server-directed reads, web editing, backfill).
- [x] 1.3 Consequences section: read exposure stated not mitigated; reversal cost (`schema_version` 3->4, refuse not migrate, ADR-0013); RQ-25 O(1)-measured obligation; unbounded growth named not solved; Q2's horizon published not implied. Bind to ADR-0013, ADR-0014, ADR-0016, RQ-2/24/25/26/28/29/44, and `run-metadata`/`opt-in-activation`/`session-ingestion`/`recording-schema`/`history-read-api`.
- [x] 1.4 PR description: `Status` flips to `Accepted` on merge (CLAUDE.md); no test surface, this PR is Inspection-only.

### Phase 2 (PR2)

- [x] 2.1 RED: extend `test_connection.py` — opening a database stamped `meta.schema_version='3'` with this build is refused, naming both versions and the path (ADR-0013 proven).
- [x] 2.2 RED: update `test_schema_manifest.py:216-221` literals 11/130/14 -> 13/139/15 (failed until 2.3).
- [x] 2.3 GREEN: `schema.sql` — added `run_metadata_file`, `run_metadata`, `idx_run_metadata_key_value` (D91) between `user_setting` and `-- Indexes`; updated header counts and the index-block comment; stamp `'4'`.
- [x] 2.4 GREEN: `connection.py` — `_SCHEMA_VERSION = 4`.
- [x] 2.5 GREEN: `docs/schema-manifest.md` — two new `###` sections (`run_metadata_file`, `run_metadata`, column-for-column) and corrected header counts (D91).
- [x] 2.6 Verified 2.1-2.2 pass; ran `uv run pytest packages/vantage/tests/test_schema_manifest.py packages/vantage/tests/test_connection.py` — 20 passed.
- [x] 2.7 PR description flags `docs/schema-manifest.md:364-403`'s pre-existing "Table count 10"/"Index count 13" drift (2026-08-15) as NOT this PR's obligation.

### Phase 3 (PR3)

- [x] 3.1 RED: `packages/vantage/tests/test_metadata.py` — `FILE_STATUSES`/`KEY_STATUSES` asserted as plain-`str` `frozenset`s, membership matching D91's SQL `CHECK` lists exactly; bounds asserted against their derived values. Confirmed failing: `ModuleNotFoundError: No module named 'vantage.core.domain.metadata'`.
- [x] 3.2 GREEN: created `packages/vantage/src/vantage/core/domain/metadata.py` — `FILE_STATUSES` (8 values), `KEY_STATUSES` (5 values), `MAX_METADATA_VALUE_BYTES=1024`, `MAX_METADATA_KEY_CHARS=1024`, `MAX_METADATA_ENTRIES=200` (D94, D95). No logic beyond vocabulary (RQ-26).
- [x] 3.3 RED+GREEN: extended `test_architecture.py::test_the_walk_is_not_vacuous` with an assertion that `vantage/core/domain/metadata.py` is examined by the RQ-26 purity walk. Confirmed RED by temporarily removing the module and re-running the test (failed as expected); confirmed GREEN with the module restored.

### Phase 4 (PR4)

- [x] 4.1 RED: `test_storage_types.py` — `MetadataFile`, `MetadataEntry`, `RunMetadata`, `EMPTY_RUN_METADATA` asserted frozen, `slots=True`, and `RunMetadata() == EMPTY_RUN_METADATA`. Confirmed failing: `ImportError: cannot import name 'EMPTY_RUN_METADATA' from 'vantage.core.ports.storage'`.
- [x] 4.2 GREEN: `core/ports/storage.py` — added `MetadataFile`, `MetadataEntry`, `RunMetadata`, `EMPTY_RUN_METADATA`; `ExecutionStore.record_session` gained `metadata: RunMetadata = EMPTY_RUN_METADATA` (D98). No existing call site changed — verified by `mypy --strict` passing unchanged and no edits to `routes/runs.py` or `scripts/measure_history_latency.py`.
- [x] 4.3 RED: `vantage_port_contract.py` — 5 new shared contract tests (round-trip persists both tables; D95 declared-but-dropped row with `value IS NULL`; replay is a no-op; finish-only session matches a start+finish pair; no-`metadata=` argument writes zero rows) plus two adapter-agnostic introspection helpers (`_stored_metadata_files`, `_stored_metadata_entries` — no port read method for these tables exists until Phase 10, D100). Confirmed failing on both adapters: `TypeError: record_session() got an unexpected keyword argument 'metadata'` / `AttributeError: 'InMemoryExecutionStore' object has no attribute '_metadata_files'` — 9 failures total (5 tests × 2 adapters minus the no-argument test on SQLite, which passed trivially against SQLite's already-created empty tables).
- [x] 4.4 GREEN: `sqlite_store.py` — `_INSERT_METADATA_FILE`/`_INSERT_METADATA_ENTRY` (`INSERT OR IGNORE`) plus `_metadata_file_rows`/`_metadata_entry_rows` helpers, appended inside the existing `BEGIN IMMEDIATE … COMMIT` after the result insert (D98).
- [x] 4.5 GREEN: `memory.py` — `self._metadata_files`/`self._metadata_entries` dicts keyed `(run_id, source_file)`/`(run_id, key)`, populated with `setdefault` mirroring `OR IGNORE`; both cleared in `close()`.
- [x] 4.6 Verified: `uv run pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_storage_types.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py` — 139 passed (was 120 before this batch).

### Phase 5 (PR5a) — flag + resolver

- [x] 5.1 RED: `test_config.py` — 9 new tests for `resolve_metadata_capture` (monotone decreasing in activation ×4, monotone increasing in `cli_opt_in` ×2, exhaustive truth table, no-opt-in default, no ini/env signature). Confirmed failing: `ImportError: cannot import name 'resolve_metadata_capture' from 'pytest_vantage.config'`.
- [x] 5.2 GREEN: `pytest_vantage/config.py` — `resolve_metadata_capture(*, activated, cli_opt_in)`, the identical monotone conjunction `resolve_failure_text_capture` uses; added to `__all__`.
- [x] 5.4 RED then GREEN: `test_opt_in.py`'s `_UnactivatedConfig`/`test_metadata_capture_requested_short_circuits_when_not_activated`. Confirmed failing: `ImportError: cannot import name '_metadata_capture_requested' from 'pytest_vantage.plugin'`. GREEN: `pytest_vantage/plugin.py` — `--vantage-metadata` registered in the existing `group.addoption` block (identical help-text shape to `--vantage-failure-text`, actively denying an ini equivalent); `_metadata_capture_requested`, short-circuited on `_activation_requested`.
- [x] 5.7 (signature half) GREEN: `pytest_vantage/recorder.py` — `Recorder.__init__` gained `metadata_requested: bool = False`, stored as `self._metadata_requested` but not yet consulted; `plugin.py`'s `pytest_configure` wires `metadata_requested=_metadata_capture_requested(config)` into the `Recorder(...)` construction on the controller only.

**Deviation from the design's literal wording, recorded not silently applied**: D99's prose says `_metadata_capture_requested` is "called on both xdist branches." It is not, in this implementation — only from the controller. `test_xdist_guard.py`'s `_WorkerConfigDouble.getoption` raises `AssertionError` for any option name outside `{"vantage", "capture", "vantage_failure_text"}`; reading `"vantage_metadata"` there would break that existing safety-net test, which is deliberately built to catch exactly this kind of scope creep (its own docstring: "the strongest available proof that the worker path reads exactly those things and touches nothing past them"). Unlike `_failure_text_capture_requested`, there is no per-worker consumer of the metadata opt-in in this slice (no `EvidenceCollector` equivalent registers on a worker for metadata) — nothing on a worker would ever act on the value even if it were read there. The declaration is still read exactly once per session regardless of worker count, because no `Recorder` is ever constructed on a worker at all (`plugin.py:214-217`), which is the property D99's paragraph was actually protecting.

### Phase 5 (PR5b) — differential, `--help` denial, Q3

- [x] 5.3 RED then GREEN: `test_opt_in.py` — C1 differential (`test_project_tree_is_byte_identical_with_a_metadata_declaration_present_but_the_flag_absent`, byte-identical tree with a `vantage-metadata.json` present and no flags; `test_no_connection_is_attempted_with_a_metadata_declaration_present_but_no_flags`, socket-level half) and C3 (`test_the_shipped_help_text_advertises_no_ini_equivalent_for_metadata`). All three ran green immediately against PR5a's already-landed flag registration — the flag's mere presence is what C1/C3 prove, and PR5a already registers it; no new production code was needed for these three, which is itself evidence the flag is correctly inert.
- [x] 5.5 RED: `test_declaration_is_not_opened_when_metadata_capture_was_not_requested` / `test_declaration_is_opened_when_metadata_capture_was_requested` (C2) — a plain-function `Path.open` call recorder (not a callable class instance: an instance is not a descriptor, so `path_instance.open(...)` would silently fail to bind `path_instance` as the first argument; a plain function is). Confirmed the "is opened" half failing against PR5a's `Recorder` (which accepted `metadata_requested` but never opened anything): `assert False` — zero paths recorded. The "not opened" half passed trivially against the same code (correct — a true negative).
- [x] 5.6 RED: `test_recorder_warns_exactly_once_when_metadata_requested_and_declaration_absent` / `test_recorder_emits_no_warning_when_metadata_requested_and_declaration_present` (Q3). Confirmed the "warns" half failing: `assert 0 == 1` — zero `VantageWarning`s recorded. The "no warning" half passed trivially (true negative).
- [x] 5.7 (behaviour half) GREEN: `pytest_vantage/recorder.py` — `_METADATA_DECLARATION_FILENAME`, `_metadata_declaration_missing_warning(rootpath)` (opens the file directly, TOCTOU-safe like `resolve_declared_path` will be, discards content, returns a message or `None`); `Recorder.__init__` calls it when `metadata_requested` is `True` and warns once via `_warn` if the declaration is missing.
- [x] 5.8 Verified: `uv run pytest packages/pytest-vantage/tests/test_config.py packages/pytest-vantage/tests/test_opt_in.py packages/pytest-vantage/tests/test_vcs.py` — 45 passed (see Work Unit Evidence below).

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `docs/adr/0017-store-user-declared-configuration-values-read-from-the-test-repository.md` | Created (PR1) | 204-line Nygard ADR, `Status: Proposed` |
| `packages/vantage/src/vantage/storage/schema.sql` | Modified (PR2) | Added `run_metadata_file`, `run_metadata`, `idx_run_metadata_key_value`; corrected header/index-block counts (thirteen tables, fifteen indexes); stamped `'4'` |
| `packages/vantage/src/vantage/storage/connection.py` | Modified (PR2) | `_SCHEMA_VERSION = 3` -> `4` |
| `packages/vantage/tests/test_connection.py` | Modified (PR2) | New RED-first refusal test `test_opening_a_database_created_by_the_previous_schema_version_is_refused` (`@pytest.mark.req(id="RQ-29")`); updated the three pre-existing version-literal tests to move with the bump |
| `packages/vantage/tests/test_schema_manifest.py` | Modified (PR2) | Ground-truth literals 11/130/14 -> 13/139/15 |
| `docs/schema-manifest.md` | Modified (PR2) | Two new `###` table sections; "Eleven tables, fourteen indexes" -> "Thirteen tables, fifteen indexes"; index list gained item 15 |
| `packages/vantage/src/vantage/core/domain/metadata.py` | Created (PR3) | `FILE_STATUSES`, `KEY_STATUSES`, `MAX_METADATA_VALUE_BYTES`, `MAX_METADATA_KEY_CHARS`, `MAX_METADATA_ENTRIES` — pure vocabulary, stdlib only |
| `packages/vantage/tests/test_metadata.py` | Created (PR3) | 7 tests: two CHECK-mirroring membership tests, two `frozenset`-of-`str`-never-`Enum` tests, three bound-value tests |
| `packages/vantage/tests/test_architecture.py` | Modified (PR3) | Added assertion that `vantage/core/domain/metadata.py` is examined by the RQ-26 purity walk |
| `openspec/changes/run-metadata-capture/tasks.md` | Modified (PR1 + PR2 + PR3 + PR4) | Phase 1/2/3/4 tasks marked `[x]` |
| `openspec/changes/run-metadata-capture/apply-progress.md` | Modified (each batch) | This artifact, mirrored on disk (hybrid store) |
| `packages/vantage/src/vantage/core/ports/storage.py` | Modified (PR4) | `MetadataFile`, `MetadataEntry`, `RunMetadata`, `EMPTY_RUN_METADATA`; `record_session`'s defaulted `metadata=` keyword |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Modified (PR4) | `_INSERT_METADATA_FILE`/`_INSERT_METADATA_ENTRY` + row-builder helpers, appended inside the existing transaction |
| `packages/vantage/src/vantage/storage/memory.py` | Modified (PR4) | `_metadata_files`/`_metadata_entries` dicts with `setdefault`, mirroring `OR IGNORE` |
| `packages/vantage/tests/test_storage_types.py` | Modified (PR4) | 9 new dataclass tests for `MetadataFile`/`MetadataEntry`/`RunMetadata`/`EMPTY_RUN_METADATA` |
| `packages/vantage/tests/vantage_port_contract.py` | Modified (PR4) | 5 new shared contract tests + 2 adapter-agnostic introspection helpers |
| `packages/pytest-vantage/src/pytest_vantage/config.py` | Modified (PR5a) | `resolve_metadata_capture(*, activated, cli_opt_in)`, added to `__all__` |
| `packages/pytest-vantage/src/pytest_vantage/plugin.py` | Modified (PR5a) | `--vantage-metadata` registered; `_metadata_capture_requested`; wired into the `Recorder(...)` construction |
| `packages/pytest-vantage/src/pytest_vantage/recorder.py` | Modified (PR5a) | `Recorder.__init__` gained `metadata_requested: bool = False`, stored, not yet acted on |
| `packages/pytest-vantage/tests/test_config.py` | Modified (PR5a) | 9 new tests for `resolve_metadata_capture` |
| `packages/pytest-vantage/tests/test_opt_in.py` | Modified (PR5a) | `_UnactivatedConfig` + the short-circuit gate test |
| `openspec/changes/run-metadata-capture/tasks.md` | Modified (PR5) | Phase 5 tasks marked `[x]`, re-slicing note added |
| `packages/pytest-vantage/src/pytest_vantage/recorder.py` | Modified (PR5b) | `_METADATA_DECLARATION_FILENAME`, `_metadata_declaration_missing_warning`; `Recorder.__init__` now acts on `metadata_requested` |
| `packages/pytest-vantage/tests/test_opt_in.py` | Modified (PR5b) | C1 (tree-identity + zero-connection), C2 (`Path.open` call recorder, both halves), C3 (`--help` denial), Q3 (warn-once + no-warning-when-present) |

## Work Unit Evidence (PR3)

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest packages/vantage/tests/test_metadata.py` — 7 passed; `uv run pytest packages/vantage/tests/test_metadata.py packages/vantage/tests/test_architecture.py` — 11 passed |
| Runtime harness command/scenario and result | N/A — pure, no I/O, no caller yet (per the work-unit table); this module is vocabulary only, wired to a port and adapter starting PR4 |
| Rollback boundary | Revert `core/domain/metadata.py` and `test_metadata.py`; nothing else in the tree imports the module yet (RQ-26 purity walk assertion is the only coupling, and it reverts with the same commit) |
| Full-suite regression check | `uv run ruff format . && uv run ruff check --fix .` — clean, 87 files unchanged; `uv run mypy .` (strict) — no issues, 87 files; `uv run pytest` — 600 passed (was 593 before this batch — 7 new tests); `uv run deptry .` — no dependency issues |

## Work Unit Evidence (PR4)

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_storage_types.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py` — 139 passed (was 120 before this batch) |
| Runtime harness command/scenario and result | N/A — no runtime boundary: `record_session` is exercised directly by the shared adapter contract against both real SQLite (a temp-dir file, `test_sqlite_store.py`'s existing fixture) and the in-memory adapter; the plugin/HTTP wire path this feeds does not exist until PR9 wires `routes/runs.py` |
| Rollback boundary | Revert `core/ports/storage.py`'s three dataclasses + `EMPTY_RUN_METADATA` + the `record_session` signature change, `sqlite_store.py`'s two `INSERT OR IGNORE` statements + helpers, `memory.py`'s two dicts, and the new test methods in both test files; `metadata=` has a default so no other call site is coupled to this change |
| Full-suite regression check | `uv run ruff format . && uv run ruff check --fix .` — clean, 87 files; `uv run mypy .` (strict) — no issues, 87 files; `uv run pytest` — 619 passed (was 600 before this batch — 19 new tests); `uv run deptry .` — no dependency issues |

## Work Unit Evidence (PR5a)

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest packages/pytest-vantage/tests/test_config.py packages/pytest-vantage/tests/test_opt_in.py` — 20 passed (was 11 before this batch — 10 new: 9 `resolve_metadata_capture` + 1 short-circuit) |
| Runtime harness command/scenario and result | N/A — pure unit level: `resolve_metadata_capture` and `_metadata_capture_requested` are exercised directly, no subprocess or server needed; the flag can be set but nothing consumes it end-to-end yet (PR5b) |
| Rollback boundary | Revert `resolve_metadata_capture` from `config.py`, the `--vantage-metadata` registration/`_metadata_capture_requested` from `plugin.py`, and the `metadata_requested` keyword from `Recorder.__init__`; the keyword has a default so no existing call site breaks |
| Full-suite regression check | `uv run ruff format . && uv run ruff check --fix .` — clean; `uv run mypy .` (strict) — no issues, 87 files; `uv run pytest` — 629 passed (was 619 before this batch — 10 new tests); `uv run deptry .` — no dependency issues; `-m 'req(id="RQ-2")'` — 4 passed, 625 deselected (narrows correctly; PR5b adds three more `RQ-2`-marked tests) |

## Work Unit Evidence (PR5b)

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest packages/pytest-vantage/tests/test_config.py packages/pytest-vantage/tests/test_opt_in.py packages/pytest-vantage/tests/test_vcs.py` — 45 passed (was 38 before this batch: 18 `test_config.py` + 6 `test_opt_in.py` + 14 `test_vcs.py`, all from PR5a's state — net +7 new tests: 2 C1, 1 C3, 2 C2, 2 Q3) |
| Runtime harness command/scenario and result | Real end-to-end differential for C1: `subprocess.run([sys.executable, "-m", "pytest", ...])` against two freshly-written project trees (`test_project_tree_is_byte_identical_with_a_metadata_declaration_present_but_the_flag_absent`) and a real in-process `pytester.runpytest()` session for the socket-level half; C2/Q3 exercise `Recorder.__init__` directly (unit level, no runtime boundary needed — the declaration read is a local filesystem operation, not a network one) |
| Rollback boundary | Revert `_METADATA_DECLARATION_FILENAME`/`_metadata_declaration_missing_warning` and the `if metadata_requested:` block from `recorder.py`, and the C1/C2/C3/Q3 test additions from `test_opt_in.py`; PR5a's flag/resolver/wiring is untouched and stays fully functional (it simply stops warning/opening again) |
| Full-suite regression check | `uv run ruff format . && uv run ruff check --fix .` — clean; `uv run mypy .` (strict) — no issues, 87 files; `uv run pytest` — 636 passed (was 629 before this batch — 7 new tests); `uv run deptry .` — no dependency issues; `-m 'req(id="RQ-2")'` — 7 passed, 629 deselected (narrows correctly) |

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 3.1/3.2 | `packages/vantage/tests/test_metadata.py` | Unit | ✅ 593/593 (baseline before this batch) | ✅ Written — confirmed failing: `ModuleNotFoundError: No module named 'vantage.core.domain.metadata'` | ✅ Passed after 3.2's module creation — 7/7 | ➖ Skipped: purely structural (frozenset literals and constant definitions, D94/D95's own values), exactly one possible output per assertion, no branching — noted per strict-tdd's explicit skip condition | ➖ None needed |
| 3.3 | `packages/vantage/tests/test_architecture.py` | Unit | ✅ 4/4 (baseline before this batch) | ✅ Written, then confirmed failing by temporarily moving `metadata.py` out of the tree and re-running: `AssertionError: assert 'vantage/core/domain/metadata.py' in {...}` | ✅ Passed after restoring the module — 4/4 | ➖ Single — one membership assertion, no branching | ➖ None needed |
| 4.1/4.2 | `packages/vantage/tests/test_storage_types.py` | Unit | ✅ 120/120 (baseline before this batch) | ✅ Written — confirmed failing: `ImportError: cannot import name 'EMPTY_RUN_METADATA' from 'vantage.core.ports.storage'` | ✅ Passed after 4.2's dataclasses landed — 16/16 in the file | ➖ Skipped: purely structural (frozen/slots shape and default-equality checks), one possible output per assertion, no branching | ➖ None needed |
| 4.3/4.4/4.5 | `packages/vantage/tests/vantage_port_contract.py` (inherited by `test_sqlite_store.py`, `test_memory_store.py`) | Unit (adapter contract) | ✅ 120/120 (baseline before this batch) | ✅ Written — confirmed failing on both adapters: `TypeError: record_session() got an unexpected keyword argument 'metadata'` (SQLite + in-memory) and `AttributeError: 'InMemoryExecutionStore' object has no attribute '_metadata_files'` (introspection helper, in-memory) — 9 failures | ✅ Passed after 4.4 (SQLite) and 4.5 (in-memory) landed — 139/139 across the four files | ✅ 5 cases per adapter: round-trip, D95 drop-whole row, write-once replay, finish-only vs. start+finish parity, no-argument default — both adapters run the same 5 tests unchanged (RQ-30's "second mechanism" proof) | ➖ None needed |
| 5.1/5.2 | `packages/pytest-vantage/tests/test_config.py` | Unit | ✅ 619/619 (baseline before this batch) | ✅ Written — confirmed failing: `ImportError: cannot import name 'resolve_metadata_capture' from 'pytest_vantage.config'` | ✅ Passed after 5.2's function landed — 18/18 in the file | ✅ 4 combinations × 2 monotone properties + the exhaustive truth table — same triangulation shape `resolve_failure_text_capture`'s own test file already uses | ➖ None needed |
| 5.4/5.7 (signature) | `packages/pytest-vantage/tests/test_opt_in.py` | Unit | ✅ 5/5 (`test_opt_in.py`'s original tests, baseline before this batch) | ✅ Written — confirmed failing: `ImportError: cannot import name '_metadata_capture_requested' from 'pytest_vantage.plugin'` | ✅ Passed after the flag registration + gate function landed | ➖ Single — one short-circuit property, no branching | ➖ None needed |
| 5.3 | `packages/pytest-vantage/tests/test_opt_in.py` | Unit + subprocess (C1) | ✅ 629/629 (baseline before this batch) | ✅ Written | ✅ Passed immediately, no production code change needed — the flag's mere presence (already registered in PR5a) is what C1/C3 prove | ➖ Single per scenario — tree-identity, socket-level, `--help` text, no branching in any | ➖ None needed |
| 5.5 | `packages/pytest-vantage/tests/test_opt_in.py` | Unit | ✅ 629/629 (baseline before this batch) | ✅ Written — confirmed failing (the "is opened" half): `assert False`, zero paths recorded | ✅ Passed after 5.7's `_metadata_declaration_missing_warning` call landed in `Recorder.__init__` | ✅ 2 cases: gate closed (zero opens) and gate open (one open) — the monotone C2 property proven both ways | ➖ None needed |
| 5.6 | `packages/pytest-vantage/tests/test_opt_in.py` | Unit | ✅ 629/629 (baseline before this batch) | ✅ Written — confirmed failing (the "warns" half): `assert 0 == 1`, zero `VantageWarning`s recorded | ✅ Passed after 5.7's warning call landed | ✅ 2 cases: absent (warns once) and present (warns never) | ➖ None needed |

### Test Summary (PR3)

- **Total tests written this batch**: 7 new in `test_metadata.py`, plus 1 new assertion in an existing `test_architecture.py` test
- **Total tests passing (full suite)**: 600
- **Layers used**: Unit only — pure vocabulary module, no I/O boundary exists
- **Approval tests** (refactoring): None — no refactoring tasks in this phase
- **Pure functions created**: 0 — vocabulary and constants only, no logic (RQ-26, by design)

### Test Summary (PR4)

- **Total tests written this batch**: 9 new in `test_storage_types.py`; 5 new shared contract methods in `vantage_port_contract.py`, each inherited by both `TestSqliteExecutionStore` and `TestInMemoryExecutionStore` (10 test executions)
- **Total tests passing (full suite)**: 619
- **Layers used**: Unit only — the shared contract exercises real SQLite (temp-dir file) and the in-memory dict adapter, but through direct Python calls, not a runtime/HTTP boundary
- **Approval tests** (refactoring): None — no refactoring tasks in this phase
- **Pure functions created**: `_metadata_file_rows`, `_metadata_entry_rows` (sqlite_store.py) — pure row-tuple builders, no I/O

### Test Summary (PR5a)

- **Total tests written this batch**: 9 new in `test_config.py` (`resolve_metadata_capture`); 1 new in `test_opt_in.py` (`_metadata_capture_requested` short-circuit)
- **Total tests passing (full suite)**: 629
- **Layers used**: Unit only — pure function and gate-double tests, no subprocess or server
- **Approval tests** (refactoring): None — no refactoring tasks in this phase
- **Pure functions created**: `resolve_metadata_capture` (config.py)

### Test Summary (PR5b)

- **Total tests written this batch**: 7 new in `test_opt_in.py` (2 C1, 1 C3, 2 C2, 2 Q3)
- **Total tests passing (full suite)**: 636
- **Layers used**: Unit (`Recorder.__init__` direct construction, C2/Q3), subprocess (tree-identity differential, C1), in-process `pytester` (socket-level, C1)
- **Approval tests** (refactoring): None — no refactoring tasks in this phase
- **Pure functions created**: `_metadata_declaration_missing_warning` (recorder.py) — no side effects beyond the file it deliberately opens and closes

## Deviations from Design

**One design gap found and filled, not silently.** `design.md`'s D94 bounds
table (line ~286-296) derives six constants but does not include
`MAX_METADATA_KEY_CHARS` — it appears exactly once in the whole document,
in the file-changes table at line ~856, listed as one of three constants
`core/domain/metadata.py` must carry, with no derivation of its own. No
other section of `design.md` (searched for "key" + "char"/"length"/"bound")
gives it a value. This module implements it at `1024`, matching the two
sibling bounds in the same D94 table that both derive from `MAX_IDENTITY_CHARS`
(`MAX_DECLARED_PATH_CHARS` and `MAX_METADATA_VALUE_BYTES`) — a declared key
is the same class of short, client-supplied, indexed string D89 already
argued that bound for. This is the precedent-consistent value, not an
arbitrary one, but it is a filled gap and the orchestrator/maintainer should
confirm it against the design's intent before Phase 8/9 (where a key-length
check would actually be enforced against a declared document).

Otherwise none — `FILE_STATUSES` and `KEY_STATUSES` match D91's SQL `CHECK`
lists exactly (verified against `schema.sql`'s literal text, not just the
design summary), `MAX_METADATA_VALUE_BYTES=1024` and `MAX_METADATA_ENTRIES=200`
match D94's table exactly.

**PR4: none.** Implemented exactly D98's chosen shape — one frozen `RunMetadata`
aggregate with a default, not the two-parameter alternative D98 explicitly
rejects. No new `ExecutionStore` methods were added (D98's Option 2,
rejected). No existing call site was touched.

**PR5a: one deviation, recorded above under Phase 5's Completed Tasks.**
`_metadata_capture_requested` is called only from the controller branch of
`pytest_configure`, not "identically on both xdist branches" as D99's prose
states — `test_xdist_guard.py`'s `_WorkerConfigDouble` allow-list would
break if a worker ever read `"vantage_metadata"`, and nothing worker-side
consumes the value in this slice. The property D99 actually cares about
(the declaration read exactly once per session, worker count irrelevant)
holds regardless, because no `Recorder` is ever constructed on a worker.

## Issues Found

None beyond the `MAX_METADATA_KEY_CHARS` gap noted above (PR3). Task 3.3
required a non-linear RED: `metadata.py` already existed (from task 3.2,
which necessarily precedes 3.3 in the phase's own numbering) by the time the
new `test_architecture.py` assertion was written, so genuine RED was proven
by temporarily moving the module out of the source tree, confirming the
assertion failed, then restoring it — not by reverting to before task 3.2.
This is recorded rather than silently treated as "trivially green."

**PR4.** No port read method exists yet for `run_metadata`/`run_metadata_file`
(that lands in Phase 10, D100), so the shared contract tests cannot assert
through the public `ExecutionStore` interface alone. Resolved by adding two
adapter-agnostic introspection helpers (`_stored_metadata_files`,
`_stored_metadata_entries`) to `vantage_port_contract.py` that dispatch on
`isinstance(store, SqliteExecutionStore | InMemoryExecutionStore)` and read
each adapter's private storage directly — the same pattern
`test_sqlite_store.py` already uses (`store._conn.execute(...)  # noqa: SLF001`).
This is test-only introspection, not a new production API surface, and does
not touch D98's rejected-Option-2 decision (no new port methods were added).
Flagged here rather than silently added, since it is a deviation from "the
contract only calls the public port" in spirit, though not in the sense D98
was reasoning about.

**PR5a.** None beyond the xdist-branch deviation already recorded above.

**PR5b.** None. `_metadata_declaration_missing_warning` opens the file
directly (`Path.open("rb").close()`), TOCTOU-safe like `resolve_declared_
path` will be, rather than checking `.exists()`/`.is_file()` first — this
is a design choice made explicit in the function's own docstring, not a
literal instruction from D99's text (which does not specify the presence
check's exact mechanism, only that a missing declaration warns). It is
consistent with the codebase's stated preference for attempt-first over
check-then-act, and it means this presence check needs no second read once
`pytest_vantage.metadata`'s `read_declaration` lands in Phase 6/7.

## Git / PR State

- Tracker: `ft/run-metadata-capture` (draft, no-merge) — untouched.
- PR1: https://github.com/guillegil/vantage/pull/88 — base tracker, head `ft/run-metadata-capture-01-adr`. Open, 12/12 checks pass. Not merged.
- PR2: https://github.com/guillegil/vantage/pull/89 — base `ft/run-metadata-capture-01-adr`, head `ft/run-metadata-capture-02-schema`. Open, 12/12 checks pass. Not merged.
- PR3: https://github.com/guillegil/vantage/pull/90 — base `ft/run-metadata-capture-02-schema`, head `ft/run-metadata-capture-03-core`. Open, 12/12 checks pass. Not merged.
- PR3 commits: `6bd5f1d` (vocabulary module + tests + architecture-walk assertion), `7a87775` (tasks.md checkboxes), plus its apply-progress commit.
- PR4: https://github.com/guillegil/vantage/pull/91 — base `ft/run-metadata-capture-03-core`, head `ft/run-metadata-capture-04-port`. Open, 12/12 checks pass. Not merged.
- PR4 commits: `9e81c58` (port dataclasses + both adapters + contract tests), `95d1667` (tasks.md checkboxes), plus this apply-progress commit.
- PR5a: https://github.com/guillegil/vantage/pull/92 — base `ft/run-metadata-capture-04-port`, head `ft/run-metadata-capture-05-flag`. Open. Not merged.
- PR5a commits: `c10703e` (flag + resolver + Recorder wiring), `c6f8c85` (tasks.md checkboxes, re-slicing note), `bfea150` (apply-progress).
- PR5b: base `ft/run-metadata-capture-05-flag`, head `ft/run-metadata-capture-05b-checks`. URL filled in once opened (this batch).
- PR5b commits: `e45f5b8` (declaration presence check + Q3 warning + C1/C2/C3 tests), plus this apply-progress commit.

## Measured changed-line count

**PR3** (vs. PR2 branch, before the apply-progress.md commit):
`git diff --stat ft/run-metadata-capture-02-schema..HEAD` — 174
insertions(+), 3 deletions(-) — 177 changed lines. Breakdown:
`packages/vantage/src/vantage/core/domain/metadata.py` +86,
`packages/vantage/tests/test_metadata.py` +84, `test_architecture.py` +1,
`tasks.md` +6/-3. Estimate was ~220.

**PR4** (vs. PR3 branch): the code+tests commit alone (`9e81c58`, before any
bookkeeping) measured 429 changed lines (417 insertions + 12 deletions) — the
number PR4's own description cites as its `size:exception`, since that was
the state at PR-open time. The **full PR diff including both bookkeeping
commits** (`git diff --stat ft/run-metadata-capture-03-core..HEAD`, after the
tasks.md and apply-progress.md commits): **562 changed lines** (516
insertions + 46 deletions). Breakdown: `core/ports/storage.py` +53/-0,
`sqlite_store.py` +52/-4, `memory.py` +24/-3, `test_storage_types.py`
+97/-0, `vantage_port_contract.py` +191/-0, `tasks.md` +12/-12 (PR4's own
Phase-4 marks, cumulative with PR1-3's), `apply-progress.md` +133/-34. Against
the 400-line budget and this task's own ~370 forecast — a documented
`size:exception` (see PR4's description). No honest seam exists inside D86's
binding constraint that the port and both adapters move together: a
`Protocol` keyword the concrete adapters do not yet accept leaves
`mypy --strict` red the moment it lands alone, so "dataclasses first, wire
second" is not a green-at-every-commit split. The bookkeeping/apply-progress
overhead itself (`tasks.md` + `apply-progress.md`, ~145 lines) is the kind of
SDD process cost this project's own historical calibration note
(tasks.md's re-slicing note) already names as part of why forecasts run
under — it is not part of the reviewable code diff proper.

**PR5a** (vs. PR4 branch, `git diff --stat ft/run-metadata-capture-04-port..HEAD`,
after both the code commit and the tasks.md commit, before the apply-progress
commit): **270 changed lines** (256 insertions + 14 deletions). Breakdown:
`config.py` +20, `plugin.py` +63/-4, `recorder.py` +13, `test_config.py`
+64/-4, `test_opt_in.py` +36/-6, `tasks.md` +19/-8. Well under the 400-line
budget — this is the split's whole point: the code+tests commit measured
428 changed lines *before* splitting (see PR5 originally attempted as one
PR, below), and the honest seam brought each half comfortably under budget.

**PR5b** (vs. PR5a branch, `git diff --stat ft/run-metadata-capture-05-flag..HEAD`,
code commit only, before the apply-progress commit): **254 changed lines**
(241 insertions + 13 deletions). Breakdown: `recorder.py` +52/-1,
`test_opt_in.py` +202/-12. No `tasks.md` commit in PR5b — Phase 5's tasks
were all marked `[x]` in PR5a's tasks.md commit, since the re-slicing note
and every task's `(PR5a)`/`(PR5b)` annotation were written once, in PR5a.

**The abandoned single-PR attempt**, for the record: before splitting, the
code+tests commit alone (`d737d80`, superseded, not on either final branch)
measured 428 changed lines (417 insertions + 11 deletions) against PR4 —
7% over budget, comparable to PR4's own 429-line `size:exception`. Unlike
PR4, an honest seam existed here (the flag/resolver/wiring is independently
useful and independently testable from the differential/Q3 behaviour that
consumes it), so this batch split rather than repeating a `size:exception`.

## Remaining Tasks

All of Phase 6 through Phase 11 (tasks 6.1 through 11.5) — see tasks.md.
Phase 6 (path containment) is next in the chain and must target
`ft/run-metadata-capture-05b-checks` per `feature-branch-chain`.

## Workload / PR Boundary

- Mode: chained PR slice (`feature-branch-chain`), Phase 5 re-sliced into PR5a (flag + resolver) and PR5b (C1/C2/C3 + Q3) — an honest seam, not a `size:exception`
- Current work unit: Phase 5 — the `--vantage-metadata` opt-in flag end to end: registration, resolver, gate short-circuit, declaration presence check, Q3's warning, and the C1/C2/C3 proof that the flag is correctly inert
- Boundary: PR5a starts from PR4's tip (`ft/run-metadata-capture-04-port`); PR5b starts from PR5a's tip and ends with both PRs opened and green. No path containment, file content reading/bounding, wire section, server parsing, or ingestion wiring touched (out of scope per launch instructions — Phase 6 onward)
- Estimated review budget impact: PR5a 270 changed lines, PR5b 254 changed lines — both comfortably under the 400-line budget, no `size:exception` needed

## Status

38/38 tasks complete across Phase 1 (4/4), Phase 2 (7/7), Phase 3 (3/3),
Phase 4 (6/6) and Phase 5 (8/8, split PR5a/PR5b this batch). PR1, PR2, PR3
and PR4 open and green, not merged (per instructions — the chain merges in
order at the end). PR5a and PR5b opened this batch, also open and green.
Ready for the next `sdd-apply` batch (Phase 6, path containment).
