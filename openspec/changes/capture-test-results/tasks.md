# Tasks: Capture test results

**Change:** `capture-test-results` · Strict TDD — every behavioural task
names its failing test first. Test command: `uv run --extra dev pytest`.

**No new `RQ-xx` identifiers are minted.** Existing IDs are referenced because they are
executable (`@pytest.mark.req`, `--strict-markers`). Obligations beyond a literal criterion
are named by capability and scenario, as the delta specs do.

**pytest entry-point registration: nothing to do.** `pytest11 = pytest_vantage.plugin` is
already declared in `packages/pytest-vantage/pyproject.toml`. `capture.py` is imported by
`recorder.py`, not registered separately, and every E2E task uses
`pytester.runpytest_subprocess` against the installed distribution. **No user ever edits a
`conftest.py` for this change.**

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | **~2,230 authored** (production ~760, tests ~1,385, ADR ~85) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 → PR 4 → PR 5 → PR 6 → PR 7 → PR 8 → PR 9 |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

**This supersedes the proposal's ~950–1,200 forecast.** The proposal's four slices do not
fit: its slice 1 alone counts ~690 lines. The gap is test volume, not production code — this
repository's test modules run 300–580 lines each, and seven requirements are verified across
unit, port-contract, integration and E2E layers. Production code is close to the proposal's
implied number; verification is roughly double it.

**The one over-budget slice was split rather than excepted.** Unit 2 originally forecast
**~430**. Its two stated constraints are real but narrower than they first read: the
`record_execution` → `record_session` rename must land **atomically**, which requires it to
occupy one PR — not to share a PR with the catalogue upsert. So it gets its own.

- **Unit 2** is now the rename alone: port, both adapters and every call site, purely
  mechanical, no behaviour change, the existing 108 tests staying green as its proof.
- **Unit 3** is the catalogue upsert and result persistence, shipping with its RED
  monotonicity tests, so strict TDD ordering holds inside one PR.

Nine units, **none over the 400-line budget**, and no `size:exception` needed. Decided
2026-08-18.

### Suggested Work Units

Bases: PR 1 → tracker branch; PR *n* → PR *n−1* branch.

| Unit | Goal | PR | Forecast | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|----------|----------------------|-----------------|-------------------|
| 1 | Core domain + ADR-0012 | PR 1 | ~255 | `uv run --extra dev pytest packages/vantage/tests/test_result.py` | N/A — pure stdlib dataclasses, no runtime surface | New files only; delete `result.py`, `test_result.py`, the ADR |
| 2 | Port rename `record_execution` → `record_session`, both adapters, every call site — mechanical, no behaviour change | PR 2 | ~80 | `uv run --extra dev pytest` (all 108 existing tests stay green — that is the proof) | `uv run vantage serve` then a manual `POST /api/v1/runs` with no `results` still returns 201 | Revert restores `record_execution`; nothing else moved |
| 3 | Catalogue upsert, result persistence in one transaction, contract suite | PR 3 | ~350 | `uv run --extra dev pytest packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py` | Real server; a two-result report leaves one run row, two result rows and one catalogue entry | Revert drops the new port methods; no schema diff, `result`/`test_case` go back to empty |
| 4 | Service: `results` on the envelope, persisted with the run | PR 4 | ~325 | `uv run --extra dev pytest packages/vantage/tests/test_ingestion.py` | Real server + `curl` a two-result report; run row + 2 result rows | Revert drops `ResultReport`; envelope `extra="ignore"` keeps old reports working |
| 5 | Service rejection, RQ-3 atomicity and the payload measurement | PR 5 | ~245 | `uv run --extra dev pytest packages/vantage/tests/test_rejection.py` | 500-result body against a live server; record the byte count and `tracemalloc` peak | Tests + one commit-counting wrapper; no production behaviour to revert |
| 6 | Plugin: identity decomposition (RQ-9) | PR 6 | ~195 | `uv run --extra dev pytest packages/pytest-vantage/tests/test_capture.py -k identity` | N/A — pure function, exercised end-to-end in unit 7 | New `capture.py` decomposition function only |
| 7 | Plugin: phase accumulation, outcome derivation, report assembly | PR 7 | ~250 | `uv run --extra dev pytest packages/pytest-vantage/tests/test_capture.py` | `uv run --extra dev pytest --vantage=<addr>` against a live server; inspect `results` | Revert removes the hook; `pytest_sessionfinish` sends `run` alone, which the server still accepts |
| 8 | E2E: RQ-4, RQ-5, RQ-9 through a real server | PR 8 | ~230 | `uv run --extra dev pytest packages/pytest-vantage/tests/test_result_capture.py` | The test *is* the harness — real subprocess pytest, real uvicorn | Test file + `vantage_test_server.py` accessors |
| 9 | E2E xdist (RQ-12), catalogue (RQ-13), traceability and schema sweep | PR 9 | ~300 | `uv run --extra dev pytest -m 'req("RQ-12") or req("RQ-13")'` | `uv run --extra dev pytest -n 2 --vantage=<addr>` and the same suite without xdist | Verification only; nothing to revert but the tests |

