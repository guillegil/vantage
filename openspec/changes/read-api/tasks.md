# Tasks: Read API

**Change:** `read-api` · Strict TDD — every implementation task is preceded by
its failing test task. Test command: `uv run pytest`.

**No numeric requirement identifiers.** Every obligation below is traced by
**capability** and **requirement name** — `history-read-api` → *Test
history*, `api-interface-document` → *Machine-readable interface document*,
and so on — matching the proposal, the five delta specs, `design.md` and
ADR-0015, none of which carry a numeric identifier. This file mints none
either. Existing `vantage.core`/`vantage.storage` code this change touches
still carries older numeric requirement markers in its own docstrings and
tests; those are pre-existing and untouched, not new.

Decisions D53–D67 (`design.md`) are settled and not re-argued here — this file
sequences them and decomposes the design's Testing Strategy table and threat
matrix into tasks. **Schema is unchanged**: no migration, no `schema_version`
bump, ADR-0013's refusal gate is not engaged. `packages/pytest-vantage/**` is
untouched — no task opens it.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,490 authored across seven slices (design's own forecast: 230 / 380 / 360 / 390 / 340 / 390 / 400) |
| 400-line budget risk | Low per slice — none exceeds 400; High for the change as a whole, which is why it is chained |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 → PR 4 → PR 5 → PR 6 → PR 7 |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Low
```

**Hard ordering constraint, preserved from design:** PR 6 (the interface
document) MUST land before PR 7 (the read-only proof harness) — the harness
derives its call list from the document (D53). Every other adjacent pair is
sequenced for reviewability, not a hard dependency; PR 3 could in principle
follow PR 1 directly, but `feature-branch-chain` targets the immediate
previous branch, so the chain stays linear.

### Dependency diagram

```
ft/read-api (tracker, draft, no-merge)
  └─ PR1 ft/read-api-01-port-projection
       └─ PR2 ft/read-api-02-list-detail-adapters
            └─ PR3 ft/read-api-03-results-history-adapters
                 └─ PR4 ft/read-api-04-run-routes
                      └─ PR5 ft/read-api-05-results-history-routes
                           └─ PR6 ft/read-api-06-interface-document   ◄─┐
                                └─ PR7 ft/read-api-07-read-only-proof ──┘ (reads PR6's document)
```

At apply time, each child PR's description marks its own position in this
diagram with 📍, per `chained-pr`.

### Suggested Work Units

| Unit | Goal | PR | Branch (base) | Est. lines | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|----|------------|-----------------------|------------------|--------------------|
| 1 | `VcsProjection`/`project_vcs`, `Page`/`RunListEntry`/`RunDetail`/`HistoryEntry`, pagination constants (D57–D60) | PR1 | `ft/read-api-01-port-projection` (base `ft/read-api`) | ~230 | `uv run pytest packages/vantage/tests/test_projection.py packages/vantage/tests/test_storage_types.py` | N/A — nothing calls these types yet | Delete `projection.py`, `test_projection.py`, `test_storage_types.py`, and the four new type/constant definitions in `storage.py`; nothing references them |
| 2 | Both adapters: `list_runs` + `get_run_detail` + contract scenarios | PR2 | `ft/read-api-02-list-detail-adapters` (base PR1) | ~380 | `uv run pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py` | N/A — no route calls the store yet; contract suite is the runtime proof against both real adapters | Revert removes `list_runs`/`get_run_detail` from the Protocol and both adapters; PR1's types stay, still uncalled |
| 3 | Both adapters: `list_results` + `list_history` + contract scenarios | PR3 | `ft/read-api-03-results-history-adapters` (base PR2) | ~360 | `uv run pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py` | N/A — same reason as PR2 | Revert removes `list_results`/`list_history`; PR2's two methods stay working |
| 4 | Run list + run detail routes, response models, pagination envelope, `derive_presentation` wiring, liveness demonstration (D62) | PR4 | `ft/read-api-04-run-routes` (base PR3) | ~390 | `uv run pytest packages/vantage/tests/test_routes_read.py` | `uv run pytest packages/vantage/tests/test_routes_read.py -k liveness` against `InMemoryExecutionStore` via ASGI `TestClient` — the abandoned/running/interrupted demonstration is itself the runtime harness | Revert `include_router(read_router, ...)` and delete `routes/read.py`'s two routes; the port methods (PR2) stay implemented but uncalled |
| 5 | Results route + history route + identity parameter and its encoding tests (D54) | PR5 | `ft/read-api-05-results-history-routes` (base PR4) | ~340 | `uv run pytest packages/vantage/tests/test_routes_read.py` | Same `TestClient` harness, extended to `/runs/{id}/results` and `/tests/history` | Revert the two new routes in `routes/read.py`; run list/detail (PR4) keep working |
| 6 | Hand-written OpenAPI document, drift test, 2xx test, generated docs disabled, `v1-ingestion.md` trim (D53, D55, D56, D66) | PR6 | `ft/read-api-06-interface-document` (base PR5) | ~390 | `uv run pytest packages/vantage/tests/test_interface_document.py` | `uv run pytest packages/vantage/tests/test_interface_document.py -k every_documented_path` drives every route live through `TestClient` | Revert `openapi_url=None, docs_url=None, redoc_url=None` (3 kwargs) and delete `openapi/`; FastAPI's generated docs return; every route keeps working |
| 7 | Read-only harness, latency benchmark, committed measurements, ADR-0015 → Accepted, OQ-9 (D63–D65, D67) | PR7 | `ft/read-api-07-read-only-proof` (base PR6) | ~400 | `uv run pytest packages/vantage/tests/test_read_only_surface.py` | `uv run python scripts/measure_history_latency.py` against a 500-run/100k-result fixture — manual, never collected by the suite | Revert deletes `test_read_only_surface.py` and the benchmark script; behavior is unaffected, only proof and docs revert |

---

## Phase 1: `projection.py` and read types on the port (PR1)

Creates `packages/vantage/src/vantage/core/domain/projection.py`,
`packages/vantage/tests/test_projection.py`,
`packages/vantage/tests/test_storage_types.py`. Modifies
`packages/vantage/src/vantage/core/ports/storage.py` (types and constants
only — the `ExecutionStore` Protocol gains no method here, so no adapter goes
out of structural conformance mid-slice). Stdlib only — the existing AST
architecture test enforcing `vantage.core` import isolation, unchanged, still
applies and still passes.

- [x] 1.1 RED `test_projection.py::test_subject_bounded_at_120_chars_sets_flag`
      — a `VcsContext` whose `commit_subject` is 200 characters and
      `commit_subject_truncated=False`; `project_vcs` returns a
      `VcsProjection` whose `commit_subject` is the first 120 characters and
      `commit_subject_truncated is True`. *(history-read-api → Lean list
      projections → The commit subject is bounded in list responses, D60)*
- [x] 1.2 RED `test_projection.py::test_capture_truncation_flag_survives_even_when_short`
      — a `VcsContext` with `commit_subject_truncated=True` and a
      `commit_subject` under 120 characters; `project_vcs` still returns
      `commit_subject_truncated is True` — the other half of the disjunction,
      independent of display width. *(Lean list projections → The truncation
      flag never surfaces independently of its subject, D60)*
- [x] 1.3 RED `test_projection.py::test_null_vcs_context_projects_to_none` —
      `project_vcs(None) is None`. *(history-read-api → Test history → A
      non-repository execution has a null VCS context, not an omitted entry)*
- [x] 1.4 RED `test_projection.py::test_vcs_projection_has_no_root_field` —
      `"root" not in {f.name for f in dataclasses.fields(VcsProjection)}` —
      the exclusion is structural, not a runtime assertion. *(Lean list
      projections → `vcs_root` appears in no run list or run detail response,
      D59)*
- [x] 1.5 GREEN `projection.py` (create): `LIST_COMMIT_SUBJECT_CHARS = 120`;
      `VcsProjection` (frozen, slots dataclass, no `root` field); `project_vcs`
      per 1.1–1.4.
- [x] 1.6 RED `test_storage_types.py` (create): `Page` carries `items: tuple`
      and `has_more: bool` and no `total` field; `RunListEntry`,
      `RunDetail`, `HistoryEntry` carry the fields listed in `design.md`'s
      Interfaces/Contracts section; `MAX_PAGE_ITEMS == 200`;
      `MAX_IDENTITY_CHARS == 1024`.
- [x] 1.7 GREEN `storage.py`: add `Page`, `RunListEntry`, `RunDetail`,
      `HistoryEntry`, `MAX_PAGE_ITEMS`, `MAX_IDENTITY_CHARS`. The
      `ExecutionStore` Protocol itself is untouched in this slice.
- [x] 1.8 Gate: `uv run pytest packages/vantage/tests/test_projection.py packages/vantage/tests/test_storage_types.py packages/vantage/tests/test_architecture.py`,
      `uv run mypy .` clean; confirm `projection.py` imports nothing but
      `dataclasses` and `__future__` (the existing architecture test covers
      this; run it explicitly here as the first gate touching `vantage.core`).

## Phase 2: Both adapters — `list_runs` + `get_run_detail` (PR2)

Modifies `packages/vantage/src/vantage/storage/sqlite_store.py`,
`packages/vantage/src/vantage/storage/memory.py`,
`packages/vantage/tests/vantage_port_contract.py`,
`packages/vantage/src/vantage/core/ports/storage.py` (adds the two Protocol
methods, paired with both implementations landing in the same commit so no
adapter is ever mid-slice out of structural conformance). **Depends on PR1.**

- [x] 2.1 RED `vantage_port_contract.py::test_list_runs_orders_newest_first_with_total_tiebreak`
      — two runs sharing one `started_at`; assert `id DESC` breaks the tie,
      inherited by both `test_sqlite_store.py` and `test_memory_store.py`.
      *(D61 total order)*
- [x] 2.2 RED `..._test_list_runs_caps_at_200_items` — 201 stored runs, a page
      requested without an explicit smaller size; assert exactly 200 items
      and `has_more is True`. *(history-read-api → Bounded pagination → A
      list response never exceeds 200 items)*
- [x] 2.3 RED `..._test_list_runs_has_more_distinguishes_exhaustion_from_truncation`
      — exactly 200 stored, a page covering all 200; assert `has_more is
      False`; store one more, repeat, assert `has_more is True`. *(Bounded
      pagination → The more-items flag distinguishes truncation from
      exhaustion)*
- [x] 2.4 RED `..._test_list_runs_honors_a_smaller_requested_page_size` —
      more runs stored than a requested page size under 200; assert exactly
      that many items and `has_more is True`. *(Bounded pagination → A
      caller-requested page size under the cap is honored)*
- [x] 2.5 RED `..._test_list_runs_includes_absent_repository_run_undistinguished`
      — a run recorded with `vcs=None` alongside runs from repositories;
      assert its entry has `vcs is None` and no positional distinction.
      *(version-control-context → Absent repository → Absent repository's
      run appears in the run list)*
- [x] 2.6 RED `..._test_list_runs_bounds_commit_subject_at_display_width` — a
      run with a 200-character commit subject; assert the list entry's
      `vcs.commit_subject` is 120 characters and `commit_subject_truncated
      is True`, proving the SQL projection agrees with `project_vcs`.
      *(Lean list projections → The commit subject is bounded in list
      responses)*
- [x] 2.7 RED `..._test_list_runs_flags_capture_truncated_subject_even_when_short`
      — a run whose stored `vcs_commit_subject_truncated = 1` and a subject
      under 120 characters; assert the list entry still reports
      `commit_subject_truncated is True`. *(Lean list projections → The
      truncation flag never surfaces independently of its subject — the
      other input to the disjunction, at adapter level)*
- [x] 2.8 RED `..._test_list_runs_null_subject_flag_is_false_not_null` — a
      run with `vcs_commit_subject IS NULL`; assert
      `commit_subject_truncated is False`, never `None` — the `COALESCE`
      edge case D60 names explicitly.
- [x] 2.9 RED `..._test_get_run_detail_returns_full_untruncated_subject` — a
      run with a 200-character commit subject; assert
      `get_run_detail(...).execution.vcs.commit_subject` is the whole 200
      characters and its `commit_subject_truncated` reflects only
      capture-time truncation (unchanged meaning). *(D58, D59; Lean list
      projections' complement — the full record stays reachable)*
- [x] 2.10 RED `..._test_get_run_detail_returns_none_for_unknown_id` —
      `get_run_detail("unknown")` is `None` in both adapters.
- [x] 2.11 GREEN `storage.py`: add `list_runs(*, limit, offset) ->
      Page[RunListEntry]` and `get_run_detail(execution_id) -> RunDetail |
      None` to the `ExecutionStore` Protocol.
- [x] 2.12 GREEN `sqlite_store.py`: implement `list_runs` —
      `substr(vcs_commit_subject, 1, ?)`/`length(...)` projection (D60),
      `LIMIT min(limit, 200) + 1 OFFSET offset`, `ORDER BY started_at DESC,
      id DESC` (D61); implement `get_run_detail` reading the full row.
- [x] 2.13 GREEN `memory.py`: implement `list_runs` using `project_vcs` and
      Python sort/slice with the identical clamp and tiebreak (D57's second
      mechanism); implement `get_run_detail`.
- [x] 2.14 Gate:
      `uv run pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py`,
      `uv run mypy .` clean.

## Phase 3: Both adapters — `list_results` + `list_history` (PR3)

Same three storage files. **Depends on PR2.**

- [x] 3.1 RED `vantage_port_contract.py::test_list_history_orders_newest_first_with_full_vcs`
      — a node id that ran in multiple sessions, at least one from a
      repository with a named branch and a dirty tree; assert newest-first
      ordering and every entry carries commit, branch, commit subject,
      truncation flag, dirty flag, and duration. *(history-read-api → Test
      history → Executions return newest first, with full VCS context)*
- [x] 3.2 RED `..._test_list_history_unknown_node_id_is_empty_not_error` — a
      node id with no recorded executions; assert an empty `Page`, not an
      error. *(Test history → An unknown test yields empty history, not an
      error)*
- [x] 3.3 RED `..._test_list_history_null_vcs_entry_present_not_omitted` — an
      execution recorded outside a git repository, present among a node
      id's history; assert its entry has `vcs is None` and is present, not
      omitted. *(Test history → A non-repository execution has a null VCS
      context, not an omitted entry)*
- [x] 3.4 RED `..._test_list_history_caps_and_reports_more_like_list_runs` —
      the same 200/201 clamp and `has_more` transition as 2.2/2.3, applied
      to `list_history`. *(Bounded pagination, reused for the history
      endpoint)*
- [x] 3.5 RED `..._test_list_results_paginates_a_runs_results` —
      `list_results(execution_id, limit=..., offset=...)` respects
      limit/offset/has_more, the paginated sibling of `get_results` (D57).
- [x] 3.6 RED `..._test_list_results_empty_for_a_run_with_no_results` — an
      execution with zero results; assert an empty `Page`.
- [x] 3.7 GREEN `storage.py`: add `list_results(execution_id, *, limit,
      offset) -> Page[Result]` and `list_history(*, node_id, limit, offset)
      -> Page[HistoryEntry]` to the `ExecutionStore` Protocol — all four
      read methods now declared.
- [x] 3.8 GREEN `sqlite_store.py`: implement `list_history` — node id →
      `test_case` (unique index) → `result` (index) → `run` (primary key),
      the join D63 sizes at ~500 index-ranged rows over the benchmark
      fixture; `node_id` is always a bound parameter, never interpolated,
      matching `_resolve_test_case_ids`'s existing discipline; same
      substr/length projection and total order as `list_runs`. Implement
      `list_results` — `SELECT ... FROM result WHERE run_id = ?`, same
      clamp.
- [x] 3.9 GREEN `memory.py`: implement `list_history` and `list_results`
      mirroring the SQL behavior in Python.
- [x] 3.10 Gate:
      `uv run pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py`,
      `uv run mypy .` clean; confirm both adapters now satisfy all four new
      `ExecutionStore` methods (mypy's structural Protocol check, run against
      every call site that types a variable as `ExecutionStore`).

## Phase 4: Run list + run detail routes, liveness demonstration (PR4)

Creates `packages/vantage/src/vantage/service/routes/read.py`,
`packages/vantage/tests/test_routes_read.py`. Modifies
`packages/vantage/src/vantage/service/schemas.py`,
`packages/vantage/src/vantage/service/app.py`. **Depends on PR2.**

**Shipped as two chained PRs, `04a` and `04b`, not one.** The Phase 4 slice
as fully implemented measured 557 code+test changed lines against the
400-line per-slice review budget (design's own forecast for this slice was
~390). Per the review workload guard, the fix is a smaller slice boundary,
not thinner tests, so Phase 4 was split at the list/detail seam: `04a`
(branch `ft/read-api-04a-list-route`) delivers `GET /api/v1/runs` alone,
`04b` (branch `ft/read-api-04b-detail-liveness`, chained from `04a`)
delivers `GET /api/v1/runs/{run_id}` and the liveness demonstration tests.
The remaining phase numbering (Phase 5 onward, `ft/read-api-05-...`) is
unchanged — `04b` is still the branch Phase 5 chains from.

- [x] 4.1 RED `test_routes_read.py::test_run_list_returns_items_and_has_more_envelope`
      — `GET /api/v1/runs` returns 200, `{"items": [...], "has_more": bool}`,
      each item built field by field (assert the response schema, not
      `from_attributes` — inspection of `schemas.py` at review time, not a
      test assertion on the mechanism). *(04a)*
- [x] 4.2 RED `..._test_run_list_response_contains_no_vcs_root_anywhere` — a
      run recorded with a known `vcs_root`; assert the raw response body
      (as text) does not contain that value. *(Lean list projections →
      `vcs_root` appears in no run list or run detail response)* *(04a)*
- [x] 4.3 RED `..._test_run_list_rejects_non_positive_limit` —
      `GET /api/v1/runs?limit=0` and `?limit=-1` both answer `422`. *(D61 —
      "not a page size")* *(04a)*
- [x] 4.4 RED `..._test_run_list_caps_at_200_at_the_route` — 201 runs stored;
      a request with no `limit`; assert ≤200 items and `has_more is true`
      at the HTTP layer, not only the port. *(Bounded pagination, route
      level)* *(04a)*
- [x] 4.5 RED `..._test_run_detail_returns_full_untruncated_subject` —
      `GET /api/v1/runs/{run_id}` returns 200 with the whole stored commit
      subject and the full `VcsContext`. *(04b)*
- [x] 4.6 RED `..._test_run_detail_response_contains_no_vcs_root` — same
      substring assertion as 4.2, on the detail response body. *(04b)*
- [x] 4.7 RED `..._test_run_detail_unknown_id_is_404` —
      `GET /api/v1/runs/{unknown}` answers `404`. *(04b)*
- [x] 4.8 RED `..._test_abandoned_run_reads_back_as_abandoned` — a run
      fixture with `last_contact_at` older than `create_app`'s configured
      `grace_period_seconds`, no clock control; assert
      `GET /api/v1/runs/{run_id}` reports `"presentation": "abandoned"`.
      *(session-liveness → Abandoned run is observable → A run past its
      grace period reads back as abandoned, Demonstration)* *(04b)*
- [x] 4.9 RED `..._test_running_run_reads_back_as_running` — the same
      fixture shape with `last_contact_at` inside the grace period; assert
      `"presentation": "running"`. *(A run inside its grace period reads
      back as running)* *(04b)*
- [x] 4.10 RED `..._test_interrupted_run_reads_back_as_interrupted` — a run
      reported interrupted via Ctrl-C; assert `"presentation":
      "interrupted"`, not `"abandoned"`, regardless of staleness. *(A
      Ctrl-C interrupted run reads back as interrupted)* *(04b)*
- [x] 4.11 RED `..._test_abandonment_invents_no_stored_field` — the
      abandoned-run fixture from 4.8; read the row back directly via
      `store.get_execution` and assert `started_at` is unchanged and no
      `finished_at` value was invented. *(Abandonment invents no stored
      field)* *(04b)*
- [x] 4.12 GREEN `schemas.py`: add `RunListItemResponse` (nested VCS model
      with no `root` field), `RunListResponse`, `RunDetailResponse` (nested
      VCS model built field by field, `root` never assigned even though
      `VcsContext` carries it) — every model constructed explicitly, never
      `model_validate(execution, from_attributes=True)`. `RunVcsResponse` and
      the list-only models landed in `04a`; `RunDetailResponse` lands in
      `04b`.
- [x] 4.13 GREEN `routes/read.py` (create): `GET /api/v1/runs` and
      `GET /api/v1/runs/{run_id}`; `limit`/`offset` query parameters,
      `422` for `limit <= 0`; `derive_presentation(execution,
      last_contact_at=entry.last_contact_at, now=datetime.now(timezone.utc),
      grace=timedelta(seconds=request.app.state.grace_period))` — its first
      caller (D62). `GET /api/v1/runs` landed in `04a`; `GET
      /api/v1/runs/{run_id}` lands in `04b`.
- [x] 4.14 GREEN `app.py`: `app.include_router(read_router, prefix="/api/v1")`.
      *(04a — the mount is shared; both routes register on the same
      `router` object regardless of which commit adds them)*
- [x] 4.15 Gate: `uv run pytest packages/vantage/tests/test_routes_read.py`,
      `uv run mypy .` clean. Passed after `04a` (4 tests) and again after
      `04b` (11 tests); marked complete once the full Phase 4 test file
      exists in `04b`.

## Phase 5: Results route + history route + identity encoding (PR5)

Modifies `packages/vantage/src/vantage/service/routes/read.py`,
`packages/vantage/src/vantage/service/errors.py`,
`packages/vantage/tests/test_routes_read.py`. **Depends on PR3, PR4.**

- [ ] 5.1 RED `test_routes_read.py::test_results_route_returns_paginated_envelope`
      — `GET /api/v1/runs/{run_id}/results` returns `{"items": [...],
      "has_more": bool}`.
- [ ] 5.2 RED `..._test_results_route_unknown_run_id_is_404` —
      `GET /api/v1/runs/{unknown}/results` answers `404`, consistent with
      run detail's 404 behavior.
- [ ] 5.3 RED `..._test_history_route_returns_newest_first_with_full_vcs` —
      `GET /api/v1/tests/history?node_id=...` returns entries newest first,
      each with full VCS context. *(Test history → Executions return
      newest first, with full VCS context — route level)*
- [ ] 5.4 RED `..._test_history_route_unknown_node_id_is_empty_not_error` —
      `?node_id=<unknown>` answers `200` with zero items. *(Test history →
      An unknown test yields empty history, not an error — route level)*
- [ ] 5.5 RED `..._test_history_route_response_contains_no_vcs_root` — same
      substring assertion as 4.2/4.6, on the history response body. *(Test
      history → `vcs_root` appears in no history entry)*
- [ ] 5.6 RED `..._test_history_identity_survives_special_characters_intact`
      — **the load-bearing D54 test.** A node id containing `/`, `::`,
      `[`, `]` (e.g. `tests/test_a.py::TestSuite::test_x[case/1]`),
      percent-encoded as a query value; a fake/spy `ExecutionStore` wired
      into `create_app` in place of `InMemoryExecutionStore`; assert
      `list_history` is called with the identical, un-mangled string.
      **This proves the value survives the transport; it does NOT prove the
      routing choice.** Measured 2026-08-21 against a live uvicorn server,
      both a query parameter and `/{identity:path}` round-trip
      byte-identical under a bare ASGI transport — the disqualifier for
      `:path` is proxy-dependent slash normalization in front of the
      application, which this in-process test structurally cannot observe
      either way. Say so in the test's docstring; do not claim it proves
      more than it does.
- [ ] 5.7 RED `..._test_history_route_missing_node_id_is_422` —
      `GET /api/v1/tests/history` with no `node_id` answers `422`.
- [ ] 5.8 RED `..._test_history_route_overlong_identity_is_422_not_414` — a
      `node_id` of 1,025 characters answers a shaped `422`, never a
      proxy-generated `414`. *(D54's 1,024-character bound)*
- [ ] 5.9 GREEN `errors.py`: one rejection for a missing or over-long test
      identity (e.g. `InvalidIdentityError`, `422`), the value routed
      through the existing `safe_segment` allow-list wherever it is echoed.
- [ ] 5.10 GREEN `routes/read.py`: `GET /api/v1/runs/{run_id}/results`;
      `GET /api/v1/tests/history` with `node_id: str = Query(...,
      max_length=MAX_IDENTITY_CHARS)`, calling `store.list_history`.
- [ ] 5.11 Gate: `uv run pytest packages/vantage/tests/test_routes_read.py`,
      `uv run mypy .` clean.

## Phase 6: The interface document (PR6)

Creates `packages/vantage/src/vantage/service/openapi/__init__.py`,
`packages/vantage/src/vantage/service/openapi/v1.yaml`,
`packages/vantage/tests/test_interface_document.py`. Modifies
`packages/vantage/src/vantage/service/app.py`, `pyproject.toml` (workspace
root), `docs/api/v1-ingestion.md`. **Depends on PR4, PR5.**

- [ ] 6.1 RED `test_interface_document.py::test_openapi_yaml_serves_the_handwritten_bytes`
      — `GET /api/v1/openapi.yaml` returns the exact bytes loaded via
      `importlib.resources.files("vantage.service.openapi")`,
      `media_type == "application/yaml"`; the body is parsed with `pyyaml`
      only inside this test, never at runtime. *(api-interface-document →
      Machine-readable interface document)*
- [ ] 6.2 RED `..._test_generated_documents_are_disabled` —
      `GET /openapi.json`, `GET /docs`, `GET /redoc` each answer `404`.
      *(The generated interface documents are disabled)*
- [ ] 6.3 RED `..._test_a_served_but_undocumented_route_is_reported` — the
      drift check over `mounted - declared` is empty against the real app;
      then, in a second app built with one extra test-only route not
      declared in the document, assert the check reports it — **the
      falsifier**: the check must be able to fail. *(A served-but-
      undocumented endpoint is reported; The document is not derived from
      the route table it checks)*
- [ ] 6.4 RED `..._test_a_documented_but_unserved_path_is_reported` — the
      reverse direction, `declared - mounted`, is empty against the real
      app; assert it is computed and would report a mismatch (not only the
      one direction the scenario names).
- [ ] 6.5 RED `..._test_every_read_operation_is_get_and_every_write_operation_is_not`
      — the document's `read`-tagged operations are all `GET`; its
      `write`-tagged operations are not; the two session-report and
      heartbeat endpoints are tagged `write`. *(D53 consistency check;
      session-ingestion → Ingestion endpoints are marked as writing, not
      reading)*
- [ ] 6.6 RED `..._test_every_documented_path_answers_2xx` — a binding table
      maps every `(path, method)` the document declares to a callable
      producing valid parameters against a dedicated fixture database
      (separate from PR7's read-only fixture, per D65); assert every
      response is `2xx`, including `GET /api/v1/capabilities` and
      `GET /api/v1/openapi.yaml` themselves. *(Every documented path
      answers 2xx)*
- [ ] 6.7 GREEN `openapi/__init__.py` (create): the `importlib.resources`
      anchor module.
- [ ] 6.8 GREEN `openapi/v1.yaml` (create): hand-written OpenAPI 3.1 YAML
      covering every mounted path — `POST /api/v1/runs`,
      `POST /api/v1/runs/{run_id}/heartbeat` tagged `write`;
      `GET /api/v1/runs`, `GET /api/v1/runs/{run_id}`,
      `GET /api/v1/runs/{run_id}/results`, `GET /api/v1/tests/history`,
      `GET /api/v1/capabilities`, `GET /api/v1/openapi.yaml` tagged `read`.
- [ ] 6.9 GREEN `routes/read.py` (or `app.py`): `GET /api/v1/openapi.yaml`
      route serving the anchored bytes as-is.
- [ ] 6.10 GREEN `app.py`: `FastAPI(openapi_url=None, docs_url=None,
      redoc_url=None)`.
- [ ] 6.11 GREEN `pyproject.toml` (workspace root): add `pyyaml` to the
      `dev` optional-dependencies group only — never a runtime dependency
      of `vantage`. Run `uv run deptry .`; confirm it passes with no new
      ignore entry needed, because `pyyaml` is a genuine `import yaml`
      inside `test_interface_document.py`, unlike the CLI-only dev tools
      `DEP002` already ignores.
- [ ] 6.12 Modify `docs/api/v1-ingestion.md`: remove the request-shape
      example and the response-status table (now stated by the document
      itself); add a header line naming `openapi/v1.yaml` as the contract
      and this file as the reasoning; keep the `extra=` asymmetry
      explanation, the `<unnamed>` allow-list rationale, and the
      "nothing written before 201/200" guarantee — one source per fact
      (D56).
- [ ] 6.13 Gate: `uv run pytest packages/vantage/tests/test_interface_document.py`,
      `uv run mypy .` clean, `uv run deptry .` clean.

## Phase 7: Read-only proof, latency, ADR-0015, OQ-9 (PR7)

Creates `packages/vantage/tests/test_read_only_surface.py`,
`scripts/measure_history_latency.py`. Modifies
`openspec/changes/read-api/specs/history-read-api/spec.md` (Measurements
paragraph), `docs/adr/0015-scope-the-read-only-guarantee-to-a-named-read-surface.md`,
`docs/open-questions.md`. **Depends on PR6 — hard ordering constraint: the
binding table's call list comes from `openapi/v1.yaml`, which does not exist
until PR6 merges.**

- [ ] 7.1 RED `test_read_only_surface.py::test_a_writing_endpoint_tagged_read_fails_the_harness`
      — **the falsifier.** A test-local copy of the binding table with
      `POST /api/v1/runs` temporarily registered as if it were `read`;
      assert the digest-pair harness reports a mismatch rather than passing
      — proves the check is not vacuously green before it is trusted with
      the real document.
- [ ] 7.2 RED `..._test_logical_content_digest_unchanged_after_every_read_path`
      — a database holding at least one run and its results; every
      `read`-tagged path called in sequence; assert the logical content
      digest (tables enumerated from `sqlite_master`, `SELECT * FROM
      <table> ORDER BY rowid` per table, sha256) is identical before and
      after, and `count_executions()`/`count_results()` are unchanged.
      *(Read-only read surface → Reading leaves stored data unchanged)*
- [ ] 7.3 RED `..._test_main_file_digest_stable_despite_wal_checkpointing` —
      the fixture writer's store is closed before the read store opens (WAL
      already checkpointed and removed); the read store stays open across
      both digests, taken before and after the same read sequence; assert
      the main `.db` file's digest is identical — `-wal`/`-shm` are never
      digested. *(The main-file digest is stable despite WAL checkpointing)*
- [ ] 7.4 RED `..._test_every_read_path_has_a_binding` — every `read`-tagged
      `(path, method)` in the document has an entry in the binding table;
      a path added without one fails this test rather than being silently
      skipped.
- [ ] 7.5 GREEN `test_read_only_surface.py`: implement the binding table
      (one callable per `read`-tagged path, its own fixture database
      distinct from PR6's 2xx-check fixture, per D65) and the digest-pair
      harness satisfying 7.1–7.4.
- [ ] 7.6 Inspection: record, in a comment beside the traceback/captured-
      output assertion this change does NOT add, that `Lean list
      projections`' traceback-exclusion half stays unfailable because
      `result.traceback` has no writer yet — state this honestly rather
      than manufacturing a green assertion. *(Lean list projections → List
      responses exclude traceback and captured output — Inspection)*
- [ ] 7.7 Analysis: write `scripts/measure_history_latency.py`, following
      `scripts/measure_vcs_overhead.py`'s shape — a fixture of 500 runs ×
      200 results (100,000 results), ~200 distinct node ids, one target
      test present in all 500 runs and a second present in one run only,
      built through `record_session` via the real SQLite adapter (never
      hand-written `INSERT`s); drives the ASGI app in-process, no socket;
      5 warm-up requests discarded, then 200 timed with
      `time.perf_counter_ns`; nearest-rank p95, stated in the output; also
      reports the slowest single response; also re-runs
      `scripts/measure_vcs_overhead.py`'s 10 ms profile (D63).
- [ ] 7.8 Analysis: run the harness by hand (never in CI); transcribe the
      p95, the max, and the re-run 10 ms-profile overhead numbers as a
      **Measurements** paragraph under `history-read-api` → *Test history
      latency* in `openspec/changes/read-api/specs/history-read-api/spec.md`,
      alongside D63's ≈55 ms headroom figure; state that a future change
      to the history query or its indexes MUST re-run the script. *(p95
      and max latency are measured and committed as numbers)*
- [ ] 7.9 Demonstration: confirm, through the live `GET /api/v1/runs`
      endpoint, that a run recorded outside a git repository appears in the
      list alongside runs from repositories, all six VCS fields null, no
      positional distinction — the promotion `version-control-context` →
      *Absent repository* records from Inspection to Test.
- [ ] 7.10 Write `docs/adr/0015-scope-the-read-only-guarantee-to-a-named-read-surface.md`:
      flip `Status: Proposed` → `Status: Accepted` — do this at merge, not
      at PR-open time.
- [ ] 7.11 Update `docs/open-questions.md`: OQ-9 → Answered, bound to
      ADR-0015.
- [ ] 7.12 Full gate: `uv run pytest packages/vantage/tests/test_read_only_surface.py`,
      then the whole suite `uv run pytest`, `uv run mypy .`,
      `uv run deptry .`, `uv run ruff format . && uv run ruff check --fix .`;
      confirm `git diff` shows zero changes to `schema.sql` for the entire
      `read-api` change (D63); state explicitly which of the 3.10–3.13
      matrix legs, the networking-disabled job, and the clean-environment
      install check ran locally versus are left to CI — do not claim a
      matrix run that did not happen.

---

## Scenario coverage — all 26 spec scenarios

| # | Scenario | Capability | Task |
|---|----------|-----------|------|
| 1 | Reading leaves stored data unchanged | history-read-api | 7.2 |
| 2 | The main-file digest is stable despite WAL checkpointing | history-read-api | 7.3 |
| 3 | Executions return newest first, with full VCS context | history-read-api | 3.1 (contract), 5.3 (route) |
| 4 | An unknown test yields empty history, not an error | history-read-api | 3.2 (contract), 5.4 (route) |
| 5 | A non-repository execution has a null VCS context, not an omitted entry | history-read-api | 3.3 |
| 6 | `vcs_root` appears in no history entry | history-read-api | 5.5 |
| 7 | List responses exclude traceback and captured output (Inspection) | history-read-api | 7.6 |
| 8 | The commit subject is bounded in list responses | history-read-api | 2.6 |
| 9 | The truncation flag never surfaces independently of its subject | history-read-api | 2.7, 2.8 |
| 10 | `vcs_root` appears in no run list or run detail response | history-read-api | 1.4, 4.2, 4.6 |
| 11 | A list response never exceeds 200 items | history-read-api | 2.2 (contract), 4.4 (route) |
| 12 | The more-items flag distinguishes truncation from exhaustion | history-read-api | 2.3 |
| 13 | A caller-requested page size under the cap is honored | history-read-api | 2.4 |
| 14 | p95 and max latency are measured and committed as numbers | history-read-api | 7.8 |
| 15 | Every documented path answers 2xx | api-interface-document | 6.6 |
| 16 | A served-but-undocumented endpoint is reported | api-interface-document | 6.3 |
| 17 | The generated interface documents are disabled | api-interface-document | 6.2 |
| 18 | The document is not derived from the route table it checks | api-interface-document | 6.3, 6.4 |
| 19 | Ingestion endpoints are marked as writing, not reading | session-ingestion | 6.5 |
| 20 | Not a git repository records nulls | version-control-context | already verified — `vcs-capture` (archived) `test_vcs.py::test_not_a_repository_records_nulls_and_no_warning`; unchanged by this delta; re-confirmed by 7.12's full-suite gate |
| 21 | Absent repository emits no warning | version-control-context | same as #20 — 7.12 |
| 22 | Absent repository's run appears in the run list | version-control-context | 2.5 (contract), 7.9 (Demonstration through the live list — the promotion this change makes) |
| 23 | A run past its grace period reads back as abandoned | session-liveness | 4.8 |
| 24 | A run inside its grace period reads back as running | session-liveness | 4.9 |
| 25 | A Ctrl-C interrupted run reads back as interrupted | session-liveness | 4.10 |
| 26 | Abandonment invents no stored field | session-liveness | 4.11 |

**All 26 spec scenarios trace to at least one task.** Scenarios 20 and 21 are
pre-existing `vcs-capture` behavior this delta does not modify — its own
`Absent repository` requirement changes only the run-list criterion (#22) —
so they trace to the already-shipped, unchanged tests plus this change's own
full-suite regression gate, not a newly authored RED task. Scenario 7 is
Inspection, not Test, and its task records that honestly rather than
asserting something that cannot fail today.

## Architecture and process notes carried into every gate

- `vantage.core` (`projection.py`) imports nothing beyond the standard
  library; `vantage.storage` imports the core only; Pydantic and FastAPI
  never appear outside `vantage.service`. The existing AST architecture test
  enforces this at every gate.
- No new class introduced by this change (`VcsProjection`, `Page`,
  `RunListEntry`, `RunDetail`, `HistoryEntry`) starts with `Test`.
- Commits are Conventional Commits; the change name goes in the commit body,
  never the subject.
- `packages/pytest-vantage/**` is untouched by every task above.
