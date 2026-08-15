# Opt-In Activation Specification

## Purpose

Defines the guarantee that recording is inert unless explicitly requested on
the command line: absent the flag, the plugin's presence must be
undetectable from the project tree.

## Requirements

### Requirement: Recording is opt-in

Where no recording option is present in the pytest invocation, the system
MUST leave the project tree byte-for-byte identical to the same invocation
with the plugin disabled.

#### Scenario: Identical trees with and without the plugin active

- GIVEN a project with the plugin installed and no recording option
- WHEN pytest is run once normally and once with `-p no:vantage`
- THEN the two resulting project trees are byte-for-byte identical

#### Scenario: No database file appears unbidden

- GIVEN a project with the plugin installed and no recording option
- WHEN pytest is run
- THEN no SQLite database file exists anywhere under the project root
