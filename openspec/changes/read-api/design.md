# Design: Read API

> **Identifier vocabulary.** This document carries no numeric requirement
> identifiers, matching `proposal.md` and the five delta specs. Every
> obligation is anchored to the **capability** and, where one exists, the
> **scenario** that owns it. Decisions continue the project's single running
> sequence: `vcs-capture` closed at **D52**, so this change opens at **D53**.

## Technical Approach

Port first, routes second, document third, proof last — the proposal's order,
unchanged.

Four read methods land on `ExecutionStore` and on **both** adapters together,
with `vantage_port_contract.py` forcing agreement. The projection that makes a
list response lean happens in **SQL**, not in the response model, so the store
never materialises what the wire will not carry. `vantage.core` gains one
stdlib-only module holding the read types and the reference implementation of
that projection; the SQLite adapter expresses the same rule in SQL and the
in-memory adapter in Python, exactly the two-mechanism pattern `merged_over`
and `COALESCE` already established (D48).

Routes stay thin: the port shapes the page, Pydantic shapes the response, and
`derive_presentation` runs in `vantage.core` on values the row already carries.
`last_contact_at` never joins the `Execution` aggregate (D1); it rides beside it
on a read type.

Nothing about the write path changes. No schema statement, no new index, no new
column, no altered `record_session`. That is not a coincidence — it is D63.

---

## Architecture Decisions

### D53 — The read surface is whatever the interface document tags `read`

ADR-0015 decides *that* the read-only guarantee is scoped to a named read
surface, and why. This decision is only the mechanism, per
`openspec/config.yaml`'s rule against restating an ADR.

The surface is not a list in a test file and not "the GET routes". It is the set
of operations the hand-written document tags `read`. `history-read-api` →
*Read-only read surface* says "every endpoint declared as a read path in the
machine-readable interface document"; `session-ingestion` → *Ingestion endpoints
excluded from the read-only surface* says the two ingestion endpoints are marked
as writing. One document answers both, and the read-only harness derives its
call list from it.

The consequence is deliberate: adding a route to the document under the `read`
tag automatically enlists it in the digest-pair proof. Adding one without a tag
fails the drift check. There is no way to add a read endpoint that the guarantee
silently does not cover.

`GET /api/v1/capabilities` is tagged `read` and joins the proof — it reads
nothing from the store, so it passes trivially, and that is the correct answer
rather than an exemption.

**Method is not the classification.** Deriving `read` from "the verb is GET"
would make the scoping implicit and unauditable, and it would leave
`session-ingestion`'s Inspection scenario with nothing to inspect. The tag is
declared; a consistency check additionally asserts every `read` operation is a
GET and every `write` operation is not, so the two cannot drift apart unnoticed.

### D54 — A test's identity is a named query parameter on an identity-free path

**Answers Q4.**

```
GET /api/v1/tests/history?node_id=<value>&limit=&offset=
```

The path contains no identity at all. The identity travels as a query parameter
whose **name is the identity scheme**.

**Why not an encoded path segment.** A pytest node id contains `/`, and an
encoded slash does not survive the ASGI path *as an encoded slash*. Uvicorn
decodes the request target and puts the *unquoted* result in `scope["path"]`;
Starlette routes on `scope["path"]`. So `%2F` has already become `/` before any
route matcher runs.

Measured against a live uvicorn server on 2026-08-21, rather than asserted:

| Route | Node id `tests/test_a.py::TestSuite::test_x[case/1]`, percent-encoded |
| --- | --- |
| `/{identity}` | `404` — the decoded value spans segments and no route matches |
| `/{identity:path}` | `200`, value received **byte-identical** |
| `?node_id=` | `200`, value received **byte-identical** |

So a plain path parameter is disqualified outright, but `{identity:path}` does
round-trip. It does so by *reassembling* a value the transport already split,
not by preserving what the client sent: the converter matches greedily across
whatever segments the decoded path happens to have. That reassembly is correct
only while nothing else in the application's routing competes for those
segments, and only while every hop in front of the application leaves the
decoded path byte-identical — which is exactly the assumption a deployment
breaks. nginx merges and normalises slashes by default, and Apache answers
`404` for `%2F` unless `AllowEncodedSlashes` is explicitly enabled. A node id
containing `//` collapses under the former and never arrives under the latter.

The disqualifier is therefore not that the match fails — under a bare uvicorn it
succeeds — but that its success is a property of the deployment rather than of
the contract. The failure is silent and deployment-dependent, which is the worst
shape a routing bug can take, and a test suite running against a bare ASGI
transport is structurally unable to catch it.

A query **value** has none of this. It is percent-decoded once by the standard
query parser, after routing, and `/`, `::`, `[` and `]` are ordinary bytes
there. No proxy normalises inside a query value.

**Why not an opaque server-minted token.** "Identity-agnostic" could mean the
endpoint takes a token the server issued. Rejected: it needs a minting endpoint
this change does not have, and it defeats the actual use case — a developer or a
CLI already holds a node id copied from pytest output and must be able to
construct the request from it. An opaque token makes the endpoint unusable by
the only clients that exist.

