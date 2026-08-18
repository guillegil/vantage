# Requirements — full Notion export

> **FROZEN. NOT A WORKING DOCUMENT. SCHEDULED FOR DELETION.**
> See `README.md` in this directory before reading a single line of this file.
> Requirements are authored in OpenSpec from now on. This file is a snapshot
> taken on 2026-08-18 so that the thinking behind `RQ-1`…`RQ-44` is not lost
> while it is migrated. Nothing here is authoritative and nothing here is
> maintained.

All 43 requirements as they stood on 2026-08-18. The identifiers run `RQ-1` to
`RQ-44`; **there is no RQ-43**. Every one was `Draft` at export time.

---

## RQ-1 · Run entry

*Must Have · Functional · Event-driven · Test · Draft*

> When a pytest session starts with recording enabled, the system shall create exactly one run entry whose identifier is unique across every run in the database.

**Acceptance criteria**

1. Given an empty database, when pytest is invoked once with recording enabled, then the run table contains exactly one row.
2. Given a database already holding one run, when pytest is invoked again, then the run table contains two rows and their identifiers differ.
3. Given a directory containing no test files, when pytest is invoked with recording enabled, then the run table contains exactly one row.
4. Given a test file that raises ImportError at collection, when pytest is invoked with recording enabled, then the run table contains exactly one row.
5. Given a session still running with recording enabled, when the database is queried before that session ends, then its run entry already exists, holding a start time and a null end time.
6. Given a session whose process is killed with SIGKILL while running, when the database is inspected afterwards, then its run entry is present with a start time and a null end time.

**Rationale**

The run is the unit of history and everything else hangs from it. "Exactly one" is load-bearing: under pytest-xdist the temptation is one entry per worker, which would silently multiply every later count. The zero-test and failed-collection criteria are there because a run that ended badly is exactly the run someone later wants to find.

Amended 2026-08-16. Criteria 1 to 4 all count rows AFTER the session ends, so they are satisfied whether the entry is created when the session starts or when it finishes — and Milestone 1 shipped the second reading, creating the entry at pytest_sessionfinish. The statement's EARS trigger has always said "when a pytest session starts". Criteria 5 and 6 are what make that trigger testable: 5 observes the entry mid-session, 6 covers the case the drift makes visible, a process killed with SIGKILL leaving no entry at all. A criterion that passes under both readings of its own trigger was never verifying the trigger.

---

## RQ-2 · Opt-in recording

*Must Have · Functional · Optional feature · Test · Draft*

> Where no recording option is present in the pytest invocation, the system shall attempt no connection to the server.

**Acceptance criteria**

1. Given a project with the plugin installed and no recording option, when pytest is run with outbound connections logged, then no connection is attempted.
2. Given a project with the plugin installed and no recording option, when pytest is run once normally and once with -p no:vantage, then the two resulting project trees are byte-for-byte identical.
3. Given a project with the plugin installed, no recording option, and no server running at all, when pytest is run, then it exits with the status it would have had without the plugin and emits no warning.

**Rationale**

A plugin that reports without being asked is a plugin that gets uninstalled on day one — and once recording travels over a network rather than into a file, the thing to guarantee is silence on the wire, not an unchanged directory. Criterion 2 survives from the previous wording as defence in depth: the plugin should still leave nothing behind on disk, even though it no longer opens a database. Criterion 3 covers the case that would otherwise be discovered by a user — an unconfigured plugin must not complain about a server it was never asked to reach.

---

## RQ-3 · Single write transaction

*Must Have · Functional · Event-driven · Test · Draft*

> When a pytest session finishes, the system shall make that session's results observable either in full or not at all, never partially.

**Acceptance criteria**

1. Given a session of 500 tests whose report reaches the server, when the server is killed with SIGKILL midway through writing it, then the database afterwards holds either all 500 results of that session or none of them.
2. Given a session of 500 tests, when its report is truncated in transit, then the database holds none of that session's results rather than a prefix of them.
3. Given a session of 500 tests that is reported and written normally, when the database is queried afterwards, then all 500 results of that session are present.

**Rationale**

Atomicity, not transaction count. An earlier wording required "a single database transaction", which is a mechanism rather than an obligation and would become false the day the storage adapter changes. The need is that no reader ever sees half a session.

The obligation now sits with the server, which performs every write, and criterion 2 is new because the failure mode is: a report can now be cut off mid-flight, which a local file write never was. A partially received session must be discarded rather than stored, because half a session recorded as if whole is worse than a session missing.

---

## RQ-4 · Outcome across phases

*Must Have · Functional · Ubiquitous · Test · Draft*

> The system shall record for each test an outcome that reflects every execution phase that test produced.

**Acceptance criteria**

1. Given a test whose fixture raises before the test body runs, when the session is recorded, then its outcome is error.
2. Given a test decorated with @pytest.mark.skip, when the session is recorded, then its outcome is skipped.
3. Given a test decorated with @pytest.mark.xfail that fails, when the session is recorded, then its outcome is xfailed.
4. Given a test decorated with @pytest.mark.xfail that passes, when the session is recorded, then its outcome is xpassed.
5. Given a test that passes but whose teardown raises, when the session is recorded, then its outcome is not passed.

**Rationale**

Setup failures and skips emit no call phase; deriving the outcome from the call phase alone loses them silently. Criterion 5 covers the mirror case — a teardown error after a passing call — which is the one most implementations get wrong, because the call phase has already reported success.

---

