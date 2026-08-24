# Tasks: Failure Capture

**Change:** `failure-capture` · Strict TDD — every implementation task is
preceded by its failing test task. Test command: `uv run pytest`.

**No numeric requirement identifiers are minted** (CLAUDE.md, decided
2026-08-18). Every obligation below is traced by **capability** and
**requirement/scenario name** — `failure-evidence` → *Traceback capture
invariant to display flags*, and so on — matching the five delta specs and
`design.md`'s D68–D81, none of which carry a numeric identifier. Pre-existing
code this change touches keeps its old `RQ-xx` markers untouched, not new.

**Hard constraints carried into every task below:** `schema.sql` is
byte-unchanged and `meta.schema_version` stays `'2'` — no task issues a
schema statement. A `Protocol` method never lands without both
`InMemoryExecutionStore` and `SqliteExecutionStore` implementing it in the
same commit. No new class starts with `Test`. `pytest-vantage` imports
nothing beyond pytest and the standard library (RQ-24); `vantage.core`
imports nothing; `vantage.storage` imports the core only; Pydantic and
FastAPI stay inside `vantage.service`. Every traceback, path and message in
any fixture is invented — synthetic data only, public repository.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~3,160 across nine slices, carried forward from `design.md`'s own table (300/340/390/290/370/360/390/390/330) — not independently recomputed here |
| 400-line budget risk | Low per slice as designed; **Slices 3, 7 and 8 carry no slack (~390 each)** — see fallback seams below; High for the change as a whole, which is why it is chained |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → … → PR 9 |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High
```

**Fallback split seams, only if measured lines exceed 400 (do not pre-split
speculatively):**
- **Slice 3** (rendering/extraction): split at the D69 full-extraction set
  (tasks 3.1–3.5, 3.9–3.11) vs the D70 non-exception branch table
  (3.6–3.8) — `3a`/`3b`, chained the same way `read-api` split Phase 4 into
  `04a`/`04b` after real measurement.
- **Slice 7** (storage): split into `7a` (insert/select column widening +
  the unrefused-existing-database test) and `7b` (`get_result` +
  `list_results`' `FailureProjection` + the contract scenarios) — **both
  halves still land port-and-both-adapters together**, never split by
  adapter.
- **Slice 8** (read surface): split the single-result route (`8.3`–`8.9`)
  from the list-projection/sentinel work (`8.1`–`8.2`, `8.10`–`8.16`),
  same precedent.

### Dependency diagram

```
ft/failure-capture (tracker, draft, no-merge)
  └─ PR1 ft/failure-capture-01-domain-types
       └─ PR2 ft/failure-capture-02-evidence-collector-registration
            └─ PR3 ft/failure-capture-03-rendering-extraction
                 └─ PR4 ft/failure-capture-04-captured-output
                      └─ PR5 ft/failure-capture-05-budget
                           └─ PR6 ft/failure-capture-06-ingestion   ◄─ needs PR5's plugin (D75 test fixture)
                                └─ PR7 ft/failure-capture-07-storage
                                     └─ PR8 ft/failure-capture-08-read-surface   ◄─ needs PR7's get_result
                                          └─ PR9 ft/failure-capture-09-measurements-docs
