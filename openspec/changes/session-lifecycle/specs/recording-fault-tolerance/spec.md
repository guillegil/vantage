# Delta for Recording Fault Tolerance

A heartbeat introduces a new internal reporting path. RQ-21's existing
"internal error while recording" language already covers it, but its shared
fault-isolation latch — one failure disables every further hook, including
ordinary result accumulation — is a bigger blast radius than a heartbeat
failure should have. This delta narrows that failure mode for the heartbeat
path specifically, confirmed by the maintainer's decision in the 2026-08-19
question round (answer 4).

## MODIFIED Requirements

### Requirement: Non-disruptive failure (RQ-21)

If the system raises an internal error while recording, then the plugin
MUST emit a warning and MUST let the pytest session terminate with the exit
status it would have had otherwise. A server that accepts the connection and
never responds MUST NOT hang the session past a bounded timeout.

A failed heartbeat MUST NOT use the same fault-isolation latch as the
reporting path: it MUST emit exactly one warning per session and MUST NOT
disable further result accumulation or hook execution for that session.
(Previously: silent on the heartbeat path, which did not exist; a failure
anywhere in `Recorder`'s hooks was assumed to share one latch.)

#### Scenario: Passing suite survives an internal error
- GIVEN a reporting path patched to raise
- WHEN a suite of passing tests is run
- THEN pytest exits with status 0 and emits one warning

#### Scenario: Failing suite still reports failure
- GIVEN a reporting path patched to raise
- WHEN a suite containing one failing test is run
- THEN pytest exits with status 1 and emits one warning

#### Scenario: Server accepts then closes without responding
- GIVEN a server that accepts the connection and then closes it without responding
- WHEN a suite of passing tests is run
- THEN pytest exits with status 0 and emits one warning

#### Scenario: Server accepts and never answers
- GIVEN a server that accepts the connection and never responds
- WHEN a suite of passing tests is run
- THEN pytest exits with status 0 within the configured timeout plus five seconds

#### Scenario: Every hook is fault-isolated
- GIVEN a reporting path patched to raise on every hook it implements
- WHEN a suite of passing tests is run
- THEN pytest exits with status 0 and the session reports no internal error

#### Scenario: A failed heartbeat does not stop result recording
- GIVEN a heartbeat send patched to fail partway through a session
- WHEN a suite of passing tests is run
- THEN pytest exits with status 0 and emits exactly one warning
- AND every test result for that session is still recorded, rather than accumulation stopping after the failed heartbeat

#### Scenario: A failed heartbeat warns once, not once per beat
- GIVEN a heartbeat send patched to fail on every attempt across a session with multiple heartbeat intervals
- WHEN the session runs to completion
- THEN exactly one warning is emitted for the heartbeat failure, not one per failed attempt
