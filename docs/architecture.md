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
  serves by default. `vantage[server]` must serve. It is the only check that
  sees what reaches a user.

Values both sides must agree on (the 1 MiB body cap, the 64 KiB text-field
cap, the metadata bounds and statuses, the outcome vocabulary, the timestamp
format, the heartbeat interval behind the default grace period) are kept as a
copy on each side. `packages/pytest-vantage/tests/test_server_contract.py`
imports both and compares every copy.

## Clean architecture with Protocol ports

Dependencies point inwards: the service and the local store depend on
ingestion, storage and the core, ingestion and storage on the core, and the
core on nothing but the standard library.

- **`vantage.core`** holds the domain as frozen standard-library dataclasses
  (`Execution`, `Identity`, `VcsContext`, `Result`, `CaseIdentity`,
  `FailureEvidence`, `CapturedOutput`, `CatalogueEntry`), the pure rules
  (how a run is presented, list projections, section summaries, metadata
  vocabularies), and the server's configuration resolution.
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
  `store.record_session`, so no Pydantic type reaches the core or storage. It
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
   run's id and start time, `finished_at` and `exit_status` null, the `vcs`
   section, `metadata` when the declaration declares keys or files were
   read, and no results. The row exists from this moment, so a session
   killed later still leaves a trace.
3. **`pytest_runtest_logreport`: accumulate and beat.** Every setup, call and
   teardown report is kept in memory, keyed by node id and xdist worker.
   After each one, if 30 seconds have passed since the last beat (or since
   the session started), the plugin sends
   `POST /api/v1/runs/{id}/heartbeat`. Beats are driven by test activity,
   not a timer thread, and measured on the monotonic clock.
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
results accumulate, first write wins; the last-contact time is set when the
run is created and advanced by heartbeats. A run's presentation is derived at
read time, never stored: `finished` when it has a finish time, `interrupted`
when a finish report arrived without one, `abandoned` when nothing was heard
for the grace period (900 seconds by default, thirty heartbeat intervals),
otherwise `running`.

**xdist.** Only the controller builds a `Recorder`; a `Recorder` per worker
would record one session as several runs. The exception behind a failure
exists only in the process that ran the test, so each worker registers an
`EvidenceCollector` that attaches failure text to the report before xdist
sends it to the controller. Reports are grouped per worker, so one result is
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
the rows a server holds. The local database is opened once, at the finish,
by the xdist controller alone, and closed again: nothing holds it open while
tests run.

**What is queued.** Sending stops at the first failed report. A failure a
later attempt can fix -- no connection, a broken one, a timeout, a 5xx
(`outbox.worth_retrying`) -- queues that report and the ones after it; the
reports the server acknowledged are not queued again, and a replay of any of
them would change nothing anyway. A 4xx, a redirect or an answer that does
not acknowledge the run would fail the same way next time and is not
queued. A server unreachable at the start queues every report. The session
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
  claim left by a killed sender lapses. An acknowledged run is deleted; one
  refused with a 4xx is deleted and named in the summary; a 5xx releases it
  with its attempt counted and moves on, since it may be that run's own
  problem; no answer, a redirect or a stranger's answer releases it and
  stops, and so does a spent budget, without counting an attempt. A
  duplicate send is harmless: the server's writes are idempotent.
- A session whose own run reached its server sends that server's queue
  within the report timeout and prints one line. It never creates the
  outbox just to find it empty.

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
| `liveness_isolated` | the start report and heartbeats | heartbeats stop; the finish write is unaffected |
| `accumulation_isolated` | accumulating reports, recording an interrupt | warns once, keeps accumulating |

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
  session-finish and terminal-summary hooks. A warning from
  `pytest_configure` or `pytest_sessionstart`, and one from the xdist
  controller's `pytest_runtest_logreport`, is printed to stderr and never
  reaches the warnings summary.

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

## Request handling and concurrency

`create_app(store, grace_period_seconds=900)` builds the app, mounts the
`runs`, `read`, `capabilities` and `sections` routers under `/api/v1`, and
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
  run in the threadpool through `run_in_threadpool`.
- **Every other route that reaches the store is a plain `def`**, which
  FastAPI runs on AnyIO's worker threads (40 by default).
- **`GET /capabilities` and `GET /openapi.yaml` are `async`** and never block,
  so they answer even while every worker thread waits on the store. The
  dependencies that read `app.state` are `async` for the same reason: an
  attribute read is not worth a thread.

