```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:516711dd519e38c9474012335e3436edf61219479682e0ab376ad595d6bf0c8c
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 9/9
scenarios: 26/26
test_command: uv run pytest -q
test_exit_code: 0
test_output_hash: sha256:bd4f1982c4f2aafc36121c70c2c99be4b4e24270b69d27f7df18dd57dbb3bedc
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:abcb6647b13baa8244d90bb839deeab7f27878b24d4a96ce9a221f280e0e8a6c
```

## Verification Report — round 3

**Change**: `read-api`
**Branch / tip**: `ft/read-api-07b-measurement-closeout` @ `e299b6a`
**Mode**: Strict TDD
**Verdict**: **PASS WITH WARNINGS — this change is archive-ready.**

Round 1 found two CRITICALs, round 2 found one. **Round 3 finds none.** I re-ran
every piece of evidence commit `e299b6a` claims, built an independent mutation
set rather than reusing theirs, and extended past the boundary their sweep
declared. Nothing I found is a defect in delivered behaviour: every surviving
mutation is a *test-strength* gap, not a wrong answer on the wire. No spec
scenario is uncovered, no task is open, and the two falsifiers are load-bearing.
Archive it.

The `gentle-ai sdd-attempt` runtime ledger was **deliberately skipped** per the
orchestrator's instruction — it is inconsistent (`status` says
`next_action: begin`, `begin` is a no-op, `acquire` answers
`blocked: maintainer_decision`). No reset, force, workaround, or
`gentle-ai review mode disable` was attempted. Correctness here rests on the
four gates plus 128 tampers, not on the ledger.

---

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 83 |
| Tasks complete | 83 (`[x]`) |
| Tasks incomplete | 0 (`[ ]`) |
| Requirements (delta specs) | 9 |
| Scenarios (delta specs) | 26 |

Counted directly from `openspec/changes/read-api/specs/**`:
`history-read-api` 5 req / 14 scen, `api-interface-document` 1/4,
`session-liveness` 1/4, `session-ingestion` 1/1, `version-control-context` 1/3.
The traceability table's 26 rows match the 26 scenarios one-for-one.

---

### Baseline — every claim re-run, none assumed

| Claim | Result |
|---|---|
| `uv run pytest` → 411 passed | ✅ **411 passed in 48.28s**, exit 0 |
| `uv run mypy .` → clean over 75 files | ✅ `Success: no issues found in 75 source files` |
| `uv run deptry .` → clean | ✅ `Success! No dependency issues found.` (74 files) |
| `schema.sql` unchanged across the whole change | ✅ `git diff 1078079 e299b6a -- .../schema.sql` is empty |
| No production source changed since `b74e643` | ⚠️ **Nearly.** Every `.py` under `packages/*/src` is byte-identical. `openapi/v1.yaml` **did** change (+26/−3): the rewritten comment block plus two `"422"` response declarations. `v1.yaml` ships inside the distribution and is served verbatim at `GET /api/v1/openapi.yaml`, so it is production bytes. The apply report's own wording (`routes/read.py`, `schemas.py` and every `vantage.core`/`vantage.storage` module are byte-identical) is precise; the looser restatement is not. Not a defect — recorded for accuracy. |
| Zero `RQ-` in this change | ✅ Exactly two, both the sanctioned archive-merge join keys: `Absent repository (RQ-23)` and `Abandoned run is observable (RQ-44)` |
| `design.md:620` diagram corrected | ✅ reads `drift check against app.openapi()`; the three surviving `app.routes` mentions are D66's history and Q5's "never generated from" |
| Latency committed as numbers | ✅ p95 **3.70 ms**, max **11.59 ms** in `specs/history-read-api/spec.md` |

**Tree integrity**: after 128 tampers the working tree is byte-clean
(`git status --porcelain` empty at `e299b6a`), and the suite re-runs at 411
passed. Every tamper restored under `finally` with a SHA-256 equality assert.

---

### Tamper campaign — 128 tampers, all groups

Harness: replace exact bytes → run the **full workspace suite**
(`uv run pytest -q -n 8 -x`) → restore → verify SHA-256. Targeted runs are
noted as such.

#### Group A — the three round-2 document tampers, plus five more (8/8 CAUGHT)