## RQ-5 · Per-phase duration

*Should Have · Functional · Ubiquitous · Test · Draft*

> The system shall record the duration of the setup, call and teardown phases separately.

**Acceptance criteria**

1. Given a test whose fixture sleeps 8 seconds and whose body sleeps 0.1 seconds, when the result is recorded, then its setup duration is at least 8 seconds and its call duration is below 1 second.
2. Given a test that fails during setup and therefore produces no call phase, when the result is recorded, then its call duration is null rather than zero.

**Rationale**

Criterion 2 settles the question the happy path hides: a null call duration means "never ran", a zero means "ran instantly". Recording zero for a test that never executed corrupts every later aggregate over durations.

---

## RQ-6 · Parameter values

*Must Have · Functional · Optional feature · Test · Draft*

> Where a test is parametrised, the system shall record for each parameter a textual representation of the supplied value that distinguishes it from any other value used for that parameter in the same run.

**Acceptance criteria**

1. Given a test parametrised with the objects Point(1, 2) and Point(3, 4), when the results are recorded, then the two stored parameter representations differ from each other.
2. Given a test parametrised with two objects of the same class whose identifiers pytest renders as param0 and param1, when the results are recorded, then the stored representations differ even though the identifiers carry no value information.

**Rationale**

pytest identifies complex parameter values positionally, so param0 silently reattributes history to a different value the moment the parameter list is reordered. "Distinguishes it from any other value" is the testable form of the need; the original wording said "the representation of each value", which does not say what makes a representation adequate.

---

## RQ-7 · Markers

*Should Have · Functional · Ubiquitous · Test · Draft*

> The system shall record every marker applied to a test together with the scope at which that marker was declared.

**Acceptance criteria**

1. Given a test carrying @pytest.mark.slow inside a class carrying @pytest.mark.integration, when the result is recorded, then both markers are present, slow is attributed to the function and integration to the class.
2. Given a module declaring pytestmark = pytest.mark.serial and a test inside it carrying no marker of its own, when the result is recorded, then serial is present and attributed to the module.
3. Given a test carrying @pytest.mark.timeout(30), when the result is recorded, then the marker's argument 30 is recorded alongside its name.

**Rationale**

Scope is what makes the record useful rather than merely present: filtering for tests that are individually marked slow gives a different answer from filtering for tests that inherit it from a module, and only one of those two questions can be answered if the origin is discarded. Criterion 3 covers marker arguments, which the original statement did not mention at all.

---

## RQ-8 · Failure location

*Must Have · Functional · Unwanted behaviour · Test · Draft*

> If a test fails, then the system shall record the file path, line number and message of the failure location as separately queryable values.

**Acceptance criteria**

1. Given twenty tests failing at the same source line, when results are queried by failure path and line, then all twenty are returned as one group.
2. Given a test that fails inside a helper function called from the test body, when the result is recorded, then the recorded path and line are those of the assertion that raised, not those of the test function's first line.
3. Given a test that fails during teardown after its body passed, when the result is recorded, then the recorded failure location is the teardown site.

**Rationale**

Storing the failure location as text prevents the grouping in criterion 1, which is the whole point — twenty failures at one line are one bug, and a report that lists them twenty times is noise. Criterion 2 fixes the ambiguity in "the failure location": pytest's report offers several, and the useful one is where the exception was raised.

---

## RQ-9 · Decomposed identity

*Must Have · Functional · Ubiquitous · Test · Draft*

> The system shall record each test's file path, class name, function name and parameter identifier as separately queryable values.

**Acceptance criteria**

1. Given a suite spanning several files and classes, when results are filtered by file path alone, then every test defined in that file is returned.
2. Given a test defined at module level rather than inside a class, when the result is recorded, then its class name is null rather than an empty string.
3. Given a test that is not parametrised, when the result is recorded, then its parameter identifier is null.

**Rationale**

Storing the node identifier as one opaque string prevents grouping and filtering, and forces a schema migration the day reconciliation heuristics are added. Criteria 2 and 3 fix the distinction between "absent" and "empty", which decides whether a query for tests outside classes can be written at all.

---

## RQ-10 · Version-control context

*Must Have · Data and integration · Optional feature · Test · Draft*

> Where the project is a git repository, the system shall record for each run the commit hash, the branch name, the first line of the commit message, and whether the working tree held uncommitted changes.

**Acceptance criteria**

1. Given a repository with uncommitted changes to a tracked file, when a session is recorded, then the run is marked as having a dirty working tree.
2. Given a repository whose working tree is clean, when a session is recorded, then the run is not marked dirty and its commit hash matches git rev-parse HEAD.
3. Given a repository in detached HEAD state, when a session is recorded, then the commit hash is recorded and the branch name is null.
4. Given a repository holding no commits yet, when a session is recorded, then the run is stored and its commit hash is null.

**Rationale**

A result obtained from a dirty working tree cannot be reproduced by anyone, including the person who produced it; without the flag the history cannot be trusted. Reclassified from Functional to Data and integration, because the failure mode to watch for is the one this type always has — what happens when the other side does not answer. Criteria 3 and 4 are those cases: detached HEAD has no branch, a fresh repository has no commit.

---

## RQ-11 · Machine context

*Should Have · Functional · Ubiquitous · Inspection · Draft*

> The system shall record for each run the host name, the user name, the Python version, the pytest version, the platform and the command line that started it.

**Acceptance criteria**

