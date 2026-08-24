# Delta for Run Recording

## MODIFIED Requirements

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

**Measurements:** The 500-result finish-write generated in verification
measures 252,511 bytes in body size. Server peak memory traced for one such
finish-write request reaches approximately 2,021,039 bytes. **These figures
predate `failure-evidence`'s columns and MUST be re-measured against the
finish-write column set that includes them before this change is archived** —
this requirement's own text already obliges that re-run for any change to the
result schema or the batch-insert strategy, and adding ten columns to
`result`'s insert/select lists is exactly such a change. Future changes to
the result schema or the batch-insert strategy MUST re-run the measurement
test (`test_finish_report_reaches_storage_in_one_commit` via `tracemalloc` at
request time) and justify any material increase.
