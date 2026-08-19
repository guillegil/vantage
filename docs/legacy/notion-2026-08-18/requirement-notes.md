# Requirement page notes — full Notion export

> **FROZEN. NOT A WORKING DOCUMENT. SCHEDULED FOR DELETION.**
> See `README.md` in this directory first. This is a snapshot taken on
> 2026-08-18. Nothing here is authoritative and nothing here is maintained.

What the requirements *table* did not carry: the design notes, the rejected
alternatives, the open questions raised against a single requirement, and the
change log. This is the part worth migrating — the rejected alternatives exist
because somebody already tried the obvious thing.

**The `Verification` paths below are stale.** They name
`packages/vantage-pytest/`, a distribution that ADR-4 replaced with
`packages/pytest-vantage/`. Read them as intent, not as paths.

---

## RQ-1 · Run entry
*Phase 1 · Source: Design document*

**Verification** — `test_session.py::test_one_run_entry_per_invocation`, marked `@pytest.mark.req("RQ-1")`. Runs a suite twice through the `pytester` fixture and asserts two run entries exist, with distinct identifiers and non-null start timestamps.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-2 · Opt-in recording
*Phase 1 · Source: Own analysis*

**Verification** — `test_session.py::test_no_file_created_without_flag`, marked `@pytest.mark.req("RQ-2")`. Runs the suite twice — once with the plugin loaded and the flag absent, once with `-p no:vantage` — and asserts the two resulting trees are identical.

**Notes**

**The recording option is `--vantage`, on the command line.** `--vantage-db=PATH` implies it, since supplying a path explicitly is unambiguous activation.

**This requirement constrains activation, not configuration.** A file or an environment variable may configure Vantage — where the database lives, the truncation limit, the page size — and none of that turns recording on for anybody. What neither may do is *activate*. "Absent from the invocation" means the flag.

Two separate reasons, and they fail differently:

- **A configuration file that activates** is committed by one person and silently enables recording for everyone who checks the repository out, which is the same failure this requirement exists to prevent.
- **An environment variable that activates** is invisible in the command line RQ-11 records. That command line exists so a result can be traced back to the invocation that produced it; if a variable can turn recording on, the stored line no longer explains why the run exists, and Vantage cannot reproduce its own results — the one thing it promises. This argument was first written in FT-1's design notes as a design preference; it is recorded here because it is the reason for a requirement.

Note that pytest's own `addopts` in `pyproject.toml` can carry `--vantage`, so a file *can* activate recording no matter what Vantage does. That is pytest's documented mechanism, visible where every other pytest setting lives and reviewable in a diff. What this requirement forbids is Vantage building a second, less visible door of its own.

The comparison is differential, and it has to be. The original criterion said no file is created in the project tree, which is not achievable: pytest itself writes `.pytest_cache` and `__pycache__` on any run. Comparing against an empty directory fails for reasons that have nothing to do with Vantage. Comparing against the same suite with the plugin disabled isolates exactly what this requirement claims.

**Change log**

| Date | Change | Why |
| --- | --- | --- |
| 2026-08-13 | Created and approved | Own analysis |
| 2026-08-13 | Acceptance criterion changed from an absolute to a differential comparison | The absolute form is unsatisfiable — pytest writes cache directories itself, so the test failed for a reason unrelated to the requirement. Found while building the Milestone 1 scaffold |
| 2026-08-14 | Statement reframed positively; notes now name `--vantage` and separate activation from configuration | The statement said what must not happen, which is testable only by exhaustion. The notes previously ruled out configuration files wholesale, which also ruled out configuring anything at all — the failure mode is a file that *activates*, not a file that exists |

---

## RQ-3 · Single write transaction
*Phase 1 · Source: Design document*

**Verification** — `test_session.py::test_single_transaction_per_session`, marked `@pytest.mark.req("RQ-3")`. Counts commits on the connection through a counting wrapper around the storage adapter, over a generated suite of 500 tests, and asserts the count is one.

**Notes** — Counting transactions rather than measuring elapsed time keeps the test deterministic on a loaded machine. The timing claim it stands in for lives in RQ-25, verified by analysis.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-4 · Outcome across phases
*Phase 1 · Source: Design document*

**Verification** — `test_capture.py::test_outcome_derived_from_all_phases`, marked `@pytest.mark.req("RQ-4")`. Fixture suite containing one setup failure, one skip, one xfail and one xpass. Asserts four recorded results with four correct outcomes.

**Notes** — The failure mode this guards against is silent: a setup failure emits no call phase, so an implementation reading only the call report drops it without error. The tool looks like it is working while losing exactly the results that matter most.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-5 · Per-phase duration
*Phase 1 · Source: Design document*

**Verification** — `test_capture.py::test_phase_durations_recorded_separately`, marked `@pytest.mark.req("RQ-5")`. A fixture that sleeps and a body that does not. Asserts the setup duration dominates and the call duration is near zero, with generous margins so the test does not become flaky on a loaded machine.

