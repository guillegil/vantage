# Apply Progress: User-configuration surface (test sections as first tenant)

Full TDD-cycle evidence, work-unit evidence, and deviation notes are recorded
in Engram under topic key `sdd/user-configuration/apply-progress` (this file
is the hybrid-mode filesystem half; kept short to stay inside the slice's
400-line review budget).

## Slice 1 (storage foundation) — 11/11 tasks complete

All of tasks 1.1–1.11 in `tasks.md` are done and checked off there. Summary:

- Schema bumped to v3 (`user_setting` table added); `docs/schema-manifest.md`
  updated to eleven tables, fourteen indexes unchanged.
- `UserSetting` + four `ExecutionStore` Protocol methods added, implemented in
  both `SqliteExecutionStore` and `InMemoryExecutionStore`.
- Contract parity proven in `vantage_port_contract.py`; a new SQLite test pins
  the real v2→v3 refusal (version found/required/path, zero DDL).
- Full suite: 559 passed. `mypy --strict`, `ruff`, `deptry` clean.
  `git diff --stat packages/pytest-vantage` empty.
- Branch diff vs. `ft/user-configuration`: 363 changed lines (this file
  included), under the 400 budget.

## Slice 2 (core sections domain) — 5/5 tasks complete

All of tasks 2.1–2.5 in `tasks.md` are done and checked off there. Summary:

- New `core/domain/sections.py`: `UNASSIGNED` sentinel, three bounds,
  `SectionDefinition`, `normalize_prefix`, `derive_section`,
  `SectionSummary`, `RunSectionSummary`, `summarize_sections`.
- Pure, stdlib-only, no dependency on slice 1. `test_sections.py`: 14 new
  tests, RED-first (`ModuleNotFoundError` confirmed before GREEN).
- `test_architecture.py`'s RQ-26 walk passed unmodified (auto-discovers
  the new module).
- Full suite: 573 passed. `mypy --strict`, `ruff`, `deptry` clean.
  `git diff --stat packages/pytest-vantage` empty.
- Branch diff vs. `ft/user-configuration-01-storage-foundation`: 360
  changed lines, under the 400 budget.

## Slice 3 (definitions API) — 8/8 tasks complete; shipped as two branches after a budget split

All of 3.1–3.8 are implemented and green together (587 passed, whole
workspace; `mypy --strict`, `ruff`, `deptry` clean). Measured against
`ft/user-configuration-02-sections-domain`: **472 changed lines, over the
400 ceiling** (estimate was ~330 — same overrun pattern as slices 1 and 2).
RED confirmed first via `git stash` (all 14 new tests failed 404, restored
before GREEN).

Per the budget guard, split into two units instead of one oversized commit:

- **3a — committed (`0233e8b`)**: `service/errors.py` (six rejection
  classes) + `service/schemas.py` (four models). 126 lines. Purely
  additive, no route wires them up yet, verified standalone (573 passed,
  matching slice 2's count — no regression) before committing.
- **3b — committed (`b0104f6`, PR #85, targets 3a)**: `service/routes/sections.py`
  (new), `service/app.py` (router wiring), `service/openapi/v1.yaml` (three
  operations + three schemas), `test_routes_sections.py` (new, 14 tests),
  `test_interface_document.py` (+3 bindings, +3 schema entries —
  undiscovered obligation: adding operations to `v1.yaml` makes its
  `test_every_documented_path_answers_2xx` binding table stale unless
  extended, beyond the two obligations design.md names), `test_read_only_surface.py`
  (+1 binding). 346 lines.

**Resolved.** Apply stopped before committing the overflow, as instructed, and
the orchestrator opened a genuinely separate child branch. Splitting commits
inside one branch would not have reduced anything: a PR's review diff is its
whole branch, so only a second branch brings it under budget. Both PRs are
green and under the ceiling.

## Slice 4 (run aggregate) — 6/6 tasks complete, plus Phase 5 gates

Branch `ft/user-configuration-04-run-aggregate`, off
`ft/user-configuration-03b-section-routes` (3b landed there, `b0104f6`).
Tasks 4.1–4.6 done: `GET /runs/{run_id}/sections` wired into
`routes/sections.py`, reading `store.get_run_case_outcomes` and calling
`summarize_sections` fresh on every request (no caching, D88).
`SectionSummaryResponse`/`RunSectionSummaryResponse` added to
`schemas.py`, the fourth operation hand-written into `v1.yaml`
(`read`-tagged), and all three obligations extended: `test_read_only_surface.py`'s
binding table, plus `test_interface_document.py`'s `ordered_bindings` and
`_RESPONSE_SCHEMAS` (the third obligation slice 3 discovered).

RED confirmed first: all five new tests failed with a plain 404 (no route
mounted) before the handler existed. Both published identities are tested
directly through the live route, not just the core: worked example (94.4%),
`sum(items.total) + unassigned.total == run result count` over unmatched
results, and the load-bearing rename test — `store.get_results`/
`get_execution` asserted byte-identical before and after a rename plus
delete, proving zero writes to any run/result row.

Full suite: 592 passed. `mypy --strict`, `ruff`, `deptry` clean.
`git diff --stat packages/pytest-vantage` empty. Branch diff vs.
`ft/user-configuration-03b-section-routes`: **317 changed lines** (297
insertions + 20 deletions), under the 400 budget. This entry first recorded
263, measured before the final documentation commit; 317 is the branch total
and is the number the budget is judged against.

## Status

Chain complete: slices 1, 2, 3a, 3b and 4 all implemented, committed (or
ready to commit), and green together. Phase 5's cross-cutting gates all
pass on this branch, chain-final.
