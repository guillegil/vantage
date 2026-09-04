# Delta for Recording Schema

## MODIFIED Requirements

### Requirement: Complete schema from first use (RQ-29)

The server SHOULD create its complete database schema when a database is
first created, including columns that no code populates until a later
phase, so that no Phase 1 release alters the schema of an existing database.

Where a later Phase 1 release needs a table or column an earlier one did
not have — such as the `run_metadata` table added by this change, or the
`user_setting` table added by an earlier change — it MUST bump
`meta.schema_version` and MUST refuse to open a database created by an
older schema version, naming the version found and the version required,
rather than issuing an `ALTER TABLE` or `CREATE TABLE` against it. Refusing
is not altering. This change bumps `meta.schema_version` from 3 to 4.
(Previously: the concrete example was the `user_setting` table and the bump
from 2 to 3; the obligation itself — bump and refuse rather than alter —
is unchanged.)

**Verified by Inspection, not Test.** The deliverable is a comparison
between a freshly created schema, including `run_metadata` and its
`(key, value)` index, and the documented column manifest at
`docs/schema-manifest.md`, plus a record of the SQL statements a later
release issues against an existing database — not a pass/fail assertion.

#### Scenario: Fresh database matches the column manifest
- GIVEN a freshly created database
- WHEN its schema is compared against `docs/schema-manifest.md`
- THEN every documented column exists, including every `run_metadata` column
- AND the `(key, value)` index on `run_metadata` is present, alongside every other documented index

#### Scenario: Opening an existing database issues no schema-altering statement
- GIVEN a database created by an earlier Phase 1 release
- WHEN a later Phase 1 release opens it
- THEN the system issues no schema-altering statement

#### Scenario: A database from an older schema version is refused, not altered
- GIVEN a database created before this change, predating the `run_metadata` table and this change's `meta.schema_version` value of 4
- WHEN a release carrying this change opens it
- THEN the server refuses to open it, naming the schema version found and the version required
- AND it issues no schema-altering statement against that database