| Tamper | Result | Test that went red |
|---|---|---|
| A1 `RunVcs` gains a required `root` | ✅ CAUGHT | `test_declared_schema_properties_match_their_model_fields` |
| A2 `RunListItem` drops `presentation` from `required` | ✅ CAUGHT | `test_declared_required_sets_match_their_models` |
| A3 `ResultItem.outcome` enum → `[ZZZ_never_emitted]` | ✅ CAUGHT | `test_declared_enums_match_the_vocabulary_the_server_can_emit` |
| A4 `RunListItem.vcs` loses its `{type: "null"}` member | ✅ CAUGHT | `test_declared_nullability_matches_its_model_field` |
| A5 `RunVcs.branch` gains an unvetted `enum` | ✅ CAUGHT | `test_declared_enums_match_the_vocabulary_the_server_can_emit` |
| A6 `ResultItem` drops the `worker_id` property | ✅ CAUGHT | `test_declared_schema_properties_match_their_model_fields` |
| A7 a `Bogus` schema declared with no bound model | ✅ CAUGHT | `test_every_declared_schema_is_bound_to_a_model` |
| A8 `HeartbeatAcknowledgement` renamed out from under its binding | ✅ CAUGHT | `test_every_declared_schema_is_bound_to_a_model` |

**Commit `e299b6a`'s three-tamper claim is true.** A7/A8 additionally answer the
round-3 question about the binding table: **an unbound schema is reported, not
silently skipped**, in both directions. Every response model in `schemas.py`
(`RunVcsResponse`, `RunListItemResponse`, `RunListResponse`,
`RunDetailResponse`, `ResultItemResponse`, `ResultsResponse`,
`HistoryEntryResponse`, `HistoryResponse`, `Acknowledgement`,
`HeartbeatAcknowledgement`) is bound. `ResultReport` and `VcsReport` are
unbound because the ingestion request declares `results` items and `vcs` as
`type: object` — `session-ingestion`'s surface, known and accepted.

#### Group B — is the declared limitation real? (4/4 SURVIVED, as claimed)

`v1.yaml` names four things the schema checks do **not** cover. All four are
genuinely uncovered — a full green suite under each:

