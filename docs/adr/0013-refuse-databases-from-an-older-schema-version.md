# 13. Refuse databases from an older schema version rather than migrating them

Date: 2026-08-19

## Status

Accepted

## Context

ADR-5 decided that the complete schema is created the first time a database is
opened, and that no migration framework ships in Phase 1. It shipped a `meta`
table with a `schema_version` row as the seam a future migration would hang
off — "a version marker, not a migration framework". RQ-29 states the same
obligation as a requirement, and its second acceptance criterion is literal:
opening an existing database issues **no schema-altering statement**.

Neither decision said what a later Phase 1 release does when it needs a column
an earlier release's database does not have. Until now nothing did, so the
question stayed theoretical.

The `session-lifecycle` change ends that. It adds `run.last_contact_at`, the
first column added since the reset. `CREATE TABLE IF NOT EXISTS` does not add a
column to a table that already exists, so a database created by the Milestone-1
release would keep its old `run` table and every write naming the new column
would fail with `no such column` — at write time, per request, on a server that
started cleanly and looked healthy.

Two further facts bear on the choice. The seam ADR-5 shipped was never
finished: `schema.sql` creates the `meta` table but nothing has ever written a
row into it, so an existing database's `schema_version` is not `1` — it is
absent. And the project is pre-1.0, with synthetic data only, no releases and no
deployments, so the population of databases this decision can inconvenience today
is empty.

The decision matters anyway, because it is not really about one column. Whatever
this release does when it meets an older database is what every later release
will be expected to do, and by the time that expectation is expensive to change,
someone will be holding data.

## Decision

**A release refuses to open a database created by a different schema version.**

`meta.schema_version` becomes a real stamp rather than a decoration: `schema.sql`
writes it as part of the same transaction that creates the schema it describes,
so no database can exist without one. This release stamps version `2`; a database
whose stamp is absent, unparseable, lower, or higher is refused.

The refusal names the version found, the version required, and the database path,
and tells the operator to recreate the database. It is raised at open, so it
surfaces once when the server starts — not per request — and the server exits
non-zero with that message rather than a traceback. It issues no DDL, alters
nothing, and deletes nothing.

Refusing is not altering, so RQ-29's second criterion remains literally true and
ADR-5 is untouched rather than eroded.

Higher versions are refused for the same reason lower ones are: a build that does
not know a column cannot honour whatever invariant the build that added it
assumed. One rule in both directions is easier to reason about than two rules
with a gap between them.

## Consequences

- An operator holding a database from an earlier release loses it. The message
  tells them so plainly and immediately, which is the whole improvement over
  discovering it as a write failure hours later.
- Recorded history is not preserved across a schema change. Anyone who wants it
  preserved must export it before upgrading; there is no tool for that, and this
  decision does not add one.
- The cost of a schema change stays visible. RQ-29 exists because a migration
  framework, once available, is what makes a casual schema change feel affordable.
  Refusing keeps each schema change a decision someone has to defend rather than
  a file someone adds.
- `meta.schema_version` acquires a reader for the first time, which means the
  stamp is now load-bearing: a release that forgets to bump it will silently open
  a database whose shape it does not match. Bumping the constant becomes part of
  the checklist for any schema change, and the schema manifest is where that is
  recorded.
- Every future schema change inherits this. The first release after real users
  hold data will have to either honour it — and refuse them — or supersede this
  ADR with the migration path ADR-5 declined to build. That is the moment this
  decision is designed to force, and forcing it deliberately is preferable to
  arriving at it by accident.
- Nothing about the runtime cost of an ordinary open changes: one indexed lookup
  in a two-column table, once per process.

## Alternatives rejected

**Idempotent `ALTER TABLE … ADD COLUMN` on open.** Preserves every existing
database, costs a few lines, and SQLite supports it directly. Rejected on two
counts. It violates RQ-29's second criterion literally — `ALTER TABLE` is a
schema-altering statement issued against an existing database, which is the exact
thing the criterion forbids. And it is step one of the migration framework ADR-5
refused: the second column needs a second `ALTER`, the first column that needs a
backfill needs ordering, and ordering needs versioning, at which point the
framework exists without anyone having decided to build one. RQ-29 exists
*because* having that available is what makes a schema change feel free.

**Open an older database read-only and degrade.** Keeps the data reachable and
fails only the writes that need the new column. Rejected because it converts one
clear failure into a permanently ambiguous state: a server that is running,
answering, and quietly recording nothing is worse than a server that refused to
start, and RQ-21's whole argument about bounded, named failure applies with more
force to the server than to the plugin. It also doubles the write path forever —
every writer would need a "does this database support the column" branch — for a
population of databases that is currently empty.

**Ship the column and let older databases fail at write time.** Costs nothing to
implement. Rejected as the worst of the three: the failure arrives per request,
long after start-up, on a server that gave every appearance of being healthy, and
the operator's first evidence is a missing row rather than a message.

Bound to: ADR-5 (complete schema at first use, no migration framework in Phase 1),
RQ-29 (complete schema from first use), and the `recording-schema` capability's
"A database from an older schema version is refused, not altered" scenario.
