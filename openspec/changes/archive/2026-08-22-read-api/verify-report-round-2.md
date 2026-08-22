```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:cbde306b0aceb6a697c4c518627766b526df2617beb8f0c717a1a9971590e55d
verdict: fail
blockers: 1
critical_findings: 1
requirements: 9/9
scenarios: 26/26
test_command: uv run pytest
test_exit_code: 0
test_output_hash: sha256:dacfa8a54ddb1e974c139beb809482532c94e0fe8e4edbba9f605e8405b08612
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:abcb6647b13baa8244d90bb839deeab7f27878b24d4a96ce9a221f280e0e8a6c
```

## Verification Report — round 2

**Change**: `read-api`
**Branch / tip**: `ft/read-api-07b-measurement-closeout` @ `b74e643`
**Mode**: Strict TDD
**Runtime attempt ledger**: deliberately skipped per orchestrator instruction (ledger
inconsistent: `status` says `next_action: begin`, `begin` is a no-op, `acquire` answers
`blocked: maintainer_decision`). No reset, force, workaround, or `review mode disable`
attempted. Correctness below rests entirely on execution evidence.

**Verdict: NOT archive-ready.** One CRITICAL blocks it. Round 1's twelve findings are
genuinely closed — every one reproduced under tamper — but the change carries a defect of
the same class round 1 found, in a different artifact, plus a broad value-fidelity gap the
remediation did not reach.

---

### Baseline (each confirmed, not assumed)

| Gate | Result |
|---|---|
| `uv run pytest` | **402 passed**, exit 0 |
| `uv run mypy .` | `Success: no issues found in 75 source files`, exit 0 |
| `uv run deptry .` | `Success! No dependency issues found.` (74 files) |
| `schema.sql` across whole change | `git diff --stat 1078079..b74e643 -- '*schema.sql'` → **empty**, zero-line diff |
| Working tree | clean at `b74e643` before and after every tamper |

Every tamper below was applied to real source, run against the full suite, then restored
and verified byte-clean by SHA-256 comparison against the pre-edit bytes. All 46 restores
verified clean; the tree is at `b74e643` with no modifications.

---

### Part 1 — Round 1's claimed remediations, re-run rather than believed

#### CRITICAL-2: the four value mutations

Each previously left the package suite green. Each now **fails**:

| Mutation | Result | Test that catches it |
|---|---|---|
| `_vcs_response(commit_subject_truncated=False)` | ❌ 2 failed / 400 passed | `test_history_route_returns_newest_first_with_full_vcs`, `test_list_response_carries_the_truncation_flag_beside_its_subject` |
| `_history_entry(duration=None)` | ❌ 1 failed / 401 passed | `test_history_route_returns_newest_first_with_full_vcs` |
| `_vcs_response(dirty=None)` | ❌ 2 failed / 400 passed | `test_history_route_returns_newest_first_with_full_vcs`, `test_run_list_returns_items_and_has_more_envelope` |
| `_vcs_response(commit=None)` | ❌ 2 failed / 400 passed | `test_history_route_returns_newest_first_with_full_vcs`, `test_run_list_returns_items_and_has_more_envelope` |

**CRITICAL-2's specific claim is true.** All four named mutations are now caught. See
WARNING-1 for what the same sweep found that the remediation did not name.

#### CRITICAL-1: `root` reintroduced on `RunVcsResponse` and populated

Two-file tamper: `root: str | None = None` added to `RunVcsResponse` in `schemas.py`, and
`root=getattr(vcs, "root", None)` added to `_vcs_response` in `read.py`.

Result: **399 passed / 3 failed**.

| Test | Expected by its docstring | Observed |
|---|---|---|
| `test_run_detail_response_contains_no_vcs_root` (4.6) | must FAIL | ❌ **FAILED** ✓ |
| `test_run_list_response_contains_no_vcs_root` (4.2) | stays green (Inspection) | ✅ green ✓ |
| `test_history_route_response_contains_no_vcs_root` (5.5) | stays green (Inspection) | ✅ green ✓ |

The corrected docstrings tell the exact truth. 4.6 carries the scenario and can fail; 4.2
and 5.5 are honestly labelled structurally unfalsifiable and are kept as regression guards
without claiming to prove anything. **CRITICAL-1 is genuinely closed, not merely reworded.**

Two additional tests failed — `test_run_list_returns_items_and_has_more_envelope` (4.1) and
`test_history_route_returns_newest_first_with_full_vcs` (5.3) — because their exact-key-set
assertions saw the extra `root` key. That independently confirms traceability row 6's claim
that 5.3's key set is what kills the history-path `root` leak.

#### SUGGESTION-2: absent-repository runs sorted last

