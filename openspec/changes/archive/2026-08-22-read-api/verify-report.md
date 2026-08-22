```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:0a55b9de2157c8fbf31ab2a0d9b4f5bcdb3a3ee92c64ebabce6a55e24e2e945a
verdict: fail
blockers: 2
critical_findings: 2
requirements: 9/9
scenarios: 24/26
test_command: uv run pytest
test_exit_code: 0
test_output_hash: sha256:ba5ab03e933dfafab355387e74dcb146cff9172b4dbeae33800c1573300175cf
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:abcb6647b13baa8244d90bb839deeab7f27878b24d4a96ce9a221f280e0e8a6c
```

## Verification Report

**Change**: `read-api`
**Round**: 1
**Commit verified**: `58ff060` (`ft/read-api-07b-measurement-closeout`), working tree clean
**Mode**: Strict TDD
**Method**: every claim below was checked by *planting the failure* and re-running,
not by reading code. 19 mutations were applied to production source and reverted;
the working tree is byte-clean afterwards (`git status --porcelain` empty).

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 83 |
| Tasks complete | 83 |
| Tasks incomplete | 0 |

Counted independently of the checkboxes: `rg -c '^- \[x\] '` = 83, `^- \[ \] ` = 0,
and the per-phase distribution is 8 / 14 / 10 / 15 / 11 / 13 / 12 = 83, matching
`sdd/read-api/apply-progress` (id 88) exactly. `gentle-ai sdd-status read-api`
independently reports `tasks: 83/83 complete`, `verify: ready`.

Substance spot-checks beyond the checkbox: ADR-0015 is `Status: Accepted`
(`docs/adr/0015-scope-the-read-only-guarantee-to-a-named-read-surface.md:7`);
OQ-9 is `**Answered** 2026-08-21 — ADR-15` in both the table row and the body
section of `docs/open-questions.md`; `docs/api/v1-ingestion.md` had its request-shape
example and response-status table removed and a contract header added (task 6.12);
`schema.sql` has a zero-line diff from `aa5c801` to `HEAD` (task 7.12, D63).

### Build & Tests Execution

**Build / type-check**: ✅ Passed
```text
uv run mypy .   →  exit 0
Success: no issues found in 75 source files
```

**Tests**: ✅ 400 passed
```text
uv run pytest   →  exit 0
400 passed in 47.96s
```

**Dependency audit**: ✅ `uv run deptry .` → `Success! No dependency issues found.` (74 files)

**Coverage**: ➖ Not available. `coverage_threshold: 0` is set deliberately in
`openspec/config.yaml` and no `pytest-cov` exists in the dev extra or the
lockfile. Not a failure; recorded per project convention.