---

## Phase 1: Core domain and the one ADR (PR 1)

- [x] 1.1 Write `docs/adr/0012-key-the-test-catalogue-by-the-pytest-node-id.md` — Nygard
      (Status / Context / Decision / Consequences), `Status: Proposed`, imperative title,
      linked to RQ-13, RQ-9 and ADR-5. Record both rejected alternatives verbatim: hashing
      the four decomposed columns (same information, same rename split, but changing the
      recipe re-keys every row) and function-plus-parameters ignoring the path (survives a
      move, collides two identically-named tests — a merged history asserts something false,
      a split one is only incomplete).
- [x] 1.2 RED `packages/vantage/tests/test_result.py`: `Result` rejects an outcome outside
      `OUTCOMES`; six accepted values match the `result.outcome` CHECK.
- [x] 1.3 RED, same file: `CaseIdentity(param_id="")` and `CaseIdentity(param_id=None)` are
      **not equal** — the dataclass hop of the four-hop `""`-vs-NULL guard (D18).
- [x] 1.4 RED: `class_name=None` for a module-level identity; a phase duration of `0.0`
      survives as `0.0` and is not coerced to `None` (RQ-5.2, forbidden idiom `x or None`).
- [x] 1.5 GREEN `packages/vantage/src/vantage/core/domain/result.py`: `OUTCOMES` as a
      module-level `frozenset` (**never `Enum`** — `class X(str, Enum)` changes `__format__`
      between 3.10 and 3.11), `CaseIdentity`, `Result`, `CatalogueEntry` as frozen slotted
      stdlib dataclasses with `__post_init__` validation. No name starts with `Test`.
- [x] 1.6 Verify `packages/vantage/tests/test_architecture.py` still passes — `vantage.core`
      imports the standard library only (RQ-26).

## Phase 2: Port rename, atomic and mechanical (PR 2)

The rename must land **atomically** — the moment the port says `record_session` and an
adapter still says `record_execution`, that adapter stops structurally satisfying
`ExecutionStore` and `mypy --strict` goes red. That is why it occupies one PR. It is *not*
why it must share a PR with the catalogue upsert, which is why it no longer does.

No behaviour changes here. **The proof is that all 108 existing tests pass untouched** — a
pure rename is the REFACTOR step of a cycle whose RED and GREEN already happened.

- [x] 2.1 REFACTOR `packages/vantage/src/vantage/core/ports/storage.py`: rename
      `record_execution` → `record_session(execution, *, results, received_at) -> bool`,
      `results` keyword-only and required. **No new methods yet** — `get_results`,
      `count_results` and `get_catalogue_entry` arrive with the tests that need them, in
      Phase 3, so the Protocol never declares something no adapter implements.
