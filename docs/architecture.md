# Architecture

A maintainer's map: how the code is divided, what each part may depend on,
and why. What a user sees is in the [README](../README.md); the wire format
is in [`api/v1-ingestion.md`](api/v1-ingestion.md).

## Two distributions

```
packages/
├── pytest-vantage/                published as `pytest-vantage`
│   ├── src/pytest_vantage/        the pytest plugin, its outbox, its one door to vantage.local
│   └── tests/
└── vantage/                       published as `vantage`, with `server` and `postgres` extras
    ├── src/vantage/
    │   ├── core/                  domain model, storage port, config resolution
    │   ├── storage/               the SQLite adapter, the PostgreSQL one in postgres/
    │   ├── ingestion/             a report validated, converted and stored: the one way in
    │   ├── local/                 runs stored on the test machine, through ingestion
    │   └── service/               FastAPI app, `vantage` and `vantage push`, OpenAPI document
    └── tests/
```

`vantage` depends on `pytest-vantage`, never the reverse: installing
`vantage` brings the plugin, and the plugin declares nothing but pytest. The
two meet in two places, both lazy. The plugin's local modes hand a run to
`vantage.local`, imported by `pytest_vantage/local.py` alone, inside its
functions, once such a mode is configured; and `vantage push` sends the
plugin's outbox with the plugin's own code (`pytest_vantage.outbox`). Each
package has its own rule for what it may import, and each rule is checked
mechanically:

| Package | Ships in | May import | Checked by |
| --- | --- | --- | --- |
| `pytest_vantage` | `pytest-vantage` | the standard library and pytest; `vantage.local` from `pytest_vantage/local.py` alone, inside functions | AST import walk, a server-mode session loading none of the local modules, deptry, clean-environment install job |
| `vantage.core` | `vantage` | the standard library, minus modules that open a database, socket or process (`sqlite3`, `socket`, `http`, `urllib`, `subprocess`, `asyncio`, ...) | AST import walk |
| `vantage.storage` | `vantage` | the standard library and `vantage.core` | AST import walk |
| `vantage.storage.postgres` | `vantage` | the same, and the PostgreSQL driver (`psycopg`, `psycopg_pool`) from the `postgres` extra | AST import walk, an import of the command with the driver unavailable |
| `vantage.ingestion` | `vantage` | the standard library, `vantage.core`, Pydantic and PyYAML; never a web framework or a storage adapter | AST import walk, an import with the web framework and the driver unavailable |
| `vantage.local` | `vantage` | the standard library, `vantage.core`, `vantage.ingestion` and the SQLite adapter's modules | AST import walk, the same import |
| `vantage.service` | `vantage` | anything; the only package that imports FastAPI, Starlette or Uvicorn, which the `server` extra declares | an AST scan of every other package for them |

The checks catch different failures:

- **The AST import walk** (`packages/vantage/tests/importwalk.py`) reads every
  import statement, those inside functions included, and resolves relative
  imports, so `from ..service import x` inside the core is caught.
  `packages/vantage/tests/test_architecture.py` applies it to the core,
  storage, ingestion and the local store, and
  `packages/pytest-vantage/tests/test_plugin_imports.py` to the plugin, where
  the one import it allows outside pytest is `vantage.local` in
  `pytest_vantage/local.py`, inside a function. The storage walk allows the
  driver's two top-level modules to `vantage.storage.postgres` and the
  modules under it, and to nothing else; the local store's walk allows the
  SQLite adapter's modules by name, not the storage package. Because both
  extras are optional, the same test also imports storage, the app and the
  `vantage` command in a fresh interpreter where `psycopg` and
  `psycopg_pool` cannot be imported, and ingestion, the local store and the
  command in one where FastAPI, Starlette and Uvicorn cannot be either, and
  checks that the PostgreSQL adapter was not loaded: nothing a SQLite server
  or a test machine runs may reach it. An AST scan of every module outside
  the service checks that none imports the web framework.
- **deptry**, run over the whole workspace, flags an import of a third-party
  package the workspace does not declare.
- **The `clean-environment-install` CI job** builds both wheels and installs
  them into fresh environments. The plugin's alone, into one holding only
  pytest, must add exactly one distribution. `vantage`'s, with the plugin's,
  must bring pytest-vantage, pytest, Pydantic and PyYAML and no web
  framework; there `vantage` must refuse to serve in one line, `vantage push`
  must run, and a session in local mode must store its run where `vantage`
  serves by default. `vantage[server]` must serve, having given its fresh
  database the admin `admin` in exactly one line on stderr. It is the only
  check that sees what reaches a user.