**Change log**

| Date | Change | Why |
| --- | --- | --- |
| 2026-08-13 | Created and approved | Derived from the design document |
| 2026-08-13 | Demoted from Must to Should | MoSCoW budget: the Must share had reached 65%, above the 60% ceiling |

---

## RQ-6 · Parameter values
*Phase 1 · Source: Own analysis*

**Verification** — `test_capture.py::test_parameter_values_not_only_ids`, marked `@pytest.mark.req("RQ-6")`. Parametrises over objects rather than primitives, so pytest falls back to positional identifiers, and asserts the stored parameters contain the representation of each value.

**Notes** — pytest names complex parameter cases positionally — `test[u0]`, `test[u1]`. Reordering the parameter list keeps the names and changes what they point at, which silently reattributes a case's entire recorded history to a different input. Nobody would notice for months.

**Open question (unresolved at export)** — `repr()` is not guaranteed stable: an object without a custom `__repr__` yields a memory address, so every run looks like a different parameter. Open decision — fall back to the positional identifier when the representation looks address-like, or accept the noise and document it.

**Change log** — 2026-08-13 created and approved, own analysis.

---

## RQ-7 · Markers
*Phase 1 · Source: Design document*

**Verification** — `test_capture.py::test_markers_with_origin`, marked `@pytest.mark.req("RQ-7")`. A test carrying its own marker inside a class carrying another. Asserts both are recorded and each is attributed to its origin.

**Notes** — `item.iter_markers()` yields declared and inherited markers indistinguishably; `own_markers` is what separates them. Storing them flattened would make "which tests are themselves marked slow" unanswerable.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-8 · Failure location
*Phase 1 · Source: Design document*

**Verification** — `test_capture.py::test_failure_location_and_traceback`, marked `@pytest.mark.req("RQ-8")`. Twenty generated tests failing at one shared source line. Asserts a query on failure path and line returns all twenty as one group, and that each stored record also carries the full traceback.

**Notes** — Grouping is the point. A flat list of twenty tracebacks reads as twenty problems; the path and line, stored as separate columns rather than parsed out of the traceback text, are what make it one.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-9 · Decomposed identity
*Phase 1 · Source: Design document*

**Verification** — `test_capture.py::test_identity_decomposed`, marked `@pytest.mark.req("RQ-9")`. Records a suite spanning several files and classes, then asserts that filtering on file path alone returns every test defined in that file.

**Notes** — Decomposing on write rather than parsing on read is what makes Phase 3 reconciliation possible — matching a test to its own history after it has been renamed or moved. Storing the node identifier as an opaque string would make that a schema migration under live user data, which is the one thing the architecture is built to avoid.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-10 · Version-control context
*Phase 1 · Source: Design document*

**Verification** — `test_context.py::test_git_context_recorded`, marked `@pytest.mark.req("RQ-10")`. Builds a throwaway repository through `pytester`, commits, then modifies a tracked file without committing. Asserts the run records the commit hash, branch, subject line, and a dirty working tree.

**Notes**

The statement enumerates four fields rather than combining four obligations — it is one obligation, recording the version-control context, with its content listed. It should be split only if the fields ever acquire different priorities.

The dirty-tree flag is the field that carries the weight. A result produced from uncommitted changes cannot be reproduced by anyone, and treating it as trustworthy history is worse than not recording it at all.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-11 · Machine context
*Phase 1 · Source: Design document*

**Verification** — method is **Inspection**, not Test: these six values are whatever the host reports, so an assertion could only restate the implementation. The inspection is to record a run and confirm all six fields are populated and plausible for the machine that produced it. Its output belongs in the release checklist, not in the test suite.

**Notes** — The statement enumerates six fields rather than combining six obligations — one obligation, recording the machine context, with its content listed.

**Open question (unresolved at export)** — The command line can contain a secret when someone passes a token as an argument. Harmless while everything stays on one machine; a real leak in Phase 3 when runs leave it. A decision is needed before team mode — redact, hash, or make command-line storage opt-out.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-12 · Distributed execution
*Phase 1 · Source: Own analysis*

**Verification** — `test_xdist.py::test_results_recorded_once_under_xdist`, marked `@pytest.mark.req("RQ-12")`. Six tests run with `-n 2`. Asserts the recorded result count is six.

**Notes** — Under xdist every result is reported twice — once by the worker that ran it and once by the controller that collected it. The filter is whether the config object carries a worker input attribute; only the controller writes.

**Rejected** — Deduplicating on read: it hides a bug in the write path behind a query, and the correct count becomes a property of the query rather than of the data.

**Change log** — 2026-08-13 created and approved, own analysis.

---

## RQ-13 · Catalogue retention
*Phase 1 · Source: Design document*

**Verification** — `test_catalogue.py::test_test_retained_after_removal`, marked `@pytest.mark.req("RQ-13")`. Records a suite, deletes one test from the source, runs again. Asserts the catalogue entry survives and its last-observed timestamp is unchanged from the earlier run.

