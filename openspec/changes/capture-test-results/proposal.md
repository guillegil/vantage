# Proposal: Capture test results

**Change:** `capture-test-results` · **Phase:** 1 · **Status:** proposed
**Source of truth:** this repository. `docs/legacy/notion-2026-08-18/` is frozen and
read for intent only.

## Intent

Today a recorded session is a run entry and nothing else: `result` and `test_case`
are created but empty. A user can ask *did I run the suite* and can ask nothing at
all about a test. **"When did this test start failing"** — the question Vantage
exists for — has no data behind it.

This change fills those two tables. After it, one recorded session leaves one row
per test, carrying an outcome that survives every execution phase, per-phase
durations, and an identity decomposed into columns you can filter on; and the
catalogue remembers a test after it is deleted from the codebase.

It is still not user-visible — there is no read API — but it is the point at which
the stored data becomes worth reading.

## Requirements in scope

| Ref | Name | Priority | Verification | Component |
| --- | --- | --- | --- | --- |
| RQ-4 | Outcome across phases | Must | Test | both |
| RQ-9 | Decomposed identity | Must | Test | both |
| RQ-12 | Distributed execution | Must | Test | both |
| RQ-5 | Per-phase duration | Should | Test | both |
| RQ-13 | Catalogue retention | Should | Test | server |
| RQ-3 | Single write transaction | Must | Test | server — **result-count criteria only** |
| RQ-38 | Concurrent sessions | Should | Test | server — **criteria 2 and 3 only** |

RQ-3 and RQ-38 were deliberately half-verified in `milestone-1-write-one-row`
because no results existed to count. They become provable here and are settled
here, not carried again.

## Capabilities

> Contract with `sdd-spec`. Names are binding; do not invent another decomposition.

### New capabilities

| Capability | Requirements | Component |
| --- | --- | --- |
| `result-capture` | RQ-4, RQ-5, RQ-9 | both |
| `distributed-execution` | RQ-12 | both |
| `test-catalogue` | RQ-13 | server |

### Modified capabilities

| Capability | What changes |
| --- | --- |
| `run-recording` | RQ-3 gains its 500-result criteria; RQ-38 gains criteria 2 and 3. Both currently carry an explicit "this milestone writes no results" carve-out that this change removes. |
| `session-ingestion` | The envelope grows a `results` section. RQ-41's idempotency and RQ-42's rejection behaviour now have to hold for it too. |

## Scope

### In scope

- **Plugin** (`pytest-vantage`): per-phase report capture, identity decomposition,
  the xdist worker filter, and a `results` array added to the existing single
  session report.
- **Server** (`vantage`): a result aggregate and an extended storage port in
  `vantage.core`; result insert and catalogue upsert in both `vantage.storage`
  adapters; a `ResultReport` model and persistence in `vantage.service`.
- **Verification**: RQ-3's and RQ-38's remaining criteria, exercised against real
  result counts.

### Out of scope

| Deferred | Why |
| --- | --- |
| RQ-6 parameter values, RQ-7 markers | Additive per-result detail; columns already exist |
| RQ-8 failure location, RQ-32 traceback, RQ-22 truncation | The failure-detail slice, one coherent piece |
| RQ-10/11/23/35/39 execution context | Run-level, not result-level |
| RQ-44 abandoned run | Needs a **session-start** write; changes the ingestion contract. Decide before, not during |
| RQ-25 overhead measurement | Analysis, once there is a payload worth measuring |
| The read API and the interface | Later phases |
| Any schema change | See below — there must not be one |

## Approach

**No schema change.** `result` and `test_case` already carry every column this
needs, including `setup_/call_/teardown_outcome`, the three phase durations, the
four decomposed identity columns, `UNIQUE (run_id, node_id, attempt)` and five
relevant indexes. RQ-29 and ADR-5 say the schema was built complete at first use
and no Phase 1 release alters an existing database. **A design that needs a column
is a design that is wrong**, and should come back as a question rather than a
migration.

**No API version.** `SessionReport` is `extra="ignore"` precisely so a newer plugin
can add a sibling section; its docstring names `results` as the next one. Add
`results` beside `run`. Do not touch `RunReport`, which is `extra="forbid"`, and do
not propose `/api/v2`.

