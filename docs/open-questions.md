# Open questions

The live register. Migrated out of Notion on 2026-08-18 and answered the same
day; the frozen copy under `docs/legacy/` is gone.

Nothing here is decided until its status says so. When something is decided it
gets applied — to a requirement in `openspec/`, or to an ADR in `docs/adr/` if
it costs more than a sprint to reverse.

| # | Question | Status |
| --- | --- | --- |
| OQ-2 | Is the server optional, or the owner? | **Answered** 2026-08-16 — ADR-9, ADR-10 |
| OQ-3 | Is Vantage an observability tool or an orchestrator? | **Answered** 2026-08-18 |
| OQ-4 | Is the storage port a permanent surface or a migration seam? | **Answered** 2026-08-18 |
| OQ-5 | Where do artefact blobs live, and how are they addressed? | **Answered** 2026-08-18 |
| OQ-6 | If a remote arrives, how do local and remote stay in containment? | **Answered** 2026-08-18 |
| OQ-7 | What is the Windows equivalent of an owner-only store? | **Answered** 2026-08-18 |
| OQ-8 | What can the launch surface actually launch? | **Answered** 2026-08-18 |
| OQ-9 | Can RQ-14 stay read-only once launching exists? | **Open** — resolve by Phase 3 |
| OQ-10 | Is the interface document generated or hand-written? | **Answered** 2026-08-18 |

---

## OQ-3 · Observability, with orchestration left possible

Vantage is an **observability tool**. Recording and showing history is the
identity, and every requirement written so far assumes it: RQ-24 zero
dependencies, RQ-28 offline, RQ-2 opt-in, RQ-40 owner-only store.

Orchestration is **not ruled out**, but it does not get to redefine the product
on arrival. If it comes, it comes as an addition under the constraints in OQ-8.

## OQ-4 · The storage port is a migration seam, not a product surface

The port exists to keep the core isolated and to allow the in-memory adapter
that makes the core suite fast (RQ-30). SQLite is the real adapter and
foreseeably the only one.

**Consequence:** FTS5 may enter behind a search interface of its own without
pretending to be portable. RQ-30 promises the core can be tested and swapped —
it does not promise every storage feature is expressible in the abstract.

## OQ-5 · Artefact blobs go to the filesystem, addressed by content

Blobs live in the artefact store on disk, named by the hash of their content.
The database holds the hash only.

**Why:** deduplication is free — a thousand runs sharing one log is one file —
and the database does not inflate, which is the same failure RQ-16 already
avoids for tracebacks. The store directory is already mode 0700 under RQ-40.

**Owed:** a pruning story, and backup now covers two things instead of one.

## OQ-6 · A remote is a separate, explicit mode

Local stays the default and stays offline. **RQ-28 holds literally as long as
nobody configures a remote.** Sending data off the machine is a deliberate act
with its own option, exactly as RQ-2 made recording itself deliberate.

The privacy promise becomes "by default nothing leaves", which is what it
already means in practice.

## OQ-7 · Windows relies on the per-user data directory

ADR-10 already places the database under the user data directory, and on
Windows `%LOCALAPPDATA%` inherits an ACL granting only that user.

**RQ-40 is satisfied by location rather than by code.** Verified by Inspection
and documented; no Windows-specific branch is written, because CI has no
Windows runner and security code without coverage is worse than none.

## OQ-8 · The launch surface can launch a saved selection, by identifier

If a launch surface is ever built, it accepts **a reference to a test selection
already stored in the database** — an identifier, never arguments, never flags,
never a command string.

This is RQ-40's note in its strictest form. The difference between "run the
test suite" and "run this command" is the difference between a tool and a
remote shell with a web interface, and no authentication layered on top
recovers it. **The constraint binds before the surface exists**, which is the
whole point of deciding it now.

## OQ-9 · Open — RQ-14 stays as written

RQ-14 says every endpoint leaves stored data unchanged. A launch surface
creates data. **Both cannot hold, and RQ-14 is deliberately left untouched for
now.**

The contradiction is real and recorded rather than resolved. It becomes
blocking the day a launch surface is designed, not before. Revisit at Phase 3.

## Note · Catalogue `last_seen_at` monotonicity is now UTC-normalized at the boundary

Found reviewing Phase 3 of `capture-test-results` (Engram observation 62): D20's
`MAX(...)` guard on `test_case.last_seen_at` compares stored TEXT lexicographically,
which is only correct when every writer normalizes to the same UTC offset. Fixed in
Phase 5: `vantage.service.routes.runs` normalizes every timestamp — `run` and each
`result`, aware or naive — to UTC before it reaches the store. The guard is now sound
for any client speaking the HTTP boundary (ADR-9), not only this project's own
`pytest-vantage` plugin, which already normalized on its own side.

## OQ-10 · The interface document is hand-written, with an automatic drift test

The OpenAPI document is authored by hand and is the contract; the code conforms
to it. A test loads that document, enumerates the routes the service actually
serves, and compares them.

**Why not generated:** a generated document is the code in another format, so
RQ-36's criterion 3 — *an endpoint present in the service and absent from the
document is reported* — could never fail. A green check that cannot fail is the
failure mode RQ-26 already guards against with its second, anti-vacuous test.
