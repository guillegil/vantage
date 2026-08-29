# Archive Report: user-configuration

**Change**: user-configuration  
**Archived**: 2026-08-29  
**Status**: Complete, archived successfully  
**Branch**: `ft/user-configuration-04-run-aggregate` (chain tip)

## Change Summary

Added a server-persisted, user-managed preference surface with test sections as the first tenant. This change delivers:

- **`user-configuration` capability**: Namespaced, server-persisted settings with a generic storage layer and namespace-specific validation in the service layer. Settings are persistent, immediately deletable, and reserved-key-protected.
- **`test-sections` capability**: Section definitions as the first tenant of user-configuration, with longest-prefix-wins derivation at read time, an always-present unassigned bucket, and per-run pass-percentage aggregates.
- **`recording-schema` modification**: Schema version bumped from 2 to 3; the `user_setting` table added to satisfy RQ-29's completeness obligation. The system now refuses to open databases created by older versions, naming the version found and required.

## Artifact Traceability (Engram Observation IDs)

All SDD artifacts were preserved in Engram during the specification and implementation phases:

- **Proposal** (ID 121): `sdd/user-configuration/proposal` — Scope, intent, decisions, success criteria, affected areas, risks, and rollback plan.
- **Specification** (ID 122): `sdd/user-configuration/spec` — Three delta capability specs: user-configuration (new, 5 requirements), test-sections (new, 9 requirements), recording-schema (modified, 1 requirement, RQ-29 updated).
- **Design** (ID 123): `sdd/user-configuration/design` — Nine architecture decisions (D82–D90) covering storage structure, port design, core domain, aggregation, HTTP surface, caching, error handling, and delivery strategy.
- **Tasks** (ID 124): `sdd/user-configuration/tasks` — 35 implementation tasks across four slices (storage foundation, core domain, definitions API split into 3a/3b, aggregate endpoint), all marked complete and verified.
- **Verification Report** (ID 127): `sdd/user-configuration/verify-report` — Verdict: PASS WITH WARNINGS. All 15 requirements implemented, all 23 scenarios proven (20 by automated test, 3 by demonstration). Zero critical findings. Warnings W1 and W2 about stale artifacts were already reconciled in commit `9a35306` before archive.

## Specifications Merged and Archived

### New Capability: user-configuration

**File**: `openspec/specs/user-configuration/spec.md`  
**Requirements**: 5  
**Scenarios**: 6

1. Namespaced setting persistence (2 scenarios: create new, replace existing)
2. Settings persist across server restart (1 scenario)
3. Deletion is immediate (1 scenario)
4. Generic storage, specific validation (1 scenario: reserved key rejected)
5. Port parity across storage implementations (1 scenario)

Verification: All scenarios tested via contract suite against both SQLite and in-memory adapters.

### New Capability: test-sections

**File**: `openspec/specs/test-sections/spec.md`  
**Requirements**: 9  
**Scenarios**: 14

1. Section definitions stored under test_sections (1 scenario: trailing slash coercion)
2. Section name constraints (2 scenarios: empty name rejected, "unassigned" reserved regardless of casing)
3. Longest-prefix-wins derivation at read time (3 scenarios: longest wins, no sibling bleed, renaming re-groups)
4. Deleting a section is immediate and silent (1 scenario)
5. `unassigned` bucket always present and reconciles (2 scenarios: empty bucket present, totals reconcile)
6. Sections ordered alphabetically (1 scenario)
7. Pass percentage computation (2 scenarios: worked example yields 94.4%, empty bucket reports null)
8. Section definitions readable/upsertable/deletable via API (1 scenario)
9. Run section-summary endpoint (1 scenario)

Verification: 20 scenarios by dedicated automated test; 3 scenarios (restart persistence, updated_at replacement, deleted section fallback) proven by demonstration during verification because the suite has no dedicated regression test for them—a follow-up noted as W3 in the verify report.

