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
- Phase 6 (PR6 → PR5b) — **partial, this batch, re-scoped at apply time**:
  `resolve_declared_path` (6.1/6.2/6.5/6.6) is landed; `read_declaration`
  (6.3/6.4) is deferred to the next `sdd-apply` batch per this launch's
  explicit narrowing (see Phase 6's own note in `tasks.md`).
- Phase 7 (PR7a/PR7b/PR7c → PR6) — complete.
- Phase 8 (PR8a/PR8b → PR7c) — complete, this batch, **re-sliced into two
  PRs** (see Phase 8's own note in `tasks.md`).
- Phases 9-11 are untouched and remain for future `sdd-apply` batches.

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

### Phase 6 (PR6) — Path containment, re-scoped

**Re-scoped at apply time, per the launch instructions.** The launch brief
for this batch narrowed Phase 6 explicitly to "path containment
(`resolve_declared_path`, and the symlink / loop / FIFO / absolute / `..`
cases)" and named "no file reading, no bounding" among this PR's exclusions.
`read_declaration` (6.3/6.4) parses the declaration file's own JSON content
-- exactly the "file reading" excluded -- so it is deferred to the next
batch. 6.1, 6.2, 6.5 and 6.6 land here, re-worded in `tasks.md` to describe
what this PR actually proves.

- [x] 6.1 RED: `packages/pytest-vantage/tests/test_metadata_containment.py`
  -- 10 tests: absolute path, `..` escape, a real symlink pointing outside
  `rootpath`, a real symlink loop (`os.symlink`, cyclic), a directory, a
  real FIFO (`os.mkfifo`, `skipif` where unsupported), a path equal to
  `rootpath` itself, a missing path -- each asserted rejected (`None`);
  plus a legitimate nested path accepted and a root reached through a
  symlink still accepting its own children. Confirmed failing before 6.2:
  `ImportError: cannot import name 'metadata' from 'pytest_vantage'`.
- [x] 6.2 GREEN: created `packages/pytest-vantage/src/pytest_vantage/metadata.py`
  -- `resolve_declared_path` exactly as D93's code sample: `PurePath`
  absolute/drive/anchor pre-check, `..` in `parts` pre-check, both
  `rootpath` and the candidate resolved (in that order) before the
  purely-lexical `is_relative_to` containment check, `target == root` and
  `not target.is_file()` rejections, `except (OSError, RuntimeError)`
  around the whole resolve-and-check block. Stdlib only (`pathlib`), no
  other import (RQ-24).
- [x] 6.5 Threat-matrix RED, scoped to `resolve_declared_path` itself (no
  production code calls it from `pytest_sessionstart` yet -- that wiring is
  Phase 7): `test_a_fifo_is_rejected_without_blocking` asserts both the
  outcome (`None`) and a bounded wall-clock time (`< 1.0s`) around the
  call, with no reader ever attached to the FIFO -- the only way the test
  can finish at all is if `resolve_declared_path` never calls `open()` on
  it, only `stat` via `is_file()`.
- [x] 6.6 Verify: `uv run pytest packages/pytest-vantage/tests/test_metadata_containment.py`
  -- 10 passed. Additionally run individually under `uv run --python 3.10`,
  `3.11`, `3.12` and `3.13` (see Cross-Version Verification below) --
  10 passed on every one, unmodified.
- [ ] 6.3, 6.4 (next batch): `read_declaration` and its four constants.

**Real basename collision found and resolved, not worked around silently.**
`packages/vantage/tests/test_metadata.py` already exists (PR3). Neither
test tree carries an `__init__.py`, and this workspace has exactly one
`pytest.ini_options` section (D9) covering both, so pytest's classic import
mode requires every test basename to be globally unique across the whole
workspace. Naming the new file `test_metadata.py`, as `tasks.md`'s original
wording said, collides and fails collection outright -- proven by running
both files together and reading pytest's `import file mismatch` error
before renaming, not assumed. Used `test_metadata_containment.py` instead.
This is a task-authoring gap in the original `tasks.md`, not a freelance
rename -- flagged here plus a forward note in `tasks.md` itself, so
Phase 7's `read_declaration`/`capture_metadata` tests (also called
`test_metadata.py` there) pick a second unique name rather than repeating
the collision.

**Cross-version verification of D93's stated trap -- measured, not
trusted.** Verified against a real `os.symlink` cyclic pair (`a -> b -> a`)
under `tmp_path` on all four `uv`-managed interpreters spanning this
project's floor and ceiling:

| Interpreter | `Path.resolve()` on a symlink loop | What rejects it |
| --- | --- | --- |
| 3.10.21 / 3.11.16 / 3.12.14 | Raises `RuntimeError: Symlink loop from '...'` | The `except (OSError, RuntimeError)` clause |
| 3.13.15 | **Raises nothing** with the default `strict=False` -- silently returns the path lexically unresolved | `target.is_file()` returns `False` (stat through the loop fails internally; `Path.is_file()` swallows that `OSError` rather than propagating it) |

**A subtler trap than "catches only one exception type crashes the other
version," which is what the launch brief warned against.** On 3.13
`resolve()` doesn't raise at all for a loop, so `is_file()`'s `False` is
what rejects it there, not the `except` clause. That clause remains
necessary anyway: it is what stands between a committed symlink loop and
an uncaught `RuntimeError` crashing `pytest_sessionstart` on three of the
four supported interpreters. The test asserts the outcome (`None`), not
the mechanism, so it proves the property on all four without branching on
`sys.version_info`. (`resolve(strict=True)` does raise `OSError` errno 40
for the same loop on 3.13 -- not used here; D93's sample uses the default
`strict=False` throughout.)

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
| `packages/pytest-vantage/src/pytest_vantage/metadata.py` | Created (PR6) | `resolve_declared_path` (D93) -- stdlib-only (`pathlib`) path containment, resolving both `rootpath` and the candidate before a lexical `is_relative_to` check |
| `packages/pytest-vantage/tests/test_metadata_containment.py` | Created (PR6) | 10 tests: 8 rejection cases (absolute, `..`, symlink escape, symlink loop, directory, FIFO with bounded wall-time, root-itself, missing) + 2 acceptance cases (nested path, root reached through a symlink) |
| `openspec/changes/run-metadata-capture/tasks.md` | Modified (PR6) | Phase 6 re-scoped: 6.1/6.2/6.5/6.6 marked `[x]`, 6.3/6.4 deferred with an explicit next-batch note; basename-collision note added |

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

## Work Unit Evidence (PR6)

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest packages/pytest-vantage/tests/test_metadata_containment.py` — 10 passed. Re-run individually via `uv run --python 3.10/3.11/3.12/3.13 pytest packages/pytest-vantage/tests/test_metadata_containment.py` — 10 passed on every interpreter, unmodified (proves the cross-version symlink-loop claim empirically, not just on the default interpreter) |
| Runtime harness command/scenario and result | N/A in the HTTP-boundary sense — `resolve_declared_path` is a pure filesystem function with no server involved. Its "runtime harness" equivalent is real `os.symlink`/`os.mkfifo` fixtures under `tmp_path` rather than a mock of filesystem behaviour, matching `test_vcs.py`'s own verification approach for the plugin's other filesystem/subprocess boundary; there is no in-process server or subprocess to exercise for a pure `pathlib` function |
| Rollback boundary | Revert `pytest_vantage/metadata.py` and `test_metadata_containment.py`; nothing else in the tree imports `pytest_vantage.metadata` yet (Phase 7 is the first caller), so this reverts standalone |
| Full-suite regression check | `uv run ruff format . && uv run ruff check --fix .` — clean, 89 files; `uv run mypy .` (strict) — no issues, 89 files (after resolving the `test_metadata.py` basename collision, which `mypy` also caught independently as "Duplicate module named"); `uv run pytest` — 646 passed (was 636 before this batch — 10 new tests); `uv run deptry .` — no dependency issues, 88 files scanned; `uv run pytest packages/pytest-vantage/tests/test_plugin_imports.py` — 2 passed, confirming the new module is still stdlib-only and is included in the RQ-24 import walk (it scans the whole `pytest_vantage` directory, so no test update was needed for the walk itself to see the new file) |

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
| 6.1/6.2 | `packages/pytest-vantage/tests/test_metadata_containment.py` | Unit (real filesystem: `os.symlink`, `os.mkfifo`) | ✅ 636/636 (baseline before this batch) | ✅ Written — confirmed failing: `ImportError: cannot import name 'metadata' from 'pytest_vantage'` | ✅ Passed after 6.2's module landed — 10/10, on the default interpreter and re-confirmed individually on 3.10/3.11/3.12/3.13 | ✅ 10 cases: 8 rejection (absolute, `..`, symlink escape, symlink loop, directory, FIFO, root-itself, missing) + 2 acceptance (nested, symlinked-root) — no branching within any single case | ➖ None needed |

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

### Test Summary (PR6)

- **Total tests written this batch**: 10 new in `test_metadata_containment.py`
- **Total tests passing (full suite)**: 646
- **Layers used**: Unit, over a real filesystem (`tmp_path`, `os.symlink`, `os.mkfifo`) — no mock of filesystem or subprocess behaviour, matching `test_vcs.py`'s own verification approach
- **Approval tests** (refactoring): None — no refactoring tasks in this phase
- **Pure functions created**: `resolve_declared_path` (metadata.py) — pure with respect to program state, though it performs real filesystem `stat` calls (`resolve()`, `is_file()`), never a write and never an `open()`

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

**PR6: one scope deviation from `tasks.md`'s literal text, authorised by
the launch instructions.** `tasks.md`'s original Phase 6 bundled
`read_declaration` (6.3/6.4) together with `resolve_declared_path`
(6.1/6.2). This batch's launch instructions explicitly narrowed scope to
"path containment (`resolve_declared_path` ...)" and named "no file
reading, no bounding" as out of scope, which is exactly what
`read_declaration` does. `tasks.md` itself is updated to record this
re-scoping and defer 6.3/6.4, rather than silently implementing a subset
and leaving the task list looking like it covers more than it does. **One
naming deviation, not authorised by anything, just a real collision found
and fixed**: the plugin-side test file is `test_metadata_containment.py`,
not `test_metadata.py` as `tasks.md` originally said — see the Phase 6
section above for the proof.

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

**PR6.** The real basename collision against `packages/vantage/tests/
test_metadata.py` (see Phase 6 section above) — resolved by renaming the
new file, not by touching the pre-existing PR3 file, since PR3 is earlier
in the chain and already open/reviewed.

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
- PR5b: https://github.com/guillegil/vantage/pull/93 — base `ft/run-metadata-capture-05-flag`, head `ft/run-metadata-capture-05b-checks`. Open. Not merged.
- PR5b commits: `e45f5b8` (declaration presence check + Q3 warning + C1/C2/C3 tests), `2e6216e` (apply-progress).
- PR6: https://github.com/guillegil/vantage/pull/94 — base `ft/run-metadata-capture-05b-checks`, head `ft/run-metadata-capture-06-paths`. Open, 12/12 checks pass. Not merged.
- PR6 commits: `7d58af1` (`resolve_declared_path` + tests), `776407a` (tasks.md re-scope), `dad4579`+`8d39c0a` (apply-progress, written then trimmed).

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

**PR5b**: the code commit alone (`e45f5b8`, before any bookkeeping) measured
254 changed lines (241 insertions + 13 deletions) against PR5a. Breakdown:
`recorder.py` +52/-1, `test_opt_in.py` +202/-12. No `tasks.md` commit in
PR5b — Phase 5's tasks were all marked `[x]` in PR5a's tasks.md commit,
since the re-slicing note and every task's `(PR5a)`/`(PR5b)` annotation
were written once, in PR5a. The **full PR diff including both
apply-progress commits** (`git diff --stat ft/run-metadata-capture-05-flag..HEAD`,
measured after this note was written) is reported in the return summary —
comfortably under the 400-line budget even with bookkeeping included; see
the apply return envelope for the exact final count.

**The abandoned single-PR attempt**, for the record: before splitting, the
code+tests commit alone (`d737d80`, superseded, not on either final branch)
measured 428 changed lines (417 insertions + 11 deletions) against PR4 —
7% over budget, comparable to PR4's own 429-line `size:exception`. Unlike
PR4, an honest seam existed here (the flag/resolver/wiring is independently
useful and independently testable from the differential/Q3 behaviour that
consumes it), so this batch split rather than repeating a `size:exception`.

**PR6** (vs. PR5b branch, `git diff --stat ft/run-metadata-capture-05b-checks..HEAD`,
final, after every bookkeeping commit): **429 changed lines** (410
insertions + 19 deletions) -- a documented `size:exception`, PR4's pattern
repeated. Code+tests alone: `metadata.py` +94, `test_metadata_containment.py`
+142 -- 236 lines, comfortably under this task's ~380 forecast. The
overshoot is entirely `tasks.md` (33) and `apply-progress.md` (160)
bookkeeping -- documenting a real basename collision and a real
cross-version measurement in enough detail to be checkable cost more than
compressing it further would have preserved; each trim pass tried this
batch reduced net content but still added its own diff lines on top of
what it removed, the same self-referential cost PR4's note already names.

## Batch: Phase 6.3/6.4 + Phase 7 (PR7a/PR7b/PR7c)

**Scope**: tasks 6.3, 6.4 (deferred from PR6), and all of Phase 7 (7.1-7.6).
Phase 8 not started, per this launch's explicit scope narrowing.

**Re-sliced into three PRs, not the planned two.** The combined 6.3/6.4 +
7.1/7.2 diff measured 759 changed lines against PR6 (90% over budget) before
any bookkeeping. An honest seam existed — `read_declaration` (6.3/6.4) does
not need `capture_metadata` (7.1/7.2), only the reverse — so it was cut a
second time instead of taking a `size:exception`. Chain:
`ft/run-metadata-capture-07a-declaration` (6.3/6.4 → PR6, #95),
`ft/run-metadata-capture-07b-capture` (7.1/7.2 → PR7a, #96),
`ft/run-metadata-capture-07c-wire` (7.3-7.6 → PR7b, #97).

**Basename collisions, again.** `test_metadata.py` (PR3) and
`test_metadata_containment.py` (PR6) already existed; this batch adds two
more uniquely-named files: `test_metadata_declaration.py` (PR7a,
`read_declaration`) and `test_metadata_capture.py` (PR7b,
`capture_metadata`) — one per PR, never shared across a PR boundary.

### Tasks completed

- [x] 6.3 RED / 6.4 GREEN (PR7a): `read_declaration` — 8 declaration-level
  rejection conditions (D92), each warns exactly once via `_warn` and
  captures nothing. **Completeness addition beyond the task's literal
  list**: path-length and total-key-count bounds are also RED-tested (both
  constants task 6.4 already required defining). Confirmed RED:
  `AttributeError: module 'pytest_vantage.metadata' has no attribute
  'read_declaration'`. GREEN: 19 tests passing.
- [x] 7.1 RED / 7.2 GREEN (PR7b): `capture_metadata` — bounded per-file read
  (`handle.read(MAX_DECLARED_FILE_BYTES + 1)`, never the whole file),
  section-budget spend in declaration order reusing `budget.py`'s exact
  `_encoded_cost` (imported directly, not reimplemented), every D97
  plugin-side failure class covered. **Completeness addition**: `not_found`
  vs `path_rejected` distinguished via a small advisory-only classifier,
  since `resolve_declared_path` intentionally collapses both to `None` for
  the security decision alone. Confirmed RED: `AttributeError: module
  'pytest_vantage.metadata' has no attribute 'capture_metadata'`. GREEN: 10
  tests passing.
- [x] 7.3 GREEN (PR7c): `budget.py` docstring-only addition, ~1,038 → ~973
  result headroom. No behaviour change.
- [x] 7.4 RED / 7.5 GREEN (PR7c): `recorder.py` — `self._metadata` captured
  once in `__init__`; `_metadata_section()` mirrors `_vcs_section()`'s D51
  freeze rule; wired into both report builds. Confirmed RED via the same
  proof technique as the existing `test_vcs_section_is_identical_on_both_
  reports`: `capture_metadata` patched to return a different section per
  call; failed before the wiring landed with `ImportError: No module named
  'pytest_vantage.recorder.metadata'`. Removed PR5b's now-superseded
  `_metadata_declaration_missing_warning` — PR5b's C2/Q3 tests pass
  **unmodified**, since `capture_metadata`'s `read_declaration` call
  reaches the same file and emits the same warning text.
- [x] 7.6 Verify (PR7c): `uv run pytest packages/pytest-vantage/tests/test_metadata_declaration.py packages/pytest-vantage/tests/test_metadata_capture.py packages/pytest-vantage/tests/test_report_budget.py packages/pytest-vantage/tests/test_run_report.py` — 61 passed.

### Full-suite regression per PR

| PR | Full `uv run pytest` | mypy strict | deptry |
|---|---|---|---|
| PR7a | 665 passed (was 646) | clean, 90 files | clean |
| PR7b | 675 passed (was 665) | clean, 91 files | clean |
| PR7c | 677 passed (was 675) | clean, 91 files | clean |

### Measured changed lines

- **PR7a** (vs PR6 branch, full diff incl. bookkeeping): 465 changed lines
  (25 tasks.md + 172 metadata.py + 262 test file, one deletion in
  metadata.py, 1 in tasks.md). Code+tests alone: 434 (172+262), a documented
  `size:exception`, 8.75% over — identical percentage to PR6's own
  precedent, for the same reason (one cohesive validation function, 8
  independent D92 conditions each needing its own RED test).
- **PR7b** (vs PR7a branch): 362 changed lines (134 metadata.py + 228 test
  file) — under budget, no bookkeeping commit needed (tasks.md already
  updated in PR7a).
- **PR7c** (vs PR7b branch, full diff incl. bookkeeping): 192 changed lines
  (8 tasks.md + 11 budget.py + 92 recorder.py + 81 test file) — well under
  budget.

### Git / PR state (this batch)

- PR7a: https://github.com/guillegil/vantage/pull/95 — base
  `ft/run-metadata-capture-06-paths`, head
  `ft/run-metadata-capture-07a-declaration`. Open, 12/12 checks green.
- PR7b: https://github.com/guillegil/vantage/pull/96 — base PR7a's branch,
  head `ft/run-metadata-capture-07b-capture`. Open, 12/12 checks green.
- PR7c: https://github.com/guillegil/vantage/pull/97 — base PR7b's branch,
  head `ft/run-metadata-capture-07c-wire`. Open, 12/12 checks green.
- None merged yet — chain merges in order once every slice up to this one
  is reviewed, per `feature-branch-chain`.

## Batch: Phase 8 (PR8a/PR8b)

### Tasks completed

- [x] 8.1 RED / 8.2 GREEN (PR8a): `MetadataFileReport`/`MetadataReport`
  (`extra="forbid"`, matching `VcsReport`) — no `max_length`, no pattern,
  no constraint of any kind on any field (D96 trap). `SessionReport.metadata:
  MetadataReport | None = None`. `service/openapi/v1.yaml`'s `SessionReport`
  schema property list updated in the same commit — required by the
  existing schema-binding drift check (`test_interface_document.py`), not a
  scope addition. Confirmed RED: `ImportError: cannot import name
  'MetadataFileReport' from 'vantage.service.schemas'`. GREEN: 17 passed.
- [x] 8.3 RED / 8.4 GREEN (PR8b): `metadata_parse.py` — the only module
  importing `yaml`; `yaml.compose()` + `ScalarNode` walk (never
  `safe_load`/`load`); `json.loads`; both plus `RecursionError` collapse to
  `None` (D97 class 7). `parse(content, content_type, keys)` classifies
  each requested key directly (`captured`/`absent`/`not_scalar`/
  `value_too_large`) rather than deferring that to Phase 9's
  `_to_run_metadata` — the node-type information the classification needs
  is exactly what the walk already produces. Confirmed RED: `ModuleNotFoundError:
  No module named 'vantage.service.metadata_parse'`. GREEN: 13 passed.
  **Depth correction, verified not assumed**: the launch brief's
  "1,000-deep" JSON nesting does not reproduce `RecursionError` on cpython
  3.13.15 (`sys.getrecursionlimit() == 1000`) — measured 1,000/5,000/8,192
  levels all return with no exception; `RecursionError` first reproduces at
  10,000 levels. Test uses 20,000 for margin, with the bare `json.loads`
  call proven first.
- [x] 8.5 GREEN (PR8b): `packages/vantage/pyproject.toml` — `PyYAML>=6.0`
  added to `vantage`'s main `dependencies`, not root's dev-only extra
  (which already carried it for `test_interface_document.py`).
- [x] 8.6 Verify (PR8b): `uv run pytest packages/vantage/tests/test_metadata_parse.py packages/vantage/tests/test_schemas.py` —
  30 passed; re-verified individually on Python 3.10/3.11/3.12/3.13.
  `uv run pytest` (whole workspace) — 698 passed (was 677). `uv run deptry .`
  — clean, no per-rule ignore needed. `uv run mypy .` (strict) — clean, 93
  files.

### Re-slicing rationale

The combined 8.1-8.6 diff measured 562 changed lines against
`ft/run-metadata-capture-07c-wire`, 40% over the 400-line budget, before
bookkeeping. `metadata_parse.py` does not import or depend on
`MetadataFileReport`/`MetadataReport`, and the schemas do not depend on the
parser — an honest seam, cut once rather than accepting a
`size:exception`. PR8a (schemas) and PR8b (parser) both land comfortably
under budget on their own.

### Measured changed lines

- **PR8a** (vs `ft/run-metadata-capture-07c-wire`): 190 changed lines (55
  schemas.py + 133 test_schemas.py + 2 openapi/v1.yaml).
- **PR8b** (vs PR8a's branch): 372 changed lines (166 metadata_parse.py +
  193 test_metadata_parse.py + 11 pyproject.toml + 2 uv.lock).

### Git / PR state (this batch)

- PR8a: base `ft/run-metadata-capture-07c-wire`, head
  `ft/run-metadata-capture-08a-schemas`.
- PR8b: base PR8a's branch, head `ft/run-metadata-capture-08b-parse`.
- PR URLs and CI status recorded once opened (see the apply-phase return
  summary for this batch).

## Batch: Phase 9 (PR9a/PR9b/PR9c)

**Scope**: all of Phase 9 (server ingest wiring, tasks 9.1-9.8). Phase 10
(read filter) and Phase 11 (RQ-25 measurement) not started, per this
launch's explicit scope narrowing to Phase 9 only.

### Re-slicing rationale

The combined 9.1-9.8 diff measured 682 changed lines against
`ft/run-metadata-capture-08b-parse` (128+1 `routes/runs.py`, 135+2
`test_routes_runs.py`, 416+0 `test_ingestion.py`), 70% over the 400-line
budget, before any bookkeeping — matching this project's own ~1.9x
historical under-forecast note almost exactly (this task's own ~350
forecast × 1.9 ≈ 665). An honest seam existed, the same shape Phase 7 and
Phase 8 already used: `_to_run_metadata` and its own unit-level proof
(`test_routes_runs.py`) do not need any endpoint-level test to be complete
and independently verifiable, only the reverse; and within the
endpoint-level tests, the D97 taxonomy proof does not need the remaining
scenario-coverage/threat-matrix tests, only the reverse. Cut into three
PRs rather than accepting a `size:exception`:

- `ft/run-metadata-capture-09a-normalizer` (9.1-9.3 → PR8b, #100)
- `ft/run-metadata-capture-09b-taxonomy` (9.4-9.6 → PR9a, #101)
- `ft/run-metadata-capture-09c-threat-matrix` (9.7-9.8 → PR9b)

### Tasks completed

- [x] 9.1 RED (PR9a): `test_routes_runs.py` — 9 unit tests for
  `_to_run_metadata`: the happy path, three shape-reject cases
  (absolute/`..`/oversized `source_file`), an unrecognised `status`, an
  unrecognised `format`, non-captured-file marking, and malformed-document
  marking. Confirmed RED: `ImportError: cannot import name
  '_to_run_metadata' from 'vantage.service.routes.runs'`.
- [x] 9.2 GREEN (PR9a): `routes/runs.py` — `_declared_path_shape_is_valid`
  (D93's server-side re-check: length, absolute, `..`) and
  `_to_run_metadata`, following `_to_vcs_context`'s shape. Drop-whole
  everywhere (D95, D97), `truncate()` never called, the function never
  raises. **Completeness addition beyond the task's literal wording**:
  `MetadataFileReport.status`/`.format` carry no Pydantic constraint at all
  (D96's trap), so a garbage value from either is validated against
  `FILE_STATUSES`/a local content-type allow-list (mirroring
  `run_metadata_file.content_type`'s own SQL `CHECK`: `json`/`yaml`/`toml`)
  and dropped whole on failure — the same D97-class-11 bucket the
  `source_file` shape check already uses. Without this, a hostile or buggy
  client's garbage `status`/`format` value would reach `record_session`
  and raise the SQL `CHECK` mid-transaction, rolling back the run row with
  it: exactly the outcome D97's governing rule forbids, and a real gap the
  task's literal wording (only naming `source_file`) did not cover.
- [x] 9.3 GREEN (PR9a): `metadata=_to_run_metadata(payload.metadata)`
  wired into the `store.record_session(...)` call.
- [x] 9.4 RED (PR9b, RQ-44): `test_ingestion.py` —
  `test_a_report_whose_metadata_is_entirely_garbage_still_records_the_run`.
  Confirmed RED (production code temporarily reverted via `git stash`):
  the assertion on stored metadata rows failed (`frozenset() ==
  frozenset({MetadataFile(...)})`) — the run itself was already stored
  before Phase 9 (D15/D47's pre-existing skew tolerance), so only the
  metadata-specific assertions are the genuine RED signal here, recorded
  rather than silently treated as "the whole test was red."
- [x] 9.5 RED (PR9b): one parametrized test per D97 row (11 cases:
  `not_found`, `path_rejected`, `too_large`, `not_text`, `unreadable`,
  `over_budget`, `malformed`, `absent`, `not_scalar`, `value_too_large`,
  `server_side_shape_reject`), each asserting the exact `(file.status,
  key.status)` pair via `vantage_port_contract`'s adapter-agnostic
  `_stored_metadata_files`/`_stored_metadata_entries` introspection.
  Confirmed RED against the same reverted production code: 11/11 taxonomy
  cases failed except `server_side_shape_reject`, which passed trivially
  (with `_to_run_metadata` not called at all, metadata is simply ignored,
  so the "no rows" expectation happened to hold anyway) — the same
  "trivial RED" shape this project's Phase 5 C1/C3 tests already
  documented, recorded rather than silently treated as a gap.
- [x] 9.6 GREEN (PR9a/PR9b): 9.2's classification already covers every row
  9.5 exercises — confirmed by running 9.4/9.5 directly against PR9a's
  already-landed code with zero further production changes needed.
- [x] 9.7 RED (PR9c): `test_a_quoting_shaped_declared_key_round_trips_
  byte_identically` (bound-parameters proof, mirroring
  `test_routes_sections.py`'s own section-name proof) and
  `test_a_crlf_shaped_metadata_key_never_appears_unescaped_in_a_rejection_
  body` (an unknown field inside the `metadata` section's own
  `extra="forbid"` model, routed through `errors.py`'s pre-existing
  `safe_segment` allow-list). The round-trip test is a genuine RED against
  reverted code (metadata ignored entirely, no entries stored); the CR/LF
  test passes against both reverted and current code, since it exercises
  Pydantic's pre-existing rejection path, not new Phase 9 production code
  — recorded as a confirmation test, not a RED/GREEN pair.
- [x] 9.8 Verify (PR9c): `uv run pytest packages/vantage/tests/test_
  routes_runs.py packages/vantage/tests/test_ingestion.py` — 73 passed.

### Full-suite regression per PR

| PR | Full `uv run pytest` | mypy strict | deptry |
|---|---|---|---|
| PR9a | 707 passed (was 698) | clean, 93 files | clean |
| PR9b | 719 passed (was 707) | clean, 93 files | clean |
| PR9c | 725 passed (was 719) | clean, 93 files | clean |

### Measured changed lines

- **PR9a** (vs `ft/run-metadata-capture-08b-parse`): 266 changed lines
  code+tests (128+1 `routes/runs.py`, 135+2 `test_routes_runs.py`) + 28
  lines `tasks.md` bookkeeping (two commits) — well under budget.
- **PR9b** (vs PR9a's branch): 269 changed lines (`test_ingestion.py`
  only) — well under budget.
- **PR9c** (vs PR9b's branch): 147 changed lines (`test_ingestion.py`
  only) — well under budget.
- **Total**: 682 code+tests + 28 bookkeeping = 710 changed lines across
  three PRs, none individually over budget.

### Deviations from Design

**Two completeness additions beyond the task list's literal text**,
both already recorded inline above: (1) task 9.1's wording names only
`source_file`'s shape as the drop-whole trigger; this batch also drops
whole for an unrecognised `status`/`format`, since D96 explicitly forbids
any Pydantic constraint on either field and the SQL `CHECK` on both
columns would otherwise be reachable from client-controlled wire content.
(2) Task 9.7's CR/LF half is a **confirmation** test proving Phase 9's new
`MetadataReport`/`MetadataFileReport` schemas inherit `errors.py`'s
pre-existing `safe_segment` protection, not a RED-then-GREEN pair — no new
production code was needed or written for it, which is itself the
evidence the models are wired correctly into the existing rejection path.

### Issues Found

None beyond the two completeness additions above.

### Git / PR state (this batch)

- PR9a: https://github.com/guillegil/vantage/pull/100 — base
  `ft/run-metadata-capture-08b-parse`, head
  `ft/run-metadata-capture-09a-normalizer`. Open.
- PR9b: https://github.com/guillegil/vantage/pull/101 — base PR9a's
  branch, head `ft/run-metadata-capture-09b-taxonomy`. Open.
- PR9c: base PR9b's branch, head `ft/run-metadata-capture-09c-threat-
  matrix`. Opened this batch (see the apply-phase return summary for the
  URL).
- None merged yet — chain merges in order once every slice up to this one
  is reviewed, per `feature-branch-chain`.

## Batch: Phase 10 (PR10a/PR10b) — the read filter

**Scope**: all of Phase 10 (read filter, tasks 10.1-10.8), per this
launch's explicit scope narrowing to Phase 10 only. Phase 11 (RQ-25
measurement + docs) not started.

### Re-slicing rationale

The combined diff measured 511 changed lines against
`ft/run-metadata-capture-09c-threat-matrix`, 28% over the 400-line budget,
before any bookkeeping (this project's own ~1.9x historical under-forecast
note: 220 forecast x 1.9 ≈ 418, and this measured higher still). An honest
seam existed, the same shape Phases 7/8/9 already used: task 10.3's
`list_runs` filter half is independently complete and independently
testable without its `count_runs_predating_metadata_key` (Q2's horizon)
half, only the reverse. Cut into two PRs rather than accepting a
`size:exception`:

- `ft/run-metadata-capture-10-read-filter` (10.1-10.3's filter half → PR9c,
  #103) — 277 changed lines
- `ft/run-metadata-capture-10b-horizon` (10.3's horizon half + 10.4-10.8 →
  PR10a) — 252 changed lines

### Tasks completed

- [x] 10.1 RED (PR10a): `test_routes_read.py` — 3 tests: the filter returns
  only matching runs (served by `idx_run_metadata_key_value`'s two-column
  point lookup — `rm.value = ?` also excludes any declared-but-dropped row
  for free, since SQL NULL never equals a bound string); one param without
  the other is `422 invalid_metadata_filter`, naming the missing field;
  an unknown key/value yields `items: []`, not an error. Confirmed RED
  (16 assertions failed, `AssertionError` not collection error) before
  `errors.py`/`storage.py`/adapters/`read.py` were touched.
- [x] 10.2 GREEN (PR10a): `errors.py` — `InvalidMetadataFilterError` (422).
- [x] 10.3 GREEN, split: `list_runs(..., metadata_key=None,
  metadata_value=None)` (PR10a, both adapters) and
  `count_runs_predating_metadata_key(key)` (PR10b, both adapters), both
  served by `idx_run_metadata_key_value` — the point lookup and the
  key-only left-anchored seek respectively.
- [x] 10.4 RED (PR10b): `test_routes_read.py` — 4 tests: predating runs
  excluded and counted; `predating` equals total run count when the key
  was never declared; `metadata_horizon: null` with no filter.
  **Completeness addition**: a declared-but-dropped key
  (`status='value_too_large'`, `value IS NULL`) still counts towards
  `first_seen` — proven directly, since D95's whole reason for keeping a
  dropped-key row is exactly to keep this count honest.
- [x] 10.5 GREEN (PR10b): `MetadataHorizonResponse`;
  `RunListResponse.metadata_horizon`; `routes/read.py` wiring.
- [x] 10.6 GREEN (PR10b): `v1.yaml` — `MetadataHorizon` schema, widened
  `RunListResponse`, `GET /runs` query params + description.
- [x] 10.7 GREEN: `test_read_only_surface.py` binding entries for the
  filter's happy/422 calls (PR10a); `test_interface_document.py`'s
  schema-binding table for `MetadataHorizon` (PR10b).
- [x] 10.8 Verify (PR10b): `uv run pytest packages/vantage/tests/test_
  routes_read.py packages/vantage/tests/test_read_only_surface.py
  packages/vantage/tests/test_interface_document.py` — 96 passed.

### Full-suite regression per PR

| PR | Full `uv run pytest` | mypy strict | deptry |
|---|---|---|---|
| PR10a | 731 passed (was 725) | clean, 93 files | clean |
| PR10b | 739 passed (was 731) | clean, 93 files | clean |

### Measured changed lines

- **PR10a** (vs `ft/run-metadata-capture-09c-threat-matrix`): 254
  insertions, 23 deletions = 277 changed lines — under budget.
- **PR10b** (vs PR10a's branch): 238 insertions, 14 deletions = 252
  changed lines — under budget.
- **Total**: 511 changed lines across two PRs, neither individually over
  budget.

### Deviations from Design

None. The `idx_run_metadata_key_value` seek shape and Q2's `first_seen`
definition match D100 exactly; the one completeness addition (10.4's
declared-but-dropped-key test) is coverage, not a behavioural deviation.

### Issues Found

None. `MAX_METADATA_KEY_CHARS` remains unenforced (flagged in the Phase 9
batch record above) — left alone per this batch's explicit instruction to
defer it to `sdd-verify`.

### Git / PR state (this batch)

- PR10a: https://github.com/guillegil/vantage/pull/103 — base
  `ft/run-metadata-capture-09c-threat-matrix`, head
  `ft/run-metadata-capture-10-read-filter`. Open.
- PR10b: base PR10a's branch, head `ft/run-metadata-capture-10b-horizon`.
  Opened this batch (see the apply-phase return summary for the URL).
- None merged yet — chain merges in order once every slice up to this one
  is reviewed, per `feature-branch-chain`.

## Batch: Phase 11 (PR11a/PR11b) — RQ-25 measurement + docs, final phase

**Scope**: all of Phase 11 (tasks 11.1-11.5), per this launch's explicit
scope narrowing to Phase 11 only. **This is the last phase of the change.**

### Re-slicing rationale

The combined diff (harness script + measured numbers + spec paragraph +
README + test marker) measured 459 changed lines against PR10b, 15% over
the 400-line budget. An honest seam existed, the same shape Phases 7-10
already used: the harness script is independently complete and runnable
without the docs that transcribe its output, only the reverse. Cut into
two PRs rather than accepting a `size:exception`:

- `ft/run-metadata-capture-11a-measurement-script` (script alone → PR10b,
  #104) — 355 changed lines
- `ft/run-metadata-capture-11b-docs` (measured numbers + spec + README +
  test marker → PR11a) — 104 changed lines

### Tasks completed

- [x] 11.1 (PR11a): `scripts/measure_metadata_overhead.py`, copying
  `measure_vcs_overhead.py`'s shape. Three arms: A = `--vantage` alone;
  B = `--vantage --vantage-metadata` against the worst legitimate
  declaration (`MAX_DECLARED_FILES`=16 files at `MAX_DECLARED_FILE_BYTES`
  =8 KiB each), interleaved with A (5 pairs) to isolate this change's own
  added cost; C = the flag alone with nothing declared (Q3's warn path),
  measured separately afterward since it needs the declaration file
  absent rather than present — cannot share the same interleaved pass as
  B without swapping declaration state on every single run.
  **Verified end to end, not just "subprocess exits 0"**: before trusting
  the harness, ran a one-off `--vantage --vantage-metadata` session
  against a real in-process server and inspected the store directly —
  `MetadataFile`/`MetadataEntry` rows for all 3 smoke-test files landed
  with `status="captured"`, confirming the worst-case declaration is
  genuinely read and its keys genuinely persisted before benchmarking it.
- [x] 11.2 Ran the script for real. **Result, not tuned to be
  favourable** (explicit launch instruction, honored): the four B-A
  deltas are -20.5ms (-0.18%, this-repo/10ms), -14.2ms (-0.85%,
  this-repo/1ms), -26.5ms (-0.23%, synthetic/10ms), +29.4ms (+1.71%,
  synthetic/1ms). Three of four negative — metadata capture cannot make
  a session faster, so this is process-spawn noise, not a real effect,
  consistent with D102's own <2ms forecast sitting an order of magnitude
  below what a 5-pair subprocess benchmark can resolve. No re-runs were
  made to chase a cleaner number.
- [x] 11.3 (PR11b): `run-metadata/spec.md`'s Measurements paragraph,
  matching `version-control-context`'s house style — full table, the
  "neither falsified nor confirmed" framing, the standing re-measure
  sentence. `@pytest.mark.req(id="RQ-25")` added to
  `test_metadata_section_is_identical_on_both_reports`
  (`test_run_report.py`) instead of a new assertion — its
  `assert call_count[0] == 1` already proves the O(1)-per-session shape
  claim D102 depends on, no test asserts a raw percentage (RQ-25 is
  Analysis, not Test, per CLAUDE.md's own taxonomy), and this mirrors
  exactly how `test_git_invocation_count_does_not_scale_with_test_count`
  carries the marker for `vcs`.
- [x] 11.4 (PR11b): README — `--vantage-metadata` in the usage block, a
  new paragraph describing the declaration file, its JSON shape, path
  containment, and the "reads and uploads a file a co-worker named"
  disclosure, alongside the existing `--vantage-failure-text` one.
- [x] 11.5 (PR11b): Recorded the number regardless of the budget verdict.
  **Also recorded, unprompted by the task's own literal wording**: RQ-25's
  normative 2% budget text does not exist anywhere in this repository —
  verified by inspection before writing anything, not assumed from the
  launch prompt. `openspec/specs/version-control-context/spec.md:154-158`
  reads its own 4.11%/4.17% (1ms profile) results as "still inside RQ-25's
  2% budget," arithmetically false for those two rows;
  `docs/open-questions.md:133-141` reads the identical numbers as a
  breach and computes ~55ms of headroom from that reading. **Neither
  document was touched.** The new Measurements paragraph names the
  contradiction explicitly and states this change's own cost rides atop
  an already-breached 1ms-profile baseline — a human decision, not one
  made silently inside this batch.

### Full-suite regression per PR

| PR | Full `uv run pytest` | mypy strict | deptry | `req(id="RQ-25")` |
|---|---|---|---|---|
| PR11a | 739 passed (unchanged from PR10b — no production code) | clean, 94 files | clean | 4 (unchanged) |
| PR11b | 739 passed (marker only, no behavior change) | clean, 94 files | clean | 5 (new marker collects) |

### Measured changed lines

- **PR11a** (vs `ft/run-metadata-capture-10b-horizon`): 355 insertions,
  1 deletion (tasks.md checkbox) = 355 changed lines — under budget.
- **PR11b** (vs PR11a's branch): 104 insertions, 4 deletions = 104 changed
  lines — under budget, no `size:exception` needed.
- **Total**: 459 changed lines across two PRs, neither individually over
  budget.

### Deviations from Design

Arm labeling in task 11.1's literal wording ("arm B = `--vantage
--vantage-metadata`; arm C = worst legitimate declaration") is read here
as the reverse pairing: B carries the worst-case declaration (interleaved
with A, since D102's own reasoning for the three-arm design is to isolate
this change's cost against a shared baseline) and C is the flag alone
with nothing declared (measured separately, since it needs the opposite
filesystem state from B). No other deviation — the harness shape, the two
RQ-25 profiles, the five-pair interleaving and the medians-not-means rule
all match `measure_vcs_overhead.py` and design.md D102 exactly.

### Issues Found

None new. `MAX_METADATA_KEY_CHARS` remains unenforced (flagged in the
Phase 9 batch record above) — left alone per this batch's explicit
instruction to defer it to `sdd-verify`. The RQ-25 budget-text
contradiction (see 11.5 above) is named, not fixed, per explicit
instruction — a human decision for `sdd-verify` or later, not this batch.

### Git / PR state (this batch)

- PR11a: https://github.com/guillegil/vantage/pull/105 — base
  `ft/run-metadata-capture-10b-horizon`, head
  `ft/run-metadata-capture-11a-measurement-script`. Open, 12/12 CI green.
- PR11b: https://github.com/guillegil/vantage/pull/106 — base PR11a's
  branch, head `ft/run-metadata-capture-11b-docs`. Open, 12/12 CI green.
- None merged yet — chain merges in order once every slice (#88-#106) is
  reviewed, per `feature-branch-chain`. Tracker `ft/run-metadata-capture`
  then aggregates the whole feature into `main`.

## Workload / PR Boundary

- Mode: chained PR slices (`feature-branch-chain`); Phase 11 re-sliced
  from 1 planned PR into 2 for size, not by launch instruction
- Current work unit: Phase 11 (RQ-25 measurement + docs), complete —
  **this was the last work unit of the change**
- Boundary: starts from PR10b's tip (metadata fully captured, stored,
  filterable and horizon-counted) and ends with a real, committed RQ-25
  measurement, the capability spec's own Measurements paragraph, the
  README documenting the feature end-to-end, and the RQ-25 marker on the
  test proving the shape claim the measurement depends on
- Estimated review budget impact: PR11a (355 lines) and PR11b (104 lines)
  are both comfortably under budget; no `size:exception` needed this batch

## Remaining Tasks

None. All 67 tasks across all 11 phases are complete.

## Status (superseded by Phase 12 below — kept for history)

67/67 tasks complete across Phase 1 (4/4), Phase 2 (7/7), Phase 3 (3/3),
Phase 4 (6/6), Phase 5 (8/8), Phase 6 (6/6), Phase 7 (6/6), Phase 8 (6/6),
Phase 9 (8/8), Phase 10 (8/8) and Phase 11 (5/5, this batch — final).
PR1 through PR11b (#88-#106) open and green, not merged (chain merges in
order at the end, then the tracker aggregates to `main`). Ready for
`sdd-verify`.

---

## Phase 12 (PR12 → `ft/run-metadata-capture-11b-docs`): sdd-verify remediation

`sdd-verify` (2026-09-04, chain tip `339f5ca`) returned **FAIL**: 2 critical, 4
warning, 4 suggestion, on an otherwise 67/67-task, 739/739-test, 33/33-scenario
green chain. Three findings (CRITICAL-1, CRITICAL-2, WARNING-1) were routed back
to `sdd-apply` as **one slice on the chain tip**; three others (WARNING-2/3/4)
were classified pre-existing and explicitly out of scope here. Both suggestions
(SUGGESTION-1, SUGGESTION-2) were also fixed, in scope of the same launch prompt.

### Branch / PR

- Branch: `ft/run-metadata-capture-12-verify-fixups`, created from the chain tip
  `ft/run-metadata-capture-11b-docs` (`339f5ca`).
- PR targets `ft/run-metadata-capture-11b-docs` (feature-branch-chain,
  unchanged strategy).
- One PR, not re-sliced — 287 changed lines (code+tests), comfortably under the
  400-line budget.

### TDD Cycle Evidence (Strict TDD Mode)

| Task | RED | GREEN | REFACTOR |
| --- | --- | --- | --- |
| 12.1 NUL-byte path (`resolve_declared_path`) | Reverted `except (OSError, RuntimeError)`, ran `test_a_path_containing_a_nul_byte_is_rejected_not_crashed` — `ValueError: lstat: embedded null character in path`, uncaught, test failed with the raw traceback (not an assertion failure) | Restored `except Exception`, test passed | Generalised module docstring: a third exception mechanism disproves an enumerated list twice; documented why `except Exception` is the more honest shape here (no resource held, no caller-visible invariant to hide, both exits already return `None`) |
| 12.1 NUL-byte path (`read_declaration`) | Removed the new `"\x00" in path` check, ran `test_a_path_containing_a_nul_byte_captures_nothing_and_warns_once` — failed, `result` was a real `DeclaredFile`, not `None` | Restored the check, test passed | — |
| 12.2 key length (`read_declaration`) | Removed the new `len(key) > MAX_DECLARED_KEY_CHARS` check, ran `test_a_key_longer_than_the_bound_captures_nothing_and_warns_once` — failed, `result` was a real `DeclaredFile` with the 1025-char key intact | Restored the check, test passed | Added `MAX_DECLARED_KEY_CHARS` mirror constant + `test_the_mirrored_key_char_bound_matches_the_server`, same shape as the existing `MAX_METADATA_ENTRIES` mirror |
| 12.3 index usage (`_LIST_RUNS_BY_METADATA`) | Reverted the query to its original `WHERE EXISTS` form, ran `test_list_runs_by_metadata_uses_the_key_value_index` — failed, plan was exactly the bad one from the verify report (`SCAN run USING INDEX idx_run_started_at` / `CORRELATED SCALAR SUBQUERY 1` / `SEARCH rm USING INDEX sqlite_autoindex_run_metadata_1 (run_id=? AND key=?)`) | Restored the `WHERE id IN (...)` rewrite, test passed, plan now `SEARCH run USING INDEX sqlite_autoindex_run_1 (id=?)` / `LIST SUBQUERY 1` / `SEARCH rm USING INDEX idx_run_metadata_key_value (key=? AND value=?)` | Rewrote the query's own comment; corrected `docs/schema-manifest.md:380`, `design.md:650`, `test_routes_read.py:1394`'s docstring — verified `core/ports/storage.py:241` and `storage/memory.py:226` already state the true thing and left them unedited |
| 12.5 no-backfill (SUGGESTION-1) | New test asserted differing-value replay leaves the first value — this is a NEW test, not a bug fix, so RED was "test doesn't exist yet"; ran it against the (already-correct) `INSERT OR IGNORE` behaviour and it passed immediately (no production change needed, `INSERT OR IGNORE` already had this property, only the test coverage was missing) | Passed on first run | — |
| 12.6 dead constant (SUGGESTION-2) | N/A — refactor, not a behavior bug; existing `test_unsupported_content_type_is_treated_the_same_as_malformed` already covered the `toml` -> `malformed` case both before and after | All 13 `test_metadata_parse.py` tests pass unchanged | `parse()`'s dispatch is a membership check + one-line ternary instead of `if`/`elif`/`else` |

### Work Unit Evidence

| Evidence | Value |
| --- | --- |
| Focused test command and result | `uv run pytest packages/pytest-vantage/tests/test_metadata_declaration.py packages/pytest-vantage/tests/test_metadata_containment.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_metadata_parse.py packages/vantage/tests/vantage_port_contract.py -q` — all passed (33 + 65 + 13 + contract tests, no failures) |
| Runtime harness command/scenario and result | `uv run pytest -q` (whole workspace) — **746 passed**, 0 failed, 12 expected warnings (was 739 before this batch; +7 new tests, 0 removed). `uv run mypy .` strict — 94 files, clean. `uv run ruff format . && uv run ruff check --fix .` — clean, zero files reformatted. `uv run deptry .` — clean |
| Rollback boundary | Each of the 6 commits on `ft/run-metadata-capture-12-verify-fixups` is independently revertible: the verify-report commit and its NUL-byte-fix follow-up touch only `verify-report.md`; the declaration-boundary fix touches only `pytest_vantage/metadata.py` + its two test files; the index fix touches only `sqlite_store.py` + `test_sqlite_store.py` + three prose files; the two suggestion commits touch one file each |

### Deviations from Design

None from `design.md`'s D91-D102 decisions. Two prose corrections were made to
`design.md` itself (D100's paragraph at line 650) because the verify report
found it stated something imprecise about which query is left-anchored on `key`
alone versus seeked on the full `(key, value)` pair — corrected to match the
implementation, not the other way around.

### Issues Found

- The committed `verify-report.md` (untracked before this batch) contained a
  literal NUL byte where the CRITICAL-1 prose meant to show the two-character
  escape `\0`, which made git/GitHub classify the whole markdown file as
  binary. Fixed in a separate one-line-purpose commit before touching any
  fix content, so the report's own text is legible in the PR diff.
- `core/ports/storage.py:241` and `storage/memory.py:226` were named among the
  "six false claims" in the launch prompt; on inspection both already state
  the query's real behaviour correctly (they distinguish the filter's full
  `(key, value)` lookup from the horizon count's `key`-only one) and needed no
  edit. Verified, not assumed — recorded here so the count is auditable: 4 of
  6 files edited, 2 of 6 confirmed already-true.

### Git / PR state (this batch)

- Branch: `ft/run-metadata-capture-12-verify-fixups`, 6 commits, based on
  `ft/run-metadata-capture-11b-docs` @ `339f5ca`.
- PR: opened against `ft/run-metadata-capture-11b-docs` — see PR URL in the
  `sdd-apply` return envelope for this batch.
- 287 changed lines (code+tests: ~226 insertions across 8 source/test files;
  bookkeeping/docs: ~61 across `schema-manifest.md`, `design.md`, `tasks.md`,
  this file). The committed `verify-report.md` (338 lines, a pre-produced
  artifact from the prior `sdd-verify` phase, not authored fresh for review
  here) is excluded from that count, matching this project's generated-content
  convention; included, the branch diff is 625 changed lines across 11 files.

## Workload / PR Boundary (Phase 12)

- Mode: single PR, `feature-branch-chain` unchanged — no re-slicing needed,
  287 changed lines is well under the 400-line budget
- Current work unit: Phase 12 (sdd-verify remediation: CRITICAL-1, CRITICAL-2,
  WARNING-1, SUGGESTION-1, SUGGESTION-2) — complete
- Boundary: starts from the chain tip exactly as `sdd-verify` left it (67/67
  tasks, 739 tests) and ends with the two blockers and the one warning fixed,
  both suggestions applied, and the query-plan regression test in place so
  CRITICAL-2's specific failure mode cannot silently regress again
- Estimated review budget impact: 287 lines, one PR, comfortably reviewable in
  well under 60 minutes

## Remaining Tasks

None. 67/67 original tasks plus 8/8 Phase 12 remediation tasks complete.

## Status

75/75 tasks complete (67 original + 8 Phase 12). PR1 through PR11b (#88-#106)
plus PR12 (`ft/run-metadata-capture-12-verify-fixups` → PR11b) all open.
Nothing merged yet — the chain merges in order once every slice is reviewed,
then the tracker `ft/run-metadata-capture` aggregates the whole feature into
`main`. Next: `sdd-verify` (re-verify with PR12 included), or merge order if
already re-verified.
