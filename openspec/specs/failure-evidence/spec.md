# Failure Evidence Specification

## Purpose

Defines what a failed result records beyond its outcome: the traceback,
rendered independently of the session's display flags; the failure's type,
message and location as separately queryable values, plus its full
representation; the captured stdout and stderr, with empty distinguished
from absent; the 64 KiB per-field bound and its out-of-band truncation flag;
the per-report encoded-byte budget spent before a session report is
assembled; the opt-in that enables this capture at all, absent by default
under `session-ingestion`'s opt-in rule; and the disclosure that stored
failure text is unredacted. New
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

**Measurements (RQ-25):** Measured 2026-08-25 on
`Linux-6.18.33.2-microsoft-standard-WSL2-x86_64` (WSL2), Python 3.10.21, via
`scripts/measure_failure_capture_overhead.py` — five interleaved A/B/A/B…
pairs per cell (A = recording on, failure capture absent — the default, no
invocation flag given; B = the identical session with failure capture
opted in via `--vantage-failure-text`), plus three recording-off context
samples per cell, all against this repository's own working tree (not the
synthetic 20,000-file repository `version-control-context` also measures
against). 1,000 tests at ~10 ms, crossed with failure density and `--tb`.
**The columns are labelled by what they mean today, after D72's revision —
the underlying sessions measured are unchanged from what was actually run,
and no number below was altered:**

| Density | `--tb` | OFF | A (default, absent) | B (opt-in) | Whole-session overhead (B vs OFF) | Per-failed-test cost (B vs A) | Fails |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1% | auto | 11.263s | 11.335s | 11.652s | 3.45% | 31.72 ms | 10 |
| 1% | no | 10.961s | 10.997s | 11.368s | 3.71% | 37.10 ms | 10 |
| 10% | auto | 14.430s | 14.495s | 17.955s | 24.43% | 34.60 ms | 100 |
| 10% | no | 11.072s | 11.058s | 14.561s | 31.52% | 35.03 ms | 100 |
| 100% | auto | 60.394s | 60.483s | 108.850s | 80.23% | 48.37 ms | 1000 |
| 100% | no | 11.342s | 11.367s | 59.987s | 428.94% | 48.62 ms | 1000 |

**Pre-measurement forecast** (design.md D79, recorded so the result can
visibly disagree with it): ~55 ms of RQ-25 headroom remains after
`version-control-context`'s own spend; the 1% profile (10 failures) expected
to fit, the 10% profile marginal, and the 100% profile expected to exceed
the 2% budget; `--tb=no` expected to be markedly more expensive (cold
rendering, nothing pre-rendered for the terminal).

**Every measured density breaches RQ-25's 2% budget, not only the 100%
profile — recorded as measured, never adjusted, no failure-count cap
invented.** The A-column confirms the default (capture-absent) path itself
costs nothing (0.15%–0.64% of OFF, noise-level, in every row except one
within-noise negative) — **this is why capture is now opt-in: the default
path this measurement calls "A" is the path every session takes unless it
asks otherwise, and it is RQ-25-compliant.** But even the **1% profile — ten
failing tests out of a thousand — already costs 3.45%–3.71% of OFF** once a
session opts in, not the "expected to fit" the forecast predicted: ten
failures at ~32–37 ms each is 317–371 ms, and `version-control-context`'s own
spend already leaves only ~55 ms of this repository's ≈220 ms budget (2% of
an ~11 s OFF suite) once its own git-read cost is paid. Per-failed-test cost
is not the constant the ~55 ms/N forecast assumed either: it rises with
density (31.7→34.6→48.4 ms under `--tb=auto`), consistent with a growing
in-process results list and a larger per-report JSON body dominating at
scale rather than the rendering call itself. `--tb=no` is confirmed the more
expensive branch in every row, as forecast — but by a much smaller margin
(0–17%) than "markedly", not the large gap the cold-rendering argument
implied; at 100% density the two are within 0.5% of each other, most likely
because all synthetic failures share one raising site, so the linecache
warms on the first render regardless of which branch pays it.

**No failure-count cap is invented here.** D79's own text anticipated this
outcome for the 100% profile; this measurement extends the same finding to
every density tested, including the ordinary case of a handful of failures
in an otherwise-passing suite — which is exactly why the response was a
polarity flip rather than a narrower mechanism: absent-by-default costs
nothing for the common case, where a count cap would still cost something
for every session under it. A cap was considered and rejected on its own
arithmetic (see the requirement below and design.md's revised D72): the
~55 ms of remaining headroom admits roughly four rendered failures before
it is spent, which is not a failure-capture feature. Whether a lower
per-report budget or another mechanism is worth building for a session that
does opt in remains open — see `docs/open-questions.md` OQ-11.

### Requirement: Capture is opt-in, absent by default

The system MUST NOT capture failure text unless the session's invocation asks
for it. The opt-in's activation MUST be available as an invocation flag. A
configuration value MAY narrow what an already-activated session records, but
no committed configuration file MAY be the means by which capture is enabled —
the same invariant RQ-2 already holds for recording itself, and for the same
reason: a file one person commits would otherwise silently enable capture for
everyone who checks the repository out.

**This polarity is required by RQ-25, not chosen for taste.** Measurement (see
Measurements below) puts failure-text capture at 3.45% of session wall time at
1% failure density, against RQ-25's 2% budget — a breach at every density
measured, not only the pathological one. With capture absent by default the
default path costs 0.15–0.64%, noise level, and RQ-25 holds; a session that
asks for capture has accepted the cost knowingly. Capping the number of
failures captured was considered and rejected: the arithmetic admits roughly
four failures per session before the budget is spent, which is not a
failure-capture feature.

**Verification: Test**, differential — the same form RQ-2's own opt-in test
uses.

#### Scenario: Absent the opt-in, no failure text is captured
- GIVEN a recording session invoked without the failure-capture opt-in
- WHEN a test in that session fails
- THEN its result is recorded without a traceback, failure fields or captured output

#### Scenario: The opt-in enables failure-text capture
- GIVEN a recording session invoked with the failure-capture opt-in
- WHEN a test in that session fails
- THEN its traceback, failure fields and captured output are recorded

#### Scenario: A committed configuration file cannot enable capture on its own
- GIVEN a project whose committed configuration file sets no invocation flag
- WHEN the suite is run once with that file present and once with it absent
- THEN failure-text capture behaves identically in both runs

#### Scenario: Capture being absent does not suppress the rest of the result
- GIVEN a recording session invoked without the failure-capture opt-in
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