Tamper: leading `execution.vcs is not None` sort key added to
`InMemoryExecutionStore.list_runs`, forcing absent-repository runs last regardless of recency.

Result: **400 passed / 2 failed** — `test_absent_repository_run_appears_in_list_undistinguished`
(the route-level demonstration, on run-id order) and
`test_list_runs_includes_absent_repository_run_undistinguished` (port level). The ordered-list
assertion that replaced the set comparison is real. ✓

#### SUGGESTION-4: an audit-log-on-miss write on a 404 branch

Tamper: `get_run_detail`'s 404 branch made to call `store.record_session(...)` with a synthetic
execution before raising `UnknownRunError`.

Result: **401 passed / 1 failed** — `test_logical_content_digest_unchanged_after_every_read_path`.
The broadened 404 binding catches a write that the old happy-path-only binding would have
missed entirely. The newly broadened binding table earns its existence. ✓

#### Both falsifiers still work

| Falsifier | Tamper | Result |
|---|---|---|
| Drift check, undeclared-route half | mounted `GET /api/v1/_smuggled-undocumented` on the real `create_app` | ❌ `test_a_served_but_undocumented_route_is_reported` **FAILED** ✓ |
| Read-only harness, tampered-binding half | `_run_read_only_proof` made to execute none of its bindings | ❌ `test_a_writing_endpoint_tagged_read_fails_the_harness` **FAILED** ✓ |

Broadening the bindings did not weaken either falsifier.

#### The remaining round-1 findings, checked by inspection

| Finding | Status |
|---|---|
| WARNING-1 delta heading join key | ✅ Closed. Both `(RQ-23)` / `(RQ-44)` headings match `openspec/specs/**` byte-for-byte (`spec.md:46`, `spec.md:71`), each with an explicit join-key note. |
| WARNING-2 `session-liveness` Purpose | ✅ Closed. Explicit "Archive instruction" block; Purpose text rewritten to state the read path now exists. |
| WARNING-6 `limit` divergence | ✅ Closed. `maximum: 200` removed; clamp stated as behaviour; `test_run_list_clamps_an_over_cap_limit_rather_than_rejecting_it` covers the over-cap branch. |
| WARNING-3 traceability credits | ✅ Closed and independently verified — see CRITICAL-1 above. |
| WARNING-4 design says `app.routes` | ⚠️ **Partially closed** — see WARNING-2 below. |
| WARNING-5 "variance" → systematic | ✅ Closed. Paragraph now states the shift is systematic, backed by the third data point, and keeps the conservative reading explicit. |
| SUGGESTION-1 task 6.11 text | ✅ Closed. `types-PyYAML` is in `DEP002`; task text corrected. |
| SUGGESTION-3 test renamed | ✅ Closed. Renamed; the only surviving occurrences of the old name are in two immutable verify-report records. |

---

### Part 2 — What round 1 and the remediation both missed

## CRITICAL

**CRITICAL-1 (round 2). `openapi/v1.yaml` claims its response schemas are drift-checked
against `service/schemas.py`. No such check exists.**

`packages/vantage/src/vantage/service/openapi/v1.yaml`, in the comment introducing the
response-body schemas, states:

> The duplication D56 warns about is bounded by `test_routes_read.py`'s exact-key-set
> assertions: if these shapes and `service/schemas.py` ever diverge, a test goes red.

Three independent tampers, each on `v1.yaml` alone, each run against the full suite:

| Tamper on `v1.yaml` | Result |
|---|---|
| `RunVcs` gains a required `root` property — the document now declares the exact field the whole `vcs_root` requirement exists to keep off the wire | **402 passed, 0 failed** |
| `RunListItem` drops `presentation` from `required` | **402 passed, 0 failed** |
| `ResultItem.outcome`'s enum replaced with `[ZZZ_never_emitted]`, a vocabulary the server never emits | **402 passed, 0 failed** |

**The claim is untrue.** No test reads `components.schemas` from `v1.yaml`.
`test_interface_document.py` compares only `(METHOD, path)` pairs;
`test_read_only_surface.py` reads only `tags`; `test_every_documented_path_answers_2xx`
asserts status codes and never validates a response body against a declared schema. The
`test_routes_read.py` key-set assertions the comment names compare a response body against a
set literal written in the test file — they never consult the document. So the coupling runs
in neither direction: the document can silently become wrong about what the server returns,
and nothing goes red.

This is a claimed obligation that is untrue, recorded in a shipped artifact — the same class
of defect as round 1's CRITICAL-1, in a different file. It is load-bearing rather than
cosmetic for two reasons:

1. **It is the justification for an accepted process exception.** PR6 was accepted at 434
   code+test lines, over the 400 ceiling, specifically so the interface document could state
   response schemas rather than `type: object` stubs. Those schemas are the part of the
   document nothing verifies.