**ADR-9 holds.** The plugin still opens no database and still sends exactly one
request per session (RQ-25 criterion 2). The results travel inside the report it
already sends from `pytest_sessionfinish`.

**Derive the outcome from all three phases, never from the call report.** A setup
failure emits no call phase; a teardown error arrives after the call has already
reported success. Both per-phase outcomes and the derived overall outcome are
stored, so the derivation is auditable rather than lossy.

**A phase that never ran is NULL.** Zero means "ran instantly". Same rule for
`class_name` and `param_id`: absent is NULL, never `""`.

## Open design questions

Named here, resolved in `sdd-design` — not glossed over.

1. **What `test_case.stable_id` holds today.** It is `NOT NULL UNIQUE` and the
   schema comment says it "supersedes `node_id` in Phase 2". Whatever fills it
   *is* catalogue identity now, so it decides RQ-13 criterion 2 (same identifier
   returning reuses the same entry). Candidates: mirror `node_id`; hash the
   decomposed identity; synthesise a key. The constraint is that Phase 2's
   supersession must not become a migration under live data.
2. **Where deduplication is enforced, and how many layers.** Four loci exist: the
   plugin-side worker-input filter (which the requirement note names), a
   server-side check, the `UNIQUE (run_id, node_id, attempt)` constraint, and
   read-time dedup — **explicitly rejected**, because it hides a write-path bug
   behind a query. Which is primary, which are backstops, what `attempt` holds in
   this change, and whether a constraint violation is an error or a silent no-op
   (RQ-41 idempotency means a whole replayed report must not fail).
3. **How the catalogue upsert behaves under RQ-38.** Two concurrent sessions
   observing the same new test race for one `test_case` row. `SELECT`-then-`INSERT`
   was already rejected for runs in `sqlite_store.py`. Open: the upsert shape,
   whether `last_seen_at` must advance monotonically so a late-arriving older
   session cannot roll it back, and how "only tests actually observed are touched"
   is expressed so RQ-13 criterion 1 holds.
4. **Whether a 500-result payload needs a size consideration.** RQ-25 criterion 2
   forbids a request per test; RQ-3 forbids a partial write. Chunking would
   therefore need a transaction spanning requests. Open: whether one request stays
   inside the overhead budget, whether the endpoint needs a body-size limit, and
   what the memory cost is on both sides.
5. **Port shape and aggregate naming.** One `record_session(...)` call (RQ-3's
   single transaction argues for it) versus a separate `record_results`. OQ-4 says
   the port is a migration seam, not a product surface, so it earns the minimum
   abstraction. And no domain class may start with `Test` — `TestResult` and
   `TestCase` are both forbidden by collection.

## Affected areas

| Area | Impact | Description |
| --- | --- | --- |
| `packages/vantage/src/vantage/core/domain/` | New | Result aggregate and outcome vocabulary, stdlib dataclasses |
| `packages/vantage/src/vantage/core/ports/storage.py` | Modified | Port extended to persist results and catalogue entries |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Modified | Result insert, catalogue upsert, inside one transaction |
| `packages/vantage/src/vantage/storage/memory.py` | Modified | Second implementation; keeps the port honest (RQ-30) |
| `packages/vantage/src/vantage/service/schemas.py` | Modified | `ResultReport`; `results` added to `SessionReport` |
| `packages/vantage/src/vantage/service/routes/runs.py` | Modified | Persist results with the run |
| `packages/pytest-vantage/src/pytest_vantage/` | New + Modified | Phase capture and identity decomposition; recorder assembles `results` |
| `packages/vantage/src/vantage/storage/schema.sql` | **Unchanged** | Deliberately. A diff here means the design went wrong |
| `openspec/specs/run-recording/spec.md` | Modified | RQ-3 and RQ-38 carve-outs removed |

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Over-aggressive dedup halves the count without xdist | **High** | RQ-12 criterion 2 is the control case and is non-optional: same suite, no xdist, same count |
| Outcome derived from the call phase alone — silent loss | High | RQ-4 criteria 1 and 5 are exactly this; per-phase outcomes stored, not only the derived one |
| A design proposes a schema change | Medium | Stated as a hard constraint; a column need returns as a question, never a migration |
| Zero written where NULL is meant | Medium | RQ-5 criterion 2 and RQ-9 criteria 2–3 assert the distinction directly |
| Catalogue upsert races under concurrency | Medium | RQ-38 criterion 3 raises the count to ten sessions; open question 3 must be answered first |
| The payload grows the plugin's memory footprint | Low | Deferred to the RQ-25 measurement; flagged, not guessed |