- [x] 2.2 REFACTOR both adapters to the new name and signature —
      `packages/vantage/src/vantage/storage/sqlite_store.py` and
      `packages/vantage/src/vantage/storage/memory.py`. Each accepts `results` and persists
      nothing from it yet; say so in a comment naming Phase 3, so a reader does not read the
      gap as an oversight.
- [x] 2.3 Update every call site: `packages/vantage/src/vantage/service/routes/runs.py`
      (passing `results=()`), `packages/vantage/tests/test_concurrency.py`, and
      `packages/vantage/tests/vantage_port_contract.py`. Confirm
      `rg record_execution --glob '!openspec/changes/archive/**'` returns nothing outside
      this change's own documents — 6 files carry it today.
- [x] 2.4 GREEN gate: `uv run --extra dev pytest` is 108 passed, `uv run mypy .` clean,
      `uv run ruff check .` clean. Any failure here means the rename was not mechanical and
      something else rode along with it.

## Phase 3: Catalogue upsert and result persistence (PR 3)

- [x] 3.1 RED: extend `packages/vantage/tests/vantage_port_contract.py` — `record_session`
      with two results persists the run and both results; `count_results() == 2`.
- [x] 3.2 RED, contract: replaying the identical report leaves `count_results()` unchanged
      and returns `False` (RQ-41 idempotency, D19 layer 3).
- [x] 3.3 RED, contract: `get_results` returns per-phase outcomes and durations exactly as
      stored — a phase that never ran reads back `None`, not `0.0` (RQ-5.2).
- [x] 3.4 RED, contract: a result stored with `param_id=""` reads back `""`, and a query for
      NULL parameter identifiers excludes it — the **SQLite hop** of the `""`-vs-NULL guard.
- [x] 3.5 RED, contract: `get_catalogue_entry` after a session; a second session with the
      same node id reuses the entry, advances `last_seen_at` and leaves `first_seen_at`
      untouched (RQ-13.2).
- [x] 3.6 RED, contract: a session whose `run.started_at` is **older** than the stored
      `last_seen_at` does not roll it back, and `last_seen_run_id` does not move (D20 `MAX`).
- [x] 3.7 RED, contract: a node id absent from the report has its catalogue row untouched —
      `last_seen_at` literally unchanged (RQ-13.1).
- [x] 3.8 GREEN `packages/vantage/src/vantage/core/ports/storage.py`: add `get_results`, `count_results`,
      `get_catalogue_entry`.
- [x] 3.9 GREEN `packages/vantage/src/vantage/storage/sqlite_store.py`: four statements in
      one `BEGIN IMMEDIATE`, in the order the FKs require (D22) — run insert, `executemany`
      catalogue upsert `ON CONFLICT(node_id) DO UPDATE` with the `MAX`/`CASE` monotonicity
      clause, batched `SELECT id, node_id … WHERE node_id IN (…)` at ≤500 placeholders,
      `executemany` result insert `ON CONFLICT(run_id, node_id, attempt) DO NOTHING`.
      **No `RETURNING`** — it needs SQLite ≥3.35, above the 3.10 floor.
- [x] 3.10 GREEN `packages/vantage/src/vantage/storage/memory.py`: same semantics over dicts
      — catalogue keyed by node id with the `MAX` guard, results keyed by
      `(run_id, node_id, attempt)`, first-write-wins. A second mechanism, not a stub (RQ-30).
- [x] 3.11 REFACTOR: update the `sqlite_store.py` and `memory.py` module docstrings to name
      D19–D22, matching the existing D3/D5/D8 citation convention.


## Phase 4: Service — `results` on the envelope (PR 4)

- [x] 4.1 RED `packages/vantage/tests/test_ingestion.py`: a report carrying N results yields
      one run row and N result rows, acknowledged (RQ-41.1).
- [x] 4.2 RED: a report with **no** `results` section records its run and is not rejected;
      `results: null` and `results: []` both record the run and write zero result rows
      (RQ-41.1, D15 — the supported plugin/server skew case).