Values both sides must agree on (the 1 MiB body cap, the 64 KiB text-field
cap, the metadata bounds and statuses, the outcome vocabulary, the timestamp
format, the heartbeat interval behind the default grace period, the shape of
a token and the statuses and error codes of the refusals of a token, the
project-name rule, `default`, the status and code of an unknown project,
and those of a refusal of the token's user in a project) are kept as a
copy on each side. `packages/pytest-vantage/tests/test_server_contract.py`
imports both and compares every copy.

## Clean architecture with Protocol ports

Dependencies point inwards: the service and the local store depend on
ingestion, storage and the core, ingestion and storage on the core, and the
core on nothing but the standard library.

- **`vantage.core`** holds the domain as frozen standard-library dataclasses
  (`Execution`, `Identity`, `VcsContext`, `Result`, `CaseIdentity`,
  `FailureEvidence`, `CapturedOutput`, `CatalogueEntry`, `User`, `Token`
  and `Grant` in `domain/access.py`, and `Project` and `Membership` in
  `domain/projects.py`), the pure rules (how a run is presented, list
  projections, section summaries, metadata vocabularies, token scopes,
  user and project names, the role a user acts with in a project, how a
  token is made and digested), and the server's configuration resolution.
- **The storage port** is `ExecutionStore` in `core/ports/storage.py`, a
  `typing.Protocol`. An adapter satisfies it by shape, without importing or
  subclassing the protocol itself. It does import the core's domain and
  value types (`Execution`, `Result`, `Page`, `RunListEntry` and the like),
  which it takes and returns; the core imports nothing from any adapter.
- **`vantage.storage`** holds the two adapters the server ships:
  `SqliteExecutionStore`, and `PostgresExecutionStore` in
  `vantage.storage.postgres`, which needs the optional `postgres` extra.
- **`vantage.ingestion`** is the one way a report becomes rows.
  `ingest(report, store, received_at=...)` validates a decoded report
  against its Pydantic models (`ingestion/schemas.py`), converts it into core
  dataclasses (`conversion.py`, declared YAML metadata included) and calls
  `store.record_session`, so no Pydantic type reaches the core or storage;
  the server also hands it an `admit` that may refuse the report's project
  first (see
  [Users, tokens and who may do what](#users-tokens-and-who-may-do-what)). It
  knows nothing of HTTP: a report it cannot take raises a plain
  `RejectionError` carrying a status code, an error code, a fixed sentence
  and field paths, which the service turns into a response and the local
  store into a line.
- **`vantage.local`** stores runs on the test machine:
  `store_reports(database, reports)` opens a `SqliteExecutionStore` at the
  path, runs each report through the same encoding, decoding and `ingest` a
  server applies, and closes it, raising `LocalStoreError`, one line, for any
  failure. `default_database_path()` is the `vantage` command's own default,
  from the same pure function in `core/config/resolution.py`.
- **`vantage.service`** is the HTTP edge: it reads and caps a body, hands it
  to ingestion in the threadpool, and shapes every answer. Its own Pydantic
  models (`schemas.py`) are the responses and the section settings.
  `create_app(store)` takes the store as a parameter: `cli.py` is the one
  place that builds one, a `SqliteExecutionStore` or a
  `PostgresExecutionStore`, and tests pass their own.

Two conventions follow from Python and pytest rather than from the design.
Vocabularies are `frozenset`s of plain `str`, never an `Enum`: a `str` enum
formats differently on Python 3.10 and 3.13. And no domain class name starts
with `Test`, because pytest would try to collect it; the aggregate is
`Execution`, not `TestExecution`.

## Why the plugin's own code never opens a database

In `server` mode the plugin reports over HTTP and the server performs every
write. The local modes do not change who writes: the plugin hands the
session's reports to `vantage.local`, which runs the server's own ingestion
in-process, against the SQLite adapter the server ships.

- **Nothing to conflict with.** Installing the plugin into a test environment
  adds one distribution and no dependency beyond pytest, so it cannot clash
  with the pins of the project under test. A database driver or ORM in the
  plugin would. The local modes need `vantage`, installed on purpose, and
  the plugin imports it only when one is configured.
- **No schema in the plugin.** Storage can change without a plugin release;
  the plugin and the server only have to agree on the HTTP contract, and a
  run stored locally is validated and written by the very code a server
  would use, so it reads back the same.
- **Few writers per database.** CI jobs, xdist sessions and developers report
  to a server, never to its database. With SQLite one server process
  serialises writes on one connection, and nobody shares a database file
  across machines or network filesystems. With PostgreSQL several servers
  may share one database, and its transactions keep their writes apart. A
  local database is written by the sessions on one machine, each opening it
  once, at its end, and by nothing else; SQLite's own locking keeps them
  apart, and a `vantage` serving the file meanwhile is one more process on
  it.
- **Liveness needs a listener.** Because the server is told when a session
  starts and hears from it while it runs, it can tell a running session from
  one that was killed. A local run is stored whole at the end, so a killed
  session leaves nothing there.

Recording to a server needs one the test machine can reach; the local modes
are for when there is none, or when a run should outlive an outage.

## The HTTP contract is the compatibility boundary

The plugin and the server are released separately, so an old plugin meets a
new server and the reverse. The versioned API is the only thing they share:
every route is under `/api/v1`, and nothing answers an unversioned path.
The one place they meet in-process is the test machine, where the local
modes call `vantage.local` and `vantage push` calls `pytest_vantage.outbox`;
there both come from one install, since `vantage` depends on
`pytest-vantage`.

The report schema tolerates skew where skew is expected and rejects it where
it signals a bug:

- **The envelope ignores unknown sections.** A newer plugin sending a section
  an older server does not know still gets its run recorded.
- **`run`, `vcs` and `metadata` reject unknown fields.** A field the server
  does not know inside one of these means client and server disagree about
  what the section is, and the report is refused with a `422`.
- **Result entries accept unknown keys** and name them back in the
  acknowledgement's `ignored` list, so drift stays visible without losing
  the session.
- **Fields added to results are optional**, defaulting to their absent
  shape, so a plugin that predates them still validates.

So a new field cannot be added to `run`, `vcs` or `metadata` under `/api/v1`
without every existing server rejecting reports that carry it. Add an
optional result key or a new sibling section instead. The one exception is
`metadata`'s `keys` and `values`, added before either distribution was first
released, so no server that rejects them was ever published. The plugin
leaves both out when they are empty, so a session that uses neither is still
accepted by a server built before them.

Features the server may lack are negotiated, not assumed. The plugin asks
`GET /api/v1/capabilities` before recording; only an explicit
`"session_lifecycle": true` turns on the start report and heartbeats. Any
other answer, the `404` of a server without the route included, makes the
plugin skip both and send only the finish write, with one warning.

The OpenAPI document, `packages/vantage/src/vantage/service/openapi/v1.yaml`,
is written by hand and served at `GET /api/v1/openapi.yaml`. FastAPI's
generated documents are disabled: a document generated from the routes can
never disagree with them, so it can never catch a change nobody meant to
make. `packages/vantage/tests/test_interface_document.py` compares the
document with the route table and with the Pydantic models, in both
directions: paths, status codes, properties, required fields, nullability,
enums and `additionalProperties`. It does not compare JSON types, formats,
patterns or lengths.

## Session lifecycle

1. **`pytest_configure`, on the controller.** An invocation that never runs
   a test (`--collect-only`, `--fixtures` and the other options in
   `_NO_TEST_OPTIONS`) stops here; a session whose selection is merely empty
   does not, and is recorded with exit status 5. Opt-in switches that were
   not typed on the command line are reported in one warning and ignored. The
   mode is resolved, then what it uses of the address, the timeout and the
   local database, and a bad value is a usage error; so is a mode with a
   local database where `vantage.local` cannot be imported. `local` mode
   builds the `Recorder` at once and touches no network, then or later.
   Otherwise a bare TCP connect checks that something listens at the
   address; if nothing does, `server` mode warns once and the session runs
   unrecorded, while the modes with a local database record it with the
   lifecycle off (no start report, no heartbeats) and say what happened only
   at the finish. A capability probe asks for `session_lifecycle`, and the
   `Recorder` is built: a random run id, the start time, one git read (five
   seconds in total), the metadata declaration and, if asked, the files it
   names.
2. **`pytest_sessionstart`: the start report.** A `POST /api/v1/runs` with the
   run's id and start time, `finished_at` and `exit_status` null, the
   `project` (every report names it, `default` included), the `vcs`
   section, `metadata` when the declaration declares keys or files were
   read, and no results. The row exists from this moment, so a session
   killed later still leaves a trace.
3. **The heartbeat, and `pytest_runtest_logreport`: accumulate.** Once the
   start report got through, a daemon thread (`Heartbeat`, registered as a
   plugin of its own) sends `POST /api/v1/runs/{id}/heartbeat` every 30
   seconds until the session finishes, whatever the tests are doing: a test
   quieter than the grace period keeps its run `running`, and so does one
   that hangs, since a beat says the process is alive, not that tests are
   moving. The thread never warns, since warnings belong to pytest's main
   thread. At its first failure it stops and keeps the exception, and the
   `Recorder` raises it at the next test report or at the finish, under
   `liveness_isolated`: one warning. `Heartbeat.pytest_sessionfinish` is
   `tryfirst`, so the thread has stopped, a beat in flight included, before
   the finish report is sent, even when the `Recorder`'s own hooks have
   latched off; `pytest_unconfigure` stops one whose session never
   finished. Every setup, call and teardown report is kept in memory, keyed
   by node id and xdist worker.
   `pytest_collectreport` keeps each collector that failed, by node id; it
   becomes one `error` result with no phases in the finish write, since
   pytest counts it as an error and a run must not read as all passing
   without it. Under xdist the controller collects nothing, and xdist calls
   this hook on it once per distinct failure the workers report.
4. **`pytest_sessionfinish`: the finish write.** The results are assembled
   and the failure-text budget spent. If the results do not fit the server's
   1 MiB body cap in one report, they are split into slices that do: every
   slice but the last goes in an in-progress report (`exit_status` null), and
   the last goes in the finishing report. A report lost on the way leaves the
   run unfinished, never finished with results missing. A single result too
   large for any report is left out and counted in a warning. The values the
   session reported through the `vantage_metadata` fixture go in the
   finishing report alone, planned against the declaration first (`absent`
   rows, the entry bound, the warnings); when the last slice leaves no room
   for them, it goes out in progress and the finishing report carries no
   results. The reports are built once, then sent, stored locally, or both,
   as the mode says (see [Where a finished run goes](#where-a-finished-run-goes)).

How the session ended decides the finish fields. Ctrl-C and `pytest.exit()`
send no finish time, `interrupted: true` and the reason. pytest's internal
error (exit status 3) sends no finish time and `interrupted: false`.
Everything else, pytest's own early stops with exit status 2 included, sends
a finish time.

On the server, the first report of a run creates it (`201`) and every later
one answers `200`. The finish is applied once, over a run not yet finished;
results accumulate until then, first write wins, and a report reaching a
finished run stores nothing; the last-contact time is set when the
run is created and advanced by heartbeats. A run's presentation is derived at
read time, never stored: `finished` when it has a finish time, `interrupted`
when a finish report arrived without one, `abandoned` when nothing was heard
for the grace period (900 seconds by default, thirty heartbeat intervals),
otherwise `running`.

**xdist.** Only the controller builds a `Recorder`; a `Recorder` per worker
would record one session as several runs. The exception behind a failure
exists only in the process that ran the test, so each worker registers an
`EvidenceCollector` that attaches failure text to the report before xdist
sends it to the controller; it does the same for a collector that failed,
with pytest's collection text. Reports are grouped per worker, so one result is
never stitched together from two workers, and under `--dist each` the most
severe execution of a node id is recorded.

Some things only a worker knows, and it tells the controller through
`workeroutput`, which xdist hands over as the worker's session finishes
(`Recorder.pytest_testnodedown`). `WorkerInterruptRelay` says whether Ctrl-C
or `pytest.exit()` interrupted the worker, and why. `WorkerMetadataRelay`
holds the mapping the `vantage_metadata` fixture hands the worker's session
fixtures, which run on every worker and never on the controller, and relays
its values. A worker cannot tell from `--vantage` alone whether its
controller records, so the `Recorder` says so the other way, in each
worker's `workerinput` (`pytest_configure_node`); without that word the
worker registers no metadata relay and its fixture stays silent. execnet
refuses a `str` subclass and any text holding a lone surrogate, and either
would crash the worker as it finishes, so keys and values are plain `str`
from the moment they are set and cross as ASCII-escaped JSON text. The
controller merges the values in the order it receives them, keeping the
first value of a key two workers disagree on, with one warning. A Ctrl-C
ends the controller's event loop at once, before any worker has finished,
so an xdist session interrupted that way is reported without its workers'
values; `pytest.exit()` in a worker ends that worker first, and its values
arrive.

**Reruns and subtests.** A new setup report starts a new attempt, so a rerun
plugin's last attempt is the one recorded. Subtest reports are kept apart
from the call report, and a failed subtest fails its test.

## Where a finished run goes

`Recorder.pytest_sessionfinish` builds the finish reports once and delivers
them by mode (`config.MODES`):

| Mode | At the finish |
| --- | --- |
| `server` | every report to the server, as it always did |
| `local` | every report to `vantage.local.store_reports`; a failure warns that the run is lost |
| `server+backup` | every report to the server; if one fails, the whole run stored locally, and queued if a retry could succeed |
| `server+local` | every report to the server, and the whole run stored locally regardless; queued as in `server+backup` |

**Local storage gets the reports a server gets.** `vantage.local` encodes
each report with `json.dumps`, as the transport does, decodes it as the
server decodes a body (lone surrogates and U+0000 replaced), and hands it to
`ingest`; the sliced reports go in one by one, so a local copy holds exactly
the rows a server holds. The one difference is the project: a server never
makes one from a report, but `ingest(..., create_missing_project=True)`
makes the project a report names in the local database, whose only admin is
its owner, so the local copy of a run a server refused for its project
still lands. The local database is opened once, at the finish,
by the xdist controller alone, and closed again: nothing holds it open while
tests run.

**What is queued.** Sending stops at the first failed report. A failure a
later attempt can fix -- no connection, a broken one, a timeout, a 5xx, a
408 or 429 asking for the request again later, a 401 or 403 refusing the
token, a `404 unknown_project` an admin can fix by adding the project, a
`403 not_a_member` or `insufficient_role` an owner of the project or an
admin can fix by making the token's user an editor of it
(`outbox.worth_retrying`; the transport raises `ProjectRefusedError` for
the last two) -- queues that report and the ones after it; the reports the server
acknowledged are not queued again, and a replay of any of them would change
nothing anyway. Any other 4xx, a redirect or an answer that does not
acknowledge the run would fail the same way next time and is not queued. A server unreachable at the start queues every report. The session
then warns once, saying where the run is: stored, queued, both, or lost.

**The outbox** (`pytest_vantage/outbox.py`) is the plugin's own SQLite file
beside the local database, `<database>-outbox`, written with the standard
library's `sqlite3`; it is not a vantage database. One row per queued run:
the server address as configured, the run id, the report bodies in send
order as JSON text, when it was queued, attempts and the last error.

- The file is created 0600 before `sqlite3` opens it, in a directory created
  0700 if missing; an existing directory keeps its mode. A `PRAGMA
  user_version` stamp marks it, so some other SQLite file at the path is
  refused untouched.
- At most 1,000 runs and 256 MiB of reports. Queueing past either deletes
  the oldest rows first, in the same transaction, and the plugin warns with
  their run ids; a run larger than the whole bound is refused.
- `send_queued(outbox, server, timeout=..., budget=...)` sends one server's
  runs, oldest first, and only runs queued for exactly that address. Each is
  claimed in a `BEGIN IMMEDIATE` transaction by writing `claimed_until`, for
  as long as sending it can take plus a minute, so concurrent senders -- two
  sessions, or a session and `vantage push` -- take different runs, and a
  claim left by a killed sender lapses; one interrupted with Ctrl-C gives
  its run back at once. An acknowledged run is deleted; one refused with a
  4xx other than 408 or 429 is deleted and named in the summary, and so is
  one whose reports no longer decode as a list of objects; a 5xx
  releases it with its attempt counted and moves on, since it may be that
  run's own problem; no answer, a 408 or 429, a redirect or a stranger's
  answer releases it and stops, and so does a spent budget, without counting
  an attempt. A duplicate send is harmless: the server's writes are
  idempotent.
- A session whose own run reached its server sends that server's queue
  within the report timeout and prints one line in `pytest_terminal_summary`
  (or at unconfigure when there is no summary): under xdist the progress
  line is still open as the session finishes. It never creates the outbox
  just to find it empty.

**`vantage push`** (`service/push.py`) is the same `send_queued` on demand,
with no budget beyond each report's timeout, for every server in the outbox
or one given with `--to`. `cli.main` hands it the arguments when the first
is `push`, before anything imports FastAPI or Uvicorn, so it runs in an
install without the `server` extra; it finds the outbox from `--database` or
`vantage.local.default_database_path()`, and never creates one. `vantage`
names no floor for `pytest-vantage`, so an older plugin with no outbox is
refused in one line naming the upgrade.

## Failure isolation in the plugin

pytest imports `plugin.py` in every session, recording or not, so it
implements only `pytest_addoption`, `pytest_configure` and the
`vantage_metadata` fixture, which runs only when a test asks for it. The
recording modules are imported, and the `Recorder` registered, only once
`--vantage` is typed; a test checks that an unactivated session loads
nothing beyond the plugin's entry module and its configuration and warning
helpers, and another that a session recording to a server alone never loads
`pytest_vantage.local`, `pytest_vantage.outbox` or `vantage.local`. A
session that is not recorded gets a fixture mapping nobody reads, so a
project's fixtures run the same either way.

Every `Recorder` hook runs behind a decorator from `boundary.py` that catches
`Exception` (never `BaseException`, so Ctrl-C still stops the run), warns
once and returns. Nothing in the plugin assigns the session's exit status.
Three decorators keep three independent flags, so one kind of failure cannot
switch off another:

| Decorator | Wraps | After a failure |
| --- | --- | --- |
| `fault_isolated` | the report header and the finish write | every later wrapped hook is skipped |
| `liveness_isolated` | the start report, and reporting a heartbeat's failure | heartbeats stop; the finish write is unaffected |
| `accumulation_isolated` | accumulating test and collection reports, recording an interrupt | warns once, keeps accumulating |

- `EvidenceCollector` wraps `pytest_runtest_makereport` as a hookwrapper,
  which pluggy forbids from returning before its `yield`, so it has its own
  `try` after the `yield` and its own flag. Each failure field is extracted
  in its own `try`: a `__repr__` that raises costs that one field.
- The git and metadata reads never raise. Each returns an empty section and
  at most one warning.
- `SessionMetadata`, the fixture's mapping, never raises into the code that
  sets a value: a key or value it cannot record is skipped with one warning,
  located at the first frame outside the plugin, the project's line that
  set it. Planning what the finishing report sends of the values has its own
  `try`, so a failure there costs the run its values, never its finish.
- `pytest_configure` has no decorator; a failure building the `Recorder` is
  caught there and leaves the session unrecorded.
- Storing locally, queueing and sending the queue each catch `Exception`
  themselves, inside the finish write, so a local database or an outbox that
  cannot be written costs its own part and never the server's copy. What
  went wrong goes into the session's one warning about where the run is.
  `pytest_vantage/local.py` turns anything `vantage.local` raises into a
  one-line `LocalStoreFailedError`, and the outbox's own failures are a
  one-line `OutboxError`.
- Warnings are `VantageWarning`, a `UserWarning` rather than a
  `PytestWarning`, so a project's `-W error::pytest.PytestWarning` does not
  turn them into errors. When the active filters raise it anyway, the message
  goes to the terminal reporter, or to stderr.
- pytest records warnings only inside its collection, test-protocol,
  session-finish and terminal-summary hooks. Anywhere else -- the plugin's
  `pytest_configure`, `pytest_sessionstart`, the xdist controller's loop --
  `warn` issues the warning through `config.issue_config_time_warning`, as
  pytest issues its own configuration warnings, so it reaches the summary
  and the project's filters apply to it. `configuring` marks the plugin's
  `pytest_configure`, which a session without `--vantage` runs too, so it
  registers nothing; `WarningPhase`, registered with the `Recorder`, marks
  `pytest_sessionstart` and the test loop, but not each test inside it. Both
  set a context variable for the length of one hook, so nothing outlives it,
  and a session pytest runs inside another keeps its own. With pytest's
  warnings plugin blocked there is no summary, and the warning is printed
  to stderr.

The transport (`transport.py`) uses an `urllib` opener with only the HTTP and
HTTPS handlers. It follows no redirect, because `urllib` would resend a
redirected POST as a bodiless GET, possibly to another scheme. It uses no
proxy: the reachability check connects directly, and honouring `http_proxy`
would send every report, failure text included, to a host that check never
saw. The timeout is a deadline on the whole exchange: the request runs on a
daemon thread that is abandoned at the deadline, so a server trickling bytes
cannot stretch it. Abandoning only the client's side means a report that
timed out may still be stored by the server; the plugin cannot know. The
answer is read to at most 64 KiB and must acknowledge this run's id with
`created` or `duplicate`, or the report counts as failed.

## Projects

Every run belongs to one project, named by the top-level `project` of the
report that creates it (absent or null: `default`) and fixed there: the run
upsert never sets it on conflict, and a later report naming another project
raises `ProjectMismatchError` (`409 project_mismatch`), checked after the
recorder and before whether the run is finished, so a replay naming another
project is refused rather than answered as a duplicate. `project` sits in
the envelope because `run` refuses an unknown field.

- **Keyed by name, like users.** A `project` row is written by `vantage
  project add`, `POST /api/v1/projects` and the local store, never by a
  server from a report (`UnknownProjectError`, `404 unknown_project`).
  `default` is written in the transaction that creates and stamps the
  database. Nothing renames or deletes a project, so "it exists" only ever
  turns true: a route can look one up and then act, and every row naming
  one keeps naming one.
- **What is per project.** The catalogue is unique on `(project, node_id)`,
  and a report resolves its results' catalogue rows within its own project,
  so two projects never share a test however alike their node ids; a
  test's history, the run list with its metadata filter, horizon and
  cursor, the section definitions (`project_setting`) and the members
  (`project_member`) are each read within one. Everything addressed by a
  run id is not: a run id names one run across projects, and
  `get_run_detail` says which project it is in, which the caller's role is
  then checked in.
- **The port** takes `project` as a required keyword wherever a call reads
  or writes within one, so no call silently falls back to `default`; the
  one default is ingestion's, for an absent field.
- **Routes.** The run list, a test's history, the sections and the members
  live under `/api/v1/projects/{project}/`. Their dependencies
  (`_project_access` in `service/access.py`) authorize first, then answer a
  name no project can have without asking the store, then look the project
  up, then check the caller's role in it (see
  [Users, tokens and who may do what](#users-tokens-and-who-may-do-what)).
  `POST /projects` needs an admin, and adds no member: an admin acts as
  the owner of every project without one.
- **The plugin** resolves `--vantage-project`, `VANTAGE_PROJECT`, then the
  `vantage_project` ini value, as it resolves the address, and names the
  project in every report and in its header. The outbox keeps a run the
  server refused for its project -- one it does not have, or one the
  token's user may not record in -- and `send_queued` passes over the rest
  of that project's runs within one call, reporting it in
  `missing_projects` or `forbidden_projects`. Neither stops the send: each
  refuses that one project, and the same token may still record in
  others.

## Users, tokens and who may do what

A database with no user is open: every route but the users, tokens,
members, login and password ones answers anyone, and checks no role, as
the server did before it had users.
Only a database pytest-vantage's local store made is ever served that way,
and only until its first `vantage user add`: `vantage` gives any other
database a user before serving it. The first user closes a database. Users
are disabled, never deleted, so "a user exists" is a latch: once true it
stays true, and every `run.recorded_by` keeps naming an existing user.

- **Who made the database.** `meta.origin` is `local` when `vantage.local`
  created the database, and `server` when anything else did: `vantage`,
  `vantage user add`, `vantage project add`, and every PostgreSQL database,
  which the local store never opens. `vantage.local` alone opens with
  `SqliteExecutionStore(path, local=True)`, which hands the flag to
  `open_database`; it is the adapter's constructor argument, not part of the
  port, and `InMemoryExecutionStore` mirrors it. The row is written with
  `INSERT OR IGNORE` in the transaction that creates the tables, after the
  version stamp, so opening an existing database with either flag never
  changes it, and of two processes creating one file the first one's value
  stays. Both kinds of creator write it, so `server` is a row, not a
  missing one. Only `create_first_admin` reads it, in SQL, and anything
  but `local`, no row included, counts as the server's: an odd or
  hand-edited database gets an admin rather than staying open. Nothing
  else could decide it: the server's default path is the local store's,
  and a copied file keeps its row but not its path; a fresh server
  database has no user either; and a row the local store wrote after
  opening would let a server starting in between give a test machine's
  file an admin.
- **The first admin.** `cli.main`, once the port and the store are held and
  before it warns about the bind or builds the app, calls
  `create_first_admin` (`service/cli.py`). When `access_required()` is
  false, it hashes a fresh `new_password()` and asks the store to
  `create_first_admin("admin", ...)`: one conditional insert, creating the
  enabled admin only while the database has no user and is not `local`.
  Of two servers starting on one empty PostgreSQL database, the second
  waits on the first one's row and inserts nothing, so one admin is made
  and one line printed. The line goes to stderr, flushed, before uvicorn's
  first, and only once the insert has committed, so a printed password is
  always the stored one; never through `logging`, whose handlers, uvicorn's
  configuration or an embedding could copy it anywhere. A store error is
  refused in one line, `cannot create the user admin in <database>:
  <detail>`, the URL redacted and the driver's message through
  `_driver_detail`. `create_app` never makes a user, so an app built on a
  userless store -- the tests', an embedding's -- is as open as ever; its
  one production caller, `cli.py`, never serves a database of the server's
  own without a user. A local database with no user pays one discarded
  hash per start, about 0.2 s. A crash leaves one of three states:
  - before the schema commits, nothing, and the next open creates it;
  - after the schema and before the admin, a `server` database with no
    user that nothing has served, since only `main` serves, and only after
    this step: the next start creates `admin` and prints its line;
  - after the admin commits and before the line is out (or with stderr
    broken), `admin` with a password nobody saw, which no later start
    prints, since a user exists: `vantage user password admin` recovers it.

  A user made before the first start means no `admin` and no line, which is
  how to keep a password out of the log. `vantage user add` during the very
  first start on PostgreSQL can leave both users, and a valid printed
  password; that is accepted rather than an advisory lock on `create_user`.
  An `admin` demoted or disabled later is never made again.
- **Passwords** (`core/domain/passwords.py`) are stored only as a scrypt
  hash in PHC string form, `$scrypt$ln=15,r=8,p=3$<salt>$<key>` (a 16-byte
  salt and a 32-byte key in unpadded base64), of the password
  NFKC-normalised and encoded in UTF-8, so the same password typed on
  another keyboard or input method matches. Unlike a token, a password may
  be guessed, so a check is expensive on purpose: n = 2^15 and r = 8 take
  32 MiB, and p = 3 triples the work without taking more memory, so each
  check costs a guesser what n = 2^17 would at a quarter of the memory,
  about 0.19 s. OpenSSL's default memory bound refuses these parameters by
  a hair, so `maxmem` is set to what they need. The parameters travel in
  the string: a later cost change needs no schema bump, old hashes keep
  verifying with no rehash at login, and tests store cheaper ones
  (`password_fixtures`). `verify_password` never raises and runs exactly
  one scrypt whatever it is given: with no hash, a malformed one, one
  asking for more than 128 MiB or a parallelism above 16, or a password
  UTF-8 cannot encode, it checks against `_UNUSABLE`, a well-formed hash at
  the current cost whose random key no password matches, made at import
  without running scrypt, so how long an answer takes tells nobody which
  names exist. `check_password` (15 to 256 characters once normalised, no
  control character or lone surrogate, the test `plain_character` shares
  with token labels) applies when a password is set, never at login, so a
  rule change locks nobody out. `User` says `has_password`, never the
  hash, which only `get_password_hash` hands out, and only for an enabled
  user. There is no per-attempt write, lockout, counter or delay: a write
  per failed attempt would let anyone grow the database, a lockout would
  let anyone lock `admin` out, and a delay is absorbed by attempts sent in
  parallel. The cost is the bound instead: two checks at a time per
  process, about ten guesses a second, and uvicorn's access log records
  each `401` with its address.
- **Login tokens.** `POST /login` (`service/routes/login.py`) checks the
  password and hands the hash it verified to `create_login_token`: one
  write that inserts a token labelled `login`, holding `read`, `manage` and
  the account's `admin`, never `record`, and expiring `LOGIN_TOKEN_LIFETIME`
  (12 hours) later -- only while the user is enabled and the stored hash is
  still that one, so a login never outlives a password change made while
  it was checked. The same write deletes that user's login tokens expired
  by then, which keeps the unpaged token lists short; a made token's row is
  never deleted. A login token is simply a token with an `expires_at`,
  which nothing else sets, and `authenticate(digest, *, now)` refuses one
  from that moment; `now` is required, since a default could skip the
  check unnoticed. `set_password` sets the hash and revokes every login
  token of the user not yet revoked, in one write, leaving made tokens
  alone; with `replacing` it is a compare-and-set against the hash the
  current password was checked against, so a `POST /password` that lost
  to a change made meanwhile changes nothing and answers `401`. `POST
  /password` takes the current password rather than a token, so a stolen
  login token cannot take the account over. `PUT /users/{name}/password`
  and `vantage user password` set a password with no `replacing`: an admin
  can already make admin tokens, and whoever can open the database is
  trusted with it.
- **Two password slots, 32 waiting.** Every scrypt the server runs -- a
  login's check, a password change's check and hash, an admin's hash -- is
  one `run_in_threadpool` call made while holding one of
  `app.state.password_slots` (`service/slots.py`), two slots awaited on
  the event loop. A waiting login holds no worker thread, at most two of
  the forty threads and 64 MiB hash at once, and reports and heartbeats
  never wait behind a flood of logins. At most 32 more requests wait; one
  past that is refused at once, `503 password_checks_busy` with
  `Retry-After: 1`, since a client that sends a body and leaves costs the
  server a queued body and a future hash while holding no connection. A
  request that gets a slot first asks whether its client is still there,
  and hashes nothing if not. The count changes only on the event loop, so
  it needs no lock; the semaphore binds to the loop the first time a
  request waits on it, and uvicorn runs one.
- **Tokens.** `new_token` is `vantage_` and 32 random bytes; the store keeps
  its SHA-256 (`token_digest`) and finds it by that digest, through the
  column's unique index. A token has nothing to guess, so a fast hash is
  enough, and nobody can choose a digest's text, so the lookup needs no
  constant-time comparison. A token is shown once, by whatever made it.
- **Scopes.** A token holds one or more of `read`, `record`, `manage` and
  `admin` (the `can_*` columns). `Grant.allows` needs the admin scope and
  an admin user: `authenticate` reads the user's standing each time, so
  demoting a user takes their admin tokens' power at once. `manage`, which
  changes a project's section definitions and members, needs no admin:
  any user's token may hold it, and the user's role in each project bounds
  it. So a token made for pytest-vantage (`DEFAULT_SCOPES`, `read` and
  `record`) never changes sections or members, and an admin can make a
  token that edits sections without it managing users. A revoked token,
  an expired one, and any token of a disabled user authenticate nothing.
- **Roles.** Once a database has users, a user acts in a project with a
  role, `viewer`, `editor` or `owner`, each allowing what the one before
  it does and more (`core/domain/projects.py`): a viewer reads the
  project, an editor also records runs into it and changes its section
  definitions, an owner also manages its members. A role is a
  `project_member` row, but for two rules no row holds, stated once, in
  `effective_role`: every user is an editor of `default`, which takes no
  rows, so a server with one team needs no membership at all; and an
  admin acts as an owner of every project, so one can always put a
  project right. The admin standing is the one `authenticate` read for
  this request: a demoted admin loses the override at their next request,
  and only their rows count then. A disabled user's rows stay, and apply
  again once they are enabled. A request needs its route's scope and,
  within a project, a role covering the one the route needs
  (`role_covers`): scopes and roles only narrow each other, so an owner's
  read-only token only reads, and an admin's `read` and `record` token
  reads and records everywhere. The anonymous caller of an open server
  passes every role check.
- **`service/access.py`** is the dependency every route declares but
  `/capabilities` and `/openapi.yaml`, which a client asks before it can
  know it needs a token, and `/login` and `/password`, which take a name
  and a password instead. A route outside any project declares a scope
  alone: `requires_read` on `GET /projects`, `requires_record` on a
  report, `requires_admin` on adding a project, and `requires_admin_token`
  on the users and tokens routes, `PUT /users/{name}/password` included.
  A route within one declares its scope and the role it needs there (see
  the next point). Each is a plain `def`, since it reads the store, so on
  `POST /runs` it runs in the threadpool before the body is read. While the
  server is open it asks the store whether a user exists on every request,
  because `vantage user add` may run against the database meanwhile; once
  one does, `app.state.access_required` keeps the answer, and the store is
  not asked again. A token sent to an open server is refused, not ignored.
  A 401, or a `403 insufficient_scope`, carries RFC 6750's
  `WWW-Authenticate` challenge (`ChallengeError`), naming the scope that
  was missing and never the token; a refusal of a role carries none, since
  no other token of the same user would help. `/login` and `/password`
  declare `requires_closed_server` instead, a plain `def` too, which
  answers `409 open_server` on an open server before the body is read and
  ignores an `Authorization` header: nobody has a password there.
- **Where the role is checked.** `require_role` refuses a caller with no
  role in the project, `403 not_a_member`, and one whose role is below
  what is needed, `403 insufficient_role`; neither body names the project
  or a member. Only a member row needs the store: an admin's role and
  every user's in `default` are `effective_role`'s alone. Three places
  call it:
  - `_project_access(scope, role, *, needs_user=False)` builds the
    dependency of every route with `{project}` in its path:
    `requires_read_project` (read, viewer: the run list, a test's history,
    the sections), `requires_edit_project` (manage, editor: changing
    sections), `requires_read_members` (read, viewer) and
    `requires_manage_members` (manage, owner). It authorizes, refuses an
    open server's anonymous caller with `409 open_server` where
    `needs_user` says so, answers a name no project can have without
    asking the store, looks the project up, checks the role, and returns
    the `Project`.
  - `_run_access(authorizes, role)` builds the dependency of every route
    with `{run_id}` in its path: `requires_read_run` (viewer: a run's
    detail, metadata, results, one result and section summary) and
    `requires_record_run` (editor: the heartbeat). It takes the caller as
    a sub-dependency and the run id as its own path parameter: FastAPI
    resolves sub-dependencies before a dependant's own parameters, so a
    caller refused for who they are is never told the id is malformed,
    whereas authorizing inside its body would answer the `422` first. It reads
    `get_run_detail` (`404 unknown_run`), checks the role in the run's
    project and returns the `RunDetail`. A route taking it declares no
    `run_id` of its own, which would report a malformed id twice, and
    reads the id from the detail. This is where a check that a run is
    shared with the caller, or private to its recorder, would go.
  - `POST /runs` learns its project only from the report:
    `ingest(..., admit=...)` calls `admit` with the report's project once
    the report is valid and converted, before anything is made or
    recorded, and whatever it raises passes through with nothing stored.
    The route's `admit` answers `404 unknown_project` for a project the
    store lacks, then checks the editor role. `record_session`'s own
    `UnknownProjectError`, `ForeignRunError` and `ProjectMismatchError`
    stay behind it, so a sender who may not record in a project never
    learns through a `409` of a run in it, or of one with the same id in
    another; an editor of the project still does. `vantage.local` passes
    no `admit`.
- **Every refusal of who asks comes before every refusal of what is
  asked.** Within a project path: `401`, `403 insufficient_scope`, on the
  members routes `409 open_server`, `404 unknown_project`,
  `403 not_a_member`, `403 insufficient_role`, then the route's own checks
  of its body, query and path. Within a run-id path: `401`,
  `403 insufficient_scope`, `422` for `path.run_id`, `404 unknown_run`,
  the role, then the query (`limit`, `offset`, `node_id`) and the route's
  own `404 unknown_result` or `409 foreign_run`. The run is resolved
  before its query is validated, as a project is, so `?limit=x` on a run
  the caller may not read is a `403`, and on one never recorded a `404`.
  A run id the plugin makes is a random `uuid4`, so answering
  `unknown_run` before a `403` reveals nothing guessable. `POST /runs`:
  `401` and `403 insufficient_scope` before the body is read, `415`,
  `413` or `400 incomplete_body`, `400 invalid_json`, `422 invalid_report`,
  `404 unknown_project` (`fields: ["project"]`), `403 not_a_member` or
  `insufficient_role`, `409 foreign_run`, `409 project_mismatch`. The
  heartbeat: `401`, `403 insufficient_scope`, `422`, `404 unknown_run`,
  the role, `409 foreign_run`.
- **Roles are read on every request**, never kept in `app.state`:
  `access_required` can be, since it is a one-way latch, and a membership
  is not. A change applies from the next request on every server process;
  a request already authorized finishes on the role it read, as one
  authorized just before its token was revoked does. `GET /projects`
  reads the caller's rows (`list_memberships`), then the projects, and
  lists each one `effective_role` gives them a role in, with that role;
  every row names a project that exists and is never deleted, so each is
  in the later list, and a row changed between the two reads gives the
  answer a moment earlier or later would. An admin gets every project as
  `owner`, and an open server's anonymous caller every project with a
  null `role`.
- **No last-owner guard.** Nothing stops an owner demoting or removing
  themselves, or the last owner: an admin acts as an owner of every
  project and can put any one right over HTTP, `vantage project member`
  does it on the database, and every new project starts with no owner
  anyway. A guard would be a count-then-delete needing a lock on the
  project row, to prevent a state that is already recovered from.
- **Who recorded a run.** `record_session` takes `recorded_by`, the
  caller's user or `None`. The report that creates a run stores it, and
  every later report must come from the same caller, or the store raises
  `ForeignRunError` before looking at whether the run is finished, and
  writes nothing; the route answers `409 foreign_run`. The run id is the
  client's, so this is what keeps one user from finishing another's run.
  The heartbeat's `requires_record_run` reads the run's detail first, with
  its recorder and project, neither of which changes once the run exists,
  so the checks race nothing.
- **Managing them.** `vantage user`, `vantage token` and `vantage project
  member` (`service/manage.py`) resolve and open the database as the
  server does, never import the web framework, and create a database only
  for `user add`. `token create` prints the token alone on stdout. `user password`
  never takes a password from an argument or the environment, which `ps`
  and shell history would show: it asks twice through `getpass`, and only
  on a terminal, since with stdin a pipe `getpass` would read the terminal
  behind it or echo where there is none; or it reads stdin, less one
  trailing newline, with `--password-stdin`. `project member` checks the
  names, and refuses to set or remove a member of `default`, before it
  opens the database; `set` points out a disabled user, whose role grants
  nothing until they are enabled, and an admin, whom a role changes
  nothing for. None of these commands asks for a token or a role: whoever
  can open the database acts with full authority, which is how a server
  with no admin left, or a project with no owner, is recovered.
- **Managing them over HTTP.** `service/routes/users.py` gives an admin
  what the two commands give, with the same domain checks in the same
  order. `requires_admin_token` is `requires_admin` that also refuses the
  anonymous caller an open server lets through, with `409 open_server`: the
  one thing it could do there is make the first user, and whoever asked
  first would own the server. It is refused for who it is, never after
  asking the store again, so a request racing the first `vantage user add`
  cannot mint that user a token or list its name; reads are refused too.
  The dependency runs before the path is validated and before the body is
  read, so nobody else learns which names or ids exist. An admin cannot
  demote or disable their own user here (`409 own_account`), decided by
  who asks and so racing nothing; the command line, unguarded, is how to
  recover when no admin is left. Revoking is idempotent: the conditional
  `UPDATE` keeps the first revocation time, and `get_token` reads the row
  back, since a revocation is never undone. The one row that read can miss
  is an expired login token's, deleted by its user's next login, before
  the revocation or after it: the token is then unknown, `404
  unknown_token`, as it would be to anyone. The two answers carrying a
  token, the `201` of `POST /tokens` and of `POST /login`, are
  `Cache-Control: no-store`, and `CreatedTokenResponse` keeps the token out
  of its `repr`. The password request models keep passwords out of their
  `repr` and hide their input in validation errors, whose text otherwise
  repeats the whole body of one missing a field, and the routes raise
  their `422` `from None`. A value that cannot be a name matches no user
  without asking the store, which keeps U+0000 from every adapter.
- **Members over HTTP.** `service/routes/members.py` gives an owner what
  `vantage project member` gives. The three routes refuse an open
  server's anonymous caller with `409 open_server`, as the users routes
  do: nobody is a member of anything until users exist. `default` takes
  no rows, so setting or removing one there is `409 default_project` for
  an admin, and `403 insufficient_role` for anyone else, whose role there
  is editor. `PUT` then checks, in order: the media type, `default`, a
  `{user}` no user can have (`404 unknown_user` with `fields: []`, before
  the body is read and without asking the store), the body (`413`,
  `400`), its shape (`422 invalid_member_request`), the role
  (`422 invalid_role`, from `check_role`, so it has a code of its own),
  and last the store: `201` when `set_member` added the row, `200` when
  it set an existing member's role, the same one included, and
  `404 unknown_user` for a user nobody has. `DELETE` answers
  `404 unknown_member` alike for a name nobody can have, a user nobody
  has and a user who is not a member, so a repeated one is a `404`, as a
  section's is. `unknown_user` tells an owner which names exist, which
  adding a colleague by name needs; the list shows a viewer user names,
  which every run's `recorded_by` shows them anyway. Every write is one
  store call, and the only read before it, the caller's role, is stale by
  one request at most, as a token's standing is.
- **The plugin** reads its token from `VANTAGE_TOKEN` alone, only once
  `--vantage` is typed; a committed ini file or a command line would show it
  to others. It goes in `Authorization` on every report and heartbeat, never
  on the capability probe, and the transport follows no redirect, so it only
  ever reaches the configured address. A vantage server's 401, 403 or 409,
  recognised by the error its body names, becomes an `AccessRefusedError`,
  still an `HTTPError` with that status, whose message says what to fix.
  A `403 not_a_member` or `insufficient_role` to a report becomes a
  `ProjectRefusedError`, as a `404 unknown_project` does, its `error`
  saying which, naming the project and the `vantage project member set`
  that lets the user record there; to a heartbeat, which names no project,
  an `AccessRefusedError` saying the user may no longer record in the
  run's project, raised only when a token was sent. A 403 with any other
  code, a proxy's page say, stays a plain `HTTPError`.
  `ReportSettings`' `repr` leaves the token out.
- **The outbox never holds a token.** A 401 or 403 is worth retrying
  (`outbox.worth_retrying`): the reports are queued, and a sender with
  another token -- `vantage push` reads `VANTAGE_TOKEN` too -- can deliver
  them. A sender refused that way stops, as for an unreachable server,
  since every run would be refused alike; a 409 drops the run, since no
  token but its recorder's will ever be taken for it. A
  `403 not_a_member` or `insufficient_role` refuses the token's user one
  project, not the token: the run is kept and the send goes on, passing
  over that project's runs (see [Projects](#projects)).

## Request handling and concurrency

`create_app(store, grace_period_seconds=900)` builds the app, mounts the
`runs`, `read`, `capabilities`, `sections`, `users`, `projects`, `members`
and `login` routers under `/api/v1`, and
registers the error handlers. The `vantage` command runs it under Uvicorn, in
one process.

**No store call runs on the event loop.** A store call blocks, on the disk,
on the store's lock or on another process's write; made on the loop, it
would stall every other request, heartbeats included.

- **`POST /runs` is `async`** only so it can stream the body: it checks
  `Content-Type` from the header, then reads the body chunk by chunk and stops
  as soon as it passes 1 MiB, never trusting `Content-Length`. Decoding
  (`ingestion/decode.py`) and `vantage.ingestion.ingest` -- validation,
  conversion (YAML parsing of metadata included) and the store write -- then
  run in the threadpool through `run_in_threadpool`. `POST /projects`, a
  project's `POST .../config/sections` and `PUT .../members/{user}`,
  `POST /users`, `PATCH /users/{name}`,
  `PUT /users/{name}/password`, `POST /tokens`, `POST /login` and
  `POST /password` read their bodies the same way, under caps of their
  own. The three password routes then run their scrypt and store calls in
  one threadpool call each, once a password slot is free (see
  [Users, tokens and who may do what](#users-tokens-and-who-may-do-what)).
- **Every other route that reaches the store is a plain `def`**, which
  FastAPI runs on AnyIO's worker threads (40 by default).
- **`GET /capabilities` and `GET /openapi.yaml` are `async`** and never block,
  so they answer even while every worker thread waits on the store. The
  dependencies that only read `app.state` are `async` for the same reason:
  an attribute read is not worth a thread. Those that authorize a request,
  or refuse one on an open server, read the store, and are plain `def`s.

**The store's lock.** `SqliteExecutionStore` keeps one `sqlite3` connection,
shared by every thread, and a `threading.Lock` held across every statement
and transaction, reads included, and across `close`. Store calls therefore
run one at a time within the process; the threadpool keeps the event loop
free, it does not make the store parallel. The lock also stops a read from
running inside another thread's open transaction on the shared connection,
where it would see rows not yet committed. Multi-statement writes open with
`BEGIN IMMEDIATE`, and so do the first admin's insert, a password set and a
login's token, so those serialise with each other in one process and
across processes on one file. WAL mode and a five-second busy timeout cover a second
process on the same file, which no in-process lock can reach: the pytest
sessions storing into a local database, and a `vantage` serving it. Opening
switches a new file to WAL, which needs the file to itself for an instant;
SQLite answers a second connection switching at the same moment "database is
locked" at once rather than through the busy timeout, so `connection.py`
retries the switch for as long as that timeout.

**PostgreSQL's transactions.** `PostgresExecutionStore` holds no lock of its
own: it keeps a `psycopg_pool.ConnectionPool` (one connection at least, 10
at most, opened when the store is built), and every store call takes a
connection and runs as its own transaction, so calls from several threads,
and from several server processes on one database, run in parallel. A
connection answers a round trip before a call gets it; one the server has
closed -- a restart or a failover closes them all -- is discarded and the
next tried at once, rather than through the pool's own check, which waits
longer after each failure than the one before. For the same reason the
pool gives up retrying a connection it cannot open after five seconds, not
psycopg_pool's five minutes: a call made once the server is back has a new
connection opened at once, rather than waiting for a retry that a long
outage has spaced out by a minute or more. The pool opens a connection
only as a call starts waiting, so when it gives up and a call is still
waiting, it is asked to open another (`pool.check()`): the calls that
began while the server was down, which by then hold every worker thread,
are served as soon as it is back, in the order they came, not when they
time out. Every connection sets its session to `READ COMMITTED` first,
whatever the database or the role defaults to: the locks below rely on it,
since a `REPEATABLE READ` transaction reads the snapshot its first
statement took, before the lock that statement waited for. A read that
must describe one moment -- a run page with its metadata horizons, a run's
metadata with the check that the run exists -- is one statement or runs
under `REPEATABLE READ`. A write is safe against the same write from
another process by construction, never by a check first:

- `record_session` is one transaction. The run is inserted with
  `ON CONFLICT (id) DO UPDATE ... WHERE` the stored run has no exit status,
  the report has one and both name the same recorder and project, and
  whether it was created comes from that statement's own result. The
  report's project is probed first, and one that does not exist is refused
  before anything is written; projects are never deleted, so the one found
  stays. The run row is then locked (`SELECT ... FOR UPDATE`), so concurrent
  reports of one run take their turn at the per-run metadata bound and the
  results; a report of another caller's run, or of another project's, is
  refused there, rolling back a transaction that changed nothing. Catalogue
  rows are upserted in sorted node id order, one report having one project,
  so two reports lock them in the same order and cannot deadlock; results and metadata insert with `ON CONFLICT DO
  NOTHING`.
- `upsert_setting` counts and inserts under a transaction-scoped advisory
  lock keyed on the project and the namespace, so the section bound holds
  per project across servers.
- `create_project` is one insert that does nothing on a taken name.
- `set_member` is one transaction: it probes the project, then the user,
  then upserts the member row, `ON CONFLICT (project, account) DO UPDATE`.
  Projects and users are never deleted, so what the probes find stays,
  and the foreign keys back them; probing first names the missing row
  correctly. Whether the row was added is the upsert's own
  `RETURNING xmax = 0`, so of two calls adding one member exactly one says
  it did. The write locks the one member row, and its foreign keys take
  `FOR KEY SHARE` on the project and user rows, which conflicts with
  nothing else the store takes on them: `update_user`'s row lock is
  `FOR NO KEY UPDATE`, a login's `FOR SHARE`. `remove_member` is one
  `DELETE`, and a set racing a remove on one row ends as whichever commits
  last; both ends are valid.
- `touch_last_contact` is one conditional `UPDATE`, and never moves the
  contact backwards.
- `create_user` is one insert that does nothing on a taken name, and
  `create_token` one `INSERT ... SELECT` from its user, which inserts
  nothing when there is none: users are never deleted, so the user found
  stays.
- `update_user` is one `UPDATE ... RETURNING` of non-key columns, so its
  row lock never blocks the key-share locks ingestion's foreign keys take,
  and `revoke_token` one conditional `UPDATE` that keeps the first
  revocation. Two servers revoking one token both read back the same time.
- `create_first_admin` is one `INSERT ... SELECT ... WHERE NOT EXISTS` a
  user or a `local` origin, `ON CONFLICT (name) DO NOTHING`: of two servers
  starting on one empty database, the second waits on the first one's row
  and inserts nothing.
- `set_password` is one transaction: the `UPDATE` of the hash, a
  compare-and-set when it replaces a checked one, then the revocation of
  the user's login tokens, a statement of its own, so under
  `READ COMMITTED` it sees every login that committed before the row lock
  was granted.
- `create_login_token` is one transaction whose `INSERT ... SELECT` reads
  the account `FOR SHARE OF a`. That lock conflicts with the row lock the
  `UPDATE` of `set_password` or `update_user` takes, so a login either
  commits first, and the change then revokes its token, or waits for the
  change, reads the row again and inserts nothing. A plain read would go on
  with the hash it saw before the change committed, and leave a live token
  minted with the old password. Both calls lock the account row first, so
  they never deadlock each other; two logins of one user deleting the same
  expired tokens can, and are retried.
- A transaction that fails with a serialization failure or a deadlock is
  retried a bounded number of times; any other error propagates.

**No store is ever handed U+0000.** PostgreSQL's `text` cannot hold it,
and no UTF-8 encoder takes a lone surrogate, so `decode_json`
(`ingestion/decode.py`, with `ingestion/text.py`) replaces every U+0000 in a
key or string value of a body with U+FFFD, and every lone surrogate too for
`POST /runs` and the local store; a project's `POST .../config/sections`,
`POST /projects` and the members, users and tokens bodies refuse a lone
surrogate instead. `metadata_parse`
replaces a U+0000 that a declared document spells as an escape the same
way. Every adapter therefore stores the same text. A value that is only
looked up with -- a node id, a metadata filter, a section name to delete --
is not rewritten into something else to find: holding U+0000, it matches
nothing, and the route answers without asking the store, even where an
older SQLite database holds that very text. The run list still asks the
store for the `metadata_horizon` of a filtered key holding U+0000, as the
key with U+FFFD in its place: the text a report carrying it stored.

**Rejections have one shape**, built in `service/errors.py`: an error code, a
fixed sentence and dotted field paths. The rejections a report itself earns
are `vantage.ingestion.errors`' plain exceptions, which carry exactly those
and a status code; the service's handlers map them, and its own HTTP-only
ones (a wrong media type, a body too large or cut short) derive from the
same base. Pydantic's own error details, which echo the submitted value, are
never forwarded, and a client-chosen key name is echoed only if it looks
like an identifier. The router's own `404` and
`405` are reshaped the same way; the `405` keeps its `Allow` header. An
unexpected exception is not a rejection and becomes Starlette's plain-text
`500`.

**Responses are built field by field**, never mapped from an object. List
responses come from lean projections with no field that could carry a
traceback, a `repr` or captured output, and no response model has a field for
the repository root the plugin sends.

## Storage

**`schema.sql` is applied whole, once.** On first open, when the `meta` table
does not exist, the whole file runs in one `BEGIN IMMEDIATE` transaction that
also stamps `meta.schema_version` (currently 10) and `meta.origin` (see
[Users, tokens and who may do what](#users-tokens-and-who-may-do-what)) and
writes the `default` project. Reopening issues no DDL. A
database carrying any other stamp, older, newer, missing or not a number, is
refused with `SchemaVersionError` before anything in it changes, and the
`vantage` command turns that into a one-line refusal. There are no
migrations: a table or column is added when code writes it, together with a
bump of `_SCHEMA_VERSION`. Both adapters take the version, and
`SchemaVersionError`, from one neutral module, `storage/version.py`, which
holds the only literal: the SQLite file and the PostgreSQL schema are one
logical schema, and change together.

| Table | Holds |
| --- | --- |
| `project` | one row per project, `default` from the database's creation; never renamed or deleted |
| `project_member` | one row per member of a project, with their role in it (`viewer`, `editor` or `owner`, a `CHECK`), keyed by project and user, with an index on `(account, project)` for a user's memberships; none in `default`, which the adapters refuse in code rather than a `CHECK`, so `DEFAULT_PROJECT` stays the only statement of that name. A disabled user's rows stay |
| `run` | one row per session: its project, times, exit status, interruption, VCS fields, last contact, the user who recorded it |
| `test_case` | the catalogue: one row per node id ever seen in a project, with first and last sighting |
| `result` | one row per test per run, unique on `(run_id, node_id)` |
| `run_metadata_file`, `run_metadata` | each declared file's status; each key's status and value, whether a file or the session gave it, its display name, and whether the declaration named it |
| `project_setting` | a project's namespaced JSON values; its section definitions live here |
| `account`, `access_token` | one row per user, never deleted, with their password's scrypt hash or null; one per token made, by its digest, with its scopes (`can_read`, `can_record`, `can_manage`, `can_admin`, at least one set), when it was revoked and, for a login token alone, when it expires. A made token's row is kept for good, a login token's deleted once it has expired, at its user's next login |
| `meta` | the schema version, who made the database (`origin`: `local` or `server`), when it was created and, if the account that made it has a name, by whom |

**Timestamps are fixed-width UTC text**, `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`,
written only through `isoformat_utc`. The service converts every incoming
timestamp to UTC first, taking one without an offset as UTC. Text order is
then chronological order, which the catalogue's `MIN`/`MAX`, the heartbeat's
never-backwards update, run ordering and the count of runs predating a
metadata key all rely on. The plugin sends the same format.

**A report is one transaction**, in a fixed order so every foreign key finds
its row: run, catalogue, results, metadata.

- The run is an upsert applied only when the stored run has no exit status
  and the incoming report has one: a finish lands over a start, never the
  reverse. Exit status rather than finish time is the test because an
  interrupted session has the first and not the second. VCS fields update
  under the same condition, column by column, and a null never replaces a
  value.
- Whether the report created the run comes from an existence probe inside the
  transaction, since SQLite's row count cannot tell an insert from an update.
  The report's project is probed first, then who recorded the run and its
  project, refusing another caller's report or another project's before
  anything is written; then the exit status: a report reaching
  a finished run returns there and stores nothing, since a client sends the
  finish last and anything after it is a replay. PostgreSQL reads it under the run's row
  lock, after an upsert that changed nothing.
- Results insert with `ON CONFLICT DO NOTHING`, metadata rows likewise, so a
  replay changes nothing. A metadata key is inserted only while the run
  holds fewer than 200, counted in the same statement, so the bound holds
  over every report of the run and the metadata route needs no paging.

**A member is set in one transaction** too: the project, the user and the
member row are probed, then the row is upserted, under the same
`BEGIN IMMEDIATE`, so the answer names what is missing, project first,
and the row probe tells an added member from a changed one, as the run's
does. `default` and a role outside `ROLES` are refused with `ValueError`
before the database is asked.

Booleans are `0`/`1`; `vcs_dirty` is null when unknown, never `0`. The long
free-text columns (the commit subject, and a result's failure message,
`repr`, traceback, skip and xfail reasons and captured output) are cut to
64 KiB and carry a `<name>_truncated` flag. `interrupt_reason` is the
exception: the server cuts it to 64 KiB too, but no column records the cut,
so a reason that was cut reads like a whole one. pytest-vantage never sends
more than 1,024 characters there. Foreign keys are on,
`synchronous` is `FULL`, the journal is WAL. A new directory is created 0700
and a new database file 0600 before `sqlite3` touches the path; existing
modes are never rewritten, and a database open to others is reported. List
queries read a byte prefix of the commit subject and failure message, never
the whole text.

**The run list reads along `run(project, started_at, id)`**, which serves a
project's order, a cursor's range and the horizon's counts. The filtered
list and the horizon's first sighting compare the project as `+run.project`,
which keeps SQLite, with no statistics since nothing runs `ANALYZE`, from
choosing that index for them: it would scan every run of the project and
probe the metadata for each, where the key/value index finds the few runs
that hold a pair. `test_sqlite_store.py` pins the plans.

### PostgreSQL

`vantage.storage.postgres` keeps the same tables, columns, constraints and
meaning in a schema of its own, named `vantage`, so they never collide with
anything else in the database. The types are PostgreSQL's own: `timestamptz`
for every timestamp, `boolean` for the flags, `bigint` for integers and for
the identity keys, `double precision` for durations and `text` for text,
`project_setting.value` included, which must come back byte for byte. The
vocabularies are the same `CHECK` constraints, and the unique keys and
foreign keys are the same. A btree entry holds at most about 2.7 kB, and a
node id, a metadata key or value or a declared file path has no bound, so
their uniqueness and lookups go through `vantage.text_key`, a SHA-256 of
the text, with the text itself compared as well. `text_key` is an index
expression, and so must be immutable, which converting to UTF-8 is only in
a UTF-8 database: the adapter refuses any other with `PostgresOpenError`,
and speaks UTF-8 on the wire whatever `PGCLIENTENCODING` says.

- **Creation is guarded.** Opening takes a transaction-scoped advisory lock,
  so two servers starting on an empty database at once cannot both create
  it. If `vantage` has no `meta` table, everything is created and stamped in
  one transaction, which PostgreSQL's transactional DDL makes all or
  nothing; the origin is always `server`, since the local store never
  opens PostgreSQL. A different stamp, or tables with no stamp, is refused with
  `SchemaVersionError` and nothing is changed.
- **Parity with SQLite is the contract.** The port contract runs against
  this adapter too, so what differs underneath must not show: text is
  ordered and compared with `COLLATE "C"`, code point order as in SQLite;
  timestamps come back aware, in UTC, because every connection sets its
  session to UTC and the ISO date style whatever the database says: psycopg
  parses no other style, and in a local zone either end of the years
  1-9999 falls outside what a `datetime` holds; every connection
  asks for doubles with all their digits (`extra_float_digits`), which a
  database set to 0 would cut to 15; paging, `has_more` and where a page
  after a cursor starts (a row comparison on `started_at` and `id`, which
  both adapters' indexes serve) are computed the same way.
- **U+0000 never reaches it** from the service (see *Request handling and
  concurrency*). The adapter still replaces it with U+FFFD in anything it
  writes, and treats a lookup value holding it as matching nothing, so a
  caller other than the service cannot make it fail either.

## Server configuration

| Setting | Flag | Environment | Default |
| --- | --- | --- | --- |
| Database: a SQLite path or a PostgreSQL URL | `--database` | `VANTAGE_DATABASE` | `$XDG_DATA_HOME/vantage/vantage.db`, else `~/.local/share/vantage/vantage.db` |
| Host | `--host` | none | `127.0.0.1` |
| Port | `--port` | none | `8765` |
| Grace period | `--grace-period` | none | 900 seconds |

A flag beats the environment, which beats the default. An empty
`--database`, `VANTAGE_DATABASE` or `XDG_DATA_HOME` is unset and a relative
`XDG_DATA_HOME` is ignored; an empty `--host` is refused instead, because
the event loop would bind `""` as every interface. Resolution
(`core/config/resolution.py`) is a pure function with no filesystem or
network access, so asking where the database would go never creates it, and
it rejects an unusable value (an empty host, a port outside 1 to 65535, a
grace period that is not positive or exceeds 365 days) before anything
opens. A port or grace period that is not a number never reaches it:
`argparse` refuses it with its usage message and exit status 2. The default
path comes from `default_sqlite_path`, which `vantage.local` calls too, with
`VANTAGE_DATABASE` left out: the plugin's local modes store by default where
`vantage` with no options serves.

Resolution names the database as a target (`core/config/database.py`): a
`PostgresTarget` for a value whose scheme is `postgresql://` or
`postgres://`, in any case, a refusal for any other URL (`mysql://`,
`sqlite:///`, `postgresql+psycopg://`; taken as a path, it would create
directories named after the URL and show its password), and a `SqliteTarget`
for anything else and for the default. The scheme is lower-cased, because libpq recognises no other
spelling and reads anything else as a `key=value` string, whose parse error
quotes it whole. A URL is shown only through `redacted`, which replaces the
password in the user part, and any `password`, `sslpassword` or
`oauth_client_secret` query parameter, with `***`. Since an unencoded
password may hold `@`, `/`, `?` or `#`, everything between the first `:`
and the last `@` counts as the password, and a query parameter's value runs
on over any `&` piece without an `=`. A `PostgresTarget`'s `repr` is
redacted too.

`service/cli.py` acts on the result, once it knows it is to serve: `vantage
push`, `vantage user` and `vantage token` are handed off before argument
parsing, and FastAPI, Uvicorn and the
app are imported only after it, so an install without the `server` extra is
refused in one line naming the extra, before anything is bound or created.
Any other `ImportError` is a broken installation and raised as it is. For
SQLite it checks that an existing
database directory is writable. For PostgreSQL it imports
`vantage.storage.postgres` by name, so no other start loads the adapter or
its driver, and a driver that is not installed (psycopg or psycopg-pool
absent, or psycopg without a libpq) is refused with one line naming the
`postgres` extra. It then opens the store, refuses any failure with one
`vantage: ...` line and exit status 1, gives a database of the server's own
its first admin (see
[Users, tokens and who may do what](#users-tokens-and-who-may-do-what)),
and warns about a bind other than `127.0.0.1` unless the database has a
user and so the server requires a token. By then only a database
pytest-vantage's local store made can lack one, and the warning says so:
`Binding to HOST with no user in the database, which pytest-vantage's local
store made: nothing authenticates requests, so anyone who can route to this
host can read and write everything. Add one with vantage user add NAME
--admin, and the server requires a token.` A server's own database never
warns, and neither does the loopback default: a warning on every normal
start trains people to ignore the one that matters. It closes the store on
shutdown. A
PostgreSQL refusal names the redacted URL, and quotes the driver's message
on one line with the URL's password taken out by `redact_message`, as
written and percent-decoded: libpq quotes a percent-escape it cannot
decode, and splits a password holding an unencoded `@` or `/` into fields
-- a host, a port, a database -- that it names one at a time, so each piece
of such a password is taken out as well. The driver's loggers are silenced
while the store opens, because psycopg's pool logs every failed attempt to
connect, quoting libpq, on stderr before any logging is configured.
Credentials beyond the URL are libpq's own (`PGPASSWORD`, `~/.pgpass`, the
other `PG*` variables); the command adds no flag for them.

The server takes configuration from the environment while the plugin's
switches refuse it, because the two are started differently: the server is
started on purpose by whoever runs it, while a value committed to a test
repository applies to everyone who checks it out.

## The container image

The root `Dockerfile` builds the server in two stages from one Python base
image, named by tag with its Debian release. The first copies in uv, pinned
by version, and runs `uv sync --locked --no-editable --package vantage
--extra server --extra postgres` into `/opt/vantage`: exactly what
`uv.lock` pins, hashes checked, with both workspace members built into
wheels, so the environment needs nothing from the source tree. `--locked`,
not `--frozen`: a lock that no longer matches the pyproject files fails the
build, where `--frozen` would build an image without whatever the lock
lacks. The second stage receives `/opt/vantage` alone, owned by root, with
bytecode compiled at build time, since the server's user cannot write it
there; it must be the same base, as the environment links to its
interpreter. `.dockerignore` admits exactly what the first stage copies.

The server runs as uid and gid 10001, fixed so a bind mount can be given
to them, as PID 1 in exec form, with no init. uvicorn handles the SIGTERM
`docker stop` sends and the app's shutdown closes the store; the SIGTERM
uvicorn then raises again, which ends the process anywhere else, is
ignored by the kernel for a PID namespace's init, so `main` returns
through its `finally` with exit status 0. The same rule drops a SIGTERM
that arrives before uvicorn has installed its handlers, so a stop in the
first moments of a start waits for Docker's SIGKILL. A shell as PID 1
would drop every SIGTERM; an init would pass it on, and a clean stop would
exit 143.

The database is named by `ENV VANTAGE_DATABASE=/data/vantage.db`, never
by a flag in the command, which would beat an operator's `-e
VANTAGE_DATABASE=postgresql://...`; `docker exec ... vantage user ...`
inherits it and so manages the database being served. `/data` is created
0700 for the server's user and declared a volume, whose owner and mode a
new named volume copies, and it is the working directory, where SQLite and
Python put their temporary files when the root filesystem is read-only.
`--host 0.0.0.0` warns about nothing, since `create_first_admin` runs
before `warn_if_bound_wide`. The health check asks `/capabilities`, which
needs no token and never waits on the store, with the image's own Python,
as the image has no curl.

`tests/test_dockerfile.py` reads both files, resolving the image's command
with `vantage`'s own parser and resolution; CI's `image` job builds the
image and runs it on a fresh volume. Nothing is pushed anywhere, and
`tests/test_ci_workflow.py` fails on a workflow that would.

## Test support

The workspace has one pytest configuration, `[tool.pytest.ini_options]` in the
root `pyproject.toml`: `--strict-markers`, the `slow` marker and the
`pythonpath`. pytest would silently prefer a configuration file under
`packages/` for runs on that directory, so `tests/test_workspace_pytest_ini.py`
fails if one appears. pytest honours `pytest_plugins` in the root
`conftest.py` and in test modules, but fails collection over one in a
package-level `conftest.py`, so the workspace has no package-level conftest.
The root `conftest.py` registers `pytester` and `vantage_test_server` for
every test, and removes `VANTAGE_TOKEN` and `VANTAGE_PROJECT` from each
test's environment, which inner sessions and `vantage push` inherit: a token
or a project exported for a real server would be refused by every test
server without users or with only `default`; a test module that needs `store_fixtures` loads it with its own
`pytest_plugins = ["store_fixtures"]`, and one that needs `cheap_passwords`
loads `password_fixtures` the same way.

`pythonpath` puts both `tests/` directories on the import path, so the support
modules import by bare name. None lives under a `src/` tree, so none ships in
a wheel. deptry's per-rule ignores name each one a test imports with an
`import` statement; `store_fixtures` and `postgres_fixtures`, loaded only as
plugins, need none.

| Module | Purpose |
| --- | --- |
| `packages/vantage/tests/importwalk.py` | the AST import walker behind the dependency checks of both distributions |
| `packages/vantage/tests/memory_store.py` | `InMemoryExecutionStore`, a complete second implementation of the port; the server never uses it |
| `packages/vantage/tests/vantage_port_contract.py` | `ExecutionStoreContract`, the behaviour every store must have; `test_sqlite_store.py`, `test_postgres_store.py` and `test_memory_store.py` subclass it |
| `packages/vantage/tests/postgres_fixtures.py` | `postgres_url`, `create_postgres_database`, `postgres_store` and `postgres_metadata`: a fresh database per test on the server `VANTAGE_TEST_POSTGRES_URL` names, dropped afterwards, skipping the test when it names none. Each database sorts text with ICU's `en-US` by default, so a text key missing `COLLATE "C"` shows even on a server whose C library sorts every locale by code point; the server needs ICU, as the official images have |
| `packages/vantage/tests/store_fixtures.py` | `any_store` and `any_stored_metadata`: each `ExecutionStore` implementation in turn (test ids `[memory]`, `[sqlite]` and `[postgres]`, the last skipped without `VANTAGE_TEST_POSTGRES_URL`), with a reader of the metadata rows it holds, for tests whose behaviour must not depend on the adapter |
| `packages/vantage/tests/password_fixtures.py` | `cheap_passwords`, which makes every new password hash, and the hash a check with nothing to match is made against, cost scrypt `ln=4` instead of about 0.2 s, and `cheap_hash`, one such hash: for tests that set, check or log in with many passwords. A stored hash carries its own cost, so either verifies with or without the fixture; `test_passwords.py` checks the real cost |
| `packages/vantage/tests/sqlite_rows.py` | reads a run's metadata rows, whose file rows no port method returns, with plain SQL on a separate connection |
| `packages/vantage/tests/loopback_server.py` | `LoopbackServer`: a real Uvicorn server on a thread, on a `127.0.0.1` port the OS assigns, with bounded start and stop |
| `packages/pytest-vantage/tests/vantage_test_server.py` | `VantageTestServer` and the `vantage_server` fixture: a real server backed by `SqliteExecutionStore` in a directory, recording each request's method and path, for the plugin's end-to-end tests, and able to serve a local database the plugin wrote, and to make a user and a token of theirs, which closes it; `ServerGate` and the `server_gate` fixture: one address that refuses connections, then forwards them to a server, then resets them after a given number, for a server that is down, back, or gone by a session's finish |

`slow` marks the tests that measure elapsed time; `-m 'not slow'` skips them
locally, and CI always runs everything. The tests that need a PostgreSQL
server run only when `VANTAGE_TEST_POSTGRES_URL` names one, as a superuser
on any database, and are skipped with a reason naming the variable
otherwise; each gets a database of its own, created and dropped around it.
The `tests/` directory at the root checks the CI workflow's shell steps, the
PostgreSQL job's wiring, the pre-commit configuration and the `Dockerfile`.

The plugin's mode tests (`test_local_modes.py`) give each session a stand-in
for `vantage.local`, written into its conftest, to see exactly what the
plugin hands local storage and to make storing fail on demand;
`test_local_storage_end_to_end.py` lets the real one write the local
database and compares what a `vantage` app serving that file returns with
what the server returns.

CI (`.github/workflows/ci.yml`) runs on pushes to `main` and on every pull
request: the suite on Python 3.10 to 3.13, with and without pytest-xdist; the
whole suite on Python 3.12 against a `postgres:17` service container, the
one job that sets `VANTAGE_TEST_POSTGRES_URL`; a check that Python 3.9
refuses to install the plugin; the whole suite with every non-loopback
outbound connection rejected and counted; the clean-environment installs of
both wheels; the server image, built and run on a fresh volume; and
ruff, `mypy --strict`, deptry and a build of both wheels. `pip-audit` runs
weekly (`audit.yml`).
