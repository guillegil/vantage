# 15. Scope the read-only guarantee to a named read surface

Date: 2026-08-21

## Status

Accepted

## Context

The obligation as written says that calling **every documented endpoint** leaves
the stored data unchanged, and offers a byte-identity check as its criterion.

That statement has been false since Milestone 1. `POST /api/v1/runs` is a
documented endpoint and its entire purpose is to write a row. So the guarantee's
first criterion cannot pass today, and — more importantly — there is no future
in which it could, because the product's whole value depends on something
writing. This was recorded as an open question (OQ-9) to be resolved by Phase 3,
when a launch surface would introduce endpoints that act rather than report. The
framing was wrong about the timing: the contradiction did not arrive with
launching. It arrived with ingestion, three milestones early, and it has been
sitting in the requirement corpus as an obligation nobody could satisfy.

Two exits were available and neither was taken at the time.

The first is to scope the guarantee to **Phase 1**, leaving the open question
open and re-deciding it when a write surface beyond ingestion exists. That
keeps the sentence true for now and defers the argument.

The second is to scope it to **the endpoints that serve recorded history** —
naming a read surface, and putting everything else outside it by construction
rather than by exception.

The `read-api` change forces the choice, because it is the first change that can
actually prove either version. Until now there was nothing to prove: no endpoint
read anything back, so a read-only guarantee had no subject.

There is a third fact that shapes the decision. `history-read-api` and
`api-interface-document` together introduce a hand-written, machine-readable
interface document that enumerates every endpoint. For the first time, "the set
of endpoints that read" is something a machine can be handed rather than a
phrase a person interprets.

## Decision

**The read-only guarantee is scoped to a named read surface, and the
machine-readable interface document is what names it.**

An operation the document tags `read` MUST leave stored data unchanged: no row,
no column, no row count altered by calling it. An operation the document tags
`write` is outside the guarantee entirely — not an exception to it, not a
tolerated violation, simply not a member of the set the guarantee quantifies
over. `session-ingestion` records that exclusion where the writes live, so the
boundary is stated on both sides.

Three properties follow from tying the surface to the document rather than to a
list maintained inside a test:

1. **The surface is enumerable.** The proof harness derives its call list from
   the document, so a read endpoint cannot be added without being proven, and it
   cannot be proven without being documented.
2. **The classification is declared, not derived.** The tag is authored. An HTTP
   method is not a safety classification, and deriving one from the other would
   make the boundary implicit, unreviewable, and silently wrong the first time
   something reads via a verb that is not `GET`.
3. **The guarantee is falsifiable.** Tagging a writing endpoint `read` must make
   the proof fail. A guarantee whose check cannot fail is an assumption wearing a
   test's clothes.

The proof itself is a **digest pair with unchanged counts**, not a naive
before/after file hash. The store opens WAL, so a read connection can checkpoint
into the main database file when it closes and `-wal`/`-shm` files appear and
change beside it — instability that has nothing to do with anything writing a
row. The pair is a logical content digest over every table plus a main-file
digest taken with the connection's lifecycle state pinned, together with
unchanged execution and result counts. The mechanics belong to the design; what
belongs here is that the guarantee is proven by content, not by bytes on disk,
because bytes on disk are not stable for reasons unrelated to the claim.

**This binds future surfaces.** A Phase 3 launch surface, or any later endpoint
that acts rather than reports, is not admitted to the read surface by being
convenient. It is tagged `write`, it is excluded, and it must justify that
exclusion where the exclusion is recorded. This decision does not authorise any
particular write endpoint; it fixes what a read endpoint may do.

## Consequences

- The obligation becomes provable for the first time, and the proof is a test
  rather than an argument. Before this decision, the only honest status was
  "contradicted as written".
- The interface document becomes load-bearing in two independent checks: the
  drift check that compares it against the mounted routes, and the read-only
  proof that derives its call list from it. A document that drifts breaks both.
  That is intended — a document nothing depends on is a document that rots.
- Adding a read endpoint costs a document entry and a fixture binding. That is a
  real, small tax on every future read endpoint, paid deliberately so the
  guarantee cannot quietly stop covering the surface it names.
- The safety posture is now a product statement rather than an implementation
  detail: a client can call anything on the read surface, in any order, as many
  times as it likes, and change nothing. That is a promise worth being able to
  make, and it is the reason the reversal cost is high — withdrawing it re-opens
  what every endpoint is allowed to do.
- OQ-9 closes. It does not close by deciding that a launch surface will never
  exist; it closes by deciding that a launch surface will not be inside the read
  surface.
- Reversal, if it ever comes, is by supersession rather than by edit, and OQ-9
  reopens with its status restored.

## Alternatives rejected

**Scope the guarantee to Phase 1 and leave the question open.** The honest
minimal move, and tempting: it changes nothing about what must be built now and
defers the argument to a change that has more information. Rejected because the
information is not actually missing — what a read endpoint may do is decidable
today, and it is decidable independently of what a launch surface will look
like. Deferring it means the read API ships with a guarantee whose scope nobody
has agreed on, and the first launch endpoint then inherits an argument instead of
a boundary. Worse, "Phase 1" is not a set an enumerable proof can be built from,
so the guarantee would stay unprovable for exactly as long as it stayed open.

**Keep the guarantee universal and grant ingestion a named exception.** Preserves
the strong-sounding sentence and adds a footnote. Rejected because an exception
list is a maintenance surface that grows: the second exception is easier to add
than the first, and the guarantee erodes one justified footnote at a time until
it means nothing. Scoping by construction has no such gradient — an endpoint is
either in the named set or it is not, and adding it to the set means submitting
it to the proof.

**Derive the read surface from the HTTP method.** Costs nothing to implement and
is right almost always. Rejected because "almost always" is the wrong standard
for a safety boundary, and because it leaves `session-ingestion`'s exclusion with
nothing to inspect: the scope would be an emergent property of the route table
rather than a statement anyone wrote down or reviewed. A declared tag is
reviewable in a diff; a derived one is only discoverable by reading the router.

**Prove the guarantee with a before/after hash of the database file.** The
obvious check, and the one the original criterion implies. Rejected on
correctness, not on cost: with WAL enabled the main file, `-wal` and `-shm`
change for reasons unrelated to any row changing, so the check fails
intermittently while nothing is wrong. A flaky safety test is worse than no
safety test, because the first three false alarms teach everyone to re-run it and
the fourth one is real.

Bound to: ADR-0009 (record over HTTP and let the server own every write),
ADR-0011 (serve the API with FastAPI on uvicorn), OQ-9, and the
`history-read-api`, `api-interface-document` and `session-ingestion`
capabilities.