```

Every child PR marks its own position with 📍 in its description, per
`chained-pr`. **Two hard ordering constraints** (design.md, not a
preference): PR5 before PR6 — the D75 ingestion test needs a plugin able to
produce the dropped-field shape; PR7 before PR8 — the single-result route
cannot be written before `get_result` exists. Every other adjacent pair is
sequenced for reviewability only.

### Suggested Work Units

| Unit | Goal | PR (base) | Est. lines | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|---|
| 1 | Domain types: `FailureEvidence`, `CapturedOutput`, `FailureProjection`, `project_failure` (D76, D77) | PR1 (tracker) | ~300 | `uv run pytest packages/vantage/tests/test_projection.py packages/vantage/tests/test_result.py` | N/A — nothing calls these types yet | Delete the new dataclasses/functions and their tests; nothing references them |
| 2 | `EvidenceCollector` on controller+workers; the opt-out and its monotonicity (D68, D72) | PR2 (PR1) | ~340 | `uv run pytest packages/pytest-vantage/tests/test_xdist_guard.py packages/pytest-vantage/tests/test_evidence.py packages/pytest-vantage/tests/test_config.py` | `pytester.runpytest_subprocess(..., "-n", "2")` — the xdist proof for D68 | Revert `evidence.py`, the `plugin.py` worker branch and the two option registrations; `Recorder` unaffected |
| 3 | Rendering + D70 branch table + phase selection (D69, D70) | PR3 (PR2) | ~390 | `uv run pytest packages/pytest-vantage/tests/test_evidence.py packages/pytest-vantage/tests/test_capture.py` | `pytester`, parametrised over `--tb=no`/`--tb=line` | Revert `_extract`'s real body back to the PR2 stub; registration (PR2) stays working |
| 4 | Captured output, empty≠absent, phase concatenation (D71) | PR4 (PR3) | ~290 | `uv run pytest packages/pytest-vantage/tests/test_evidence.py packages/pytest-vantage/tests/test_capture.py` | `pytester`, `-s` vs default capture | Revert the capture-read lines; failure fields (PR3) keep working |
| 5 | Budget: constants, pinning test, one-pass spend, drop semantics (D73, D74) | PR5 (PR4) | ~370 | `uv run pytest packages/pytest-vantage/tests/test_report_budget.py` | `pytester` + `vantage_test_server` — a many-large-failures session, asserting body stays under the cap | Revert the `spend_failure_text_budget` call in `recorder.py`; fields ship untruncated by budget again |
| 6 | Ingestion: `ResultReport` optional fields, the flag disjunction, `_to_result` (D75) | PR6 (PR5) | ~360 | `uv run pytest packages/vantage/tests/test_schemas.py packages/vantage/tests/test_routes_runs.py packages/vantage/tests/test_ingestion.py` | ASGI `TestClient`, in-process, both directions of version skew | Revert `_to_failure_evidence`/`_to_captured_output` and the new `ResultReport` fields; storage (PR7) not yet built, so nothing downstream breaks |
| 7 | Storage: both adapters, `get_result`, port contract, unrefused-DB test (D76–D78) | PR7 (PR6) | ~390 | `uv run pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py` | N/A — no route calls the store yet; contract suite is the runtime proof against both real adapters | Revert the widened insert/select and `get_result`/`ResultListEntry`; PR6's mapping stays uncalled by storage |
| 8 | Read surface: single-result route, list projection, `v1.yaml`, read-only binding (D76, D78) | PR8 (PR7) | ~390 | `uv run pytest packages/vantage/tests/test_routes_read.py packages/vantage/tests/test_interface_document.py packages/vantage/tests/test_read_only_surface.py` | `TestClient` against `InMemoryExecutionStore`, sentinel-substring assertion on raw bytes | Revert the new route and the two response models; `get_result` (PR7) stays implemented but uncalled |
| 9 | Measurements re-run, overhead script, docs, ADR-0016 → Accepted, OQ-11 (D79–D81) | PR9 (PR8) | ~330 | `uv run pytest` (whole suite) | `uv run python scripts/measure_failure_capture_overhead.py` — manual, never collected by CI | Revert deletes the script and the transcribed Measurements paragraphs; behavior unaffected, only proof and docs revert |

---

## Phase 1: Domain types (PR1)

Modifies `packages/vantage/src/vantage/core/domain/result.py`,
`packages/vantage/src/vantage/core/domain/projection.py`. Extends
`packages/vantage/tests/test_result.py`, `test_projection.py`. Stdlib only
(RQ-26) — the existing architecture test still applies.

- [x] 1.1 RED `test_projection.py::test_project_failure_bounds_message_to_200_chars_and_flags` — a `FailureEvidence` with a 300-char `failure_message`, `failure_message_truncated=False`; `project_failure` returns a `FailureProjection` bounded to `LIST_FAILURE_MESSAGE_CHARS` with the flag set. *(D76)*
- [x] 1.2 RED `..._test_project_failure_flag_survives_a_short_capture_truncated_message` — a short message with `failure_message_truncated=True` on the input; assert the projection flag stays `True` — the disjunction at the domain layer. *(history-read-api → Lean list projections → truncation flag never surfaces independently)*
- [x] 1.3 RED `..._test_failure_projection_excludes_the_heavy_fields_structurally` — `{f.name for f in dataclasses.fields(FailureProjection)}` has no `traceback`, `failure_repr` or any captured-output field. *(D76)*
- [x] 1.4 RED `..._test_project_failure_of_none_is_none` — `project_failure(None) is None`.
- [x] 1.5 GREEN `projection.py`: `LIST_FAILURE_MESSAGE_CHARS = 200`; `FailureProjection` (frozen, slots); `project_failure`.
- [x] 1.6 RED `test_result.py::test_result_gains_failure_and_captured_fields` — `dataclasses.fields(Result)` includes `failure: FailureEvidence | None` and `captured: CapturedOutput`; `FailureEvidence` carries its 13 named fields, `CapturedOutput` its 4. *(D77)*
- [x] 1.7 GREEN `result.py`: `FailureEvidence`, `CapturedOutput` dataclasses; `Result` gains `failure`/`captured`.
- [x] 1.8 Gate: `uv run pytest packages/vantage/tests/test_projection.py packages/vantage/tests/test_result.py packages/vantage/tests/test_architecture.py`; `uv run mypy .` clean; confirm both files import only `dataclasses`/`__future__`.

## Phase 2: `EvidenceCollector` registration and the opt-out (PR2)

Creates `packages/pytest-vantage/src/pytest_vantage/evidence.py`,
`packages/pytest-vantage/tests/test_evidence.py`,
`packages/pytest-vantage/tests/test_config.py`. Modifies `plugin.py`,
`config.py`, `test_xdist_guard.py` (extends the worker-config doubles),
`test_opt_in.py`. **No rendering yet — `_extract` is a stub.**

- [ ] 2.1 RED `test_xdist_guard.py` — extend the worker double with `getoption("vantage") → True`; assert `pytest_configure` registers exactly one plugin of type `EvidenceCollector` on the worker (import fails: `evidence.py` doesn't exist). **The highest-value RED test for D68 — confirmed failing before any collection code exists.**
- [ ] 2.2 RED `..._test_worker_never_registers_a_recorder_even_when_activated` — same double; no `Recorder` instance among `config.pluginmanager.registered`. *(D68 — no worker opens a socket)*
- [ ] 2.3 RED `test_evidence.py::test_evidencecollector_registers_on_the_controller_when_activated` — non-xdist controller double; `EvidenceCollector` present after `pytest_configure`.
- [ ] 2.4 GREEN `evidence.py` (create): `EvidenceCollector.__init__` stores `_config`, `_disabled=False`, `_capture_disabled = config.getoption("capture") == "no"`; `pytest_runtest_makereport` hookwrapper — `outcome = yield` never inside `try`; disabled check; `try/except Exception` around `report.vantage_evidence = _extract(...)`, `_extract` stubbed to `{}` here; catches `Exception` only, latches `_disabled`, warns via `_warn`.
- [ ] 2.5 GREEN `plugin.py`: worker branch registers `EvidenceCollector` (when activated) before the existing early return; controller path registers it before the preflight/capability probe/`Recorder`.
- [ ] 2.6 RED `test_evidence.py::test_report_vantage_evidence_attribute_survives_the_xdist_wire` — `pytester`, `-n 2`, one failing test; the controller's collected `TestReport` for that node id carries a `vantage_evidence` dict. *(the xdist-serialization test the proposal scopes)*
- [ ] 2.7 RED `test_config.py::test_resolve_failure_text_capture_is_monotone_decreasing` — property over all eight `(activated, cli_opt_out, ini_opt_out)` combinations: `resolve(...) <= activated`. *(D72)*
- [ ] 2.8 GREEN `config.py`: `resolve_failure_text_capture(*, activated, cli_opt_out, ini_opt_out) -> bool`.
- [ ] 2.9 RED `test_opt_in.py::test_failure_text_opt_out_ini_alone_cannot_enable_capture` — RQ-2's established differential: run once with the `vantage_no_failure_text` ini value present and once absent, no invocation flag either time; capture behaves identically. *(failure-evidence → Capture opt-out under the opt-in rule → A committed configuration file cannot enable capture on its own)*
- [ ] 2.10 RED `test_evidence.py::test_opt_out_flag_means_evidencecollector_is_never_registered` — `--vantage --vantage-no-failure-text`; no `EvidenceCollector` registered, no `vantage_evidence` attribute anywhere. *(The opt-out suppresses failure-text capture)*
- [ ] 2.11 RED `..._test_opt_out_does_not_suppress_outcome_timings_or_identity` — same invocation; outcome/timings/identity still recorded via `vantage_server.results()`. *(The opt-out does not suppress the rest of the result)*
- [ ] 2.12 GREEN `plugin.py`: register `--vantage-no-failure-text` (`store_true`, default `False`) and ini `vantage_no_failure_text`; both branches gate `EvidenceCollector` through `resolve_failure_text_capture`.
- [ ] 2.13 RED `test_config.py::test_no_environment_variable_surface_exists_for_the_opt_out` — `resolve_failure_text_capture`'s signature carries no env parameter; `os.environ` is not referenced on the opt-out path. *(D72)*
- [ ] 2.14 Gate: `uv run pytest packages/pytest-vantage/tests/test_xdist_guard.py packages/pytest-vantage/tests/test_evidence.py packages/pytest-vantage/tests/test_config.py packages/pytest-vantage/tests/test_opt_in.py packages/pytest-vantage/tests/test_plugin_imports.py`; `uv run mypy .` clean.

## Phase 3: Rendering and field extraction (PR3)

Modifies `evidence.py`, `capture.py`. Extends `test_evidence.py`,
`test_capture.py`.

- [ ] 3.1 RED `test_evidence.py::test_traceback_is_complete_under_tb_no` — a 3-frame failure run with `--tb=no`; stored traceback names all 3 frames. *(Traceback capture invariant to display flags → --tb=no)*
- [ ] 3.2 RED `..._test_traceback_is_complete_under_tb_line` — same, `--tb=line`.
- [ ] 3.3 RED `..._test_failure_type_message_repr_come_from_excinfo` — `failure_type == excinfo.typename`, `failure_message == excinfo.exconly()`, `failure_repr == repr(excinfo.value)`.
- [ ] 3.4 RED `..._test_twenty_tests_failing_at_one_line_group_as_one` — twenty tests raising from the identical helper line share one `(failure_path, failure_lineno)`.
- [ ] 3.5 RED `..._test_recorded_location_is_the_raising_helper_not_the_test_function` — the helper's raising line, not the test's first line.
- [ ] 3.6 RED `..._test_skipped_test_records_skip_reason_not_failure_fields` — `skip_reason` recorded verbatim (pytest's own prefix included); failure fields absent; recording does not raise. *(A skipped test does not crash the recorder)*
- [ ] 3.7 RED `..._test_bare_xfail_records_empty_reason_not_none` — `@pytest.mark.xfail` with no `reason=`; `xfail_reason == ""`, never absent — `hasattr`, never truthiness. *(D70)*
- [ ] 3.8 RED `..._test_xfail_precedes_skip_when_both_shapes_are_present` — `xfail_reason` set, `skip_reason` not — row 2 before row 3.
- [ ] 3.9 RED `..._test_a_repr_that_raises_costs_only_that_field` — an exception whose `__repr__` raises; `failure_repr is None` while type/message/traceback are still recorded.
- [ ] 3.10 GREEN `evidence.py`: the real `_extract(item, call, report, capture_disabled)` — the D70 branch table (`excinfo is None` → nothing; `hasattr(report,"wasxfail")` → `xfail_reason`; `report.outcome=="skipped"` → `skip_reason` from `longrepr[2]` behind the tuple/len-3 guard, `str(excinfo.value)` fallback; otherwise the full D69 set), each field in its own `try/except Exception → None`.
- [ ] 3.11 GREEN `capture.py`: `_select_evidence_phase(setup, call, teardown, outcome)` implementing D69's phase-precedence table (error: setup-if-failed else teardown; failed/xfailed: call; skipped: setup-if-skipped else call; xpassed/passed: none).
- [ ] 3.12 RED `test_capture.py::test_evidence_phase_selection_matches_the_derived_outcome_table` — parametrised over `derive_outcome`'s nine rows.
- [ ] 3.13 Gate: `uv run pytest packages/pytest-vantage/tests/test_evidence.py packages/pytest-vantage/tests/test_capture.py`; `uv run mypy .` clean.

## Phase 4: Captured output (PR4)

Modifies `evidence.py`, `capture.py`. Extends `test_evidence.py`,
`test_capture.py`.

- [ ] 4.1 RED `test_evidence.py::test_silent_test_has_empty_captured_output_not_absent` — a test printing nothing, default capture; `captured_stdout == ""`.
- [ ] 4.2 RED `..._test_capture_disabled_leaves_output_absent` — `-s` session; `captured_stdout is None` and `captured_stderr is None`.
- [ ] 4.3 RED `test_capture.py::test_captured_output_concatenates_phases_in_order_no_marker` — distinct sentinels per phase; joined text carries all three in order with no delimiter. *(D71)*
- [ ] 4.4 GREEN `evidence.py`: read `report.capstdout`/`capstderr` per phase when not `_capture_disabled`, else `None` for both.
- [ ] 4.5 GREEN `capture.py`: concatenate `captured_stdout`/`captured_stderr` across setup→call→teardown, independent of the D69 failure-phase selection.
- [ ] 4.6 Gate: `uv run pytest packages/pytest-vantage/tests/test_evidence.py packages/pytest-vantage/tests/test_capture.py`; `uv run mypy .` clean; document the `capsys`/`capfd`-consumed-output gap in `evidence.py`'s docstring.

## Phase 5: The per-report budget (PR5)

Creates `packages/pytest-vantage/src/pytest_vantage/budget.py`,
`packages/pytest-vantage/tests/test_report_budget.py`. Modifies
`recorder.py`.

- [ ] 5.1 RED `test_report_budget.py::test_the_mirrored_cap_matches_the_server` — `_REPORT_BYTES_CAP == MAX_REPORT_BYTES` (test-only cross-package import) and `MAX_FAILURE_TEXT_BYTES * 2 == MAX_REPORT_BYTES`. *(D73 — the pinning test)*
- [ ] 5.2 GREEN `budget.py` (create): `_REPORT_BYTES_CAP = 1024 * 1024`; `MAX_FAILURE_TEXT_BYTES = _REPORT_BYTES_CAP // 2`.
- [ ] 5.3 RED `..._test_spend_budget_charges_encoded_json_bytes_not_raw_len` — a quote/newline-heavy traceback; cost equals `len(json.dumps(value, ensure_ascii=False).encode())`.
- [ ] 5.4 RED `..._test_spend_budget_is_execution_order_first_come` — three over-budget results; the first in execution order stays whole, a later one drops.
- [ ] 5.5 RED `..._test_spend_budget_field_priority_within_a_result` — `failure_message` fits, `traceback` doesn't, at the boundary; message survives, traceback drops.
- [ ] 5.6 RED `..._test_short_fields_are_never_charged_or_dropped` — `failure_type`/`failure_path`/`failure_lineno`/`skip_reason`/`xfail_reason` excluded from the charged set even when exhausted.
- [ ] 5.7 RED `..._test_a_dropped_field_is_null_with_its_truncated_flag_set` — dropped field serializes `{"traceback": null, "traceback_truncated": true}`. *(A field dropped for budget is flagged, not missing)*
- [ ] 5.8 RED `..._test_a_session_within_budget_sets_no_exhaustion_flags` — under-budget session sets no `*_truncated` flag from the budget pass. *(A session within budget carries no exhaustion flags)*
- [ ] 5.9 RED `..._test_a_field_is_dropped_whole_never_cut` — a traceback larger than the remaining budget but under 64 KiB drops entirely, never sliced.
- [ ] 5.10 GREEN `budget.py`: `spend_failure_text_budget(entries)` — one pass, execution order, field priority `(failure_message, failure_repr, traceback, captured_stdout, captured_stderr)`, encoded-cost measurement, drop-whole semantics.
- [ ] 5.11 RED `test_run_report.py::test_a_session_of_many_large_failures_stays_within_the_report_size_cap` — several richly-failing tests via `pytester`/`vantage_test_server`; the actual HTTP body stays within `MAX_REPORT_BYTES`. *(A session of many large failures stays within the report size cap)*
- [ ] 5.12 GREEN `recorder.py`: call `spend_failure_text_budget(entries)` between `assemble_results(...)` and `send(...)`.
- [ ] 5.13 Gate: `uv run pytest packages/pytest-vantage/tests/test_report_budget.py packages/pytest-vantage/tests/test_run_report.py`; `uv run mypy .` clean; confirm `budget.py` imports only `json` and stdlib.