## Rollback plan

**This change is not schema-affecting, and that is what makes rollback cheap.**

- Revert the PR chain with `git revert`. There is no migration to undo and no
  `schema.sql` diff.
- Existing databases keep working: `result` and `test_case` simply go back to
  being empty, exactly as they are today.
- Rows already written by a newer server remain. They are additive and nothing
  reads them yet — there is no read API — so they are inert, not corrupt.
- **A reverted server against an un-reverted plugin is the supported skew case,
  not an outage.** `SessionReport` is `extra="ignore"`: the old server drops the
  `results` section and still records the run. This is the mechanism ADR-4 exists
  for, and it means plugin and server can be rolled back independently.
- If only the plugin is reverted, the server receives a report with no `results`
  section and records the run alone. `results` must therefore be optional on the
  envelope, not required.

## Delivery and line forecast

**Review budget: 400 authored lines per PR. Forecast: ~950–1,200 lines. It exceeds
the budget, so it is chained.** Delivery strategy is `auto-chain`; slices below are
the proposed boundaries, and `sdd-tasks` produces the authoritative number.

| # | Slice | Requirements provable | Forecast |
| --- | --- | --- | --- |
| 1 | Core domain, extended port, both storage adapters, catalogue upsert | RQ-13, RQ-38 (2, 3) at storage level, RQ-30 contract suite still green | ~350 |
| 2 | Service: `results` on the envelope, persistence with the run in one transaction | RQ-3, RQ-38 (2, 3) end to end, RQ-41/RQ-42 extended | ~300 |
| 3 | Plugin: phase capture, identity decomposition, xdist filter, report assembly | RQ-4, RQ-5, RQ-9, RQ-12 | ~350 |
| 4 | End-to-end verification and traceability sweep | Whatever slices 1–3 leave | ~150 |

Feature-branch chain: PR 1 targets the change branch, each later PR targets its
predecessor. Server first, plugin third — the same order that worked for the run
entry, and it means the plugin never reports into a stub.

## Dependencies

- None external. The schema, the envelope extension point and both storage
  adapters already exist.
- Open design questions 1–3 must be answered in `sdd-design` before `sdd-apply`
  starts slice 1; they decide storage behaviour, not just naming.

## Success criteria

Each maps to a named acceptance criterion of a named requirement.

- [ ] **RQ-4.1** a fixture raising before the body yields outcome `error`; **4.2** `skip` yields `skipped`; **4.3** failing `xfail` yields `xfailed`; **4.4** passing `xfail` yields `xpassed`; **4.5** a passing test with a raising teardown is **not** `passed`.
- [ ] **RQ-5.1** an 8-second fixture and a 0.1-second body record setup ≥ 8 s and call < 1 s; **5.2** a setup failure records a **null** call duration, not zero.
- [ ] **RQ-9.1** filtering on file path alone returns every test defined in that file; **9.2** a module-level test records a null class name; **9.3** an unparametrised test records a null parameter identifier.
- [ ] **RQ-12.1** six tests under `-n 2` record six results; **12.2** the same six without xdist also record six; **12.3** one run entry either way.
- [ ] **RQ-13.1** a deleted test keeps its catalogue entry with its last-observed timestamp unchanged; **13.2** the same identifier returning reuses the **same** entry and advances the timestamp.
- [ ] **RQ-3** a 500-test session is present in full or not at all — never as a prefix — under both a mid-write kill and a truncated report; the normal case holds all 500.
- [ ] **RQ-38.2** two concurrent 200-test sessions yield 400 results; **38.3** ten simultaneous sessions all succeed with no error response.
- [ ] `schema.sql` is byte-identical to its state before this change.
- [ ] `grep -r "RQ-4"` reaches the test that proves it, and likewise for every requirement above.