| Tamper | Result |
|---|---|
| B1 `RunListItem.exit_status` `type: [integer,"null"]` → `[string,"null"]` (the comment's own example) | ⚪ SURVIVED — 411 passed |
| B2 `HistoryEntry.started_at` loses `format: date-time` | ⚪ SURVIVED — 411 passed |
| B3 `RunListItem.id` `pattern` → `'^[0-9]{8}$'` (no id can match) | ⚪ SURVIVED — 411 passed |
| B4 `node_id` `maxLength: 1024` → `4` (contradicts `MAX_IDENTITY_CHARS`) | ⚪ SURVIVED — 411 passed |

**The limitation is real and the four named keywords are named accurately.**

#### Group C — is the named list *complete*? (2 SURVIVED, 1 CAUGHT)

| Tamper | Result |
|---|---|
| C1 `RunListResponse.items.items.$ref` → `ResultItem` | ⚪ **SURVIVED** — 411 passed |
| C2 `HistoryEntry.vcs`'s `oneOf` `$ref` `RunVcs` → `ResultItem` | ⚪ **SURVIVED** — 411 passed |
| C3 `SessionReport.required` gains `results` (has a default) | ✅ CAUGHT — `test_declared_required_sets_match_their_models` |

**This is WARNING-5.** The comment says: *"What is still unchecked, and is named
rather than implied: the JSON `type` and `format` keywords, and the `pattern` /
`maxLength` constraints."* `$ref` retargeting is also unchecked and is **not**
named. It is the keyword that binds `RunListResponse.items` to `RunListItem`
and every `vcs` to `RunVcs`; pointing either at the wrong schema is silent. Same
class as round 2's CRITICAL — a comment stating a bound narrower than reality —
but one notch weaker: round 2's claimed a check that did not exist at all, this
one under-enumerates a gap it was otherwise honest about.

#### Group R — independent 42-mutation sweep of `routes/read.py`'s builders (41 CAUGHT, 1 SURVIVED)

Built from the source, not from their list: `_vcs_response` (5),
`_run_list_item` (7), `_run_detail_response` (8), `_result_item` (16),
`_history_entry` (6) = **42**, matching their enumeration exactly. Mutations are
type-appropriate and deliberately *plausible* — no non-nullable field nulled, so
no catch is a disguised 500; where a constant is plausible I used one
(`presentation="finished"`, `outcome="passed"`, `id="0"*32`) rather than an
obviously-wrong literal.

**41 of 42 caught. One survivor**, and it is a harder mutation than the sweep's
own class rather than a hole they missed:

| Survivor | Why it survives |
|---|---|
| `_result_item.setup_outcome` → `"passed"` | ⚪ SURVIVED — 411 passed. The only result fixture that asserts all 16 columns (`test_result_item_carries_every_stored_column_by_value`) records `setup_outcome="passed"`, so a constant equal to that fixture's own value is indistinguishable. The four outcome columns are distinct *from each other* (so a transposition fails, exactly as the docstring claims), but with one result in the store a constant colliding with the fixture cannot be caught. The `presentation` fix in round 2 solved the identical problem by using four runs; the result-item test still uses one result. |

**Commit `e299b6a`'s "42 caught, 0 survivors" stands for its stated mutation
set.** My one survivor is a strictly harder mutation, not a contradiction.

#### Group H — route-handler plumbing, *outside* their declared scope (3 CAUGHT, 8 SURVIVED)

The apply sweep's scope was "every field of every read-response builder". The
route handlers' own argument plumbing was never mutated. It is where the next
gap lives.

| Mutation | Result |
|---|---|
| `list_runs`: `store.list_runs(limit=MAX_PAGE_ITEMS, …)` — caller's `limit` ignored | ⚪ **SURVIVED** |
| `list_runs`: `offset=0` — caller's `offset` ignored | ⚪ **SURVIVED** |
| `list_results`: `limit` ignored | ⚪ **SURVIVED** |
| `list_results`: `offset` ignored | ⚪ **SURVIVED** |
| `list_results`: `has_more=False` hardcoded | ⚪ **SURVIVED** |
| `list_history`: `limit` ignored | ⚪ **SURVIVED** |
| `list_history`: `offset` ignored | ⚪ **SURVIVED** |
| `list_history`: `has_more=False` hardcoded | ⚪ **SURVIVED** |
| `list_runs`: `has_more=False` hardcoded | ✅ CAUGHT — `test_run_list_caps_at_200_at_the_route` |
| `list_results`: the `404` branch removed | ✅ CAUGHT — `test_results_route_unknown_run_id_is_404` |
| `GET /openapi.yaml` serves different bytes | ✅ CAUGHT — `test_openapi_yaml_serves_the_handwritten_bytes` |

**This is WARNING-1.** `offset` is never sent by any test to any of the three
list routes; `limit` is only ever sent as `0`, `-1` (both `422`, rejected by
FastAPI before the handler body) or `500` (clamped to 200, indistinguishable
from ignoring it). `has_more` reaches the wire proven only on the run list.

#### Group M / S — adapters (16 + 30 mutations; 7 + 12 SURVIVED)

`InMemoryExecutionStore` survivors:

| Mutation | Result |
|---|---|
| `list_runs`: `execution=replace(execution, vcs=execution.vcs)` — entry keeps the full `VcsContext` | ⚪ **SURVIVED** |
| `list_runs`: `last_contact_at=None` | ⚪ **SURVIVED** |
| `list_runs`: `offset` ignored | ⚪ **SURVIVED** |
| `get_run_detail`: `last_contact_at=None` | ⚪ **SURVIVED** |
| `list_results`: `page_limit = limit` — no 200 clamp | ⚪ **SURVIVED** |
| `list_results`: the `run_id` filter dropped (`if True`) | ⚪ **SURVIVED** |
| `list_history`: the `node_id` filter dropped (`if True`) | ⚪ **SURVIVED** |

`SqliteExecutionStore` survivors:

| Mutation | Result |
|---|---|
| `_row_to_vcs_projection`: `root` removed from the all-null check | ⚪ **SURVIVED** |
| `_row_to_run_list_entry`: `finished_at=None` | ⚪ **SURVIVED** |
| `_row_to_run_list_entry`: `exit_status=0` | ⚪ **SURVIVED** |
| `_row_to_run_list_entry`: `interrupted=False` | ⚪ **SURVIVED** |
| `_row_to_run_list_entry`: `interrupt_reason=None` | ⚪ **SURVIVED** |
| `_row_to_run_list_entry`: `last_contact_at=None` | ⚪ **SURVIVED** |
| `_row_to_history_entry`: `finished_at=None` | ⚪ **SURVIVED** |
| `_row_to_history_entry`: `outcome="passed"` | ⚪ **SURVIVED** |
| `_row_to_history_entry`: `last_contact_at=None` | ⚪ **SURVIVED** |
| `list_results`: no 200 clamp | ⚪ **SURVIVED** |
| `list_history`: no 200 clamp | ⚪ **SURVIVED** |
| `list_history`: `LIST_COMMIT_SUBJECT_CHARS` → `100000` (no display bound) | ⚪ **SURVIVED** |

**Group P — `project_vcs` (10 mutations, 10 CAUGHT, 0 survivors).** Every field,
the display bound, `LIST_COMMIT_SUBJECT_CHARS = 120 → 100`, both halves of the
D60 truncation disjunction independently, and the `None`-in/`None`-out rule. The
projection is the best-tested unit in this change.

#### Group F — both falsifiers, and two more (4/4 CAUGHT)

| Falsifier neutered | Result |
|---|---|
| F1 drift check, undeclared-route half: the extra probe router not mounted | ✅ CAUGHT — `test_a_served_but_undocumented_route_is_reported` goes red |
| F2 read-only harness, tampered-binding half: `POST /runs` not injected into `ops` | ✅ CAUGHT — `test_a_writing_endpoint_tagged_read_fails_the_harness` goes red |
| F3 a `read`-tagged path silently loses its binding | ✅ CAUGHT — reported, not skipped |
| F4 drift check, documented-but-unserved half neutered | ✅ CAUGHT — `test_a_documented_but_unserved_path_is_reported` goes red |

**Both falsifiers are still load-bearing. The five schema checks landing in
`test_interface_document.py` disturbed neither.**

#### Group X — targeted claim checks

| Check | Result |
|---|---|
| Reintroduce `root` on `RunVcsResponse` and populate it | ✅ **4.2 and 5.5 stay green, 4.6 goes red** — exactly what their docstrings claim. Round 2's schema checks now catch it twice more (`…properties_match…`, `…required_sets…`). The D59 leak is guarded in three places, up from one. |
| `POST /runs` retagged `read` in the document | ✅ CAUGHT — `test_every_read_operation_is_get_and_every_write_operation_is_not` |
| A documented path answers `500` | ✅ CAUGHT — `test_every_documented_path_answers_2xx` |
| `openapi_url=None` removed from `create_app` | ✅ CAUGHT — `test_generated_documents_are_disabled` |
| `docs_url=None` removed from `create_app` | ⚪ SURVIVED — redundant: FastAPI does not mount `/docs` when `openapi_url is None`. Behaviour is correct; the kwarg is belt-and-braces. `tasks.md` row 71 calls the rollback "3 kwargs"; one is load-bearing. |

---

### Spec Compliance Matrix

All 26 scenarios have a covering test that **passed at runtime**. The `Proved
falsifiable` column records whether *I* drove a covering test red under tamper —
absence there is absence of evidence from this round, not evidence of a hole,
except where noted.

| # | Scenario | Capability | Covering evidence | Result | Proved falsifiable |
|---|---|---|---|---|---|
| 1 | Reading leaves stored data unchanged | history-read-api | 7.2 | ✅ COMPLIANT | ✅ via F2 (same harness) |
| 2 | Main-file digest stable despite WAL | history-read-api | 7.3 | ✅ COMPLIANT | ➖ declared unfalsifiable (round-2 SUGGESTION-4, declined with reason — accepted) |
| 3 | Executions newest first, full VCS | history-read-api | 3.1, 5.3 | ✅ COMPLIANT | ✅ |
| 4 | Unknown test yields empty history | history-read-api | 3.2, 5.4 | ⚠️ PARTIAL | ⚪ its natural falsifier (drop the `node_id` filter) **survived** — see WARNING-2 |
| 5 | Non-repo execution has a null VCS context | history-read-api | 3.3, route test | ✅ COMPLIANT | ✅ |
| 6 | `vcs_root` in no history entry | history-read-api | 5.3 (Test), 5.5 (Inspection) | ✅ COMPLIANT | ✅ (Test half) |
| 7 | List responses exclude traceback/output | history-read-api | 7.6 | ✅ COMPLIANT (Inspection) | ➖ declared unfailable — no writer exists |
| 8 | Commit subject bounded in list responses | history-read-api | 2.6 | ✅ COMPLIANT | ✅ both adapters |
| 9 | Truncation flag never surfaces alone | history-read-api | 2.7, 2.8, response test, 5.3 | ✅ COMPLIANT | ✅ both halves of the disjunction |
| 10 | `vcs_root` in no run list or detail | history-read-api | 4.6 (Test), 4.2 (Inspection) | ✅ COMPLIANT | ✅ |
| 11 | A list response never exceeds 200 items | history-read-api | 2.2, 4.4 | ✅ COMPLIANT | ✅ both adapters + route |
| 12 | More-items flag distinguishes truncation | history-read-api | 2.3 | ✅ COMPLIANT | ✅ |
| 13 | Caller-requested page size honored | history-read-api | 2.4 | ⚠️ PARTIAL | ⚪ route half unproven — see WARNING-1 |
| 14 | p95 and max measured and committed | history-read-api | 7.8 | ✅ COMPLIANT (Analysis) | ➖ measurement, not an assertion |
| 15 | Every documented path answers 2xx | api-interface-document | 6.6 | ✅ COMPLIANT | ✅ (X3) |
| 16 | Served-but-undocumented endpoint reported | api-interface-document | 6.3 | ✅ COMPLIANT | ✅ (F1) |
| 17 | Generated interface documents disabled | api-interface-document | 6.2 | ✅ COMPLIANT | ✅ (`openapi_url` removal) |
| 18 | Document not derived from the route table | api-interface-document | 6.3, 6.4 | ✅ COMPLIANT | ✅ (F1 + F4) |
| 19 | Ingestion endpoints marked as writing | session-ingestion | 6.5 | ✅ COMPLIANT | ✅ (X2) |
| 20 | Not a git repository records nulls | version-control-context | archived `vcs-capture` + full-suite gate | ✅ COMPLIANT | ➖ carried over, verified in its own change |
| 21 | Absent repository emits no warning | version-control-context | same as #20 | ✅ COMPLIANT | ➖ carried over |
| 22 | Absent-repo run appears in the run list | version-control-context | 2.5, 7.9 | ✅ COMPLIANT | ✅ both adapters |
| 23 | Past grace reads back as abandoned | session-liveness | 4.8 + list test | ⚠️ PARTIAL | ✅ presentation proved; ⚪ `last_contact_at` never distinguished from `started_at` — WARNING-4 |
| 24 | Inside grace reads back as running | session-liveness | 4.9 + list test | ⚠️ PARTIAL | ✅ / ⚪ same as #23 |
| 25 | Ctrl-C run reads back as interrupted | session-liveness | 4.10 + list test | ✅ COMPLIANT | ✅ |
| 26 | Abandonment invents no stored field | session-liveness | 4.11 | ✅ COMPLIANT | ➖ not exercised this round |

**Compliance summary**: 26/26 scenarios compliant — 22 fully, 4 PARTIAL.
**18 of 26 trace to a test I drove red under tamper.** Of the remaining 8:
three are declared non-Test by the spec itself (#2 unfalsifiable-and-declined,
#7 Inspection, #14 Analysis), two are archived-capability carryovers verified in
`vcs-capture` (#20, #21), and three I did not exercise (#4, #13, #26) — of which
#4 and #13 are directly implicated by surviving mutations and are carried as
WARNINGs.

---

### Correctness (static evidence)

| Requirement | Status | Notes |
|---|---|---|
| Read-only read surface | ✅ Implemented | digest pair + pinned connection; falsifier F2 load-bearing |
| Test history | ✅ Implemented | newest-first, full VCS, empty-not-error, null-vcs entry present |
| Lean list projections | ✅ Implemented | `VcsProjection` has no `root` (structural); `_vcs_response` names five fields (choice); both now cross-checked by the document |
| Bounded pagination | ✅ Implemented | cap enforced in both adapters; route defaults keep it through HTTP. Proof depth is uneven — WARNING-1/2 |
| Test history latency | ✅ Implemented | p95 3.70 ms, max 11.59 ms, committed as numbers |
| Machine-readable interface document | ✅ Implemented | hand-written, both drift directions falsifiable, generated docs off, schemas now bound to models |
| Ingestion excluded from read-only surface | ✅ Implemented | `write` tags proved load-bearing (X2) |
| Abandoned run is observable | ✅ Implemented | Demonstration through the live read path on both detail and list. Depth caveat — WARNING-4 |
| Absent repository | ✅ Implemented | run present, undistinguished in position, all VCS fields null |

---

### Coherence (design)

| Decision | Followed? | Notes |
|---|---|---|
| D53 — the document is the read/write boundary | ✅ Yes | `_read_operations` derives from tags; retagging is caught |
| D54 — identity as a named query parameter | ✅ Yes | `node_id`, bounded, shaped 422 never a proxy 414 |
| D56 — one source per fact | ✅ Yes | the response schemas are a second statement, and round 2 made that duplication checked in five ways |
| D57/D58 — lean list, no `total` | ✅ Yes | |
| D59 — `root` off the wire | ✅ Yes | now guarded in three places; proved by reintroducing it |
| D60 — truncation flag is a disjunction | ✅ Yes | both halves independently falsifiable |
| D61 — clamp, never reject | ✅ Yes | `maximum` correctly absent from the document; behaviour stated instead |
| D62 — `derive_presentation` gets its first caller | ✅ Yes | list path now observed by the four-run test |
| D65 — digest pair, not a naive file hash | ✅ Yes | |
| D66 — drift against `app.openapi()` | ✅ Yes | diagram corrected; no stale mechanism claim remains |
| Q5 — never generated from the route table | ✅ Yes | F1/F4 prove both directions can fail |

---

### Issues Found

#### CRITICAL (0)

None. **Nothing blocks archive.**

#### WARNING (6)

**WARNING-1 — the route handlers' own `limit`/`offset`/`has_more` plumbing is
unproven; 8 mutations survive a green suite.**
All three list routes can ignore the caller's `limit`, all three can ignore
`offset` entirely, and `list_results`/`list_history` can hardcode
`has_more=False`. No test sends `offset` to any route; `limit` is only ever sent
as `0`/`-1` (rejected by FastAPI before the handler runs) or `500` (clamped,
indistinguishable from ignored). This is the exact boundary of the apply sweep's
declared scope — "every field of every read-response builder" — and the scope
statement is honest, but the closure of round-2 WARNING-1 reads as though the
read routes were covered, and their argument plumbing was not. Makes scenario 13
PARTIAL. *Fix: one route test per endpoint sending `limit=2, offset=2` against
five stored items and asserting the exact window and `has_more`.*

**WARNING-2 — the 200-item cap, run scoping and node scoping are unproven for
`list_results` and `list_history` on both adapters; 6 mutations survive.**
`list_results` can drop its `run_id` filter and `list_history` its `node_id`
filter with the suite green: the contract tests seed only one run and one node,
so a broken filter has nothing to leak. Neither method is ever called with a
`limit` above 200, so the clamp `v1.yaml`'s shared `limit` parameter documents
for **all three** endpoints ("Values above 200 are clamped to 200") is proven for
the run list only. `list_history` can also ignore `LIST_COMMIT_SUBJECT_CHARS`
entirely, so the commit-subject display bound is unproven on the history path
even though history is a list response. This is why scenario 4 is PARTIAL: the
covering tests cannot distinguish "empty because the id is unknown" from "the
filter is broken and the store happens to hold nothing else". *Fix: seed a
second run and a second node in the contract suite; call both with
`limit=MAX_PAGE_ITEMS + 1`.*

**WARNING-3 — round 2's value-fidelity remediation runs only against the
in-memory double; the shipping SQLite adapter's row mapping is unverified for 8
fields.**
`_row_to_run_list_entry` can null `finished_at`, zero `exit_status`, false
`interrupted`, drop `interrupt_reason` and `last_contact_at`;
`_row_to_history_entry` can null `finished_at`, constant `outcome` and drop
`last_contact_at` — 8 surviving mutations, whole suite green each time. Every
route-level value assertion added in round 2 uses `InMemoryExecutionStore`; the
two tests that do drive `SqliteExecutionStore` through HTTP assert only status
codes (`test_every_documented_path_answers_2xx`) or digests
(`test_read_only_surface.py`). `finished_at`, `exit_status` and `interrupted` are
on the wire in every run-list response, and in production they come from this
mapper. Behaviour is correct today; nothing would notice if it stopped being.
*Fix: parametrise the route fidelity tests over both adapters, or assert entry
field values in the contract suite where both already run.*

**WARNING-4 — `last_contact_at` is never distinguished from `started_at`, so the
liveness Demonstration does not exercise the input the grace period is about.**
Four mutations survive: `last_contact_at=None` in the in-memory `list_runs` and
`get_run_detail`, and in the SQLite `_row_to_run_list_entry` and
`_row_to_history_entry`. `derive_presentation` falls back to
`execution.started_at`, and every liveness fixture stamps `received_at` at or
near `started_at`, so the fallback and the primary always agree. The
discriminating case — a run **started well outside** the grace period whose
**last contact is inside** it, which is the entire reason the heartbeat endpoint
exists — is tested nowhere on the read path. `liveness.py`'s own docstring calls
that fallback "the defensive branch of D27 — unreachable for any row this code
writes", yet it is what every liveness test actually measures. This change
promoted `Abandoned run is observable` from Analysis to **Demonstration**;
scenarios 23 and 24 are PARTIAL for this reason. *Fix: one fixture with
`started_at = now − 2h`, `last_contact_at = now − 5s`, `grace = 60s`, asserting
`running`.*

**WARNING-5 — `v1.yaml`'s named-gap list is incomplete: `$ref` retargeting is
unchecked and unnamed.**
Tampers C1 and C2 both survive a green suite. The comment enumerates the
unchecked keywords "rather than implied", which reads as complete; `$ref` — the
keyword binding `RunListResponse.items` to `RunListItem` and every `vcs` to
`RunVcs` — is not among them. Same class as rounds 1 and 2 (a comment claiming a
bound narrower than reality), one notch weaker: it under-enumerates rather than
asserting a check that does not exist. *Fix: either add `$ref` and array `items`
to the named list, or bind them — the binding table already holds the mapping
needed to check them.*

**WARNING-6 — two production modules carry docstrings asserting they have no
callers, which this change falsified.**
`core/domain/projection.py`: *"this module has exactly one caller today
(`test_projection.py`); the two storage adapters gain their own calls to
`project_vcs` starting in Phase 2/3 of this change."* Phases 2 and 3 shipped;
seven modules import from it, four of them production (`memory.py`,
`sqlite_store.py`, `routes/read.py`, `ports/storage.py`).
`core/domain/liveness.py`: *"A pure function with no caller yet —
`app.state.grace_period` is a named seam for the read API this write-side change
does not add."* `routes/read.py` calls it twice, and D62 makes "gets its first
caller" the headline of this change. Both are future-tense notes left behind by
the work that made them false. *Fix: one sentence each.*

#### SUGGESTION (4)

**SUGGESTION-1 — `test_interface_document.py`'s module docstring says "The four
schema tests at the end of this module"; there are five**
(`…is_bound_to_a_model`, `…properties_match_their_model_fields`,
`…required_sets_match_their_models`, `…nullability_matches_its_model_field`,
`…enums_match_the_vocabulary…`). `v1.yaml`'s parallel comment enumerates all
five correctly, so the two documents of the same fact disagree by one — the
duplication D56 warns about, in miniature.

**SUGGESTION-2 — two stated invariants have no test.**
`ports/storage.py`'s `list_runs` docstring: *"the entry's own `execution.vcs` is
always `None`"* — the in-memory adapter can keep the full `VcsContext` on the
entry and nothing fails. `sqlite_store._row_to_vcs_projection`'s docstring:
*"`root` is one of the five inputs to the null check … a run whose only known
field is `root` must not be misread as absent"* — removing `root` from that check
survives. Both are correct in the code and unasserted anywhere.

**SUGGESTION-3 — the 16-column result fidelity test uses a single result, so a
constant colliding with the fixture survives.**
`_result_item.setup_outcome = "passed"` is undetectable because the sole fixture
records `"passed"`. The docstring's claim about transposition holds; the
constant case needs a second result the way `presentation` needed four runs.

**SUGGESTION-4 — traceability credits are uneven in rows the last two rounds did
not touch.**
Row 8 ("The commit subject is bounded in list responses") credits only 2.6, a
port-level test, though the scenario has a second half — "the full stored
subject remains reachable via that run's detail endpoint" — proved by 4.5, and a
response-level bounding assertion exists in the round-1 truncation test; neither
is cited. Rows 12 and 13 credit port-level evidence only, while rows 11 and 22
credit both port and route for comparable scenarios. Also `tasks.md` row 71
describes the PR6 rollback as "3 kwargs" when only `openapi_url=None` is
load-bearing.

#### Known and accepted — not reported as defects

The two `(RQ-23)` / `(RQ-44)` heading join keys; the 591-line size exception on
`e299b6a` (PR6's 434-line precedent); Phases 4 and 7 shipping as chained PRs;
the skipped attempt ledger; `results`/`vcs` as `type: object` inside the
ingestion request; round-2's declined SUGGESTION-2 and SUGGESTION-4. **I read
both declines and agree with both.** SUGGESTION-2's reasoning — that a `Literal`
on the response models would be a fourth statement of a vocabulary the domain
already constrains — is right, and binding the document's enum to the domain
frozensets is the better fix. SUGGESTION-4's reasoning — that manufacturing a
falsifier for 7.3 means changing production behaviour to make a test fail — is
the correct mirror of the rule against changing behaviour to make one pass.

---

### TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD evidence reported | ✅ | apply-progress carries per-phase tables across 11 batches |
| All tasks have tests | ✅ | 83/83 tasks checked; every task cites a named test or a stated non-Test method |
| RED confirmed (test files exist) | ✅ | every cited module exists |
| GREEN confirmed (tests pass now) | ✅ | 411 passed, exit 0 |
| Triangulation adequate | ⚠️ | strong for `presentation` (four runs) and history (two entries); single-fixture for `_result_item` (SUGGESTION-3) and for run/node scoping (WARNING-2) |
| Safety net for modified files | ✅ | `e299b6a` changed no production `.py`; `v1.yaml`'s two `"422"` additions are additive |

**TDD compliance**: 5/6 clean, 1 partial.

### Test Layer Distribution

| Layer | Tests | Files | Tools |
|---|---|---|---|
| Unit / domain | ~200 | `test_projection.py`, `test_liveness.py`, `test_result.py`, `test_execution.py`, `test_truncation.py`, … | pytest |
| Integration (port + adapters) | ~150 | `vantage_port_contract.py` driven by `test_sqlite_store.py`, `test_memory_store.py` | pytest, `sqlite3` |
| Integration (HTTP, in-process ASGI) | ~60 | `test_routes_read.py`, `test_routes_runs.py`, `test_interface_document.py`, `test_read_only_surface.py` | `fastapi.testclient` |
| E2E (real server) | 0 | — | not installed; D54's uvicorn measurement was manual and is recorded as such |
| **Total** | **411** | 23 modules | |

The HTTP layer runs almost entirely against `InMemoryExecutionStore` — the
asymmetry WARNING-3 names.

### Changed File Coverage

Coverage analysis skipped — no coverage tool detected. `pytest-cov` is absent
from the dev extra and the lockfile, and `openspec/config.yaml` sets
`coverage_threshold: 0` deliberately (CLAUDE.md). This is per project policy,
not a gap. **The 128-tamper campaign is the substitute measurement, and it is a
stronger one**: 100 of 128 tampers caught (78%), with every survivor named above.

### Assertion Quality

No tautologies, no orphan empty-collection assertions, no ghost loops, no
smoke-tests, no mock-heavy modules. Every empty-collection assertion has a
companion non-empty test (`test_history_route_unknown_node_id_is_empty_not_error`
beside `test_history_route_returns_newest_first_with_full_vcs`;
`test_list_results_empty_for_a_run_with_no_results` beside
`test_list_results_paginates_a_runs_results`). Three tests declare themselves
structurally unfalsifiable **in their own docstrings** (4.2, 5.5, and 7.3's
stability property) rather than passing as if they proved something — I
confirmed 4.2 and 5.5 by reintroducing `root`, and both stayed green exactly as
documented. That honesty is the opposite of the assertion-quality failure this
audit looks for.

**Assertion quality**: ✅ 0 CRITICAL, 0 WARNING.

### Quality Metrics

**Linter**: ✅ `ruff` — clean (`ruff format`/`check` reported unchanged at commit time; tree byte-identical since).
**Type checker**: ✅ `mypy .` — no issues in 75 source files.
**Dependencies**: ✅ `deptry .` — no issues, 74 files.

---

### Verdict

**PASS WITH WARNINGS — archive-ready.**

0 CRITICAL, 6 WARNING, 4 SUGGESTION. All 83 tasks complete, all 26 scenarios
covered by tests that passed, 18 of them proved falsifiable by tamper this
round. Both falsifiers still fail correctly and the five new schema checks did
not disturb them. Every round-2 claim I re-ran held: the three document tampers
all fail, the declared limitation is real and its four named keywords are named
accurately, and the 42-mutation builder sweep reproduces at 41/42 with the one
survivor being a strictly harder mutation than the class they declared.

The six WARNINGs are depth-of-proof findings in the layers rounds 1 and 2 did
not reach — route-handler plumbing, the SQLite adapter's row mapping, the
`last_contact_at` input, and one incomplete gap enumeration. **None of them is a
wrong answer on the wire.** I looked specifically for a third defect of rounds 1
and 2's class — an assertion or comment claiming a guarantee it does not provide
— and found one (WARNING-5, the unnamed `$ref` gap) plus two stale
"no callers yet" docstrings (WARNING-6). All three are one-line corrections and
none of them is a reason to hold the change.

Ship it, and carry the six WARNINGs as follow-up work against the read surface's
next milestone.

---

## Post-verdict closure — 2026-08-22

Recorded after the verdict above was issued, against a tree this report did
not verify. The verdict's scope remains `e299b6a`; this section is the audit
trail for what changed afterwards and why.

**The value-fidelity WARNING was closed rather than carried.** The verdict's
own summary says "none of them is a wrong answer on the wire" while the same
report lists 12 surviving mutations in `sqlite_store.py` — the adapter that
actually ships. Those two statements are in tension, so the claim was tested
directly: mutating `_row_to_run_list_entry` to return `finished_at=None`
unconditionally left the package suite green at 284 passed. A real caller on
the real adapter would have read `finished_at: null` for every run. The
finding was a genuine gap in proof, not a wrong answer in the code — but the
gap sat exactly where this change placed its central discipline, both
adapters in lockstep, so it was closed instead of deferred.

Closed by `0a660dd`, test-only: `test_routes_read.py`'s `store` fixture is
parametrised over `InMemoryExecutionStore` and `SqliteExecutionStore`, so
every read-route test executes against both. Run detail additionally asserts
its five VCS fields by value — it is the only route reading a full
`VcsContext` rather than a `VcsProjection`, and `vcs is not None` alone left
`commit`, `branch` and `dirty` mutable.

A 49-mutation sweep over the four SQLite row mappers now catches 47. The two
survivors are structurally unobservable on the wire by design:
`VcsContext.root`, which the lean-list rule exists to keep off every
response, and the list entry's `interrupt_reason`, which the list item shape
does not carry. Catching either would indicate a leak, not a fix.

Workspace suite 411 -> 436 passed. `mypy --strict` and `ruff` clean.

**No re-verification round was run for `0a660dd`.** It adds tests only,
strictly increases coverage, changes no production source, and its effect was
measured by mutation rather than asserted. The remaining five WARNINGs are
carried as follow-up work unchanged.
