# Tasks: User-configuration surface (test sections as first tenant)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,090 (design.md D90, per-file derivation) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 → PR 4 |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High
```

Cached by the orchestrator (auto-chain resolves the decision): proceed straight
to `sdd-apply` with the four slices below as chained PRs against a draft
tracker branch. Slice 2 has no dependency and may land before Slice 1; Slices
3 and 4 cannot precede 1 (a route cannot call a port method that does not
exist without breaking `mypy --strict`), and 4 also needs 2 and 3.

```
tracker (draft, no-merge)
  └─ PR1 storage foundation           📍 (or PR2, order-flexible)
       └─ PR2 core sections domain    📍 (or PR1, order-flexible)
            └─ PR3 definitions API (needs PR1)
                 └─ PR4 aggregate endpoint (needs PR1+PR2+PR3)
```

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `user_setting`, schema v3, port methods, both adapters | PR 1 (base: tracker) | `uv run --extra dev pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py` | `uv run --extra dev pytest packages/vantage/tests/test_sqlite_store.py -k version` (real SQLite file, ADR-0013 refusal) | Revert branch: drop `user_setting`, the four port methods, both adapter bodies; `_SCHEMA_VERSION` back to 2 |
| 2 | `core/domain/sections.py`, pure, no I/O | PR 2 (base: tracker; order-flexible with PR1) | `uv run --extra dev pytest packages/vantage/tests/test_sections.py` | N/A — pure stdlib function, nothing to run live | Revert branch: delete `core/domain/sections.py` and its test file; nothing else imports it yet |
| 3 | Definitions API: CRUD routes | PR 3 (base: PR 1) | `uv run --extra dev pytest packages/vantage/tests/test_routes_sections.py packages/vantage/tests/test_interface_document.py` | `uv run --extra dev pytest packages/vantage/tests/test_routes_sections.py` — ASGI `TestClient` against `InMemoryExecutionStore` | Revert branch: delete `routes/sections.py`, its schemas/errors/openapi entries, unregister the router |
| 4 | Aggregate: `GET /runs/{run_id}/sections` | PR 4 (base: PR 3) | `uv run --extra dev pytest packages/vantage/tests/test_routes_sections.py` | Same ASGI `TestClient`, worked-example run fixture | Revert branch: delete the aggregate handler, response models, its openapi/binding entries |

## Phase 1 (Slice 1): Storage foundation — user_setting, schema v3, port, adapters

- [x] 1.1 RED: add failing `vantage_port_contract.py` cases for `list_settings`/`upsert_setting`/`delete_setting`/`get_run_case_outcomes` — create, replace-not-duplicate, delete-then-absent, `ORDER BY key`, empty-run pairs (spec user-configuration: create/replace/delete/parity)
- [x] 1.2 RED: add failing test in `test_sqlite_store.py` — a v2-stamped db is refused with version-found/required/path, no DDL issued (spec recording-schema: refusal, no-alter)
- [x] 1.3 GREEN: add `user_setting` table to `storage/schema.sql`, header count 10→11, stamp `'3'` (D82)
- [x] 1.4 GREEN: `_SCHEMA_VERSION = 3` in `storage/connection.py` (D82)
- [x] 1.5 GREEN: add `UserSetting` dataclass + four `Protocol` methods to `core/ports/storage.py` (D83, D86)
- [x] 1.6 GREEN: implement the four SQL constants/methods in `storage/sqlite_store.py`, `updated_at` via `_fixed_width_isoformat` (D86)
- [x] 1.7 GREEN: implement the same four methods in `storage/memory.py` (`dict[tuple[str,str], UserSetting]`, `sorted()`) (D86)
- [x] 1.8 Verify: 1.1/1.2 now green on both adapters
- [x] 1.9 GREEN: update `docs/schema-manifest.md` — `### user_setting` section, ten→eleven, indexes unchanged at fourteen, `meta` stamp `3` (D82, RQ-29 Inspection; spec recording-schema: fresh db matches manifest)
- [x] 1.10 Verify (Inspection): open an existing v2 db with new code, confirm zero schema-altering statements issued
- [x] 1.11 Verify: `git diff --stat packages/pytest-vantage` is empty (RQ-24, ADR-0009)

## Phase 2 (Slice 2): Core section domain — pure, stdlib only

