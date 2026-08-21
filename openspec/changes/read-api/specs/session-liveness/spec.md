# Delta for Session Liveness

## Purpose

> **Archive instruction — this section does not merge itself.** The delta
> format auto-merges `ADDED` / `MODIFIED` / `REMOVED` / `RENAMED` requirement
> blocks only; it has no slot for a capability's Purpose text, and every
> archived delta in this repository that carries a `## Purpose` was an ADDED
> capability, not a modification. So `sdd-archive` MUST replace
> `openspec/specs/session-liveness/spec.md`'s Purpose section with the text
> below **by hand**. Skipping it leaves that spec asserting that presenting
> the derived state "waits for a read API that does not exist yet",
> immediately above a requirement whose verification method is Demonstration
> through the read API this change shipped — a spec contradicting itself on
> the same page.


Defines how the server knows a session is still alive while it runs, and how
it derives a run's presentation — running, interrupted, or abandoned — from
last contact and a configured grace period. This capability maintains the
data (`last_contact_at`, the derivation helper) and the heartbeat wire
contract. The derived state is now presented: `history-read-api` supplies a
live read path, so the *Abandoned run is observable* read-back criteria are
Demonstration through that path rather than Analysis against the derivation
helper in isolation.

**Component:** across the boundary — `pytest-vantage` sends heartbeats,
activity-driven off `pytest_runtest_logreport`; `vantage.service` exposes the
endpoint and the read path; `vantage.storage` maintains `last_contact_at`;
`vantage.core` hosts the stdlib-only abandonment-derivation helper, which
`architecture-boundaries` → *Core isolation* requires stay free of any
pytest, database or web import.

## MODIFIED Requirements

### Requirement: Abandoned run is observable (RQ-44)

> **Heading note.** This heading carries the merged capability spec's exact
> text, trailing identifier included, because the archive merge matches a
> `MODIFIED` block to its target **by heading text**. A heading that does not
> match appends a duplicate requirement instead of replacing the existing one.
> The identifier is a join key here, not vocabulary: this change writes none
> of its own, and this suffix disappears when the merged spec is renumbered
> in a change of its own.


If a run entry has a start time and no end time and no report or heartbeat
contact has arrived for it within a configured grace period, then the system
MUST derive that run's presentation as abandoned rather than as still
running. This presentation MUST be observable by reading the run back
through the read API `history-read-api` supplies, not only by invoking the
derivation helper directly.
(Previously: read-back criteria were Analysis, verified by invoking the
abandonment-derivation helper directly; no live read path existed to
demonstrate through.)

**Verification: Demonstration**, through the live read path.

#### Scenario: A run past its grace period reads back as abandoned
- GIVEN a run entry with a start time, no end time, and no contact recorded for longer than the configured grace period
- WHEN that run is read back through the read API
- THEN its presented state is abandoned

#### Scenario: A run inside its grace period reads back as running
- GIVEN a run entry with a start time, no end time, and its last contact inside the configured grace period
- WHEN that run is read back through the read API
- THEN its presented state is running, not abandoned

#### Scenario: A Ctrl-C interrupted run reads back as interrupted
- GIVEN a run entry reported as interrupted with Ctrl-C
- WHEN that run is read back through the read API
- THEN its presented state is interrupted, not abandoned, because a report did arrive for it

#### Scenario: Abandonment invents no stored field
- GIVEN a run whose presented state, read back through the read API, is abandoned
- WHEN its stored record is inspected directly
- THEN the start time it was recorded with is unchanged, and no column represents an end that never happened
