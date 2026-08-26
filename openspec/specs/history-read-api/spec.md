# History Read API Specification

## Purpose

Defines the server-side surface that reads back what has already been
recorded: a run list, a run detail, a run's results, and a test's execution
history — plus the read-only guarantee and the latency bound that surface
must hold. New capability; nothing previously read this data back except by
opening the database file directly.

**Component:** across the boundary — `vantage.core` hosts `derive_presentation`
(reused, first caller); `vantage.storage` implements the read queries on
`ExecutionStore` in both adapters; `vantage.service` owns the read router and
response shaping.

**Wire identity of a test is not fixed here.** `node_id` contains `/`, `::`,
`[`, `]`, and `stable_id` supersedes it later (`schema.sql`); every scenario
below describes behavior against "a test's identity," not a URL or parameter
shape — that choice is design's.

## Requirements

### Requirement: Read-only read surface

Every endpoint declared as a read path in the machine-readable interface
document MUST leave stored data unchanged: calling it MUST NOT alter any row,
column, or row count in the store. This guarantee is scoped to the
document-declared read paths; endpoints that write (session-report and
heartbeat ingestion) are excluded by name in `session-ingestion`.

**Verification: Test**, via a digest pair — a main-file digest taken with
connection state pinned, plus a logical content digest over every table —
together with unchanged `count_executions()`/`count_results()`. A naive
before/after file hash is unstable because the store opens WAL and a read
connection can checkpoint on close, which is unrelated to writing.

#### Scenario: Reading leaves stored data unchanged
- GIVEN a database holding at least one run and its results
- WHEN every document-declared read endpoint is called in sequence
- THEN the logical content digest over every table, taken before and after, is identical, and `count_executions()`/`count_results()` are unchanged

#### Scenario: The main-file digest is stable despite WAL checkpointing
- GIVEN a read connection open against the store, taken with its state pinned
- WHEN the main database file's digest is taken before and after a full read sequence
- THEN the two digests match, independent of WAL checkpoint timing

### Requirement: Test history

For a given test identity, its execution history MUST return entries newest
first, each carrying its full VCS context — commit, branch, commit subject,
the truncation flag, and the dirty flag — and its duration. An identity with
no recorded executions MUST yield an empty history, not an error. An
execution recorded from a directory that is not a git repository MUST be
returned with a null VCS context rather than omitted.

**Verification: Test**, over all three criteria — the commit is recorded now
that `vcs-capture` is archived.

#### Scenario: Executions return newest first, with full VCS context
- GIVEN a test that has run in multiple sessions, at least one from a repository with a named branch and a dirty tree
- WHEN its history is requested
- THEN entries are ordered newest first by start time, and each carries its commit, branch, commit subject, truncation flag, dirty flag, and duration

#### Scenario: An unknown test yields empty history, not an error
- GIVEN a test identity with no recorded executions
- WHEN its history is requested
- THEN the response reports zero entries rather than an error

#### Scenario: A non-repository execution has a null VCS context, not an omitted entry
- GIVEN an execution recorded from a directory that is not a git repository
- WHEN that test's history is requested
- THEN the entry for that execution is present with a null VCS context — not five fields independently null, and not omitted from the list

#### Scenario: `vcs_root` appears in no history entry
- GIVEN an execution recorded from a repository with a known `vcs_root`
- WHEN that test's history is requested
- THEN the recorded `vcs_root` value does not appear anywhere in the response body

### Requirement: Lean list projections

List responses (run list, and any results list) MUST carry only
bounded-size fields, excluding traceback and captured output; the full
record MUST remain reachable via the corresponding single-item endpoint. A
run's commit subject in a list response MUST be limited to a fixed,
documented display width smaller than the stored value, with the full
stored subject reachable on the run's detail endpoint. The truncation flag
MUST travel with the subject wherever the subject appears, never surfaced on
its own. `vcs_root` MUST appear in no response, list or detail.
(Previously: the traceback/captured-output exclusion half was verified only
by Inspection, because `result.traceback` had no writer and the exclusion
could not fail. `failure-evidence` now populates it, so the exclusion is
Test.)

**Verification: Test**, over both halves — the VCS-projection half (subject
width, truncation flag, `vcs_root` exclusion) and the traceback/captured-
output exclusion half, now that `failure-evidence` populates `traceback`,
`captured_stdout` and `captured_stderr`.

#### Scenario: List responses exclude traceback and captured output
- GIVEN a result whose traceback and captured stdout/stderr are recorded
- WHEN that result appears in a list response
- THEN its entry excludes the traceback and captured-output fields
- AND the full record remains reachable via that result's single-item endpoint

#### Scenario: The commit subject is bounded in list responses
- GIVEN a run whose recorded commit subject exceeds the list projection's display width
- WHEN that run appears in a list response
- THEN the commit subject in that entry is limited to a fixed, documented width smaller than the stored value, and the full stored subject remains reachable via that run's detail endpoint

#### Scenario: The truncation flag never surfaces independently of its subject
- GIVEN a run whose stored commit subject was truncated at capture time
- WHEN that run's commit subject appears in any response, list or detail
- THEN the truncation flag is present alongside it in that same response

#### Scenario: `vcs_root` appears in no run list or run detail response
- GIVEN a run recorded from a repository with a known `vcs_root`
- WHEN that run appears in a list response and is separately requested by its detail endpoint
- THEN the recorded `vcs_root` value does not appear in either response body

