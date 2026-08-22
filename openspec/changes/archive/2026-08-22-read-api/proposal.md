# Proposal: Read API

> **Refreshed 2026-08-21.** The original was written 2026-08-19 against a tree
> where nothing wrote the `vcs_*` columns. `vcs-capture` landed and was archived
> on 2026-08-20, so **Q1's blocking dependency is satisfied** and every claim
> that treated VCS data as unwritten has been corrected. Two new decisions were
> taken this session (history entries carry the full VCS context; scope stays at
> five slices), and the review budget moved from 500 to 400 lines. The
> 2026-08-19 question round and its answers are preserved unedited below; the
> corrections are recorded as a dated addendum, per this repository's
> supersede-rather-than-edit convention.
>
> **Identifier vocabulary translated 2026-08-21.** At the user's direction this
> proposal no longer carries numeric requirement identifiers. Every obligation
> is now stated by *what it demands* and anchored to the **capability** — and,
> where one exists, the **scenario** — that owns it, which is the vocabulary
> `CLAUDE.md` names for obligations that are not numbered. **No decision,
> answer, scope boundary, forecast number or verification method changed.**
> The rest of the repository still uses the numeric corpus; this file is
> deliberately ahead of it.

## Intent

Vantage records well and shows nothing. `vantage.service` serves two write
endpoints and `GET /api/v1/capabilities`; the tests prove data goes in, and the
only way to read it back is opening the SQLite file by hand. This change adds
the server-side read surface — run list, run detail, test history — and the
machine-readable interface document that makes "every endpoint" enumerable.

It is also the first caller of `derive_presentation`, which `session-lifecycle`
shipped deliberately as a seam with no reader, and the first reader of the
`vcs_*` columns `vcs-capture` filled.

## What the data actually supports today

`_UPSERT_RUN` now writes fourteen `run` columns: `id`, `received_at`,
`last_contact_at`, `started_at`, `finished_at`, `exit_status`, `interrupted`,
`interrupt_reason`, and the six `vcs_*` columns through `_vcs_columns`, merged
on conflict with per-column `COALESCE` so a partial snapshot never nulls a
fuller stored one. `_to_result` maps outcome, durations, phase outcomes and
`worker_id`. Failure, traceback, captured-output and environment columns are
still populated by nothing.

| Obligation | Owner | Status this change can reach | Blocker |
|---|---|---|---|
| Every documented endpoint leaves stored data unchanged | `history-read-api` → *Read-only read surface* (new) | **Contradicted as written** — see below | OQ-9 |
| A test's executions come back newest first, each carrying its commit and its duration; an unknown test yields an empty history, not an error; a run recorded outside a repository is returned with a null commit rather than omitted | `history-read-api` → *Test history* (new) | **Fully reachable**: order, duration, commit, empty history, null-commit tolerance | none — `vcs-capture` (archived 2026-08-20) supplies the commit |
| List responses carry only bounded-size fields, excluding traceback and captured output; the full record stays reachable on the single-item endpoint | `history-read-api` → *Lean list projections* (new) | **Partly non-vacuous**: `commit_subject` is real recorded data with real size; the *traceback* exclusion is still unfailable | no failure capture yet |
| No list response exceeds 200 items, and every list response says whether more exist | `history-read-api` → *Bounded pagination* (new) | **Fully met** | none |
| A run recorded outside a git repository appears in a run list alongside runs from repositories | `version-control-context` → *Absent repository* → *Absent repository's run is retrievable in storage, pending a run list* | **Promotable** from Inspection to Test — that spec defers the criterion to `read-api` **by name** | none |
| A test's history returns within 100 ms at p95 server-side over 500 runs / 100,000 results, with the slowest single response recorded too | `history-read-api` → *Test history latency* (new) | **Measurable** (Analysis, committed numbers) | needs a 500-run / 100k-result fixture generator |
| Each execution recorded from a dirty working tree is marked as such **in a history view** | the web interface (ADR-0008) — unowned, no spec | **Data path supplied, obligation still unmet** — it asks for a *view* | no web interface |
| A machine-readable document describes every endpoint, every path it declares answers 2xx, and an endpoint served but absent from it is reported | `api-interface-document` → *Machine-readable interface document* (new) | **Fully met** | `docs/api/v1-ingestion.md` is prose, not machine-readable |
| A run with a start time and no end time reads back as abandoned once the grace period lapses, and as interrupted when a Ctrl-C report arrived | `session-liveness` → *Abandoned run is observable* | **Promoted** from Analysis to Demonstration | port does not expose `last_contact_at` |
| The run-list view, the test-history view, and serving the interface entirely from installed assets | the web interface (ADR-0008) — unowned, no spec | **Out of scope** — Demonstration through an interface that does not exist | |

