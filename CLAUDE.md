# CLAUDE.md

Guidance for agents working in this repository.

## What this is

Vantage records what a pytest suite did, run after run. The pytest plugin
reports each session over HTTP; the server stores it in SQLite and serves it
through a JSON read API. Pre-release (0.1.0); nothing is published.

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
| `vantage.service` | `packages/vantage/src/vantage/service` | anything (FastAPI, Pydantic, uvicorn, PyYAML) | — |

- Ports are `typing.Protocol` (`core/ports/storage.py`); adapters satisfy them
  by shape. The server ships one adapter, `SqliteExecutionStore`.
  `InMemoryExecutionStore` is a test double (`packages/vantage/tests/memory_store.py`);
  `vantage_port_contract.py` runs the same contract against both, and they
  must stay in parity.
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
  `SqliteExecutionStore` serialises every statement behind one lock.
- **Timestamps** are stored as fixed-width UTC text (`isoformat_utc`), so text
  order is time order.
- **Schema:** `storage/schema.sql` is applied whole and stamped with
  `_SCHEMA_VERSION` (`storage/connection.py`, the only literal, currently 6).
  Any other stamp is refused; there are no migrations. Changing the schema
  means bumping that literal. No table or column exists before code writes it.
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
vantage --database ./vantage.db              # server on 127.0.0.1:8765
```

## Before finishing

All green: pytest (full suite), `ruff format --check .`, `ruff check .`,
`mypy .`, `deptry .`. CI additionally runs Python 3.10–3.13 with and without
xdist, the suite with non-loopback networking blocked, the clean-environment
plugin install, the Python 3.9 install refusal and both wheel builds.

## Conventions

- Short descriptive branch names (`fix/xdist-dedup`, `feat/run-filter`).
- Commits: imperative subject ≤ 72 characters, a body saying why, signed with
  the 1Password SSH key.
- All documentation in English.
