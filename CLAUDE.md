# CLAUDE.md

Guidance for agents working in this repository.

## What this is

Vantage records what a pytest suite did, run after run. The pytest plugin
reports each session over HTTP; the server stores it in SQLite or
PostgreSQL and serves it through a JSON read API. Pre-release (0.1.0);
nothing is published.

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

One uv workspace, one lockfile, two distributions:

| Package | Path | May import | Enforced by |
| --- | --- | --- | --- |
| `pytest_vantage` | `packages/pytest-vantage/src` | stdlib + pytest | `test_plugin_imports.py`, deptry, CI clean-environment install |
| `vantage.core` | `packages/vantage/src/vantage/core` | stdlib, minus I/O modules (`sqlite3`, `socket`, `http`, `urllib`, `subprocess`, `asyncio`, ...) | `test_architecture.py` |
| `vantage.storage` | `packages/vantage/src/vantage/storage` | stdlib + `vantage.core` | `test_architecture.py` |
| `vantage.storage.postgres` | `packages/vantage/src/vantage/storage/postgres` | the above + `psycopg`, `psycopg_pool` (the optional `postgres` extra); the only place the driver is imported | `test_architecture.py` (walk, and an import with the driver blocked) |
| `vantage.service` | `packages/vantage/src/vantage/service` | anything (FastAPI, Pydantic, uvicorn, PyYAML) | — |

- Ports are `typing.Protocol` (`core/ports/storage.py`); adapters satisfy them
  by shape. The server ships two adapters, `SqliteExecutionStore` and
  `PostgresExecutionStore` (`vantage.storage.postgres`, the optional
  `postgres` extra, imported by the `vantage` command only for a
  `postgresql://` URL). `InMemoryExecutionStore` is a test double
  (`packages/vantage/tests/memory_store.py`); `vantage_port_contract.py` runs
  the same contract against all three, and they must stay in parity.
- Pydantic lives in `vantage.service` only; everything else uses stdlib
  `dataclasses` and hand-written validation.
- Test-support modules sit in `packages/*/tests` on the root `pythonpath` and
  never ship. The only pytest config is `[tool.pytest.ini_options]` in the root
  `pyproject.toml`.

## Invariants

- **The plugin never opens a database** and never depends on `vantage`. It
  talks to `/api/v1` with `urllib`; the server performs every write. The
  versioned API is the compatibility boundary, not package versions.
- **Opt-in only by typed flag.** `--vantage`, `--vantage-failure-text` and
  `--vantage-metadata` count only when present in `config.invocation_params.args`;
  from `addopts`, `PYTEST_ADDOPTS` or an `@file` they are ignored with a warning.
  Ini, env and config may set *where* (server, timeout), never *whether*.
  Without `--vantage` the plugin reads nothing and sends nothing.
- **The suite's exit status is never changed by the plugin**, except that an
  invalid vantage setting raises `pytest.UsageError` (exit 4) in
  `pytest_configure`. Everything else is at most one `VantageWarning` per kind;
  every recorder hook is fault-isolated and every request has a deadline.
- **xdist:** branch on `workerinput`. Only the controller builds a `Recorder`;
  workers attach failure evidence to reports and hand interrupts and
  `vantage_metadata` values to the controller through `workeroutput`.
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
  `_SCHEMA_VERSION` (`storage/version.py`, the only literal, currently 6,
  one version for both). Any other stamp is refused; there are no
  migrations. Changing either schema means bumping that literal. No table
  or column exists before code writes it.
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
```

## Before finishing

All green: pytest (full suite), once without and once with
`VANTAGE_TEST_POSTGRES_URL`, `ruff format --check .`, `ruff check .`,
`mypy .`, `deptry .`. CI additionally runs Python 3.10–3.13 with and without
xdist, the suite against a `postgres:17` service (the only job that sets the
variable), the suite with non-loopback networking blocked, the
clean-environment plugin install, the Python 3.9 install refusal and both
wheel builds.

## Conventions

- Short descriptive branch names (`fix/xdist-dedup`, `feat/run-filter`).
- Commits: imperative subject ≤ 72 characters, a body saying why, signed with
  the 1Password SSH key.
- All documentation in English.
