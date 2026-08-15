# Recording Fault Tolerance Specification

## Purpose

Defines the two failure paths the recorder must survive without disrupting
the host pytest session: an internal error while recording, and a database
path that cannot be opened for writing at all.

## Requirements

### Requirement: Non-disruptive internal failure

If the system raises an internal error while recording, then the system
MUST emit a warning and MUST let the pytest session terminate with the exit
status it would have had otherwise.

#### Scenario: Passing suite survives a write failure

- GIVEN a recording path patched to raise on write
- WHEN a suite of passing tests is run
- THEN pytest exits with status 0 and emits one warning

#### Scenario: Failing suite still reports failure

- GIVEN a recording path patched to raise on write
- WHEN a suite containing one failing test is run
- THEN pytest exits with status 1 and emits one warning

#### Scenario: Every recorder hook is fault-isolated

- GIVEN a recording path patched to raise on every hook it implements
- WHEN a suite of passing tests is run
- THEN pytest exits with status 0 and the session reports no internal error

### Requirement: Unwritable database path

If the configured database path cannot be opened for writing, then the
system MUST emit a warning naming that path and MUST let the pytest session
run to completion unrecorded.

#### Scenario: Read-only directory

- GIVEN a database path inside a read-only directory
- WHEN a suite of passing tests is run with that path configured
- THEN pytest exits with status 0 and emits one warning naming the path

#### Scenario: Missing directory

- GIVEN a database path inside a directory that does not exist
- WHEN a suite of passing tests is run with that path configured
- THEN pytest exits with status 0 and emits one warning naming the path

#### Scenario: File exists but is not a valid database

- GIVEN a database path whose file exists but is not a valid database
- WHEN a suite of passing tests is run with that path configured
- THEN pytest exits with status 0 and emits one warning naming the path
