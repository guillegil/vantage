# Recording Schema Specification

## Purpose

Defines the completeness and stability guarantee on the database schema:
every documented column, including Phase 2 columns nothing populates yet,
exists from the schema's first creation, and no later Phase 1 release
alters it.

## Requirements

### Requirement: Complete schema from first use

The system SHOULD create its complete database schema when a database is
first created, including columns that no code populates until a later
phase, so that no Phase 1 release alters the schema of an existing database.

**Verification method: Inspection, not Test.** The obligation is proven by
comparing a freshly created schema against a documented column manifest and
by inspecting the DDL a later release issues against an existing database —
not by a pass/fail assertion in a test. The inspection artifacts are: (1)
the column manifest document, and (2) a record of the SQL statements issued
when an existing database is opened.

#### Scenario: Fresh database matches the column manifest

- GIVEN a freshly created database
- WHEN its schema is compared against the documented column manifest
- THEN every documented column exists

#### Scenario: Opening an existing database issues no schema-altering statement

- GIVEN a database created by an earlier Phase 1 release
- WHEN a later Phase 1 release opens it
- THEN the system issues no schema-altering statement