**The store's lock.** `SqliteExecutionStore` keeps one `sqlite3` connection,
shared by every thread, and a `threading.Lock` held across every statement
and transaction, reads included, and across `close`. Store calls therefore
run one at a time within the process; the threadpool keeps the event loop
free, it does not make the store parallel. The lock also stops a read from
running inside another thread's open transaction on the shared connection,
where it would see rows not yet committed. Multi-statement writes open with
`BEGIN IMMEDIATE`. WAL mode and a five-second busy timeout cover a second
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
  `ON CONFLICT (id) DO UPDATE ... WHERE` the stored run has no exit status
  and the report has one, and whether it was created comes from that
  statement's own result. The run row is then locked (`SELECT ... FOR
  UPDATE`), so concurrent reports of one run take their turn at the
  per-run metadata bound and the results. Catalogue rows are upserted in
  sorted node id order, so two reports lock them in the same order and
  cannot deadlock; results and metadata insert with `ON CONFLICT DO
  NOTHING`.
- `upsert_setting` counts and inserts under a transaction-scoped advisory
  lock keyed on the namespace, so the section bound holds across servers.
- `touch_last_contact` is one conditional `UPDATE`, and never moves the
  contact backwards.
- A transaction that fails with a serialization failure or a deadlock is
  retried a bounded number of times; any other error propagates.

**No store is ever handed U+0000.** PostgreSQL's `text` cannot hold it,
and no UTF-8 encoder takes a lone surrogate, so `decode_json`
(`ingestion/decode.py`, with `ingestion/text.py`) replaces every U+0000 in a
key or string value of a body with U+FFFD, and every lone surrogate too for
`POST /runs` and the local store; `POST /config/sections` refuses a lone
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
also stamps `meta.schema_version` (currently 6). Reopening issues no DDL. A
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
| `run` | one row per session: times, exit status, interruption, VCS fields, last contact |
| `test_case` | the catalogue: one row per node id ever seen, with first and last sighting |
| `result` | one row per test per run, unique on `(run_id, node_id)` |
| `run_metadata_file`, `run_metadata` | each declared file's status; each key's status and value, whether a file or the session gave it, its display name, and whether the declaration named it |
| `user_setting` | namespaced JSON values; section definitions live here |
| `meta` | the schema version, and when and by whom the database was created |

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
- Results insert with `ON CONFLICT DO NOTHING`, metadata rows likewise, so a
  replay changes nothing. A metadata key is inserted only while the run
  holds fewer than 200, counted in the same statement, so the bound holds
  over every report of the run and the metadata route needs no paging.

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

### PostgreSQL

`vantage.storage.postgres` keeps the same tables, columns, constraints and
meaning in a schema of its own, named `vantage`, so they never collide with
anything else in the database. The types are PostgreSQL's own: `timestamptz`
for every timestamp, `boolean` for the flags, `bigint` for integers and for
the identity keys, `double precision` for durations and `text` for text,
`user_setting.value` included, which must come back byte for byte. The
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
  nothing. A different stamp, or tables with no stamp, is refused with
  `SchemaVersionError` and nothing is changed.
- **Parity with SQLite is the contract.** The port contract runs against
  this adapter too, so what differs underneath must not show: text is
  ordered and compared with `COLLATE "C"`, code point order as in SQLite;
  timestamps come back aware, in UTC, because every connection sets its
  session to UTC and the ISO date style whatever the database says: psycopg
  parses no other style, and in a local zone either end of the years
  1-9999 falls outside what a `datetime` holds; every connection
  asks for doubles with all their digits (`extra_float_digits`), which a
  database set to 0 would cut to 15; paging and `has_more` are computed
  the same way.
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
`postgres://`, in any case, and a `SqliteTarget` for any other value and for
the default. The scheme is lower-cased, because libpq recognises no other
spelling and reads anything else as a `key=value` string, whose parse error
quotes it whole. A URL is shown only through `redacted`, which replaces the
password in the user part, and any `password`, `sslpassword` or
`oauth_client_secret` query parameter, with `***`. Since an unencoded
password may hold `@`, `/`, `?` or `#`, everything between the first `:`
and the last `@` counts as the password, and a query parameter's value runs
on over any `&` piece without an `=`. A `PostgresTarget`'s `repr` is
redacted too.

`service/cli.py` acts on the result, once it knows it is to serve: `vantage
push` is handed off before argument parsing, and FastAPI, Uvicorn and the
app are imported only after it, so an install without the `server` extra is
refused in one line naming the extra, before anything is bound or created.
Any other `ImportError` is a broken installation and raised as it is. For
SQLite it checks that an existing
database directory is writable. For PostgreSQL it imports
`vantage.storage.postgres` by name, so no other start loads the adapter or
its driver, and a driver that is not installed (psycopg or psycopg-pool
absent, or psycopg without a libpq) is refused with one line naming the
`postgres` extra. It then opens the store, refuses any failure with one
`vantage: ...` line and exit status 1, warns about a bind other than
`127.0.0.1` once the database is open, and closes the store on shutdown. A
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

## Test support

The workspace has one pytest configuration, `[tool.pytest.ini_options]` in the
root `pyproject.toml`: `--strict-markers`, the `slow` marker and the
`pythonpath`. pytest would silently prefer a configuration file under
`packages/` for runs on that directory, so `tests/test_workspace_pytest_ini.py`
fails if one appears. pytest honours `pytest_plugins` in the root
`conftest.py` and in test modules, but fails collection over one in a
package-level `conftest.py`, so the workspace has no package-level conftest.
The root `conftest.py` registers `pytester` and `vantage_test_server` for
every test; a test module that needs `store_fixtures` loads it with its own
`pytest_plugins = ["store_fixtures"]`.

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
| `packages/vantage/tests/sqlite_rows.py` | reads a run's metadata rows, whose file rows no port method returns, with plain SQL on a separate connection |
| `packages/vantage/tests/loopback_server.py` | `LoopbackServer`: a real Uvicorn server on a thread, on a `127.0.0.1` port the OS assigns, with bounded start and stop |
| `packages/pytest-vantage/tests/vantage_test_server.py` | `VantageTestServer` and the `vantage_server` fixture: a real server backed by `SqliteExecutionStore` in a directory, recording each request's method and path, for the plugin's end-to-end tests, and able to serve a local database the plugin wrote; `ServerGate` and the `server_gate` fixture: one address that refuses connections, then forwards them to a server, then resets them after a given number, for a server that is down, back, or gone by a session's finish |

`slow` marks the tests that measure elapsed time; `-m 'not slow'` skips them
locally, and CI always runs everything. The tests that need a PostgreSQL
server run only when `VANTAGE_TEST_POSTGRES_URL` names one, as a superuser
on any database, and are skipped with a reason naming the variable
otherwise; each gets a database of its own, created and dropped around it.
The `tests/` directory at the root checks the CI workflow's shell steps, the
PostgreSQL job's wiring and the pre-commit configuration.

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
both wheels; and
ruff, `mypy --strict`, deptry and a build of both wheels. `pip-audit` runs
weekly (`audit.yml`).