- [x] 4.3 RED: replaying a report carrying N results leaves exactly N result rows and returns
      200 `duplicate`, never an error (RQ-41.2).
- [x] 4.4 RED: a `ResultReport` carrying an unknown key records normally and the **key name**
      appears deduplicated in `Acknowledgement.ignored` as `results[].<name>`, never a
      per-index path (D15).
- [x] 4.5 RED: `param_id: ""` on the wire arrives as `""` and `param_id: null` as `None` —
      the **Pydantic hop**. No `min_length=1`, no falsy-to-`None` coercion (D18).
- [x] 4.6 GREEN `packages/vantage/src/vantage/service/schemas.py`: `ResultReport`
      (`extra="allow"`, every known field required-and-nullable, never defaulted);
      `results: list[ResultReport] | None = None` on `SessionReport`. **Do not touch
      `RunReport`** (`extra="forbid"`). No `/api/v2`.
- [x] 4.7 GREEN `packages/vantage/src/vantage/service/routes/runs.py`: convert `ResultReport`
      to the core `Result`, assign `stable_id` server-side from `node_id` (it is not on the
      wire), call `record_session`, populate `ignored`. The 201/200 branch is untouched.

## Phase 5: Service — rejection, atomicity, measurement (PR 5)

- [x] 5.1 RED `packages/vantage/tests/test_rejection.py`: one malformed entry at index 250 of
      500 rejects the **entire** report — `count_executions() == 0` and `count_results() == 0`
      (RQ-42.1 with RQ-3.2).
- [x] 5.2 RED: a duplicate `node_id` inside one report is 422, whole report (D19 layer 2).
- [x] 5.3 RED (**threat-derived**, D15/Threat Matrix): the rejection body and `ignored` name
      the offending field through the existing `errors.py::safe_segment` allow-list, and no
      node id **value** is ever echoed. This is a task, not an assumption.
- [x] 5.4 GREEN: list validator for duplicate node ids; `ignored` key names routed through the
      same allow-list.
- [x] 5.5 RED then measure `test_five_hundred_results_fit_within_the_body_cap`
      (`@pytest.mark.req("RQ-3")`): build a 500-result report through the real assembler and
      assert `len(json.dumps(report).encode("utf-8")) < MAX_REPORT_BYTES`, **reporting the
      real byte count**. Do not raise `MAX_REPORT_BYTES` in this change (D23).
- [x] 5.6 Measure server peak memory with `tracemalloc` around one 500-result request. Record
      the number; assert nothing against an invented threshold (D23).
- [x] 5.7 RED: a 500-result report reaches storage in **one commit**, counted through a
      commit-counting store wrapper (RQ-3.3, D21).
- [x] 5.8 Add the outcome-vocabulary consistency test: parse the six values out of
      `schema.sql`'s `CHECK`, assert they equal `OUTCOMES` and the service `Literal` arguments.

### Added 2026-08-18 — normalize timestamps to UTC at the service boundary

Found while reviewing Phase 3, recorded as Engram observation 62. `test_case.last_seen_at`
is TEXT, and D20's guard advances it with `MAX(test_case.last_seen_at, excluded.last_seen_at)`
— a **lexicographic** comparison. That equals an instant comparison only if every stored
string carries the same offset, and `routes/runs.py` passes wire timestamps through
unnormalized. Reproduced: `'2026-08-18T12:00:00+02:00'` (10:00 UTC) sorts *after*
`'2026-08-18T11:00:00+00:00'` (11:00 UTC), so an older run rolls `last_seen_at` forward and
drags `last_seen_run_id` with it. The in-memory adapter compares real `datetime` objects, so
on mixed input it raises `TypeError` instead — two adapters, two different wrong answers, and
the RQ-30 contract suite misses both because every test uses one timestamp form.

