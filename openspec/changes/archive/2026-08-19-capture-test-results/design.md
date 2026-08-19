# Design: Capture test results

**Change:** `capture-test-results` · **Phase:** 1 · **Milestone:** 2
**Authoritative inputs:** `proposal.md` including its binding *Decisions taken after the
proposal round*, ADR-3, ADR-4, ADR-5, ADR-6, ADR-9, `docs/open-questions.md` OQ-4,
`openspec/config.yaml`, `CLAUDE.md`, and the Milestone 1 design D1–D14
(`openspec/changes/archive/2026-08-16-milestone-1-write-one-row/design.md`).
**Read for intent only, authoritative of nothing:** `docs/legacy/notion-2026-08-18/`.

> **Decision numbering continues from Milestone 1.** That document ended at D14 and its
> numbers are cited from source comments (`sqlite_store.py` D5/D8, `errors.py` D12). This
> design starts at **D15** so a `design.md D…` reference stays unique across the project.
>
> **This document exceeds the 800-word budget on purpose.** The proposal makes open design
> questions 2–5 a *dependency of slice 1*, not commentary: each is answered here with its
> rejected alternatives, because a rejected alternative that is not written down gets
> re-proposed. That has already happened once in this project.

## Technical Approach

Nothing structural moves. The plugin grows one hook, the envelope grows one optional
sibling section, the port collapses its two write concerns into one call, and both adapters
implement it. `schema.sql` is untouched — `result` and `test_case` already carry every
column, and `test_schema_manifest.py::test_fresh_database_matches_the_recorded_ground_truth`
is the existing guard that fails if a diff appears (RQ-29, ADR-5).

```
pytest (controller only, D2)
  pytest_runtest_logreport ×3 per test ──► Recorder._results  dict[node_id, pending]
                                                   │  resolved at the teardown report
  pytest_sessionfinish ──► one POST /api/v1/runs ───┘   {"run": {...}, "results": [...]}
        │
        ▼
  vantage.service   SessionReport(extra="ignore") · ResultReport(extra="allow") · Pydantic v2
        │           malformed results ⇒ 422, whole report, never a subset
        ▼
  vantage.core      Execution · Result · CaseIdentity · CatalogueEntry — stdlib only (RQ-26)
        │           ExecutionStore.record_session(execution, results=…, received_at=…)
        ▼
  vantage.storage   ONE BEGIN IMMEDIATE: run ▸ test_case upsert ▸ id resolve ▸ result insert
        ▼
  SQLite
```

---

## Architecture Decisions

### D15 — `results` is an optional sibling section; a *result* is an open record, a *run* is a closed one

`results` sits beside `run` on the envelope (D1's extension point, which `schemas.py`'s
docstring already names). It is **optional**: a reverted plugin against an un-reverted
server sends no `results`, and that report must still record the run (proposal rollback
plan). `None` (section absent) and `[]` (session collected nothing) are both legal and both
write zero result rows. No `/api/v2`. `RunReport` is not touched.

`ResultReport`'s `extra=` cannot simply copy `RunReport`'s.

| Option | Trade-off | Verdict |
| --- | --- | --- |
| `extra="allow"`, unknown keys dropped before the core and their **names** returned in `Acknowledgement.ignored` | a newer plugin's enriched results (RQ-6 parameters, RQ-7 markers, RQ-8 failure detail) still record on an older server, and the drop is visible rather than silent | **Chosen** |
| `extra="forbid"` (copy `RunReport`) | consistent-looking, but `run` can be closed *only because* future run-level data arrives as sibling **sections** (`environment`, `vcs`). Result-level data has no such escape: it belongs inside the result object. Forbidding here imports D1's strictness without the escape hatch that made it safe, and makes every future plugin release break every older server | Rejected |
| `extra="ignore"` | same tolerance, but the drop is invisible, which D1 explicitly declines to pay for | Rejected |

RQ-42 is not weakened: **every known field is required and nullable, never defaulted** (the
`RunReport` rule). A typo'd `duation` leaves `duration` missing → 422; the typo *also*
surfaces by name in `ignored`. `ignored` carries deduplicated **key names**
(`["results[].duation"]`), never per-index paths, so 500 results cannot inflate it; the
existing `errors.py::_safe_segment` allow-list already covers the `loc` segments
(`results`, an index, a field name) and no node id value is ever echoed.

