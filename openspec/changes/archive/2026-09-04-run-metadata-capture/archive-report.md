# Archive Report: run-metadata-capture

**Date archived**: 2026-09-04  
**Change name**: run-metadata-capture  
**Status**: ARCHIVED — SDD cycle complete  
**Verdict**: PASS WITH WARNINGS (0 blockers, 0 critical findings)

## Summary

The `run-metadata-capture` change has been successfully archived after implementation, verification, and remediation. This change adds the capability to capture user-declared configuration values from the test repository and store them per run, enabling queries like "which runs ran firmware_version 2.1?". The implementation spans 20 chained PRs (#88–#107) with two critical blockers discovered during verification and fixed in a final remediation slice (PR #107).

## Artifacts Retrieved

The following SDD artifacts were retrieved from Engram (hybrid mode):

| Artifact | Observation ID | Timestamp | Status |
|----------|---|-----------|--------|
| proposal | #132 | 2026-09-02 18:39:39 | Archived |
| spec | #134 | 2026-09-02 18:48:39 | Archived |
| design | #135 | 2026-09-02 18:59:14 | Archived |
| tasks | #136 | 2026-09-02 19:07:48 | Archived (14 revisions) |
| verify-report | #145 | 2026-09-04 20:38:54 | Archived |

## Specs Merged into Main

Five capability specs were updated by merging delta specs into the main specs:

| Capability | Action | Details |
|------------|--------|---------|
| run-metadata | **CREATED** (NEW) | 8 requirements, 14 scenarios |
| opt-in-activation | Updated | Added 1 requirement (metadata flag inertness) |
| session-ingestion | Updated | Added 4 requirements (metadata section, formats, parse failure, per-value bound) |
| recording-schema | **REPLACED** RQ-29 | Updated requirement to reflect schema version 3→4 with run_metadata table |
| history-read-api | Updated | Added 1 requirement (key=value equality filter) |

**Capability count**: Before archive: 18 capabilities in `openspec/specs/`. After merge: **19 capabilities** (run-metadata is new).

## Final State (Per Verify Report)

**Verdict**: PASS WITH WARNINGS — archive-ready

**Test Results**:
- Requirements: 15/15 satisfied
- Scenarios: 33/33 satisfied
- Tests: 746 passed (7 new tests added in remediation), 0 failed, 0 skipped
- Test command validation: exit 0
- Build validation (mypy --strict): 94 files, clean
- Linting (ruff check, ruff format): clean
- Dependency audit (deptry): clean

**Critical Findings**: 0 blockers, 0 critical issues

**Warnings (all pre-existing, routed to separate work on main)**:
- WARNING-1: MAX_METADATA_KEY_CHARS bound enforcement - CLOSED by design choice (enforced in read_declaration)
- WARNING-2: docs/schema-manifest.md narrative block dated (claims 10 tables/13 indexes/125 columns; live is 13/15/139 at schema_version 4) - mark as historical record
- WARNING-3: RQ-25 contradiction in version-control-context spec (claims 4.11%/4.17% reads as "inside 2% budget") - human decision required
- WARNING-4: _StubServer latent flake in test_failure_paths.py - structural cause unchanged, fix stub not tests

**Suggestions (non-blocking)**:
1. Plan test does not assert "SCAN run" is absent
2. Query-plan tradeoff is two-sided: selective filter 245x faster, degenerate filter slower - upside not claimed in design note
3. except Exception swallows programming bugs - debug log would help diagnosis
4. Scenario-traceability docstring annotations inconsistent (session-ingestion/history-read-api have them, others don't)

## Blockers Resolved During Cycle

Two critical findings were discovered during first verification (commit 339f5ca) and independently confirmed closed in final verification (commit afad467, PR #107):

### CRITICAL-1: NUL Byte in Declared Path Crashes Session
**Symptom**: `Path.resolve()` raises `ValueError` for embedded NUL, escaping the `(OSError, RuntimeError)` tuple, crashing pytest with INTERNALERROR (rc 3, zero tests run).

**Fix**: Added NUL check in `resolve_declared_path` that returns None for any path containing `\0`, marking the file as path_rejected with a VantageWarning. End-to-end test: real pytest session now exits 0, INTERNALERROR absent.

**Verification**: Confirmed load-bearing on Python 3.13.15; `isinstance(e, (OSError, RuntimeError))` is False for ValueError.

### CRITICAL-2: Metadata Filter Query Plan Did Not Use Index
**Symptom**: `_LIST_RUNS_BY_METADATA` query used `SCAN run` instead of `idx_run_metadata_key_value` index, causing O(n) behavior instead of O(matches).

**Fix**: Rewrote filter from `WHERE EXISTS (SELECT 1 FROM run_metadata WHERE ...)` to `WHERE id IN (SELECT run_id FROM run_metadata WHERE key=? AND value=?)`, enabling index use and query-plan rewrite.

**Verification**: 
- EXPLAIN QUERY PLAN on production query object shows correct index use
- Row equivalence test: 1,020 query/param pairs, 600 non-empty, 0 mismatches
- No-duplicates verified: max rows sharing (run_id,key) = 1 (PRIMARY KEY enforces)
- Regression test prevents re-introduction of old plan

**Learned**: The measured tradeoff is two-sided—selective filter (34 matches at 20k runs) improved 245x (14.73 ms→0.06 ms), but degenerate filter (all matching) now slower (0.05 ms→7.35 ms) due to full sort. Correct tradeoff: cost scales with matches rather than total runs.

## Key Implementation Facts

**Line count** (per verify-report, measured): 
- Phase 1–11: 5,275 changed lines across PR #88–#106 (all open)
- Phase 12 (remediation): 287 changed lines in PR #107
- Total authored (excluding generated verify-report): ~5,562 lines

**Schema changes** (per D91):
- Tables: 11 → 13 (added run_metadata, run_metadata_file)
- Columns: 130 → 139 (9 new columns)
- Indexes: 14 → 15 (added idx_run_metadata_key_value)
- Schema version: 3 → 4 (refuses older database)

**Architecture decisions** (D91–D102, 12 decisions):
- Two tables (not one) with status enumerations for failure taxonomy
- Path containment as security boundary with symlink and TOCTOU handling
- Six bounds derived from existing project numbers
- Drop-whole semantics (no truncate)
- Metadata rides existing record_session call (Option 1, default removed)
- Plugin flag `--vantage-metadata` with RQ-2 differential inertness
- Read filter on (key,value) index with pre-declaration horizon counting
- ADR-0017 written and landing in PR #88 (before irreversible schema bump)
- RQ-25 overhead measured: indistinguishable from noise at measurement scale (−42.1 ms to +29.4 ms deltas straddling zero)

**Open questions resolved by user** (from proposal, addressed 2026-09-02):
- Q1 Declaration location: Chosen B (vantage-metadata.json, self-documenting)
- Q2 Pre-declaration run query horizon: Chosen B (excluded but reported)
- Q3 No declaration found with flag set: Chosen B (warn via pytest warning)
- Q4 Declared-document formats: Chosen B (JSON+YAML; TOML adds later without schema change)

## Archive Contents

The change folder has been moved to `openspec/changes/archive/2026-09-04-run-metadata-capture/` and contains:

- ✅ `proposal.md` — scope, decisions, approach, risks, success criteria
- ✅ `specs/` — 5 delta spec directories (run-metadata, opt-in-activation, session-ingestion, recording-schema, history-read-api)
- ✅ `design.md` — 12 architecture decisions (D91–D102), 7 slices
- ✅ `tasks.md` — 75/75 tasks completed (67 original + 8 Phase 12 remediation)
- ✅ `apply-progress.md` — final state of all 20 PRs
- ✅ `verify-report.md` — PASS WITH WARNINGS, validator-admitted
- ✅ `explore.md` — initial exploration (included for completeness)

## Pre-Existing Findings Routed to Main

These findings are **not caused by this change** and route to separate work on `main`:

### RQ-25 Contradiction
**Finding**: `version-control-context/spec.md:152–159` records 4.11%–4.17% on the 1 ms profile while claiming it "still inside 2% budget" — arithmetically false.

**Impact**: The measured cost is already above any reasonable 2% reading of the budget before this change. This change's own RQ-25 measurement is indistinguishable from noise but sits on top of an already-strained baseline.

**Action required**: Human decision to either (1) correct the false conclusion in version-control-context, or (2) establish whether the 2% budget should be read differently. No spec merge can resolve this.

### schema-manifest.md Dated Narrative Block
**Finding**: Lines 364–403 state "10 tables / 13 indexes / 125 columns" (2026-08-15) against live 13 tables / 15 indexes / 139 columns.

**Impact**: Stale before this change. The machine-checked body is current and bidirectionally verified (RQ-29 Inspection satisfied).

**Action required**: Relabel the narrative block as a historical record; the body is the authority.

### _StubServer Daemon Thread Flake
**Finding**: `test_failure_paths.py::_StubServer` spawns one untracked daemon thread per connection; `__exit__` joins only the accept loop. Handler-completion order ≠ connection order. Tests read `requests_seen[2]` positionally, sometimes hitting the preflight's empty body → JSONDecodeError.

**Impact**: Latent (did not fire in full run nor 5 consecutive dedicated runs). Structural cause unchanged by this change.

**Action required**: Fix the stub, not the tests. Move to separate PR.

## Measurements Committed

### RQ-25 Overhead (Metadata Capture Session-Start Cost)
Measured 2026-09-04 via `scripts/measure_metadata_overhead.py` (copying `measure_vcs_overhead.py` harness). Five interleaved A/B paired runs per profile:

| Repository | Profile | A (baseline) | B (worst-case) | Delta | % of A |
|---|---|---|---|---|---|
| This repo | 1,000 × ~10 ms | 11.305 s | 11.285 s | −20.5 ms | −0.18% |
| This repo | 1,000 × ~1 ms | 1.678 s | 1.664 s | −14.2 ms | −0.85% |
| Synthetic (20k files) | 1,000 × ~10 ms | 11.347 s | 11.320 s | −26.5 ms | −0.23% |
| Synthetic (20k files) | 1,000 × ~1 ms | 1.717 s | 1.746 s | +29.4 ms | +1.71% |

**Interpretation**: Deltas span −42.1 ms to +29.4 ms, straddling zero, exactly the shape expected when true effect (<2 ms forecast) sits an order of magnitude below process-spawn variance this benchmark resolves. Result is **indistinguishable from noise**, not falsifying the forecast but not confirming it either. The honest statement is recorded rather than collapsed into a false "holds."

**Verdict**: RQ-25 obligation satisfied by measurement and recording. The measured delta rides on top of `vcs.capture`'s baseline (already at 4.11%–4.17% on 1 ms profile before this change).

## Final-State Authority

This archive report reflects the state of the change **at close**. Per the Final-State Authority hierarchy:

1. **Native review authority**: Not applicable (review mode off in this clone per CLAUDE.md, authorized 2026-08-28).
2. **Persisted tasks artifact**: 75/75 tasks marked complete in `tasks.md`, validated by Task Completion Gate ✓
3. **Explicit final-state facts** (launch prompt): 15/15 requirements, 33/33 scenarios, 746 tests passing, two blockers independently confirmed closed, schema version 3→4.
4. **Intermediate snapshots** (verify-report, apply-progress): Superseded by explicit facts above when they differ.

When sources disagreed, the highest-ranked source governed the reported state. Contradictory claims within lower-ranked sources are recorded explicitly (e.g., RQ-25 contradiction in version-control-context).

## SDD Cycle Closed

- **Proposal**: ✅ Scope, approach, decisions, rollback plan
- **Spec**: ✅ 15 requirements, 33 scenarios, delta specs merged to main
- **Design**: ✅ 12 architecture decisions, 7 implementation slices
- **Apply**: ✅ 20 chained PRs, 5,562 authored lines, all tests passing
- **Verify**: ✅ PASS WITH WARNINGS, two blockers found and fixed, re-verified clean
- **Archive**: ✅ Specs merged, change folder moved, archive report written

**Next phase**: None for this change. The chain merges to `main` via tracker branch `ft/run-metadata-capture` when the orchestrator signals.

---

**Archive report generated**: 2026-09-04 22:12 UTC  
**Archived to**: `openspec/changes/archive/2026-09-04-run-metadata-capture/`  
**Engram topic key**: `sdd/run-metadata-capture/archive-report`  
**Engram observation IDs**: 132 (proposal), 134 (spec), 135 (design), 136 (tasks), 145 (verify-report)
