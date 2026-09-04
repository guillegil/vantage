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

### Requirement: Ingestion endpoints excluded from the read-only surface

The session-report and heartbeat ingestion endpoints MUST NOT be counted
among the document-declared read paths that `history-read-api`'s read-only
guarantee covers. Recording the boundary here, alongside the endpoints that
write, keeps "read-only" scoped by the endpoints that actually read rather
than asserted only in the capability that benefits from the exclusion.

**Verification: Inspection** — a scope assertion checked against what the
machine-readable interface document declares as a read path versus a write
path, not a behavior that can fail a runtime assertion on its own.

#### Scenario: Ingestion endpoints are marked as writing, not reading
- GIVEN the machine-readable interface document
- WHEN its entries for the session-report and heartbeat ingestion endpoints are inspected
- THEN both are marked as endpoints that write, and neither appears among the paths covered by the read-only guarantee

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

### Requirement: Optional metadata section acceptance

A session report MAY carry a `metadata` section alongside its `run` and
`vcs` sections. That section is OPTIONAL: a well-formed report without it
MUST still have its run stored and acknowledged — the same skew tolerance
already applied to `results` and `vcs`. When present, the server MUST parse
its declared files and persist the extracted keys against the run.

**Verification: Test**, at the endpoint level, independent of storage-level
scenarios in `run-metadata`.

#### Scenario: A report carrying a metadata section persists its declared keys
- GIVEN an empty database
- WHEN a well-formed session report carrying a `metadata` section is submitted to `/api/v1/runs`
- THEN the run is stored, and its declared keys and values are persisted against it

#### Scenario: A report with no metadata section still records its run
- GIVEN an empty database and a plugin predating this change
- WHEN a well-formed session report carrying no `metadata` section is submitted
- THEN the run table holds one row and the response acknowledges it, rather than the report being rejected

### Requirement: Declared-document formats

The server MUST parse declared documents encoded as JSON or YAML. A declared
document in a format the server does not support MUST be treated the same
as a malformed document: no keys are extracted from it, and the failure is
marked rather than raised.

**Verification: Test.**

#### Scenario: A JSON declared document is parsed
- GIVEN a metadata section naming a JSON-encoded declared document
- WHEN the report is ingested
- THEN its declared keys are extracted and persisted

#### Scenario: A YAML declared document is parsed
- GIVEN a metadata section naming a YAML-encoded declared document
- WHEN the report is ingested
- THEN its declared keys are extracted and persisted

#### Scenario: An unsupported format is treated as malformed
- GIVEN a metadata section naming a document in a format the server does not parse
- WHEN the report is ingested
- THEN no keys are extracted from that file, it is marked, and the run is still stored

### Requirement: Malformed declared document does not fail ingestion

A malformed declared document MUST NOT fail the report's ingestion: the file
contributes no keys, and the run records that it failed to parse, while the
run row is written regardless. A declared key absent from an otherwise
well-formed document MUST be marked absent for that run, not treated as a
parse failure. A declared key present but non-scalar (a list or a mapping)
MUST be marked uncapturable and MUST NOT be serialized into `value`.

**Verification: Test.**

#### Scenario: A malformed document does not block the run from being stored
- GIVEN a metadata section naming a declared document that is not well-formed
- WHEN the report is ingested
- THEN the run is stored, and that file contributes no keys, marked as failed to parse

#### Scenario: A declared key absent from a well-formed document is marked absent
- GIVEN a well-formed declared document that does not contain one of its declared keys
- WHEN the report is ingested
- THEN that key is marked absent for this run rather than causing rejection

#### Scenario: A non-scalar declared value is marked uncapturable, never serialized
- GIVEN a well-formed declared document whose declared key holds a list or a mapping
- WHEN the report is ingested
- THEN that key is marked uncapturable for this run, and no value is stored for it

### Requirement: Per-value bound

Each extracted value MUST be bounded to a dedicated size smaller than the
report-wide text-field bound. A value exceeding this bound MUST be dropped
whole, never truncated, and the key MUST be marked uncapturable for that
run.

**Verification: Test.**

#### Scenario: An oversized value is dropped whole, marked uncapturable
- GIVEN a declared document whose extracted value for a key exceeds the per-value bound
- WHEN the report is ingested
- THEN no value is stored for that key on this run, and it is marked uncapturable

#### Scenario: A value within bound is stored whole
- GIVEN a declared document whose extracted value for a key is within the per-value bound
- WHEN the report is ingested
- THEN that value is stored unchanged
