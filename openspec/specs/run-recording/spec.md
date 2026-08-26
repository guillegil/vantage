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
(Previously: only criteria 1-4 were specified; criteria 5 and 6, which make
the "when a session starts" trigger testable rather than satisfiable by
either reading of it, were absent.)

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

#### Scenario: A still-running session already has a run entry (RQ-1.5)
- GIVEN a session still running with recording enabled
- WHEN the database is queried before that session ends
- THEN its run entry already exists, holding a start time and a null end time

#### Scenario: A SIGKILL'd session's entry is present (RQ-1.6)
- GIVEN a session whose process is killed with SIGKILL while running
- WHEN the database is inspected afterwards
- THEN its run entry is present with a start time and a null end time

### Requirement: Run timestamps (RQ-31)

The server MUST record on each run entry the time its session started and
the time its session ended, leaving the end time null when the session never
finished in an orderly way.
(Previously: only criteria 1 and 2 were specified; criterion 3, covering a
kill Python is never given the chance to observe, was absent.)

#### Scenario: Completed session records both timestamps
- GIVEN a session that runs for at least two seconds
- WHEN it completes
- THEN its run entry holds a start time and an end time, and the end time is later than the start time

#### Scenario: Interrupted session leaves a null end time
- GIVEN a session interrupted with Ctrl-C
- WHEN the database is inspected afterwards
- THEN its run entry holds a start time and a null end time

#### Scenario: SIGKILL'd session carries no interrupt reason (RQ-31.3)
- GIVEN a session whose process is killed with SIGKILL
- WHEN the database is inspected afterwards
- THEN its run entry holds a start time and a null end time, and carries no interrupt reason

This criterion asserts an **absence**, not a value. SIGKILL cannot be caught,
blocked or handled: the process stops between two instructions and no code of
this project runs, so there is nothing for a reason to be recorded from. The
distinction between "finished" and "did not" survives a kill; the distinction
between one kind of death and another does not.

### Requirement: Run atomicity (RQ-3)

When a pytest session finishes and reports to the server, the server MUST
make that session's results observable either in full or not at all, never
partially — whether the write is cut off by the server dying mid-write or by
the report being truncated in transit.

"That session's results" means its run entry and every result row it reported,
written as one indivisible unit. A run entry present without the results its
report carried is a partial write and violates this requirement.

Each report a session sends — start, heartbeat, finish — is its own atomic
unit. A report already applied MUST NOT be overwritten by a stale or
reordered later report for the same run: a start-write arriving after that
run's finish has already been recorded MUST NOT null out the recorded finish,
its exit fields, or its result rows.
(Previously: this requirement's Measurements paragraph reflected the
column set before `failure-evidence` added its columns to `result`'s
insert/select lists; its own text obliges a re-run for exactly this kind of
change.)

#### Scenario: Server killed mid-write (RQ-3.1)
- GIVEN a session of 500 tests whose finish report reaches the server
- WHEN the server is killed with SIGKILL midway through writing it
- THEN the database afterwards holds either all 500 result rows of that session or none of them

**Verification method: Analysis, not Test.**

The argument: each accepted report — the start-write and the finish-write —
is written inside its own single `BEGIN IMMEDIATE` … `COMMIT`. A SIGKILL lands
either before that report's `COMMIT`, in which case SQLite's journal rolls
that report's transaction back on the next open, or after it, in which case
it is durable in full. There is no third position for that report, so there
is no partial state to observe from it.
`test_start_write_reaches_storage_in_one_commit` supplies the premise for the
start-write (one commit, one run row with a null `finished_at`);
`test_finish_report_reaches_storage_in_one_commit` (renamed from
`test_five_hundred_results_reach_storage_in_one_commit`) supplies it for the
finish-write (one commit, 500 result rows and the run update actually written
by it).

Why not a test: killing a process mid-transaction and asserting on what
survives is a test of SQLite's journal, not of this project's code, and it is
timing-dependent enough to be flaky in the 3.10–3.13 × xdist matrix.

What would invalidate this: any change that splits a single report's write
across more than one transaction. The commit-counting tests are what catch
that, which is why each asserts its row counts too — a commit that wrote
nothing would otherwise satisfy it.

#### Scenario: Report truncated in transit, no prior start-write (RQ-3.2)
- GIVEN a session of 500 tests with no prior accepted report
- WHEN its report is truncated in transit
- THEN the database holds none of that session's result rows rather than a prefix of them
- AND no run entry for that session is present either (RQ-42 criterion 3)