- [x] 2.1 RED: `test_sections.py` — `normalize_prefix` coercion/idempotence; `tests/SectA` never matches `tests/SectAlpha/test_x.py` (spec test-sections: coercion, sibling non-bleed)
- [x] 2.2 RED: same file — `derive_section` longest-wins, no-match→`UNASSIGNED`, alphabetical tie-break, case sensitivity (spec test-sections: longest-prefix-wins)
- [x] 2.3 RED: same file — `summarize_sections`: worked example → 94.4; `measured == 0` → `None`; `total - measured` == skipped; items alphabetical; `unassigned` present-when-empty (spec test-sections: pass percentage, ordering, unassigned bucket)
- [x] 2.4 GREEN: create `core/domain/sections.py` — `UNASSIGNED`, three bounds, `SectionDefinition`, `normalize_prefix`, `derive_section`, `SectionSummary`, `RunSectionSummary`, `summarize_sections` (D84, D85)
- [x] 2.5 Verify: 2.1–2.3 green; AST architecture test confirms the module imports nothing outside stdlib (RQ-26)

## Phase 3 (Slice 3): Definitions API — depends on Slice 1

**Budget split (apply-time discovery).** The full slice measured 472 changed
lines against `ft/user-configuration-02-sections-domain`, over the 400-line
hard ceiling. Split into 3a (committed, `0233e8b`) and 3b (implemented,
verified green, **uncommitted** — sitting in the working tree pending a
chain/exception decision, since committing it as-is would still put the
branch at 472 lines). See `apply-progress` for the measured breakdown.

- [x] 3.1 RED: `test_routes_sections.py` — POST 201/200; trailing-slash coercion; empty/whitespace name rejected; `Unassigned`/`UNASSIGNED` rejected case-insensitively; over-length name/prefix rejected; `too_many_sections` at the bound; DELETE 204 then 404 `unknown_section`; GET lists an upserted section; a CR/LF + `</script>` name is rejected without appearing in the body; a quoting-shaped name round-trips byte-identically (spec test-sections: name constraints, trailing slash, listed section; design.md threat notes) — **3b, uncommitted**
- [x] 3.2 GREEN: six rejection classes in `service/errors.py` — `InvalidSectionNameError`, `ReservedSectionNameError`, `InvalidSectionPrefixError`, `UnknownSectionError`, `TooManySectionsError`, `UnreadableSettingError`; update `__all__` (D89) — **3a, committed**
- [x] 3.3 GREEN: `SectionValue`, `SectionUpsertRequest`, `SectionResponse`, `SectionListResponse` in `service/schemas.py` — **3a, committed**
- [x] 3.4 GREEN: create `service/routes/sections.py` — `TEST_SECTIONS_NAMESPACE` constant, definition loader, GET/POST/DELETE `/config/sections` (D87, D89); typed `store: ExecutionStore = request.app.state.store` — **3b, uncommitted**
- [x] 3.5 GREEN: `include_router(sections_router, prefix="/api/v1")` in `service/app.py` — **3b, uncommitted**
- [x] 3.6 GREEN: hand-write the three operations into `service/openapi/v1.yaml`, `read`/`write` tagged (D87) — **3b, uncommitted**
- [x] 3.7 GREEN: add the GET `read` path's binding-table entry in `test_read_only_surface.py` — **3b, uncommitted**
- [x] 3.8 Verify: 3.1 green; `test_interface_document.py` and `test_read_only_surface.py` still pass — verified with 3a+3b together (587 passed, whole workspace); 3b's own diff not yet committed

## Phase 4 (Slice 4): Run aggregate — depends on Slices 1, 2, 3

- [x] 4.1 RED: `test_routes_sections.py` — `GET /runs/{run_id}/sections` 404 `unknown_run`; worked example → 94.4%; `sum(items.total)+unassigned.total == run result count` over a run with unmatched results; renaming re-groups with zero writes to run/result rows; a malformed stored `value` → 500 `unreadable_setting` (spec test-sections: run summary, unassigned reconciles, renaming)
- [x] 4.2 GREEN: `SectionSummaryResponse`, `RunSectionSummaryResponse` in `service/schemas.py`
- [x] 4.3 GREEN: wire `get_run_case_outcomes` + `summarize_sections` into the new `GET /runs/{run_id}/sections` handler in `routes/sections.py`; definitions read on every request, never cached (D85, D87, D88)
- [x] 4.4 GREEN: hand-write the fourth operation into `v1.yaml`, `read`-tagged
- [x] 4.5 GREEN: add its binding-table entry in `test_read_only_surface.py`
- [x] 4.6 Verify: 4.1 green; identity and worked-example assertions hold

## Phase 5: Cross-cutting gates (chain-final, after PR 4)

- [x] 5.1 `uv run mypy --strict .` clean across all four slices
- [x] 5.2 `uv run --extra dev ruff format . && ruff check --fix .` clean
- [x] 5.3 `uv run deptry .` clean — no new third-party import in `pytest-vantage`, `vantage.core`, or `vantage.storage` (RQ-24, RQ-26)
- [x] 5.4 `uv run --extra dev pytest` green, whole workspace
- [x] 5.5 `git diff --stat packages/pytest-vantage` empty, repeated as the chain-final gate (RQ-24, ADR-0009)
