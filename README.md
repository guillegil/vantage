# Vantage

[![CI](https://github.com/guillegil/vantage/actions/workflows/ci.yml/badge.svg)](https://github.com/guillegil/vantage/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10-3.13](https://img.shields.io/badge/python-3.10--3.13-blue.svg)](https://www.python.org/downloads/)

A pytest run leaves nothing behind but terminal scrollback. Vantage records
what a test suite did, over time, and on which commit -- so that *when did
this start failing, and what changed* is a question you read the answer to
rather than bisect for.

## Status

Milestone 1: the plugin is installable and one pytest invocation leaves one
recorded run. Nothing user-visible yet -- this milestone is dogfooding
readiness, not a feature. See `docs/architecture.md` for the design and
`docs/adr/` for the decisions behind it.

## How it fits together

```
pytest + pytest-vantage  ──HTTP──>  vantage  ──>  SQLite or PostgreSQL
                                       │
                                       └──>  web interface
```

The plugin reports finished sessions to the server; **the server performs
every write** (`docs/adr/0009-record-over-http-and-let-the-server-own-every-write.md`).

That is what keeps the plugin free of dependencies. It holds no database
code, no schema knowledge and no driver, so it can never conflict with
anything in the environment it lands in -- and a plugin for another test
runner becomes possible later, because the boundary is a protocol rather
than an import.

The cost is honest: recording needs a running server, in CI as much as on a
laptop.

## Install

```bash
pip install pytest-vantage      # in the project under test. pytest and stdlib only
pip install vantage             # wherever the server runs
pip install vantage[postgres]   # + the PostgreSQL driver
```

Two distributions from one repository
(`docs/adr/0004-publish-two-distributions-with-an-http-boundary.md`),
released independently through prefixed tags. The contract between them is
the versioned HTTP API, not their version numbers -- so a 1.2 plugin talking
to a 1.5 server is ordinary rather than a bug.

## Architecture

Clean architecture, not hexagonal
(`docs/adr/0003-use-clean-architecture-rather-than-hexagonal.md`). Ports are
`typing.Protocol`, not abstract base classes, so an adapter satisfies a port
without importing the core.

| Package | Depends on | What it is |
| --- | --- | --- |
| `pytest_vantage` | pytest, standard library | the plugin. Reports over HTTP, writes nothing |
| `vantage.core` | nothing | domain model, storage port, option resolution. Standard library only |
| `vantage.storage` | core | the schema and its adapters -- SQLite first, others later |
| `vantage.service` | anything | ingestion endpoint and read API. The only one allowed a third-party dependency |

## Development

```bash
uv sync --extra dev                                  # whole workspace, one lockfile
uv run --extra dev pytest                            # everything
uv run --extra dev pytest packages/pytest-vantage    # one distribution
uv run --extra dev pytest -m 'req("RQ-2")'           # everything verifying one requirement
uv run --extra dev ruff format . && uv run --extra dev ruff check --fix .
uv run --extra dev mypy .                            # strict
uv run --extra dev deptry .                          # undeclared / unused dependencies
uv run --extra dev pip-audit                         # CVEs
```

## Requirement traceability

Every test that verifies a requirement carries `@pytest.mark.req("RQ-nn")`.
Where verification is not a test -- a CI matrix job, an inspection artifact --
the ID goes in a comment on the block that proves it. The invariant:
`grep -r "RQ-12"` reaches the thing that proves RQ-12.

Requirements live in Notion under PROJ-1 and nowhere else. This repository
mirrors them one way; ADRs go the other way, with `docs/adr/` as the source
of truth and Notion mirroring it.