2. **It is the exact hazard the design names.** D56's rule is "one source per fact… two
   statements of one fact drift," and D56 refuses to copy the document to `docs/api/`
   precisely because "a second copy is a drift source, and the drift check would not cover
   it." The response schemas are a second statement of `service/schemas.py`'s facts, and the
   drift check does not cover them either. The comment asserts a mitigation that the design's
   own reasoning would predict is absent.

Either the check must exist, or the comment must stop claiming it does.

## WARNING

**WARNING-1 (round 2). The value-fidelity remediation is incomplete: 27 of 37 field
mutations survive a full green suite.**

CRITICAL-2's four named mutations are caught. A systematic sweep of every field in every
read response builder in `routes/read.py` — 37 mutations, type-appropriate so that a catch
reflects a value assertion and not a Pydantic type error — found **27 that leave the suite at
402 passed**:

| Builder | Fields that can still be nulled or swapped undetected |
|---|---|
| `_run_list_item` (5 of 7) | `started_at` (shifted +1 day), `finished_at` (→ `None`), `exit_status` (→ `None`), `interrupted` (flipped), `presentation` (hardcoded `"finished"`) |
| `_run_detail_response` (6 of 8) | `id` (→ constant), `started_at` (+1 day), `finished_at` (→ `None`), `exit_status` (→ `None`), `interrupted` (flipped), `interrupt_reason` (→ `None`) |
| `_history_entry` (2 of 6) | `started_at` (+1 day), `finished_at` (→ `None`) |
| `_result_item` (14 of 16) | `file_path`, `class_name`, `function_name`, `param_id`, `duration`, `started_at`, `finished_at`, `setup_outcome`, `call_outcome`, `teardown_outcome`, `setup_duration`, `call_duration`, `teardown_duration`, `worker_id` |

Caught (10): `_vcs_response`'s `commit`, `branch`, `commit_subject`,
`commit_subject_truncated`, `dirty`; `_run_list_item`'s `id` and `vcs`;
`_run_detail_response`'s `vcs`; `_history_entry`'s `run_id`, `outcome`, `duration`, `vcs`;
`_result_item`'s `node_id` and `outcome`. `_run_detail_response.presentation` was also
verified caught (hardcoding `"abandoned"` fails 4.9 and 4.10).

Two of these deserve naming:

- **`_run_list_item.presentation` can be hardcoded to `"finished"` with the suite green.**
  `derive_presentation` getting its first caller is D62's whole point, and on the list path
  nothing observes that the call happens. The four `session-liveness` scenarios are
  demonstrated only through the detail path.
- **`_result_item` asserts 2 of 16 fields.** `test_results_route_returns_paginated_envelope`
  checks `node_id` and `outcome` and nothing else, so fourteen result fields — including
  every phase outcome and duration — reach the wire unverified. `file_path` and
  `function_name` survive even a non-null swap to a distinct literal.

This is WARNING rather than CRITICAL because no spec scenario is left without a passing
covering test: the spec's list requirements concern *exclusion* and *bounds*, not the values
of these scalars, and the liveness scenarios are satisfied on the detail path. But it is the
same defect class CRITICAL-2 named, and the remediation closed only the instances round 1
happened to list.

**WARNING-2 (round 2). WARNING-4 is only partially closed — `design.md` still contradicts
itself on the drift mechanism.**

D66's prose was correctly rewritten (`design.md:511-527`) to record that `app.routes` was
tried, stopped working behind FastAPI's lazy `_IncludedRouter`, and was replaced by
`app.openapi()` for the mounted side only. But the flow diagram in the same document still
reads:

```
│  every op    → drift check against app.routes                  (D66)   │
```

(`design.md:620`) — the exact statement WARNING-4 was raised about, left standing 93 lines
below its own correction. Every other `app.routes` occurrence in the change is contextually
correct ("never generated from `app.routes`", "was the first thing tried"). This one is a
stale mechanism claim.

## SUGGESTION

1. **Documented error responses are inconsistent.** `/tests/history` declares its `422`;
   `/runs` and `/runs/{run_id}/results` declare none, though both return `422` for `limit=0`
   and the read-only harness now exercises exactly those branches. The document describes
   fewer outcomes than the harness drives.
2. **Declared enums are stricter than the served models.** `v1.yaml` declares closed enums for
   `presentation` and `outcome`; `RunListItemResponse.presentation`, `RunDetailResponse.presentation`
   and `ResultItemResponse.outcome` are plain `str`. The server would serve a value the
   document forbids without anything objecting. (Subsumed by CRITICAL-1 if a schema check lands.)
