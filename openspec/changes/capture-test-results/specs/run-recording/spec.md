# Delta for Run Recording

Both requirements below currently carry an explicit carve-out saying no result
rows are written, so their result-counting criteria could not be exercised.
This change writes result rows, so the carve-outs are removed and the deferred
criteria are supplied. Requirements RQ-1 and RQ-31 are untouched.

## RENAMED Requirements

### Requirement: Concurrent session recording (RQ-38, criterion 1 only) → Concurrent session recording (RQ-38)

(Reason: criteria 2 and 3 count results. They were out of scope while no result
rows existed and are in scope now, so the heading's scope qualifier is no
longer true.)
(Migration: references to the qualified heading — in specs, tasks or verify
reports — become the unqualified one. Tests already marked
`@pytest.mark.req("RQ-38")` need no change.)

## MODIFIED Requirements

### Requirement: Run atomicity (RQ-3)

When a pytest session finishes and reports to the server, the server MUST
make that session's results observable either in full or not at all, never
partially — whether the write is cut off by the server dying mid-write or by
the report being truncated in transit.

"That session's results" means its run entry and every result row it reported,
written as one indivisible unit. A run entry present without the results its
report carried is a partial write and violates this requirement.
(Previously: the requirement carried a carve-out stating that no result rows
were written, so it was exercised through the run entry alone.)

#### Scenario: Server killed mid-write (RQ-3.1)
- GIVEN a session of 500 tests whose report reaches the server
- WHEN the server is killed with SIGKILL midway through writing it
- THEN the database afterwards holds either all 500 result rows of that session or none of them

#### Scenario: Report truncated in transit (RQ-3.2)
- GIVEN a session of 500 tests
- WHEN its report is truncated in transit
- THEN the database holds none of that session's result rows rather than a prefix of them
- AND no run entry for that session is present either (RQ-42 criterion 3)

#### Scenario: Normal report is fully present (RQ-3.3)
- GIVEN a session of 500 tests that is reported and written normally
- WHEN the database is queried afterwards
- THEN all 500 result rows of that session are present

(The transit-truncation path shares its trigger with RQ-42's rejection
behaviour — see `session-ingestion`.)

### Requirement: Concurrent session recording (RQ-38)

While more than one pytest session is reporting to the server concurrently,
the server MUST record every one of those sessions' run entries and every
result those sessions reported, and MUST NOT answer any of them with an error
response.
(Previously: scoped to criterion 1 only — run entries — because no result rows
were written and criteria 2 and 3 count results.)

#### Scenario: Two concurrent sessions both leave a run entry (RQ-38.1)
- GIVEN two pytest sessions started within the same second against one server
- WHEN both complete
- THEN the database holds two run entries with different identifiers

#### Scenario: Two concurrent 200-test sessions leave 400 results (RQ-38.2)
- GIVEN two pytest sessions of 200 tests each started within the same second against one server
- WHEN both complete
- THEN the database holds 400 result rows

#### Scenario: Ten simultaneous sessions all succeed (RQ-38.3)
- GIVEN ten pytest sessions reporting simultaneously
- WHEN all complete
- THEN the database holds ten run entries
- AND no session receives an error response
