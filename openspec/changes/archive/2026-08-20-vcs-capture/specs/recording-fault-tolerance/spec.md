# Delta for Recording Fault Tolerance

VCS capture (RQ-10/RQ-23/RQ-39) introduces a third internal isolation path.
Reusing `fault_isolated` or `liveness_isolated` would be wrong: both latch —
one failure disables every later call sharing the flag — which would turn
"record the run with nulls" (RQ-23, RQ-39) into "record nothing", the exact
wrong implementation RQ-23 criterion 2 exists to catch. This delta adds that
third path as new, standalone behavior; it does not change RQ-21's own text.

## ADDED Requirements

### Requirement: VCS capture isolation

Git repository capture MUST use a fail-closed boundary distinct from, and
**non-latching** unlike, `fault_isolated` and `liveness_isolated`. A failure
anywhere in VCS capture — including a `git` process that hangs — MUST NOT
disable result accumulation, heartbeats, or any other hook for that session,
and MUST NOT itself change the exit status pytest would otherwise have had.
The git subprocess MUST be bounded at 5 seconds (decided over the preflight's
2.0 s bound: a cold cache on a large repository is a slow but healthy read,
and a shorter bound would turn it into null fields with nothing actually
broken).

**Verification: Test.** The non-latching proof requires demonstrating other
reporting paths survive a VCS failure in the same session — a mocked-raise is
acceptable here because the object under test is the isolation boundary
itself, not git's behavior.

#### Scenario: A git failure disables nothing else in the same session
- GIVEN VCS capture patched to raise
- WHEN a suite of passing tests is run
- THEN pytest exits with status 0, and every test result and heartbeat for that session is still recorded

#### Scenario: A hung git is bounded at five seconds
- GIVEN a `git` invocation that hangs indefinitely
- WHEN a session is recorded
- THEN the git subprocess is terminated at 5 seconds, the run is stored with all six vcs fields null, and the session is not otherwise delayed or disrupted
