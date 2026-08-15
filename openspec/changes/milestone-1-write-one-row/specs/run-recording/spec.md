# Run Recording Specification

## Purpose

Defines what the server persists for every pytest session: exactly one run
entry per invocation, its start/end timestamps, atomicity of that write, and
correctness when more than one session reports concurrently.

**Component:** `vantage` (server) — the ingestion path from `vantage.service`
through `vantage.core`'s storage port to `vantage.storage`.

## Requirements

### Requirement: Run entry per invocation (RQ-1)

When a pytest session starts with recording enabled, the server MUST create
exactly one run entry whose identifier is unique across every run in the
database, regardless of whether the session collects any tests or fails
collection.

#### Scenario: First invocation against an empty database
- GIVEN an empty database
- WHEN pytest is invoked once with recording enabled
- THEN the run table contains exactly one row

#### Scenario: Second invocation gets a distinct identifier
- GIVEN a database already holding one run
- WHEN pytest is invoked again
- THEN the run table contains two rows and their identifiers differ

#### Scenario: Zero-test collection still writes a row
- GIVEN a directory containing no test files
- WHEN pytest is invoked with recording enabled
- THEN the run table contains exactly one row

#### Scenario: Failed collection still writes a row
- GIVEN a test file that raises ImportError at collection
- WHEN pytest is invoked with recording enabled
- THEN the run table contains exactly one row

### Requirement: Run timestamps (RQ-31)

The server MUST record on each run entry the time its session started and
the time its session ended, leaving the end time null when the session never
finished in an orderly way.

#### Scenario: Completed session records both timestamps
- GIVEN a session that runs for at least two seconds
- WHEN it completes
- THEN its run entry holds a start time and an end time, and the end time is later than the start time

#### Scenario: Interrupted session leaves a null end time
- GIVEN a session interrupted with Ctrl-C
- WHEN the database is inspected afterwards
- THEN its run entry holds a start time and a null end time

### Requirement: Run atomicity (RQ-3)

When a pytest session finishes and reports to the server, the server MUST
make that session's results observable either in full or not at all, never
partially — whether the write is cut off by the server dying mid-write or by
the report being truncated in transit.

#### Scenario: Server killed mid-write
- GIVEN a session of 500 tests whose report reaches the server
- WHEN the server is killed with SIGKILL midway through writing it
- THEN the database afterwards holds either all 500 results of that session or none of them

#### Scenario: Report truncated in transit
- GIVEN a session of 500 tests
- WHEN its report is truncated in transit
- THEN the database holds none of that session's results rather than a prefix of them

#### Scenario: Normal report is fully present
- GIVEN a session of 500 tests that is reported and written normally
- WHEN the database is queried afterwards
- THEN all 500 results of that session are present

(This milestone writes no result rows, so this requirement is exercised
through the run entry; the transit-truncation path shares its trigger with
RQ-42's rejection behaviour — see `session-ingestion`.)

### Requirement: Concurrent session recording (RQ-38, criterion 1 only)

While more than one pytest session is reporting to the server concurrently,
the server MUST record every one of those sessions' run entries.

Only criterion 1 is in scope for this milestone. Criteria 2 and 3 count
results, and this milestone writes none; both are carried to Milestone 2.

#### Scenario: Two concurrent sessions both leave a run entry
- GIVEN two pytest sessions started within the same second against one server
- WHEN both complete
- THEN the database holds two run entries with different identifiers