**Notes** — A test disappearing is information, not garbage: it separates "this was deliberately removed" from "this never existed". Deleting the row would destroy the only evidence of the former. The frozen last-observed timestamp is what makes the absence legible — it dates the disappearance.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-14 · Read-only API
*Phase 1 · Source: Design document*

> ⚠️ **This requirement contradicts a planned Phase 3 feature, and the contradiction was not resolved.**
>
> PROJ-1's roadmap has Phase 3 *"stop being read-only — launch and schedule runs from the interface"*. **Launching a run creates data.** This requirement says every endpoint leaves stored data unchanged. Both cannot hold.
>
> Two ways out, neither chosen: scope this requirement explicitly to Phase 1 and let a separate write surface arrive later with its own rules, or narrow it to *"the endpoints that serve recorded history"* so a launch surface is out of its scope by construction.
>
> The second matters more than it looks, because whatever the launch surface turns out to be, **what it can launch must be a bounded named operation rather than an arbitrary command string** — otherwise it is a remote shell with a web interface. See RQ-40's notes.
>
> Recorded 2026-08-15.

**Verification** — `test_api.py::test_openapi_document_and_endpoints`, marked `@pytest.mark.req("RQ-14")`. Fetches the interface document, enumerates the paths it declares, and calls each one. Asserts every declared endpoint responds successfully — so the document cannot drift away from the service without the suite going red.

**Notes** — Read-only is structural, not conventional: no write route exists, so there is nothing to accidentally expose when Phase 3 puts this on a network.

**Open question (unresolved at export)** — Whether the interface document is generated from the code or hand-written is undecided. Generated stays true automatically; hand-written can be reviewed as a contract before the code exists, which fits the way these requirements are being written.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-15 · Test history endpoint
*Phase 1 · Source: Design document*

**Verification** — `test_api.py::test_test_history_endpoint`, marked `@pytest.mark.req("RQ-15")`. Seeds a database with 500 runs, requests one test's history, asserts the payload carries commit and duration per execution and that the response is returned within 100 ms.

**Notes** — This is the endpoint the whole product exists to serve. Everything else in the Read API is scaffolding around it.

**Open questions (unresolved at export)**

- The 100 ms figure holds only with an index on the decomposed identity columns, and that index is not free on the write path — where RQ-25 caps the budget at 2%. The two numbers need to be measured together rather than defended separately.
- A timing assertion inside a test suite is flaky by nature on a loaded machine. Consider marking it and reporting it as a benchmark rather than letting it fail a build.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-16 · Lean list responses
*Phase 1 · Source: Design document*

**Verification** — `test_api.py::test_list_responses_exclude_long_fields`, marked `@pytest.mark.req("RQ-16")`. Seeds 500 results each carrying a traceback, requests the result list, asserts the response is below 500 KB and that no long text field appears in any item.

**Notes** — Asserting on both size and field presence is deliberate: the size check alone would pass by accident on short tracebacks, and the field check alone would not catch a new long column added later.

**Rejected** — Letting the client request extra fields: it makes the expensive case the default for anyone who has not read the documentation. The detail endpoint returns the full record instead.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-17 · Bounded pagination
*Phase 1 · Source: Design document*

**Verification** — `test_api.py::test_list_limit_enforced_server_side`, marked `@pytest.mark.req("RQ-17")`. Requests 100000 items from every list endpoint and asserts none returns more than the configured maximum.

**Notes** — A maximum, not a default. A default can be overridden by a client asking for everything; the failure being prevented is the browser tab that hangs, and the user will attribute that to the tool rather than to their own query.

**Assumption flagged in the rationale** — **200 is an assumption, not measured**: it is set where it stops changing a decision, being comfortably above a screenful and comfortably below a response size that needs streaming. Revisit once real response sizes exist.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-18 · Run list
*Phase 1 · Source: Design document*

**Verification** — method is **Demonstration**: this is a rendered view, and an assertion on markup would test the template rather than the capability. Record five runs, open the run list, confirm the five appear in descending order of start time. Capture a screenshot in the release checklist so the demonstration leaves a durable record.

**Notes** — The ordering is the whole requirement. A run list in insertion order is the same list nobody could read in the terminal.

**Change log**

| Date | Change | Why |
| --- | --- | --- |
| 2026-08-13 | Created and approved | Derived from the design document |
| 2026-08-13 | Demoted from Must to Should | MoSCoW budget: the Must share had reached 65%, above the 60% ceiling |

---

## RQ-19 · Test history view
*Phase 1 · Source: Design document*

**Verification** — method is **Demonstration**: this is the acceptance criterion of the whole MVP, and what is being verified is that a person can answer a question — not that a template renders. Seed a test that passed for ten runs and then failed. Open its history view. Confirm the first failing execution is identifiable, its commit is reachable from the view, and any execution recorded from a dirty working tree is visibly marked. Record it in the release checklist.