1. Given a recorded run, when its entry is inspected, then all six fields are populated and none is an empty string.
2. Given a run started as pytest -k slow --vantage-db=v.db, when its entry is inspected, then the recorded command line contains all three arguments in the order given.

**Rationale**

These six answer "was it just me, or does it fail everywhere" — the first question asked of any result that disagrees with someone else's. Note that the command line may contain secrets passed as arguments; see the separate requirement covering redaction.

---

## RQ-12 · Distributed execution

*Must Have · Functional · State-driven · Test · Draft*

> While pytest-xdist is active, the system shall record each test result exactly once.

**Acceptance criteria**

1. Given a suite of six tests run with two xdist workers, when the session is recorded, then the result count is six.
2. Given the same suite of six tests run without xdist installed, when the session is recorded, then the result count is also six.
3. Given a suite of six tests run with two xdist workers, when the session is recorded, then exactly one run entry exists.

**Rationale**

Under xdist every result is emitted twice, once by the worker that ran it and once by the controller that collected it. Criterion 2 is the control: a de-duplication filter that is too aggressive halves the count when xdist is absent, and only comparing the two invocations catches that. Criterion 3 guards the run entry against the same doubling.

---

## RQ-13 · Catalogue retention

*Should Have · Functional · Ubiquitous · Test · Draft*

> The system shall retain a test's catalogue entry after that test is removed from the codebase, together with the timestamp at which it was last observed.

**Acceptance criteria**

1. Given a recorded test, when it is deleted from the codebase and the suite is run again, then its catalogue entry remains and its last-observed timestamp is unchanged.
2. Given a catalogue entry for a test deleted three runs ago, when a test with the same identifier is added back and the suite is run, then the same catalogue entry is reused and its last-observed timestamp advances to the new run.

**Rationale**

A test disappearing is information, not garbage — "when did this stop being run" is a question the history can only answer if the entry survives. Criterion 2 fixes what happens on return: reusing the entry preserves the history across the gap, whereas creating a second entry splits one test's life into two and loses the connection.

---

## RQ-14 · Read-only API

*Must Have · Functional · Ubiquitous · Test · Draft*

> The system shall expose an HTTP API every endpoint of which leaves stored data unchanged.

**Acceptance criteria**

1. Given a database holding recorded runs, when every documented endpoint is called in turn, then the database file is byte-identical to what it was before.
2. Given a running service, when the machine-readable interface document is requested, then it is returned and every path it declares responds with a 2xx status.

**Rationale**

"Read-only" stated as an observable property — the bytes do not change — rather than as an intention, so criterion 1 can assert it directly against any future endpoint. The interface document is what makes criterion 1 enumerable: without it, "every endpoint" is whatever someone remembers to test.

---

## RQ-15 · Test history endpoint

*Must Have · Functional · Event-driven · Test · Draft*

> When a client requests the history of a test, the system shall return that test's executions newest first, each carrying the commit it ran on and its duration, up to the response limit of RQ-17.

**Acceptance criteria**

1. Given a test with 12 recorded executions, when its history is requested, then the response lists all 12 newest first, and each entry carries a commit hash and a duration.
2. Given a test recorded once from a directory that is not a git repository, when its history is requested, then the entry is returned with a null commit rather than being omitted.
3. Given an identifier matching no recorded test, when its history is requested, then the response is an empty history rather than an error.

**Rationale**

This is the endpoint the whole product exists to serve. Two changes from the original. "Most recent executions" was an unquantified plural — how many was left to the reader — and is now tied to the single pagination limit in RQ-17 rather than inventing a second one. The original acceptance criterion also carried a 100 ms latency target, which is a non-functional obligation in a functional requirement's clothing: it had no percentile, no measurement point and no stated corpus beyond "500 runs". It has been lifted out; see the proposed latency NFR.

---

## RQ-16 · Lean list responses

*Should Have · Functional · Ubiquitous · Test · Draft*

> The system shall serve every list endpoint with a response carrying only bounded-size fields, excluding the traceback and captured-output fields.

**Acceptance criteria**

1. Given 500 recorded results each carrying a 40 KB traceback, when the result list is requested, then the response is below 500 KB.
2. Given one of those results, when that single result is requested by identifier, then the response carries its full traceback.

**Rationale**

Criterion 2 is the complement and matters as much as criterion 1: excluding a field from lists is only correct if it remains reachable somewhere, otherwise the requirement has quietly deleted data from the API. The original said "long text fields", which named no field and no threshold — two readers would exclude different columns.

---

## RQ-17 · Bounded pagination

*Should Have · Functional · Ubiquitous · Test · Draft*

> The system shall return no more than 200 items in any single list-endpoint response, and shall indicate in the response whether further items exist.

**Acceptance criteria**

1. Given 5,000 recorded runs, when a client requests 100,000 items from the run list, then the response contains exactly 200 items.
2. Given 5,000 recorded runs, when a client requests the run list, then the response indicates that further items exist.
3. Given 40 recorded runs, when a client requests the run list, then the response contains 40 items and indicates that no further items exist.

**Rationale**

The original stated "a server-side maximum number of items" without naming the number, so it could never be checked — the acceptance criterion referred back to "the configured maximum", which is circular. 200 is an assumption, not measured: it is set where it stops changing a decision, being comfortably above a screenful and comfortably below a response size that needs streaming. Revisit once real response sizes exist. The second clause is the boundary behaviour a bare ceiling always omits: a client that cannot tell truncation from exhaustion will silently show partial data as if it were complete.

