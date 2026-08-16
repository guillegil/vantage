# 5. Complete schema at first use, no migration framework in Phase 1

Date: 2026-08-14

## Status

Accepted on 2026-08-16, when PR #19 merged Milestone 1 into `main`.

## Context

Milestone 1 writes one table's worth of data (`run`), but the full product
(Milestones 2-3) needs nine more -- `test_case`, `result` and its satellite
tables -- with columns that only later phases populate. Two shapes are
available: create the schema incrementally, one migration per phase, or
create the whole documented schema the first time any database is opened,
leaving later phases to populate columns that already exist.

RQ-29 requires the complete schema to exist from first creation. That
requirement exists because Vantage has no users yet holding data: every
schema change up to the point someone does is free, and a migration
framework's whole cost -- versioned migration scripts, an upgrade path, a
downgrade path, a way to detect which version a database is at -- buys
nothing until there is a database worth preserving across a schema change.

## Decision

`packages/vantage/src/vantage/storage/schema.sql` declares all ten tables
and thirteen indexes documented in the RQ-29 schema manifest
(`docs/schema-manifest.md`) at once, executed with stdlib `sqlite3` the
first time a database is created. Later milestones write code that
populates the columns their requirements own; they do not alter the schema.

A `meta` table with a `schema_version` row ships now, even though nothing
reads or bumps it yet -- without a version stamp, a future migration could
not identify what it is migrating. This is a version marker, not a
migration framework, and it is the seam a future migration framework hangs
off, not a substitute for one.

## Consequences

- A column whose design turns out to be wrong is discovered only once code
  in a later milestone tries to populate it, not when the schema was
  written, so a design mistake here waits longer to surface than an
  incremental-migration approach would let it.
- Nine of the ten tables are dead weight for the entire duration of
  Milestone 1: `PRAGMA table_info` on a fresh database looks like a
  finished product long before the writer code that would justify it
  exists, which risks the schema and the implementation drifting apart.
- No migration path exists yet for the day it is actually needed: the
  first real schema change after users hold data has no framework to lean
  on, only the `schema_version` stamp recording that a change happened.
- The RQ-29 inspection artifact (`docs/schema-manifest.md`) and the schema
  itself are two documents that must be kept in sync by hand until
  `test_schema_manifest.py` lands in Phase B; until then, drift between
  them is caught by neither a test nor a build failure.
