# Delta for Session Report Ingestion

The session report envelope grows a `results` section alongside the existing
`run` section. It is a sibling on the envelope, which is deliberately tolerant
of unknown sections for exactly this purpose; the run report itself is
unchanged and the endpoint stays at `/api/v1/runs`. RQ-41's idempotency and
RQ-42's rejection behaviour have to hold for the new section too, so both
requirements are restated below with the scenarios that cover it.

## MODIFIED Requirements

### Requirement: Session report ingestion (RQ-41)

When a client submits a well-formed session report to the versioned
ingestion endpoint, the server MUST store that session and acknowledge it. A
retried submission of an already-stored report MUST NOT create a duplicate
run, and MUST NOT create duplicate results.

A session report MAY carry a `results` section alongside its run. That section
is OPTIONAL: a well-formed report without it MUST still have its run stored and
acknowledged. This is the supported skew case between independently released
plugin and server versions, not an error.
(Previously: the report carried a run alone, so neither the storage obligation
nor idempotency said anything about results.)

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
(Previously: rejection covered a report carrying a run alone; nothing said what
an unparseable result entry does to the rest of the report.)

#### Scenario: Missing required field (RQ-42.1)
- GIVEN an empty database
- WHEN a report with a missing required field is submitted
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Invalid JSON (RQ-42.2)
- GIVEN an empty database
- WHEN a payload that is not valid JSON is submitted
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Body truncated midway (RQ-42.3)
- GIVEN an empty database
- WHEN a report is submitted whose body is truncated midway
- THEN the response reports the rejection and the run table stays empty

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
