# session-ingestion — capability advertisement

Written after delivery, 2026-08-20. This change shipped its decisions
(D38–D42) in `tasks.md` rather than in delta specs, so its obligations were
tested but absent from the corpus. Backfilled rather than left in an archived
change folder, because the behaviour is real and someone will need to find it.

## ADDED Requirements

### Requirement: Capability advertisement

The server MUST advertise, at a documented endpoint, whether it can complete
a session lifecycle, and a client MUST NOT begin one against a server that
has not said so.

A newer plugin against an older server previously lost the end of every run
in silence: the start-write created the row, the older server's
`ON CONFLICT DO NOTHING` discarded the finish, and the acknowledgement said
`200 duplicate`. Result rows still inserted, so the damage did not even look
empty.

**One capability, not a version number** (D38). There is one thing a client
needs to know, and a version scheme invented before a second capability
exists would be guessing. A later capability adds a key, which an older
client ignores.

**The check fails closed** (D40). Only an explicit positive answer enables
the lifecycle. Anything else — a missing route, a malformed body, a value of
the wrong type, an explicit `false`, an empty body, a server error, a
connection that hangs — degrades. A capability check that fails open is worse
than none, because it promises a guarantee it does not hold.

**Degrading means the previous release** (D41), not a third state: no
start-write, no heartbeats, and a finish report byte-identical to the one
that shipped before the lifecycle existed.

#### Scenario: The server advertises the session lifecycle
- GIVEN a running server
- WHEN `GET /api/v1/capabilities` is requested
- THEN it answers `200` with `{"session_lifecycle": true}`

#### Scenario: The advertisement is versioned like every other route
- GIVEN a running server
- WHEN `GET /capabilities` is requested without the version prefix
- THEN it answers `404`, matching the absence rule the run routes already follow

#### Scenario: An older server's missing route is an answer, not a failure
- GIVEN a server that predates this capability and has no such route
- WHEN a client probes it
- THEN the probe reports the lifecycle unavailable rather than raising or warning about a transport error

**Verification: Test.** This is the row that matters most, because every
server needing detection is already published — nothing can change on that
side, which is the only reason the design works at all.

#### Scenario: Every non-positive answer degrades
- GIVEN a server answering with malformed JSON, valid JSON of the wrong type, an explicit `false`, an empty body, a `500`, or a connection that hangs past the liveness timeout
- WHEN a client probes it
- THEN every one of those answers reports the lifecycle unavailable

**Verification: Test**, table-driven — one row per way a check could quietly
fail open. Proven by mutation: a probe that returns true unconditionally turns
the whole table red.

#### Scenario: A degraded session records exactly as the previous release did
- GIVEN a server that does not advertise the lifecycle
- WHEN a session is recorded against it
- THEN no start-write and no heartbeat are sent, one warning is emitted on the liveness path, and the finish report is byte-identical to the pre-lifecycle shape
- AND result recording is unaffected

#### Scenario: The probe is bounded by the liveness timeout
- GIVEN a configured report timeout longer than the liveness timeout
- WHEN the capability probe runs
- THEN it is bounded by the liveness timeout, never the report timeout

**Verification: Test.** A probe that could block for the report timeout would
put that cost in front of every session.
