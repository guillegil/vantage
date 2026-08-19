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

"That session's results" means its run entry and every result row it reported,
written as one indivisible unit. A run entry present without the results its
report carried is a partial write and violates this requirement.

#### Scenario: Server killed mid-write (RQ-3.1)
- GIVEN a session of 500 tests whose report reaches the server
- WHEN the server is killed with SIGKILL midway through writing it
- THEN the database afterwards holds either all 500 result rows of that session or none of them

**Verification method: Analysis, not Test.** Decided 2026-08-19, after
`sdd-verify` reported this scenario as the change's one untested obligation.

The argument: the whole session — run entry, catalogue upsert and every result
row — is written inside a single `BEGIN IMMEDIATE` … `COMMIT`. A SIGKILL lands
either before that `COMMIT`, in which case SQLite's journal rolls the whole
transaction back on the next open, or after it, in which case the transaction
is durable in full. There is no third position, so there is no partial state to
observe. `test_five_hundred_results_reach_storage_in_one_commit` supplies the
premise that the argument depends on: exactly one commit, and 500 result rows
and one run entry actually written by it.

Why not a test: killing a process mid-transaction and asserting on what
survives is a test of SQLite's journal, not of this project's code, and it is
timing-dependent enough to be flaky in the 3.10–3.13 × xdist matrix.

What would invalidate this: any change that splits the session write across
more than one transaction. The commit-counting test is what catches that, which
is why it asserts the row counts too — a commit that wrote nothing would
otherwise satisfy it.

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

**Measurements:** The 500-result session report generated in verification
measures 252,511 bytes in body size. Server peak memory traced for one such
request reaches approximately 2,021,039 bytes. These measurements are
diagnostic; they inform payload size budgeting and resource planning for
deployments. Future changes to the result schema or the batch-insert strategy
MUST re-run the measurement test (`test_five_hundred_results_reach_storage_in_one_commit` via `tracemalloc` at request time) and justify any material increase.

### Requirement: Concurrent session recording (RQ-38)

While more than one pytest session is reporting to the server concurrently,
the server MUST record every one of those sessions' run entries and every
result those sessions reported, and MUST NOT answer any of them with an error
response.

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