**Notes** — If this demonstration cannot be performed, Phase 1 is not done regardless of how many other requirements are green. Everything else exists to make this view possible.

**Open question (unresolved at export)** — The demonstration is written against ten executions. A test with a thousand needs windowing or pagination in the view, and what the view does at that size is not decided.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-20 · Self-contained interface
*Phase 1 · Source: Design document*

**Verification** — method is **Demonstration**, performed on a clean machine or container with no Node.js installed: install the package, start the service, confirm the interface loads. Worth automating later as a container step in CI, since a demonstration performed on the maintainer's own machine is the one most likely to pass for the wrong reason.

**Notes** — The user is a Python developer installing a pytest plugin. Requiring a JavaScript runtime on their machine to look at their own test results is a reason not to install it at all. Any build step that needs Node runs on the maintainer's machine; its output ships inside the package.

**Open question (unresolved at export)** — Shipping built assets means committing generated output to the repository, which goes stale silently. A CI check that the committed build matches its source would catch it — not designed yet.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-21 · Non-disruptive failure
*Phase 1 · Source: Own analysis*

**Verification** — `test_resilience.py::test_internal_error_does_not_change_exit_status`, marked `@pytest.mark.req("RQ-21")`. Monkeypatches the recording path to raise, runs a suite of passing tests, asserts pytest exits 0 and that a warning was emitted. The warning assertion matters as much as the exit status: silent failure and non-disruptive failure are not the same thing.

**Notes** — The most important requirement in Phase 1, and the one with no visible output. The asymmetry is total — the value of a recorded run is small and cumulative, the cost of a broken build is immediate and blamed on the tool.

**Open questions (unresolved at export)**

- Swallowing every exception also hides real bugs during development, when they are cheapest to find. There should be an escape hatch — an environment variable used by Vantage's own suite — off by default and obscure enough that nobody turns it on in CI.
- "Emits a warning" is not the same as "the user sees it". Under `-q`, or in CI logs nobody reads, a silently non-recording install can go unnoticed for a long time. Accepted for Phase 1.

**Change log** — 2026-08-13 created and approved, own analysis.

---

## RQ-22 · Bounded text fields
*Phase 1 · Source: Design document*

**Verification** — `test_resilience.py::test_text_fields_truncated_and_marked`, marked `@pytest.mark.req("RQ-22")`. A test failing with a 10 MB traceback. Asserts the stored field is at or below the configured maximum and contains the truncation marker.

**Notes** — The marker lives inside the value rather than in a separate flag column, so anyone reading the field sees that it was cut without needing to know a second column exists. The failure being avoided is someone debugging from a traceback that quietly ends early.

**Assumption flagged in the rationale** — **64 KiB is an assumption, not measured**: roughly two hundred traceback lines, comfortably past any traceback anyone reads, and small enough that a thousand of them stay under 64 MB. Revisit once real tracebacks have been recorded. Note this limit governs the traceback referred to by RQ-8; the two requirements previously contradicted each other, RQ-8 requiring a *complete* traceback while this one truncated every text field.

**Open question (unresolved at export)** — Cutting at a byte offset can split a multi-byte character or an escape sequence and produce something that breaks the renderer downstream. Truncate on a character boundary and add a test for exactly that.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-23 · Absent version control
*Phase 1 · Source: Design document*

**Verification** — `test_context.py::test_run_recorded_without_git`, marked `@pytest.mark.req("RQ-23")`. Runs a suite in a directory that is not a repository. Asserts the run is stored, its version-control fields are empty, and no warning is emitted.

**Notes** — Asserting the absence of a warning is deliberate. A tool that complains about something optional being missing trains people to ignore its warnings, which then also hides the warnings that matter — including RQ-21's.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-24 · Zero runtime dependencies
*Phase 1 · Source: Own analysis*