Invisible today only because `pytest_vantage.recorder.isoformat_utc` normalizes; ADR-9 exists
so a plugin in another language can talk to this server, and that one would not.

The fix goes at the boundary, where the plugin already does it, so the storage layer only ever
sees one form. Decided 2026-08-18.

- [x] 5.9 RED `packages/vantage/tests/test_ingestion.py`: record a session, then record a
      second one for the same `node_id` whose `run.started_at` is an **earlier instant
      expressed with a `+02:00` offset**, chosen so its raw ISO string sorts *after* the
      stored one. Assert `last_seen_at` did not move and `last_seen_run_id` still names the
      first run (D20). This fails today.
- [x] 5.10 RED, same file: a report whose timestamps carry a non-UTC offset reads back as the
      **equivalent UTC instant**, and a report whose timestamps are **naive** is interpreted
      as UTC. Cover `run.started_at`/`finished_at` and a result's `started_at`/`finished_at`
      in the same test — one path, not two.
- [x] 5.11 GREEN `packages/vantage/src/vantage/service/routes/runs.py`: one helper applied to
      every timestamp before it reaches the store. An **aware** datetime converts with
      `astimezone(timezone.utc)`. A **naive** one is stamped `replace(tzinfo=timezone.utc)` —
      never `astimezone()` on a naive value, which silently assumes the server's local zone
      and makes the result depend on where the server runs. Never `datetime.UTC` (3.11+).
      Replace the `_to_result` comment that currently says normalization is deliberately
      absent; it stops being true here.
- [x] 5.12 Correct Engram observation 62 to record the resolution, and note in
      `docs/open-questions.md` that the guard is now sound for any client, not just this
      project's plugin.

## Phase 6: Plugin — identity decomposition (PR 6)

- [ ] 6.1 RED `packages/pytest-vantage/tests/test_capture.py`, table-driven: module-level
      test → `class_name is None` (RQ-9.2); nested class → segments joined with `"::"`;
      unparametrised → `param_id is None` (RQ-9.3).
- [ ] 6.2 RED: `…::test_x[]` → `param_id == ""`, **not** `None` — the brackets are the
      evidence of parametrisation, the content is not. The live case is
      `test_execution.py::test_identity_rejects_anything_but_32_lowercase_hex_characters[]`.
- [ ] 6.3 RED: `…::test_x[[0]]` → `param_id == "[0]"`, proving first-`[`/last-`]` slicing
      rather than `partition`/`rpartition` symmetry.
- [ ] 6.4 GREEN `packages/pytest-vantage/src/pytest_vantage/capture.py`: `decompose` per D18.
      Standard library and `pytest` only — **never import xdist** (RQ-24).
- [ ] 6.5 Confirm `packages/pytest-vantage/tests/test_plugin_imports.py` still passes.

## Phase 7: Plugin — phases, outcome, assembly (PR 7)

- [ ] 7.1 RED `test_capture.py`, table-driven over synthetic report doubles: all nine D17
      precedence rows — setup failed → `error` (RQ-4.1); setup skipped → `skipped` (RQ-4.2);
      call skipped/failed with `wasxfail` → `xfailed` (RQ-4.3); call passed with `wasxfail` →
      `xpassed` (RQ-4.4); call passed with teardown failed → `error` (RQ-4.5).
- [ ] 7.2 RED, **its own test**: a **strict** `xfail` that passes is `failed`, not `xpassed` —
      pytest sets `outcome="failed"` with `wasxfail` absent for `[XPASS(strict)]`. RQ-4.4
      describes only the non-strict case; reading `wasxfail` naively mislabels this one.
- [ ] 7.3 RED: a teardown failure downgrades **only** a `passed` result; a `failed` result
      keeps its own word and the teardown failure stays visible in `teardown_outcome`.
- [ ] 7.4 RED: a phase that never ran serialises as `null`, never `0.0`, and a genuine `0.0`
      survives as `0.0` — the **JSON hop** of the `""`-vs-NULL family (D17).
