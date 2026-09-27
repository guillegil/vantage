# CLAUDE.md

Guidance for agents working in this repository.

## What this is

Vantage records what a pytest suite did, run after run. The pytest plugin
reports each session over HTTP to a server, which stores it in SQLite or
PostgreSQL and serves it through a JSON read API; or, in its local modes,
stores it in a SQLite file on the test machine through `vantage.local`,
queueing what a server could not take in an outbox to send later.
Pre-release (0.1.0); nothing is published.

## Ground rules

- The code, its tests and its comments are the only statement of behaviour.
  There are no requirements, ADRs or spec documents: never cite or create
  requirement IDs, decision numbers, phases or milestones. Argue from behaviour
  and trade-offs, and put a non-obvious reason in a short present-tense comment.
- Keep `README.md` (users), `docs/architecture.md` (maintainers),
  `docs/api/v1-ingestion.md` and `packages/vantage/src/vantage/service/openapi/v1.yaml`
  (the HTTP contract) true in the same change that alters behaviour.
- Every behaviour change ships with a test that fails without it.

## Layout and dependency rules

One uv workspace, one lockfile, two distributions. `pytest-vantage`
declares only pytest; `vantage` depends on it, plus Pydantic and PyYAML,
with FastAPI and Uvicorn in its `server` extra and the PostgreSQL driver in
its `postgres` extra.

| Package | Path | May import | Enforced by |
| --- | --- | --- | --- |
| `pytest_vantage` | `packages/pytest-vantage/src` | stdlib + pytest; `vantage.local` from `pytest_vantage/local.py` alone, inside functions | `test_plugin_imports.py`, deptry, CI clean-environment install |
| `vantage.core` | `packages/vantage/src/vantage/core` | stdlib, minus I/O modules (`sqlite3`, `socket`, `http`, `urllib`, `subprocess`, `asyncio`, ...) | `test_architecture.py` |
| `vantage.storage` | `packages/vantage/src/vantage/storage` | stdlib + `vantage.core` | `test_architecture.py` |
| `vantage.storage.postgres` | `packages/vantage/src/vantage/storage/postgres` | the above + `psycopg`, `psycopg_pool` (the optional `postgres` extra); the only place the driver is imported | `test_architecture.py` (walk, and an import with the driver blocked) |
| `vantage.ingestion` | `packages/vantage/src/vantage/ingestion` | stdlib + `vantage.core` + Pydantic + PyYAML; never a web framework or a storage adapter | `test_architecture.py` (walk, and an import with the web framework and the driver blocked) |
| `vantage.local` | `packages/vantage/src/vantage/local` | stdlib + `vantage.core` + `vantage.ingestion` + the SQLite adapter's modules | `test_architecture.py` |
| `vantage.service` | `packages/vantage/src/vantage/service` | anything; the only importer of FastAPI, Starlette and uvicorn, and only once it is to serve | `test_architecture.py` (an AST scan), `test_cli.py`, `test_push.py` |

- Ports are `typing.Protocol` (`core/ports/storage.py`); adapters satisfy them
  by shape. The server ships two adapters, `SqliteExecutionStore` and
  `PostgresExecutionStore` (`vantage.storage.postgres`, the optional
  `postgres` extra, imported by the `vantage` command only for a
  `postgresql://` URL). `InMemoryExecutionStore` is a test double
  (`packages/vantage/tests/memory_store.py`); `vantage_port_contract.py` runs
  the same contract against all three, and they must stay in parity.
- A report becomes rows one way: `vantage.ingestion.ingest` validates,
  converts and records it, for `POST /api/v1/runs` and for `vantage.local`
  alike. Its rejections are plain exceptions; `service/errors.py` maps them
  to responses.
- Pydantic lives in `vantage.ingestion` and `vantage.service` only;
  everything else uses stdlib `dataclasses` and hand-written validation.
- Test-support modules sit in `packages/*/tests` on the root `pythonpath` and
  never ship. The only pytest config is `[tool.pytest.ini_options]` in the root
  `pyproject.toml`.

## Invariants

- **The plugin's own code never opens the vantage database.** It talks to
  `/api/v1` with `urllib`; local modes hand reports to `vantage.local`, which
  runs the server's ingestion in-process. `pytest-vantage` never declares
  `vantage`: it is imported from `pytest_vantage/local.py` alone, inside
  functions, and only when a local mode is configured, so a server-mode
  session never loads it. The versioned API is the compatibility boundary
  between plugin and server.
- **The outbox is the plugin's, not a vantage database**
  (`pytest_vantage/outbox.py`, stdlib `sqlite3`, `<local database>-outbox`,
  0600). A queued run is sent only to the address it was queued for,
  compared exactly; senders claim an entry before sending it; bounds are
  1,000 runs and 256 MiB. `vantage push` sends it with the plugin's own code
  and never needs the `server` extra.
- **Opt-in only by typed flag.** `--vantage`, `--vantage-failure-text` and
  `--vantage-metadata` count only when present in `config.invocation_params.args`;
  from `addopts`, `PYTEST_ADDOPTS` or an `@file` they are ignored with a warning.
  Ini, env and config may set *where* (server, timeout, mode, local
  database), never *whether*.
  Without `--vantage` the plugin reads nothing and sends nothing.
