# Run Recording Specification

## Purpose

Defines what a pytest session must leave behind in the run table: exactly one
entry per invocation, its start/end timestamps, and the atomicity and
concurrency guarantees on that entry.

## Requirements

### Requirement: Run entry per invocation

When a pytest session starts with recording enabled, the system MUST create
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

### Requirement: Run timestamps

The system MUST record on each run entry the time its session started and
the time its session ended, leaving the end time null if the session never
finished.

#### Scenario: Completed session records both timestamps

- GIVEN a session that runs for at least two seconds
- WHEN it completes
- THEN its run entry holds a start time and an end time, and the end time is later than the start time

#### Scenario: Interrupted session leaves a null end time

- GIVEN a session interrupted with Ctrl-C
- WHEN the database is inspected afterwards
- THEN its run entry holds a start time and a null end time

### Requirement: Run atomicity

When a pytest session finishes, the system MUST make that session's results
observable either in full or not at all, never partially.

(At this milestone a run entry is a single row, so the guarantee holds
trivially by construction — SQLite commits or does not commit one row. It
becomes a substantive obligation once Milestone 2 adds result rows written
alongside it; listed here so it is not later assumed untested.)

#### Scenario: A written run entry is complete

- GIVEN a pytest session that completes recording
- WHEN its run entry becomes visible in the database
- THEN every field the session produced is present, never a partial subset

### Requirement: Concurrent session recording

While more than one pytest session is recording to the same database, the
system MUST record every one of those sessions' run entries.

Only criterion 1 (run-entry count) is in scope for this milestone. Criterion
2 — that concurrent sessions' *result* rows are also all recorded — depends
on result recording, which Milestone 1 does not implement, and is deferred
to Milestone 2.

#### Scenario: Two concurrent sessions both leave a run entry

- GIVEN two pytest sessions started within the same second against one database
- WHEN both complete
- THEN the database holds two run entries
