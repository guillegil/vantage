# Tasks: run-metadata-capture

Strict TDD: every implementation task is preceded by its RED test task.
`chain_strategy: feature-branch-chain`. Tracker branch: `ft/run-metadata-capture`
(draft, no-merge until every slice below is reviewed and merged in order).

**Re-slicing note.** The design's 7-slice plan (~2,160 lines) is split here into
**11 slices**, for two reasons. (1) This project's forecasts run ~1.9x under
measured (`user-configuration`: 450–600 forecast, ~1,090 measured); design's own
slice 1 (~360 est.) bundles schema DDL + 3 port dataclasses + both adapters +
contract tests + a refusal test + manifest docs + a ~220-line ADR — recomputed
per-file that is ~750+ lines, already 2x its own estimate before any correction.
(2) The hard rollback constraint — ADR-0017 must not arrive *after* the
irreversible `schema_version` 4 bump — is satisfied more safely by landing the
ADR **strictly before** the bump merges (Slice 1, PR1) rather than bundling both
in one oversized diff (Slice 2, PR2, targets PR1's branch). Authorization arriving
before the point of no return satisfies D101 at least as well as arriving with it.

## Suggested Work Units

| # | Goal | Base | Est. lines | Focused test | Runtime harness | Rollback boundary |
|---|------|------|-----------:|---------------|------------------|--------------------|
| 1 | ADR-0017, Status: Proposed | tracker | ~220 | N/A — doc-only | N/A — no code path exists yet | Revert the one new file |
| 2 | Schema bump: 2 tables, index 15, `_SCHEMA_VERSION=4`, manifest, refusal test | PR1 | ~164 | `uv run pytest packages/vantage/tests/test_schema_manifest.py packages/vantage/tests/test_connection.py` | N/A until PR4 wires a writer | Clean revert only *before* any DB opened at v4 (ADR-0013) |
| 3 | Core vocabulary: `core/domain/metadata.py` frozensets + bounds | PR2 | ~220 | `uv run pytest packages/vantage/tests/test_metadata.py` | N/A — pure, no I/O | Delete module + its import sites |
| 4 | Port dataclasses + both adapters + contract tests | PR3 | ~370 | `uv run pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_storage_types.py` | `uv run pytest packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py` | Revert; `metadata=` kwarg has a default, no call site broke |
| 5 | Plugin flag + resolver + `--help` denial + C1/C2/C3 + Q3 warning | PR4 | ~380 | `uv run pytest packages/pytest-vantage/tests/test_config.py packages/pytest-vantage/tests/test_opt_in.py` | `uv run pytest packages/pytest-vantage` (real subprocess-run differential) | Flag removed, unflagged sessions unaffected |
| 6 | Path containment: `resolve_declared_path`, symlink/loop/FIFO/absolute/`..` | PR5 | ~380 | `uv run pytest packages/pytest-vantage/tests/test_metadata.py` | Real `tmp_path` + `os.symlink`/`os.mkfifo` | Revert; flag-gated, unreachable otherwise |
| 7 | Declaration read, bounds, `_metadata_section()`, both reports | PR6 | ~350 | `uv run pytest packages/pytest-vantage/tests/test_metadata.py packages/pytest-vantage/tests/test_report_budget.py packages/pytest-vantage/tests/test_run_report.py` | In-process `_LiveServer`, real start+finish POST pair | Revert; wire section additive, `extra="ignore"` |
| 8 | `MetadataReport`/`MetadataFileReport` schemas + `metadata_parse.py` (JSON/YAML) | PR7 | ~250 | `uv run pytest packages/vantage/tests/test_metadata_parse.py packages/vantage/tests/test_schemas.py` | N/A — pure parse functions | Revert; module unreferenced until PR9 |
| 9 | `_to_run_metadata`, `routes/runs.py` wiring, 11 taxonomy tests, PyYAML dep | PR8 | ~350 | `uv run pytest packages/vantage/tests/test_routes_runs.py packages/vantage/tests/test_ingestion.py` | ASGI in-process client, real POST /api/v1/runs | Revert; `metadata=` still defaults empty |
| 10 | Read filter: query params, horizon count, `v1.yaml`, binding test | PR9 | ~220 | `uv run pytest packages/vantage/tests/test_routes_read.py packages/vantage/tests/test_read_only_surface.py packages/vantage/tests/test_interface_document.py` | ASGI in-process, real GET /api/v1/runs | Revert; additive query params, `metadata_horizon` nullable |
| 11 | `scripts/measure_metadata_overhead.py`, Measurements paragraph, README, `budget.py` docstring | PR10 | ~180 | `python scripts/measure_metadata_overhead.py` (manual harness run, not pytest) | Same in-process `_LiveServer` A/B harness as `measure_vcs_overhead.py` | Revert; measurement is documentation, not behavior |

**`Base` is the branch each PR targets, and the chain is strictly linear** (`feature-branch-chain`): PR1 targets the tracker, PR*n* targets PR*n-1*'s branch. Content dependencies that skip backwards -- PR8 needs PR3's vocabulary, PR9 needs PR4's port and PR7's wire section, PR10 needs PR2's schema -- are satisfied transitively by the chain and are NOT alternative bases. A PR has exactly one base.

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High
```

## Phase 1 (PR1 → tracker): ADR-0017

- [x] 1.1 Write `docs/adr/0017-store-user-declared-configuration-values-read-from-the-test-repository.md`. Nygard + `Alternatives rejected` (ADR-0016's shape). `Status: Proposed`.
- [x] 1.2 Decision section: what is authorised (declared top-level scalars only, never file bodies, D-k deferred); the five conditions C1–C5 held together, inheriting ADR-0016's register; the EAV justification (D-e); the must-not-fail-the-run rule (D97); what is not authorised (host env, arbitrary bodies, server-directed reads, web editing, backfill).
- [x] 1.3 Consequences section: read exposure stated not mitigated; reversal cost (`schema_version` 3→4, refuse not migrate, ADR-0013); RQ-25 O(1)-measured obligation; unbounded growth named not solved; Q2's horizon published not implied. Bind to ADR-0013, ADR-0014, ADR-0016, RQ-2/24/25/26/28/29/44, and `run-metadata`/`opt-in-activation`/`session-ingestion`/`recording-schema`/`history-read-api`.
- [x] 1.4 PR description: `Status` flips to `Accepted` on merge (CLAUDE.md); no test surface, this PR is Inspection-only.

## Phase 2 (PR2 → PR1): Schema bump — the irreversible point

- [ ] 2.1 RED: extend `test_connection.py` — opening a database stamped `meta.schema_version='3'` with this build is refused, naming both versions and the path (ADR-0013 proven).
- [ ] 2.2 RED: update `test_schema_manifest.py:216-221` literals 11/130/14 → 13/139/15 (fails until 2.3).
- [ ] 2.3 GREEN: `schema.sql` — add `run_metadata_file`, `run_metadata`, `idx_run_metadata_key_value` (D91) between `user_setting` and `-- Indexes`; update header counts and the "fourteen in total" comment; stamp `'4'`.
- [ ] 2.4 GREEN: `connection.py` — `_SCHEMA_VERSION = 4`.
- [ ] 2.5 GREEN: `docs/schema-manifest.md` — two new `###` sections (`run_metadata_file`, `run_metadata`, column-for-column) and corrected header counts (D91).
- [ ] 2.6 Verify 2.1–2.2 pass; run `uv run pytest packages/vantage/tests/test_schema_manifest.py packages/vantage/tests/test_connection.py`.
- [ ] 2.7 PR description: flag `docs/schema-manifest.md:364-403`'s pre-existing "Table count 10"/"Index count 13" drift (2026-08-15) as NOT this PR's obligation, so a reviewer does not misattribute it.

## Phase 3 (PR3 → PR2): Core vocabulary

- [ ] 3.1 RED: `packages/vantage/tests/test_metadata.py` — `FILE_STATUSES`/`KEY_STATUSES` are plain-`str` `frozenset`s (never `Enum`, per `liveness.py`'s 3.10-vs-3.13 `__format__` precedent); membership covers exactly D91's SQL `CHECK` lists.
- [ ] 3.2 GREEN: create `packages/vantage/src/vantage/core/domain/metadata.py` — `FILE_STATUSES`, `KEY_STATUSES`, `MAX_METADATA_VALUE_BYTES`, `MAX_METADATA_KEY_CHARS`, `MAX_METADATA_ENTRIES` (D94, D95). No logic beyond vocabulary (RQ-26).
- [ ] 3.3 RED+GREEN: extend `test_architecture.py`'s RQ-26 purity walk to include `metadata.py` importing nothing outside stdlib.

## Phase 4 (PR4 → PR3): Port dataclasses + both adapters + contract

- [ ] 4.1 RED: `test_storage_types.py` — `MetadataFile`, `MetadataEntry`, `RunMetadata`, `EMPTY_RUN_METADATA` are frozen, `slots=True`, and `RunMetadata()` equals `EMPTY_RUN_METADATA`.
- [ ] 4.2 GREEN: `core/ports/storage.py` — add the three dataclasses + `EMPTY_RUN_METADATA`; `ExecutionStore.record_session` gains `metadata: RunMetadata = EMPTY_RUN_METADATA` (D98). No existing call site changes.
- [ ] 4.3 RED: `vantage_port_contract.py` — metadata round-trip persists both tables; `INSERT OR IGNORE`/`setdefault` makes a second `record_session` call for the same run a no-op (write-once, D-b); a finish-only session (no prior start-write) records the same rows a start+finish pair would.
- [ ] 4.4 GREEN: `sqlite_store.py` — two `INSERT OR IGNORE` statements appended inside the existing `BEGIN IMMEDIATE … COMMIT` transaction, after the result insert, referencing `run(id)` (D98).
- [ ] 4.5 GREEN: `memory.py` — mirror with two `dict[tuple[str, str], …]` using `setdefault` (second-mechanism discipline, D98).
- [ ] 4.6 Verify 4.1/4.3 pass on both adapters: `uv run pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_storage_types.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py`.

## Phase 5 (PR5 → PR4): Plugin flag

- [ ] 5.1 RED: `test_config.py` — `resolve_metadata_capture(activated, cli_opt_in)` signature carries **no** ini/env parameter; monotone conjunction table (T/T→T, else F).
- [ ] 5.2 GREEN: `pytest_vantage/config.py` — `resolve_metadata_capture`.
- [ ] 5.3 RED: `test_opt_in.py` — **C1** differential: `vantage-metadata.json` present, flag absent → byte-identical tree vs `-p no:vantage`, zero connections. **C3**: shipped `--help` contains "there is no ini equivalent" and never "or the ini equivalent is given" for `--vantage-metadata`.
- [ ] 5.4 GREEN: `pytest_vantage/plugin.py` — register `--vantage-metadata` in the existing `group.addoption` block; `_metadata_capture_requested`, short-circuited on `_activation_requested` (mirrors `plugin.py:157-158`); called identically on both xdist branches.
- [ ] 5.5 RED: **C2** — a `_CallRecorder`-wrapped `open` asserts the declaration is opened zero times when either gate is closed (`test_vcs.py`'s shape).
- [ ] 5.6 RED: Q3 scenario — flag set, no `vantage-metadata.json` present → exactly one pytest warning, run otherwise unaffected; a declaration present emits none.
- [ ] 5.7 GREEN: wire `metadata_requested=_metadata_capture_requested(config)` into the `Recorder(...)` construction on the controller only (no `Recorder` on an xdist worker, D99).
- [ ] 5.8 Verify: `uv run pytest packages/pytest-vantage/tests/test_config.py packages/pytest-vantage/tests/test_opt_in.py packages/pytest-vantage/tests/test_vcs.py`.

## Phase 6 (PR6 → PR5): Path containment

- [ ] 6.1 RED: `test_metadata.py` (plugin) — `resolve_declared_path` rejects: absolute path, `..` escape, a real symlink pointing outside `rootpath`, a symlink loop (`RuntimeError` caught), a directory, a real FIFO (`os.mkfifo`, skip where unsupported), a path equal to `rootpath` itself — each **rejected, never clamped**. Accepts a legitimate nested path, and a root reached through a symlink still accepts its own children.
- [ ] 6.2 GREEN: create `packages/pytest-vantage/src/pytest_vantage/metadata.py` — `resolve_declared_path` exactly as D93: resolve both `rootpath` and candidate, `is_relative_to`, `is_file`, catch `OSError`/`RuntimeError`.
- [ ] 6.3 RED: `read_declaration` — absent file, non-JSON, non-object, `version != 1`, missing `path`/`format`/`keys`, unknown `format`, duplicate stored key, over `MAX_DECLARED_FILES` — each captures nothing and warns **exactly once** via `_warn`.
- [ ] 6.4 GREEN: `read_declaration` in `metadata.py`; constants `DECLARATION_FILENAME`, `MAX_DECLARED_FILES=16`, `MAX_DECLARED_PATH_CHARS=1024` (D94).
- [ ] 6.5 Threat-matrix RED: blocking-open guard — a real FIFO at a declared path must not hang `pytest_sessionstart` (bounded-wall-time assertion, not just outcome).
- [ ] 6.6 Verify: `uv run pytest packages/pytest-vantage/tests/test_metadata.py`.

## Phase 7 (PR7 → PR6): Read, bound, ship

- [ ] 7.1 RED: `test_metadata.py` — a file at `MAX_DECLARED_FILE_BYTES` (8,192) is kept; one byte over is dropped whole, marked `too_large`; files past `MAX_METADATA_SECTION_BYTES` (32,768) are marked `over_budget` in declaration order; a non-UTF-8 file is marked `not_text` before JSON encoding; a permission-denied open is marked `unreadable` (class 5, the eighth plugin-side class).
- [ ] 7.2 GREEN: `metadata.py` — `capture_metadata`: per-file read ≤8,192 bytes, UTF-8 decode, `_encoded_cost` charge against 32,768 bytes reusing `budget.py`'s exact `_encoded_cost` rule (no `ensure_ascii=False`); every failure sets a status, content=`None`, never raises (D97).
- [ ] 7.3 GREEN: `budget.py` — docstring-only addition recording the finish-write headroom drop from ~1,038 to ~973 results (D94).
- [ ] 7.4 RED: `recorder.py` — identical serialized `metadata` bytes appear on the start report and the finish report (D51 freeze rule extended, D96).
- [ ] 7.5 GREEN: `recorder.py` — `self._metadata` captured once in `__init__` beside `self._vcs`; `_metadata_section()` mirroring `_vcs_section()`; wired into both report builds.
- [ ] 7.6 Verify: `uv run pytest packages/pytest-vantage/tests/test_metadata.py packages/pytest-vantage/tests/test_report_budget.py packages/pytest-vantage/tests/test_run_report.py`.

## Phase 8 (PR8 → PR2, PR3): Server parse engine

- [ ] 8.1 RED: `test_schemas.py` — `MetadataFileReport`/`MetadataReport` accept a declared value of arbitrary length and content without raising; **no `max_length`, no pattern, no constraint of any kind** on any field in the metadata section (the D96 trap, made a falsifier before it can be committed by accident).
- [ ] 8.2 GREEN: `service/schemas.py` — `MetadataFileReport`, `MetadataReport` (`extra="forbid"`, matching `VcsReport`), `SessionReport.metadata: MetadataReport | None` (envelope stays `extra="ignore"`, D96).
- [ ] 8.3 RED: `test_metadata_parse.py` (new) — malformed YAML, malformed JSON, 1,000-deep JSON nesting (`RecursionError`, not `JSONDecodeError`), a YAML alias-expansion bomb that `safe_load` would expand and `compose` does not (bounded wall-time asserted), a `!!python/object/apply` document yields `malformed` and executes nothing, a non-scalar value, an absent key, a value over `MAX_METADATA_VALUE_BYTES`.
- [ ] 8.4 GREEN: create `packages/vantage/src/vantage/service/metadata_parse.py` — the **only** module importing `yaml`; `yaml.compose()` + walk top-level `ScalarNode`s only (never `safe_load`/`load`); `json.loads`; catch `YAMLError`, `JSONDecodeError`, `RecursionError` → all become class 7 `malformed` (D97).
- [ ] 8.5 GREEN: `packages/vantage/pyproject.toml` — add `PyYAML` to `vantage.service`'s dependencies only.
- [ ] 8.6 Verify: `uv run pytest packages/vantage/tests/test_metadata_parse.py packages/vantage/tests/test_schemas.py`; `uv run deptry .` — PyYAML declared and used in `vantage` only, never in `pytest-vantage`.

## Phase 9 (PR9 → PR4, PR7, PR8): Server ingest wiring

- [ ] 9.1 RED: `test_routes_runs.py` — a metadata section with an oversized/absolute/`..` `source_file` is dropped, never rejected (D93's server re-check); no `422` reaches the client for it.
- [ ] 9.2 GREEN: `routes/runs.py` — `_to_run_metadata(payload.metadata)` following `_to_vcs_context`'s shape: re-check `source_file` shape (≤1024 chars, not absolute, no `..`); call `metadata_parse.parse`; classify each declared key into `captured | absent | not_scalar | value_too_large | source_unavailable`; drop-whole everywhere, `truncate()` never called (D95, D97).
- [ ] 9.3 GREEN: wire `metadata=` into the `store.record_session(...)` call.
- [ ] 9.4 RED (RQ-44, `@pytest.mark.req(id="RQ-44")`): `test_ingestion.py` — a report whose metadata section is entirely garbage still yields `201` and a written run row (RQ-44's rule proven, not asserted).
- [ ] 9.5 RED: one integration test per D97 row (11 classes: `not_found`, `path_rejected`, `too_large`, `not_text`, `unreadable`, `over_budget`, `malformed`, `absent`, `not_scalar`, `value_too_large`, server-side-shape-reject) — each asserts the exact `(file.status, key.status)` pair.
- [ ] 9.6 GREEN: implement whatever of 9.2's classification 9.5 finds incomplete.
- [ ] 9.7 RED: a quoting-shaped metadata key round-trips byte-identically through storage and a response; a CR/LF-containing key never appears unescaped in an error body (threat matrix: client-chosen text reaching SQL/response bodies — bound parameters only, `_fields_from_errors`/`safe_segment` reused, never interpolated).
- [ ] 9.8 Verify: `uv run pytest packages/vantage/tests/test_routes_runs.py packages/vantage/tests/test_ingestion.py`.

## Phase 10 (PR10 → PR2, PR9): Read filter

- [ ] 10.1 RED: `test_routes_read.py` — `GET /api/v1/runs?metadata_key=K&metadata_value=V` returns only matching runs; one param without the other → `422 invalid_metadata_filter`; an unknown key/value yields zero matches, not an error.
- [ ] 10.2 GREEN: `errors.py` — `InvalidMetadataFilterError` (422) + `__all__` entry.
- [ ] 10.3 GREEN: `core/ports/storage.py` — `list_runs(..., metadata_key=None, metadata_value=None)`, `count_runs_predating_metadata_key(key)`; both adapters implement, served by `idx_run_metadata_key_value` left-anchored on `key` (D100).
- [ ] 10.4 RED: `test_routes_read.py` — runs predating a declared key are excluded from the match and the response reports `metadata_horizon: {key, predating}`; `predating` equals total run count when the key was never declared; `metadata_horizon: null` when no filter given (Q2).
- [ ] 10.5 GREEN: `RunListResponse.metadata_horizon` field; `routes/read.py` wiring.
- [ ] 10.6 GREEN: hand-edit `service/openapi/v1.yaml` for the widened `GET /runs` operation; run the drift check.
- [ ] 10.7 GREEN: add the binding-table entry in `test_read_only_surface.py` for the widened `read` path.
- [ ] 10.8 Verify: `uv run pytest packages/vantage/tests/test_routes_read.py packages/vantage/tests/test_read_only_surface.py packages/vantage/tests/test_interface_document.py`.

## Phase 11 (PR11 → PR7): RQ-25 measurement + docs

- [ ] 11.1 Create `scripts/measure_metadata_overhead.py` by copying `scripts/measure_vcs_overhead.py`'s harness: same two RQ-25 profiles, five interleaved A/B/A/B pairs, medians reported never means, same in-process `_LiveServer` over `InMemoryExecutionStore`. Arm A = `--vantage`; arm B = `--vantage --vantage-metadata`; arm C = worst legitimate declaration (16 files × 8 KiB).
- [ ] 11.2 Run the script; record the four measured medians (this-repo/synthetic × 10ms/1ms profiles) — a real deliverable, a committed number, not an assertion.
- [ ] 11.3 Add the `run-metadata` capability spec's own Measurements paragraph with the medians and the standing re-measure sentence, `@pytest.mark.req(id="RQ-25")` cross-reference in whichever test asserts the budget comparison.
- [ ] 11.4 Update `README` with the new flag and declaration file.
- [ ] 11.5 Whether or not the 2% budget holds, record the number anyway — the 1ms profile is already breached before this change starts (D102); state that explicitly, not silently.

## Cross-cutting rules (apply throughout)

- No new `RQ-xx` identifiers. New obligations cite the capability/scenario only. Only tests verifying an *existing* RQ (RQ-2 in Phase 5, RQ-24 in Phase 8, RQ-25 in Phase 11, RQ-26 in Phase 3, RQ-29 in Phase 2, RQ-44 in Phase 9) carry `@pytest.mark.req(id="RQ-xx")`, always as a keyword, never positional.
- Every RED test names its capability + scenario in its docstring (no `req` marker on new-obligation tests).
- `uv run mypy .` (strict) and `uv run ruff format . && uv run ruff check --fix .` run at the end of every phase, not only at the end of the chain.
- Each PR body includes: Chain Context section, dependency diagram marking the current PR with `📍`, and the Phase 2 / Phase 10 notes above verbatim.

## Review Workload Forecast

Estimated changed lines: PR1 ~220, PR2 ~164, PR3 ~220, PR4 ~370, PR5 ~380, PR6 ~380, PR7 ~350, PR8 ~250, PR9 ~350, PR10 ~220, PR11 ~180 — **total ~3,084**, expect the upper half given this project's 1.9x historical under-forecast (design's own uncorrected total: ~2,160).
Chained PRs recommended: Yes
400-line budget risk: High
Decision needed before apply: No
