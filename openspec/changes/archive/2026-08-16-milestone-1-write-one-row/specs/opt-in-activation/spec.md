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
