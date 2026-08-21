# Proposal: The plugin refuses to half-record against a server that cannot finish the job

## Intent

A newer `pytest-vantage` against an older `vantage` **silently loses the end of
every run**. Found by code review of the `session-lifecycle` chain, after four
verification rounds and 248 passing tests missed it.

An older server's run insert is `ON CONFLICT(id) DO NOTHING`. The start-write
creates the row; the finish-write then matches an existing id and is discarded
whole — `finished_at`, `exit_status`, `interrupted` and `interrupt_reason`, gone.
Result rows still insert, so the damage is not even visibly empty: every run
reads as permanently unfinished.

**Nothing warns.** The old server answers the finish-write `200 {"status":
"duplicate"}`, which the plugin does not inspect. The only symptom is the
heartbeat's `404`, and only after 30 seconds of activity, and only as one
warning on the liveness latch.

This is the skew direction `service/schemas.py`'s own module docstring calls
"ordinary, expected, supported". It is currently the one direction that
destroys data.

## Why now, before `main`

`session-lifecycle` is merged into its tracker and not yet in `main`. Once a
release carries the start-write, every user who upgrades the plugin before the
server hits this, and the failure is silent. The maintainer chose on 2026-08-19
to close it first.

## Scope

### In Scope

- A capability advertisement on the server that a client can read **before**
  sending anything that matters.
- A plugin preflight that reads it, and **degrades rather than half-records**:
  if the server cannot finish the job, send no start-write and no heartbeat,
  warn once, and record exactly as the previous release did.
- The degraded path must be the *previous release's* behaviour, not a new
  half-state — the same principle `session-lifecycle` used when its start-write
  failed.

### Out of Scope

- Version negotiation in the other direction. An older plugin against a newer
  server already works: it sends only the finish report, which the upsert's
  insert branch handles.
- Any read API.
- Semantic versioning of the wire contract as a whole. This advertises one
  capability, not a version scheme; inventing the scheme before there is a
  second capability to name would be guessing.

## The shape

The plugin's preflight today is a bare TCP connect (`plugin.py::_preflight_reachable`)
whose docstring makes a virtue of sending **no bytes**. Reading a capability
means it becomes a real HTTP request. That is the decision this change turns on,
and it is not free:

- It sends bytes to an address the user named, after activation is already
  confirmed. RQ-2 is untouched — the preflight only runs once the flag is
  present — but "sends no bytes" stops being true and the docstring must stop
  claiming it.
- It costs one round trip per session, on a path RQ-25 measures. One request
  per session is independent of test count, so RQ-25.2 holds; criterion 1's 2%
  budget is what to watch.
- An older server has no such endpoint and answers `404`. **That 404 is the
  signal**, not an error: it means "this server predates the lifecycle", which
  is exactly what the plugin needs to know. No new endpoint is required on the
  old side, which is the whole reason this works at all.

## Risks

| Risk | Mitigation |
| --- | --- |
| The preflight becomes a place where a slow server costs the user time | Bound it by the liveness timeout, not the report timeout — the same rule `session-lifecycle` applied to the beat |
| A capability check that fails open | It must fail **closed**: anything other than a positive answer means degrade. An unreachable, malformed or ambiguous response is not permission |
| Degrading silently | Warn once, naming the address and what was lost, on the liveness latch so it cannot cost results |

## Rollback

Revert restores the bare TCP preflight and the unconditional start-write. Nothing
persists, no schema moves, and an older server goes back to losing finish data —
which is the state this change exists to end, so the rollback is a return to a
known-bad, not to a neutral.

## Changed-line forecast

~250 against the 500 review budget. One slice.