## Phase 6: Ingestion — the flag disjunction (PR6)

Modifies `packages/vantage/src/vantage/service/schemas.py`,
`packages/vantage/src/vantage/service/routes/runs.py`. Extends
`test_schemas.py`, `test_routes_runs.py`, `test_ingestion.py`,
`test_rejection.py`.

- [ ] 6.1 RED `test_schemas.py::test_result_report_failure_evidence_fields_all_default_to_absent` — a minimal `ResultReport` construction; every new field defaults `None`/`False`. *(session-ingestion → Optional failure-evidence fields — schema half)*
- [ ] 6.2 GREEN `schemas.py`: add the failure-evidence optional fields to `ResultReport` (traceback, failure type/message/path/lineno/repr, skip/xfail reason, captured stdout/stderr, each with its `*_truncated` flag), every one defaulted.
- [ ] 6.3 RED `test_routes_runs.py::test_to_result_bounds_a_64kib_oversized_traceback_and_flags_it` — a >64 KiB traceback; stored bounded, flag set. *(failure-evidence → Per-field 64 KiB bound → An oversized field is stored truncated, flagged)*
- [ ] 6.4 RED `..._test_to_result_a_field_within_bound_is_stored_whole_unflagged` — a sub-64-KiB traceback; unchanged, flag clear.
- [ ] 6.5 RED `..._test_to_result_truncation_flag_is_a_disjunction_client_true_server_false` — client `failure_message_truncated=True`, server-side `truncate()` reports `False`; stored flag is `True`. **The D75 test proven able to fail** — a naive `stored_flag = server_flag` assignment clears it.
- [ ] 6.6 RED `..._test_to_result_disjunction_other_direction_server_flag_still_wins` — client sends `False` with an oversized field the server itself cuts; stored flag is `True` regardless.
- [ ] 6.7 RED `..._test_to_result_normalizes_all_null_failure_to_none` — every failure field absent/None/False; `_to_result(...).failure is None`. *(D77, mirroring D48's `_to_vcs_context`)*
- [ ] 6.8 RED `..._test_to_result_captured_output_is_never_none` — both `captured_stdout=None` and `captured_stdout=""` inputs; `Result.captured` is a `CapturedOutput` instance in both cases, never `None`. *(D77 asymmetry)*
- [ ] 6.9 GREEN `routes/runs.py`: `_to_failure_evidence(item) -> FailureEvidence | None` (mirrors `_to_vcs_context`) — applies `truncate()` per 64 KiB field, ORs each flag with the client's, normalizes all-null-or-false to `None`; `_to_captured_output(item) -> CapturedOutput` (never `None`); wired into `_to_result`.
- [ ] 6.10 RED `test_ingestion.py::test_an_older_plugin_omitting_failure_fields_still_stores_run_and_results` — end-to-end ASGI; one run row, results stored, every failure field absent. *(An older plugin omitting the fields still stores its run and results)*
- [ ] 6.11 RED `..._test_a_newer_plugins_failure_evidence_fields_are_persisted` — a report carrying the new fields round-trips through storage. *(A newer plugin's failure-evidence fields are persisted)*
- [ ] 6.12 RED `..._test_an_older_server_tolerates_unrecognized_failure_evidence_keys` — extra unrecognized keys under the existing `extra="allow"` tolerance. *(An older server tolerates a newer plugin's failure-evidence fields)*
- [ ] 6.13 RED `test_rejection.py::test_a_report_exceeding_the_size_cap_with_failure_evidence_stores_nothing` — encoded body (failure evidence included) exceeds `MAX_REPORT_BYTES`; run table stays empty. *(session-ingestion → Whole-report rejection at the size cap → A report exceeding the size cap stores nothing)*
- [ ] 6.14 RED `test_ingestion.py::test_a_report_carrying_failure_evidence_within_the_cap_is_accepted_normally` — within-cap report; one run row, results stored with their fields, response acknowledges. *(A report carrying failure evidence within the cap is accepted normally)*
- [ ] 6.15 Gate: `uv run pytest packages/vantage/tests/test_schemas.py packages/vantage/tests/test_routes_runs.py packages/vantage/tests/test_ingestion.py packages/vantage/tests/test_rejection.py`; `uv run mypy .` clean.

## Phase 7: Storage — both adapters (PR7)

Modifies `packages/vantage/src/vantage/core/ports/storage.py`,
`packages/vantage/src/vantage/storage/sqlite_store.py`,
`packages/vantage/src/vantage/storage/memory.py`,
`packages/vantage/tests/vantage_port_contract.py`. Extends
`test_storage_types.py`, `test_sqlite_store.py`, `test_memory_store.py`,
`test_ingestion.py`. **Port method and both adapters land together.**

- [ ] 7.1 RED `test_storage_types.py::test_result_list_entry_carries_identity_outcome_timings_worker_and_failure_projection` — `ResultListEntry` has exactly identity/outcome/timings/`worker_id`/`failure: FailureProjection | None`, no `traceback`/`captured`. *(D77)*
- [ ] 7.2 GREEN `storage.py`: `ResultListEntry` dataclass.
- [ ] 7.3 RED `vantage_port_contract.py::test_list_results_projects_failure_evidence_via_failure_projection` — a stored result with a >200-char message; `list_results` entries agree with `project_failure`'s bounding and disjunction. *(D76, two-mechanism agreement)*
- [ ] 7.4 RED `..._test_list_results_excludes_the_heavy_fields_structurally` — `ResultListEntry` has no field to carry traceback/repr/captured output.
- [ ] 7.5 RED `..._test_get_result_returns_the_full_record_hit` — `get_result(execution_id, node_id=...)` returns `Result` with `failure`/`captured` populated in full, unbounded. *(history-read-api → Single result detail → The full record is reachable — port half)*
- [ ] 7.6 RED `..._test_get_result_returns_none_for_unknown_node_id_miss`.
- [ ] 7.7 RED `..._test_get_result_truncation_flag_travels_with_the_field` — a capture-truncated traceback; `get_result(...).failure.traceback_truncated is True`. *(A bounded field's truncation flag travels with it — port half)*
- [ ] 7.8 RED `..._test_captured_output_empty_versus_absent_round_trips_through_storage` — `""` vs `None` round-trip distinctly, both adapters.
- [ ] 7.9 RED `test_ingestion.py::test_finish_report_reaches_storage_in_one_commit` — extend the existing row-count assertions to the wider 27-column insert; still one `BEGIN IMMEDIATE`…`COMMIT`. *(run-recording → Run atomicity — the D80 batch-insert-strategy premise)*
- [ ] 7.10 RED `test_sqlite_store.py::test_an_existing_pre_change_database_opens_unrefused_and_reads_back_its_rows` — a fixture DB written by the pre-change 14-column insert path opens unrefused under the 27-column adapter; pre-existing rows read back with `NULL` in the new columns. *(ADR-0013's non-firing, proven not assumed)*
- [ ] 7.11 GREEN `storage.py`: `get_result(execution_id, *, node_id) -> Result | None` on `ExecutionStore`; `list_results` return type becomes `Page[ResultListEntry]`.
- [ ] 7.12 GREEN `sqlite_store.py`: widen `_INSERT_RESULT` to 27 columns, `_SELECT_RESULTS_FOR_RUN` to 29; implement `get_result`; rewrite `list_results`' `SELECT` to project via `substr`/`COALESCE` (D76, mirroring `_LIST_RUNS`'s commit-subject shape) — no traceback/repr/captured columns selected.
- [ ] 7.13 GREEN `memory.py`: mirror the same insert/select width, `get_result`, and `list_results` → `ResultListEntry` via `project_failure`.
- [ ] 7.14 Gate: `uv run pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_memory_store.py packages/vantage/tests/test_ingestion.py`; `uv run mypy .` clean — confirms both adapters satisfy the widened Protocol structurally.

## Phase 8: Read surface (PR8)

Modifies `packages/vantage/src/vantage/service/schemas.py`,
`packages/vantage/src/vantage/service/routes/read.py`,
`packages/vantage/src/vantage/service/errors.py`,
`packages/vantage/src/vantage/service/openapi/v1.yaml`. Extends
`test_routes_read.py`, `test_interface_document.py`,
`test_read_only_surface.py`. **Depends on PR7.**

- [ ] 8.1 RED `test_routes_read.py::test_results_route_response_excludes_traceback_and_captured_output_sentinel` — a distinctive sentinel in the stored traceback is absent from the raw `GET /runs/{id}/results` body; **delete the retired task-7.6 Inspection comment in this same edit**. *(history-read-api → Lean list projections → List responses exclude traceback and captured output — now Test)*
- [ ] 8.2 RED `..._test_results_route_includes_bounded_failure_message_and_disjunction_flag` — a >200-char message; list entry carries the first 200 chars plus the flag.
- [ ] 8.3 RED `..._test_result_detail_route_returns_full_record` — `GET /runs/{run_id}/result?node_id=...` returns 200 with the sentinel traceback and every failure/captured field, unbounded. *(Single result detail → The full record is reachable — route half)*
- [ ] 8.4 RED `..._test_result_detail_truncation_flag_travels_with_the_field` — a truncated traceback; flag present alongside it. *(A bounded field's truncation flag travels with it on the single-item endpoint)*
- [ ] 8.5 RED `..._test_result_detail_unknown_run_id_is_404_unknown_run_error`.
- [ ] 8.6 RED `..._test_result_detail_unknown_node_id_is_404_unknown_result_error` — known run, unknown `node_id`, a distinct error shape. *(An unknown result identifier leaves stored data unchanged — 404 half)*
- [ ] 8.7 RED `..._test_result_detail_unknown_identifier_leaves_stored_data_unchanged` — read the table directly after the request; no row created/altered/removed. *(An unknown result identifier leaves stored data unchanged)*
- [ ] 8.8 RED `..._test_result_detail_overlong_node_id_is_422_not_414` — over `MAX_IDENTITY_CHARS`, D54 inherited.
- [ ] 8.9 GREEN `errors.py`: `UnknownResultError` (404-shaped, its own kind).
- [ ] 8.10 GREEN `schemas.py`: `ResultListItemResponse` (nested `FailureProjection` shape); `ResultDetailResponse` (every field, field by field, never `from_attributes`).
- [ ] 8.11 GREEN `routes/read.py`: `GET /api/v1/runs/{run_id}/result?node_id=` (`Query(..., max_length=MAX_IDENTITY_CHARS)`) calling `store.get_result`; `_result_item` reads from `ResultListEntry`/`FailureProjection`, never the full `Result`.
- [ ] 8.12 GREEN `openapi/v1.yaml`: new `read`-tagged `GET /runs/{run_id}/result` operation, hand-written.
- [ ] 8.13 RED `test_interface_document.py::test_the_new_result_detail_path_is_declared_and_answers_2xx` — extend the binding table.
- [ ] 8.14 RED `test_read_only_surface.py::test_every_read_path_has_a_binding` — confirm the existing falsifier now also requires an entry for the new path.
- [ ] 8.15 GREEN `test_read_only_surface.py`: add the binding-table entry for the new route.
- [ ] 8.16 Gate: `uv run pytest packages/vantage/tests/test_routes_read.py packages/vantage/tests/test_interface_document.py packages/vantage/tests/test_read_only_surface.py`; `uv run mypy .` clean.

## Phase 9: Measurements, overhead script, docs, ADR-0016, OQ-11 (PR9)

Creates `scripts/measure_failure_capture_overhead.py`. Modifies
`openspec/changes/failure-capture/specs/failure-evidence/spec.md`,
`openspec/changes/failure-capture/specs/run-recording/spec.md`,
`docs/adr/0016-store-pytest-s-rendered-failure-text-bounded-and-unredacted.md`,
`docs/open-questions.md`, `docs/schema-manifest.md`, `README.md`.
**Depends on PR8.**

- [ ] 9.1 Analysis: write `scripts/measure_failure_capture_overhead.py` following `scripts/measure_vcs_overhead.py`'s shape — five interleaved A/B/A/B paired runs per cell, medians; baseline A = recording+VCS on, failure capture opted out via `--vantage-no-failure-text`; treatment B = failure capture on; axes: 1,000 tests at ~10 ms with 1%/10%/100% failing, crossed with `--tb=auto`/`--tb=no`; report per-failed-test rendering cost and whole-session overhead; also report the recording-off comparison. *(D79)*
- [ ] 9.2 Analysis: run the script by hand (never in CI); transcribe the six-cell table into `.../failure-evidence/spec.md` as a **Measurements** paragraph beside the ≈55 ms headroom figure; state plainly whether the 100%-failing profile breaches RQ-25's 2% budget — recorded as measured, never adjusted, **no failure-count cap invented**. *(RQ-25's overhead obligation, Analysis)*
- [ ] 9.3 Confirm (regression, no new code): 7.9's row-count assertions still pass unmodified in shape at 27 columns — the batch-insert strategy did not split. *(D80 re-run premise for RQ-3's Analysis argument)*
- [ ] 9.4 Analysis: re-run the `tracemalloc`-based 500-result finish-write measurement test; transcribe the re-measured no-failure body size/peak memory into `.../run-recording/spec.md`'s Measurements paragraph, replacing the pre-`failure-evidence` figures (252,511 bytes / ≈2,021,039 bytes); separately measure the all-failing 500-result body size against the `776,799`-byte bound (D73); justify any material increase. *(Measurements are re-run for the failure-evidence column set)*
- [ ] 9.5 Write `docs/adr/0016-...md`: flip `Status: Proposed` → `Status: Accepted` at merge, not at PR-open time. *(D81)*
- [ ] 9.6 Write `docs/open-questions.md`: open OQ-11 (unredacted storage, credentials risk); note the failure-count-cap possibility as unresolved pending 9.2's measurement.
- [ ] 9.7 Write `docs/schema-manifest.md`: mark the twelve `result` failure/captured-output columns and index 5 as populated, citing this change.
- [ ] 9.8 Write `README.md`: the disclosure sentence and `--vantage-no-failure-text` documentation, beside `--vantage`/`--vantage-server`.
- [ ] 9.9 Inspection (recorded in the PR description, not an assertion): confirm the merged `failure-evidence` spec and `README.md` both state the disclosure plainly. *(Unredacted storage is disclosed → The disclosure is present in the capability spec and the README)*
- [ ] 9.10 Full gate: `uv run pytest`, `uv run mypy .`, `uv run deptry .`, `uv run ruff format . && uv run ruff check --fix .`; confirm `git diff` shows zero `schema.sql` changes across the whole change; state which CI-matrix legs, the networking-disabled job, and the clean-environment install check ran locally versus are left to CI.

---

## Scenario coverage — every new/modified spec scenario

| # | Scenario | Capability | Task |
|---|---|---|---|
| 1 | The traceback is complete under `--tb=no` | failure-evidence | 3.1 |
| 2 | The traceback is complete under `--tb=line` | failure-evidence | 3.2 |
| 3 | Twenty tests failing at one source line group as one | failure-evidence | 3.4 |
| 4 | The recorded location is the raising site | failure-evidence | 3.5 |
| 5 | A skipped test does not crash the recorder | failure-evidence | 3.6 |
| 6 | A silent test has empty captured output, not absent | failure-evidence | 4.1 |
| 7 | Capture disabled leaves output absent, not empty | failure-evidence | 4.2 |
| 8 | An oversized field is stored truncated, flagged | failure-evidence | 6.3 |
| 9 | A field within bound is stored whole, unflagged | failure-evidence | 6.4 |
| 10 | A session of many large failures stays within the report size cap | failure-evidence | 5.11 |
| 11 | A field dropped for budget is flagged, not missing | failure-evidence | 5.7 |
| 12 | A session within budget carries no exhaustion flags | failure-evidence | 5.8 |
| 13 | The opt-out suppresses failure-text capture | failure-evidence | 2.10 |
| 14 | A committed configuration file cannot enable capture on its own | failure-evidence | 2.9 |
| 15 | The opt-out does not suppress the rest of the result | failure-evidence | 2.11 |
| 16 | The disclosure is present in the capability spec and the README | failure-evidence | 9.9 |
| 17 | List responses exclude traceback and captured output | history-read-api | 8.1 |
| 18 | The commit subject is bounded in list responses | history-read-api | pre-existing (`read-api`), unaffected; re-confirmed by 8.16/9.10's regression gate |
| 19 | The truncation flag never surfaces independently of its subject | history-read-api | pre-existing, unaffected; regression gate |
| 20 | `vcs_root` appears in no run list or run detail response | history-read-api | pre-existing, unaffected; regression gate |
| 21 | The full record is reachable for a given result | history-read-api | 7.5 (port), 8.3 (route) |
| 22 | A bounded field's truncation flag travels with it on the single-item endpoint | history-read-api | 7.7 (port), 8.4 (route) |
| 23 | An unknown result identifier leaves stored data unchanged | history-read-api | 7.6 (port miss), 8.6, 8.7 (route) |
| 24 | An older plugin omitting the fields still stores its run and results | session-ingestion | 6.10 |
| 25 | A newer plugin's failure-evidence fields are persisted | session-ingestion | 6.11 |
| 26 | An older server tolerates a newer plugin's failure-evidence fields | session-ingestion | 6.12 |
| 27 | A report exceeding the size cap stores nothing | session-ingestion | 6.13 |
| 28 | A report carrying failure evidence within the cap is accepted normally | session-ingestion | 6.14 |
| 29 | Measurements are re-run for the failure-evidence column set | run-recording | 9.4 |
| — | RQ-3.1–RQ-3.4, reordered-start-write scenarios | run-recording | pre-existing, unmodified by this change; re-confirmed by 7.9 and 9.10's full-suite gate |
| — | `result-capture`'s Purpose cross-reference to `failure-evidence` | result-capture | Purpose-text-only delta, no requirement change; verified by inspection at archive, not a task |

**All 29 new/modified scenarios trace to at least one task.** Scenarios 18–20
and the run-recording atomicity scenarios are pre-existing obligations this
change does not modify — they trace to the already-shipped tests plus this
change's own full-suite regression gate (9.10), not a newly authored RED
task, the same treatment `read-api`'s tasks file gave scenarios 20/21 there.

## Architecture and process notes carried into every gate

- `vantage.core` (`result.py`, `projection.py`) imports nothing beyond the
  standard library; `vantage.storage` imports the core only; Pydantic and
  FastAPI never appear outside `vantage.service`; `pytest-vantage` imports
  nothing beyond pytest and the standard library (RQ-24) and never opens a
  database (ADR-9) — enforced by the existing architecture and import tests
  at every phase gate.
- No new class introduced by this change (`FailureEvidence`, `CapturedOutput`,
  `FailureProjection`, `EvidenceCollector`, `ResultListEntry`) starts with
  `Test`.
- The `id=` keyword is load-bearing wherever an existing `RQ-xx` marker is
  touched; no new `req` marker is minted by this change.
- Commits are Conventional Commits, no AI attribution; the change name goes
  in the commit body, never the subject.
- `schema.sql` is touched by no task in this file; `meta.schema_version`
  stays `'2'` throughout every phase.