- **The suite's exit status is never changed by the plugin**, except that an
  invalid vantage setting raises `pytest.UsageError` (exit 4) in
  `pytest_configure`. Everything else is at most one `VantageWarning` per kind;
  every recorder hook is fault-isolated and every request has a deadline.
- **xdist:** branch on `workerinput`. Only the controller builds a `Recorder`,
  so only it stores locally or touches the outbox; workers attach failure
  evidence to reports and hand interrupts and `vantage_metadata` values to
  the controller through `workeroutput`.
- **No store call on the event loop.** Store-calling routes are plain `def`;
  `POST /runs` streams its body async, then uses `run_in_threadpool`.
- **Concurrency, per adapter.** `SqliteExecutionStore` serialises every
  statement behind one lock, in one process. `PostgresExecutionStore` must
  stay safe with several server processes on one database: each call is its
  own pooled transaction; writes are single conditional statements, row
  locks (`FOR UPDATE`, catalogue rows in sorted order) or advisory locks,
  never check-then-act; snapshot reads use `REPEATABLE READ` or one
  statement; serialization failures and deadlocks are retried, bounded.
  Every connection sets its session to `READ COMMITTED` (and full float
  digits, ISO dates, UTC) first, since the locks and the readers assume it
  whatever the database defaults to, and a connection the server closed is
  replaced at once (`live_connection`), not through the pool's own
  backing-off check.
- **No U+0000 reaches a store.** The body decoder replaces it (and a lone
  surrogate in reports) with U+FFFD; a lookup value holding it matches
  nothing without asking the store.
- **Timestamps** are stored as fixed-width UTC text in SQLite
  (`isoformat_utc`), so text order is time order, and as `timestamptz` in
  PostgreSQL, read back as UTC.
- **Schema:** each adapter applies its whole schema at first use and stamps
  `_SCHEMA_VERSION` (`storage/version.py`, the only literal, currently 7,
  one version for both). Any other stamp is refused; there are no
  migrations. Changing either schema means bumping that literal. No table
  or column exists before code writes it.
- **Access.** A database with no user is open; the first user closes it
  for good, since users are disabled, never deleted. Every route but
  `/capabilities` and `/openapi.yaml` declares its scope through
  `service/access.py` (`read`, `record`, `admin`), and
  `test_interface_document.py` checks each against the document. A token is
  stored only as its SHA-256, printed once by whatever made it, read by the
  plugin from `VANTAGE_TOKEN` alone, and never written to the outbox, a
  message or a `repr`. A run takes reports and heartbeats only from the
  user whose token created it (`ForeignRunError`, `409 foreign_run`). The
  users and tokens routes (`routes/users.py`) need an admin's token on
  every server and answer `409 open_server` while the database has no
  user: the first user is the CLI's alone.
- **Passwords never printed.** A PostgreSQL URL is shown only through
  `redacted`, and a driver message only through `redact_message`
  (`core/config/database.py`); the driver's loggers are silenced while the
  store opens.
- **Python 3.10 floor:** no `StrEnum`, `datetime.UTC`, `tomllib`. Vocabularies
  are `frozenset`s of `str`, never enums.
- No domain class name starts with `Test` (pytest would collect it).

## Commands

```bash
uv sync --extra dev                          # once; hooks use `uv run --no-sync`
uv run --extra dev pytest                    # everything (-n 8 with xdist)
uv run --extra dev pytest -m 'not slow'      # skip timing tests; CI runs all
uv run --extra dev ruff format . && uv run --extra dev ruff check --fix .
uv run --extra dev mypy .                    # strict
uv run --extra dev deptry .
VANTAGE_TEST_POSTGRES_URL=postgresql://postgres:PASSWORD@127.0.0.1:5432/postgres \
  uv run --extra dev pytest                  # PostgreSQL tests too (skipped when unset)
vantage --database ./vantage.db              # server on 127.0.0.1:8765
vantage --database postgresql://user@host/db # the same, storing in PostgreSQL
vantage push                                 # send the runs the plugin queued
vantage user add alice --admin               # the first user closes the database
vantage token create alice                   # prints a token once; VANTAGE_TOKEN for the plugin
pytest --vantage --vantage-mode local        # record into the local database
```

## Before finishing

All green: pytest (full suite), once without and once with
`VANTAGE_TEST_POSTGRES_URL`, `ruff format --check .`, `ruff check .`,
`mypy .`, `deptry .`. CI additionally runs Python 3.10–3.13 with and without
xdist, the suite against a `postgres:17` service (the only job that sets the
variable), the suite with non-loopback networking blocked, the
clean-environment installs (the plugin alone; `vantage` without its extra,
serving refused, `vantage push` and a local-mode session there;
`vantage[server]` serving), the Python 3.9 install refusal and both wheel
builds.

## Conventions

- Short descriptive branch names (`fix/xdist-dedup`, `feat/run-filter`).
- Commits: imperative subject ≤ 72 characters, a body saying why. Commits
  made on a workstation are signed with the 1Password SSH key; commits made
  on agentbox are unsigned, since that key is never on the server.
- All documentation in English.
