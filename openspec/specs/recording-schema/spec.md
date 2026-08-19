# Recording Schema Specification

## Purpose

Defines the completeness and stability guarantee on the database schema:
every documented column, including Phase 2 columns nothing populates yet,
exists from the schema's first creation, and no later Phase 1 release
alters it.

**Component:** `vantage` (server) — `vantage.storage`'s `schema.sql`,
applied by the sqlite adapter at first use.

## Requirements

### Requirement: Complete schema from first use (RQ-29)

The server SHOULD create its complete database schema when a database is
first created, including columns that no code populates until a later
phase, so that no Phase 1 release alters the schema of an existing database.

Where a later Phase 1 release needs a column an earlier one did not have —
such as `run.last_contact_at`, added by this change, `TEXT NULL`, ISO-8601
UTC — it MUST bump `meta.schema_version` and MUST refuse to open a database
created by an older schema version, naming the version found and the version
required, rather than issuing an `ALTER TABLE` against it. Refusing is not
altering.
(Previously: silent on what a later release does when it needs a column an
earlier release's database does not have; only the create-fresh and
no-schema-altering-statement scenarios were specified.)

**Verified by Inspection, not Test.** The deliverable is a comparison
between a freshly created schema and the documented column manifest at
`docs/schema-manifest.md`, plus a record of the SQL statements a later
release issues against an existing database — not a pass/fail assertion.

#### Scenario: Fresh database matches the column manifest
- GIVEN a freshly created database
- WHEN its schema is compared against `docs/schema-manifest.md`
- THEN every documented column exists, including `run.last_contact_at`
- AND `idx_run_last_contact_at` is present, alongside every other documented index

#### Scenario: Opening an existing database issues no schema-altering statement
- GIVEN a database created by an earlier Phase 1 release
- WHEN a later Phase 1 release opens it
- THEN the system issues no schema-altering statement

#### Scenario: A database from an older schema version is refused, not altered
- GIVEN a database created by an earlier Phase 1 release, predating the `last_contact_at` column and this change's `meta.schema_version`
- WHEN a release carrying this change opens it
- THEN the server refuses to open it, naming the schema version found and the version required
- AND it issues no schema-altering statement against that database