**What identity-agnostic means here instead.** The resource is *a test's
history*; the identity scheme is a parameter of the request, not part of the
resource's address. When `stable_id` supersedes `node_id` (`schema.sql`,
ADR-0012), the endpoint gains `?stable_id=` as a **sibling parameter**. That is
additive. The path never changes, no client that sends `?node_id=` breaks, and
both columns exist on `test_case` today so both lookups are implementable
simultaneously. Exactly one of the two must be present: neither and both are
`422`, shaped by `errors.py` like every other rejection.

**The identity value is bounded at 1,024 characters**, rejected above that with
a shaped `422` rather than left to become a proxy-generated `414`. The bound is
derived, not guessed: a 1,024-character identity percent-encodes to at most
~3 KiB, comfortably inside the 8 KiB request-line buffer that is the common
default (nginx `large_client_header_buffers`). A test whose node id exceeds
1,024 characters is unreachable through this endpoint; that is recorded as a
known limit rather than discovered as a truncation.

### D55 — Hand-written OpenAPI 3.1 YAML, inside the distribution, served as bytes

**Answers Q5, first half.**

| Question | Decision |
| --- | --- |
| Format | OpenAPI 3.1, YAML |
| Location | `packages/vantage/src/vantage/service/openapi/v1.yaml` — **inside** the package, not `docs/api/v1.yaml` |
| Loaded by | `importlib.resources.files("vantage.service.openapi")` |
| Served at | `GET /api/v1/openapi.yaml`, as bytes, `media_type: application/yaml` |
| Parsed at runtime | **No** |
| Parsed at test time | Yes — `pyyaml`, dev extra only |
| Earns its own ADR | **No** — design note. See D67 |

**The proposal's `docs/api/v1.yaml` assumption does not hold, and packaging is
why.** `packages/vantage/pyproject.toml` builds with hatchling and
`packages = ["src/vantage"]`. `docs/api/` sits at the repository root, above the
distribution root; a build backend cannot reliably reach above its own project
directory, and forcing it would produce a wheel whose contents depend on the
checkout layout rather than on the package. Moving the document inside the
package makes it ordinary package data — hatchling ships every file under the
configured package path — and `importlib.resources` reads it identically from a
wheel, a zip and an editable install, with no `__file__` arithmetic.

**Runtime needs no parser.** The route reads the bytes and returns them. That
keeps the serving path dependency-free and means a malformed document cannot
break the server at import time. The drift check and the 2xx check parse it in
CI, so a malformed document cannot pass the gate either — the parse happens
where a failure is a red test rather than a production incident. `pyyaml` is a
**dev** dependency: it is not a runtime dependency of anything, and
`architecture-boundaries` → *Zero runtime dependencies* is untouched.

**Why YAML and not JSON.** JSON would need no parser even in tests. It also
carries no comments, and every hand-written artefact in this repository earns
its keep partly through explanation. A hand-written contract with no room to say
*why* a status code is what it is would be a worse document, and the drift check
would then be comparing an unexplained list against the code.

**A wheel-content check, not a build-config assumption.** A test loads the
document through `importlib.resources` rather than a relative path, so the
mechanism that must work in a wheel is the mechanism the suite exercises.

### D56 — `docs/api/v1-ingestion.md` stays as prose, and stops enumerating paths

**Answers Q5, second half.**

Not folded in. OpenAPI `description` fields are a poor home for multi-paragraph
rationale, and burying the reasoning there would make every drift diff noisy
while making the reasoning harder to find.

Kept, with one rule applied: **one source per fact.** The file keeps what only
prose can carry — why `extra=` is asymmetric between `run` and the envelope, why
`<unnamed>` is an allow-list and not a failure to identify a field, and the
"nothing is written before `201`/`200`" guarantee. Its request-shape example and
its response-status table are removed, because the document now states those and
two statements of one fact drift. A header line names the document as the
contract and this file as the reasoning.

The document is **not** copied to `docs/api/`. A second copy is a drift source,
and the drift check would not cover it.

### D57 — Four read methods; the projection happens in SQL

```python
# vantage/core/ports/storage.py — additions
def list_runs(self, *, limit: int, offset: int) -> Page[RunListEntry]: ...
def get_run_detail(self, execution_id: str) -> RunDetail | None: ...
def list_results(self, execution_id: str, *, limit: int, offset: int) -> Page[Result]: ...
def list_history(self, *, node_id: str, limit: int, offset: int) -> Page[HistoryEntry]: ...
```

**Where the projection happens: SQL.** The lean-list rule exists to bound
response size. Bounding in the response model bounds the wire and nothing else —
the store has already pulled 200 × up to 64 KiB of commit subject into memory to
throw most of it away. `substr(vcs_commit_subject, 1, ?)` bounds it before it
leaves SQLite. The in-memory adapter slices the same value in Python, and the
contract suite proves the two agree.

The units match by construction, which is the reason to bound in **characters**
rather than bytes: SQLite's `substr`/`length` count characters on TEXT, and
Python's slicing and `len` count characters on `str`. A byte bound would need
two different mechanisms and would invite the mismatch the 64 KiB capture bound
already had to think about (D49).