#### Scenario: Finish report truncated after an accepted start-write (RQ-3.2)
- GIVEN a session whose start-write has already been accepted, leaving a run entry with a null `finished_at`
- WHEN that session's finish report is truncated in transit
- THEN the database holds none of that session's result rows
- AND the run entry is left exactly as the start-write wrote it — a start time, a null `finished_at`, no exit fields and no result rows — rather than the finish report's data being applied

#### Scenario: Normal report is fully present (RQ-3.3)
- GIVEN a session of 500 tests that is reported and written normally
- WHEN the database is queried afterwards
- THEN all 500 result rows of that session are present

#### Scenario: A reordered start-write never nulls a recorded finish
- GIVEN a run whose finish has already been recorded, including its result rows
- WHEN a start-write for the same run id is received after that finish, out of order
- THEN the run entry still holds its recorded finish time, exit status, interrupt fields and every result row, unchanged

(The transit-truncation path shares its trigger with RQ-42's rejection
behaviour — see `session-ingestion`.)

#### Scenario: Measurements are re-run for the failure-evidence column set
- GIVEN `failure-evidence`'s columns added to `result`'s insert and select column lists
- WHEN the 500-result finish-write measurement test is re-run
- THEN this requirement's Measurements paragraph is updated with the re-measured body size and peak memory, and any material increase is justified in this spec

**Verification: Analysis** — a re-measurement against the existing
`tracemalloc`-based test, not a new assertion.

**Measurements, re-run 2026-08-25 against the widened `_INSERT_RESULT`/
`_SELECT_RESULTS_FOR_RUN` (31/33 columns) `failure-evidence` shipped**, via
`uv run pytest packages/vantage/tests/test_rejection.py -k "five_hundred_results_fit_within_the_body_cap
or server_peak_memory_for_one_five_hundred_result_request" -s` and one ad hoc
script exercising `pytest_vantage.budget.spend_failure_text_budget` against a
representative all-failing 500-result report (methodology below; the script
itself is not committed — this paragraph is the citable record, the same
treatment `version-control-context`'s Measurements paragraph gives its own
script's output):

- **No-failure 500-result body: 252,511 bytes, unchanged** from the
  pre-`failure-evidence` figure. Stronger than "the new keys are `null`"
  (design.md D80's forecast): a passing result carries none of the
  seventeen new keys at all — they are absent from the wire, never emitted
  as `null` — so a session with no failures costs nothing extra on the wire.
- **Server peak memory, one 500-result finish-write request: 2,880,085
  bytes, up from 2,021,039 (+859,046 bytes, +42.5%).** Material, and
  explained: seventeen more `Optional[str | int | bool]` fields per result,
  carried through both the wire-parsing model and the widened 31-column SQL
  insert tuple, cost real Python object overhead even when every one of
  them is `None` for all 500 results — a cost `tracemalloc` sees on the
  server that the (unchanged) wire body does not.
- **All-failing 500-result body (representative failure evidence and
  captured output on all 500 results, spent through the plugin's
  `spend_failure_text_budget` exactly as a real session would be): measured
  794,291 bytes.** This is 17,492 bytes (2.25%) **over** design.md D80's
  originally-stated bound of `252,511 + MAX_FAILURE_TEXT_BYTES` = 776,799
  bytes — the bound formula undercounted, not this measurement. The budget
  charges only the five budgeted fields' own JSON-encoded *values*; it
  never charges their key names and punctuation, nor the twelve
  non-budgeted columns present on every failing result (`failure_type`,
  `failure_path`, `failure_lineno`, `skip_reason`, `xfail_reason`, and the
  seven `_truncated` flags) — each short by construction, but not zero
  across 500 results. **Still 254,285 bytes (24.2%) under `MAX_REPORT_BYTES`
  (1,048,576)** for a 500-result, 100%-failing session, so this undercount
  does not put RQ-3's whole-report cap at risk in practice; design.md D80
  carries the corrected derivation.

This requirement's own text already obliges a re-run for any change to the
result schema or the batch-insert strategy, and widening `result`'s
insert/select lists by seventeen columns for `failure-evidence` is exactly
such a change. Future changes to the result schema or the batch-insert
strategy MUST re-run the measurement test
(`test_finish_report_reaches_storage_in_one_commit`,
`test_five_hundred_results_fit_within_the_body_cap` and
`test_server_peak_memory_for_one_five_hundred_result_request`, via
`tracemalloc` at request time for the memory figure) and justify any
material increase.

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