### Requirement: Bounded pagination

No list response MUST exceed 200 items, regardless of a caller-requested
page size. Every list response MUST report whether more items exist, and
that report MUST distinguish a page truncated by the cap or a smaller
requested size from a list that is genuinely exhausted.

**Verification: Test.**

#### Scenario: A list response never exceeds 200 items
- GIVEN more than 200 runs stored
- WHEN a run list page is requested without an explicit smaller page size
- THEN the response contains at most 200 items and reports more items exist

#### Scenario: The more-items flag distinguishes truncation from exhaustion
- GIVEN exactly 200 runs stored
- WHEN a page covering all 200 is requested
- THEN the response reports no more items exist
- AND when one additional run is then stored and the equivalent request repeated, the response reports more items exist

#### Scenario: A caller-requested page size under the cap is honored
- GIVEN more stored runs than a caller-requested page size under 200
- WHEN a list is requested with that page size
- THEN the response contains exactly that many items and reports more items exist

### Requirement: Test history latency

A test's history request MUST return within 100 ms at p95, server-side,
measured over a fixture of 500 runs and 100,000 results, and the slowest
single response observed during that measurement MUST be recorded alongside
the percentile.

**Verification: Analysis** — a percentile over a distribution, not an
assertion; a hard timing assertion is flaky across the 3.10–3.13 × xdist
matrix.

#### Scenario: p95 and max latency are measured and committed as numbers
- GIVEN a fixture of 500 runs and 100,000 results
- WHEN a test's history is requested repeatedly against that fixture
- THEN the 95th-percentile server-side response time and the slowest single response observed are both measured and committed to this spec as numbers

**Measurements** (`scripts/measure_history_latency.py`, 2026-08-21,
Linux-6.18.33.2-microsoft-standard-WSL2-x86_64, Python 3.10.21, git 2.55.0).
Fixture: 500 runs × 200 results (100,000 results), ~200 distinct node ids, a
target present in every run and a second present in exactly one. 5 warm-up
requests discarded, then 200 timed requests against `GET
/api/v1/tests/history`, server-side, in-process (no socket):

- **p95 (nearest-rank, n=200): 3.70 ms**
- **max (slowest single response): 11.59 ms**

Both are comfortably inside the 100 ms budget — 96.3 ms of headroom at p95 —
which supports D63's claim that the read path needs no new index: a ~500-row
scan over existing indexes costs low single-digit milliseconds, not a
meaningful fraction of the budget.

The same run also re-measured `measure_vcs_overhead.py`'s synthetic-
repository 10 ms profile (D63): **OFF = 11.062 s, ON = 11.160 s, delta =
97.7 ms (0.88 %)** against the 2 % budget of 221.2 ms, leaving **≈123.5 ms**
of headroom on this sitting's own arithmetic. That delta is 67 ms below the
164.8 ms recorded on 2026-08-20 (`version-control-context` spec.md, OFF
10.981 s / ON 11.146 s) — and this is **not** ordinary variance. A
`sdd-verify` round 1 re-run of the same benchmark, on this same machine but
a separate sitting, measured **OFF = 11.042 s, ON = 11.142 s, delta =
100.7 ms (0.91 %)**: within 3 ms of this sitting's 97.7 ms and 64 ms away
from the 2026-08-20 figure. Two independent sittings clustering that
tightly, both far from the 2026-08-20 number, look **systematic** —
different machine state, date, or kernel between sittings — rather than
symmetric run-to-run noise; the paragraph's own evidence agrees: `OFF` alone
moved 81 ms between the 2026-08-20 sitting and this one (10.981 s →
11.062 s), a shift comparable in size to the 67 ms change in the delta. This
does not overwrite D63's recorded 164.8 ms figure, and the conservative
reading survives regardless of which number is used: even at the archived
164.8 ms and its ≈55 ms headroom, the read path this change adds spends
**zero** of that budget, because `schema.sql` carries a zero-line diff from
before this change and no new index was added. A reader relying on this
paragraph MUST NOT treat 97.7 ms as interchangeable with 164.8 ms — they
come from different sittings, and the difference between them does not
average out.

A future change to the history query or its indexes MUST re-run
`scripts/measure_history_latency.py` and update this paragraph.

### Requirement: Single result detail

A single result MUST be reachable via a documented single-item endpoint
that returns its full record, including every field a list response
excludes — traceback, captured stdout, captured stderr, failure type,
message, path, line number and representation, and their truncation flags.
Requesting an identifier for a result that does not exist MUST be answered
without altering stored data.

**Verification: Test.**

#### Scenario: The full record is reachable for a given result
- GIVEN a failed result whose traceback and captured output were recorded
- WHEN that result is requested by its single-item endpoint
- THEN the response carries its traceback, captured stdout, captured stderr, failure type, message, path, line number and representation

#### Scenario: A bounded field's truncation flag travels with it on the single-item endpoint
- GIVEN a result whose traceback was truncated at capture time
- WHEN that result is requested by its single-item endpoint
- THEN the truncation flag is present alongside the traceback in that response

#### Scenario: An unknown result identifier leaves stored data unchanged
- GIVEN a result identifier that does not exist
- WHEN it is requested by the single-item endpoint
- THEN the request is answered without creating, altering or removing any row
