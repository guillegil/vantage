# Runtime Support Specification

## Purpose

Defines the supported Python and pytest-xdist runtime matrix, the guard that
refuses installation below the declared floor, and the guarantee that every
operation completes without network access.

## Requirements

### Requirement: Supported runtimes

The system MUST pass its own test suite on Python 3.10, 3.11, 3.12 and 3.13,
both with and without pytest-xdist installed.

#### Scenario: CI matrix is green

- GIVEN the continuous integration matrix
- WHEN it runs
- THEN all eight combinations pass

#### Scenario: Below-floor install is refused, not broken at import

- GIVEN a Python 3.9 environment
- WHEN the plugin package is installed
- THEN installation is refused by the declared version floor rather than failing at import time

### Requirement: Offline operation

The system MUST complete every operation using only resources on the local
machine.

(This milestone builds only recording; "the interface" named in criteria
below is not implemented until Milestones 4–6. The recording half of each
criterion is exercised now; the interface half is exercised once it exists.)

#### Scenario: Recording succeeds with networking disabled

- GIVEN a machine with networking disabled
- WHEN a suite is recorded and the interface is opened
- THEN both succeed

#### Scenario: No outbound connection is attempted

- GIVEN the system running with outbound connections logged
- WHEN a suite is recorded and the interface is opened
- THEN no outbound connection is attempted