### The read-only obligation is already false, not future-tense

OQ-9 frames the read-only contradiction as arriving with a Phase 3 launch
surface. It arrived in Milestone 1. `POST /api/v1/runs` is a documented
endpoint and it writes, so the obligation's first criterion — *call every
documented endpoint, the file is byte-identical* — cannot pass today and could
never pass. The requirement notes offered two ways out and chose neither; this
change takes the second: **scope the read-only obligation to a named read
surface**, so ingestion and any later launch surface are out of scope by
construction rather than by exception. That resolves OQ-9 and earns
**ADR-0015** on the reversal-cost filter: reversing it re-decides the product's
safety posture and re-specifies every endpoint.

Proving it is not free. The store opens WAL, so a read connection can
checkpoint into the main file on close and `-wal`/`-shm` appear beside it.
Naive `hash(db_bytes)` before/after is unstable for reasons unrelated to
writing. The proof is a **pair**: main-file digest taken with the connection
state pinned, plus a logical content digest over every table, plus
`count_executions()`/`count_results()` unchanged. Verification method is
**Test**, over the document-declared read paths only.

### What a history entry carries, and what it must not

Decided 2026-08-21 (addendum decision A): a history entry exposes `commit`,
`branch`, `commit_subject`, `commit_subject_truncated` and `dirty` — not the
bare commit hash the test-history obligation strictly requires.

| Field | On the wire | Why |
|---|---|---|
| `commit`, `branch`, `dirty` | **Yes** | "this test failed on this branch, with a dirty tree" is the product; the columns are populated |
| `commit_subject` | **Yes, display-bounded in lists** | 64 KiB × a 200-item page is 12.8 MB — the lean-list rule forbids exactly that. The full stored subject stays reachable on run detail, which is that same rule's complement half: excluding a field from lists is only correct while it remains reachable somewhere. Design settles the width |
| `commit_subject_truncated` | **Yes** | A provenance flag, not a value column — `_row_to_vcs_context` excludes it from the all-null check for that reason. But `VcsContext`'s docstring is explicit that it *travels with* `commit_subject`, never independently: a client rendering a truncated subject as the commit's subject misrepresents git. One boolean, and dropping it makes the value it describes dishonest |
| `vcs_root` | **No** | An absolute filesystem path from the reporter's machine. It has no reader-facing purpose and leaks the developer's directory layout into a public API. Excluded by an assertion, not by discipline: a test asserts the recorded root value appears in no read response body |

**The port speaks `VcsContext`, not a second near-duplicate type.** The history
*entry* type is read-specific (it carries run id, timestamps, outcome, duration
and `last_contact_at`, which no domain aggregate holds), but its VCS member is
the existing `VcsContext`: the adapters already build it via
`_row_to_vcs_context`, and the all-null normalisation rule — a run recorded
outside a repository reads back as `None`, never a `VcsContext` full of nulls —
comes with it rather than being restated and drifting. The cost is that `root`
exclusion becomes the service layer's job; that cost is paid by the exclusion
test above. The rejected alternative — a read-specific VCS type without `root` —
buys leak-safety by construction and pays with two vocabularies for one concept
and a second copy of the null rule.

**Null tolerance keeps its original meaning.** A run recorded outside a git
repository has all six columns NULL and reads back as `vcs is None`. The
test-history obligation's tolerance for a null commit — *an execution recorded
from a directory that is not a git repository is returned with a null commit
rather than omitted* — still means exactly that, and not something wider. It is
what the 2026-08-19 Q1 answer insisted it must not become a cover for.

## Scope

### In scope

