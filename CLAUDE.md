# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Vantage records what a pytest suite did, over time and on which commit. A pytest
plugin reports each session over HTTP; a server stores it and serves its history.

## How to reason about this project

There is no requirements corpus, no ADR log and no spec process. The code, its
tests and the plain-words comments in them are the whole statement of how the
system behaves and why. Never justify a change by citing a requirement ID, an
ADR, a design-decision number, a spec scenario, a phase or a milestone — argue
from the behaviour and the trade-off itself, and write the reason in a concise
comment where the code alone would not make it obvious. Do not reintroduce
`RQ-xx`-style identifiers, traceability markers or spec documents.

## Layout

One uv workspace, two published distributions, one lockfile:

| Distribution | Path | Contains | May depend on |
| --- | --- | --- | --- |
| `pytest-vantage` | `packages/pytest-vantage` | the plugin | pytest and the standard library, nothing else |
| `vantage` | `packages/vantage` | `vantage.core`, `vantage.storage`, `vantage.service` | see below |

Inside `vantage` the dependency rule is per internal package:

| Package | May depend on |
| --- | --- |
| `vantage.core` | the standard library only — no pytest, database or web module |
| `vantage.storage` | the core and the standard library (`sqlite3`) |
| `vantage.service` | anything; the only place third-party packages (FastAPI, Pydantic, uvicorn, PyYAML) are allowed |

Clean architecture: ports are `typing.Protocol`, not abstract base classes, so an
adapter satisfies a port by shape, without importing or subclassing the
protocol. It still imports the core's domain and value types; the core never
imports an adapter.

The server ships one adapter, `SqliteExecutionStore`. `InMemoryExecutionStore`
is a test double in `packages/vantage/tests/memory_store.py`; the shared port
contract (`vantage_port_contract.py`) runs against both.

`README.md` describes behaviour for users, `docs/architecture.md` is the
maintainer's map, and `docs/api/v1-ingestion.md` plus
`packages/vantage/src/vantage/service/openapi/v1.yaml` define the HTTP contract.
Keep them true when behaviour changes.

**The plugin never opens a database.** It reports to `POST /api/v1/runs` with
`urllib` and `json`, and the server performs every write. That is what keeps the
plugin dependency-free; installing `pytest-vantage` must never pull in `vantage`.
The contract between the two is the versioned HTTP API, not their version
numbers — they release independently on prefixed tags.

## Behaviour that is easy to get wrong

- **Recording is opt-in by command-line flag only.** `--vantage` is the one
  switch. An ini value, a config file or an environment variable may say *where*
  the server is, never *whether* to record: a value committed by one person must
  not silently enable recording for everyone who clones the repository. The
  same holds for `--vantage-failure-text` and `--vantage-metadata`. All three
  count only when typed: the plugin checks `config.invocation_params.args`, so
  the same flags arriving through `addopts`, `PYTEST_ADDOPTS` or an `@file` are
  ignored with a warning. Tests for this are differential — run once with the
  flag absent and once with `-p no:vantage` and compare the trees — because
  pytest itself writes `.pytest_cache` and `__pycache__`.
- **Plugin failures never change the suite's exit status**, with one
  deliberate exception: with `--vantage`, an invalid address, timeout or ini
  value raises `pytest.UsageError` from `pytest_configure` (exit status 4)
  before any test runs, because recording was asked for and can never work.
  Everything after that is a warning. An unreachable server is found by a
  preflight and produces one warning; an error while reporting is caught by
  the fault-isolation boundary around every recorder hook and also produces
  one warning. A hang is worse than a failure, so every request is bounded by
  a timeout.
- **xdist.** Every worker re-runs `pytest_configure` as a session of its own and
  every result reaches the controller too. Branch on the config's `workerinput`
  attribute: workers collect failure evidence only; the `Recorder` exists only on
  the controller.
- **Timestamps** are stored as fixed-width ISO-8601 UTC text so lexicographic
  order is chronological order. The server normalizes everything it receives.
- **Python 3.10 is the floor.** `StrEnum`, `datetime.UTC` and `tomllib` are
  3.11+ and must not be used.
- **Schema.** `schema.sql` is applied whole on first open, in the same
  transaction that stamps `meta.schema_version` from `_SCHEMA_VERSION` in
  `storage/connection.py` (the only version literal); a database stamped with
  any other version is refused rather than migrated. There is no migration
  framework, and no table or column exists before code writes it.

## Conventions

- No domain class name starts with `Test` — pytest would collect it. The
  aggregate is `Execution`, its identity is `Identity`.
- Pydantic belongs at system boundaries and lives in `vantage.service` only.
  `vantage.core`, `vantage.storage` and the plugin use standard-library
  `dataclasses` and hand-written validation over `json`.
- Comments explain why, briefly, in present tense. No process history.
- All project documentation is in English.
- Branch names are short and descriptive (`fix/xdist-dedup`,
  `feat/run-list-filter`). Commits are signed with the 1Password SSH key.

## Constraints

These come from the author's employment situation and are operative rules:

- **Synthetic data only.** Every fixture and every example is generated. No test
  suite, log, trace or artefact from ASML or TMC ever touches this repository.
- **Nothing in the semiconductor, EDA or RTL domain.**
- **Personal equipment, outside TMC hours.**
- **The repository is public** (MIT). Nothing confidential, personal or
  regulated is committed to it.

## Commands

```bash
uv sync --extra dev                                  # whole workspace, one lockfile
uv run --extra dev pytest                            # every package
uv run --extra dev pytest packages/pytest-vantage    # one distribution
uv run --extra dev pytest -k test_name               # one test
uv run --extra dev pytest -m 'not slow'              # fast local loop; CI runs everything
uv run --extra dev ruff format . && uv run --extra dev ruff check --fix .
uv run --extra dev mypy .                            # strict
uv run --extra dev deptry .                          # undeclared / unused dependencies
uv run --extra dev pip-audit                         # CVEs
vantage --database ./vantage.db                      # run the server (default 127.0.0.1:8765)
```

## Quality gates

| Layer | Contents |
| --- | --- |
| pre-commit | `ruff format`, `ruff check --fix` and hygiene hooks, modified files only |
| pre-push | `mypy --strict` over the whole project; the tests not marked `slow` |
| CI | `ruff format --check`, `ruff check` and `mypy` again (not the pre-commit hygiene hooks), every test on the 3.10–3.13 × with/without-xdist matrix, a job that runs the suite with non-loopback networking blocked, `deptry`, a clean-environment install proving the plugin adds exactly one distribution, a job proving Python 3.9 refuses the install, and a build of both wheels |
| Weekly | `pip-audit` |

Coverage is not measured. The plugin's dependency boundary has three guards that
catch different failures: the AST import walk in
`packages/pytest-vantage/tests/test_plugin_imports.py` (the plugin importing
anything beyond the standard library and pytest), `deptry` (an undeclared
import), and the clean-environment install (what actually lands in a user's
environment). `packages/vantage/tests/test_architecture.py` applies the same
walk to `vantage.core` and `vantage.storage`.
