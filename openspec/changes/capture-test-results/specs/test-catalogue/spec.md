# Test Catalogue Specification

## Purpose

Defines the catalogue of tests the system has ever observed: an entry survives
the test's deletion from the codebase, carrying the timestamp at which it was
last observed, so that "when did this stop being run" remains answerable.

**Component:** `vantage` (server) — the catalogue is written on the ingestion
path; the plugin reports observations and holds no catalogue of its own
(ADR-9).

## ADDED Requirements

### Requirement: Catalogue retention (RQ-13)

The system SHOULD retain a test's catalogue entry after that test is removed
from the codebase, together with the timestamp at which it was last observed.
A recorded session MUST update the last-observed timestamp only of the tests
that session actually observed.

**Catalogue identity is the pytest node id, verbatim.** Per the accepted
decision, the catalogue identifier is the full node id, unhashed and
untransformed. A rename or a move therefore produces a different identifier and
a separate entry, splitting that test's history visibly rather than disguising
it; reconciling a renamed test with its former history is Phase 3 and is out of
scope here. "The same identifier" in criterion 2 below means the same node id.

#### Scenario: A deleted test keeps its entry, frozen (RQ-13.1)
- GIVEN a recorded test
- WHEN it is deleted from the codebase and the suite is run again
- THEN its catalogue entry remains
- AND its last-observed timestamp is unchanged from the earlier run

#### Scenario: The same identifier returning reuses the same entry (RQ-13.2)
- GIVEN a catalogue entry for a test deleted three runs ago
- WHEN a test with the same node id is added back and the suite is run
- THEN the same catalogue entry is reused rather than a second one created
- AND its last-observed timestamp advances to the new run
