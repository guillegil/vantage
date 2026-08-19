# Delta for Session Report Ingestion

`SessionReport` gains an optional `vcs` sibling section (RQ-10/RQ-23/RQ-39's
carrier onto the wire). `RunReport` stays `extra="forbid"`, untouched.
`SessionReport` is `extra="ignore"`, so no capability gate or version bump is
needed: an older server drops an unknown `vcs` key and records the run as it
does today, the same skew posture RQ-41 already established for `results`.

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
