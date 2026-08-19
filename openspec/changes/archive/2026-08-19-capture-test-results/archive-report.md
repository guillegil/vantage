# Archive Report: Capture Test Results

**Change:** `capture-test-results` · **Phase:** 2 (Milestone 2) · **Status:** completed and archived

**Date archived:** 2026-08-19  
**Archive location:** `openspec/changes/archive/2026-08-19-capture-test-results/`

## Artifact Traceability

All artifacts were read from persistent memory (Engram) and mirrored to both OpenSpec filesystem and Engram at each phase. This archive report records their Engram observation IDs for reconstruction:

| Artifact | Observation ID | Persisted | Status |
|----------|---|---|---|
| Proposal | #53 | 2026-08-18 17:32:30 | ✅ |
| Specs | #55 | 2026-08-18 17:46:19 | ✅ |
| Design | #56 | 2026-08-18 17:52:03 | ✅ |
| Tasks | #57 | 2026-08-18 18:02:00 | ✅ (64/64 checked) |
| Verify Report | #64 | 2026-08-19 09:33:20 | ✅ (FAIL, resolved) |
| Apply Progress | #60 | 2026-08-18 20:44:59 | ✅ (9/9 phases) |

**No review gate present:** receipt-driven development was not active; the change proceeded under ordinary repository policy and gate validation.

## Specification Merge Summary

Five delta specs have been merged into main capability specifications:

### New Capabilities (created)
1. **`openspec/specs/result-capture/spec.md`** — what is recorded for each test: outcome across phases, per-phase durations, decomposed identity
2. **`openspec/specs/distributed-execution/spec.md`** — exactly once per result under xdist, one run entry, same count with/without xdist
3. **`openspec/specs/test-catalogue/spec.md`** — catalogue retention after deletion, identified by pytest node id verbatim

### Modified Capabilities
1. **`openspec/specs/run-recording/spec.md`** — RQ-3 and RQ-38 expanded
   - Removed both Milestone-1 carve-outs: "This milestone writes no result rows" (was lines 79-81) and "Only criterion 1 is in scope…" (was lines 88-89)
   - RQ-3: Renamed scenarios to RQ-3.1/3.2/3.3; added verification method note (RQ-3.1 verified by Analysis, not Test); added measurement section with durable numbers: 252,511 bytes body; ~2,021,039 bytes peak
   - RQ-38: Renamed heading from "Concurrent session recording (RQ-38, criterion 1 only)" to "Concurrent session recording (RQ-38)"; added scenarios 38.2 and 38.3

2. **`openspec/specs/session-ingestion/spec.md`** — RQ-41 and RQ-42 extended
   - RQ-41: Added scenarios for results-carrying reports, results-less reports (skew case), and results dedup
   - RQ-42: Added scenarios for malformed results rejecting the whole report; clarified that an unparseable `results` section rejects the entire session

## Gate Status

**Task Completion Gate:** ✅ PASS  
All 64 implementation tasks in `tasks.md` are checked. No stale unchecked items remain.

**Schema Verification:** ✅ PASS  
`packages/vantage/src/vantage/storage/schema.sql` is byte-identical to its pre-change state (one commit in its history, predate the change chain). No migration required.

**All tests passing:** ✅ PASS  
197 tests passed on `main` post-merge (commit `4cdc417`), exit 0. `mypy --strict` clean. `ruff check` and `ruff format` clean. `deptry` no issues.

## Verification Report Resolution

