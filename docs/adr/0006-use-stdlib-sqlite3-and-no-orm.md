# 6. Use standard-library `sqlite3` and no ORM

Date: 2026-08-14

## Status

Accepted on 2026-08-16, when PR #19 merged Milestone 1 into `main`.

## Context

`vantage.storage` needs to write to and read from a SQLite database. Python
ships `sqlite3` in the standard library; SQLAlchemy (with or without
Alembic for migrations) is the alternative the deleted `src/vantage` tree
used, and it is exactly what RQ-24 (zero third-party runtime dependencies
in `vantage.core`, `vantage.storage` and `pytest-vantage`) rules out for
this package.

An ORM's usual case -- mapping rows to objects across a schema that
changes under active development, with relationships to navigate -- is a
weaker argument here: ADR-5 fixes the schema whole at first use, and the
storage port (`ExecutionStore`, ADR-3) is a small, hand-written contract,
not a general query surface.

## Decision

Use stdlib `sqlite3` directly, with hand-written SQL in
`schema.sql` and the writer modules. No ORM, in any package.

## Consequences

- Every schema change is hand-written SQL, checked against
  `docs/schema-manifest.md` by inspection (RQ-29) rather than expressed
  once in a declarative model and diffed automatically.
- Query construction has no protection against a malformed SQL string
  beyond what `test_schema_manifest.py` and the contract suite exercise;
  an ORM's query builder would catch some classes of typo at
  construction time that hand-written SQL only fails at execution.
- Object-relational mapping -- turning a `result` row plus its markers and
  parameters into one Python object graph -- is code Vantage now owns and
  maintains itself, rather than a solved problem an ORM would have
  provided.
- `PRAGMA` handling, connection lifecycle (WAL mode, busy timeout,
  `BEGIN IMMEDIATE`) and adapter code are all hand-rolled in
  `vantage/storage/connection.py`, each a place a future contributor
  unfamiliar with SQLite's concurrency model could get wrong in a way an
  ORM's connection pool might have hidden.
