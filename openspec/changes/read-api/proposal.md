# Proposal: Read API

## Intent

Vantage records well and shows nothing. `vantage.service` serves two write
endpoints and `GET /api/v1/capabilities`; 269 tests prove data goes in, and the
only way to read it back is opening the SQLite file by hand. This change adds
the server-side read surface — run list, run detail, test history — and the
machine-readable interface document that makes "every endpoint" enumerable.

It is also the first caller of `derive_presentation`, which
`session-lifecycle` shipped deliberately as a seam with no reader.

## What the data actually supports today

`_UPSERT_RUN` writes eight `run` columns: `id`, `received_at`,
`last_contact_at`, `started_at`, `finished_at`, `exit_status`, `interrupted`,
`interrupt_reason`. `_to_result` maps outcome, durations, phase outcomes and
`worker_id`. Nothing else is populated.

| Requirement | Status this change can reach | Blocker |
|---|---|---|
| RQ-14 read-only | **Contradicted as written** — see below | OQ-9 |
| RQ-15 test history | **Partly met**: order, duration, empty-history, null-commit-tolerance. Criterion 1's *commit hash* unreachable | `vcs_*` written by nothing; `vcs` is an unbuilt Milestone 3 envelope section |
| RQ-16 lean lists | **Vacuously met** — `traceback`, `failure_*`, `captured_*` appear only in `schema.sql` | no failure capture yet |
| RQ-17 pagination | **Fully met** | none |
| RQ-33 latency | **Measurable** (Analysis, committed numbers) | needs a 500-run / 100k-result fixture generator |
| RQ-36 interface document | **Fully met** | `docs/api/v1-ingestion.md` is prose, not machine-readable |
| RQ-44 read-back | **Promoted** from Analysis to Demonstration | port does not expose `last_contact_at` |
| RQ-18/19/20/34 | **Out of scope** — Demonstration through a web interface (ADR-8) that does not exist | |

### RQ-14 is already false, not future-tense

OQ-9 frames the read-only contradiction as arriving with a Phase 3 launch
surface. It arrived in Milestone 1. `POST /api/v1/runs` is a documented
endpoint and it writes, so criterion 1 — *call every documented endpoint, the
file is byte-identical* — cannot pass today and could never pass. The
requirement notes offered two ways out and chose neither; this change takes the
second: **scope the read-only obligation to a named read surface**, so
ingestion and any later launch surface are out of scope by construction rather
than by exception. That resolves OQ-9 and earns **ADR-0014** on the reversal-cost
filter: reversing it re-decides the product's safety posture and re-specifies
every endpoint.

Proving it is not free. The store opens WAL, so a read connection can
checkpoint into the main file on close and `-wal`/`-shm` appear beside it.
Naive `hash(db_bytes)` before/after is unstable for reasons unrelated to
writing. The proof is a **pair**: main-file digest taken with the connection
state pinned, plus a logical content digest over every table, plus
`count_executions()`/`count_results()` unchanged. Verification method is
**Test**, over the document-declared read paths only.

## Scope

### In scope

- `GET /api/v1/runs` (newest first by `started_at`, paginated), `GET /api/v1/runs/{id}`, `GET /api/v1/runs/{id}/results`, test history keyed by `node_id` (ADR-0012)
- Pagination: hard cap 200, `has_more` (RQ-17 all three criteria)
- Lean list projection with the full record on the single-item endpoint (RQ-16)
- Read methods on `ExecutionStore`, implemented in **both** adapters and added to `vantage_port_contract.py`
- Exposing `last_contact_at` through the port so `derive_presentation` gets its first caller
- A hand-written machine-readable OpenAPI document + drift test (OQ-10, RQ-36); FastAPI's generated `/openapi.json`, `/docs`, `/redoc` disabled — a generated document is the code in another format and RQ-36.3 could never fail
- RQ-33 benchmark script, p95 and max committed as numbers in the spec (the RQ-3 precedent in `run-recording/spec.md`)
- ADR-0014 resolving OQ-9

### Out of scope

- The web interface (ADR-8) — RQ-18, RQ-19, RQ-20, RQ-34 stay unmet
- VCS capture (`vcs_*`), failure/traceback capture, environment capture
- `pytest-vantage` — unchanged, not opened
- Search, filtering, aggregation, flake scoring; any write endpoint
- Migrating the remaining 27 legacy requirements

## Capabilities

### New Capabilities
- `history-read-api`: read endpoints, ordering, pagination, lean projection, latency, and the scoped read-only guarantee (RQ-14 scoped, RQ-15, RQ-16, RQ-17, RQ-33)
- `api-interface-document`: hand-written machine-readable document and its drift test (RQ-36, RQ-14.2)