### D16 — Capture through `pytest_runtest_logreport`, keyed by node id, emitted on resolution

| Option | Trade-off | Verdict |
| --- | --- | --- |
| `pytest_runtest_logreport` on the `Recorder` | the one hook xdist forwards **to the controller**, which is the only process this plugin is registered in (D2) | **Chosen** |
| `pytest_runtest_makereport` hookwrapper | same data, but it runs in the process that *executed* the test — under xdist that is the worker, which is exactly where the recorder must not be | Rejected |
| `pytest_runtest_protocol` / item-level hooks | would have to re-derive phase outcomes pytest has already computed | Rejected |

The recorder accumulates into `self._results: dict[str, _Pending]` keyed by `report.nodeid`.
A `dict` gives insertion order for free, so the array is in execution order, and it makes a
duplicated report an overwrite rather than a second row (D20 layer 1).

**Resolution, not attendance.** An entry is emitted only once its **teardown** report has
been seen. pytest runs teardown even after a setup failure, so every test that pytest itself
resolved is emitted. A test interrupted mid-call produces no call and no teardown report
(`CallInfo.from_call` re-raises `KeyboardInterrupt` before a report exists) and is **dropped**
rather than invented — the vocabulary has no word for "still running", and `run.interrupted`
already explains the absence. RQ-44 (abandoned runs) is out of scope and untouched by this.

`started_at`/`finished_at` come from `getattr(report, "start"/"stop", None)` (epoch floats on
pytest ≥ 8) converted with `datetime.fromtimestamp(..., timezone.utc)` — never
`datetime.UTC`, which is 3.11+. `worker_id` is read defensively —
`getattr(report, "worker_id", None)` then `getattr(getattr(report, "node", None), "gateway",
None).id` — because **`pytest-vantage` must not import xdist** (RQ-24); unknown is NULL.

### D17 — Outcome derivation: precedence, not a lookup of `call`

The overall outcome is derived from all three phase reports, and **all three per-phase
outcomes are stored alongside it** so the derivation is auditable rather than trusted.
`wasxfail` is an attribute pytest sets on the report and xdist round-trips it, so it is
readable on the controller.

| # | Condition, evaluated in this order | `outcome` | Criterion |
| --- | --- | --- | --- |
| 1 | setup failed | `error` | RQ-4.1 |
| 2 | setup skipped | `skipped` | RQ-4.2 (`@pytest.mark.skip` raises in setup) |
| 3 | call skipped **and** `wasxfail` present | `xfailed` | RQ-4.3 |
| 4 | call skipped | `skipped` | — |
| 5 | call failed **and** `wasxfail` present | `xfailed` | RQ-4.3 (`raises=` mismatch path) |
| 6 | call failed | `failed` | — |
| 7 | call passed **and** `wasxfail` present | `xpassed` | RQ-4.4 |
| 8 | call passed **and** teardown failed | `error` | **RQ-4.5** |
| 9 | call passed | `passed` | — |

Two consequences worth stating because they are the parts an implementation gets wrong:

- **A teardown failure downgrades only a `passed` result.** A `failed`, `xfailed`, `xpassed`
  or `skipped` result keeps its own word and the teardown failure stays visible in
  `teardown_outcome`. Letting teardown dominate unconditionally would mask a real assertion
  failure behind a fixture's error — the more informative verdict must win.
- **A strict `xfail` that passes is `failed`, not `xpassed`.** pytest itself sets
  `outcome="failed"` with no `wasxfail` for `[XPASS(strict)]`; RQ-4.4 describes the default
  non-strict case. Rule 7 therefore cannot fire, and the suite's own verdict is preserved.

**A phase that never ran is NULL, never zero** (RQ-5.2) — for `*_outcome` and `*_duration`
alike. `duration` is the sum of the phase durations that exist, or NULL if none do. The
forbidden idiom is `x or None`: it turns a genuine `0.0` into NULL. Use
`x if x is not None else None`.

### D18 — Identity decomposition: the brackets decide, never their content (RQ-9)

`node_id` is `path::Class::…::function[param]`, always `/`-separated even on Windows, and it
is stored **verbatim** in `result.node_id` and `test_case.stable_id`/`node_id` (binding
decision 1 — hashing and path-free identity are both rejected and not to be re-proposed).
`stable_id` is **not on the wire**: the server assigns it from `node_id`, so the two columns
cannot drift apart through a plugin/server disagreement.

