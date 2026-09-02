# Apply Progress: run-metadata-capture

**Mode**: Strict TDD from Phase 2 onward (Phase 1 was the documented
Strict-TDD exception — doc-only, no code path existed yet, per tasks.md's
own work-unit table). Phase 2 is the first phase implemented under Strict
TDD in full.

## Scope covered so far

- Phase 1 (PR1 → tracker `ft/run-metadata-capture`) — complete.
- Phase 2 (PR2 → PR1) — complete, this batch.
- Phases 3-11 are untouched and remain for future `sdd-apply` batches.

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

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `docs/adr/0017-store-user-declared-configuration-values-read-from-the-test-repository.md` | Created (PR1) | 204-line Nygard ADR, `Status: Proposed` |
| `packages/vantage/src/vantage/storage/schema.sql` | Modified (PR2) | Added `run_metadata_file`, `run_metadata`, `idx_run_metadata_key_value`; corrected header/index-block counts (thirteen tables, fifteen indexes); stamped `'4'` |
| `packages/vantage/src/vantage/storage/connection.py` | Modified (PR2) | `_SCHEMA_VERSION = 3` -> `4` |
| `packages/vantage/tests/test_connection.py` | Modified (PR2) | New RED-first refusal test `test_opening_a_database_created_by_the_previous_schema_version_is_refused` (`@pytest.mark.req(id="RQ-29")`); updated the three pre-existing version-literal tests to move with the bump (`newer` seed 4->5, `current` seed 3->4, username-lookup assertion 3->4) |
| `packages/vantage/tests/test_schema_manifest.py` | Modified (PR2) | Ground-truth literals 11/130/14 -> 13/139/15 |
| `docs/schema-manifest.md` | Modified (PR2) | Two new `###` table sections (`run_metadata_file`, `run_metadata`); "Eleven tables, fourteen indexes" -> "Thirteen tables, fifteen indexes"; index list gained item 15 |
| `openspec/changes/run-metadata-capture/tasks.md` | Modified (PR1 + PR2) | Phase 1 tasks 1.1-1.4 and Phase 2 tasks 2.1-2.7 marked `[x]` |