---

## RQ-18 · Run list

*Should Have · Functional · Event-driven · Demonstration · Draft*

> When a user opens the run list, the system shall display the recorded runs newest first by start time.

**Acceptance criteria**

1. Given five recorded runs with distinct start times, when the run list is opened, then the five appear in descending order of start time.
2. Given a database holding no runs, when the run list is opened, then an empty-state message is displayed rather than an error.

**Rationale**

Opening the list is a moment, so the pattern is event-driven rather than ubiquitous — the original phrasing hid the trigger and left it unclear whether the ordering was a property of the view or of the stored data. "Newest first by start time" names which of the several timestamps orders the list.

---

## RQ-19 · Test history view

*Must Have · Functional · Event-driven · Demonstration · Draft*

> When a user opens a test's history view, the system shall display that test's outcome for each recorded execution in chronological order, with each execution's commit reachable from the view.

**Acceptance criteria**

1. Given a test that passed in ten consecutive runs and then failed, when its history view is opened, then the first failing execution is identifiable and its commit is reachable from the view.
2. Given a test with one recorded execution, when its history view is opened, then that single execution is displayed rather than an empty view.

**Rationale**

This view is the acceptance criterion of the whole MVP. The original also required marking executions recorded from a dirty working tree; that is a second obligation with its own priority and has been proposed as a separate requirement, so that shipping the history without the dirty marker cannot be recorded as this requirement being half-met.

---

## RQ-20 · Self-contained interface

*Should Have · Functional · Ubiquitous · Demonstration · Draft*

> The system shall serve the web interface entirely from assets contained in the installed distribution.

**Acceptance criteria**

1. Given a machine with no Node.js installed, when the package is installed and the service started, then the interface loads and renders the run list.
2. Given the service running, when the interface is loaded, then it issues no request to any host other than the local service.

**Rationale**

Stated positively — what the system serves from — rather than as "without requiring a JavaScript runtime", which described a thing that must not be needed rather than a property anyone can inspect. Criterion 2 closes the other half of self-containment: an interface that loads a font or a script from a CDN also fails RQ-28, and the Node.js check alone would not catch it.

---

## RQ-21 · Non-disruptive failure

*Must Have · Functional · Unwanted behaviour · Test · Draft*

> If the system raises an internal error while recording, then the system shall emit a warning and shall let the pytest session terminate with the exit status it would have had otherwise.

**Acceptance criteria**

1. Given a reporting path patched to raise, when a suite of passing tests is run, then pytest exits with status 0 and emits one warning.
2. Given a reporting path patched to raise, when a suite containing one failing test is run, then pytest exits with status 1 and emits one warning.
3. Given a server that accepts the connection and then closes it without responding, when a suite of passing tests is run, then pytest exits with status 0 and emits one warning.
4. Given a server that accepts the connection and never responds, when a suite of passing tests is run, then pytest exits with status 0 within the configured timeout plus five seconds.
5. Given a reporting path patched to raise on every hook it implements, when a suite of passing tests is run, then pytest exits with status 0 and the session reports no internal error.

**Rationale**

Vantage is an observability tool. If it breaks somebody's suite it is uninstalled the same day and never reinstalled.

Criterion 2 is the one that matters most and is easy to omit: preserving exit status 0 happens by accident, whereas preserving a non-zero status is where a naive error boundary swallows the failure and reports success.

Criteria 3 and 4 are new, and they exist because reporting now crosses a network. A file write fails immediately; a socket can accept and then hang, which turns a fault-tolerance requirement into a hang — the worst outcome of all, because the user cannot tell whether their suite is slow or stuck. A bounded timeout is therefore part of the obligation, not an implementation detail. Criterion 5 covers the hook whose uncaught exceptions pytest turns into its own INTERNALERROR with exit status 3.

---

## RQ-22 · Bounded text fields

*Should Have · Functional · Ubiquitous · Test · Draft*

> The system shall truncate each stored text field to at most 64 KiB and shall mark within the stored value that truncation occurred.

**Acceptance criteria**

1. Given a test failing with a 10 MB traceback, when the result is recorded, then the stored field is at most 64 KiB and ends with a truncation marker.
2. Given a test failing with a 1 KiB traceback, when the result is recorded, then the stored field holds the traceback in full and carries no truncation marker.
3. Given a stored field that was truncated, when it is read back, then the truncation marker is distinguishable from text the traceback itself contained.

**Rationale**

The original said "a configurable maximum size" with no default, so nothing could be checked — configurability is a mechanism, and the requirement still owes a number. 64 KiB is an assumption, not measured: it is roughly two hundred traceback lines, comfortably past any traceback anyone reads, and small enough that a thousand of them stay under 64 MB. Revisit once real tracebacks have been recorded. Criterion 2 is the boundary from the other side — a truncation marker appended to untruncated text is the defect this pairing catches. Note that this limit governs the traceback referred to by RQ-8; the two requirements previously contradicted each other, RQ-8 requiring a complete traceback while this one truncated every text field.

---

## RQ-23 · Absent version control

*Should Have · Functional · Optional feature · Test · Draft*

> Where the project directory is not a git repository, the system shall record the run with null version-control fields.

**Acceptance criteria**

1. Given a directory that is not a git repository, when a session is recorded, then the run is stored and its commit hash, branch name, commit message and dirty flag are all null.
2. Given a directory that is not a git repository, when the run list is requested, then that run appears in it alongside runs recorded from repositories.