`get_results` is left in place, unchanged and un-deprecated; `list_results` is
its paginated sibling.

### D58 — The lean/full split is two port return types, not one type projected twice

`RunListEntry` and `RunDetail` are distinct types with distinct SQL behind them.
The list type's commit subject is bounded before it leaves the database; the
detail type's is the whole stored value. One type used for both would make its
`commit_subject` field mean different things depending on which call produced
it, and would push the split back into the response model, which is what D57
rejects.

`Page[T]` is a two-field envelope:

```python
@dataclass(frozen=True)          # no slots=True: dataclass slot-recreation
class Page(Generic[T]):          # plus Generic is a hazard on the 3.10 floor,
    items: tuple[T, ...]         # and this envelope gains nothing from slots
    has_more: bool
```

No `total`. A total requires a `COUNT(*)` on every page and no scenario asks
for one.

### D59 — List-shaped reads carry `VcsProjection`; run detail carries `VcsContext`

**This diverges from the proposal's addendum decision A, which assumed the read
types reuse `VcsContext` throughout. It is spec-neutral** — no scenario in
`history-read-api` names a type; they name fields and the null-block behaviour,
both of which `VcsProjection | None` satisfies exactly.

```python
# vantage/core/domain/projection.py — stdlib only
LIST_COMMIT_SUBJECT_CHARS = 120

@dataclass(frozen=True, slots=True)
class VcsProjection:
    commit: str | None
    branch: str | None
    commit_subject: str | None        # bounded to LIST_COMMIT_SUBJECT_CHARS
    commit_subject_truncated: bool    # see D60
    dirty: bool | None
    # no `root` — absent by construction

def project_vcs(vcs: VcsContext | None) -> VcsProjection | None: ...
```

Two reasons the proposal's reuse does not survive contact with the bounded
subject:

1. **A bounded value inside `VcsContext` breaks that type's own contract.**
   `VcsContext`'s docstring binds `commit_subject_truncated` to the stored
   subject. A `VcsContext` whose `commit_subject` is a 120-character slice is a
   type saying one thing and holding another — the exact dishonesty the flag
   exists to prevent, relocated.
2. **`root` cannot leak from a type that has no `root`.** On the list and
   history paths the exclusion becomes structural rather than disciplinary, so a
   field added to `VcsContext` later cannot ride onto the wire by default. The
   exclusion tests the specs require still exist; they now guard the **detail**
   path, where `VcsContext` is genuinely the type in hand and the exclusion is a
   choice that can be got wrong.

**The proposal's rejected-alternative cost is not paid.** Its objection was "two
vocabularies for one concept and a second copy of the null rule". There is no
second copy: `project_vcs` takes a `VcsContext | None` and returns `None` for
`None`, so the all-null normalisation stays where `_row_to_vcs_context` already
put it and is inherited rather than restated. The SQLite adapter builds
`VcsProjection` from its own `substr`/`length` columns; `project_vcs` is the
reference implementation the contract suite holds it to. Same shape as
`merged_over` versus `COALESCE`.

**Run detail keeps `VcsContext` unchanged**, so the proposal's reasoning stands
where it applies. The detail response model is built **field by field**, never
with `from_attributes` over a domain object — an implicit mapping is how `root`
would arrive on the wire the day someone adds a field.

### D60 — 120 characters, and what the flag means in a list

| Where | `commit_subject` | `commit_subject_truncated` means |
| --- | --- | --- |
| Run list, history entry | first 120 characters | *this value is not the whole stored subject* — capture-truncated **or** display-bounded |
| Run detail | the whole stored value | *git's own subject exceeded the 64 KiB capture bound* (unchanged meaning) |

**The width.** 64 KiB × 200 items is 12.8 MB, which the bounded-response rule
forbids outright. At 120 characters a full page is at most 200 × 120 × 4 bytes
≈ 94 KiB of subject in the worst multi-byte case, and roughly 24 KiB for ASCII.
120 is chosen over git's own 50/72 subject conventions because those describe
what authors *should* write, not what repositories contain: merge subjects and
scoped conventional-commit subjects routinely run past 72, and a width that
shortens ordinary real subjects would make the flag noise rather than signal.

**A subject longer than the width** is cut to 120 characters and the flag is
set; the whole stored subject stays reachable at `GET /api/v1/runs/{run_id}`,
which is the other half of the lean-list rule — excluding a field from a list is
only correct while it remains reachable somewhere.

**Why one flag with a widened meaning, not two booleans.** In a list, the only
question a reader can act on is "is this the whole thing, or do I open the run?".
One boolean answers it. Capture-time provenance only matters once you hold the
stored value, which is to say on detail, where the flag keeps its original
meaning exactly. Two booleans in a list would be two answers to a question
nobody asked twice.

**This extends the specs rather than contradicting them.** `history-read-api` →
*Lean list projections* requires the flag to travel with the subject wherever the
subject appears; both rows above satisfy that, and a capture-truncated subject
still reports `true` in a list because the condition is a disjunction. The specs
do not define the disjunction; this decision does.