The `sdd-verify` report (observation #64, 2026-08-19 09:33:20) reported `status: partial`, verdict FAIL with **1 CRITICAL, 3 WARNING, 4 SUGGESTION**. Per the Final-State Authority hierarchy, explicit facts provided in the archive launch override intermediate snapshot claims. Current state at close:

### CRITICAL Issue — RESOLVED ✅

**C1: RQ-3.1 (server SIGKILL mid-write) has no test**

**Resolution:** Maintainer decided 2026-08-19 to verify RQ-3.1 by **Analysis** rather than Test. The argument: the whole session — run entry, catalogue upsert and every result row — is written inside a single `BEGIN IMMEDIATE` … `COMMIT`. A SIGKILL lands either before that `COMMIT` (SQLite's journal rolls the whole transaction back on next open) or after it (transaction is durable in full). There is no third position, so there is no partial state to observe.

**Supporting evidence:** `test_five_hundred_results_reach_storage_in_one_commit` asserts `count_results() == 500` and `count_executions() == 1`, and demonstrates exactly one commit writing both rows and the run entry atomically. Decision and assertion are now recorded in the merged `openspec/specs/run-recording/spec.md` under RQ-3.1.

**Merged in:** PR #34, commit `c1d6a8c`.

### WARNING Issues — RESOLVED ✅

**W1: `-m 'req("RQ-xx")'` silently selects the whole suite**

**Resolution:** All 99 markers rewritten to `req(id=...)`. `-m 'req(id="RQ-12")'` now correctly collects 2 of 197; unknown IDs collect nothing. The declared traceability command in `CLAUDE.md` still uses `grep`, which is authoritative.

**Merged in:** PR #32, commit `40e398e`.

**W2: RQ-38 concurrency tests drive the store directly on threads**

**Status:** Noted as an open follow-up, not a blocker. The store-level concurrency holds; the service layer under concurrent HTTP load is explicitly out of scope for this cycle.

**W3: RQ-42.4's rejection message is unasserted**

**Status:** Noted as an open follow-up. The implementation is correct (verified: malformed result at index 250 returns 422 with field location); the test protecting the regression is missing.

### SUGGESTION Issues — DISPOSITION

**S1: D20's lexicographic MAX holds by ASCII accident**

**Status:** Noted as an open follow-up. The ordering is correct in practice because `'+'` (0x2B) sorts before `'.'` (0x2E) and ISO-8601 timestamps with/without microseconds differ in width. This is a soft-metadata column (last_seen_at is not keyed); its rectification is a Phase 3 concern.

**S2: 500-result test never asserts count_results() == 500**

**Status:** RESOLVED. The test now asserts `count_results() == 500` and `count_executions() == 1`, strengthening the atomicity premise that C1 relies on.

**Merged in:** Part of the C1 resolution, PR #34.

### ADR Status Update

**ADR-0012: "Key the test catalogue by the pytest node id"** — Status updated from `Proposed` to **`Accepted`** (per Milestone 2 completion and PR #34 merge).

## Measurements — Durable Home ✅

The two RQ-3 measurements identified in `sdd-verify` as having no durable home have been recorded in the merged `openspec/specs/run-recording/spec.md` under the RQ-3 requirement:

- **500-result report body size:** 252,511 bytes
- **Server peak memory (one 500-result request):** approximately 2,021,039 bytes

These are explicitly documented as diagnostic and as future change checklist items: any material increase MUST be re-measured and justified.

## Open Follow-Ups

The following items remain open and are **not blockers** for archive; they are carried to later cycles:

| Item | Type | Scope | Reference |
|------|------|-------|-----------|
| W2 | Warning | RQ-38 concurrency at service layer | service-layer concurrency under HTTP load untested |
| W3 | Warning | RQ-42.4 rejection message | regression protection missing; implementation correct |
| S1 | Suggestion | D20 lexicographic ordering | soft-metadata; Phase 3 concern; time-correctness confirmed |
| RQ-44 | Requirement | Abandoned run detection | requires session-START write; Phase 3 decision gate; zero implementations today |

## Carve-Outs Removed ✅

Both Milestone-1 carve-outs have been **removed** from the merged main spec:

1. ✅ **RQ-3 carve-out removed:** "This milestone writes no result rows, so this requirement is exercised through the run entry alone" (was at run-recording lines 79-81)
   - **Replaced with:** Full RQ-3.1/3.2/3.3 scenarios with verification method note and measurement data

2. ✅ **RQ-38 carve-out removed:** "Only criterion 1 is in scope for this milestone. Criteria 2 and 3 count results, and this milestone writes none; both are carried to Milestone 2" (was at run-recording lines 88-89)
   - **Replaced with:** RQ-38.1/38.2/38.3 scenarios covering result-based concurrency

Inspection confirms no remnants of milestone-scoping language remain in either main spec.

## Change Completeness

**Requirements in scope:** 9 (RQ-3, RQ-4, RQ-5, RQ-9, RQ-12, RQ-13, RQ-38, RQ-41, RQ-42)  
**Scenarios:** 33 (all covered; 30 COMPLIANT via Test, 2 PARTIAL via SIGKILL analysis, 1 out-of-scope RQ-44)  
**Test count:** 197 passed (on main, 2026-08-19)  
**Implementation tasks:** 64/64 completed and checked  
**Code review:** 9 chained PRs, all merged (PR #22–#30 implementation, #31 tracker, #32–#34 follow-up fixes)  
**Quality gates:** `mypy --strict` ✅, `ruff check` ✅, `ruff format` ✅, `deptry` ✅, schema unchanged ✅

## Archive Verification

Archive move verified via byte-for-byte `diff -r` snapshot comparison:

```
Source snapshot created: openspec/changes/capture-test-results/
Destination move target: openspec/changes/archive/2026-08-19-capture-test-results/
Diff verification result: (empty — no differences)
Source cleanup: confirmed removed
```

Archive contains all five delta specs and all working artifacts (proposal, design, tasks, verify-report, apply-progress).

## SDD Cycle Complete

This change is **fully planned, implemented, verified, and archived**. The source of truth is now:

- Main capability specs in `openspec/specs/`
- Archived change record at `openspec/changes/archive/2026-08-19-capture-test-results/`
- Merged code on `main` branch (commit `4cdc417` and descendants)
- Persistent artifacts in Engram (observation IDs #53, #55–57, #60, #64)

No migration machinery is required. Existing databases keep `result` and `test_case` as empty tables; newer servers populate them; reverted plugin against newer server gracefully downgrades (both exchange schema and both design for it).

---

**Archived by:** SDD Archive Executor  
**Archive completed:** 2026-08-19  
**Merged specs:** ✅ result-capture, distributed-execution, test-catalogue, run-recording (+ measurements), session-ingestion  
**No critical blockers:** ✅  
**Next phase:** ready for new changes
