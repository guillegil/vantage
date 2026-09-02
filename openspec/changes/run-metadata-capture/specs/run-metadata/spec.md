# Run Metadata Specification

## Purpose

Defines user-declared configuration values captured from the test repository
and stored per run: the declaration surface, path containment, the
plugin-side bounding rules, the fault-tolerance posture for a malformed
declaration, and the write-once storage contract. New capability — the first
surface describing what is under test, distinct from every existing `run`
column, which describes the machine and the test repository. The declaration
file's own opt-in gate and its RQ-2 differential inertness are defined in
`opt-in-activation`; server-side document parsing and its failure taxonomy
are defined in `session-ingestion`; the table shape is defined in
`recording-schema`.

**Component:** across the boundary — `pytest-vantage` reads, validates and
bounds the declaration and the files it names, and ships raw UTF-8 content
without parsing it (RQ-24, ADR-9); `vantage.service` parses declared
documents and extracts declared keys; `vantage.storage` persists them.

## Requirements

### Requirement: Declaration file location and format

The system MUST read a declaration from `vantage-metadata.json` at the test
repository's `rootpath`, parsed with the standard library's `json` module,
naming which files to read and which keys to take from each. A malformed
declaration file MUST NOT fail the run's ingestion; the run MUST still be
recorded, with metadata capture contributing nothing for that session.

**Verification: Test.**

#### Scenario: A well-formed declaration is read
- GIVEN a `vantage-metadata.json` at rootpath naming one file and one key
- WHEN a session runs with metadata capture enabled
- THEN the named file is read and the named key is captured for that run

#### Scenario: A malformed declaration does not fail the run
- GIVEN a `vantage-metadata.json` that is not valid JSON
- WHEN a session runs with metadata capture enabled
- THEN the run is still recorded and no metadata is captured for that session

### Requirement: Missing declaration file emits a warning

Where metadata capture is enabled and no `vantage-metadata.json` exists at
rootpath, the system MUST emit a pytest warning, so a deliberate flag met
with nothing is visibly a misconfiguration rather than a silent no-op.

**Verification: Test.**

#### Scenario: The flag alone, with nothing declared, warns
- GIVEN metadata capture enabled and no `vantage-metadata.json` at rootpath
- WHEN the session runs
- THEN a pytest warning is emitted and the run proceeds unaffected otherwise

#### Scenario: A declaration present emits no such warning
- GIVEN metadata capture enabled and a `vantage-metadata.json` present at rootpath
- WHEN the session runs
- THEN no missing-declaration warning is emitted

### Requirement: Declared paths are contained under rootpath

Every declared path MUST resolve strictly under `rootpath`. Symlinks MUST be
resolved before the containment check runs. An absolute path or a path that
resolves outside `rootpath` MUST be rejected outright, never clamped into
containment.

**Verification: Test.**

#### Scenario: An absolute path is rejected
- GIVEN a declaration naming an absolute path
- WHEN the session runs with metadata capture enabled
- THEN that path is rejected and marked, and no file outside rootpath is read

#### Scenario: A `..` escape is rejected
- GIVEN a declaration naming a path that resolves outside rootpath via `..`
- WHEN the session runs with metadata capture enabled
- THEN that path is rejected rather than clamped to rootpath

#### Scenario: A symlink escape is rejected after resolution
- GIVEN a declared path that is a symlink inside rootpath pointing outside it
- WHEN containment is checked
- THEN the symlink is resolved before the check runs, and the path is rejected

### Requirement: The read surface is auditable

The run record MUST name every declared file that was actually read, storing
the path exactly as declared (rootpath-relative), not the resolved path.

**Verification: Test.**

#### Scenario: A read file is named on the run
- GIVEN a declaration naming one file that is successfully read
- WHEN the run is recorded
- THEN the run names that file by its declared, rootpath-relative path

### Requirement: Declared files are bounded, dropped whole

Each declared file MUST be bounded to 64 KiB of UTF-8 before it is included
in the report. A file exceeding this bound MUST be dropped in its entirety,
never truncated, and marked declared-but-uncaptured. The metadata section of
a session report MUST additionally be bounded on its total JSON-encoded
bytes before the request is built; when that budget is exhausted, files MUST
be dropped whole, each one marked, rather than the section being truncated.

**Verification: Test.**

#### Scenario: An oversized file is dropped whole, not truncated
- GIVEN a declared file exceeding 64 KiB of UTF-8
- WHEN the session report is built
- THEN that file's content is entirely absent from the report and the run marks it declared-but-uncaptured

#### Scenario: The metadata section budget drops files, not bytes
- GIVEN declared files whose combined content would exceed the metadata section's per-report budget
- WHEN the session report is built
- THEN files are dropped whole until the section fits, and every dropped file is marked

### Requirement: Non-UTF-8 declared files are rejected before encoding

A declared file that is not UTF-8 decodable MUST be rejected as uncapturable
before the report is JSON-encoded, rather than causing the report to fail
encoding.

**Verification: Test.**

#### Scenario: A non-UTF-8 file is marked uncapturable
- GIVEN a declared file containing bytes that are not valid UTF-8
- WHEN the session report is built
- THEN that file is marked uncapturable and the report is still built and sent

### Requirement: Metadata is a write-once fact, no backfill

Each captured key/value pair MUST be written once, at ingestion, and never
updated thereafter. Only keys named in the declaration MUST be stored; a key
declared after a run was recorded MUST NOT be backfilled onto that run.

**Verification: Test.**

#### Scenario: A captured value is never updated after ingestion
- GIVEN a run whose metadata was recorded at ingestion
- WHEN the same run is later queried
- THEN its metadata values are unchanged from what was written at ingestion

#### Scenario: A key declared later has no value on earlier runs
- GIVEN a run recorded before a key was added to the declaration
- WHEN that key is later declared and a new run is recorded
- THEN the earlier run carries no value for that key

### Requirement: Session-start overhead is measured (RQ-25)

The session-start cost of reading, validating and bounding the declared
files MUST be measured against RQ-25's overhead budget, on the same harness
used for `version-control-context`, and the measured number MUST be
committed to this spec rather than assumed or asserted.

**Verification: Analysis** — a measurement against a budget, not a pass/fail
assertion.

#### Scenario: Overhead is measured and recorded as a number
- GIVEN a fixture and harness equivalent to `version-control-context`'s own measurement
- WHEN metadata capture's session-start cost is measured with the flag enabled
- THEN the measured overhead is committed to this spec as a number, alongside whether it fits the remaining RQ-25 budget
