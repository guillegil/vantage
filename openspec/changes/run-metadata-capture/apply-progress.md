# Apply Progress: run-metadata-capture

**Mode**: Standard (Phase 1 is the documented Strict-TDD exception — doc-only,
no code path exists yet, per tasks.md's own work-unit table). Strict TDD
applies in full from Phase 2 onward.

## Scope of this batch

Phase 1 (PR1 → tracker `ft/run-metadata-capture`) only, per launch instructions.
Phases 2-11 are untouched and remain for future `sdd-apply` batches.

## Completed Tasks

- [x] 1.1 Write `docs/adr/0017-store-user-declared-configuration-values-read-from-the-test-repository.md`. Nygard + `Alternatives rejected` (ADR-0016's shape). `Status: Proposed`.
- [x] 1.2 Decision section: what is authorised (declared top-level scalars only, never file bodies, D-k deferred); the five conditions C1-C5 held together, inheriting ADR-0016's register; the EAV justification (D-e); the must-not-fail-the-run rule (D97); what is not authorised (host env, arbitrary bodies, server-directed reads, web editing, backfill).
- [x] 1.3 Consequences section: read exposure stated not mitigated; reversal cost (`schema_version` 3->4, refuse not migrate, ADR-0013); RQ-25 O(1)-measured obligation; unbounded growth named not solved; Q2's horizon published not implied. Bind to ADR-0013, ADR-0014, ADR-0016, RQ-2/24/25/26/28/29/44, and `run-metadata`/`opt-in-activation`/`session-ingestion`/`recording-schema`/`history-read-api`.
- [x] 1.4 PR description: `Status` flips to `Accepted` on merge (CLAUDE.md); no test surface, this PR is Inspection-only.

## Files Changed

| File | Action | What Was Done |
|------|--------|----------------|
| `docs/adr/0017-store-user-declared-configuration-values-read-from-the-test-repository.md` | Created | 204-line Nygard ADR (Status/Context/Decision/Consequences/Alternatives rejected), `Status: Proposed`; quotes ADR-0016 lines 94-97 as the authority argument; reversal-cost argument (ADR-0013 refuse-not-migrate); C1-C5 held together; EAV justification (D-e); must-not-fail-the-run rule; explicit non-authorization list; closing `Bound to:` line |
| `openspec/changes/run-metadata-capture/tasks.md` | Modified | Phase 1 tasks 1.1-1.4 marked `[x]` |

## Work Unit Evidence (PR1)

| Evidence | Value |
|---|---|
| Focused test command and result | N/A — doc-only, no code path exists yet (tasks.md's own documented exception) |
| Runtime harness command/scenario and result | N/A — no code path exists yet |
| Rollback boundary | Revert the one new file (`docs/adr/0017-*.md`) and the 4 checkbox edits in `tasks.md`; nothing else touched |
| Full-suite regression check | `uv run ruff format . && uv run ruff check --fix .` — clean, 85 files unchanged; `uv run pytest` — 592 passed, 12 warnings, no test moved |

## TDD Cycle Evidence

Not applicable to this batch. Strict TDD is active project-wide, but the launch
instructions and tasks.md both record Phase 1 as the documented exception: an
ADR is a document, and no code path exists yet to write a RED test against.
Strict TDD resumes in full at Phase 2 (schema bump), whose tasks table already
encodes RED-before-GREEN pairs (2.1/2.2 RED before 2.3/2.4 GREEN).

## Deviations from Design

None — implementation matches design.md's D101 checklist exactly (title,
Nygard + Alternatives rejected shape, C1-C5, EAV justification, must-not-fail
rule, non-authorization list, Bound-to line, lands in slice 1 not slice 5).

## Issues Found

None.

## Git / PR State

- Tracker branch: `ft/run-metadata-capture` (draft, no-merge until every chain slice merges in order) — pre-existing, untouched by this batch.
- Working branch: `ft/run-metadata-capture-01-adr`, branched from the tracker, pushed to `origin`.
- PR: https://github.com/guillegil/vantage/pull/88 — base `ft/run-metadata-capture`, head `ft/run-metadata-capture-01-adr`.
- CI: all 12 checks pass (clean-environment-install, networking-disabled, python-3-9-install-refused, quality, test × {3.10,3.11,3.12,3.13} × {with,without} xdist).
- Not merged — per instructions, no merge action taken.

## Measured changed-line count (PR1 vs. tracker branch)

`git diff --shortstat ft/run-metadata-capture...ft/run-metadata-capture-01-adr`:

**208 insertions(+), 4 deletions(-) — 212 changed lines total.**

Breakdown: 204 lines are the new ADR file (all additions); 8 lines are the
tasks.md checkbox flips (4 lines `[ ]`→`[x]`, each counted as 1 deletion + 1
addition = 4 deletions + 4 additions).

Estimate was ~220 (tasks.md's own Review Workload Forecast table). **Measured
212 is *under* the estimate** — the first work unit in this chain to land
at or below forecast rather than the project's typical ~1.9x overrun. This is
expected for Phase 1 specifically: it is pure prose with no code, tests, or
wiring to discover mid-implementation, unlike the schema/plugin/server slices
still ahead. The 1.9x historical pattern (see `user-configuration`: 450-600
forecast, ~1,090 measured) should still be assumed for Phases 2-11.

## Remaining Tasks

All of Phase 2 through Phase 11 (tasks 2.1 through 11.5) — see tasks.md. Phase
2 is next in the chain and is the irreversible schema-version bump; it must
target `ft/run-metadata-capture-01-adr` per `feature-branch-chain`, per
tasks.md's own base-branch table.

## Workload / PR Boundary

- Mode: chained PR slice (`feature-branch-chain`, PR 1 of 11)
- Current work unit: Phase 1 — ADR-0017 only
- Boundary: starts from tracker branch state, ends with PR1 opened and green; no schema/code touched
- Estimated review budget impact: 212 changed lines, well under the 400-line budget; PR2 (schema bump) is next and estimated ~164 but should be assumed to run higher given historical under-forecast

## Status

4/4 Phase-1 tasks complete (1.1-1.4). PR1 open and green at
https://github.com/guillegil/vantage/pull/88, targeting tracker
`ft/run-metadata-capture`. Not merged (per instructions). Ready for the next
`sdd-apply` batch (Phase 2) once a maintainer decides whether to merge PR1
first or continue stacking PR2 on top of it per the chain.
