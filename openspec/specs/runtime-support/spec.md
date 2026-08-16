# Runtime Support Specification

## Purpose

Defines the supported Python and pytest-xdist runtime matrix, the guard that
refuses installation below the declared floor, and the guarantee that every
operation completes without network access beyond the configured local
server.

**Component:** both — the CI matrix (RQ-27) and offline operation (RQ-28)
span the plugin and the server together.

## Requirements

### Requirement: Supported runtimes (RQ-27)

The system MUST pass its own test suite on Python 3.10, 3.11, 3.12 and 3.13,
both with and without pytest-xdist installed.

#### Scenario: CI matrix is green
- GIVEN the continuous integration matrix
- WHEN it runs
- THEN all eight combinations of the four Python versions and the two xdist configurations pass

#### Scenario: Below-floor install is refused, not broken at import
- GIVEN a Python 3.9 environment
- WHEN the plugin package is installed
- THEN installation is refused by the declared version floor rather than failing at import time

### Requirement: Offline operation (RQ-28)

The system MUST complete every operation using only resources on the local
machine.

*Criterion 1's "the interface is opened" is carried verbatim from the
requirement. No interface exists until Milestone 5, so this milestone
demonstrates only the recording half of each criterion — session recording
over localhost HTTP — never the interface half.*

#### Scenario: Recording succeeds with networking disabled
- GIVEN a machine with networking disabled
- WHEN a suite is recorded (the interface-opening half is not exercised until Milestone 5)
- THEN recording succeeds

#### Scenario: No outbound connection beyond the local server
- GIVEN the system running with outbound connections logged
- WHEN a suite is recorded
- THEN no connection to any address other than the configured local server is attempted
