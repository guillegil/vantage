# Archive Report: plugin-server-compatibility

**Status**: Complete  
**Archived**: 2026-08-20 (delivered 2026-08-19, PR #44)  
**SDD Cycle**: Closed

## Executive Summary

The `plugin-server-compatibility` change has been fully implemented, verified, and archived. It shipped to `main` in PR #44 with all 13 tasks complete, fixing a silent data loss bug where newer plugins against older servers lost the end of every run. The change was archived late with its delta spec backfilled post-delivery because its decisions (D38–D42) were recorded in `tasks.md` instead of a delta spec at the time of implementation.

## Final State

All 13 implementation tasks marked complete:
- 1.1 through 1.13: All checked ✓
- Test status at delivery: 255 passed, zero warnings
- Baseline maintained: 255 passed, zero warnings
- No CRITICAL issues in verification report
- No unchecked implementation tasks blocking archive

## The Problem This Solved

A newer `pytest-vantage` plugin against an older `vantage` server silently lost the end of every run:
- The start-write created the run row
- The finish-write matched an existing id and was discarded whole (older server's `ON CONFLICT(id) DO NOTHING`)
- Finish metadata (`finished_at`, `exit_status`, `interrupted`, `interrupt_reason`) was lost
- Result rows still inserted, so the damage was not visibly empty — every run read as permanently unfinished
- The server answered the finish-write `200 {"status": "duplicate"}` without warning

This is a supported version-skew direction that was destroying data silently.

## The Solution

A capability advertisement endpoint that clients read before sending anything that matters:

**Design decisions (D38–D42):**

- **D38**: The server advertises one capability, not a version number. `GET /api/v1/capabilities` answers `{"session_lifecycle": true}`. There is one thing a client needs to know today; inventing a version scheme before a second capability exists would be guessing.
- **D39**: An older server's `404` is the answer, not a failure. Nothing has to change on the old side, which is the only reason this works at all.
- **D40**: The check fails closed. Only an explicit positive answer enables the lifecycle. Anything else — missing route, malformed body, wrong type, explicit false, empty body, error, timeout — degrades.
- **D41**: Degrading means the previous release, not a third state. No start-write, no heartbeats, finish report byte-identical to what shipped before the lifecycle existed. This mirrors `session-lifecycle`'s own rule for a failed start-write.
- **D42**: Bounded by the liveness timeout, never the report timeout. A preflight that blocks for the report timeout puts that cost in front of every session.

## Spec Merge

**Delta spec merged into `openspec/specs/session-ingestion/spec.md`**:

- **Source**: `openspec/changes/plugin-server-compatibility/specs/session-ingestion/spec.md`
- **Target**: `openspec/specs/session-ingestion/spec.md`
- **Action**: Appended "Capability advertisement" requirement with six scenarios to main spec
- **Preserved**: "Session report ingestion (RQ-41)" requirement and "Optional VCS section acceptance" requirement (added earlier today by `vcs-capture` archive) both remain intact
- **Status**: Two new requirements now in main spec — VCS section acceptance and capability advertisement

**Final spec now contains**:
1. Session report ingestion (RQ-41) — foundational ingestion contract
2. Malformed report rejection (RQ-42) — rejection and data safety
3. Optional VCS section acceptance — from `vcs-capture` change (2026-08-20)
4. Capability advertisement — from this change (backfilled 2026-08-20)

## Archive Contents

✓ proposal.md  
✓ specs/session-ingestion/spec.md (delta spec)  
✓ tasks.md (13/13 tasks complete)  
✓ archive-report.md (this file)  

Moved to: `openspec/changes/archive/2026-08-19-plugin-server-compatibility/`

## Process Note

This change was never archived because its delta spec was missing at the time of implementation. The decisions (D38–D42) were recorded in `tasks.md` instead of in a separate `specs/` directory. While the behaviour was tested and passed, the spec corpus did not carry it. This created an audit gap: the change sat in `openspec/changes/` looking "in flight" while its code was live on `main` for hours.

**The backfill was necessary because** the decisions are real, the tests are passing, and someone will need to find them. A spec written after delivery, describing scenarios whose tests already exist and pass, is preferable to an archived change folder with no spec at all. The header of the backfilled spec is explicit about the timing.

**For future changes**: Record decisions in delta specs alongside their tasks at the time of implementation, not in `tasks.md`. This keeps the corpus synchronized with the code and simplifies archive.

## SDD Cycle Complete

The change has been fully planned (proposal), specified (delta spec, backfilled after delivery), designed (decisions D38–D42), implemented (13 tasks), verified (255 tests, zero warnings), and archived.

Ready for the next change.