- [ ] 7.5 RED: a test resolved only through setup and call, with no teardown report, is
      **dropped** rather than invented — "resolution, not attendance" (D16).
- [ ] 7.6 GREEN `capture.py`: `_Pending` accumulation in `dict[str, _Pending]` (insertion
      order = execution order, duplicate report = overwrite), `derive_outcome`, payload
      assembly. Timestamps via `datetime.fromtimestamp(..., timezone.utc)` — **never
      `datetime.UTC`** (3.11+). `worker_id` read through a `getattr` chain.
- [ ] 7.7 GREEN `packages/pytest-vantage/src/pytest_vantage/recorder.py`: add
      `pytest_runtest_logreport` (the one hook xdist forwards to the controller) and attach
      `results` to the report already sent from `pytest_sessionfinish`. **Still exactly one
      HTTP request per session** (ADR-9, RQ-25.2). `plugin.py` is unchanged — its xdist
      controller guard is already D19 layer 1.

## Phase 8: End-to-end capture (PR 8)

- [ ] 8.1 Extend `packages/pytest-vantage/tests/vantage_test_server.py` with `results()` and
      `catalogue_entry(node_id)` accessors over the in-memory store.
- [ ] 8.2 RED `packages/pytest-vantage/tests/test_result_capture.py` (pytester subprocess +
      real server): a fixture raising before the body → `error`; `@pytest.mark.skip` →
      `skipped`; failing `xfail` → `xfailed`; passing non-strict `xfail` → `xpassed`; a test
      passing with a raising teardown is **not** `passed` (RQ-4.1–4.5).
- [ ] 8.3 RED: an 8s fixture with a 0.1s body → `setup_duration >= 8` and `call_duration < 1`;
      a setup failure → `call_duration is None`, not `0.0` (RQ-5.1, RQ-5.2).
- [ ] 8.4 RED: filtering stored results by file path alone returns every test defined in that
      file (RQ-9.1); a module-level test stores a null class name (RQ-9.2).

## Phase 9: xdist, catalogue, and the sweep (PR 9)

- [ ] 9.1 RED `packages/pytest-vantage/tests/test_xdist_capture.py`: six tests under `-n 2`
      produce six results (RQ-12.1) and exactly one run entry (RQ-12.3).
- [ ] 9.2 RED, **non-optional control**: the same six tests without xdist also produce six
      results (RQ-12.2). This is the only test that catches an over-aggressive dedup filter.
- [ ] 9.3 RED: delete a test, re-run — its catalogue entry survives with `last_seen_at`
      unchanged (RQ-13.1); add the same node id back — the same entry is reused and the
      timestamp advances (RQ-13.2).
- [ ] 9.4 RED `packages/vantage/tests/test_concurrency.py`: two concurrent 200-test sessions
      → 400 result rows (RQ-38.2); ten simultaneous sessions → ten run entries and no error
      response (RQ-38.3).
- [ ] 9.5 **Schema-unchanged verification**: assert
      `git diff --exit-code -- packages/vantage/src/vantage/storage/schema.sql` is empty
      across the whole chain, and that
      `test_schema_manifest.py::test_fresh_database_matches_the_recorded_ground_truth` is
      green. A diff here means the design went wrong (RQ-29, ADR-5).
- [ ] 9.6 Traceability sweep: `grep -r "RQ-4"`, `RQ-5`, `RQ-9`, `RQ-12`, `RQ-13`, `RQ-3`,
      `RQ-38` each reach the test that proves them; every new verifying test carries
      `@pytest.mark.req`, declared under `--strict-markers`.
- [ ] 9.7 Run the full gate: `uv run ruff format . && uv run ruff check --fix .`,
      `uv run mypy .`, `uv run deptry .`, and the 3.10–3.13 × xdist matrix. Confirm
      `pytest-vantage` still imports nothing but pytest and the standard library (RQ-24) and
      `vantage.core` nothing but the standard library (RQ-26).
