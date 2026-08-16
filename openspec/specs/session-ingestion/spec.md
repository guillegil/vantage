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
run.

#### Scenario: Well-formed report is stored and acknowledged
- GIVEN an empty database
- WHEN a well-formed session report is submitted to `/api/v1/runs`
- THEN the run table holds one row and the response acknowledges it with the identifier stored

#### Scenario: Retried report is idempotent
- GIVEN a session report that has already been submitted
- WHEN the identical report is submitted a second time
- THEN the run table still holds one row for that session and the response acknowledges it

#### Scenario: Unversioned path is refused
- GIVEN a running server
- WHEN the ingestion endpoint is requested at an unversioned path
- THEN the request is refused rather than served

### Requirement: Malformed report rejection (RQ-42)

If a submitted session report cannot be understood, then the server MUST
reject it and MUST store nothing from it. The rejection response MUST name
the offending field or condition without exposing internal identifiers or a
traceback.

#### Scenario: Missing required field
- GIVEN an empty database
- WHEN a report with a missing required field is submitted
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Invalid JSON
- GIVEN an empty database
- WHEN a payload that is not valid JSON is submitted
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Body truncated midway
- GIVEN an empty database
- WHEN a report is submitted whose body is truncated midway
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Rejection names the cause, safely
- GIVEN a rejected report
- WHEN the response is read
- THEN it names which field or condition caused the rejection, without exposing internal identifiers or a traceback
