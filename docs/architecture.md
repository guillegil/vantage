# Architecture

A maintainer's map: how the code is divided, what each part may depend on,
and why. What a user sees is in the [README](../README.md); the wire format
is in [`api/v1-ingestion.md`](api/v1-ingestion.md).

## Two distributions

```
packages/
├── pytest-vantage/                published as `pytest-vantage`
│   ├── src/pytest_vantage/        the pytest plugin
│   └── tests/
└── vantage/                       published as `vantage`
    ├── src/vantage/
    │   ├── core/                  domain model, storage port, config resolution
    │   ├── storage/               schema.sql and the SQLite adapter
    │   └── service/               FastAPI app, `vantage` command, OpenAPI document
    └── tests/
```

The plugin and the server share no code and never import each other. Each
has its own rule for what it may import, and each rule is checked
mechanically:

| Package | Ships in | May import | Checked by |
| --- | --- | --- | --- |
| `pytest_vantage` | `pytest-vantage` | the standard library and pytest | AST import walk, deptry, clean-environment install job |
| `vantage.core` | `vantage` | the standard library, minus modules that open a database, socket or process (`sqlite3`, `socket`, `http`, `urllib`, `subprocess`, `asyncio`, ...) | AST import walk |
| `vantage.storage` | `vantage` | the standard library and `vantage.core` | AST import walk |
| `vantage.storage.postgres` | `vantage` | the same, and the PostgreSQL driver (`psycopg`, `psycopg_pool`) from the `postgres` extra | AST import walk, an import of the command with the driver unavailable |
| `vantage.service` | `vantage` | anything; it declares FastAPI, Uvicorn and PyYAML | none needed |

The three checks catch different failures:

- **The AST import walk** (`packages/vantage/tests/importwalk.py`) reads every
  import statement, those inside functions included, and resolves relative
  imports, so `from ..service import x` inside the core is caught.
  `packages/vantage/tests/test_architecture.py` applies it to the core and
  storage, and `packages/pytest-vantage/tests/test_plugin_imports.py` to the
  plugin. The storage walk allows the driver's two top-level modules to
  `vantage.storage.postgres` and the modules under it, and to nothing else.
  Because the extra is optional, the same test also imports storage, the app
  and the `vantage` command in a fresh interpreter where `psycopg` and
  `psycopg_pool` cannot be imported, and checks that the adapter was not
  loaded: nothing a SQLite server runs may reach it.
- **deptry**, run over the whole workspace, flags an import of a third-party
  package the workspace does not declare.
- **The `clean-environment-install` CI job** builds the plugin wheel, installs
  it into an environment holding only pytest, and fails unless exactly one
  distribution was added. It is the only check that sees what reaches a user.

Values both sides must agree on (the 1 MiB body cap, the 64 KiB text-field
cap, the metadata bounds and statuses, the outcome vocabulary, the timestamp
format, the heartbeat interval behind the default grace period) are kept as a
copy on each side. `packages/pytest-vantage/tests/test_server_contract.py`
imports both and compares every copy.

## Clean architecture with Protocol ports

Dependencies point inwards: the service depends on storage and the core,
storage on the core, and the core on nothing but the standard library.

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
- **`vantage.storage`** holds `SqliteExecutionStore`, the one adapter the
  server ships.
- **`vantage.service`** is the HTTP edge. Pydantic models (`schemas.py`)
  validate what arrives; the routes convert a validated report into core
  dataclasses before calling the store, so no Pydantic type reaches the core
  or storage. `create_app(store)` takes the store as a parameter: `cli.py` is
  the one place that builds one, a `SqliteExecutionStore` or a
  `PostgresExecutionStore`, and tests pass their own.

Two conventions follow from Python and pytest rather than from the design.
Vocabularies are `frozenset`s of plain `str`, never an `Enum`: a `str` enum
formats differently on Python 3.10 and 3.13. And no domain class name starts
with `Test`, because pytest would try to collect it; the aggregate is
`Execution`, not `TestExecution`.

## Why the plugin never opens a database

The plugin reports over HTTP and the server performs every write.

- **Nothing to conflict with.** Installing the plugin into a test environment
  adds one distribution and no dependency beyond pytest, so it cannot clash
  with the pins of the project under test. A database driver or ORM in the
  plugin would.
- **No schema in the plugin.** Storage can change without a plugin release;
  the plugin and the server only have to agree on the HTTP contract.
- **One writer.** CI jobs, xdist sessions and developers all report to one
  server process, which serialises writes on one connection. Nobody shares a
  database file across machines or network filesystems.
- **Liveness needs a listener.** Because the server is told when a session
  starts and hears from it while it runs, it can tell a running session from
  one that was killed.

The cost is that recording needs a server the test machine can reach.

## The HTTP contract is the compatibility boundary

The plugin and the server are released separately, so an old plugin meets a
new server and the reverse. The versioned API is the only thing they share:
every route is under `/api/v1`, and nothing answers an unversioned path.

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
   address and timeout are resolved and validated; a bad value is a usage
   error. A bare TCP connect checks that something listens at the address;
   if nothing does, one warning and the session runs unrecorded. A capability
   probe asks for `session_lifecycle`, and the `Recorder` is built: a random
   run id, the start time, one git read (five seconds in total), the
   metadata declaration and, if asked, the files it names.
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
   results.

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