- `GET /api/v1/runs` (newest first by `started_at`, paginated), `GET /api/v1/runs/{id}`, `GET /api/v1/runs/{id}/results`, test history keyed by `node_id` (ADR-0012)
- Pagination: hard cap 200, `has_more`, and the truncated-versus-exhausted distinction — all three criteria of `history-read-api` → *Bounded pagination*
- Lean list projection with the full record on the single-item endpoint (`history-read-api` → *Lean list projections*), now including the subject-width rule above
- VCS context on run and history projections; `vcs_root` excluded and the exclusion tested
- Read methods on `ExecutionStore`, implemented in **both** adapters and added to `vantage_port_contract.py`
- Exposing `last_contact_at` through the port so `derive_presentation` gets its first caller
- A hand-written machine-readable OpenAPI document + drift test (OQ-10; `api-interface-document`); FastAPI's generated `/openapi.json`, `/docs`, `/redoc` disabled — a generated document is the code in another format, and the drift criterion (*an endpoint served but absent from the document is reported*) could never fail against it
- Latency benchmark script, p95 and max committed as numbers in the spec — the precedent is `run-recording`'s own **Measurements** paragraph, which commits body-size and peak-memory numbers into the spec text and obliges a future change to re-run them
- ADR-0015 resolving OQ-9

### Out of scope

- The web interface (ADR-0008) — the run-list view, the test-history view, the marking of dirty-tree executions *in that view*, and serving the interface entirely from installed assets all stay unmet; each is Demonstration through an interface that does not exist
- Capturing any *new* data: failure/traceback capture, environment capture. VCS capture is no longer here because it is **done** (archived 2026-08-20), not because it is deferred
- `pytest-vantage` — unchanged, not opened
- Search, filtering, aggregation, flake scoring; any write endpoint

## Capabilities

### New Capabilities
- `history-read-api`: the read endpoints and everything observable about them — ordering, pagination, lean projection, VCS context on entries, latency, and the scoped read-only guarantee. It will own *Read-only read surface* (scoped to the document-declared read paths), *Test history*, *Lean list projections*, *Bounded pagination*, and *Test history latency*.
- `api-interface-document`: the hand-written machine-readable document, the drift test, and the criterion that every path the document declares answers 2xx. It will own *Machine-readable interface document*.

### Modified Capabilities
- `session-liveness`: its Purpose states that the read-back criteria of *Abandoned run is observable* are Analysis "against the derivation helper here, not Demonstration through a live read path". This change supplies that path — those scenarios become Demonstration.
- `session-ingestion`: names the ingestion endpoints as outside the read-only surface, so the scoping is recorded where the writes live rather than only in the new capability.
- `version-control-context`: its *Absent repository* requirement records the run-list criterion as **Inspection, not claimed as met**, and stands it in with the scenario *Absent repository's run is retrievable in storage, pending a run list*, whose heading is marked *awaiting `read-api`* and whose deferral paragraph says "Promote to Test/Demonstration once `read-api` exposes a run list". This change is that run list: the criterion is promoted to Test and the deferral paragraph is retired.
  **Cross-reference, both directions.** A reader of `openspec/specs/version-control-context/spec.md` reaches this proposal by following the `read-api` name in that requirement's deferral paragraph and scenario heading. A reader of this proposal reaches that spec by the requirement name *Absent repository* and the scenario name *Absent repository's run is retrievable in storage, pending a run list*, both quoted verbatim here.

## Approach

Port first, routes second, document third, proof last. Read methods land on
`ExecutionStore` and both adapters together (the contract suite forces
agreement, which is the point). Routes are thin: SQL shapes the page, Pydantic
shapes the response, and `derive_presentation` runs in `vantage.core` on values
the row already carries. `last_contact_at` is not on `Execution` by design
(D1); the port grows a read type that carries it alongside — and now the VCS
context alongside that — rather than polluting the domain aggregate.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `vantage/core/ports/storage.py` | Modified | read/query methods, page/detail/history read types carrying `VcsContext` |
| `vantage/core/domain/liveness.py` | Unchanged | gains its first caller |
| `vantage/core/domain/execution.py` | Unchanged | `VcsContext` is reused, not re-modelled |
| `vantage/storage/{sqlite_store,memory}.py` | Modified | both adapters, in lockstep; history select reads the `vcs_*` columns |
| `vantage/service/routes/read.py` | New | the read router |
| `vantage/service/schemas.py` | Modified | response models (list vs detail projections), `root` excluded, subject bounded |
| `vantage/service/app.py` | Modified | mount router; disable generated OpenAPI/docs |
| `docs/api/v1.yaml` | New | the hand-written contract |
| `docs/adr/0015-*.md` | New | OQ-9 resolution |
| `docs/open-questions.md` | Modified | OQ-9 → Answered |
| `packages/vantage/tests/vantage_port_contract.py` | Modified | new contract scenarios, including a null-VCS run |
| `openspec/specs/version-control-context/spec.md` | Modified | *Absent repository*'s run-list criterion promoted out of its Inspection deferral |
| `pytest-vantage` | **Untouched** | |