**Rationale**

"Empty" was ambiguous between null and the empty string, and the difference decides whether "runs with no version control" can be queried at all — an empty string is indistinguishable from a branch whose name failed to read. Criterion 2 guards against the obvious wrong implementation, which is to skip recording the run entirely when git is absent.

---

## RQ-24 · Zero runtime dependencies

*Must Have · Non-functional · Optional feature · Test · Draft*

> Where pytest is already present in the environment, installing the plugin package shall add no distribution other than the plugin package itself.

**Acceptance criteria**

1. Given a virtual environment with pytest installed, when pytest-vantage is installed, then exactly one distribution is added to the environment.
2. Given the plugin package's source, when its imports are analysed, then every one resolves either to the Python standard library or to pytest.
3. Given the plugin package's metadata, when its declared dependencies are read, then pytest is the only one.

**Rationale**

A pytest plugin lives in someone else's test environment, beside that project's real dependencies, competing with them for a single resolution. Every package it brings is a version conflict waiting for the wrong week — and the conflict arrives because the plugin pins correctly, not despite it.

Corrected 2026-08-15. A previous wording said "exactly one distribution" against a clean environment, which is false: a plugin declares pytest, so a clean environment gains two. The condition is now stated where it belongs — pytest is already present, by definition, in any environment where a pytest plugin is being installed. Nobody installs one without it.

The requirement is otherwise stronger than it was. It previously permitted "only distributions published by this project", plural, because the plugin then imported core and storage code and legitimately pulled them in. Under ADR-9 it reports over HTTP and depends on nothing but the standard library and the runner it extends, so the rule can be checked by counting to one.

---

## RQ-25 · Runtime overhead

*Should Have · Non-functional · Ubiquitous · Analysis · Draft*

> The system shall add no more than 2 percent to the wall-clock duration of a suite of 1,000 tests each taking approximately 10 milliseconds, reporting to a server on the same machine.

**Acceptance criteria**

1. Given a suite of 1,000 tests each taking approximately 10 milliseconds and a server on the same machine, when the suite is run five times with recording and five times without, then the median difference in total wall-clock duration is at or below 2 percent.
2. Given the same suite, when it is run with recording, then the number of requests sent to the server is independent of the test count.
3. Given a suite of 1,000 tests each taking approximately 1 millisecond, when the same comparison is run, then the absolute overhead added per test is recorded, whether or not the 2 percent holds.

**Rationale**

The figure is meaningless without the input profile: a suite of trivial tests and one of integration tests differ by orders of magnitude, so the same implementation passes against one and fails against the other.

Criterion 2 is the one that changed under ADR-9, and it is the one that decides whether this requirement is achievable at all. Reporting now crosses a network. A request per test would put a round trip in the inner loop of somebody's suite and no budget would survive it; the session must be batched and sent once. Stating it as a criterion rather than leaving it to the design keeps the obligation observable — you can count requests — without prescribing how the batching works.

"On the same machine" is stated because it is the Phase 1 deployment and because a remote server is a different measurement entirely, governed by a network nobody here controls. That number, when it matters, is a separate requirement.

A median over paired runs is used deliberately rather than a percentile: the measurement is one total duration per run, not a distribution of request latencies, so there is no tail for a percentile to describe. Criterion 3 records the number FT-7 flags as the real risk — on very fast suites a fixed per-session cost dominates the percentage.

---

## RQ-26 · Core isolation

*Must Have · Non-functional · Ubiquitous · Test · Draft*

> The core package shall import only modules from the Python standard library.

**Acceptance criteria**

1. Given the core package, when every import statement in it is resolved by static analysis, then each one resolves to a Python standard-library module.
2. Given the static analysis of criterion 1, when it runs, then it reports having examined at least one module.

**Rationale**

Stated as what the core may import rather than what it may not, so the check enumerates a closed set instead of trying to prove the absence of an open one — a forbidden list of "pytest, database or web-framework module" also silently permits every third-party package nobody thought to name. Criterion 2 exists because the check passes vacuously the day someone moves the package and forgets to update the path, and a green test that examined nothing is worse than no test.

---

## RQ-27 · Supported runtimes

*Must Have · Non-functional · Ubiquitous · Test · Draft*

> The system shall pass its own test suite on Python 3.10, 3.11, 3.12 and 3.13, both with and without pytest-xdist installed.

**Acceptance criteria**

1. Given the continuous integration matrix, when it runs, then all eight combinations of the four Python versions and the two xdist configurations pass.
2. Given a Python 3.9 environment, when the plugin package is installed, then installation is refused by the declared version floor rather than failing at import time.

**Rationale**

"Operate on" was unmeasurable — operate meaning what, and observed how? Passing its own suite is the observation that makes the claim checkable. Criterion 2 covers the boundary below the range: a package with no floor installs happily on 3.9 and fails with a syntax error the first time it is imported, which is a far worse experience than a refused install.

---

## RQ-28 · Offline operation

*Must Have · Non-functional · Ubiquitous · Test · Draft*

> The system shall complete every operation using only resources on the local machine.

**Acceptance criteria**

1. Given a machine with networking disabled, when a suite is recorded and the interface is opened, then both succeed.
2. Given the system running with outbound connections logged, when a suite is recorded and the interface is opened, then no outbound connection is attempted.

**Rationale**