### Modified Capability: recording-schema

**File**: `openspec/specs/recording-schema/spec.md`  
**Requirements**: 1 (modified)  
**Scenarios**: 3

**Requirement: Complete schema from first use (RQ-29)** — MODIFIED

Updated to reflect the `user_setting` table addition and schema version bump from 2 to 3. The requirement text now references both the `user_setting` table added by this change and the `run.last_contact_at` column added by an earlier change, establishing the pattern for future additions. Verification method remains Inspection: comparison against the schema manifest and a record of SQL statements issued against existing databases.

1. Fresh database matches the column manifest (now includes `user_setting` columns and the full index set)
2. Opening an existing database issues no schema-altering statement
3. A database from an older schema version is refused, not altered (now predates `user_setting` and version 3)

Verification: Inspection conducted by comparing a freshly created database against `docs/schema-manifest.md` and inspecting SQL statements issued against existing v2-stamped databases—no schema-altering statements found.

## Requirements and Scenarios (Final Count)

Counted from the merged capability specs:

| Capability | Requirements | Scenarios |
|---|---|---|
| user-configuration | 5 | 6 |
| test-sections | 9 | 14 |
| recording-schema (modified) | 1 | 3 |
| **Total** | **15** | **23** |

All 15 requirements are implemented in the codebase. All 23 scenarios are verified (20 by test, 3 by demonstration).

## Implementation Summary

**Delivery Strategy**: Feature-branch chain (auto-chain resolution)

**Four PR slices**:
1. **PR #82** (`ft/user-configuration-01-storage-foundation`): `user_setting` table, schema v3, port methods, both adapters, refusal test. ~390 lines.
2. **PR #83** (`ft/user-configuration-02-sections-domain`): Pure `core/domain/sections.py` module, no I/O, stdlib only. ~378 lines.
3. **PR #84/85** (`ft/user-configuration-03-definitions-api` split into 3a/3b): Definitions CRUD routes, schemas, rejection types, OpenAPI. ~180 + 346 = 526 lines total.
4. **PR #86** (`ft/user-configuration-04-run-aggregate`): Run aggregate endpoint, response models, bindings. 317 lines.

**Total measured lines** (counted at verify time): 1,611 changed lines across the full chain.

**Test suite growth**: 540 → 592 passed (52 new tests in test_sections.py and test_routes_sections.py, plus extended cases in shared contract and document-verification suites).

**Quality gates all green**:
- `uv run mypy --strict .` → 85 source files, clean
- `uv run ruff format . && ruff check --fix .` → clean
- `uv run deptry .` → no undeclared or unused dependencies, RQ-24 enforcement clean
- `git diff --stat packages/pytest-vantage` → empty (ADR-0009, RQ-24)

## Open Follow-ups (Preserved for Next Iteration)

The archive report inherits follow-ups identified during specification, design, verification, and implementation. These are not regressions or defects—they are intentional deferrals and genuine improvements documented for future work:

### Critical (Must Address Before Non-Local Deployment)

**No authentication in front of the write surface**: This change introduced the project's first user-facing write surface beyond ingestion (`POST`/`DELETE /api/v1/config/sections`). Anyone who can route to the host can rewrite section definitions. Named in proposal (Risks table), design (D87 threat matrix), and verify report (S7). `MAX_SECTIONS = 200` bounds growth but does not close the access gap. Must be answered before any non-local deployment.

### Important (Regression Risk)

**W3 — Three spec scenarios have no dedicated regression test** (verify-report W3): All three behave correctly and were proven by demonstration during verification, but nothing in CI would catch a regression:

1. `user-configuration` "replaces it, not duplicates it" — the contract test asserts `len == 1` and new `value`, but never that `updated_at` was replaced. One added assertion closes it and runs against both adapters.
2. `user-configuration` "a restart does not lose a setting" — no test composes write, `close()` and reopen for settings. Pre-existing compose-test infrastructure exists for runs/results; the gap is small.
3. `test-sections` "a deleted section's tests fall back to unassigned" — the rename test upserts a replacement before deleting, so it asserts `unassigned.total == 0` and never exercises the fallback. The no-match path is proven separately; the gap is narrow.