**Verification** — `test_packaging.py::test_no_third_party_runtime_dependencies`, marked `@pytest.mark.req("RQ-24")`. Installs the plugin distribution into a clean virtual environment and asserts nothing beyond the plugin itself appears. *(The page's wording still named `vantage-core` / `vantage-storage` / `vantage-pytest`; that predates ADR-4 and ADR-9.)*

**Notes** — Scope is the plugin only. The server needs a web framework and is installed deliberately by someone who wants a server; the plugin lands in everybody's test environment whether they thought about it or not. Every third-party dependency a plugin brings is a version conflict waiting for the wrong week. The plugin that pins a library the project also uses is the plugin that gets removed.

**Change log**

| Date | Change | Why |
| --- | --- | --- |
| 2026-08-13 | Created and approved | Own analysis |
| 2026-08-13 | Statement and criterion changed from "no runtime dependency" to "no third-party distribution" | The original wording contradicted the monorepo decision: the plugin necessarily depended on `vantage-core` and `vantage-storage`, so "the list grows only by the plugin itself" could never be true. What the requirement always meant was third parties. Found while building the Milestone 1 scaffold |
| 2026-08-15 | Condition restated as "where pytest is already present"; requirement strengthened to counting to one | The previous wording said "exactly one distribution" against a *clean* environment, which is false — a plugin declares `pytest`, so a clean environment gains two. Under ADR-9 the plugin reports over HTTP and depends on nothing but the standard library and the runner it extends |

---

## RQ-25 · Runtime overhead
*Phase 1 · Source: Own analysis*

**Verification** — method is **Analysis**, not Test: a single before-and-after comparison on one machine measures the machine, not the plugin. The method: a generated suite of 1000 tests, five runs with recording and five without, **interleaved rather than grouped** so that thermal and background drift affects both arms equally. Compare medians. The result belongs in the release notes with the machine it was measured on.

**Notes** — Nobody measures a 2% slowdown, but everybody notices a suite that "got slower after we installed that thing" — and the tool is blamed whether or not it is responsible. The number exists to make that argument answerable with data.

**Why criterion 2 exists (from the rationale)** — **Criterion 2 is the one that changed under ADR-9, and it is the one that decides whether this requirement is achievable at all.** Reporting now crosses a network. A request per test would put a round trip in the inner loop of somebody's suite and no budget would survive it; the session must be batched and sent once. A median over paired runs is used deliberately rather than a percentile: the measurement is one total duration per run, not a distribution of request latencies, so there is no tail for a percentile to describe.

**Open questions (unresolved at export)**

- 2% may be optimistic on very fast suites, where a thousand trivial tests finish in seconds and any fixed session cost dominates the percentage. It may need restating as a fixed per-test budget plus a fixed session cost.
- The index RQ-15/RQ-33 needs to hit 100 ms is paid for on this write path. The two numbers have to be measured together.

**Change log** — 2026-08-13 created and approved, own analysis.

---

## RQ-26 · Core isolation
*Phase 1 · Source: Design document*

**Verification** — `test_architecture.py::test_core_imports_no_infrastructure`, marked `@pytest.mark.req("RQ-26")`. Walks every module in the core with `ast`, collects the root name of each import, and asserts none is in the forbidden set — pytest, database drivers, web frameworks, HTTP clients. A second test, `test_core_package_is_not_empty`, asserts the walk found modules at all. Without it the check passes vacuously the day someone moves the package and forgets to update the path, which is the worst possible failure mode for a test whose whole job is to say no.

**Notes** — This is the first test to write — before any code exists for it to constrain. A boundary that nothing checks is a boundary that lasts until the first inconvenient afternoon. Verified against a deliberate violation: adding `import sqlite3` to a core module turns it red. A guard that cannot fail is not a guard.

**Open question (unresolved at export)** — Static analysis only sees static imports. Anything reached through a string, a registry, or a late import inside a function passes. This raises the cost of a violation rather than making one impossible, and that limit is written here so the next person does not mistake a green test for a proof.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-27 · Supported runtimes
*Phase 1 · Source: Design document*

**Verification** — the CI matrix is the test: Python 3.10, 3.11, 3.12 and 3.13, each with and without pytest-xdist installed — eight combinations, all required to pass before a release. Tag the matrix definition with `RQ-27` in a comment so the requirement is greppable from the workflow file, not only from the test suite.

**Notes** — Python 3.9 and earlier are out of scope permanently; 3.9 is past end of life. pytest-xdist is a test-time dependency only. It must never become a runtime dependency of the plugin — see RQ-24.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-28 · Offline operation
*Phase 1 · Source: Own analysis*

**Verification** — `test_packaging.py::test_operates_without_network`, marked `@pytest.mark.req("RQ-28")`. Runs in a container with networking disabled: record a suite, start the service, load the interface. Asserts all three succeed. A weaker variant that only blocks outbound sockets at the Python level is worth having as a fast local test, but the container is the one that counts — it also catches a subprocess reaching out.

**Notes** — This is a privacy promise as much as a portability one. Vantage records commit messages, file paths and command lines; "it never leaves your machine" has to be enforced, not stated.

**Change log** — 2026-08-13 created and approved, own analysis.

---

## RQ-29 · Complete schema from first use
*Phase 1 · Source: Design document*

**Verification** — method is **Inspection**: create a fresh database, read back its schema, compare column by column against the documented data model. Worth writing as an automated comparison rather than a manual read, since the thing it protects against is a column quietly not being created.

**Notes** — `fixtures`, `logs`, `stdout` and `stderr` exist from the first write and stay NULL until Phase 2 populates them. Deferring a feature is free; migrating a schema under live user data is not, and the deliberate absence of a migration framework in Phase 1 is what keeps casual schema changes from feeling affordable.

**Open question (unresolved at export)** — Declaring Phase 2 columns now commits to their shape before the code that fills them exists. If the shape turns out wrong, the migration this requirement exists to avoid happens anyway — and this decision will have caused it. Accepted deliberately rather than discovered later.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-30 · Replaceable storage
*Phase 1 · Source: Design document*

**Verification** — `test_storage_port.py::test_core_suite_passes_against_in_memory_adapter`, marked `@pytest.mark.req("RQ-30")`. Runs the core test suite twice — once against the SQLite adapter, once against an in-memory implementation of the same port — and asserts both pass with no change to the core.

**Notes** — The in-memory adapter is not a stub written to make the test pass; it is the proof that the port is a real boundary and the second implementation that keeps it honest. It also makes the core suite fast enough to run on every save. Ports are `typing.Protocol` rather than abstract base classes, so an adapter satisfies one without importing the core — the dependency arrow points inwards at the type level too.

**Change log** — 2026-08-13 created and approved, derived from the design document.

---

## RQ-31 · Run timestamps
*Phase 1 · Source: Own analysis · split out of RQ-1 on 2026-08-14*

**Verification** — a test in the plugin package asserting both timestamps on a completed session, and a second asserting the null end time after an interrupted one. Both carry this requirement's ID.

**Notes** — "Ended" rather than "finished" is deliberate: a session that is killed still ends, and the requirement must cover it. The null end time in criterion 2 is the observable difference between a session that completed and one that did not, and every later query that filters for complete runs depends on it.

**The signal analysis, from the 2026-08-16 amendment** — A reason exists only for interruptions Python is given the chance to observe. Ctrl-C raises `KeyboardInterrupt`, pytest's `wrap_session` calls `pytest_sessionfinish` from a `finally` block, and the plugin gets to say what happened — that is criterion 2. **SIGKILL cannot be caught, blocked or handled**: the process stops between two instructions and no code of ours runs. Criterion 3 therefore asserts the ABSENCE of a reason rather than a value for it. The distinction between "finished" and "did not" survives a kill; the distinction between one kind of death and another does not.

**Note** — this is the one requirement whose "Ready to leave Draft" atomicity box was left *unchecked*.

**Change log** — 2026-08-14 created, split out of RQ-1, because RQ-1 bundled identity and timing into one statement so neither could be accepted on its own.

---

## RQ-32 · Traceback capture
*Phase 1 · Source: Own analysis · split out of RQ-8 on 2026-08-14*

**Verification** — a test asserting frame names in a deliberately nested failure, and a second asserting the truncation boundary. Both carry this requirement's ID.

**Notes** — The word "complete" has deliberately been dropped from the original wording. It was unsatisfiable alongside RQ-22, and a 10 MB traceback is not something anyone reads — the useful obligation is that the frames leading to the failure are present, which criterion 1 states directly.

**Change log** — 2026-08-14 created, split out of RQ-8, which bundled failure location and traceback and whose "complete traceback" contradicted RQ-22's truncation of every text field.

---

## RQ-33 · Test history latency
*Phase 1 · Source: Own analysis · lifted out of RQ-15 on 2026-08-14*

**Verification** — method is Analysis, not Test: the figure is a percentile over a distribution and needs a fixture database, a repetition count and a percentile calculation, not an assertion. A benchmark script generates the corpus, issues the requests and reports p95 and the maximum. Its output is committed alongside the script, and this requirement's ID appears in a comment at the top of it.

**Notes** — Measured server-side deliberately. The same request measured in a browser includes connection setup and rendering and can differ by several times, so a browser-side number would describe the machine rather than the system. Criterion 2 exists because a p95 alone hides the tail, and for an interaction this central the worst case is worth knowing even when the budget holds.

**Assumption flagged in the rationale** — **100 ms is an assumption, not measured**: the classic threshold at which an interaction feels instant, and this is the endpoint the whole product exists to serve, so the tighter of the defensible numbers is the right starting point. Revisit once real databases exist.

**Change log** — 2026-08-14 created, lifted out of RQ-15, where a non-functional obligation was hiding inside a functional requirement's acceptance criterion with no percentile, measurement point or load.

---

## RQ-34 · Dirty working tree marking
*Phase 1 · Source: Own analysis · split out of RQ-19 on 2026-08-14*

**Verification** — method is Demonstration: a seeded database, the view opened in a browser, and a screenshot recorded against this requirement's ID.

**Notes** — The distinction criterion 2 protects is **three-valued, not two**: an execution is recorded from a clean tree, from a dirty tree, or from somewhere with no version control at all. Rendering the third as clean is the failure mode, because it presents a result as reproducible when nobody knows whether it is.

**Change log** — 2026-08-14 created, split out of RQ-19, which carried three obligations in one statement so shipping the history without the dirty marker would have been recorded as it being half-met.

---

## RQ-35 · Command-line redaction
*Phase 1 · Source: Own analysis · created in the 2026-08-14 audit*

**Verification** — a test passing a synthetic credential on the command line and asserting its absence from the stored value, plus a second asserting an ordinary argument survives. Both carry this requirement's ID. **The synthetic credential is generated, never a real one.**

**Notes** — The four substrings (`password`, `token`, `secret`, `key`) are named explicitly rather than described as "secret-bearing", because "a pattern that looks like a secret" is exactly the kind of phrase two readers implement differently. The list is deliberately short and will miss cases; **it is a floor, not a guarantee**, and widening it is a change to this requirement rather than a judgement call at implementation time. This requirement does not attempt to redact secrets appearing in environment variables or in test output. Those are separate obligations and are not written yet.

**Change log** — 2026-08-14 created: RQ-11 records the command line and nothing constrained what it may contain; walking the ISO 25010 security characteristic surfaced the gap.

---

## RQ-36 · Interface document
*Phase 1 · Source: Own analysis · split out of RQ-14 on 2026-08-14*

**Verification** — a test that fetches the document, parses it, walks every declared path, and compares the declared set against the routes the service actually serves.

**Notes** — **The format is deliberately not named.** Which document standard is used is a decision between plausible alternatives and belongs in an ADR; the requirement only obliges that one exists, is machine-readable and is complete.

**Change log** — 2026-08-14 created, split out of RQ-14, which bundled the read-only property with the existence of the interface document; the two are accepted independently.

---

## RQ-37 · Unreachable server
*Phase 1 · Source: Own analysis · created in the 2026-08-14 audit*

**Verification** — three tests, one per criterion, each carrying this requirement's ID.

**Notes** — "Run to completion unrecorded" is deliberate. **The alternatives are both worse**: refusing to start breaks a suite over an observability tool, which is the failure RQ-21's rationale describes; creating the missing directory writes to a location the user did not ask for, which is the failure RQ-2 exists to prevent. Whether the warning appears once per session or once per attempt is not specified here, because only one attempt is made.

**Naming the address matters (from the rationale)** — the difference between fixing a typo in seconds and filing a bug: "could not reach the server" without the address sends someone looking in the wrong place. Criterion 4 exists because a per-test warning turns a small misconfiguration into hundreds of lines of noise, which trains people to ignore warnings — including the ones that matter.

**Note** — this requirement's atomicity box was left *unchecked*.

**Change log** — 2026-08-14 created: the commonest real failure had no requirement; behaviour would have been whatever the storage layer happened to throw. Restated under ADR-9 from "the configured database path cannot be opened for writing" to "the configured server cannot be reached" — the intent (warn, name the target, do not break the suite) carries over unchanged.

---

## RQ-38 · Concurrent sessions
*Phase 1 · Source: Own analysis · created in the 2026-08-14 audit*

**Verification** — a test spawning two pytest subprocesses against one server and asserting the counts.

> ⚠️ **Only criterion 1 was verifiable at Milestone 1.** Criterion 2 counts 400 results from two 200-test sessions, and Milestone 1 writes run entries only — no results exist yet. Criterion 1, two sessions leaving two run entries, is provable now. **Do not read this requirement as fully verified when Milestone 1 closes**; criterion 2 belongs to Milestone 2, alongside the first result write.

**Notes** — How long a blocked session waits before giving up is not stated here. If it gives up, the behaviour it falls back to is RQ-37's — a warning and an unrecorded session — so the two requirements together leave no undefined outcome. This requirement concerns sessions on one machine sharing one database. Sharing a database between machines is Phase 3 and out of scope.

**What ADR-9 changed (from the rationale)** — This is now an obligation of the **server**, not of the plugin. That is a genuine simplification: the plugin no longer competes for a database lock, and the whole apparatus the previous design carried for it — WAL, `BEGIN IMMEDIATE`, a busy timeout, a network-filesystem fallback — belongs to whatever storage the server chooses, behind its port. What has not gone away is the failure it guards against: silently losing the second session is still the worst outcome and still the easiest to ship by accident, because the losing session exits 0 and its user sees nothing.

**Change log** — 2026-08-14 created.

---

## RQ-39 · Unreadable version control
*Phase 1 · Source: Own analysis · created in the 2026-08-14 audit*

**Verification** — three tests, one per criterion. Criterion 2 is exercised by running the subprocess with a PATH from which `git` is absent.

**Notes** — The three requirements covering version control now **partition the space with no gap**: RQ-10 for a readable repository, RQ-23 for no repository, and this one for a repository that cannot be read. All three record the run; none of them refuses to. Null is used rather than a sentinel string, matching RQ-23, so that "version control unknown" is queryable as one condition regardless of which cause produced it.

**Change log** — 2026-08-14 created: RQ-10 and RQ-23 left a gap between them.

---

## RQ-40 · Owner-only store permissions
*Phase 1 · Source: Own analysis · created 2026-08-15*

> Created during a design conversation that found **no security requirement among the previous 39**. Walking the ISO 25010 security characteristic against Phase 1 produced exactly one obligation that is real today; the rest belong to later phases and are recorded as open questions rather than invented now.

**Verification** — a test creating a store under a deliberately permissive umask and asserting the resulting mode, plus a test that an already-permissive database is reported rather than corrected. Criteria 1 and 2 are skipped on Windows, where the POSIX mode has no meaning.

**Notes** — This requirement is deliberately narrow. It does not address authentication, transport security, or authorisation — none of which have a subject in Phase 1, since there is no network listener and no second user.

**The two obligations that will matter later, not yet written as requirements**

- When the read API arrives, it should **bind to the loopback interface by default**, and binding wider should be an explicit act rather than a comfortable default.
- When run launching arrives, **what can be launched must be a bounded, named operation rather than an arbitrary command string.** The difference between "run the test suite" and "run this command" is the difference between a tool and a remote shell with a web interface, and no amount of authentication layered on top recovers it. **This is an architectural decision, not a security control**, which is why it has to be made before the surface exists rather than retrofitted onto one that already accepts commands.

**Open questions (unresolved at export)**

- The Windows equivalent of an owner-only mode is an ACL rather than a POSIX mode, and is not specified.
- Whether criterion 3 should warn once per session or once per database is undecided.

**Change log** — 2026-08-15 created.

---

## RQ-41 · Session report ingestion
*Phase 1 · Source: Own analysis · created 2026-08-15 during the ADR-9 replan*

**Verification** — tests against the service exercising each criterion. Criterion 2 submits the same payload twice and asserts one row.

**Notes** — **The payload's shape is not specified here.** What a session report contains is dictated by the requirements whose data it carries — RQ-1 and RQ-31 in this milestone, RQ-4 onwards later — and pinning a wire format in this requirement would freeze it before those exist. **Authentication is deliberately absent**: in Phase 1 the server binds to loopback and serves one person; an authenticated ingestion endpoint has no subject until Phase 4 brings a shared server.

**Why the path is versioned (from the rationale)** — plugin and server release independently (ADR-4) and a 1.2 plugin will meet a 1.5 server. The version in the path is what makes that ordinary rather than a defect, and criterion 3 keeps an unversioned path from quietly becoming a second, unpromised contract. **Criterion 2 is idempotency, and it is not optional**: a plugin that times out waiting for an acknowledgement must be able to retry without inventing a duplicate run. Since the identifier is generated by the client (`uuid4`), the server can settle this by identity rather than by guessing.

**Change log** — 2026-08-15 created: ADR-9 made the server the sole writer; the endpoint that receives reports had no requirement.

---

## RQ-42 · Malformed report rejection
*Phase 1 · Source: Own analysis · created 2026-08-15*

**Verification** — a test per criterion against the service. Criterion 3 sends a body shorter than its declared length.

**Notes** — "Cannot be understood" covers three distinct causes deliberately: invalid encoding, a valid structure missing something required, and a body cut off in transit. They arrive differently and it is worth knowing all three are the same obligation.

**Not covered, and flagged as such** — This requirement says nothing about **rate limiting or payload size limits**. Both are real concerns for a server that accepts input, and both are unwritten — they have no subject while the server binds to loopback and serves one person, and they will need requirements of their own before Phase 4 exposes it.

**Validation placement** — hand-written over the standard library only where it runs inside a package RQ-24 constrains. In `vantage.service`, which RQ-24 does not constrain, Pydantic is the project's standard for system boundaries and this is exactly one.

**Change log** — 2026-08-15 created.

---

## RQ-44 · Abandoned run is observable
*Phase 2 · Source: Own analysis · created 2026-08-16*

**The Notion page body was blank.** Everything below comes from its properties.

**Rationale** — A killed session must not be a lost session, and it must not be a permanently running one either. RQ-1 and RQ-31 together guarantee the entry exists with a null end time, which is the raw fact. This requirement is about what that fact MEANS to a reader: without it, a run killed six months ago and a run started four seconds ago are indistinguishable in the history, and the view that RQ-19 demonstrates would show both as in flight forever.

The grace period is what makes this decidable, and it belongs to the server rather than the plugin — the plugin is not running any more, which is the entire point. **Phase 2 rather than Phase 1 because Phase 2 already brings incremental flush, and the two share a mechanism: both need the server to reason about a run it has not heard from.**

> **Known defect in this requirement, found 2026-08-18 and never fixed in Notion.**
> Acceptance criterion 2 reads *"a start time inside the grace period"* — it measures the
> grace period **from the run's start**. That is unsound and no constant repairs it: a
> 30-minute window marks a healthy two-day regression suite abandoned, and a three-day
> window leaves a run killed at minute three showing as running for three days. The
> number is trying to encode "how long does a suite take", which has no bound.
>
> The repair is to measure from **last contact**, which forces a periodic heartbeat —
> the server cannot distinguish "working hard" from "dead" without a sign of life. The
> incremental flush this requirement's own rationale pairs it with *is* that heartbeat.
> Residual hole: a single test running six hours flushes nothing, so either a daemon
> timer thread (which must not breach RQ-21) or activity-driven beats sized against the
> slowest single test.
>
> **Carry this correction forward when the requirement is rewritten in OpenSpec.**

---