This is a privacy promise as much as a portability one — Vantage records commit messages, file paths and command lines, and "it never leaves your machine" has to be enforced rather than asserted. Criterion 2 is the stronger form: a disabled network proves the system survives without one, but only observing the attempts proves it never wanted one.

---

## RQ-29 · Complete schema from first use

*Should Have · Non-functional · Ubiquitous · Inspection · Draft*

> The system shall create its complete database schema when a database is first created, including columns that no code populates until a later phase, so that no Phase 1 release alters the schema of an existing database.

**Acceptance criteria**

1. Given a freshly created database, when its schema is compared against the documented column manifest, then every documented column exists.
2. Given a database created by an earlier Phase 1 release, when a later Phase 1 release opens it, then the system issues no schema-altering statement.

**Rationale**

Deferring a feature is cheap; migrating a schema after users hold data is not. The statement now carries the obligation the schema serves — no Phase 1 release alters an existing database — rather than only the mechanism, so criterion 2 can check the thing anyone actually cares about. Without it the requirement described a shape rather than a promise, and a release could add a column while still satisfying "created in full at first use".

---

## RQ-30 · Replaceable storage

*Should Have · Non-functional · Ubiquitous · Test · Draft*

> The system shall support replacing the storage adapter by providing an alternative implementation of the storage port, without modifying the core package.

**Acceptance criteria**

1. Given an in-memory implementation of the storage port, when the core test suite is run against it, then it passes unchanged.
2. Given the in-memory implementation of criterion 1, when the core package is inspected, then it contains no import of either storage implementation.

**Rationale**

Maintainability expressed as a change that either does or does not require touching a given file, which is the only form of it a test can settle. Criterion 2 closes the loophole in criterion 1: a core that imports both implementations and branches between them also passes the suite, while being exactly the coupling the requirement exists to prevent.

---

## RQ-31 · Run timestamps

*Must Have · Functional · Ubiquitous · Test · Draft*

> The system shall record on each run entry the time its session started and the time its session ended.

**Acceptance criteria**

1. Given a session that runs for at least two seconds, when it completes, then its run entry holds a start time and an end time, and the end time is later than the start time.
2. Given a session interrupted with Ctrl-C, when the database is inspected afterwards, then its run entry holds a start time and a null end time.
3. Given a session whose process is killed with SIGKILL, when the database is inspected afterwards, then its run entry holds a start time and a null end time, and carries no interrupt reason.

**Rationale**

Split out of RQ-1, which previously carried both the identity and the timing obligations in one sentence and could therefore never be partially accepted. Kept as one requirement rather than two because a run entry with a start but no defined treatment of its end is not a useful half. Criterion 2 is the reason the pair belongs together: a null end time is what distinguishes "still running or killed" from "finished", and a requirement covering only the start would leave that distinction unwritten.

Amended 2026-08-16, on the limit of what a reason can ever say. A reason exists only for interruptions Python is given the chance to observe. Ctrl-C raises KeyboardInterrupt, pytest's wrap_session calls pytest_sessionfinish from a finally block, and the plugin gets to say what happened — that is criterion 2. SIGKILL cannot be caught, blocked or handled: the process stops between two instructions and no code of ours runs. Criterion 3 therefore asserts the ABSENCE of a reason rather than a value for it. The distinction between "finished" and "did not" survives a kill; the distinction between one kind of death and another does not.

---

## RQ-32 · Traceback capture

*Must Have · Functional · Unwanted behaviour · Test · Draft*

> If a test fails, then the system shall record the traceback of the failure.

**Acceptance criteria**

1. Given a test failing with an assertion three call frames deep, when the result is recorded, then the stored traceback names all three frames.
2. Given a test failing with a 10 MB traceback, when the result is recorded, then the stored traceback is truncated to the limit set by RQ-22 and carries its truncation marker.

**Rationale**

Split out of RQ-8, which previously required the failure location and the complete traceback in one sentence. Separating them also resolves a contradiction: RQ-8 demanded a complete traceback while RQ-22 truncates every stored text field, so the two requirements could not both be satisfied. Criterion 2 now states which one wins.

---

## RQ-33 · Test history latency

*Should Have · Non-functional · Ubiquitous · Analysis · Draft*

> The system shall return a test's history within 100 ms at p95, measured server-side from request receipt to last byte sent, for a database holding 500 runs and 100,000 recorded results.

**Acceptance criteria**

1. Given a database holding 500 runs and 100,000 results, when one test's history is requested 200 times, then the 95th percentile of server-side response time is at or below 100 ms.
2. Given the same database, when one test's history is requested 200 times, then the slowest single response is recorded, whether or not it is within budget.

**Rationale**

Lifted out of RQ-15, where it lived inside a functional requirement's acceptance criterion as a bare "under 100 ms" — no percentile, no measurement point and no corpus beyond the run count, so it was met and unmet at the same time depending on who measured. 100 ms is an assumption, not measured: it is the classic threshold at which an interaction feels instant, and this is the endpoint the whole product exists to serve, so the tighter of the defensible numbers is the right starting point. Revisit once real databases exist.

---

## RQ-34 · Dirty working tree marking

*Should Have · Functional · Event-driven · Demonstration · Draft*

> When a user opens a test's history view, the system shall mark each execution that was recorded from a dirty working tree.

**Acceptance criteria**

1. Given a test with one execution recorded from a clean tree and one from a dirty tree, when its history view is opened, then the dirty execution is marked and the clean one is not.
2. Given a test with one execution recorded outside any git repository, when its history view is opened, then that execution is not marked dirty.