These three small tests would close the regression guard. None reveals a defect in the current code.

### Pre-Existing (Noted for Reference)

**S1 — Pre-existing inaccuracy**: `packages/vantage/src/vantage/storage/schema.sql:82` says `node_id`'s unique index is one of "thirteen" indexes; the schema declares fourteen. Predates this change. Noted for correction in a future pass.

### Design Deferrals (Intentional)

**Caching**: Definitions are never cached; they are read from the store on every request (D88). No `lru_cache`, no `app.state` cache, no TTL. Cost is one indexed query over ≤200 rows per request. Acceptable for M1; revisit if load metrics warrant it.

**Unpaginated aggregate**: `get_run_case_outcomes` is unpaginated by design—a run with tens of thousands of results materialises all tuples for one request. Recorded in design and accepted as a consequence.

**Race condition in MAX_SECTIONS**: Enforcement is check-then-act, a race under concurrent writers. Accepted because it is an ergonomic and response-size guard, not a security invariant. Would become necessary to serialize only if truly concurrent section writes are demonstrated.

**No index on test_case.file_path** (D82, D86): Deliberately rejected because no queried path needs it today. Becomes necessary only if a cross-run "all history under section X" query is ever built.

**updated_at stored but not published**: The field is persisted correctly via `_fixed_width_isoformat` but is not exposed in any API response. Preserved for a future "last-modified" query that might be useful.

## Decisions Recorded (D82–D90)

All nine design decisions were followed and are documented in `design.md`. No new ADR was created (CLAUDE.md's reversal-cost filter was not met), and no new capability flag was added (D90). The schema version bump was the only breaking change and follows ADR-0013's established policy.

## No New RQ-xx Identifiers

Per project convention (established 2026-08-18), no new `RQ-xx` identifiers were minted. RQ-24, RQ-26, and RQ-29 were referenced where genuinely relevant to the requirements.

## Archive Contents

The archived folder at `openspec/changes/archive/2026-08-29-user-configuration/` contains:

- `proposal.md` — complete proposal with intent, scope, decisions, risks, rollback plan
- `design.md` — full design document with nine decisions and threat matrix
- `tasks.md` — all 35 tasks across four slices, all marked complete
- `specs/` — three capability specs (user-configuration, test-sections, recording-schema delta)
- `archive-report.md` — this file

## Closure Notes

**Verification Verdict**: PASS WITH WARNINGS (verify-report ID 127, 2026-08-29 21:39:58)
- 0 CRITICAL findings
- 0 Blockers
- 3 Warnings: W1 and W2 (stale artifacts) reconciled in commit `9a35306`; W3 (regression test gaps) noted above
- 15/15 requirements implemented and verified
- 23/23 scenarios proven
- 592 tests passed (52 new), all green
- `mypy --strict`, `ruff`, `deptry` all green
- AST architecture test passed

**Tasks**: 35/35 complete, all checks verified against code

**Specs**: Three capability specs merged into main sources of truth at `openspec/specs/`. Two new, one modified. All five scenarios of the modified spec carry forward the RQ-29 obligation intact.

**Rollback Plan**: Per-slice branch revert in reverse chain order (PR #86 ← #85 ← #84 ← #83 ← #82). Reverting #82 returns `_SCHEMA_VERSION` to 2; anyone holding a version-3 database recreates it per ADR-0013.

**Change is archived. SDD cycle complete.**

---

**Archive date**: 2026-08-29  
**Archived by**: sdd-archive executor  
**Observation IDs in Engram**: 121 (proposal), 122 (spec), 123 (design), 124 (tasks), 127 (verify-report)