## Failure isolation in the plugin

pytest imports `plugin.py` in every session, recording or not, so it
implements only `pytest_addoption`, `pytest_configure` and the
`vantage_metadata` fixture, which runs only when a test asks for it. The
recording modules are imported, and the `Recorder` registered, only once
`--vantage` is typed; a test checks that an unactivated session loads
nothing beyond the plugin's entry module and its configuration and warning
helpers. A session that is not recorded gets a fixture mapping nobody reads,
so a project's fixtures run the same either way.

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
  as soon as it passes 1 MiB, never trusting `Content-Length`. Decoding,
  validation, conversion (YAML parsing of metadata included) and the store
  write then run in the threadpool through `run_in_threadpool`.
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
process on the same file, which no in-process lock can reach.

**No store is ever handed U+0000.** PostgreSQL's `text` cannot hold it,
and no UTF-8 encoder takes a lone surrogate, so `decode_json`
(`service/text.py`) replaces every U+0000 in a key or string value of a body
with U+FFFD, and every lone surrogate too for `POST /runs`;
`POST /config/sections` refuses a lone surrogate instead. `metadata_parse`
replaces a U+0000 that a declared document spells as an escape the same
way. Every adapter therefore stores the same text. A value that is only
looked up with -- a node id, a metadata filter, a section name to delete --
is not rewritten into something else to find: holding U+0000, it matches
nothing, and the route answers without asking the store, even where an
older SQLite database holds that very text. The run list still asks the
store for the `metadata_horizon` of a filtered key holding U+0000, as the
key with U+FFFD in its place: the text a report carrying it stored.

**Rejections have one shape**, built in `service/errors.py`: an error code, a
fixed sentence and dotted field paths. Pydantic's own error details, which
echo the submitted value, are never forwarded, and a client-chosen key name
is echoed only if it looks like an identifier. The router's own `404` and
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
bump of `_SCHEMA_VERSION` in `storage/connection.py`.

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
`argparse` refuses it with its usage message and exit status 2.

Resolution names the database as a target (`core/config/database.py`): a
`PostgresTarget` for a value whose scheme is `postgresql://` or
`postgres://`, in any case, and a `SqliteTarget` for any other value and for
the default. The scheme is lower-cased, because libpq recognises no other
spelling and reads anything else as a `key=value` string, whose parse error
quotes it whole. A URL is shown only through `redacted`, which replaces the
password in the user part and any `password=` query parameter with `***`;
since an unencoded password may hold `@`, `/`, `?` or `#`, everything
between the first `:` and the last `@` counts as the password. A
`PostgresTarget`'s `repr` is redacted too.

`service/cli.py` acts on the result. For SQLite it checks that an existing
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
decode, and takes what follows an unencoded `@` for a host it then names.
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
`import` statement; `store_fixtures`, loaded only as a plugin, needs none.

| Module | Purpose |
| --- | --- |
| `packages/vantage/tests/importwalk.py` | the AST import walker behind the dependency checks of both distributions |
| `packages/vantage/tests/memory_store.py` | `InMemoryExecutionStore`, a complete second implementation of the port; the server never uses it |
| `packages/vantage/tests/vantage_port_contract.py` | `ExecutionStoreContract`, the behaviour every store must have; `test_sqlite_store.py` and `test_memory_store.py` subclass it |
| `packages/vantage/tests/store_fixtures.py` | `any_store` and `any_stored_metadata`: each `ExecutionStore` implementation in turn (test ids `[memory]` and `[sqlite]`), with a reader of the metadata rows it holds, for tests whose behaviour must not depend on the adapter |
| `packages/vantage/tests/sqlite_rows.py` | reads a run's metadata rows, whose file rows no port method returns, with plain SQL on a separate connection |
| `packages/vantage/tests/loopback_server.py` | `LoopbackServer`: a real Uvicorn server on a thread, on a `127.0.0.1` port the OS assigns, with bounded start and stop |
| `packages/pytest-vantage/tests/vantage_test_server.py` | `VantageTestServer` and the `vantage_server` fixture: a real server backed by `SqliteExecutionStore` in a temporary directory, recording each request's method and path, for the plugin's end-to-end tests |

`slow` marks the tests that measure elapsed time; `-m 'not slow'` skips them
locally, and CI always runs everything. The tests that need a PostgreSQL
server run only when `VANTAGE_TEST_POSTGRES_URL` names one, as a superuser
on any database, and are skipped with a reason naming the variable
otherwise; each gets a database of its own, created and dropped around it.
The `tests/` directory at the root checks the CI workflow's shell steps, the
PostgreSQL job's wiring and the pre-commit configuration.

CI (`.github/workflows/ci.yml`) runs on pushes to `main` and on every pull
request: the suite on Python 3.10 to 3.13, with and without pytest-xdist; the
whole suite on Python 3.12 against a `postgres:17` service container, the
one job that sets `VANTAGE_TEST_POSTGRES_URL`; a check that Python 3.9
refuses to install the plugin; the whole suite with every non-loopback
outbound connection rejected and counted; the clean-environment install; and
ruff, `mypy --strict`, deptry and a build of both wheels. `pip-audit` runs
weekly (`audit.yml`).