**The history entry's subject is bounded too.** A test's history is a list
response containing a run's commit subject, so *Lean list projections* reaches
it. Each history entry carries its run id, so the full subject is one hop away
at that run's detail endpoint. The spec permits this reading; it does not state
it for the history endpoint specifically.

In SQL, with `?` bound to `LIST_COMMIT_SUBJECT_CHARS`:

```sql
substr(vcs_commit_subject, 1, ?)                              AS commit_subject,
CASE WHEN vcs_commit_subject_truncated = 1
       OR COALESCE(length(vcs_commit_subject) > ?, 0) = 1
     THEN 1 ELSE 0 END                                        AS commit_subject_truncated
```

`COALESCE` is load-bearing: `length(NULL) > 120` is `NULL`, not `0`, and a null
subject must produce a `false` flag rather than a null one.

### D61 — Pagination: clamp at 200, fetch one extra, order totally

| Concern | Decision |
| --- | --- |
| Hard cap | `MAX_PAGE_ITEMS = 200`, enforced **in the adapter**, clamped never rejected |
| `has_more` | fetch `limit + 1` rows, return `limit`, `has_more = fetched > limit` |
| `limit` ≤ 0 or non-integer | `422` at the route — that is not a page size |
| Ordering | `ORDER BY started_at DESC, id DESC` |

**Clamp, not reject**, because `history-read-api` → *Bounded pagination*
describes a page "requested without an explicit smaller page size" returning at
most 200 and reporting more exist. A rejection would not satisfy that scenario.

**`limit + 1` needs no `COUNT`** and gets the exactly-200 scenario right by
construction: 200 stored and 200 requested fetches 201 and receives 200, so
`has_more` is `false`; store one more and the same request receives 201, so it
is `true`. Truncation and exhaustion are distinguished by the fetch itself
rather than by a second query that can race the first.

**The cap lives in the adapter**, not only at the route, so no future caller can
ask a store for ten thousand rows. The route validates the parameter shape; the
adapter enforces the bound. Two layers, deliberately, the same reasoning as the
concurrency net (D8).

**`ORDER BY started_at DESC, id DESC`.** `started_at` is not unique — two runs
can share it, and under xdist they routinely nearly do. An order that is only
partial lets a page boundary fall inside a tie group, which duplicates a row on
one page and drops it from the next. The primary key is the tiebreak, so the
order is total. Both adapters sort identically and the contract suite proves it
with two runs sharing a `started_at`.

Offset pagination, not a cursor: a run arriving mid-pagination shifts the window.
That is accepted for this phase — no scenario asks for a stable cursor — and
noted in Open Questions rather than left to be discovered.

### D62 — Presentation is derived at the service layer; the port never holds a clock

`derive_presentation` gets its first caller in `routes/read.py`:

```python
derive_presentation(
    entry.execution,
    last_contact_at=entry.last_contact_at,
    now=datetime.now(timezone.utc),
    grace=timedelta(seconds=request.app.state.grace_period),
)
```

`app.state.grace_period` — a named seam with no reader since D34 — finally has
one. The port returns `last_contact_at` beside the `Execution` and computes
nothing: a store that knows what time it is has started doing domain work, and
the two adapters would then have to agree about clocks as well as rows.

**The demonstration needs no clock control.** `session-liveness` → *Abandoned run
is observable* is demonstrated by writing a run whose `last_contact_at` is old
relative to a configured grace period, not by freezing time or sleeping. The
fixture owns the data; `create_app(grace_period_seconds=...)` owns the threshold.
Deterministic on every interpreter in the matrix.

### D63 — No new index, and that is what dissolves the budget conflict

The proposal flags the latency obligation and the whole-session recording
overhead as conflicting, and requires them measured together.

**They do not conflict, because the read path needs no new index.** The history
query walks `idx_test_case_node_id` (unique) to one `test_case.id`, then
`idx_result_test_case_id` for that test's results, then the `run` primary key
per result. Over the specified fixture — 500 runs, 100,000 results, a target
test present in every run — that is ~500 index-ranged rows and ~500 primary-key
lookups, then a sort of at most 500 rows. Both indexes already exist in
`schema.sql`.

`schema.sql` is therefore **unchanged**, `record_session` is unchanged, and the
write path pays nothing at all for this change. The overhead numbers in
`version-control-context` → *Measurements* remain true by construction rather
than by re-measurement.

**The number the fallback is designed against.** If the measured p95 misses
100 ms, the first remedy is a composite `result(test_case_id, run_id)` index —
and it must be paid for out of the measured headroom before it is accepted. That
headroom is computable from the recorded numbers: the synthetic-repository
10 ms profile records an 11.146 s ON median against a 10.981 s OFF median, so
the 2% budget allows 219.6 ms and 164.8 ms is already spent. **≈55 ms per
session remains.** A secondary B-tree insert for each of a 500-result session's
rows costs single-digit milliseconds inside one transaction, so the index would
fit — but the point is that it is checked against 55 ms rather than waved
through, and the benchmark re-runs `scripts/measure_vcs_overhead.py`'s 10 ms
profile alongside the latency measurement so both numbers come from one sitting.