**Not run locally** (unchanged from apply's own honest statement): only Python
3.10.21 was exercised. The 3.11/3.12/3.13 legs, the networking-disabled job, the
Python-3.9-refusal job and the clean-environment install check are left to CI.

### Falsifier verification — both load-bearing falsifiers run tampered

Neither was accepted by reading the code. Each was made to fail.

| # | Tamper applied | Result |
|---|---|---|
| A | `_logical_content_digest` neutered to `return b"TAMPERED-CONSTANT"` | `test_a_writing_endpoint_tagged_read_fails_the_harness` **FAILED** (`assert b'TAMPERED-CONSTANT' != b'TAMPERED-CONSTANT'`). The PR7 falsifier's own assertion is real, not a tautology. Note 7.2/7.3 stayed green under this tamper — which is precisely why 7.1 has to exist. |
| B | `GET /api/v1/runs` made to call `touch_last_contact` | `test_logical_content_digest_unchanged_after_every_read_path` **FAILED** on a genuine digest mismatch. The read-only proof detects a read path that writes. |
| C | An undeclared `GET /api/v1/_secretly-added` mounted on the **real** app | `test_a_served_but_undocumented_route_is_reported` **FAILED**, reporting `{('GET', '/_secretly-added')}`. The PR6 drift falsifier's undeclared-route half is genuinely failable. |
| D | `/phantom-endpoint` added to `openapi/v1.yaml`, never mounted | `test_a_documented_but_unserved_path_is_reported`, `test_every_documented_path_answers_2xx` and `test_every_read_path_has_a_binding` **all FAILED**. The reverse drift direction and the PR7 binding-completeness guard are both failable. |
| E | `root` added back to `RunVcsResponse` and populated from the source object | `test_run_detail_response_contains_no_vcs_root` **FAILED**. See CRITICAL-1 for what did *not* fail. |
| M19 | A read-tagged binding made to write **and** force `PRAGMA wal_checkpoint(TRUNCATE)` mid-sequence | `test_main_file_digest_stable_despite_wal_checkpointing` **FAILED**. The weak half of the digest pair reacts to real on-disk bytes, not a cached value. |

**The interface document is hand-written, never derived — confirmed.**
`openapi/v1.yaml` is a static file inside the distribution; I edited it by hand
(tamper D) and produced drift the check reported. A derived document could not
have been edited into disagreement with itself. `_mounted_operations` reads
`app.openapi()` for the *mounted* side only — see WARNING-4 on that deviation.

### Independent reproduction of the committed measurements

I re-ran `scripts/measure_history_latency.py` end to end on this machine.

| Figure | Committed in spec | My re-run | Verdict |
|---|---|---|---|
| p95 (nearest-rank, n=200) | 3.70 ms | **4.41 ms** | Same shape, same order of magnitude |
| max (slowest single) | 11.59 ms | **12.98 ms** | Same shape |
| D63 10 ms profile OFF | 11.062 s | **11.042 s** | Agrees |
| D63 10 ms profile ON | 11.160 s | **11.142 s** | Agrees |
| D63 delta | 97.7 ms (0.88 %) | **100.7 ms (0.91 %)** | Agrees within 3 ms |

**The numbers are real and transcribed from a real run, not copied from the
design.** `design.md` contains no p95/max figures at all, so there was nothing to
copy; the printed format (`{:.2f} ms`, `OFF=… ON=… delta=…ms (…%)`) matches the
committed text character for character. Internal arithmetic all checks out:
100 − 3.70 = 96.3 ms headroom ✓; 2 % of 11.062 s = 221.24 ms ✓; 221.2 − 97.7 =
123.5 ms ✓; 97.7 / 11062 = 0.883 % → `0.88` ✓. The `11.062`/`11.160` pair with a
97.7 ms delta sits exactly on the `.3f` rounding boundary and is self-consistent.

**Scrutiny of the variance explanation — see WARNING-5.** The spec explains the
164.8 ms → 97.7 ms shift against `version-control-context`'s recorded figure
(`openspec/specs/version-control-context/spec.md:142`, OFF 10.981 s / ON 11.146 s /
164.8 ms / 1.50 %) as "ordinary variance the paired, interleaved methodology
already accounts for". My independent re-run got **100.7 ms** — clustering tightly
with 97.7 ms and nowhere near 164.8 ms.

### Spec Compliance Matrix

26 scenarios across 9 requirements in 5 delta specs. Every entry below was
confirmed by execution; the "Proven failable by" column records the mutation that
made the covering test go red.

| # | Scenario (capability) | Covering test | Proven failable by | Result |
|---|---|---|---|---|
| 1 | Reading leaves stored data unchanged (history-read-api) | `test_read_only_surface.py::test_logical_content_digest_unchanged_after_every_read_path` | Tamper B | ✅ COMPLIANT |
| 2 | Main-file digest stable despite WAL checkpointing | `…::test_main_file_digest_stable_despite_wal_checkpointing` | M19 | ✅ COMPLIANT |
| 3 | Executions newest first, with full VCS context | `vantage_port_contract.py::test_list_history_orders_newest_first_with_full_vcs` (port) + `test_routes_read.py::test_history_route_returns_newest_first_with_full_vcs` (route) | M5 (ordering, both layers) — but **not** the enumerated values at the route | ⚠️ PARTIAL — see CRITICAL-2 |
| 4 | Unknown test yields empty history, not an error | `…::test_list_history_unknown_node_id_is_empty_not_error` + `test_history_route_unknown_node_id_is_empty_not_error` | M10 | ✅ COMPLIANT |
| 5 | Non-repository execution has a null VCS context | `…::test_list_history_null_vcs_entry_present_not_omitted` | M6 | ✅ COMPLIANT |
| 6 | `vcs_root` appears in no history entry | credited to `test_history_route_response_contains_no_vcs_root` (**unfailable, self-declared**); actual protection is `test_history_route_returns_newest_first_with_full_vcs`'s key-set assertion | Tamper E (via the key-set test, not the credited one) | ✅ COMPLIANT via an uncredited test |
| 7 | List responses exclude traceback and captured output | **Inspection** — comment at `test_routes_read.py:410-418` | n/a — declared unfailable | ✅ Honestly recorded (see below) |
| 8 | Commit subject bounded in list responses | `…::test_list_runs_bounds_commit_subject_at_display_width` + `test_projection.py::test_subject_bounded_at_120_chars_sets_flag` | M4 | ✅ COMPLIANT |
| 9 | Truncation flag never surfaces independently of its subject | `…::test_list_runs_flags_capture_truncated_subject_even_when_short`, `…_null_subject_flag_is_false_not_null` (port only) | M4 at the port; **nothing at the response layer** (M8) | ⚠️ PARTIAL — see CRITICAL-2 |
| 10 | `vcs_root` in no run list or run detail response | `test_projection.py::test_vcs_projection_has_no_root_field`, `test_run_detail_response_contains_no_vcs_root` | Tamper E | ✅ COMPLIANT — but one credited test is unfailable, see CRITICAL-1 |
| 11 | A list response never exceeds 200 items | `…::test_list_runs_caps_at_200_items` + `test_run_list_caps_at_200_at_the_route` | M1, M2 | ✅ COMPLIANT |
| 12 | More-items flag distinguishes truncation from exhaustion | `…::test_list_runs_has_more_distinguishes_exhaustion_from_truncation` | M2 | ✅ COMPLIANT |
| 13 | A caller-requested page size under the cap is honored | `…::test_list_runs_honors_a_smaller_requested_page_size` | M3 | ✅ COMPLIANT |
| 14 | p95 and max measured and committed as numbers | **Analysis** — `scripts/measure_history_latency.py`, re-run by this verify round | n/a — Analysis | ✅ COMPLIANT (reproduced) |
| 15 | Every documented path answers 2xx (api-interface-document) | `test_interface_document.py::test_every_documented_path_answers_2xx` | Tamper D | ✅ COMPLIANT |
| 16 | A served-but-undocumented endpoint is reported | `…::test_a_served_but_undocumented_route_is_reported` | Tamper C | ✅ COMPLIANT |
| 17 | The generated interface documents are disabled | `…::test_generated_documents_are_disabled` | M9 | ✅ COMPLIANT |
| 18 | The document is not derived from the route table it checks | `…::test_a_served_but_undocumented_route_is_reported` + `…::test_a_documented_but_unserved_path_is_reported` | Tampers C and D (both directions) | ✅ COMPLIANT |
| 19 | Ingestion endpoints are marked as writing, not reading (session-ingestion) | `…::test_every_read_operation_is_get_and_every_write_operation_is_not` | Tamper D (document edit changes the read/write partition) | ✅ COMPLIANT |
| 20 | Not a git repository records nulls (version-control-context) | pre-existing `packages/pytest-vantage/tests/test_vcs.py::test_not_a_repository_records_nulls_and_no_warning` | unchanged by this delta | ✅ COMPLIANT |
| 21 | Absent repository emits no warning | same test as #20 | unchanged by this delta | ✅ COMPLIANT |
| 22 | Absent repository's run appears in the run list | `vantage_port_contract.py::test_list_runs_includes_absent_repository_run_undistinguished` (asserts **position** `[c,b,a]`) + `test_routes_read.py::test_absent_repository_run_appears_in_list_undistinguished` (live `GET /api/v1/runs`) | M6 | ✅ COMPLIANT — the deferral is genuinely discharged |
| 23 | A run past its grace period reads back as abandoned (session-liveness) | `test_routes_read.py::test_abandoned_run_reads_back_as_abandoned` | M7 | ✅ COMPLIANT |
| 24 | A run inside its grace period reads back as running | `…::test_running_run_reads_back_as_running` | M17 | ✅ COMPLIANT |
| 25 | A Ctrl-C interrupted run reads back as interrupted | `…::test_interrupted_run_reads_back_as_interrupted` | M18 | ✅ COMPLIANT |
| 26 | Abandonment invents no stored field | `…::test_abandonment_invents_no_stored_field` | M7 | ✅ COMPLIANT |

**Compliance summary**: 24/26 fully compliant, 2 PARTIAL (#3, #9), 0 UNTESTED,
0 FAILING. Every one of the 26 has at least one covering test that passed at
runtime in this round.

### Specific claims checked, one by one

**`vcs_root` never reaches the wire — TRUE, and the mechanism is as claimed.**
No `model_validate(..., from_attributes=True)` and no whole-object mapping exists
anywhere in `vantage.service`: the only `model_validate` in the package is
`routes/runs.py:249`, `SessionReport.model_validate(payload_dict)`, a dict-based
ingestion parse on the write path. `_vcs_response` (`routes/read.py:73-84`) reads
exactly five names and never `root`. `RunVcsResponse` has no `root` field. The
detail-path test does assert on the **raw serialized body**
(`assert _KNOWN_ROOT not in response.text`, `test_routes_read.py:267`) and it
genuinely fails when `root` is reintroduced (Tamper E). ✅

**`session-liveness` reads back over HTTP, not by calling the helper — TRUE.**
All four tests (`test_routes_read.py:282-381`) drive `GET /api/v1/runs/{run_id}`
through a real ASGI `TestClient` and assert on `response.json()["presentation"]`;
none imports `derive_presentation`. No clock control is used — `last_contact_at`
is stamped relative to a `now` the test computes and the grace period is
configured per client. Deriving "abandoned" invents no stored field: `derive_presentation`
is pure, returns a `str`, and nothing persists it; `test_abandonment_invents_no_stored_field`
reads the row back via `store.get_execution` and M7 proves it failable. ✅

**Architecture — TRUE, checked against the diff, not just the architecture test.**
`git diff aa5c801 HEAD -- .../core .../storage | rg '^\+(import|from) '` yields only
`__future__`, `dataclasses`, `typing`, and intra-`vantage` imports. No `pydantic`
or `fastapi` import exists anywhere outside `vantage.service`.
`git diff --name-only aa5c801 HEAD -- packages/pytest-vantage` returns **0 files**.
`schema.sql` diff is **0 lines**, so D63's "the write path pays nothing" holds
structurally, not by argument. ✅

**`version-control-context` → *Absent repository* promotion — TRUE and complete.**
The delta promotes the run-list criterion to Test and retires both the deferral
paragraph and the stand-in scenario. A real test demonstrates it through the live
list endpoint (`test_absent_repository_run_appears_in_list_undistinguished`,
`GET /api/v1/runs`), and the port-level contract test additionally asserts the
**positional** half (`[c,b,a]` ordering) that the route-level test's set-based
assertion does not. Both fail under M6. ✅ See SUGGESTION-3 on the retired
scenario's still-live test name.

**The traceback half of *Lean list projections* is recorded as Inspection and is
not counted as Test-verified anywhere — CONFIRMED, in four independent places:**
`specs/history-read-api/spec.md:90-100` (verification split + the scenario's own
`AND this check cannot fail today` line); `tasks.md` row 7 of the scenario table
("Scenario 7 is Inspection, not Test"); task 7.6 itself; the 9-line comment at
`test_routes_read.py:410-418`; and `openapi/v1.yaml:144-147`. No assertion of the
form `"traceback" not in item` exists in the suite. ✅

---

### Issues Found

#### CRITICAL

**CRITICAL-1 — `test_run_list_response_contains_no_vcs_root_anywhere` cannot fail,
and its docstring claims the opposite. This is the one nobody caught.**

*File*: `packages/vantage/tests/test_routes_read.py:154-171`
*Claimed* (docstring, lines 157-160): "A substring assertion on the raw serialized
body — **the only test shape that catches an accidental `from_attributes`
passthrough of `VcsContext.root`**."
*Actually true*: it catches nothing. Proven by Tamper E — I added
`root: str | None = None` to `RunVcsResponse` and populated it in `_vcs_response`.
`test_run_detail_response_contains_no_vcs_root` failed, as it should.
**`test_run_list_response_contains_no_vcs_root_anywhere` stayed green.** It cannot
fail, for two independent structural reasons: on the list path the source object
is `VcsProjection`, which has no `root` field (`projection.py:29-44`), and both
adapters additionally strip the context off the entry
(`memory.py:202` `replace(execution, vcs=None)`; `sqlite_store.py` `vcs=None,  # the lean
projection rides beside it`). `_KNOWN_ROOT` has no path to that body.

*Why this is CRITICAL and not a nit*: this change's defining discipline is that a
check must be able to fail. The project already knows this test is unfailable —
its sibling at line 499 says so explicitly: "**Structurally unfalsifiable, like
4.2**". So the codebase declares 4.2 unfalsifiable *in another test's docstring*
while 4.2's own docstring declares it uniquely falsifiable, and `tasks.md`'s
scenario table (row 10) credits it as Test with no Inspection marking — contrast
row 7, which is explicitly marked Inspection. An unfailable check recorded as a
passing Test is exactly the failure mode OQ-10 rejected a generated interface
document over. The obligation itself is not endangered (scenario 10 is genuinely
covered by `test_vcs_projection_has_no_root_field` and by the detail-path test),
but the record is untrue.

*Fix shape (do not apply here)*: rewrite the docstring to state the structural
truth — as line 499 already does — and mark row 10's `4.2` entry as Inspection in
the `tasks.md` scenario table, or delete the test as redundant with
`test_vcs_projection_has_no_root_field`.

**CRITICAL-2 — the read routes are never checked for *value* fidelity. Five
distinct fields can be silently destroyed on the wire with all 400 tests green.**

*Files*: `packages/vantage/src/vantage/service/routes/read.py:73-84` (`_vcs_response`),
`:144-154` (`_history_entry`); tests `packages/vantage/tests/test_routes_read.py:107-148`
(4.1) and `:434-473` (5.3).

Each mutation below was applied alone to production source and the **full
`packages/vantage/tests` suite re-run**:

| Mutation | Suite result |
|---|---|
| `_vcs_response(commit_subject_truncated=False)` — hardcode the flag | **273 passed, 0 failed** |
| `_history_entry(duration=None)` — destroy the duration | **273 passed, 0 failed** |
| `_vcs_response(dirty=None)` — destroy the dirty flag | **273 passed, 0 failed** |
| `_vcs_response(commit=None)` — destroy the commit hash | **273 passed, 0 failed** |
| `_vcs_response(branch=None)` | 1 failed (the single value assertion that exists) |
| `_vcs_response(commit_subject=None)` | 1 failed — detail path only; the list/history paths caught nothing |

*What is claimed*: `tasks.md`'s scenario table credits scenario 3 (*Executions
return newest first, with full VCS context*) to "3.1 (contract), **5.3 (route)**".
The scenario text enumerates six things each entry carries: "its **commit**,
**branch**, **commit subject**, **truncation flag**, **dirty flag**, and
**duration**".
*What is actually true*: the route test asserts ordering (real — M5 kills it), a
**key set** (`set(item["vcs"].keys()) == {...}`, real — Tamper E kills it), and
exactly **one** value: `body["items"][0]["vcs"]["branch"] == "feature"`. Five of the
six enumerated values are unasserted at the route. The credited route-level
coverage is a shape check, not a content check.

Separately, scenario 9 (*The truncation flag never surfaces independently of its
subject*) is written explicitly about responses — "WHEN that run's commit subject
appears in **any response, list or detail**, THEN the truncation flag is present
alongside it **in that same response**" — and the requirement prose says the flag
"MUST **travel with** the subject wherever the subject appears". `tasks.md`
credits it to 2.7 and 2.8, both port-level. There is **no response-level value
check anywhere**, and a wire that always answers `commit_subject_truncated: false`
beside a subject bounded to 120 characters — the exact dishonesty the proposal
argued the flag exists to prevent ("a client rendering a truncated subject as the
commit's subject misrepresents git") — ships green.

*Why this is CRITICAL*: the implementation is correct today; I read it and it
wires every field. But the traceability claim "5.3 (route)" is materially untrue
for what the scenario enumerates, and the one genuinely-failable instance of the
lean-list rule — the one the proposal singled out as what rescues that rule from
vacuity — is unverified at the layer its own scenario names. A response-shaping
regression in either helper is undetectable by this suite.

*Fix shape (do not apply here)*: add value assertions to 4.1/5.3 (assert the full
`item` dict against an expected literal, not just its key set), and add one
response-level test for scenario 9: a stored subject over 120 characters served in
a list response, asserting both the bounded value **and**
`commit_subject_truncated is True` in the same body.

#### WARNING

**WARNING-1 — the two MODIFIED delta specs will not match their target
requirement headings at archive time.** The merged capability specs still carry
the numeric suffix this change deliberately drops:

| Merged spec heading | Delta heading |
|---|---|
| `### Requirement: Absent repository (RQ-23)` (`openspec/specs/version-control-context/spec.md:46`) | `### Requirement: Absent repository` |
| `### Requirement: Abandoned run is observable (RQ-44)` (`openspec/specs/session-liveness/spec.md:71`) | `### Requirement: Abandoned run is observable` |

There is **no `openspec` binary in this environment** — archiving is a manual/agent
edit (precedent: commit `177466a`, "archive session-lifecycle and merge its
deltas"). If the merge matches requirement blocks by heading text, neither MODIFIED
block matches and both would be appended as *new* requirements, leaving the retired
"not claimed as met" deferral paragraph and the promoted version side by side in
one file. This is the direct consequence of the accepted mixed-vocabulary state
meeting an archive step, and it is the top archive risk. Good news: the retired
stand-in scenario *does* live inside the `Absent repository` block
(`openspec/specs/version-control-context/spec.md:71`), so a correct block-level
replacement retires it cleanly — the only thing at risk is the matching.

**WARNING-2 — `session-liveness`'s delta `## Purpose` has no archive precedent in
this repository and will not survive a requirement-block-only merge.**
Every archived delta carrying a `## Purpose` is an **ADDED** capability whose whole
file became the merged spec (`2026-08-19-session-lifecycle/specs/session-liveness`,
`2026-08-20-vcs-capture/specs/version-control-context`, the eight milestone-1
deltas). **No MODIFIED delta in this repository's history has ever rewritten an
existing capability's Purpose.** If the Purpose is not merged by hand,
`openspec/specs/session-liveness/spec.md:3-13` keeps saying "**Write side only**…
presenting the derived state waits for a read API that does not exist yet, so
RQ-44's read-back criteria are Analysis against the derivation helper here, not
Demonstration through a live read path" — while the requirement block directly
below it would say "**Verification: Demonstration**, through the live read path."
A merged spec that contradicts itself in two adjacent sections. Archive must
replace that Purpose explicitly.

**WARNING-3 — scenario 6's credited coverage is the unfailable test, not the one
that works.** `tasks.md`'s scenario table maps *`vcs_root` appears in no history
entry* to task **5.5 alone**. I proved 5.5 unfailable (Tamper E: green). The
scenario is in fact protected — by `test_history_route_returns_newest_first_with_full_vcs`'s
exact-key-set assertion, which Tamper E does kill — but that test is not credited
for it, and row 6 carries no Inspection marking (unlike row 7). 5.5's own docstring
is honest; the traceability table is not.

**WARNING-4 — design deviation: D66 says the drift check compares against
`app.routes`; the implementation reads `app.openapi()`.**
`design.md:511` — "D66 — The drift check compares the document against
`app.routes`". `test_interface_document.py:73-94` uses `app.openapi()` instead,
with a clear docstring explaining why (this FastAPI version resolves included
routers lazily behind a private `_IncludedRouter` wrapper, so `app.routes` no longer
yields flat `APIRoute` instances). The reasoning is sound and I verified the
mechanism works in both directions (Tampers C and D), and it does not derive the
document — only the mounted side. But the design decision text was not updated,
so the design and the code disagree in writing. Does not break a spec.

**WARNING-5 — the "ordinary run-to-run variance" explanation understates what the
data shows.** `specs/history-read-api/spec.md:173-182` explains the 164.8 ms →
97.7 ms shift as "ordinary variance the paired, interleaved methodology already
accounts for (medians, not means)". My independent re-run measured **100.7 ms** —
within 3 ms of 97.7 ms and 64 ms away from 164.8 ms. Two independent runs
clustering that tightly, both far from the archived figure, look like a
**systematic** difference (different machine state / date / kernel) rather than
symmetric noise. The paragraph's own supporting evidence is stronger than "variance":
`OFF` itself moved 81 ms between the two recorded sittings (10.981 s → 11.062 s), a
shift comparable in magnitude to the 67 ms change in the delta. Nothing here breaks
an obligation — the direction is favourable and the conservative reading survives
intact: even at the archived 164.8 ms / ≈55 ms headroom, the read path spends
**zero** of it, because `schema.sql` is unchanged and no index was added. But the
spec should say "reproducible on this machine, differing systematically from the
2026-08-20 sitting" rather than "ordinary variance", or the next reader will treat
97.7 ms as interchangeable with 164.8 ms.

**WARNING-6 — the document's `limit` bound is not enforced and not drift-checked.**
`openapi/v1.yaml:97` declares `limit: {schema: {type: integer, minimum: 1, maximum: 200}}`.
`routes/read.py:160` binds `limit: int = Query(default=MAX_PAGE_ITEMS, gt=0)` with no
upper bound; `limit=500` answers `200 OK` with 200 items rather than `422`. The
document says one thing, the server does another. The drift check compares only
`(method, path)` sets, so no test can see it. The *Bounded pagination* obligation
is still satisfied (the store clamps — M1/M2/M3 all prove it), and the
*Machine-readable interface document* obligation is only about **valid** input
reaching 2xx, which holds. Recorded because it is a real document/implementation
divergence in a change whose whole point is that the document is the contract.

#### SUGGESTION

**SUGGESTION-1 — task 6.11's stated outcome differs slightly from what happened.**
The task says "Run `uv run deptry .`; confirm it passes with **no new ignore entry
needed**". `pyproject.toml` gained `types-PyYAML` to `DEP002` (and `measure_vcs_overhead`
to `DEP001` for task 7.7). The substance is intact — `pyyaml` itself needed no ignore
because it is a genuine `import yaml`; the added entry is for the mypy **stub**
package, documented inline. The checkbox text just no longer describes the outcome.

**SUGGESTION-2 — `test_absent_repository_run_appears_in_list_undistinguished`
asserts on a `set`, discarding the "position" half.** `test_routes_read.py:610-613`
uses `set(items) == {repo_run_id, absent_run_id}`, which by construction cannot
observe positional distinction, even though the scenario says "in no way
distinguished **in position** or omission". The gap is covered by the port-level
contract test (which asserts `[c,b,a]`), so the scenario is compliant — but the
route-level demonstration would be stronger asserting the ordered list.

**SUGGESTION-3 — a test named after a retired scenario is still live.**
`vantage_port_contract.py:618::test_absent_repository_run_is_retrievable_in_storage_pending_a_run_list`
still carries "pending a run list" in its name. Nothing is pending any more. The
assertion is still true and should stay; the name should lose the deferral.

**SUGGESTION-4 — `_read_bindings` exercises only the happy path.** The read-only
proof calls each read path once with valid input. A read path that writes only on
its 404 or 422 branch (an audit-log-on-miss, say) would not be caught. Cheap to
close: add the 404/422 variants to the binding table.

---

### Correctness (Static Evidence)

| Requirement | Status | Notes |
|------------|--------|-------|
| Read-only read surface (history-read-api) | ✅ Implemented | digest pair over document-derived read set; both halves proven failable |
| Test history | ⚠️ Implemented, under-verified at the wire | CRITICAL-2 |
| Lean list projections | ⚠️ Implemented, under-verified at the wire | CRITICAL-2; traceback half honestly Inspection |
| Bounded pagination | ✅ Implemented | clamp + fetch-one-extra, both adapters, route and port |
| Test history latency | ✅ Measured | reproduced independently this round |
| Machine-readable interface document (api-interface-document) | ✅ Implemented | hand-written, drift-checked both directions |
| Ingestion endpoints excluded from read-only surface (session-ingestion) | ✅ Implemented | `write` tags asserted, partition checked |
| Absent repository (version-control-context) | ✅ Implemented | promoted to Test through the live list |
| Abandoned run is observable (session-liveness) | ✅ Implemented | Demonstration over HTTP, all four scenarios failable |

### Coherence (Design)

| Decision | Followed? | Notes |
|----------|-----------|-------|
| D53 read surface = document `read` tags | ✅ Yes | `_read_operations` derives from `v1.yaml`; Tamper D proves it |
| D54 identity as query parameter, 1024 bound | ✅ Yes | corrected reasoning recorded in test docstring; 422 not 414 |
| D55 hand-written YAML inside the distribution | ✅ Yes | `importlib.resources` anchor, served as raw bytes |
| D56 `v1-ingestion.md` stops enumerating paths | ✅ Yes | shape example + status table removed, reasoning kept |
| D57–D61 projection, split types, pagination, total order | ✅ Yes | all proven failable (M1–M5) |
| D62 presentation derived at the service layer | ✅ Yes | `derive_presentation` has its first caller; port holds no clock |
| D63 no new index | ✅ Yes | `schema.sql` diff is 0 lines; latency reproduced |
| D64 percentile → committed number | ✅ Yes | nearest-rank, warm-ups discarded, reproduced |
| D65 digest pair, separate fixtures | ✅ Yes | PR6 and PR7 fixtures are distinct; connection pinned |
| **D66 drift check vs `app.routes`** | ⚠️ Deviated | uses `app.openapi()`; reasoned in code, not updated in design — WARNING-4 |
| D67 exactly one ADR | ✅ Yes | ADR-0015, `Accepted` |

### TDD Compliance

| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | ✅ | TDD Cycle Evidence table present in `apply-progress` (id 88) |
| All tasks have tests | ✅ | 83/83; the 4 non-test tasks (7.6 Inspection, 7.7/7.8 Analysis, 7.10/7.11 docs) are declared as such |
| RED confirmed (test files exist) | ✅ | all 6 new/modified test files exist and are collected |
| GREEN confirmed (tests pass) | ✅ | 400/400 on re-execution this round |
| Triangulation adequate | ⚠️ | adequate at the port layer; **absent at the response layer** — CRITICAL-2 |
| Safety net for modified files | ✅ | apply reports 20/20 pre-existing green before the `test_routes_read.py` edit; the full suite is green now |

### Test Layer Distribution

| Layer | Tests | Files | Tools |
|-------|-------|-------|-------|
| Unit / pure | 10 | `test_projection.py`, `test_storage_types.py` | pytest |
| Contract (both adapters) | 16 × 2 | `vantage_port_contract.py` via `test_sqlite_store.py`, `test_memory_store.py` | pytest |
| Integration (ASGI, in-process) | 30 | `test_routes_read.py`, `test_interface_document.py`, `test_read_only_surface.py` | starlette `TestClient`, `httpx2` |
| Analysis (manual, never CI) | 1 script | `scripts/measure_history_latency.py` | run by hand |
| **Whole workspace** | **400** | | |

### Assertion Quality

| File | Line | Assertion | Issue | Severity |
|------|------|-----------|-------|----------|
| `test_routes_read.py` | 171 | `assert _KNOWN_ROOT not in response.text` | Cannot fail; docstring claims it can | CRITICAL |
| `test_routes_read.py` | 514 | `assert _KNOWN_ROOT not in response.text` | Cannot fail; honestly declared in docstring, but credited as Test in `tasks.md` | WARNING |
| `test_routes_read.py` | 465-472 | `set(item["vcs"].keys()) == {...}` | Key-set only; no value assertion for commit, subject, truncation flag, dirty, duration | CRITICAL |
| `test_routes_read.py` | 131-148 | `set(item.keys()) == {...}`, `set(item["vcs"].keys()) == {...}` | Same — shape without content | CRITICAL |
| `test_routes_read.py` | 611 | `set(items) == {...}` | Set discards the "position" half of the scenario | SUGGESTION |

No tautologies, no ghost loops, no orphan-empty assertions, no mock-heavy tests,
and no smoke-test-only tests were found. The two `for item in body["items"]` loops
are both preceded by an assertion that fixes the collection non-empty, so neither
is a ghost loop.

**Assertion quality**: 3 CRITICAL entries (rolling up to CRITICAL-1 and
CRITICAL-2), 1 WARNING, 1 SUGGESTION.

### Quality Metrics

**Linter**: ✅ `uv run ruff format --check` / `ruff check` clean (working tree
byte-identical after 19 applied-and-reverted mutations).
**Type checker**: ✅ `uv run mypy .` — no issues in 75 source files.
**Dependencies**: ✅ `uv run deptry .` — no issues.
**Coverage**: ➖ not measured, by deliberate project policy.

### Explicitly not reported as defects (accepted by the maintainer)

- The mixed `RQ-` vocabulary. Independently confirmed: **zero** `RQ-` occurrences
  in `openspec/changes/read-api/`, `docs/adr/0015-*.md`, and every new source and
  test file of this change. The 13 merged specs under `openspec/specs/` still carry
  numbers — known and accepted. (WARNING-1 reports the *archive-matching*
  consequence of that state, not the state itself.)
- PR6 at 434 code+test lines over the 400 ceiling, and PR7 at 470 — deliberate
  maintainer rulings; `tasks.md` churn and `uv.lock` regeneration excluded.
- Phase 4 and Phase 7 shipping as `04a`/`04b` and `07a`/`07b` under `auto-chain`.
- `results` items and `vcs` remaining `type: object` in the **ingestion request**
  schema (`openapi/v1.yaml:103`) — that belongs to `session-ingestion`.

### Verdict

**FAIL** — 2 CRITICAL, 6 WARNING, 4 SUGGESTION.

This change is very close and its hard parts are genuinely proven: both
load-bearing falsifiers fail when tampered, the read-only digest pair detects a
writing read path *and* a forced WAL checkpoint, the interface document is
demonstrably hand-written, the committed latency numbers reproduce independently,
the architecture boundaries hold in the diff, `schema.sql` never moved, and 24 of
26 scenarios trace to a covering test I confirmed both passed and can fail.

What blocks archive is the same class of defect this change was built to hunt, one
layer up from where it was hunted. The port layer was triangulated ruthlessly; the
**response** layer was not. Four fields — the commit hash, the dirty flag, the
truncation flag and the duration — can each be destroyed on the wire with all 400
tests green, and one `vcs_root` assertion advertises in its own docstring a
falsifiability it provably does not have while a sibling test already names it as
unfalsifiable. Both are cheap to fix and neither requires re-opening a design
decision.

Two archive-time hazards (WARNING-1, WARNING-2) must be handled by whoever performs
the merge, or `openspec/specs/session-liveness/spec.md` will contradict itself in
adjacent sections and `version-control-context` may carry both a retired deferral
and its own retirement.