3. **Scenario 5 has port-level coverage only.** "A non-repository execution has a null VCS
   context, not an omitted entry" traces to `test_list_history_null_vcs_entry_present_not_omitted`
   (port contract, both adapters). A route-level history test with a null-VCS entry would close
   it through the surface the scenario is written about, as scenario 22 already does for the run list.
4. **`test_main_file_digest_stable_despite_wal_checkpointing` (7.3) was not falsified.** The
   SUGGESTION-4 audit-log tamper wrote to WAL and left the main file untouched, so 7.3 stayed
   green while 7.2 caught it. 7.3 asserts a stability property, so this is expected rather than
   wrong — but it means the scenario rests on a check no tamper in this round could fail.

---

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 83 |
| Tasks complete | 83 (`rg -c '^- \[x\]'` → 83; `^- \[ \]` → 0) |
| Tasks incomplete | 0 |

All 83 are genuinely complete: the checkbox count matches, and every task credited in the
traceability table resolves to a real test or a real artifact. Spot-checks of rows the
remediation did *not* touch: row 20/21's credited
`test_vcs.py::test_not_a_repository_records_nulls_and_no_warning` exists
(`packages/pytest-vantage/tests/test_vcs.py:161`); row 5's `test_list_history_null_vcs_entry_present_not_omitted`
exists (`vantage_port_contract.py:880`); rows 15-19's `6.x` tasks all resolve to tests in
`test_interface_document.py`. No row credits a test that does not exist, and no row I checked
credits a test that does not do what the row says.

### Spec compliance

9 requirements, 26 scenarios, all with passing covering evidence.

**Of the 26, twelve were proved falsifiable by tamper in this round**: 1 (via the
audit-log-on-miss), 3, 6, 8, 9, 10, 16, 18, 22, 24, 25, and 23 collectively with 24/25
through the mutually-exclusive presentation triple. Three are declared non-Test by the spec
itself and are honestly recorded as such: 7 (Inspection — `result.traceback` has no writer),
14 (Analysis — p95 3.70 ms, max 11.59 ms, committed as numbers), 19 (Inspection). The
remaining eleven have passing covering tests that this round did not individually falsify;
none showed a defect, but their falsifiability is asserted rather than demonstrated.

The two guards round 1 exposed as unfalsifiable (4.2, 5.5) are now correctly labelled
Inspection in both docstring and traceability table, and this round's CRITICAL-1 tamper
confirms both stay green exactly as they now admit.

### Design coherence

| Decision | Followed? | Notes |
|---|---|---|
| D53 read surface derived from the document | ✅ | `_read_operations` reads `tags`, never a local list |
| D56 one source per fact | ❌ | Response schemas are a second statement with no covering check — CRITICAL-1 |
| D59 `root` never on the wire | ✅ | Proven by the CRITICAL-1 tamper |
| D61 cap/clamp behaviour | ✅ | Over-cap clamp documented and tested |
| D62 `derive_presentation` gets its first caller | ⚠️ | True on the detail path; unobserved on the list path — WARNING-1 |
| D63 no new index, `schema.sql` untouched | ✅ | Zero-line diff confirmed |
| D65 digest-pair read-only proof | ✅ | Falsifier live; 404 binding proven to catch a real write |
| D66 drift check mechanism | ⚠️ | Prose corrected, diagram stale — WARNING-2 |

### TDD compliance

| Check | Result | Details |
|---|---|---|
| TDD evidence reported | ✅ | apply-progress carries per-batch evidence with commands and results |
| All tasks have tests | ✅ | 83/83; 50 RED test tasks |
| GREEN confirmed by execution | ✅ | 402 passed, independently re-run |
| Assertion quality | ⚠️ | No tautologies, no ghost loops, no mock-heavy tests, no smoke-only tests. But 27 surviving mutations show assertions that are present and passing while verifying less than their surrounding docstrings imply — WARNING-1 |

### Issues Found

**CRITICAL** (1): `v1.yaml`'s response-schema comment claims a drift check against
`service/schemas.py` that does not exist; three tampers on the document leave the suite at
402 passed.

**WARNING** (2): (1) 27 of 37 read-response field mutations survive a green suite; (2)
`design.md:620`'s diagram still names `app.routes` as the drift mechanism.

**SUGGESTION** (4): undocumented `422` responses; declared enums stricter than served models;
scenario 5 port-level only; 7.3 not falsifiable by any tamper available this round.

### Verdict

**FAIL — not archive-ready.**

Round 1's twelve findings are genuinely and verifiably closed; the remediation did what it
said and its tamper evidence reproduces exactly. That is not the question. The change is
blocked because it ships an artifact asserting a drift check that does not exist — the same
defect class round 1 caught, one file over — and because the value-fidelity repair reached
only the four instances round 1 happened to enumerate, leaving 27 fields on the read surface
that can be nulled or swapped with every gate green.