### Modified Capabilities
- `session-liveness`: Purpose states RQ-44's read-back criteria are Analysis "against the derivation helper here, not Demonstration through a live read path". This change supplies that path — the criteria become Demonstration.
- `session-ingestion`: names the ingestion endpoints as outside the read-only surface, so the scoping is recorded where the writes live rather than only in the new capability.

## Approach

Port first, routes second, document third, proof last. Read methods land on
`ExecutionStore` and both adapters together (the contract suite forces
agreement, which is the point). Routes are thin: SQL shapes the page, Pydantic
shapes the response, and `derive_presentation` runs in `vantage.core` on values
the row already carries. `last_contact_at` is not on `Execution` by design
(D1); the port grows a read type that carries it alongside, rather than
polluting the domain aggregate.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `vantage/core/ports/storage.py` | Modified | read/query methods, page and detail read types |
| `vantage/core/domain/liveness.py` | Unchanged | gains its first caller |
| `vantage/storage/{sqlite_store,memory}.py` | Modified | both adapters, in lockstep |
| `vantage/service/routes/read.py` | New | the read router |
| `vantage/service/schemas.py` | Modified | response models (list vs detail projections) |
| `vantage/service/app.py` | Modified | mount router; disable generated OpenAPI/docs |
| `docs/api/v1.yaml` | New | the hand-written contract |
| `docs/adr/0014-*.md` | New | OQ-9 resolution |
| `docs/open-questions.md` | Modified | OQ-9 → Answered |
| `packages/vantage/tests/vantage_port_contract.py` | Modified | new contract scenarios |
| `pytest-vantage` | **Untouched** | |

## Delivery forecast (500-line review budget)

| # | Slice | Forecast | Risk |
|---|---|---|---|
| 1 | Port read surface + both adapters + contract scenarios | ~470 | High — biggest slice, two implementations |
| 2 | Run list + run detail routes, pagination envelope, lean projection | ~420 | Medium |
| 3 | Test history route + `derive_presentation` wired in | ~360 | Medium |
| 4 | Hand-written OpenAPI document, drift test, generated doc disabled | ~340 | Low |
| 5 | Read-only proof harness + RQ-33 benchmark + committed measurements + ADR-0014 | ~400 | Medium |

**Total ~1,990 lines across 5 slices. No slice exceeds 500.**
`chain_strategy: feature-branch-chain`.

**Verification forecast** (the `session-lifecycle` lesson — six slices, not
four, because the test-layer plan was skipped):

| Obligation | Method | Why |
|---|---|---|
| RQ-15, RQ-16, RQ-17 | Test | assertable response properties |
| RQ-14 scoped | Test | digest pair over document-declared read paths |
| RQ-33 | **Analysis** | a percentile over a distribution, not an assertion; timing assertions are flaky on the 3.10–3.13 × xdist matrix |
| RQ-36.3 | Test | drift test compares document to served routes |
| RQ-44 read-back | **Demonstration** | through the live read path |
| RQ-16 non-vacuity | **Inspection** | with no traceback recorded, the exclusion test cannot fail; record that honestly |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| WAL checkpointing makes the byte-identity proof flake | High | digest pair (file + logical) with pinned connection state; a flaky RQ-14 test is worse than none |
| RQ-16 ships as a green check that cannot fail | High | state it as Inspection; re-verify when failure capture lands |
| RQ-15 recorded as met with null commits | Medium | proposal states it partly met; spec must not claim criterion 1 |
| Node ids contain `/`, `::`, `[`, `]` — path-segment encoding | Medium | decide query parameter vs encoded segment in design; wrong choice is a breaking API change later |
| RQ-33's index conflicts with RQ-25's 2% write budget (flagged in requirement notes) | Medium | measure both, together, not separately |
| Two adapters drift | Low | contract suite already forces agreement |
| Container/timezone/Python-version defects the last change's four verify rounds missed | Medium | verification asks "does it survive another timezone, another interpreter, an older client", not only "does it satisfy the spec" |

## Rollback Plan

Every slice is additive; nothing existing changes behaviour except two lines.

1. **Per slice**: revert the branch. No schema change, no migration, no data
   written — the read API cannot corrupt what it never writes.
