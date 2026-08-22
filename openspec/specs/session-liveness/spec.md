# Session Liveness Specification

## Purpose

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

## Requirements

### Requirement: Heartbeat endpoint

The server MUST expose `POST /api/v1/runs/{id}/heartbeat`, distinct from the
run-report envelope used by `/api/v1/runs`, whose only effect is advancing
that run's `last_contact_at` to the time the request was received. It MUST
NOT accept or apply `finished_at`, `exit_status`, `interrupted` or
`interrupt_reason` — a bare `finished_at: null` on the existing envelope
cannot distinguish "still running" from "erase a recorded finish," which is
why this is its own endpoint rather than a field.

#### Scenario: A heartbeat advances last contact
- GIVEN a run created by an accepted start-write
- WHEN a heartbeat is submitted for its id
- THEN `last_contact_at` for that run advances to the time of the request

#### Scenario: A heartbeat cannot touch finish fields
- GIVEN a run whose finish has already been recorded
- WHEN a heartbeat is submitted for its id
- THEN `finished_at`, `exit_status`, `interrupted` and `interrupt_reason` remain exactly as recorded

### Requirement: Activity-driven last-contact tracking (RQ-25 criterion 2)

While a pytest session is active, the plugin MUST send heartbeats whose
request count is independent of the number of tests executed, driven by
elapsed wall-clock time rather than test count.

#### Scenario: A long suite's last contact advances during execution
- GIVEN a suite whose execution exceeds one heartbeat interval
- WHEN the suite runs
- THEN `last_contact_at` for its run advances at least once before the session finishes

#### Scenario: A fast suite emits no heartbeat
- GIVEN a suite of 1,000 tests each taking approximately 10 milliseconds (RQ-25's measured profile)
- WHEN it runs to completion within one heartbeat interval
- THEN no heartbeat request is sent

### Requirement: A single long test is not observed mid-body (documented limitation)

No pytest hook fires during a single test's body. A session containing one
test whose body runs longer than the grace period MAY be presented as
abandoned while it is still executing. This is an accepted, documented
limitation of activity-driven heartbeats, mitigated by a generous,
configurable grace period — not a defect for a future change to silently
patch over.

#### Scenario: A single very long test can read as abandoned while alive
- GIVEN a session containing exactly one test whose body runs longer than the configured grace period
- WHEN no further contact is recorded during that test's body
- THEN the run may derive as abandoned before that test completes, and this is the stated, accepted behavior of this requirement, not a bug

### Requirement: Abandoned run is observable (RQ-44)

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

### Requirement: Grace period is server-side, configurable, and measured from last contact

The server MUST measure the grace period from a run's last contact, never
from its start time, and MUST make the grace period configurable, defaulting
to a value expressed as a multiple of the heartbeat interval (~15 minutes at
the ~30 s default interval, ~2.0 s bounded per-beat timeout mirroring the
existing `_MAX_CONNECT_TIMEOUT` preflight pattern).

#### Scenario: Grace is measured from last contact, not start
- GIVEN a run whose session has been active, with contact well past its original start time, for longer than the default grace period
- WHEN the abandonment-derivation helper is invoked against it before its last-contact timeout has elapsed
- THEN it derives that run as running, not abandoned

#### Scenario: Grace period is configurable
- GIVEN a server configured with a non-default grace period
- WHEN the abandonment-derivation helper is invoked against a run whose last contact is older than the configured value but younger than the default
- THEN it derives that run as abandoned, honoring the configured value rather than the default
