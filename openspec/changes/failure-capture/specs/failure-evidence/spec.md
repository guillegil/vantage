# Failure Evidence Specification

## Purpose

Defines what a failed result records beyond its outcome: the traceback,
rendered independently of the session's display flags; the failure's type,
message and location as separately queryable values, plus its full
representation; the captured stdout and stderr, with empty distinguished
from absent; the 64 KiB per-field bound and its out-of-band truncation flag;
the per-report encoded-byte budget spent before a session report is
assembled; the opt-out that disables this capture under `session-ingestion`'s
opt-in rule; and the disclosure that stored failure text is unredacted. New
capability. `result-capture` defines what every result records regardless of
outcome; this capability defines what a failed one adds. List responses
exclude the fields defined here — `history-read-api` → *Lean list
projections*.

**Component:** both — `pytest-vantage` renders the traceback, reads the
failure's type/message/location, captures stdout/stderr, and spends the
per-report budget, all before the session report is built; `vantage`
applies the 64 KiB per-field bound and persists the result through
`vantage.core`'s storage port. The plugin opens no database (ADR-9) and
imports nothing beyond pytest and the standard library (RQ-24).

## Requirements

### Requirement: Traceback capture invariant to display flags

For each failed or errored result, the system MUST record a traceback
rendered independently of the session's `--tb` display flag, so the stored
value is complete regardless of what was shown on the terminal.

**Verification: Test.**

#### Scenario: The traceback is complete under `--tb=no`
- GIVEN a test whose failure raises through three stack frames, run with `--tb=no`
- WHEN the result is recorded
- THEN the stored traceback names all three frames

#### Scenario: The traceback is complete under `--tb=line`
- GIVEN a test whose failure raises through three stack frames, run with `--tb=line`
- WHEN the result is recorded
- THEN the stored traceback names all three frames

### Requirement: Failure location, type and message

For each failed or errored result, the system MUST record its failure type,
message, source path and line number as separately queryable values, and its
full representation as a separately queryable field, so that failures at the
same source location can be grouped without inspecting the traceback text.
The recorded location MUST be the frame that raised, not the test function's
first line. For a skipped or xfailed result — whose underlying
representation is not an exception — the system MUST record its skip or
xfail reason instead of these failure fields, and reading that shape MUST
NOT raise.

**Verification: Test.**

#### Scenario: Twenty tests failing at one source line group as one
- GIVEN twenty tests that all raise from the same source path and line
- WHEN their results are recorded and grouped by failure location
- THEN they come back as one group rather than twenty distinct ones

#### Scenario: The recorded location is the raising site
- GIVEN a test whose body calls a helper function, and the helper is where the exception is raised
- WHEN the result is recorded
- THEN the recorded failure path and line number identify the helper's raising line, not the test function's first line

#### Scenario: A skipped test does not crash the recorder
- GIVEN a test skipped via a skip marker, carrying no exception
- WHEN the result is recorded
- THEN its failure type, message, path and line number are absent, its skip reason is recorded instead, and recording does not raise

### Requirement: Captured output, empty distinct from absent

For each result, the system MUST record its captured stdout and captured
stderr, and MUST distinguish output that was empty from output that was
never captured.

**Verification: Test.**

#### Scenario: A silent test has empty captured output, not absent
- GIVEN a test that prints nothing during any phase
- WHEN the result is recorded
- THEN its captured stdout is the empty string, not absent

#### Scenario: Capture disabled leaves output absent, not empty
- GIVEN a session run with capture disabled
- WHEN a result is recorded
- THEN its captured stdout and captured stderr are absent, not the empty string

### Requirement: Per-field 64 KiB bound

The system MUST bound each of the traceback, failure message, failure
representation, captured stdout and captured stderr fields to 64 KiB, and
MUST record alongside each field a truncation flag carried out-of-band from
the text itself — the same shape already used for a run's commit subject —
so the flag cannot be forged by content the field itself contains. A field
dropped entirely by the per-report budget (below) MUST also set this flag,
rather than appearing merely absent.

**Verification: Test.**

#### Scenario: An oversized field is stored truncated, flagged
- GIVEN a failed test whose traceback exceeds 64 KiB
- WHEN the result is recorded
- THEN the stored traceback is bounded to 64 KiB and its truncation flag is set

#### Scenario: A field within bound is stored whole, unflagged
- GIVEN a failed test whose traceback is under 64 KiB
- WHEN the result is recorded
- THEN the stored traceback is unchanged and its truncation flag is clear

### Requirement: Per-report failure-text budget

Before a session report is assembled, the system MUST bound the total
JSON-encoded bytes spent across the traceback, failure message, failure
representation, captured stdout and captured stderr fields of every result
in that report to a documented per-report budget, so a session carrying many
richly-failing tests is not rejected by the server's whole-report size cap.
A field dropped to remain within that budget MUST be flagged as truncated
rather than silently omitted.

**Verification: Test.**

#### Scenario: A session of many large failures stays within the report size cap
- GIVEN a session whose failed results would otherwise carry enough failure text to exceed the report size cap
- WHEN the session report is assembled
- THEN its encoded body stays within the per-report budget

#### Scenario: A field dropped for budget is flagged, not missing
- GIVEN a session whose per-report budget is exhausted partway through assembling its results
- WHEN a subsequent result's failure text is dropped to stay within budget
- THEN that field's truncation flag is set rather than the field appearing as if it had never been captured

#### Scenario: A session within budget carries no exhaustion flags
- GIVEN a session whose total failure text fits the per-report budget without dropping any field
- WHEN the session report is assembled
- THEN no field's truncation flag is set as a result of the budget

### Requirement: Capture opt-out under the opt-in rule

The system MUST provide a session-level opt-out that disables failure-text
capture. The opt-out's activation MUST be available as an invocation flag. A
configuration value MAY narrow what an already-activated session records,
but no committed configuration file MAY be the means by which capture is
enabled or the opt-out disabled — the same invariant RQ-2 already holds for
recording itself.

**Verification: Test**, differential — the same form RQ-2's own opt-in test
uses.

#### Scenario: The opt-out suppresses failure-text capture
- GIVEN a session invoked with the failure-capture opt-out
- WHEN a test in that session fails
- THEN its result is recorded without a traceback, failure fields or captured output

#### Scenario: A committed configuration file cannot enable capture on its own
- GIVEN a project whose committed configuration file sets no invocation flag
- WHEN the suite is run once with that file present and once with it absent
- THEN failure-text capture behaves identically in both runs

#### Scenario: The opt-out does not suppress the rest of the result
- GIVEN a session invoked with the failure-capture opt-out
- WHEN a test in that session fails
- THEN its outcome, timings and identity are still recorded in full

### Requirement: Unredacted storage is disclosed

The system's documentation MUST disclose that stored failure text, including
captured output, is not redacted and may contain any value a test printed or
asserted, including credentials.

**Verification: Inspection** — a documentation property, not a runtime
assertion.

#### Scenario: The disclosure is present in the capability spec and the README
- GIVEN this capability's documentation and the project README
- WHEN they are inspected
- THEN both state plainly that stored failure text is unredacted and may contain sensitive values
