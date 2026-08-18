# Open questions — full Notion export

> **FROZEN. NOT A WORKING DOCUMENT. SCHEDULED FOR DELETION.**
> See `README.md` in this directory first. Snapshot taken 2026-08-18.

The Notion Open Questions database, filtered to Vantage. Numbering starts at
`OQ-2` — there was no `OQ-1` row. `OQ-11` in the same database belongs to a
different project and is not included.

**These are the ones still worth an answer.** One is settled; the rest are not.

## OQ-2 · Is the server optional, or the owner? — `Answered` 2026-08-16

> The server owns the write path: the plugin reports a finished session over
> HTTP and never opens a database, so it never needs a driver.

Settled by **ADR-9** and **ADR-10**. Touches RQ-24 and RQ-44, features FT-1 and FT-8.

## OQ-3 · Is Vantage an observability tool or an orchestrator? — `Open`

**Resolve by** Phase 3 — when launching runs makes the line real.

The heaviest question open at export. Phase 3 launches runs and Phase 4
schedules them, and somewhere on that line this stops being a pytest plugin
with a viewer and becomes a test orchestration server that uses a plugin as its
agent. That may be the more valuable product. It is also a different one, with
different competitors, and everything written so far is written for the first.
Worth deciding deliberately rather than arriving at it.

## OQ-4 · Is the storage port a permanent product surface, or a migration seam crossed once? — `Open`

**Resolve by** Phase 2 — FTS5 enters the port with Phase 2 search, and that is the canary.

Touches feature FT-8 and ADR-2.

## OQ-5 · Where do artefact blobs live, and how are they addressed? — `Open`

No `Resolve by` set. Touches RQ-24.

## OQ-6 · If a remote arrives, how do local and remote stay in containment? — `Open`

**Resolve by** Phase 3 — the first time a remote exists. Touches RQ-25.

## OQ-7 · What is the Windows equivalent of an owner-only store? — `Open`

No `Resolve by` set. Touches RQ-40 — whose criteria 1 and 2 are skipped on
Windows because the POSIX mode has no meaning there.

## OQ-8 · What can the launch surface actually launch? — `Open`

**Resolve by** Phase 3 — before the launch API accepts anything.

RQ-40's notes carry the constraint this question has to respect: **what can be
launched must be a bounded, named operation rather than an arbitrary command
string.** The difference between "run the test suite" and "run this command" is
the difference between a tool and a remote shell with a web interface, and no
amount of authentication layered on top recovers it.

## OQ-9 · Can RQ-14 stay read-only once launching exists? — `Open`

**Resolve by** Phase 3 — the roadmap phase that creates the contradiction.

A contradiction rather than a choice. RQ-14 says every endpoint leaves stored
data unchanged; the Phase 3 launch feature creates data. Both cannot hold. Two
ways out, neither chosen: scope RQ-14 explicitly to Phase 1, or narrow it to
"the endpoints that serve recorded history". Touches RQ-14 and FT-4.

## OQ-10 · Is the interface document generated from the code or hand-written? — `Open`

No `Resolve by` set. Generated stays true automatically; hand-written can be
reviewed as a contract before the code exists, which fits the way these
requirements were written. Touches RQ-14 and RQ-36, feature FT-4.

---

## Obligations waiting for a subject

From the retired "Open design questions" page. **Not open questions** — nothing
about them is undecided. They are requirements that cannot be written yet
because the system they constrain does not exist.

- **When the read API arrives:** bind to loopback by default. Binding wider
  should be an explicit act, not a comfortable default.
- **Authentication, authorisation and transport security** have no subject until
  Phase 4 brings a shared server. Writing them now would be inventing
  requirements for a system that does not exist.
- **Rate limiting and payload size limits** on the ingestion endpoint (from
  RQ-42's notes), for the same reason.
- **Redacting secrets in environment variables and in test output** (from
  RQ-35's notes) — RQ-35 covers the command line only.