## Work Unit Evidence (PR2)

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest packages/vantage/tests/test_schema_manifest.py packages/vantage/tests/test_connection.py` — 20 passed |
| Runtime harness command/scenario and result | N/A until PR4 wires a writer — both new tables exist empty by design (RQ-29, ADR-5); confirmed via `uv run pytest -m 'req(id="RQ-29")'` — 1 passed, 592 deselected (proves the keyword marker filters correctly, not positionally) |
| Rollback boundary | Clean revert only *before* any database has been opened at `schema_version` 4 (ADR-0013) — the 2 PR2 commits (`ed99899`, `c18722c`) revert cleanly on top of PR1's tip; nothing in PR1 touched |
| Full-suite regression check | `uv run ruff format . && uv run ruff check --fix .` — clean, 85 files unchanged; `uv run mypy .` (strict) — no issues, 85 files; `uv run pytest` — 593 passed (was 592 before this batch — the one new RED-then-GREEN test); `uv run deptry .` — no dependency issues |

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 2.1 | `packages/vantage/tests/test_connection.py` | Unit | ✅ 19/19 (baseline before this batch) | ✅ Written — confirmed failing: `DID NOT RAISE SchemaVersionError` against `_SCHEMA_VERSION=3` | ✅ Passed after 2.4's bump | ✅ Existing older/newer-version tests already cover the other two directions; new test covers the specific "predates this change" scenario RQ-29's third spec scenario names | ➖ None needed |
| 2.2 | `packages/vantage/tests/test_schema_manifest.py` | Unit | ✅ 19/19 | ✅ Written — literals changed to 13/139/15, confirmed failing: `assert 11 == 13` | ✅ Passed after 2.3's schema.sql edit | ➖ Single — one ground-truth assertion, no branching | ➖ None needed |
| 2.3 | (implementation, not test-bearing) | — | — | — | ✅ Verified via 2.2/2.6 | — | ➖ None needed — matches `user_setting`'s exact structural precedent (D82-D90) |
| 2.4 | (implementation, not test-bearing) | — | — | — | ✅ Verified via 2.1/2.6 | — | ➖ None needed |
| 2.5 | `packages/vantage/tests/test_schema_manifest.py::test_fresh_database_matches_the_manifest_in_both_directions` | Unit (Inspection-verified per RQ-29) | ✅ | ✅ (this test existed and went RED the moment 2.3 landed schema.sql ahead of the manifest doc — confirmed: `tables only in schema: ['run_metadata', 'run_metadata_file']`) | ✅ Passed after the manifest doc edit | ➖ Single | ➖ None needed |

### Test Summary

- **Total tests written this batch**: 1 new (`test_opening_a_database_created_by_the_previous_schema_version_is_refused`)
- **Total tests updated this batch (literal-only, behavior unchanged)**: 4 (`test_opening_a_database_with_a_newer_schema_version_is_refused`, `test_opening_a_database_with_an_older_schema_version_is_refused`, `test_opening_a_database_with_the_current_schema_version_succeeds_and_applies_no_ddl`, `test_creating_a_database_survives_a_username_lookup_failure`, plus `test_fresh_database_matches_the_recorded_ground_truth`'s three literals) — none weakened, all moved the same fixed offset the bump requires
- **Total tests passing (full suite)**: 593
- **Layers used**: Unit (all — this is schema/connection-layer work, no integration/E2E boundary exists yet)
- **Approval tests** (refactoring): None — no refactoring tasks in this phase
- **Pure functions created**: 0 (schema DDL and a version-comparison change; no new logic beyond what `_check_schema_version` already had)

## Deviations from Design

None — `run_metadata`/`run_metadata_file`/`idx_run_metadata_key_value` match design.md D91's exact column list, types, `CHECK` vocabularies, and placement (after `user_setting`, before `-- Indexes`). Column count derived from the actual columns written: `run_metadata_file` = 4 (`run_id`, `source_file`, `content_type`, `status`), `run_metadata` = 5 (`run_id`, `key`, `value`, `source_file`, `status`) = 9 new columns; 130 + 9 = **139**, confirmed against a live introspected database via `test_fresh_database_matches_the_recorded_ground_truth`. This matches design.md's own stated 139 exactly — no correction needed to the design's number.

## Issues Found

None. One pre-existing-test-maintenance note, not a deviation: three tests in `test_connection.py` hardcoded the "current"/"newer"/"older" schema-version literals relative to `_SCHEMA_VERSION`'s old value of 3. Bumping to 4 required moving those literals the same fixed offset (3→4, 4→5) to keep asserting the identical behavior — this is not "weakening an assertion to make a test pass"; the assertions are byte-for-byte the same shape, only the version numbers they compare against moved with the constant they're pinned to.

## Git / PR State

- Tracker branch: `ft/run-metadata-capture` (draft, no-merge until every chain slice merges in order) — untouched.
- PR1: https://github.com/guillegil/vantage/pull/88 — base `ft/run-metadata-capture`, head `ft/run-metadata-capture-01-adr`. Open, all 12 CI checks pass. Not merged (per instructions).
- PR2: https://github.com/guillegil/vantage/pull/89 — base `ft/run-metadata-capture-01-adr`, head `ft/run-metadata-capture-02-schema`. **Open, all 12 CI checks pass.** Not merged (per instructions).
- PR2 commits: `ed99899` (schema bump implementation + tests + docs), `c18722c` (tasks.md checkbox flips).

## Measured changed-line count (PR2 vs. PR1 branch, full PR diff)

`git diff --stat ft/run-metadata-capture-01-adr..HEAD` (equivalently `--shortstat`):

**137 insertions(+), 24 deletions(-) — 161 changed lines total.**

Breakdown by file: `docs/schema-manifest.md` +56/-4 (two new table sections, corrected header/index-list counts), `packages/vantage/src/vantage/storage/schema.sql` +42/-8 (two tables, one index, corrected comments, version stamp), `packages/vantage/tests/test_connection.py` +25/-4 (one new test, four literal updates), `packages/vantage/tests/test_schema_manifest.py` +3/-3 (three literals), `packages/vantage/src/vantage/storage/connection.py` +1/-1 (the constant), `openspec/changes/run-metadata-capture/tasks.md` +7/-7 (checkbox flips).

Estimate was ~164 (tasks.md's own Review Workload Forecast table). **Measured 161 is *under* the estimate** — the second work unit in this chain to land at or below forecast. This does not contradict the project's ~1.9x historical-underforecast pattern (`user-configuration`: 450-600 forecast, ~1,090 measured): that pattern was observed on slices carrying new logic, parsing, or wiring discovered mid-implementation. PR2, like PR1, is a narrowly-scoped, previously-precedented slice (the `user_setting` table addition is its exact structural twin, D82-D90) with no logic to discover — schema DDL, a constant, and literal-following test updates are close to fully specified by the design before implementation starts. Phases 4-10 (ports/adapters, plugin flag, path containment, parse engine, server wiring) carry the kind of branching logic and edge-case discovery that produced the 1.9x overrun before, and should still be assumed to run higher than their forecasts.

## Remaining Tasks

All of Phase 3 through Phase 11 (tasks 3.1 through 11.5) — see tasks.md. Phase 3 (core vocabulary, `core/domain/metadata.py`) is next in the chain and must target `ft/run-metadata-capture-02-schema` per `feature-branch-chain`.

## Workload / PR Boundary

- Mode: chained PR slice (`feature-branch-chain`, PR 2 of 11)
- Current work unit: Phase 2 — schema bump only (two empty tables, one index, `_SCHEMA_VERSION` 3->4)
- Boundary: starts from PR1's tip (`ft/run-metadata-capture-01-adr`), ends with PR2 opened and green; no port dataclasses, adapters, plugin code, parsing, or route changes touched (explicitly out of scope per launch instructions)
- Estimated review budget impact: 161 changed lines, well under the 400-line budget

## Status

11/11 tasks complete across Phase 1 (4/4) and Phase 2 (7/7). PR1 open and green at
https://github.com/guillegil/vantage/pull/88 (tracker `ft/run-metadata-capture`).
PR2 open and green at https://github.com/guillegil/vantage/pull/89 (base PR1).
Neither merged (per instructions — the chain merges in order at the end). Ready
for the next `sdd-apply` batch (Phase 3, core vocabulary) once a maintainer
decides on merge order, or immediately if stacking continues per the chain.
