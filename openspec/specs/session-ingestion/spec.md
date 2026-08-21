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

## ADDED Requirements

### Requirement: Optional VCS section acceptance

A session report MAY carry a `vcs` section alongside its run. That section is
OPTIONAL: a well-formed report without it MUST still have its run stored and
acknowledged — the supported skew case for a plugin that predates this
change, mirroring how `results` is already optional under RQ-41. When
present, the server MUST persist its six fields onto the run entry. The
ingestion endpoint MUST NOT require a capability negotiation or version gate
before accepting a `vcs` section.

**Verification: Test**, at the endpoint level (`/api/v1/runs`), independent
of the storage-level scenarios in `version-control-context`.

#### Scenario: A report carrying a vcs section persists its six fields
- GIVEN an empty database
- WHEN a well-formed session report carrying a `vcs` section is submitted to `/api/v1/runs`
- THEN the run entry holds the reported commit hash, branch, commit subject and dirty flag

#### Scenario: A report with no vcs section still records its run
- GIVEN an empty database and a plugin predating this change
- WHEN a well-formed session report carrying no `vcs` section is submitted to `/api/v1/runs`
- THEN the run table holds one row with all six vcs fields null, and the response acknowledges it, rather than the report being rejected

#### Scenario: The endpoint accepts a vcs section without any capability check
- GIVEN a running server that has never advertised a `vcs`-related capability
- WHEN a well-formed session report carrying a `vcs` section is submitted to `/api/v1/runs`
- THEN the report is accepted and stored, with no prior capability probe required

### Requirement: Capability advertisement

The server MUST advertise, at a documented endpoint, whether it can complete
a session lifecycle, and a client MUST NOT begin one against a server that
has not said so.

A newer plugin against an older server previously lost the end of every run
in silence: the start-write created the row, the older server's
`ON CONFLICT DO NOTHING` discarded the finish, and the acknowledgement said
`200 duplicate`. Result rows still inserted, so the damage did not even look
empty.

**One capability, not a version number** (D38). There is one thing a client
needs to know, and a version scheme invented before a second capability
exists would be guessing at a shape nothing yet constrains. A later capability
adds a key, which an older client ignores.

**The check fails closed** (D40). Only an explicit positive answer enables
the lifecycle. Anything else — a missing route, a malformed body, a value of
the wrong type, an explicit `false`, an empty body, a server error, a
connection that hangs — degrades. A capability check that fails open is worse
than none, because it promises a guarantee it does not hold.

**Degrading means the previous release** (D41), not a third state: no
start-write, no heartbeats, and a finish report byte-identical to the one
that shipped before the lifecycle existed.

#### Scenario: The server advertises the session lifecycle
- GIVEN a running server
- WHEN `GET /api/v1/capabilities` is requested
- THEN it answers `200` with `{"session_lifecycle": true}`

#### Scenario: The advertisement is versioned like every other route
- GIVEN a running server
- WHEN `GET /capabilities` is requested without the version prefix
- THEN it answers `404`, matching the absence rule the run routes already follow

#### Scenario: An older server's missing route is an answer, not a failure
- GIVEN a server that predates this capability and has no such route
- WHEN a client probes it
- THEN the probe reports the lifecycle unavailable rather than raising or warning about a transport error

**Verification: Test.** This is the row that matters most, because every
server needing detection is already published — nothing can change on that
side, which is the only reason the design works at all.

#### Scenario: Every non-positive answer degrades
- GIVEN a server answering with malformed JSON, valid JSON of the wrong type, an explicit `false`, an empty body, a `500`, or a connection that hangs past the liveness timeout
- WHEN a client probes it
- THEN every one of those answers reports the lifecycle unavailable

**Verification: Test**, table-driven — one row per way a check could quietly
fail open. Proven by mutation: a probe that returns true unconditionally turns
the whole table red.

#### Scenario: A degraded session records exactly as the previous release did
- GIVEN a server that does not advertise the lifecycle
- WHEN a session is recorded against it
- THEN no start-write and no heartbeat are sent, one warning is emitted on the liveness path, and the finish report is byte-identical to the pre-lifecycle shape
- AND result recording is unaffected

#### Scenario: The probe is bounded by the liveness timeout
- GIVEN a configured report timeout longer than the liveness timeout
- WHEN the capability probe runs
- THEN it is bounded by the liveness timeout, never the report timeout

**Verification: Test.** A probe that could block for the report timeout would
put that cost in front of every session.
