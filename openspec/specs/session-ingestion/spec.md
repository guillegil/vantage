# Session Report Ingestion Specification

## Purpose

Defines the versioned HTTP endpoint that accepts a session report from
`pytest-vantage`: what a well-formed report does, including idempotent
retry, and what a malformed one does — reject and store nothing. New
capability made necessary by ADR-9; no previous spec covered ingestion.

**Component:** across the boundary — `pytest-vantage` is the client;
`vantage.service` owns the endpoint, and the storage/rejection decision is
entirely server-side.

## Requirements

### Requirement: Session report ingestion (RQ-41)

When a client submits a well-formed session report to the versioned
ingestion endpoint, the server MUST store that session and acknowledge it. A
retried submission of an already-stored report MUST NOT create a duplicate
run, and MUST NOT create duplicate results.

A session report MAY carry a `results` section alongside its run. That section
is OPTIONAL: a well-formed report without it MUST still have its run stored and
acknowledged. This is the supported skew case between independently released
plugin and server versions, not an error.

#### Scenario: Well-formed report is stored and acknowledged (RQ-41.1)
- GIVEN an empty database
- WHEN a well-formed session report is submitted to `/api/v1/runs`
- THEN the run table holds one row and the response acknowledges it with the identifier stored

#### Scenario: A report carrying results stores them with the run (RQ-41.1)
- GIVEN an empty database
- WHEN a well-formed session report carrying a `results` section of N entries is submitted to `/api/v1/runs`
- THEN the run table holds one row, N result rows are stored against it, and the response acknowledges it with the identifier stored

#### Scenario: A report with no results section still records its run (RQ-41.1)
- GIVEN an empty database and a server newer than the client that reports to it
- WHEN a well-formed session report carrying no `results` section is submitted to `/api/v1/runs`
- THEN the run table holds one row and the response acknowledges it, rather than the report being rejected

#### Scenario: Retried report is idempotent (RQ-41.2)
- GIVEN a session report that has already been submitted
- WHEN the identical report is submitted a second time
- THEN the run table still holds one row for that session and the response acknowledges it

#### Scenario: Retried report does not duplicate results (RQ-41.2)
- GIVEN a session report carrying N results that has already been submitted and stored
- WHEN the identical report is submitted a second time
- THEN that session still holds exactly N result rows and the response acknowledges it rather than failing

#### Scenario: Unversioned path is refused (RQ-41.3)
- GIVEN a running server
- WHEN the ingestion endpoint is requested at an unversioned path
- THEN the request is refused rather than served

### Requirement: Malformed report rejection (RQ-42)

If a submitted session report cannot be understood, then the server MUST
reject it and MUST store nothing from it. The rejection response MUST name
the offending field or condition without exposing internal identifiers or a
traceback.

A `results` section that cannot be understood MUST reject the **entire**
report, including its run and every other result it carried. The server MUST
NOT store the parseable subset: RQ-3 requires a session to be observable in
full or not at all, and a session stored minus the entries that failed to parse
is a partial write wearing the appearance of a whole one.

Rejecting a report never removes or alters a run entry created by an earlier
*accepted* report for the same session. A rejected report stores nothing
**from that report**; it does not undo what a prior accepted report already
wrote.
(Previously: RQ-42.3 asserted a truncated report leaves the run table empty
in every case, which assumed no prior report for the session could exist.)

#### Scenario: Missing required field (RQ-42.1)
- GIVEN an empty database
- WHEN a report with a missing required field is submitted
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Invalid JSON (RQ-42.2)
- GIVEN an empty database
- WHEN a payload that is not valid JSON is submitted
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Body truncated midway, no prior report (RQ-42.3)
- GIVEN an empty database with no prior report for this session
- WHEN a report is submitted whose body is truncated midway
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Finish report truncated after an accepted start-write (RQ-42.3, RQ-3.2)
- GIVEN a session whose start-write has already been accepted, leaving a run entry with a null `finished_at`
- WHEN that session's finish report is submitted with its body truncated midway
- THEN the response reports the rejection
- AND the run entry is left exactly as the start-write wrote it — a start time, a null `finished_at`, and no result rows — rather than the run table being emptied or the finish report's data being applied

#### Scenario: One malformed result rejects the whole report (RQ-42.1, with RQ-3.2)
- GIVEN an empty database
- WHEN a report carrying 500 results is submitted whose single result at index 250 is malformed
- THEN the response reports the rejection
- AND the run table stays empty and no result row is stored, rather than the 499 parseable entries being kept

#### Scenario: Rejection names the cause, safely (RQ-42.4)
- GIVEN a rejected report
- WHEN the response is read
- THEN it names which field or condition caused the rejection, without exposing internal identifiers or a traceback
- AND where the cause is an entry in the `results` section, it names that entry and its offending field