## Delivery forecast (400-line review budget)

**The budget moved from 500 to 400.** The 2026-08-19 forecast was written
against 500 and closed with "No slice exceeds 500", which was true then and is
not the question now. At 400, slices 1 and 2 were already over and slice 5 sat
exactly on the line *before* decision A. With the VCS context added, **four of
five slices exceed the budget.**

| # | Slice | 2026-08-19 (vs 500) | Now (vs 400) | Over? |
|---|---|---|---|---|
| 1 | Port read surface + both adapters + contract scenarios | ~470 | **~530** | **Yes** |
| 2 | Run list + run detail routes, pagination envelope, lean projection | ~420 | **~460** | **Yes** |
| 3 | Test history route + `derive_presentation` + VCS entry fields | ~360 | **~430** | **Yes** |
| 4 | Hand-written OpenAPI document, drift test, generated doc disabled | ~340 | ~375 | No |
| 5 | Read-only proof harness + latency benchmark + measurements + ADR-0015 | ~400 | **~425** | **Yes** |

**Total ~2,220 lines across 5 slices, up from ~1,990.** The growth is decision
A: VCS fields on two read types, both adapters selecting six more columns,
response models, the `root` exclusion test, and null-VCS scenarios in the
contract suite.

**This is a forecast, not a slicing.** Re-slicing is `sdd-tasks`' job under
`delivery_strategy: auto-chain`; this proposal's obligation is to make the
number true and visible rather than quietly re-cut it or accept a
`size:exception` on its own authority. Preliminary signal for `sdd-tasks`:

```
Decision needed before apply: Yes
Chained PRs recommended: Yes
400-line budget risk: High
```

`chain_strategy: feature-branch-chain` (unchanged).

**Verification forecast** (the `session-lifecycle` lesson — six slices, not
four, because the test-layer plan was skipped):

| Obligation | Method | Why |
|---|---|---|
| *Test history* — all three criteria | **Test** | the commit hash is recorded now; the first criterion is assertable end to end |
| *Lean list projections* — the bounded-size rule | **Test** | `commit_subject` is recorded data with real size — a genuinely failable check |
| *Lean list projections* — the traceback exclusion | **Inspection** | with no traceback recorded, *that* exclusion still cannot fail; record it honestly |
| *Bounded pagination* | Test | assertable response properties |
| *Read-only read surface*, scoped | Test | digest pair over document-declared read paths |
| `version-control-context` → *Absent repository*, the run-list criterion | **Test** | an absent-repository run appearing in a real run list, which is what the deferral asked for |
| *Test history latency* | **Analysis** | a percentile over a distribution, not an assertion; timing assertions are flaky on the 3.10–3.13 × xdist matrix |
| *Machine-readable interface document* — the drift criterion | Test | drift test compares document to served routes |
| `session-liveness` → *Abandoned run is observable*, read-back | **Demonstration** | through the live read path |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Review workload**: four of five slices exceed the 400-line budget | **High** | `sdd-tasks` re-slices under `auto-chain`; do not accept `size:exception` by default and do not let a slice grow silently |
| WAL checkpointing makes the byte-identity proof flake | High | digest pair (file + logical) with pinned connection state; a flaky read-only test is worse than none |
| The traceback half of the lean-list rule ships as a green check that cannot fail | High | state it as Inspection; the bounded-size half is Test-verified via `commit_subject`, so the two halves are recorded separately |
| `vcs_root` leaks an absolute developer path into a public API | Medium | excluded from every response model and asserted absent from response bodies; the port carries it, the wire does not |
| `commit_subject` at 64 KiB × 200 blows the list response size | Medium | display-bounded in lists, full on detail; design fixes the width |
| Node ids contain `/`, `::`, `[`, `]` — path-segment encoding | Medium | decide query parameter vs encoded segment in design; wrong choice is a breaking API change later |
| The latency index conflicts with the 2% whole-session recording-overhead budget (flagged in the requirement notes) | Medium | measure both, together, not separately. `vcs-capture`'s measurements — recorded in `version-control-context`'s **Measurements** paragraph, which reports against that same 2% budget — consumed part of it already; re-measure against the current numbers, not the pre-VCS ones |
| Two adapters drift | Low | contract suite already forces agreement |
| Container/timezone/Python-version defects the last change's verify rounds missed | Medium | verification asks "does it survive another timezone, another interpreter, an older client", not only "does it satisfy the spec" |