### D64 — The benchmark, and how a percentile becomes a committed number

`scripts/measure_history_latency.py`, following `scripts/measure_vcs_overhead.py`
exactly — a manual harness, never collected by the suite, whose printed numbers a
human transcribes into the spec.

| Aspect | Decision |
| --- | --- |
| Fixture | 500 runs × 200 results = 100,000 results; ~200 distinct node ids; one target test present in all 500 runs; a second target present in one run only |
| Built by | `record_session` through the real SQLite adapter — never hand-written `INSERT`s, which could diverge from the rows production writes |
| Data | synthetic, generated in the script |
| Driven through | the ASGI app in-process, no socket — the obligation says *server-side*, and a loopback socket measures the kernel |
| Sampling | 5 warm-up requests discarded, then 200 timed with `time.perf_counter_ns` |
| p95 method | nearest-rank on the sorted samples, stated in the output — an unstated percentile method is not reproducible |
| Reported | p95 **and** the slowest single response, both required by the scenario |
| Also reported | the 10 ms-profile whole-session overhead, re-run per D63 |

**How it becomes a committed number rather than a `print()`**: the printed values
are transcribed into a **Measurements** paragraph under `history-read-api` →
*Test history latency*, carrying the same obligation sentence
`version-control-context` and `run-recording` already carry — a future change to
the history query or its indexes MUST re-run the script and update the
paragraph. The number lives in the spec, where a reviewer sees it; the script is
how it is produced, not where it is kept.

It is not a CI assertion, for the reason the spec already records: a timing
assertion is flaky across the 3.10–3.13 × xdist matrix, and a benchmark inside
that matrix is a check people learn to skip.

### D65 — The read-only proof, designed so it cannot flake

Three assertions, one sequence:

1. **Logical content digest** over every table — the strong one.
2. **Main-file digest**, main `.db` bytes only.
3. `count_executions()` / `count_results()` unchanged.

**Why the naive hash flakes, and what removes each cause.**

| Instability | Removed by |
| --- | --- |
| A closing connection checkpoints WAL into the main file | The fixture writer's store is **closed before** the read store opens, so the WAL is already checkpointed and removed; the read store then stays open across **both** digests, so no close can fire between them |
| `-wal` / `-shm` contents change without a row changing | They are **never digested**. Only the main file is |
| A second connection opening mid-sequence | `SqliteExecutionStore` holds exactly one connection for its lifetime and the app is wired to that one instance, so the read sequence opens and closes nothing. This is an existing property, not something added |

"Connection state pinned", in `history-read-api` → *The main-file digest is
stable despite WAL checkpointing*, means precisely this: both digests are taken
at the same point in one connection's lifecycle.

**The logical digest enumerates tables from `sqlite_master`**, never from a
hard-coded list:

```sql
SELECT name FROM sqlite_master
 WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
 ORDER BY name
```

then `SELECT * FROM <name> ORDER BY rowid` per table, canonically serialised into
one `sha256`. Every table in `schema.sql` is a rowid table, so the ordering is
available everywhere. A hard-coded list of ten would silently stop covering the
eleventh table someone adds; this cannot.

**The call list comes from the document** (D53), and a **binding table** maps each
`read`-tagged path template to a callable producing valid parameters from the
fixture. The test asserts every `read` path has a binding, so a path added to the
document without one **fails** rather than being skipped. That is what keeps this
proof from becoming the unfailable check the project rejected a generated
document over.

**The 2xx check and the read-only check must not share a database.**
`api-interface-document` → *Every documented path answers 2xx* covers every
declared path including the ingestion ones, so that check writes. The read-only
harness uses only `read`-tagged paths and its own fixture database. Two tests,
two path sets, two fixtures — stated here because sharing a fixture would make
the read-only proof fail for a reason that has nothing to do with reading.

### D66 — The drift check compares the document against `app.routes`

```
declared  = {(path, method) for the document's paths × their operations}
mounted   = {(route.path, method) for route in app.routes, excluding the
             framework's own default routes}
```

- `mounted - declared` → **served but undocumented**, the criterion that must be
  able to fail.
- `declared - mounted` → **documented but not served**, reported too; the
  scenario only names one direction, but a document declaring a path nothing
  serves is a lie in the other direction and costs one set difference to catch.

**FastAPI's generated documents are disabled** at the factory —
`FastAPI(openapi_url=None, docs_url=None, redoc_url=None)` — for the reason
already decided: a generated document is the code in another format, so
`mounted - declared` would be empty by construction and the check could never
fail. Disabling them also removes three routes from `mounted` that no
hand-written document should have to declare. A test requests all three and
asserts each answers `404`, and that `GET /api/v1/openapi.yaml` answers the
hand-written bytes instead.

`request.app.routes` includes Starlette's own `/openapi.json`-style entries only
when those URLs are configured; with all three set to `None` the exclusion list
is empty, and the test asserts that too rather than assuming it.

### D67 — Exactly one decision here earns an ADR

`openspec/config.yaml` and `CLAUDE.md` set the filter: more than a sprint to
reverse.

