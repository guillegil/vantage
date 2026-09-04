# Delta for Session Report Ingestion

## ADDED Requirements

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
