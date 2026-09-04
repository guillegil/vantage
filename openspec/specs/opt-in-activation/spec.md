# Opt-In Activation Specification

## Purpose

Defines the guarantee that recording is inert unless explicitly requested on
the pytest command line: absent the option, the plugin attempts no network
connection and the project tree is unaffected.

**Component:** `pytest-vantage` (plugin) — entirely inside `plugin.py`'s
activation check; no server behavior is exercised.

## Requirements

### Requirement: Recording is opt-in (RQ-2)

Where no recording option is present in the pytest invocation, the plugin
MUST attempt no connection to the server.

#### Scenario: No connection without the option
- GIVEN a project with the plugin installed and no recording option
- WHEN pytest is run with outbound connections logged
- THEN no connection is attempted

#### Scenario: Identical trees with and without the plugin active
- GIVEN a project with the plugin installed and no recording option
- WHEN pytest is run once normally and once with `-p no:vantage`
- THEN the two resulting project trees are byte-for-byte identical

#### Scenario: No server needed, no warning either
- GIVEN a project with the plugin installed, no recording option, and no server running at all
- WHEN pytest is run
- THEN it exits with the status it would have had without the plugin and emits no warning

### Requirement: Metadata capture flag inertness (RQ-2 extended)

The metadata capture flag MUST be its own invocation flag, gated identically
to the base activation flag: it MUST have no ini equivalent, and the shipped
`--help` MUST actively deny one. Absent the metadata flag, the declaration
file MUST be opened zero times, no connection MUST be attempted on account
of it, and the resulting project tree MUST be byte-identical to a run made
with `-p no:vantage`. The declaration MUST be read only after both the base
activation gate and the metadata flag's own gate have passed.

**Verification: Test**, differential — the same shape RQ-2's own opt-in test
uses.

#### Scenario: No read or connection without the metadata flag
- GIVEN a project with a `vantage-metadata.json` present and the metadata flag absent
- WHEN pytest is run once normally and once with `-p no:vantage`
- THEN the declaration file is never opened, no connection is attempted, and the two resulting project trees are byte-for-byte identical

#### Scenario: The declaration is read only after both gates pass
- GIVEN the base activation flag present but the metadata flag absent
- WHEN pytest is run
- THEN the declaration file is not opened

#### Scenario: The shipped `--help` denies an ini equivalent for the metadata flag
- GIVEN the shipped `--help` output
- WHEN it is inspected for the metadata flag's entry
- THEN it states there is no ini equivalent, and does not state that an ini equivalent is given