| Decision | Reversal cost | Verdict |
| --- | --- | --- |
| **Scope the read-only guarantee to a named read surface** | Reversing re-decides the product's safety posture and re-specifies every endpoint, and it binds a Phase 3 launch surface that does not exist yet. **Far more than a sprint** | **ADR-0015 — "Scope the read-only guarantee to a named read surface"**, Nygard, `Proposed` in the PR |
| Interface document format (OpenAPI 3.1 YAML) | One file rewritten, one loader, one parse in the drift test. The document *describes* the wire; no client keys on it. **Days, not a sprint** | design note (D55) |
| Document location inside the package | One path and one `importlib.resources` anchor | design note (D55) |
| Identity as a query parameter | A route signature and a document entry; the Phase 2 addition is additive by construction | design note (D54) |
| 120-character display width | One constant, two adapters | design note (D60) |
| 200-item cap and `limit + 1` | One constant and one SQL clause | design note (D61) |
| `VcsProjection` on list paths | One dataclass and one function | design note (D59) |
| No new index | Adding one later is one `CREATE INDEX IF NOT EXISTS` in a schema built complete at first use | design note (D63) |

No existing ADR is restated. ADR-0003 (clean architecture, `Protocol` ports),
ADR-0005 (complete schema, no migration framework), ADR-0006 (stdlib `sqlite3`),
ADR-0008 (the web interface owns output encoding), ADR-0009 (the server owns
every write), ADR-0011 (FastAPI on uvicorn) and ADR-0012 (the catalogue is keyed
by the node id) are referenced and relied on, never re-argued.

---

## Data Flow

