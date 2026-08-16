# Requirements — Milestone 1 scope

> **Generated from Notion. One direction only.**
> The source of truth is the Requirements database under **PROJ-1**. Editing
> this file changes nothing; edit the Notion row and regenerate. This is a
> mirror so that tooling and agents without Notion access can read the exact
> text a requirement is verified against.
>
> **Partial.** Only the sixteen requirements in Milestone 1's scope are
> mirrored here. The full set is `RQ-1`…`RQ-42`, all currently `Draft`.
>
> Generated **2026-08-15**, after the ADR-9 replan.

---

## RQ-1 · Run entry
*Must Have · Functional · Event-driven · Test · server*

> When a pytest session starts with recording enabled, the system shall create exactly one run entry whose identifier is unique across every run in the database.

1. Given an empty database, when pytest is invoked once with recording enabled, then the run table contains exactly one row.
2. Given a database already holding one run, when pytest is invoked again, then the run table contains two rows and their identifiers differ.
3. Given a directory containing no test files, when pytest is invoked with recording enabled, then the run table contains exactly one row.
4. Given a test file that raises ImportError at collection, when pytest is invoked with recording enabled, then the run table contains exactly one row.

## RQ-2 · Opt-in recording
*Must Have · Functional · Optional feature · Test · plugin*

> Where no recording option is present in the pytest invocation, the system shall attempt no connection to the server.

1. Given a project with the plugin installed and no recording option, when pytest is run with outbound connections logged, then no connection is attempted.
2. Given a project with the plugin installed and no recording option, when pytest is run once normally and once with `-p no:vantage`, then the two resulting project trees are byte-for-byte identical.
3. Given a project with the plugin installed, no recording option, and no server running at all, when pytest is run, then it exits with the status it would have had without the plugin and emits no warning.

## RQ-3 · Single write transaction
*Must Have · Functional · Event-driven · Test · server*

> When a pytest session finishes, the system shall make that session's results observable either in full or not at all, never partially.

1. Given a session of 500 tests whose report reaches the server, when the server is killed with SIGKILL midway through writing it, then the database afterwards holds either all 500 results of that session or none of them.
2. Given a session of 500 tests, when its report is truncated in transit, then the database holds none of that session's results rather than a prefix of them.
3. Given a session of 500 tests that is reported and written normally, when the database is queried afterwards, then all 500 results of that session are present.

## RQ-21 · Non-disruptive failure
*Must Have · Functional · Unwanted behaviour · Test · plugin*

> If the system raises an internal error while recording, then the system shall emit a warning and shall let the pytest session terminate with the exit status it would have had otherwise.

1. Given a reporting path patched to raise, when a suite of passing tests is run, then pytest exits with status 0 and emits one warning.
2. Given a reporting path patched to raise, when a suite containing one failing test is run, then pytest exits with status 1 and emits one warning.
3. Given a server that accepts the connection and then closes it without responding, when a suite of passing tests is run, then pytest exits with status 0 and emits one warning.
4. Given a server that accepts the connection and never responds, when a suite of passing tests is run, then pytest exits with status 0 within the configured timeout plus five seconds.
5. Given a reporting path patched to raise on every hook it implements, when a suite of passing tests is run, then pytest exits with status 0 and the session reports no internal error.

## RQ-24 · Zero runtime dependencies
*Must Have · Non-functional · Optional feature · Test · plugin*

> Where pytest is already present in the environment, installing the plugin package shall add no distribution other than the plugin package itself.

1. Given a virtual environment with pytest installed, when `pytest-vantage` is installed, then exactly one distribution is added to the environment.
2. Given the plugin package's source, when its imports are analysed, then every one resolves either to the Python standard library or to `pytest`.
3. Given the plugin package's metadata, when its declared dependencies are read, then `pytest` is the only one.

## RQ-26 · Core isolation
*Must Have · Non-functional · Ubiquitous · Test · server*

> The core package shall import only modules from the Python standard library.

1. Given the core package, when every import statement in it is resolved by static analysis, then each one resolves to a Python standard-library module.
2. Given the static analysis of criterion 1, when it runs, then it reports having examined at least one module.

## RQ-27 · Supported runtimes
*Must Have · Non-functional · Ubiquitous · Test · both*

> The system shall pass its own test suite on Python 3.10, 3.11, 3.12 and 3.13, both with and without pytest-xdist installed.

1. Given the continuous integration matrix, when it runs, then all eight combinations of the four Python versions and the two xdist configurations pass.
2. Given a Python 3.9 environment, when the plugin package is installed, then installation is refused by the declared version floor rather than failing at import time.

## RQ-28 · Offline operation
*Must Have · Non-functional · Ubiquitous · Test · both*

> The system shall complete every operation using only resources on the local machine.

