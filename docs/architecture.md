# Architecture Notes

Clean architecture (`docs/adr/0003-use-clean-architecture-rather-than-hexagonal.md`),
two published distributions with an HTTP boundary between them
(`docs/adr/0004-publish-two-distributions-with-an-http-boundary.md`,
`docs/adr/0009-record-over-http-and-let-the-server-own-every-write.md`).

This file is a map. The decisions live in `docs/adr/`; the Milestone 1 design
lives in `openspec/changes/milestone-1-write-one-row/design.md`; the
requirements live in Notion under PROJ-1 and nowhere else.

## Two distributions, one repository

```
packages/
├── pytest-vantage/          published as `pytest-vantage`
│   └── src/pytest_vantage/  the plugin. pytest and the standard library,
│                            nothing else (RQ-24)
└── vantage/                 published as `vantage`
    └── src/vantage/
        ├── core/            domain model, storage port (typing.Protocol),
        │                    option resolution -- stdlib only (RQ-26)
        ├── storage/         schema, the sqlite3 adapter, an in-memory one
        │                    -- depends on core alone (RQ-30)
        └── service/         ingestion endpoint and read API -- the only
                             package allowed a third-party dependency
```

```bash
pip install pytest-vantage      # in the project under test
pip install vantage             # wherever the server runs
pip install vantage[postgres]   # + the driver
```

They release independently through prefixed tags — `pytest-vantage-v1.2.0`,
`vantage-v1.4.0` — and CI builds only the package a tag names.

## The plugin never touches the database

`pytest-vantage` reports finished sessions to the server over HTTP, and the
server performs every write (ADR-9). The plugin holds no database code, no
schema knowledge and no storage port.

Four pressures decided this, and no two of them alone would have:

- **A database driver cannot live in a user's test virtualenv.** Supporting
  PostgreSQL from the plugin would mean `psycopg` beside somebody's real
  dependencies, which is the conflict RQ-24 names.
- **A plugin for another language cannot import Python.** If Vantage ever
  records `vitest` runs, that plugin is JavaScript. The only boundary both
  can speak is a protocol.
- **SQLite serialises writers**, and everything the previous design carried
  to work around that — WAL, `BEGIN IMMEDIATE`, a busy timeout, a
  network-filesystem fallback — belongs behind the server's storage port
  instead.
- **Live monitoring** against a shared file means polling a table; against a
  process already being told, it is free.

It costs the frictionless install, and that cost is real: recording now needs
a running server, in CI as much as on a laptop.

### The contract is the API version, not the package versions

Plugin and server release on their own cadences, so a 1.2 plugin will meet a
1.5 server. That is ordinary — it is the everyday case in any networked
system — and it works only because `/api/v1` is what both sides agree on.

Not all version skew is equal. Across shared *imports* it is fatal and often
silent; across a versioned HTTP boundary it is expected. Treating them as one
risk is what produced two earlier packaging answers that had to be replaced.

## Two independent decisions

**Activation is a command-line flag, never a file or an environment
variable.** A configuration file or an environment variable may set *where*
the server is; neither may turn recording on. A value committed by one person
must not silently start reporting for everyone who clones the repository, and
an environment variable is invisible in the command line RQ-11 records — so a
run enabled by one could not be reproduced from its own history (RQ-2).

**Failure has two disjoint paths.** One before reporting is possible — an
unreachable server, discovered without breaking anything — warns naming the
address and runs the session unrecorded (RQ-37). The other is an internal
error raised while reporting is underway, caught by a boundary around every
recorder hook, which warns once and disables itself (RQ-21). Both leave the
suite's own exit status untouched, including a non-zero one.

A third case exists only because the boundary is now a network: a server that
accepts the connection and never answers. A bounded timeout is part of the
obligation, not an implementation detail — a hang is worse than a failure,
because the user cannot tell whether the suite is slow or stuck.

## Schema

`packages/vantage/src/vantage/storage/schema.sql` is created complete at first
use, including columns no code populates until a later phase (ADR-5).
`docs/schema-manifest.md` is the column-by-column record it is checked against
by inspection (RQ-29).

The manifest has a second job once a remote store exists: everything recorded
locally must fit the remote schema, so the manifest is the data model *both*
stores satisfy rather than a description of one of them.

## Quality gates

`ruff`, `mypy --strict`, `deptry` and `pip-audit` run from the workspace root
across both distributions.

Three guards protect RQ-24 and RQ-26 and none is redundant: the AST
architecture test catches `vantage.core` importing an infrastructure module,
`deptry` catches an undeclared third-party import in source, and a
clean-environment install check catches what actually reaches a user's
environment. Different failure modes, different layers.