**Rationale**

Split out of RQ-19, which carried three obligations — display the history, link the commits, mark the dirty executions — and could therefore never be partially accepted. A result from a dirty working tree cannot be reproduced by anyone, including the person who produced it, so an unmarked one silently invites a conclusion the evidence does not support. Criterion 2 fixes the case where dirtiness is unknown rather than false: absent version control is not the same as a clean tree, and marking it clean would be a lie.

---

## RQ-35 · Command-line redaction

*Should Have · Non-functional · Unwanted behaviour · Test · Draft*

> If a recorded command-line argument supplies a value to an option whose name contains password, token, secret or key, then the system shall store that value replaced by a redaction marker.

**Acceptance criteria**

1. Given a session started as pytest --api-token=abc123, when the run entry is inspected, then the recorded command line contains the option name and a redaction marker, and does not contain abc123.
2. Given a session started as pytest -k slow, when the run entry is inspected, then the recorded command line contains slow unredacted.

**Rationale**

RQ-11 records the command line of every run, and a command line routinely carries credentials passed as arguments. Recording them verbatim turns an observability database into a secret store, and Vantage's whole privacy position — RQ-28, nothing leaves the machine — is worth nothing if the file left behind is the leak. Criterion 2 bounds the redaction: a rule that redacts too eagerly makes the recorded command line useless for the question it exists to answer.

---

## RQ-36 · Interface document

*Must Have · Functional · Ubiquitous · Test · Draft*

> The system shall publish a machine-readable document describing every endpoint of its HTTP API.

**Acceptance criteria**

1. Given a running service, when the interface document is requested, then it is returned and parses as a valid document of its declared format.
2. Given the interface document, when every path it declares is requested in turn, then each responds with a 2xx status.
3. Given a service exposing an endpoint absent from the interface document, when the document is compared against the served routes, then the discrepancy is reported.

**Rationale**

Split out of RQ-14, which bundled the read-only obligation with the existence of the document. The document is what makes RQ-14's own criterion enumerable: "every endpoint leaves stored data unchanged" is only checkable if something declares what every endpoint is. Criterion 3 is the one that keeps it honest over time — a document that drifts from the routes is worse than none, because it is trusted.

---

## RQ-37 · Unreachable server

*Must Have · Functional · Unwanted behaviour · Test · Draft*

> If the configured server cannot be reached, then the system shall emit a warning naming that server and shall let the pytest session run to completion unrecorded.

**Acceptance criteria**

1. Given a configured server address where nothing is listening, when a suite of passing tests is run, then pytest exits with status 0 and emits one warning naming the address.
2. Given a configured server address whose host does not resolve, when a suite of passing tests is run, then pytest exits with status 0 and emits one warning naming the address.
3. Given a server that becomes unreachable after the session has started but before the report is sent, when the suite finishes, then pytest exits with the status it would have had and emits one warning.
4. Given a configured server that is unreachable, when a suite of 200 tests is run, then exactly one warning is emitted rather than one per test.

**Rationale**

The commonest failure in ordinary use once recording crosses a network, and the first thing a new user will hit — they install the plugin, configure an address, and have not started the server yet.

Naming the address in the warning is the difference between fixing a typo in seconds and filing a bug: "could not reach the server" without the address sends someone looking in the wrong place. Criterion 4 exists because a per-test warning turns a small misconfiguration into hundreds of lines of noise, which trains people to ignore warnings — including the ones that matter.

This requirement previously read "if the configured database path cannot be opened for writing". Under ADR-9 the plugin never opens a database; the equivalent failure is now the server being unreachable, and the intent — warn, name the target, do not break the suite — carries over unchanged.

---

## RQ-38 · Concurrent sessions

*Should Have · Functional · State-driven · Test · Draft*

> While more than one pytest session is reporting to the server concurrently, the system shall record every one of those sessions' run entries.

**Acceptance criteria**

1. Given two pytest sessions started within the same second against one server, when both complete, then the database holds two run entries with different identifiers.
2. Given two pytest sessions of 200 tests each started within the same second against one server, when both complete, then the database holds 400 results.
3. Given ten pytest sessions reporting simultaneously, when all complete, then the database holds ten run entries and no session receives an error response.

**Rationale**

Two terminals, one repository, or a CI matrix reporting from several jobs at once, is ordinary use rather than an edge case.

Under ADR-9 this is now an obligation of the server, not of the plugin. That is a genuine simplification: the plugin no longer competes for a database lock, and the whole apparatus the previous design carried for it — WAL, BEGIN IMMEDIATE, a busy timeout, a network-filesystem fallback — belongs to whatever storage the server chooses, behind its port.

What has not gone away is the failure it guards against. Silently losing the second session is still the worst outcome and still the easiest to ship by accident, because the losing session exits 0 and its user sees nothing. Criterion 3 raises the count past two, since a lock held under real concurrency behaves differently from one contended by exactly two writers.

---

## RQ-39 · Unreadable version control

*Should Have · Data and integration · Unwanted behaviour · Test · Draft*

> If the project is a git repository whose version-control information cannot be read, then the system shall record the run with null version-control fields.

**Acceptance criteria**

1. Given a directory containing a .git entry that is not a valid repository, when a session is recorded, then the run is stored with null commit hash, branch name, commit message and dirty flag.
2. Given a git repository and no git executable on PATH, when a session is recorded, then the run is stored with null version-control fields.
3. Given a git repository whose version-control information cannot be read, when a session is recorded, then pytest exits with the status it would have had otherwise.