Decomposition:

1. Split on `"::"`. First segment → `file_path`. Middle segments joined with `"::"` →
   `class_name`, or **`None` when there are none** (RQ-9.2: module-level ⇒ NULL, not `""`).
2. In the last segment: if it ends with `"]"` **and** contains `"["`, then
   `function_name = seg[:seg.index("[")]` and `param_id = seg[seg.index("[") + 1 : -1]`.
   Otherwise `function_name = seg` and `param_id = None`.

First `[` and last `]` — not `partition`/`rpartition` symmetry — so a parameter id that
itself contains brackets (`test_x[[0]]`) survives intact.

**The empty-parameter case, which RQ-9's own examples do not cover.** This project's suite
contains
`packages/vantage/tests/test_execution.py::test_identity_rejects_anything_but_32_lowercase_hex_characters[]`.
It **is** parametrised and its id is `""`. Rule 2 stores `param_id = ""`, not NULL, because
the *brackets* are the evidence of parametrisation and the *content* is not. RQ-9.3 asks
that an **unparametrised** test store NULL, and that still holds — an unparametrised test
has no brackets at all. The query "tests taking no parameters" is `WHERE param_id IS NULL`
and correctly excludes this one. This is RQ-9.2's absent-versus-empty distinction, stated for
`param_id` as the requirement states it for `class_name`.

`""` must survive four hops: JSON (`"" != null`), Pydantic (`str | None`, **never**
`min_length=1`, never a validator that coerces falsy to `None`), the core dataclass, and
SQLite (`''` is distinct from `NULL` and `IS NULL` excludes it). One RED test per hop is
cheaper than discovering it in a query six months later.

### D19 — Deduplication: one primary, three backstops, read-time rejected (RQ-12, RQ-41)

| Layer | Mechanism | Role | If it fires |
| --- | --- | --- | --- |
| 1 | Recorder registered on the controller only (`hasattr(config, "workerinput")`, D2 — **already shipped**), and per-node-id `dict` accumulation | **Primary.** Worker reports never leave the worker; each test appears once in the array by construction | not observable |
| 2 | `ResultReport` list validator: duplicate `node_id` inside one report | Backstop, **loud** — 422, whole report rejected (RQ-3: in full or not at all) | a plugin defect surfaces immediately |
| 3 | `INSERT INTO result … ON CONFLICT(run_id, node_id, attempt) DO NOTHING` | Backstop, **silent** — makes a replayed whole report a no-op | RQ-41: replay returns 200/`duplicate`, never an error |
| 4 | `UNIQUE (run_id, node_id, attempt)` in `schema.sql` | The constraint layer 3 names; last line of defence | — |
| ✗ | Dedup at read time | **Rejected** — the requirement note is explicit: it hides a write-path bug behind a query and makes the correct count a property of the query rather than of the data | — |

**A constraint violation is a silent no-op, and that does not hide a bug**, because the only
way a *fresh* run could produce one is a duplicate node id inside one report, and layer 2
already rejected that loudly one layer earlier. What remains reachable at layer 3 is
exclusively a replay of an already-stored run — which RQ-41 requires to succeed, not fail.
A pleasant side effect: a report replayed to a server that recorded the run before this
change (results empty) backfills its results instead of erroring.

`attempt` is not on the wire and not in the INSERT column list; the schema's `DEFAULT 0`
supplies it. Reruns are out of scope (binding decision 2) and the column already exists, so
adding them later is not a migration.

**RQ-12 criterion 2 is not optional.** The same six tests without xdist must still yield six
results; a filter that is too aggressive halves the count and only the paired invocation
catches it.

### D20 — Catalogue upsert: one statement, monotonic, observed rows only (RQ-13, RQ-38)

`SELECT`-then-`INSERT` was already rejected for run rows (`sqlite_store.py` module docstring,
D3/D5) and is rejected again here for the same reason: two concurrent sessions observing the
same new test both pass the `SELECT` and both attempt the `INSERT`.

```sql
INSERT INTO test_case (stable_id, node_id, file_path, class_name, function_name,
                       param_id, first_seen_at, last_seen_at, last_seen_run_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(node_id) DO UPDATE SET
    file_path        = excluded.file_path,
    class_name       = excluded.class_name,
    function_name    = excluded.function_name,
    param_id         = excluded.param_id,
    last_seen_run_id = CASE WHEN excluded.last_seen_at > test_case.last_seen_at
                            THEN excluded.last_seen_run_id ELSE test_case.last_seen_run_id END,
    last_seen_at     = MAX(test_case.last_seen_at, excluded.last_seen_at)
```