1. Given a machine with networking disabled, when a suite is recorded and the interface is opened, then both succeed.
2. Given the system running with outbound connections logged, when a suite is recorded and the interface is opened, then no outbound connection is attempted.

*Note: "the interface is opened" is carried verbatim from the requirement. No interface exists until Milestone 5; the recording half is what this milestone can demonstrate.*

## RQ-29 · Complete schema from first use
*Should Have · Non-functional · Ubiquitous · **Inspection** · server*

> The system shall create its complete database schema when a database is first created, including columns that no code populates until a later phase, so that no Phase 1 release alters the schema of an existing database.

1. Given a freshly created database, when its schema is compared against the documented column manifest, then every documented column exists.
2. Given a database created by an earlier Phase 1 release, when a later Phase 1 release opens it, then the system issues no schema-altering statement.

*Verified by **Inspection**, not Test: the deliverable is the comparison against `docs/schema-manifest.md`, not an assertion.*

## RQ-30 · Replaceable storage
*Should Have · Non-functional · Ubiquitous · Test · server*

> The system shall support replacing the storage adapter by providing an alternative implementation of the storage port, without modifying the core package.

1. Given an in-memory implementation of the storage port, when the core test suite is run against it, then it passes unchanged.
2. Given the in-memory implementation of criterion 1, when the core package is inspected, then it contains no import of either storage implementation.

## RQ-31 · Run timestamps
*Must Have · Functional · Ubiquitous · Test · server*

> The system shall record on each run entry the time its session started and the time its session ended.

1. Given a session that runs for at least two seconds, when it completes, then its run entry holds a start time and an end time, and the end time is later than the start time.
2. Given a session interrupted with Ctrl-C, when the database is inspected afterwards, then its run entry holds a start time and a null end time.

## RQ-37 · Unreachable server
*Must Have · Functional · Unwanted behaviour · Test · plugin*

> If the configured server cannot be reached, then the system shall emit a warning naming that server and shall let the pytest session run to completion unrecorded.

1. Given a configured server address where nothing is listening, when a suite of passing tests is run, then pytest exits with status 0 and emits one warning naming the address.
2. Given a configured server address whose host does not resolve, when a suite of passing tests is run, then pytest exits with status 0 and emits one warning naming the address.
3. Given a server that becomes unreachable after the session has started but before the report is sent, when the suite finishes, then pytest exits with the status it would have had and emits one warning.
4. Given a configured server that is unreachable, when a suite of 200 tests is run, then exactly one warning is emitted rather than one per test.

## RQ-38 · Concurrent sessions
*Should Have · Functional · State-driven · Test · server*

> While more than one pytest session is reporting to the server concurrently, the system shall record every one of those sessions' run entries.

1. Given two pytest sessions started within the same second against one server, when both complete, then the database holds two run entries with different identifiers.
2. Given two pytest sessions of 200 tests each started within the same second against one server, when both complete, then the database holds 400 results.
3. Given ten pytest sessions reporting simultaneously, when all complete, then the database holds ten run entries and no session receives an error response.

*Only criterion 1 is in scope for Milestone 1. Criteria 2 and 3 count results, and this milestone writes none.*

## RQ-40 · Owner-only store permissions
*Should Have · Non-functional · Ubiquitous · Test · server*

> The system shall create the database file and the artefact store readable and writable only by the user account that created them.

1. Given a POSIX machine with a permissive umask of 022, when a database is created, then its mode is 0600.
2. Given a POSIX machine with a permissive umask of 022, when the artefact store directory is created, then its mode is 0700.
3. Given an existing database whose mode is 0644, when a session records to it, then the run is recorded and a warning names the permissive mode.

## RQ-41 · Session report ingestion
*Must Have · Functional · Event-driven · Test · server*

> When a client submits a well-formed session report to the versioned ingestion endpoint, the system shall store that session and acknowledge it.

1. Given an empty database, when a well-formed session report is submitted to `/api/v1/runs`, then the run table holds one row and the response acknowledges it with the identifier stored.
2. Given a session report that has already been submitted, when the identical report is submitted a second time, then the run table still holds one row for that session and the response acknowledges it.
3. Given a running server, when the ingestion endpoint is requested at an unversioned path, then the request is refused rather than served.

## RQ-42 · Malformed report rejection
*Must Have · Functional · Unwanted behaviour · Test · server*

> If a submitted session report cannot be understood, then the system shall reject it and store nothing from it.

1. Given an empty database, when a report with a missing required field is submitted, then the response reports the rejection and the run table stays empty.
2. Given an empty database, when a payload that is not valid JSON is submitted, then the response reports the rejection and the run table stays empty.
3. Given an empty database, when a report is submitted whose body is truncated midway, then the response reports the rejection and the run table stays empty.
4. Given a rejected report, when the response is read, then it names which field or condition caused the rejection, without exposing internal identifiers or a traceback.
