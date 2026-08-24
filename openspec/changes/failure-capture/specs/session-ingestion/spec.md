# Delta for Session Report Ingestion

## ADDED Requirements

### Requirement: Optional failure-evidence fields in results[] entries

Each entry in a well-formed report's `results[]` section MAY carry the
failure-evidence fields defined in `failure-evidence` — traceback, failure
type, message, path, line number, representation, captured stdout,
captured stderr, skip reason, xfail reason, and their truncation flags.
Every one of these fields MUST default to absent, so a `results[]` entry
omitting them is still well-formed and stored: a plugin that predates this
change reports normally against a server that already expects them, and a
plugin sending them reports normally against a server that does not
recognize them yet, under the report's existing tolerance for unrecognized
keys.

**Verification: Test**, at the endpoint level (`/api/v1/runs`), independent
of the storage-level scenarios in `failure-evidence`.

#### Scenario: An older plugin omitting the fields still stores its run and results
- GIVEN an empty database and a plugin predating this change
- WHEN a well-formed session report whose `results[]` entries carry none of the failure-evidence fields is submitted
- THEN the run table holds one row, its results are stored, and every failure-evidence field on them is absent, rather than the report being rejected

#### Scenario: A newer plugin's failure-evidence fields are persisted
- GIVEN an empty database
- WHEN a well-formed session report whose `results[]` entries carry failure-evidence fields is submitted
- THEN those fields are stored against their results and the response acknowledges the report

#### Scenario: An older server tolerates a newer plugin's failure-evidence fields
- GIVEN a server that does not yet recognize the failure-evidence fields
- WHEN a session report whose `results[]` entries carry those fields is submitted
- THEN the report is accepted and stored, with the unrecognized fields ignored rather than causing rejection

### Requirement: Whole-report rejection at the size cap

A session report whose body exceeds the server's per-report byte cap MUST be
rejected in its entirety: no run entry and no result row from that report
MUST be stored. This mirrors RQ-42's malformed-report handling — an
oversized report is discarded whole, never as a partial write — and applies
regardless of which fields pushed the report over the cap.

**Verification: Test.**

#### Scenario: A report exceeding the size cap stores nothing
- GIVEN an empty database
- WHEN a session report whose encoded body exceeds the per-report byte cap is submitted
- THEN the response reports the rejection and the run table stays empty

#### Scenario: A report carrying failure evidence within the cap is accepted normally
- GIVEN an empty database
- WHEN a well-formed session report carrying failure-evidence fields, whose encoded body stays within the per-report byte cap, is submitted
- THEN the run table holds one row, its results are stored with their failure-evidence fields, and the response acknowledges it