- **Conflict target `node_id`** — the schema comment names it the Phase 1 identity key.
  `stable_id` carries the identical string in Phase 1, so its own UNIQUE index cannot be
  violated by the row this statement updates. A Phase 2 divergence between the two columns
  must revisit this target; that is the seam, and it is named here so it is not discovered.
- **`last_seen_at` advances monotonically** via `MAX`. Timestamps are fixed-width ISO-8601
  UTC text (D13), so lexicographic order *is* chronological order and `MAX` over TEXT is
  correct. A late-arriving older session therefore cannot roll the timestamp back, and
  `last_seen_run_id` moves only in the same step, never independently.
- **The clock is `run.started_at`, not `received_at`.** "Last observed" is a property of when
  the session ran, not of when its report landed (D1's two clocks). Cost: a client whose
  clock is far in the future pins the column. Blast radius is one soft-metadata column, no
  key and no result field, so it is a design note, not an ADR (see D23).
- **RQ-13.1 holds by construction**: only node ids present in *this* report appear in the
  parameter list, so a deleted test's row is never touched and its `last_seen_at` is
  literally unchanged, not merely re-written to the same value.
- **RQ-13.2 holds** because a returning test conflicts on the same `node_id` and reuses the
  same surrogate `id`; `first_seen_at` is never in the `DO UPDATE` set, so the gap stays
  visible in the history.

**Resolving `test_case.id` for the FK.** `RETURNING` is deliberately not used: it needs
SQLite ≥ 3.35, which the 3.10 floor does not guarantee, and a `DO UPDATE … WHERE` that
declines returns *no row at all* — a trap that reads as a missing catalogue entry. Instead,
after the `executemany` upsert, one `SELECT id, node_id FROM test_case WHERE node_id IN (…)`
runs **inside the same transaction**, batched at ≤ 500 placeholders so
`SQLITE_MAX_VARIABLE_NUMBER` (999 on older builds) is never approached. This is not the
rejected check-then-act shape: it decides nothing, it resolves surrogate keys after the write
that guaranteed they exist.

### D21 — The port collapses to `record_session`; three reads, each earned (OQ-4, RQ-3, RQ-30)

OQ-4 settled that the port is a **migration seam, not a product surface**, so it gets the
minimum that works and no general-purpose repository API.

| Option | Trade-off | Verdict |
| --- | --- | --- |
| Rename to `record_session(execution, *, results, received_at) -> bool` | one call, therefore one transaction, therefore RQ-3 by construction; the RQ-3 verification counts commits through a wrapper and gets 1 without the port exposing a transaction | **Chosen** |
| Keep `record_execution` + add `record_results` | two calls need a transaction spanning them, so the port grows `begin`/`commit` — exactly the general-purpose API OQ-4 rules out | Rejected |
| Keep the name, add `results=()` | smallest diff, but the method's name would no longer describe what it writes | Rejected |

`results` is **keyword-only and required**: a caller with no results passes `results=()`
explicitly, matching `RunReport`'s "even the client's own nulls are sent explicitly". The
`bool` return is unchanged, so the route's 201/200 branch is untouched.

Three read methods are added, and each exists because a named acceptance criterion cannot be
proved through the port without it — not because a repository "should" have them:
`count_results()` (RQ-12.1/2, RQ-38.2), `get_results(execution_id)` (RQ-4, RQ-5, RQ-9 field
assertions), `get_catalogue_entry(node_id)` (RQ-13.1/2). Reaching into SQL from the tests
instead would leave `InMemoryExecutionStore` unable to run the shared contract suite, which
is the one thing keeping the port honest (RQ-30, D10).

Domain names, stdlib frozen slotted dataclasses, **no name beginning with `Test`**:
`Result`, `CaseIdentity`, `CatalogueEntry`. The outcome vocabulary is a module-level
`frozenset` validated in `__post_init__`, mirroring `Identity`'s regex — **not an `Enum`**:
`class X(str, Enum)` changes `__format__` between 3.10 and 3.11, and this project runs both.

### D22 — One transaction, four statements, one fixed order (RQ-3)

Inside the existing `self._lock` + `BEGIN IMMEDIATE` (D8, unchanged):

1. `INSERT INTO run … ON CONFLICT(id) DO NOTHING` → `created = cursor.rowcount == 1`
2. `executemany` the catalogue upsert (D20)
3. batched `SELECT` resolving `node_id → test_case.id` (D20)
4. `executemany` the result insert with `ON CONFLICT … DO NOTHING` (D19)

then one `COMMIT`; any `BaseException` `ROLLBACK`s and re-raises, as today. The order is
**required, not tidy**: `PRAGMA foreign_keys=ON` is set on every connection, so
`result.run_id`, `test_case.last_seen_run_id` and `result.test_case_id` each need their
referent to exist first. RQ-3.1 (SIGKILL mid-write) is SQLite's own atomicity over that one
transaction; RQ-3.2 (truncated in transit) never reaches the store at all — `_read_bounded_body`
raises `IncompleteBodyError` before the first byte is parsed.

`InMemoryExecutionStore` mirrors the same semantics with dicts: catalogue keyed by node id
with the `MAX` guard, results keyed by `(run_id, node_id, attempt)` with first-write-wins.
It is a second mechanism, not a stub — that is the point of RQ-30.

### D23 — Payload size: no new limit, and the number is measured rather than guessed

The endpoint **already** caps bodies at `MAX_REPORT_BYTES = 1 MiB`, enforced by streaming
before buffering (`_read_bounded_body`). No new limit, no chunking: RQ-25.2 forbids a request
per test and RQ-3 forbids a partial write, so chunking would need a transaction spanning
requests — strictly worse than one bounded request.

What the cap *permits* is arithmetic, not a guess: 1 MiB ÷ 500 results ≈ **2,097 bytes per
result**. The section sends ~16 keys of which the only unbounded one is `node_id` (this
project's longest are ≈ 100 characters), so the expected per-result size is a few hundred
bytes and 500 results should land near a fifth of the cap.

**"Should" is an estimate, and this design does not ship estimates as facts.** The
measurement: `test_five_hundred_results_fit_within_the_body_cap`
(`@pytest.mark.req("RQ-3")`) builds a 500-result report through the real assembler and
asserts `len(json.dumps(report).encode("utf-8")) < MAX_REPORT_BYTES`, reporting the actual
byte count. Server-side peak memory is **unknown until measured**; the measurement is
`tracemalloc` around one 500-result request in the service test, recorded as a number and not
asserted against an invented threshold.

Extrapolation, named as a risk rather than solved here: at a few hundred bytes per result the
cap is reached somewhere in the low thousands of tests, and a suite past that would be
rejected `413` — permitted by RQ-3 ("or not at all") but a poor outcome. The lever is one
constant, and raising it is not a schema change. **Do not raise it in this change**; raise it
when the RQ-25 measurement says what to raise it to.

### D24 — Exactly one decision here earns an ADR

`openspec/config.yaml` and CLAUDE.md set the filter: anything costing **more than a sprint to
reverse** becomes an ADR.

| Decision | Reversal cost | Verdict |
| --- | --- | --- |
| Catalogue identity is the pytest node id, stored verbatim in `stable_id` | Live databases become keyed by it. Changing the recipe re-keys every row — the migration ADR-5 and RQ-29 exist to prevent. **Far more than a sprint** | **ADR-0012 — "Key the test catalogue by the pytest node id"**, Nygard, `Proposed` in the PR, linked to RQ-13, RQ-9 and ADR-5, recording both rejected alternatives verbatim from the proposal |
| `results` as an optional envelope section | `git revert`; no schema diff, no migration (proposal rollback plan) | Design note (D15) |
| `ResultReport` is `extra="allow"` + `ignored` | one `ConfigDict` value | Design note (D15) |
| Dedup layering | no data is keyed by it; rows are re-derivable from a re-run | Design note (D19) |
| `last_seen_at` uses the client clock | one soft-metadata column, no key | Design note (D20) |
| Port renamed to `record_session` | one mechanical refactor behind a seam OQ-4 already declared temporary | Design note (D21) |

No existing ADR is restated here: ADR-3 (clean architecture, `Protocol` ports), ADR-4 (two
distributions, independent release), ADR-5 (complete schema, no migrations), ADR-6 (stdlib
`sqlite3`, no ORM) and ADR-9 (server owns every write) are referenced and relied on, not
re-argued.

---

## Interfaces / Contracts

**Wire — the `results` section** (one element shown; `stable_id` and `attempt` are absent by
design, D18/D19):

```json
{
  "run": { "id": "…32 hex…", "started_at": "…", "finished_at": "…",
           "exit_status": 0, "interrupted": false, "interrupt_reason": null },
  "results": [
    {
      "node_id": "packages/vantage/tests/test_execution.py::test_identity_rejects_anything_but_32_lowercase_hex_characters[]",
      "file_path": "packages/vantage/tests/test_execution.py",
      "class_name": null,
      "function_name": "test_identity_rejects_anything_but_32_lowercase_hex_characters",
      "param_id": "",
      "outcome": "passed",
      "duration": 0.0031,
      "started_at": "2026-08-18T09:14:02.481930+00:00",
      "finished_at": "2026-08-18T09:14:02.485012+00:00",
      "setup_outcome": "passed", "call_outcome": "passed", "teardown_outcome": "passed",
      "setup_duration": 0.0008, "call_duration": 0.0019, "teardown_duration": 0.0004,
      "worker_id": "gw1"
    }
  ]
}
```

**Port** (`vantage/core/ports/storage.py`):

```python
class ExecutionStore(Protocol):
    def record_session(self, execution: Execution, *, results: Sequence[Result],
                       received_at: datetime) -> bool: ...
    def get_execution(self, execution_id: str) -> Execution | None: ...
    def count_executions(self) -> int: ...
    def get_results(self, execution_id: str) -> Sequence[Result]: ...      # RQ-4/5/9
    def count_results(self) -> int: ...                                    # RQ-12, RQ-38.2
    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None: ...  # RQ-13
    def close(self) -> None: ...
```

**Domain** (`vantage/core/domain/result.py`, stdlib only, frozen + slots):

```python
OUTCOMES = frozenset({"passed", "failed", "error", "skipped", "xfailed", "xpassed"})

@dataclass(frozen=True, slots=True)
class CaseIdentity:
    node_id: str; file_path: str; class_name: str | None
    function_name: str; param_id: str | None      # "" and None are different values

@dataclass(frozen=True, slots=True)
class Result:
    identity: CaseIdentity
    outcome: str                                   # __post_init__ validates against OUTCOMES
    duration: float | None
    started_at: datetime | None; finished_at: datetime | None
    setup_outcome: str | None; call_outcome: str | None; teardown_outcome: str | None
    setup_duration: float | None; call_duration: float | None; teardown_duration: float | None
    worker_id: str | None

@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    identity: CaseIdentity
    first_seen_at: datetime; last_seen_at: datetime; last_seen_run_id: str | None
```

The six outcome strings now exist in three places — the schema `CHECK`, `OUTCOMES`, and the
service `Literal` — because the plugin cannot import `vantage` (RQ-24) and the boundary is
HTTP (ADR-9). One consistency test parses the six values out of `schema.sql`'s `CHECK` and
asserts they equal `OUTCOMES` and the `Literal` arguments; on the plugin side the wire
validation *is* the enforcement — an unknown outcome is a 422.

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `packages/vantage/src/vantage/core/domain/result.py` | Create | `Result`, `CaseIdentity`, `CatalogueEntry`, `OUTCOMES` |
| `packages/vantage/src/vantage/core/ports/storage.py` | Modify | `record_execution` → `record_session`; three read methods (D21) |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Modify | Four statements in one transaction (D22); catalogue upsert (D20) |
| `packages/vantage/src/vantage/storage/memory.py` | Modify | Same semantics over dicts; second mechanism, not a stub (RQ-30) |
| `packages/vantage/src/vantage/service/schemas.py` | Modify | `ResultReport`; `results: list[ResultReport] \| None = None`; duplicate-node-id validator |
| `packages/vantage/src/vantage/service/routes/runs.py` | Modify | Convert results, call `record_session`, populate `Acknowledgement.ignored` |
| `packages/pytest-vantage/src/pytest_vantage/capture.py` | Create | Phase accumulation, outcome derivation (D17), identity decomposition (D18) |
| `packages/pytest-vantage/src/pytest_vantage/recorder.py` | Modify | `pytest_runtest_logreport` hook; assemble `results` into the existing single POST |
| `packages/vantage/src/vantage/storage/schema.sql` | **Unchanged** | A diff here means the design went wrong; the manifest ground-truth test enforces it |
| `packages/pytest-vantage/src/pytest_vantage/plugin.py` | **Unchanged** | The xdist controller guard is already there (D2) and is D19 layer 1 |
| `docs/adr/0012-key-the-test-catalogue-by-the-pytest-node-id.md` | Create | The one decision past the reversal-cost filter (D24) |

## Testing Strategy

Strict TDD: RED first, every verifying test carries `@pytest.mark.req("RQ-xx")` so
`grep -r "RQ-9"` reaches the thing that proves it.

| Layer | What | Approach |
| --- | --- | --- |
| Unit (plugin) | Outcome derivation, all nine precedence rows incl. strict-xpass; identity decomposition incl. `[]`, module-level, nested class, bracketed param id | Table-driven over synthetic report doubles — no subprocess needed for the pure functions |
| Unit (core) | `Result` rejects an outcome outside `OUTCOMES`; `""` vs `None` survives the dataclass | Mirrors `test_execution.py` |
| Contract (both adapters) | `record_session`, replay no-op, catalogue monotonicity, RQ-13.1 untouched entry | Extend `packages/vantage/tests/vantage_port_contract.py` — both adapters, one suite (RQ-30) |
| Integration (service) | `results` optional/absent/empty; malformed section ⇒ 422 whole report; duplicate node id ⇒ 422; unknown key ⇒ recorded + named in `ignored`; one commit for 500 results (RQ-3); 500-result payload byte measurement | `test_ingestion.py`, `test_rejection.py`, commit-counting wrapper |
| Integration (storage) | Two concurrent 200-test sessions ⇒ 400 results (RQ-38.2); ten simultaneous ⇒ no error (RQ-38.3) | Extend `test_concurrency.py` |
| E2E | Six tests under `-n 2` ⇒ six results **and** one run entry; the same six without xdist ⇒ six (RQ-12.1/2/3); delete a test, re-run, entry survives with `last_seen_at` unchanged (RQ-13.1); RQ-4 fixture suite; RQ-5 sleeping fixture | `pytester` subprocesses against `packages/pytest-vantage/tests/vantage_test_server.py` |
| Inspection | `schema.sql` byte-identical | Existing `test_schema_manifest.py::test_fresh_database_matches_the_recorded_ground_truth` |
| Architecture | `vantage.core` stdlib-only; `pytest-vantage` imports nothing but pytest + stdlib (no xdist import) | Existing `test_architecture.py` AST walk, plus `deptry` and the clean-environment install job |

## Threat Matrix

`references/threat-matrix.md` is **N/A**: this change adds no routing selector, shell command,
subprocess, VCS or PR automation, executable-file classification, or process-integration
boundary. All five of its rows (documentation-like paths, git repository selection, commit
state, push state, PR commands) are N/A for that reason.

The HTTP boundary it *does* touch is already covered by the Milestone 1 matrix and stays
covered: the unbounded-body row is unchanged (D23 adds volume inside the same 1 MiB cap, and
a 1 MiB body bounds the row count too), and the rejection-echo row is unchanged — `results`
`loc` segments pass `_safe_segment`'s allow-list and no node id **value** is ever echoed.
`ignored` (D15) carries client-chosen key names, so it goes through the same allow-list, and
that is a task, not an assumption.

## Migration / Rollout

**No migration.** No schema change, no data backfill, no feature flag. Rollout is the
proposal's four-slice feature-branch chain, server first so the plugin never reports into a
stub. Rollback is `git revert`: existing databases keep working with `result` and `test_case`
empty again, and rows written by a newer server stay inert (no read API reads them). The
skew case — new plugin, reverted server — is supported by `extra="ignore"` on the envelope,
which is why `results` is optional (D15).

## Open Questions

Open design questions 1–5 from the proposal are all resolved: 1 by binding decision (node id
verbatim), 2 by D19, 3 by D20, 4 by D23, 5 by D21. Remaining, and none of them blocks
`sdd-tasks`:

- [ ] Exact 500-result payload size and server peak memory — **measured, not guessed** (D23);
      the numbers land in the slice-2 and slice-4 test output.
- [ ] Whether `MAX_REPORT_BYTES` needs raising for suites in the low thousands. Deferred to
      the RQ-25 measurement; do not raise it blind in this change.
- [ ] A client clock far in the future pins `test_case.last_seen_at` (D20). One soft-metadata
      column; revisit with RQ-44 and Phase 3, not here.