**Rationale**

RQ-10 covers a readable repository and RQ-23 covers no repository at all. The case between them — a repository that is present but unreadable, through a corrupt index, a missing git binary, or a permissions problem — had no requirement, and it is the failure mode the guide names as the one data and integration requirements always omit: what happens when the other side does not answer. Criterion 3 states the part that matters most, which is that an unreadable repository must not become a broken test run.

---

## RQ-40 · Owner-only store permissions

*Should Have · Non-functional · Ubiquitous · Test · Draft*

> The system shall create the database file and the artefact store readable and writable only by the user account that created them.

**Acceptance criteria**

1. Given a POSIX machine with a permissive umask of 022, when a database is created, then its mode is 0600.
2. Given a POSIX machine with a permissive umask of 022, when the artefact store directory is created, then its mode is 0700.
3. Given an existing database whose mode is 0644, when a session records to it, then the run is recorded and a warning names the permissive mode.

**Rationale**

The database holds command lines, absolute file paths, branch names, commit messages, host names and user names. On a shared machine the file mode is the only thing protecting any of it, and a default umask of 022 makes it world-readable. This is the one security obligation that is real in Phase 1, where there is no network surface and no second user of the service — the leak is local and it exists the day the first run is recorded. Criterion 3 keeps the requirement from silently rewriting a mode the user may have chosen deliberately: it reports rather than corrects.

---

## RQ-41 · Session report ingestion

*Must Have · Functional · Event-driven · Test · Draft*

> When a client submits a well-formed session report to the versioned ingestion endpoint, the system shall store that session and acknowledge it.

**Acceptance criteria**

1. Given an empty database, when a well-formed session report is submitted to /api/v1/runs, then the run table holds one row and the response acknowledges it with the identifier stored.
2. Given a session report that has already been submitted, when the identical report is submitted a second time, then the run table still holds one row for that session and the response acknowledges it.
3. Given a running server, when the ingestion endpoint is requested at an unversioned path, then the request is refused rather than served.

**Rationale**

Under ADR-9 the plugin performs no writes; this endpoint is how anything reaches the database at all, so nothing else in the system works without it. No requirement covered it before that decision, because before it there was no endpoint.

The path is versioned because plugin and server release independently (ADR-4) and a 1.2 plugin will meet a 1.5 server. The version in the path is what makes that ordinary rather than a defect, and criterion 3 keeps an unversioned path from quietly becoming a second, unpromised contract.

Criterion 2 is idempotency, and it is not optional: a plugin that times out waiting for an acknowledgement must be able to retry without inventing a duplicate run. Since the identifier is generated by the client (uuid4), the server can settle this by identity rather than by guessing.

---

## RQ-42 · Malformed report rejection

*Must Have · Functional · Unwanted behaviour · Test · Draft*

> If a submitted session report cannot be understood, then the system shall reject it and store nothing from it.

**Acceptance criteria**

1. Given an empty database, when a report with a missing required field is submitted, then the response reports the rejection and the run table stays empty.
2. Given an empty database, when a payload that is not valid JSON is submitted, then the response reports the rejection and the run table stays empty.
3. Given an empty database, when a report is submitted whose body is truncated midway, then the response reports the rejection and the run table stays empty.
4. Given a rejected report, when the response is read, then it names which field or condition caused the rejection, without exposing internal identifiers or a traceback.

**Rationale**

The boundary where untrusted input enters the system. A report arrives over a network from a process the server does not control, and can be malformed by a bug in any plugin, in any language, or simply by being cut off in flight.

Storing nothing is what makes RQ-3 achievable: "observable in full or not at all" cannot hold if a partial parse leaves a partial row. Criterion 3 is the case that only exists because the boundary is now a network — a truncated body was impossible when the plugin wrote to a local file.

Criterion 4 is what makes the rejection useful rather than merely correct: a plugin author debugging their own integration needs to know which field, and a stack trace in a response body is an information leak.

---

## RQ-44 · Abandoned run is observable

*Must Have · Functional · Unwanted behaviour · Test · Draft*

> If a run entry has a start time and no end time and no report has arrived for it within a configured grace period, then the system shall present that run as abandoned rather than as still running.

**Acceptance criteria**

1. Given a run entry with a start time, no end time, and no report received for longer than the grace period, when the run is read back, then it is presented as abandoned.
2. Given a run entry with a start time, no end time, and a start time inside the grace period, when the run is read back, then it is presented as running rather than abandoned.
3. Given a run entry that was reported as interrupted with Ctrl-C, when the run is read back, then it is presented as interrupted rather than abandoned, because a report did arrive for it.
4. Given a run presented as abandoned, when its record is inspected, then the start time it was recorded with is unchanged — no field is invented to represent an end that never happened.

**Rationale**

A killed session must not be a lost session, and it must not be a permanently running one either. RQ-1 and RQ-31 together guarantee the entry exists with a null end time, which is the raw fact. This requirement is about what that fact MEANS to a reader: without it, a run killed six months ago and a run started four seconds ago are indistinguishable in the history, and the view that RQ-19 demonstrates would show both as in flight forever.

The grace period is what makes this decidable, and it belongs to the server rather than the plugin — the plugin is not running any more, which is the entire point. Phase 2 rather than Phase 1 because Phase 2 already brings incremental flush, and the two share a mechanism: both need the server to reason about a run it has not heard from.

---

