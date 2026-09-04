# Apply Progress: run-metadata-capture

**Mode**: Strict TDD from Phase 2 onward (Phase 1 was the documented
Strict-TDD exception — doc-only, no code path existed yet, per tasks.md's
own work-unit table). Phase 2 and Phase 3 are implemented under Strict TDD
in full.

## Scope covered so far

- Phase 1 (PR1 → tracker `ft/run-metadata-capture`) — complete.
- Phase 2 (PR2 → PR1) — complete.
- Phase 3 (PR3 → PR2) — complete, this batch.
- Phases 4-11 are untouched and remain for future `sdd-apply` batches.

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
| `openspec/changes/run-metadata-capture/tasks.md` | Modified (PR1 + PR2 + PR3) | Phase 1/2/3 tasks marked `[x]` |
| `openspec/changes/run-metadata-capture/apply-progress.md` | Modified (each batch) | This artifact, mirrored on disk (hybrid store) |

## Work Unit Evidence (PR3)

| Evidence | Value |
|---|---|
| Focused test command and result | `uv run pytest packages/vantage/tests/test_metadata.py` — 7 passed; `uv run pytest packages/vantage/tests/test_metadata.py packages/vantage/tests/test_architecture.py` — 11 passed |
| Runtime harness command/scenario and result | N/A — pure, no I/O, no caller yet (per the work-unit table); this module is vocabulary only, wired to a port and adapter starting PR4 |
| Rollback boundary | Revert `core/domain/metadata.py` and `test_metadata.py`; nothing else in the tree imports the module yet (RQ-26 purity walk assertion is the only coupling, and it reverts with the same commit) |
| Full-suite regression check | `uv run ruff format . && uv run ruff check --fix .` — clean, 87 files unchanged; `uv run mypy .` (strict) — no issues, 87 files; `uv run pytest` — 600 passed (was 593 before this batch — 7 new tests); `uv run deptry .` — no dependency issues |

## TDD Cycle Evidence

| Task | Test File | Layer | Safety Net | RED | GREEN | TRIANGULATE | REFACTOR |
|------|-----------|-------|------------|-----|-------|-------------|----------|
| 3.1/3.2 | `packages/vantage/tests/test_metadata.py` | Unit | ✅ 593/593 (baseline before this batch) | ✅ Written — confirmed failing: `ModuleNotFoundError: No module named 'vantage.core.domain.metadata'` | ✅ Passed after 3.2's module creation — 7/7 | ➖ Skipped: purely structural (frozenset literals and constant definitions, D94/D95's own values), exactly one possible output per assertion, no branching — noted per strict-tdd's explicit skip condition | ➖ None needed |
| 3.3 | `packages/vantage/tests/test_architecture.py` | Unit | ✅ 4/4 (baseline before this batch) | ✅ Written, then confirmed failing by temporarily moving `metadata.py` out of the tree and re-running: `AssertionError: assert 'vantage/core/domain/metadata.py' in {...}` | ✅ Passed after restoring the module — 4/4 | ➖ Single — one membership assertion, no branching | ➖ None needed |

### Test Summary (PR3)

- **Total tests written this batch**: 7 new in `test_metadata.py`, plus 1 new assertion in an existing `test_architecture.py` test
- **Total tests passing (full suite)**: 600
- **Layers used**: Unit only — pure vocabulary module, no I/O boundary exists
- **Approval tests** (refactoring): None — no refactoring tasks in this phase
- **Pure functions created**: 0 — vocabulary and constants only, no logic (RQ-26, by design)

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

## Issues Found

None beyond the `MAX_METADATA_KEY_CHARS` gap noted above. Task 3.3 required
a non-linear RED: `metadata.py` already existed (from task 3.2, which
necessarily precedes 3.3 in the phase's own numbering) by the time the new
`test_architecture.py` assertion was written, so genuine RED was proven by
temporarily moving the module out of the source tree, confirming the
assertion failed, then restoring it — not by reverting to before task 3.2.
This is recorded rather than silently treated as "trivially green."

## Git / PR State

- Tracker: `ft/run-metadata-capture` (draft, no-merge) — untouched.
- PR1: https://github.com/guillegil/vantage/pull/88 — base tracker, head `ft/run-metadata-capture-01-adr`. Open, 12/12 checks pass. Not merged.
- PR2: https://github.com/guillegil/vantage/pull/89 — base `ft/run-metadata-capture-01-adr`, head `ft/run-metadata-capture-02-schema`. Open, 12/12 checks pass. Not merged.
- PR3: opened this batch, base `ft/run-metadata-capture-02-schema`, head `ft/run-metadata-capture-03-core`. See PR URL in the return summary.
- PR3 commits: `6bd5f1d` (vocabulary module + tests + architecture-walk assertion), `7a87775` (tasks.md checkboxes), plus this apply-progress commit.

## Measured changed-line count (PR3 vs. PR2 branch, full PR diff)

`git diff --stat ft/run-metadata-capture-02-schema..HEAD` before the
apply-progress.md commit: **174 insertions(+), 3 deletions(-) — 177 changed
lines.** Breakdown: `packages/vantage/src/vantage/core/domain/metadata.py`
+86, `packages/vantage/tests/test_metadata.py` +84, `test_architecture.py`
+1, `tasks.md` +6/-3.

Estimate was ~220. See the return summary for the final number including
this apply-progress commit.

## Remaining Tasks

All of Phase 4 through Phase 11 (tasks 4.1 through 11.5) — see tasks.md.
Phase 4 (port dataclasses + both adapters + contract tests) is next in the
chain and must target `ft/run-metadata-capture-03-core` per
`feature-branch-chain`.

## Workload / PR Boundary

- Mode: chained PR slice (`feature-branch-chain`, PR 3 of 11)
- Current work unit: Phase 3 — core vocabulary only (`core/domain/metadata.py`, pure, no I/O, nothing imports it yet)
- Boundary: starts from PR2's tip (`ft/run-metadata-capture-02-schema`), ends with PR3 opened and green; no port dataclasses, adapters, plugin code, parsing, or route changes touched (explicitly out of scope per launch instructions)
- Estimated review budget impact: well under the 400-line budget

## Status

18/18 tasks complete across Phase 1 (4/4), Phase 2 (7/7) and Phase 3 (3/3).
PR1 and PR2 open and green, not merged (per instructions — the chain merges
in order at the end). PR3 opened this batch. Ready for the next `sdd-apply`
batch (Phase 4, port dataclasses + both adapters + contract tests).
