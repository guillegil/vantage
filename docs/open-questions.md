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
| OQ-9 | Can the read-only guarantee stay read-only once launching exists? | **Answered** 2026-08-21 — ADR-15 |
| OQ-10 | Is the interface document generated or hand-written? | **Answered** 2026-08-18 |
| OQ-11 | Unredacted failure-text storage: is a redactor ever needed? | **Open** — failure-count cap rejected 2026-08-25 |

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

## OQ-9 · Answered — the guarantee is scoped to a named read surface

The read-only guarantee ("every endpoint leaves stored data unchanged") and a
future launch surface that creates data cannot both hold universally.
**ADR-15 resolves this by scoping the guarantee to whatever the
machine-readable interface document tags `read`** — not to "every endpoint"
and not to "Phase 1" — so a launch surface is excluded by being tagged
`write`, not by an exception nobody reviewed. See ADR-15 for the full
reasoning and the alternatives it rejected.

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

## OQ-11 · Unredacted failure-text storage: is a redactor ever needed?

ADR-0016 decides the storage question — Vantage stores pytest's rendered
failure evidence and captured output verbatim and unredacted, disclosed
rather than claimed safe, refusable by omission: capture is opt-in and
absent unless a session's invocation asks for it via
`--vantage-failure-text`. It leaves one thing open rather than deciding it,
named in its own Consequences and Alternatives-rejected sections:

**A redactor.** Content-scanning free-form text for secrets is an unbounded
problem and a redactor that misses once is more dangerous than none, so
ADR-0016 defers it rather than refusing it forever. Nothing changes this
until one is designed and its false-negative rate is itself measured and
disclosed — a redactor nobody has evaluated is not a safer default than the
disclosed absence of one.

**A failure-count cap — considered, and rejected 2026-08-25.**
`failure-evidence`'s own Measurements paragraph (RQ-25) found that **every
measured failure density breaches RQ-25's 2% overhead budget, not only a
pathological all-failing session** — ten failing tests out of a thousand
already cost 3.45%–3.71% of a recording-off baseline, because
`version-control-context`'s own git-read overhead already spends most of the
budget before a single failure is rendered. Design.md D79 named this
possibility explicitly and declined to invent one without a number behind
it; that number now exists, and the arithmetic it produces rejects the cap
rather than sizing one: RQ-25 leaves roughly 55 ms of headroom per session,
and a single rendered failure costs 32–48 ms depending on density, so the
budget admits **roughly four failures per session** before it is spent — not
a failure-capture feature, a feature that fails on the fifth test in an
otherwise-healthy suite. The response taken instead was `design.md` D72's
polarity flip: capture is now opt-in and absent by default, so the common
case pays nothing at all rather than a small, arbitrary amount, and a
session that does opt in accepts the measured cost knowingly. Whether a
narrower mechanism is worth building for a session that has already opted in
— a lower per-report budget, a cheaper rendering path, or something else —
remains genuinely open, distinct from the cap question this paragraph
closes.

**Not open:** whether to store unredacted text at all (ADR-0016 decided
that), whether an opt-in surface must exist and stay invocation-flag-only
(it does, under RQ-2's own invariant), or whether a failure-count cap should
replace the opt-in default (considered and rejected above, with the
arithmetic behind it).