## Proposal question round

Execution mode is `auto`, so these were not asked. Each records the assumption
taken; correct any of them before `sdd-design`.

1. **Is an outcome that pytest itself has no word for possible?** *Assumed no* —
   the six values in the `result.outcome` CHECK are the whole vocabulary, and a
   teardown error after a passing call maps to `error`.
2. **Does a rerun (e.g. `pytest-rerunfailures`) count as one result or several?**
   *Assumed out of scope* — `attempt` exists for it, and this change writes a
   single fixed value. If reruns matter now, say so, because it changes question 2.
3. **Is a test's catalogue identity its node id, or something that survives a
   rename?** *Assumed node id for this change*, with reconciliation left to Phase
   3. This is the same decision as open question 1 and the more consequential half.
4. **Is losing a result worse than rejecting a whole report?** *Assumed yes* —
   RQ-3 says in full or not at all, so a malformed `results` section rejects the
   entire report rather than storing the parseable subset.

---

## Decisions taken after the proposal round (2026-08-18)

These were put to the user and answered. They are binding on `sdd-design` and
`sdd-spec`; do not reopen them.

### Open design question 1 is RESOLVED — `stable_id` holds the node id verbatim

`test_case.stable_id` stores the full pytest node id, unhashed and
untransformed, e.g.
`packages/vantage/tests/test_memory_store.py::TestInMemoryExecutionStore::test_first_write_creates_a_row`.

**Rationale, in the user's decision:** the node id says exactly what it knows —
where the test lived when it was last seen — and nothing more. A rename or a
move therefore splits a test's history, and that split stays **visible** rather
than being disguised. Visibility is the point: Phase 3 reconciliation needs to
be able to find what it must reconcile.

Both rejected alternatives are recorded so they are not re-proposed:

- **Hashing the four decomposed columns** carries identical information and
  splits identically on rename, while hiding the recipe behind a hash. Changing
  that recipe later re-keys every row, which is the migration ADR-5 and RQ-29
  exist to prevent — merely less visible in a diff.
- **Identity from function name and parameters, ignoring the path** does survive
  a file move, but makes two identically named tests in different files collide
  into one entry. A merged history asserts something false; a split history is
  only incomplete. Incomplete beats wrong.

Catalogue identity is therefore the node id, and cross-rename reconciliation
stays in Phase 3, out of scope here.

### Proposal question round — resolutions

| # | Question | Resolution |
| --- | --- | --- |
| 1 | An outcome pytest has no word for? | **Assumption accepted.** The six values in the `result.outcome` CHECK are the whole vocabulary; a teardown error after a passing call maps to `error`. |
| 2 | Does a rerun count as one result or several? | **Confirmed out of scope.** Reruns arrive in a later change. This change writes a single fixed `attempt` value. The column already exists, so supporting them later is not a migration. |
| 3 | Catalogue identity — node id, or rename-surviving? | **Node id**, per the resolution above. |
| 4 | Losing a result vs rejecting a whole report? | **Assumption accepted.** RQ-3 says in full or not at all, so a malformed `results` section rejects the entire report rather than storing the parseable subset. |

### Delivery: chain strategy confirmed

`feature-branch-chain`. PR 1 targets the change branch; each later PR targets its
immediate predecessor; only the change branch merges to main. This keeps every
diff scoped to its own slice.

### New constraint found while explaining node ids — for `sdd-design`

The project's own suite contains a parametrised case whose parameter id is the
**empty string**:

```
packages/vantage/tests/test_execution.py::test_identity_rejects_anything_but_32_lowercase_hex_characters[]
```

RQ-9 criterion 3 requires an **unparametrised** test to store `param_id` as
NULL. This test *is* parametrised and its id is `""`. NULL and `""` must
therefore stay distinguishable, or a query for "tests that take no parameters"
silently returns this one too — the exact absent-versus-empty confusion RQ-9
criteria 2 and 3 exist to prevent, appearing in a column the requirement's own
examples do not cover.

The same distinction already applies to `class_name`, where RQ-9 criterion 2
states it directly. `param_id` needs it stated too.