2. **The two reversible edits**: `create_app`'s `openapi_url=None,
   docs_url=None, redoc_url=None` restores FastAPI's generated document by
   deleting three kwargs; `include_router(read_router)` removes the surface
   entirely.
3. **ADR-0014**: if OQ-9's resolution is later rejected, supersede rather than
   edit (`CLAUDE.md`), and reopen OQ-9 with its status restored.
4. **Committed measurements**: numbers in a spec are documentation; reverting
   is a text revert.

## Dependencies

- None external. No new third-party distribution — FastAPI and Pydantic are already `vantage.service` dependencies (ADR-11), and `vantage.core`/`vantage.storage` stay stdlib-only (RQ-24, RQ-26).
- **Blocked-on decisions**: see the question round below. Specs cannot be written until Q1 and Q2 are answered.

## Success Criteria

- [ ] A client can list runs, open one, and read a test's history over HTTP, without opening the database file
- [ ] Every documented read endpoint leaves the stored data provably unchanged, by both digests
- [ ] The hand-written document is the contract, and an endpoint absent from it fails the suite
- [ ] `derive_presentation` has a caller, and RQ-44's read-back criteria are demonstrated rather than argued
- [ ] p95 and max latency exist as committed numbers, not a `print()`
- [ ] RQ-15 and RQ-16's unmet halves are recorded as unmet, not quietly claimed
- [ ] OQ-9 is answered in `docs/open-questions.md` and bound by ADR-0014

---

## Proposal question round

These are decisions a human must make. None has a safe default.

**Q1 — RQ-15's commit hash: ship partial, or capture VCS first?**
Criterion 1 requires a commit hash per history entry. Six `vcs_*` columns exist
and nothing writes them. Either (a) this change ships returning `null` commits
and RQ-15 stays partly unmet until VCS capture lands, or (b) a `vcs-capture`
change goes first and read-api fully satisfies RQ-15. (a) gets a usable read
API sooner and matches criterion 2's own tolerance for null commits; (b) avoids
a spec that records a Must-Have as half-delivered. **Which?**

**Q2 — Does OQ-9 close here, or only get restated?**
The proposal takes the "narrow RQ-14 to the endpoints that serve recorded
history" exit and closes OQ-9 via ADR-0014. The alternative is scoping RQ-14 to
Phase 1 and leaving OQ-9 open for a later write surface. Closing it now binds a
future launch surface to a boundary that does not exist yet. **Close, or
restate?**

**Q3 — Is RQ-16 in scope at all while no traceback is recorded?**
Its exclusion test cannot fail today. Options: (a) implement the lean/full
split now and record RQ-16 as Inspection-verified-vacuous; (b) seed
`result.traceback` directly at the storage layer for the test, which exercises
response shaping honestly but tests a column no recorder writes; (c) defer
RQ-16 to the failure-capture change. **Which?**

**Q4 — Test history identity on the wire.**
`node_id` contains `/`, `::`, `[`, `]`. Path segment (encoded), or query
parameter? `stable_id` supersedes `node_id` in Phase 2 (`schema.sql`), so
whichever is chosen becomes a breaking change then unless the endpoint is
identity-agnostic from the start. **Which shape?**

**Q5 — Interface document format and location.**
RQ-36 deliberately does not name a format (requirement notes: "the format is
deliberately not named... belongs in an ADR"). Proposal assumes hand-written
OpenAPI 3.1 YAML at `docs/api/v1.yaml`, packaged into the wheel and served.
Does the format choice want its own ADR, or is it cheap enough to reverse that
a design note suffices? And does `docs/api/v1-ingestion.md` become prose
commentary beside it, or get folded in?

## Answers to the question round — 2026-08-19

**Q1 — VCS capture goes first.** RQ-15 is the endpoint whose own rationale says
it is what the product exists to serve, and its first criterion requires a commit
hash. Six `vcs_*` columns exist in `schema.sql` and nothing writes any of them.
Shipping the history endpoint with null commits would record a Must Have as
half-delivered and leave the flagship endpoint born incomplete. A `vcs-capture`
change lands first; this one then satisfies RQ-15 whole. Criterion 2's tolerance
for a null commit stays what it was written for — a directory that is not a git
repository — rather than becoming a cover for not recording it at all.

**Q2 — OQ-9 closes here, by narrowing RQ-14.** The requirement says every
documented endpoint leaves stored data unchanged, and `POST /api/v1/runs` has
violated that since Milestone 1 — this was never a Phase 3 problem. RQ-14 is
narrowed to the endpoints that *serve recorded history*, which is what it always
meant and the only reading that can be proven. That binds what a future launch
surface may do, which is why it earns **ADR-0014** on the reversal-cost filter.
The proof stays strong: the database is unchanged after every read endpoint is
called.

**Q3 — RQ-16 ships with the lean/full split, recorded as Inspection-verified and
currently vacuous.** `result.traceback` has no writer, so criterion 1's
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
the code in another format: RQ-36's criterion — an endpoint present in the
service and absent from the document is reported — could never fail against it.
Leaving them on would have the drift test compare the code with itself.