```
  GET /api/v1/runs?limit&offset
        │  route: clamp-shape validation (limit > 0), 422 otherwise      (D61)
        ▼
  store.list_runs(limit=…, offset=…)
        │  SQL: SELECT … substr(vcs_commit_subject,1,120) …              (D57, D60)
        │       ORDER BY started_at DESC, id DESC                        (D61)
        │       LIMIT min(limit,200)+1 OFFSET offset
        ▼
  Page[RunListEntry]  ── entry.execution, entry.last_contact_at,
        │                entry.vcs: VcsProjection | None  (no `root`)    (D59)
        ▼
  derive_presentation(execution, last_contact_at=…, now=…, grace=…)      (D62)
        │  vantage.core, stdlib only — first caller since D34
        ▼
  RunListResponse{items:[…], has_more}  ── field-by-field construction,
                                            never from_attributes        (D59)

  GET /api/v1/tests/history?node_id=…    ── identity is a query VALUE     (D54)
        │  node_id → test_case (unique index) → result (index) → run (PK) (D63)
        ▼
  Page[HistoryEntry]  ── run id, started_at, outcome, duration,
                         last_contact_at, VcsProjection | None

  GET /api/v1/openapi.yaml ── importlib.resources bytes, no parse         (D55)

  ┌─ the document is the boundary ───────────────────────────────────────┐
  │  tag: read   → read-only digest-pair harness calls it        (D53, D65)│
  │  tag: write  → excluded by name; session-ingestion's scope     (D53)   │
  │  every op    → drift check against app.routes                  (D66)   │
  └──────────────────────────────────────────────────────────────────────┘
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `packages/vantage/src/vantage/core/domain/projection.py` | **Create** | `VcsProjection`, `project_vcs`, `LIST_COMMIT_SUBJECT_CHARS` (D59, D60) |
| `packages/vantage/src/vantage/core/ports/storage.py` | Modify | four read methods; `Page`, `RunListEntry`, `RunDetail`, `HistoryEntry`, `MAX_PAGE_ITEMS` (D57, D58, D61) |
| `packages/vantage/src/vantage/core/domain/liveness.py` | **Unchanged** | gains its first caller (D62) |
| `packages/vantage/src/vantage/core/domain/execution.py` | **Unchanged** | `VcsContext` reused on the detail path (D59) |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Modify | four read queries; `substr`/`length` projection; `limit + 1`; total ordering |
| `packages/vantage/src/vantage/storage/memory.py` | Modify | the same four, second mechanism (D57) |
| `packages/vantage/src/vantage/storage/schema.sql` | **Unchanged** | no new index — deliberately (D63) |
| `packages/vantage/src/vantage/service/routes/read.py` | **Create** | the read router and the document route |
| `packages/vantage/src/vantage/service/schemas.py` | Modify | list/detail/history response models, explicit construction (D59) |
| `packages/vantage/src/vantage/service/app.py` | Modify | mount the router; `openapi_url=None, docs_url=None, redoc_url=None` (D66) |
| `packages/vantage/src/vantage/service/openapi/__init__.py` | **Create** | the `importlib.resources` anchor (D55) |
| `packages/vantage/src/vantage/service/openapi/v1.yaml` | **Create** | the hand-written contract, `read`/`write` tagged (D53, D55) |
| `packages/vantage/src/vantage/service/errors.py` | Modify | one rejection code for a missing/ambiguous/over-long identity (D54) |
| `packages/vantage/tests/vantage_port_contract.py` | Modify | read scenarios against both adapters, including a null-VCS run, a tie on `started_at`, and the 200-item clamp |
| `packages/vantage/tests/test_read_only_surface.py` | **Create** | the digest-pair harness and its binding table (D65) |
| `packages/vantage/tests/test_interface_document.py` | **Create** | drift, every-path-2xx, generated documents disabled, `importlib.resources` load (D55, D66) |
| `scripts/measure_history_latency.py` | **Create** | the manual benchmark (D64) |
| `packages/vantage/pyproject.toml` | Modify | `pyyaml` into the dev extra only (D55) |
| `docs/api/v1-ingestion.md` | Modify | prose kept, path/status enumeration removed (D56) |
| `docs/adr/0015-scope-the-read-only-guarantee-to-a-named-read-surface.md` | **Create** | the one decision past the filter (D67) |
| `docs/open-questions.md` | Modify | OQ-9 → Answered, bound to ADR-0015 |
| `packages/pytest-vantage/**` | **Untouched** | not opened by this change |

## Interfaces / Contracts

```python
# vantage/core/domain/projection.py — stdlib only
LIST_COMMIT_SUBJECT_CHARS = 120

@dataclass(frozen=True, slots=True)
class VcsProjection:
    commit: str | None
    branch: str | None
    commit_subject: str | None
    commit_subject_truncated: bool
    dirty: bool | None

def project_vcs(vcs: VcsContext | None) -> VcsProjection | None:
    """Reference implementation of the rule the SQLite adapter states in SQL."""

# vantage/core/ports/storage.py — stdlib only
MAX_PAGE_ITEMS = 200
MAX_IDENTITY_CHARS = 1024

@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    has_more: bool

@dataclass(frozen=True, slots=True)
class RunListEntry:
    execution: Execution                 # execution.vcs is None here — see below
    last_contact_at: datetime | None
    vcs: VcsProjection | None

@dataclass(frozen=True, slots=True)
class RunDetail:
    execution: Execution                 # execution.vcs is the full VcsContext
    last_contact_at: datetime | None

@dataclass(frozen=True, slots=True)
class HistoryEntry:
    run_id: str
    started_at: datetime
    finished_at: datetime | None
    last_contact_at: datetime | None
    outcome: str
    duration: float | None
    vcs: VcsProjection | None
```

`RunListEntry.execution.vcs` is `None` and the projection rides beside it, so
there is exactly one place a list entry's VCS data can be read from. A list
entry carrying both would be two answers to one question.

Wire shapes:

```json
GET /api/v1/runs
{"items": [{"id": "…", "started_at": "…", "finished_at": null,
            "exit_status": null, "interrupted": false,
            "presentation": "abandoned",
            "vcs": {"commit": "…", "branch": "main",
                    "commit_subject": "…", "commit_subject_truncated": false,
                    "dirty": true}}],
 "has_more": true}

GET /api/v1/tests/history?node_id=tests%2Ftest_a.py%3A%3Atest_x%5B1%5D
{"items": [{"run_id": "…", "started_at": "…", "outcome": "failed",
            "duration": 0.31, "presentation": "finished",
            "vcs": {…}}],
 "has_more": false}
```

No `vcs_root`, in any response, at any depth.

## Testing Strategy

Strict TDD, RED first. **New tests carry no `req` marker** — no new numeric
identifiers are minted (`CLAUDE.md`), and several `vcs-capture` contract tests
already ship unmarked. Each new test names the capability and scenario it
verifies in its docstring, which is what `grep` has to find.

| Layer | What | Approach |
| --- | --- | --- |
| Unit (core) | `project_vcs`: bounding, the disjunction flag, null passthrough, all-null → `None` | Pure function, no fixtures |
| Unit (core) | `derive_presentation` already covered; add only the read-path wiring | Existing suite unchanged |
| Contract (both adapters) | Ordering incl. a `started_at` tie; 200-item clamp; `has_more` at exactly 200 and 201; unknown identity → empty page; null-VCS run present in the list; the bounded subject and its flag; `last_contact_at` round-trip | `vantage_port_contract.py`, inherited by both `test_*_store.py` |
| Integration (service) | Every route; `422` shapes for identity and `limit`; `404` for an unknown run; the encoded-identity case (`/`, `::`, `[`, `]` in one node id) reaching the store intact | ASGI in-process against `InMemoryExecutionStore` |
| Integration (service) | `vcs_root` appears in no response body — asserted on the raw bytes, list **and** detail **and** history | Substring assertion on the serialised body |
| Demonstration | Abandoned / running / interrupted read back through the live path; no stored field invented | Old `last_contact_at` in the fixture; configured grace; no clock control (D62) |
| Test (document) | Drift both directions; every declared path answers 2xx; the three generated routes answer 404; the document loads via `importlib.resources` | `test_interface_document.py` (D66) |
| Test (read-only) | Digest pair + unchanged counts over every `read`-tagged path; every `read` path has a binding | `test_read_only_surface.py` (D65) |
| Inspection | The traceback / captured-output exclusion — recorded as unfailable until failure capture lands | Stated in the spec, not asserted as if it could fail |
| Analysis | p95 and max history latency; the 10 ms-profile overhead re-run | `scripts/measure_history_latency.py`, transcribed (D64) |

**The encoded-identity test is the one that proves D54 rather than assuming it.**
It sends a node id containing `/`, `::`, `[` and `]` and asserts the store
received the exact string. If the reasoning about ASGI path decoding were wrong
in either direction, this test — not a deployment — is where it surfaces.

## Threat Matrix

`references/threat-matrix.md` rows are **all N/A**: this change adds no
subprocess, no shell, no VCS or PR automation, and no content-based file
classification. It reads rows and serves bytes.

| Boundary | Applicability |
| --- | --- |
| Documentation-like paths | **N/A** — nothing is classified by content; the one file read from disk is package data at a fixed anchor, never executed |
| Git repository selection | **N/A** — no process is spawned; VCS data is read from columns another change wrote |
| Commit state | **N/A** — the working tree is never inspected here |
| Push state | **N/A** — nothing pushes; no remote or refspec is resolved |
| PR commands | **N/A** — no PR automation, no composed command |

Boundaries this change *does* add, recorded as notes rather than invented rows:

- **A client-chosen identity string reaching SQL.** Response: it is a bound
  parameter in every query, never interpolated — the same discipline
  `_resolve_test_case_ids` already documents. It is length-bounded at 1,024
  characters (D54) and, when a rejection names it, routed through `errors.py`'s
  existing `safe_segment` allow-list, because it is exactly the client-chosen
  text that allow-list exists for. RED tests: a quoting-shaped identity; an
  over-long identity; an identity echoed in a `422` body.
- **An unbounded response.** Response: the 200-item cap in the adapter (D61) and
  the 120-character subject bound in SQL (D60); the store never materialises the
  page it will not send. RED tests: 201 stored runs yield 200 items; a 64 KiB
  subject yields 120 characters.
- **A read path that writes.** Response: the digest pair (D65) over the paths the
  document itself declares readable (D53). RED test: temporarily tagging a
  writing endpoint `read` must make the harness fail — the check's own falsifier.

## Migration / Rollout

**No migration.** No schema statement changes, no `schema_version` bump,
ADR-0013's refusal gate is not engaged. Every slice is additive; rollback is a
branch revert plus, at the far end, deleting three `create_app` kwargs and one
`include_router` call.

The proposal forecast five slices against a 400-line budget and recorded that
four of them exceed it. **This design forecasts seven**, because the honest
answer to an over-budget slice is a smaller slice, and `session-lifecycle`'s
lesson is that an under-forecast slice splits at apply time anyway — late.

| # | Slice | Est. lines | Independently deliverable |
| --- | --- | --- | --- |
| 1 | `projection.py` + read types on the port + `project_vcs` unit tests (D57–D60) | ~230 | Yes — nothing calls them yet |
| 2 | Both adapters: `list_runs` + `get_run_detail` + contract scenarios (ordering, tie, clamp, null VCS) | ~380 | Yes — depends on 1 |
| 3 | Both adapters: `list_results` + `list_history` + contract scenarios | ~360 | Yes — depends on 1 |
| 4 | Run list + run detail routes, response models, pagination envelope, `derive_presentation` wiring, the liveness demonstration (D62) | ~390 | Yes — depends on 2 |
| 5 | Results route + history route + the identity parameter and its encoding tests (D54) | ~340 | Yes — depends on 3, 4 |
| 6 | The hand-written document, drift test, 2xx test, generated documents disabled, `v1-ingestion.md` trim (D53, D55, D56, D66) | ~390 | Yes — depends on 4, 5 |
| 7 | Read-only harness, latency benchmark, committed measurements, ADR-0015, OQ-9 (D63–D65, D67) | ~400 | Yes — depends on 6 |

~2,490 lines across seven slices; **no slice exceeds the 400-line review
budget.** `chain_strategy: feature-branch-chain`. Rollback in reverse chain
order.

```
Decision needed before apply: Yes
Chained PRs recommended: Yes
400-line budget risk: High
```

Slice 6 must land before slice 7: the read-only harness derives its call list
from the document (D53), so the proof is not constructible until the document
exists. That ordering is a dependency, not a preference.

## Open Questions

None blocks `sdd-tasks`.

- [ ] Offset pagination shifts its window when a run arrives mid-pagination
      (D61). A cursor keyed on `(started_at, id)` — the total order is already
      there — is the fix when a client needs one. No scenario asks today.
- [ ] `VcsProjection` diverges from the proposal's addendum decision A, which
      assumed `VcsContext` reuse throughout (D59). Spec-neutral: no scenario
      names a type. Recorded so the proposal's record and this one do not
      silently disagree.
- [ ] The lean/full split has no single-**result** endpoint, because `Result`
      carries no excluded field today. When failure capture lands and adds
      traceback, that change adds `GET /api/v1/runs/{id}/results/{node_id}` and
      the split stops being vacuous. Named here so it is inherited, not
      rediscovered.
- [ ] A node id longer than 1,024 characters is unreachable through the history
      endpoint (D54). A `POST`-with-a-body variant would lift the bound and cost
      a second shape for one resource. Held until something needs it.