## Rollback Plan

Every slice is additive; nothing existing changes behaviour except two lines.

1. **Per slice**: revert the branch. No schema change, no migration, no data
   written — the read API cannot corrupt what it never writes.
2. **The two reversible edits**: `create_app`'s `openapi_url=None,
   docs_url=None, redoc_url=None` restores FastAPI's generated document by
   deleting three kwargs; `include_router(read_router)` removes the surface
   entirely.
3. **ADR-0015**: if OQ-9's resolution is later rejected, supersede rather than
   edit (`CLAUDE.md`), and reopen OQ-9 with its status restored.
4. **The `version-control-context` promotion**: reverting restores the
   *Absent repository* Inspection deferral paragraph and its
   pending-a-run-list scenario verbatim; nothing else in that spec is touched.
5. **Committed measurements**: numbers in a spec are documentation; reverting
   is a text revert.

## Dependencies

- **`vcs-capture` — satisfied.** Archived 2026-08-20 at
  `openspec/changes/archive/2026-08-20-vcs-capture/`, merged capability at
  `openspec/specs/version-control-context/spec.md`. This was the one blocking
  prerequisite (Q1) and it is gone.
- None external. No new third-party distribution — FastAPI and Pydantic are already `vantage.service` dependencies (ADR-0011), and `vantage.core`/`vantage.storage` stay stdlib-only, which is what `architecture-boundaries` → *Zero runtime dependencies* and *Core isolation* require.
- **No blocked-on decisions remain for the spec phase.** Q1, Q2, Q3 and Q6 are
  answered; Q4 and Q5 are design's, and neither changes the shape of a spec.

## Success Criteria

- [ ] A client can list runs, open one, and read a test's history over HTTP, without opening the database file
- [ ] A history entry shows which commit, which branch, and whether the tree was dirty — not just a hash
- [ ] `vcs_root` appears in no read response, proven by assertion
- [ ] Every documented read endpoint leaves the stored data provably unchanged, by both digests
- [ ] The hand-written document is the contract, and an endpoint absent from it fails the suite
- [ ] `derive_presentation` has a caller, and `session-liveness`'s *Abandoned run is observable* scenarios are demonstrated rather than argued
- [ ] `version-control-context`'s absent-repository run-list criterion is demonstrated through a real run list, and both its deferral paragraph and its pending-a-run-list scenario are retired from that spec
- [ ] p95 and max latency exist as committed numbers, not a `print()`
- [ ] The traceback half of the lean-list rule is recorded as unmet-and-unfailable, not quietly claimed
- [ ] OQ-9 is answered in `docs/open-questions.md` and bound by ADR-0015

---

*The two sections below are a preserved record of decisions already taken. On
2026-08-21 their numeric requirement identifiers were translated to capability
and obligation names at the user's direction; no answer, reasoning or decision
was altered.*

## Proposal question round

These are decisions a human must make. None has a safe default.

**Q1 — The test history's commit hash: ship partial, or capture VCS first?**
Its first criterion requires a commit hash per history entry. Six `vcs_*`
columns exist and nothing writes them. Either (a) this change ships returning
`null` commits and the test-history obligation stays partly unmet until VCS
capture lands, or (b) a `vcs-capture` change goes first and read-api satisfies
it fully. (a) gets a usable read API sooner and matches the second criterion's
own tolerance for null commits; (b) avoids a spec that records a Must-Have as
half-delivered. **Which?**

**Q2 — Does OQ-9 close here, or only get restated?**
The proposal takes the "narrow the read-only obligation to the endpoints that
serve recorded history" exit and closes OQ-9 via ADR-0015. The alternative is
scoping the read-only obligation to Phase 1 and leaving OQ-9 open for a later
write surface. Closing it now binds a future launch surface to a boundary that
does not exist yet. **Close, or restate?**

**Q3 — Is the lean/full split in scope at all while no traceback is recorded?**
Its exclusion test cannot fail today. Options: (a) implement the lean/full
split now and record the lean-list obligation as Inspection-verified-vacuous;
(b) seed `result.traceback` directly at the storage layer for the test, which
exercises response shaping honestly but tests a column no recorder writes; (c)
defer the lean-list obligation to the failure-capture change. **Which?**

**Q4 — Test history identity on the wire.**
`node_id` contains `/`, `::`, `[`, `]`. Path segment (encoded), or query
parameter? `stable_id` supersedes `node_id` in Phase 2 (`schema.sql`), so
whichever is chosen becomes a breaking change then unless the endpoint is
identity-agnostic from the start. **Which shape?**

**Q5 — Interface document format and location.**
The interface-document obligation deliberately does not name a format
(requirement notes: "the format is deliberately not named... belongs in an
ADR"). Proposal assumes hand-written OpenAPI 3.1 YAML at `docs/api/v1.yaml`,
packaged into the wheel and served. Does the format choice want its own ADR, or
is it cheap enough to reverse that a design note suffices? And does
`docs/api/v1-ingestion.md` become prose commentary beside it, or get folded in?

## Answers to the question round — 2026-08-19

**Q1 — VCS capture goes first.** The test history is the endpoint whose own
rationale says it is what the product exists to serve, and its first criterion
requires a commit hash. Six `vcs_*` columns exist in `schema.sql` and nothing
writes any of them. Shipping the history endpoint with null commits would record
a Must Have as half-delivered and leave the flagship endpoint born incomplete. A
`vcs-capture` change lands first; this one then satisfies the test-history
obligation whole. The second criterion's tolerance for a null commit stays what
it was written for — a directory that is not a git repository — rather than
becoming a cover for not recording it at all.

**Q2 — OQ-9 closes here, by narrowing the read-only obligation.** It says every
documented endpoint leaves stored data unchanged, and `POST /api/v1/runs` has
violated that since Milestone 1 — this was never a Phase 3 problem. The
obligation is narrowed to the endpoints that *serve recorded history*, which is
what it always meant and the only reading that can be proven. That binds what a
future launch surface may do, which is why it earns **ADR-0015** on the
reversal-cost filter. The proof stays strong: the database is unchanged after
every read endpoint is called.

**Q3 — The lean/full split ships, recorded as Inspection-verified and currently
vacuous.** `result.traceback` has no writer, so the first criterion's
"500 results each carrying a 40 KB traceback" cannot be constructed from recorded
data and the check cannot fail. Build the split anyway — it is the shape the
endpoint needs and retrofitting it later is worse — but **say in the spec that
its non-vacuity waits on failure capture**, and do not count it as Test-verified.
An unfailable check recorded as passing is the failure mode OQ-10 rejected a
generated interface document over.

**Q4 and Q5 are design's to settle, with a stated reason**: the wire encoding of
a test's identity (`node_id` contains `/`, `::`, `[`, `]`, and `stable_id`
supersedes it in Phase 2, so the endpoint should be identity-agnostic from the
start), and the interface document's format and location. Neither changes the
shape of the change; both change a detail that must be written down rather than
chosen silently.

**Q6, decided without being asked — the generated documents are disabled.**
`create_app` uses a bare `FastAPI()`, so `/openapi.json`, `/docs` and `/redoc`
are served **generated** today; verified by request, all three answer `200`. That
contradicts OQ-10, where a generated document was rejected by name because it is
the code in another format: the interface document's drift criterion — an
endpoint present in the service and absent from the document is reported — could
never fail against it. Leaving them on would have the drift test compare the code
with itself.

## Addendum — 2026-08-21

The 2026-08-19 answers above are left unedited in substance. This addendum
records what has since changed and what was newly decided.

**Q1 is resolved, not reopened.** The answer was "VCS capture goes first", and
it went first: `vcs-capture` was implemented, verified and archived on
2026-08-20 (`openspec/changes/archive/2026-08-20-vcs-capture/`), merging
`openspec/specs/version-control-context/spec.md`. `_UPSERT_RUN` writes all six
`vcs_*` columns via `_vcs_columns`, with per-column `COALESCE` merge on the
conflict branch, and `_row_to_vcs_context` reads them back. The Q1 answer stands
as the record of *why* `vcs-capture` exists; it is no longer a dependency. The
test history is now fully reachable here.

**A — History entries carry the full VCS context, not just the commit hash.**
`commit`, `branch`, `commit_subject`, `commit_subject_truncated` and `dirty`,
because "this test failed on this branch, with a dirty tree" is what the product
exists to serve; the columns are populated, and retrofitting the shape later is
worse than paying for it now. Consequences worked through in *What a history
entry carries, and what it must not* above: the port reuses `VcsContext` rather
than minting a second type; `commit_subject_truncated` surfaces because it must
travel with the value it describes; `vcs_root` is excluded and the exclusion is
asserted; the list projection bounds the subject width; the lean-list rule gains
a genuinely-failable instance while its traceback half stays vacuous; and the
null-VCS case keeps the meaning Q1 gave it.

**B — Scope stays as written: all five slices.** Port read surface + both
adapters; run list + run detail; test history + `derive_presentation`;
hand-written OpenAPI document + drift test + generated docs disabled; read-only
proof harness + latency benchmark + committed measurements + ADR-0015. Nothing
is split out to a later change.

**Budget note, surfaced rather than resolved.** The review budget for this
session is **400 lines**, not the 500 the original forecast was written against.
Four of five slices now exceed it. That is recorded as the top risk and handed to
`sdd-tasks`; this phase did not re-slice and did not accept a `size:exception`.

**Q4 and Q5 remain deferred to `sdd-design`, for the reason given on
2026-08-19.** They are not answered here.

**Identifier translation, 2026-08-21.** Every numeric requirement identifier in
this document — including in the question round and its answers above — was
replaced by the obligation stated in prose and anchored to its owning capability
and scenario, at the user's direction. **No decision was changed, no answer
reversed, and no reasoning altered.** Separately, this change's ADR was
renumbered from 0014 to **0015**, because `vcs-capture` took 0014 when it landed
(`docs/adr/0014-execute-git-from-the-plugin-as-a-bounded-fail-closed-subprocess.md`).

---

## Superseded by design — 2026-08-21

`sdd-design` (decisions D53–D67, `openspec/changes/read-api/design.md`) settled
two points this proposal had asserted earlier. **The design is authoritative on
both.** They are recorded here rather than edited above, so the reasoning that
led to each remains readable.

**1. The VCS type on list paths.** The addendum above states that a history
entry reuses the existing `VcsContext` domain type throughout. The design keeps
that reuse on **run detail only**, and gives list paths a read-specific
projection that omits `root` structurally rather than by assertion. The
obligation is unchanged — `root` never reaches the wire — but the mechanism
moved from a runtime assertion to a type that has no field to leak. No delta
spec names a type, so no scenario is affected.

**2. Five slices became seven.** The forecast above reads ~2,220 lines across
five slices with four over the 400-line budget. The design re-forecasts
**~2,490 lines across seven slices, none over 400**, and adds a hard ordering
constraint the five-slice shape did not have: the interface document must land
**before** the read-only proof harness, because the harness derives its call
list from the document. `sdd-tasks` owns the final slicing.

**3. The interface document is not in `docs/`.** In scope above places it at
`docs/api/v1.yaml`, packaged into the wheel. The design found that impracticable
— hatchling with `packages = ["src/vantage"]` cannot reach a file above the
distribution root — and moved it inside the distribution. Location is design's;
the obligation is unchanged.

**4. A correction to D54's stated mechanism, measured 2026-08-21.** The design
originally justified the query-parameter choice by asserting that an encoded
slash cannot survive a path parameter. Measured against a live uvicorn server,
`/{identity:path}` returns the node id **byte-identical**; only a plain
`/{identity}` fails, with `404`. The decision stands, on the corrected reasoning
now recorded in D54: `:path` *reassembles* a value the transport already split,
so its success is a property of the deployment — proxy slash normalisation — and
not of the contract. A suite running against a bare ASGI transport cannot
observe that difference.
