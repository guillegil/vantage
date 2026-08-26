```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:689e90515b091c074bc4a35af5cbb10fd124dd2f8f2c80ca4c3398ca0c298a00
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 12/12
scenarios: 35/35
test_command: uv run pytest -q
test_exit_code: 0
test_output_hash: sha256:11babba001aa94e17d65559820e4340c2feefd4a30ff11e64c3d6c11f2825bbf
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:3bce484934e38822ff7a68a9eb1d47d2dd53c92386f38d0dd24152acfb6fab7c
```

## Verification Report

**Change**: failure-capture
**Round**: 2 (supersedes ROUND 1, verdict FAIL — treated as stale input throughout)
**Version**: N/A
**Mode**: Strict TDD
**Commit verified**: `7fc7677`, branch `ft/failure-capture-11d-ini-not-a-means`, working tree clean

### Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 117 |
| Tasks complete | 117 |
| Tasks incomplete | 0 |
| Requirements (delta specs) | 12 |
| Scenarios (delta specs) | 35 |

Counts independently derived from the five delta specs, not taken from the
brief or from round 1:

| Spec file | Requirements | Scenarios |
|---|---|---|
| `specs/failure-evidence/spec.md` | 7 | 17 |
| `specs/history-read-api/spec.md` | 2 | 7 |
| `specs/result-capture/spec.md` | 0 | 0 |
| `specs/run-recording/spec.md` | 1 | 6 |
| `specs/session-ingestion/spec.md` | 2 | 5 |
| **Total** | **12** | **35** |

`result-capture` legitimately carries 0/0: it is a Purpose-only delta with an
explicit archiver directive stating that `result-capture`'s `## Requirements`
are unchanged and that the delta deliberately carries no
`ADDED`/`MODIFIED`/`REMOVED` block. Round 1's admitted total of 34 scenarios
is short by one; the missing scenario is *The opt-in enables failure-text
capture*, added by the opt-in rewrite of the `Capture is opt-in, absent by
default` requirement.

### Build & Tests Execution

**Build**: PASSED

```text
$ uv run mypy .          -> exit 0   Success: no issues found in 81 source files
$ uv run deptry .        -> exit 0   Success! No dependency issues found. (80 files)
$ uv run ruff check .    -> exit 0   All checks passed!
$ uv run ruff format --check .       81 files already formatted
```

**Tests**: PASSED — 539 passed, 0 failed, 0 skipped, 12 warnings

```text
$ uv run pytest -q       -> exit 0   539 passed, 12 warnings in 49.95s
$ uv run pytest -n 4 -q  -> exit 0   539 passed, 12 warnings in 20.53s   (xdist, RQ-12/RQ-27)
```

Collected: 175 tests in `pytest-vantage`, 363 in `vantage`.

**Coverage**: Not available — this project measures no coverage by deliberate
decision (`openspec/config.yaml` sets `coverage_threshold: 0`; no `pytest-cov`
in the dev extra or the lockfile). No figure is reported.

### Spec Compliance Matrix

35/35 scenarios COMPLIANT. Every row was confirmed by a test that passed at
runtime in the 539-test run above, except the two rows whose spec-declared
verification method is Inspection or Analysis — those were confirmed by the
method the spec itself declares, and are marked as such.

#### `failure-evidence` (7 requirements, 17 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Traceback capture invariant to display flags | complete under `--tb=no` | `test_evidence.py:306` | COMPLIANT |
| Traceback capture invariant to display flags | complete under `--tb=line` | `test_evidence.py:339` | COMPLIANT |
| Failure location, type and message | twenty tests at one line group as one | `test_evidence.py:397` | COMPLIANT |
| Failure location, type and message | recorded location is the raising site | `test_evidence.py:421` | COMPLIANT |
| Failure location, type and message | skipped test does not crash the recorder | `test_evidence.py:447` | COMPLIANT |
| Captured output, empty distinct from absent | silent test has empty output, not absent | `test_evidence.py:553`, `test_capture.py:473` | COMPLIANT |
| Captured output, empty distinct from absent | capture disabled leaves output absent | `test_evidence.py:574` | COMPLIANT |
| Per-field 64 KiB bound | oversized field stored truncated, flagged | `test_routes_runs.py:146` | COMPLIANT |
| Per-field 64 KiB bound | field within bound whole, unflagged | `test_routes_runs.py:159` | COMPLIANT |
| Per-report failure-text budget | many large failures stay within the cap | `test_run_report.py:716` | COMPLIANT |
| Per-report failure-text budget | dropped field is flagged, not missing | `test_report_budget.py:148` | COMPLIANT |
| Per-report failure-text budget | within budget carries no exhaustion flags | `test_report_budget.py:163` | COMPLIANT |
| Capture is opt-in, absent by default | absent the opt-in, no failure text captured | `test_evidence.py:137` | COMPLIANT |
| Capture is opt-in, absent by default | the opt-in enables capture | `test_evidence.py:177` | COMPLIANT |
| Capture is opt-in, absent by default | committed config file cannot enable capture | `test_opt_in.py:172`, `test_opt_in.py:87` | COMPLIANT |
| Capture is opt-in, absent by default | absence does not suppress the rest of the result | `test_evidence.py:219` | COMPLIANT |
| Unredacted storage is disclosed | disclosure in capability spec and README | Inspection: `specs/failure-evidence/spec.md:249-261`, `README.md:70-77` | COMPLIANT (Inspection) |

#### `history-read-api` (2 requirements, 7 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Lean list projections | list excludes traceback and captured output | `test_routes_read.py:739`, `test_routes_read.py:767` | COMPLIANT |
| Lean list projections | commit subject bounded in list responses | `test_projection.py:38`, `vantage_port_contract.py:811` | COMPLIANT |
| Lean list projections | truncation flag never surfaces independently | `test_projection.py:118`, `vantage_port_contract.py:834` | COMPLIANT |
| Lean list projections | `vcs_root` in no list or detail response | `test_routes_read.py:325`, `:460`, `:1134` | COMPLIANT |
| Single result detail | full record reachable | `vantage_port_contract.py:1058`, `test_routes_read.py:793` | COMPLIANT |
| Single result detail | truncation flag travels on single-item endpoint | `test_routes_read.py` (`..._truncation_flag_travels_with_the_field`) | COMPLIANT |
| Single result detail | unknown identifier leaves stored data unchanged | `vantage_port_contract.py` (`get_result` miss), route 404 tests | COMPLIANT |

#### `run-recording` (1 requirement, 6 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Run atomicity (RQ-3) | server killed mid-write (RQ-3.1) | Analysis; premises `test_rejection.py:405`, `:514` | COMPLIANT (Analysis) |
| Run atomicity (RQ-3) | truncated in transit, no prior start-write | `test_rejection.py` truncation tests | COMPLIANT |
| Run atomicity (RQ-3) | finish truncated after accepted start-write | `test_rejection.py:476` | COMPLIANT |
| Run atomicity (RQ-3) | normal report fully present (RQ-3.3) | `test_rejection.py:405` | COMPLIANT |
| Run atomicity (RQ-3) | reordered start-write never nulls a finish | `test_rejection.py:540`, `vantage_port_contract.py:277` | COMPLIANT |
| Run atomicity (RQ-3) | measurements re-run for the new column set | Analysis, re-run this phase — see below | COMPLIANT (Analysis) |

The last row is the one Analysis obligation this change newly incurs, so it
was re-executed rather than read:

```text
$ uv run pytest packages/vantage/tests/test_rejection.py \
    -k "five_hundred_results_fit_within_the_body_cap or server_peak_memory_for_one_five_hundred_result_request" -s -q
500-result report size: 252511 bytes (cap 1048576)
peak traced memory for one 500-result request: 2878382 bytes
2 passed, 21 deselected
```

The spec's Measurements paragraph claims 252,511 bytes and 2,880,085 bytes.
The body size reproduces exactly. Peak memory reproduces to within 1,703 bytes
(0.06%), which is ordinary `tracemalloc` run-to-run variation, not drift. The
Analysis record is sound.

#### `session-ingestion` (2 requirements, 5 scenarios)

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Optional failure-evidence fields | older plugin omitting fields still stores | `test_ingestion.py:330` | COMPLIANT |
| Optional failure-evidence fields | newer plugin's fields are persisted | `test_ingestion.py:351` | COMPLIANT |
| Optional failure-evidence fields | older server tolerates newer plugin's fields | `test_ingestion.py:374` | COMPLIANT |
| Whole-report rejection at the size cap | report over the cap stores nothing | `test_rejection.py:179` | COMPLIANT |
| Whole-report rejection at the size cap | report with evidence within the cap accepted | `test_ingestion.py:398` | COMPLIANT |

#### `result-capture` (0 requirements, 0 scenarios)

Purpose-text-only delta. Verified by inspection: the delta's archiver
directive is explicit and self-consistent, and its replacement `## Purpose`
correctly cross-references `failure-evidence` for what a failed result adds.

**Compliance summary**: 35/35 scenarios COMPLIANT.

### Tamper Proofs Executed This Phase

No claim below is carried on the strength of a passing test alone. Each rule
was tampered, the rule-specific symptom observed, and the tree restored and
confirmed byte-clean with `git diff --quiet`.

| # | Rule under test | Tamper applied | Symptom observed | Restored clean |
|---|---|---|---|---|
| 1 | D75 truncation-flag disjunction at all 7 budgeted fields | replaced `bool(item.X_truncated) or X_cut` with the naive `X_cut` at all 7 sites in `routes/runs.py` | **7 failed** — every parametrised case of `test_to_result_disjunction_holds_at_every_budgeted_field` (`failure_message`, `failure_repr`, `traceback`, `skip_reason`, `xfail_reason`, `captured_stdout`, `captured_stderr`) | yes |
| 2 | The invocation flag is the only means of enabling capture | restored the three-parameter form: `resolve_failure_text_capture(*, activated, cli_opt_in, ini_opt_in)` returning `activated and (cli_opt_in or ini_opt_in)`, plus `addini`/`getini` in `plugin.py` | **11 failed, 2 passed**; the behavioural guard `test_a_committed_ini_cannot_be_the_means_by_which_capture_is_enabled` failed on `assert True is False` — a committed ini flipped capture on — and the signature guard on `assert {'activated','cli_opt_in','ini_opt_in'} == {'activated','cli_opt_in'}` | yes |
| 3 | Capture polarity is opt-in, not opt-out (the RQ-25 lever) | `cli_opt_in=bool(config.getoption(...))` -> `cli_opt_in=True`, simulating the pre-flip on-by-default polarity | **2 failed, 173 passed** — `test_absent_flag_means_evidencecollector_is_never_registered` and `test_a_committed_ini_cannot_be_the_means_by_which_capture_is_enabled`, two independent guards | yes |

Tamper 1 is the direct re-adjudication of round 1's CRITICAL. Round 1 found
6 of 7 fields undefended (the naive assignment left the suite green at
`failure_repr`, `traceback`, `skip_reason`, `xfail_reason`, `stdout`,
`stderr`). All seven now fail. **Round 1's CRITICAL is closed, and closed at
the fields that matter most**: `budget.py` drops `traceback`,
`captured_stdout` and `captured_stderr` first, so the three fields the budget
most often drops are now defended where previously none of them were. The fix
was test-only; no production code changed, which the tamper independently
confirms — the pre-tamper behaviour was already correct.

Tamper 3 is the one worth stating positively: the headline product decision of
this change does not rest on a single test. Two independent guards, one a
subprocess registration probe and one a behavioural config double, both catch
a silent return to on-by-default capture.

### TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD evidence reported | PASS (non-standard shape) | Not the prescribed "TDD Cycle Evidence" table; carried per-task in `tasks.md` as 71 `RED` and 30 `GREEN` annotations across 117 tasks, plus 3 recorded tamper proofs, plus the apply-progress artifact's own verification block |
| All tasks have tests | PASS | Every scenario-bearing task names a concrete test file and test name; all resolve to files that exist |
| RED confirmed (tests exist) | PASS | Every test file named in the scenario map exists and collects |
| GREEN confirmed (tests pass) | PASS | 539/539 pass at `7fc7677`, single-process and under `-n 4` |
| Triangulation adequate | PASS | The D75 invariant is parametrised over all 7 fields; `resolve_failure_text_capture` is covered by an exhaustive 4-row truth table plus two monotonicity property tests; the opt-in has both a negative and a positive registration case |
| Safety net for modified files | PASS | Full-suite gate recorded at every phase; independently re-confirmed here |

**TDD compliance**: 6/6 checks passed. The one deviation is the shape of the
evidence, not its substance, and I re-derived the two load-bearing guards
myself by tampering rather than trusting the report.

### Test Layer Distribution

| Layer | Tests | Files | Tools |
|---|---|---|---|
| Unit (pure functions, domain, projection, config) | ~310 | 12 | pytest |
| Integration (routes via TestClient, port contract over both stores, in-process HTTP server) | ~200 | 6 | pytest, FastAPI TestClient, `VantageTestServer` |
| E2E (real subprocess pytest -> HTTP -> server, incl. xdist) | ~29 | 3 | `pytest.Pytester` `runpytest_subprocess`, xdist |
| **Total** | **539** | **21** | |

Counts are approximate bucket sizes derived from file classification; the
total is exact. The port contract (`vantage_port_contract.py`) is inherited by
both the in-memory and SQLite store test classes, so every storage assertion
runs twice against genuinely different adapters — which is what keeps the
`request.app.state.store: Any` weakness (WARNING 6) from being able to hide a
row-mapper defect.

### Changed File Coverage

Coverage analysis skipped — no coverage tool is installed and none is
configured. This is a deliberate project decision, not a gap introduced here.

### Assertion Quality

Audited all 19 test files this change touched.

**Assertion quality**: All assertions verify real behaviour. 0 CRITICAL, 0 WARNING.

Two patterns were investigated and cleared rather than assumed benign:

- `assert True` appears 14 times in `test_xdist_capture.py` and
  `test_result_capture.py`. Both are **untouched by this change**
  (`git diff --numstat` against the change base returns empty for them), and
  in both the occurrences sit inside string literals such as `_SIX_TESTS` —
  they are the synthetic *subject* tests handed to `pytester`, not the
  assertions of the verifying test. Not tautologies.
- `assert ... is not None` appears 10 times in `test_routes_runs.py` and 16
  in `test_evidence.py`. Every occurrence is a narrowing guard immediately
  followed by a value assertion on the same object in the same test — the
  combined form the audit permits, not a type-only assertion standing alone.

No ghost loops, no smoke-only tests, no mock-heavy files, no orphan
empty-collection assertions.

### Quality Metrics

**Linter**: PASS — `ruff check` clean, `ruff format --check` reports 81 files already formatted.
**Type checker**: PASS — `mypy .` strict, no issues in 81 source files.
**Dependency hygiene**: PASS — `deptry .` clean over 80 files.

### Correctness (Static Evidence)

| Claim | Status | Evidence |
|---|---|---|
| `resolve_failure_text_capture` has no ini surface | VERIFIED | `config.py:86` signature is `(*, activated: bool, cli_opt_in: bool)`; body is `return activated and cli_opt_in`. No `addini("vantage_failure_text")` anywhere in `src/`; only two `addini` calls remain (`vantage_server`, `vantage_timeout`). Tamper 2 confirms the guards bite. |
| The D75 disjunction holds at all 7 fields | VERIFIED | `routes/runs.py:165,167,169,171,173,221,223` — each `bool(item.X_truncated) or X_cut`. Tamper 1 confirms all 7 are defended. |
| `schema.sql` unchanged across the whole change | VERIFIED | `git diff --stat 51996b4 HEAD -- '*schema.sql'` returns empty. |
| RQ-24 — `pytest-vantage` third-party-free | VERIFIED | `deptry` clean; plugin imports only pytest and the standard library. |
| RQ-2 — activation is the flag, not a file | VERIFIED | `_activation_requested` (`plugin.py:127`) reads `config.getoption("vantage")` alone; the failure-text opt-in now follows the identical precedent. |
| RQ-12/RQ-27 — xdist | VERIFIED | Full suite green under `-n 4`; worker branch registers `EvidenceCollector` only, never a `Recorder`. |
| Round-1 "flaky" `test_capability_probe_404_...` | NOT REPRODUCED | 15/15 passes in isolation, plus green in both full-suite runs. Not carried as a finding, per instruction and per my own inability to reproduce it. |

### Coherence (Design)

| Decision | Followed? | Notes |
|---|---|---|
| D68 — evidence collected by a second plugin object on workers | Yes | `pytest_configure` worker branch registers `EvidenceCollector` and returns; no `Recorder` on a worker. |
| D69 — rendering via `_repr_failure_py(excinfo, style="long")` | Yes | Probed against installed pytest 9.1.1. Round 1's stale diagram at `design.md:847` is fixed — `design.md:894` now reads `_repr_failure_py`. |
| D72 — opt-in, invocation flag the only means | Yes | Code matches the revised decision exactly, including the ini-row "removed — no ini surface exists". |
| D74 — budget charges what the wire charges | Yes | `budget.py:73` is `len(json.dumps(value).encode("utf-8"))` — bare, so `ensure_ascii=True`, matching `transport.send`. Pinned by a dedicated non-ASCII test at `test_report_budget.py:213-235`. |
| D75 — truncation flag is a disjunction | Yes | Behaviour correct and now defended at all 7 fields. |
| D80 — column arithmetic | Yes | Round 1's two stale sites are fixed; `design.md:953` and `:1035` now read 31/33. `FailureEvidence` 13 + `CapturedOutput` 4 = 17 new columns, consistent. |
| D81 — exactly one ADR | Yes | `docs/adr/0016-...md`, deliberately `Status: Proposed` until merge. Not flagged. |

All four design.md claims round 1 found "confidently wrong" have been probed
again this round against the installed code. Three were corrected and are now
accurate; none remains wrong.

### Issues Found

**CRITICAL**: None.

**BLOCKER**: None. Round 1's single blocker is re-adjudicated below and does
not block archive.

#### Re-adjudication of round 1's BLOCKER — RQ-25

**Verdict: no longer blocks archive.**

RQ-25's normative text
(`docs/legacy/notion-2026-08-18/requirements.md:457-467`) bounds the overhead
of *recording*: "the system shall add no more than 2 percent to the
wall-clock duration of a suite of 1,000 tests each taking approximately 10
milliseconds", and its criterion 1 measures a suite "run five times **with
recording** and five times **without**".

After the opt-in flip, the quantity RQ-25 actually measures is the A-vs-OFF
comparison — recording on, failure capture absent, which is what every
session does unless its invocation asks otherwise. That is **0.15%–0.64%**
across all six measured cells, comfortably inside the 2% budget. The 3.45%+
figures that made round 1's blocker are the B column, a path the invocation
must explicitly request per session and whose cost is measured, published
unadjusted in the capability spec, and knowingly accepted by whoever passes
the flag. RQ-25's declared verification method is **Analysis**, and the
Measurements paragraph is a conforming analysis record.

The residual question — whether a session that *has* opted in deserves a
cheaper mechanism — is explicitly open at `docs/open-questions.md` OQ-11,
which also records that a failure-count cap was considered and rejected on
its own arithmetic (roughly four failures per session before the budget is
spent). That is the project's own documented mechanism for an open question,
correctly used.

**What artifact would be needed to unblock it, if it did block**: none is
creatable in the intended shape, and none is needed. The project forbids
minting new `RQ-xx` identifiers (CLAUDE.md, decided 2026-08-18), so an
"RQ-25 exception record" cannot be written as a new requirement. The correct
home for the residual is exactly where it now sits: the capability spec's
Measurements paragraph plus OQ-11. Archive is not gated on further artifacts.

**WARNING**

1. **`pytest --help` advertises an ini surface that does not exist, and
   contradicts this change's own spec clause.**
   `packages/pytest-vantage/src/pytest_vantage/plugin.py:81` reads "capture
   never happens unless this or the ini equivalent is given". Confirmed
   user-visible — `uv run pytest --help` renders that sentence verbatim. The
   ini surface was removed in `7fc7677`; `README.md:68` correctly says "an
   invocation flag only -- there is no ini equivalent". A user following the
   help text into `pytest.ini` gets `PytestConfigWarning: Unknown config
   option` and silently no capture. More seriously, the help text asserts the
   very thing the requirement forbids ("no committed configuration file MAY
   be the means by which capture is enabled"). Behaviour is correct and
   double-guarded; **the shipped documentation is not**. Documentation-only
   fix, one sentence.

2. **Stale docstring on the composition function.**
   `plugin.py:145` still describes `_failure_text_capture_requested` as
   composing "both opt-in surfaces" through "the single monotone
   **disjunction**". There is now one surface and the composition is a
   **conjunction**. Same root cause as WARNING 1 — the ini removal did not
   sweep `plugin.py`'s prose. `config.py:86-107`'s docstring, by contrast, is
   fully correct and even states the invariant explicitly.

3. **RQ-25 still has no requirement record in `openspec/specs/`.**
   Downgraded from round 1's BLOCKER but not closed. Four capability specs
   cite RQ-25 (`session-liveness`, `result-capture`,
   `version-control-context`, and this change's `failure-evidence`), yet its
   normative text exists only in `docs/legacy/notion-2026-08-18/`, which
   CLAUDE.md describes as "frozen, authoritative of nothing, and scheduled
   for deletion". When that directory is deleted, the 2% budget four specs
   depend on disappears from the repository. This is project-wide migration
   debt that predates this change and that this change cannot fix (no new
   `RQ-xx` may be minted; the remedy is to migrate RQ-25's text into a
   capability spec as separate work). **Not archive-blocking for
   `failure-capture`**, but it should not be lost when the legacy directory
   is removed.

4. **`tasks.md`'s scenario-coverage table is stale against the current spec.**
   `tasks.md:353-391` states "All 29 new/modified scenarios trace to at least
   one task"; the specs now carry 35. Row 13 names *The opt-out suppresses
   failure-text capture* and row 15 *The opt-out does not suppress the rest
   of the result* — scenarios that no longer exist after the polarity flip —
   and the table has no row for *The opt-in enables failure-text capture*,
   which is precisely the 35th scenario that invalidated round 1's evidence.
   **This is not a coverage gap**: all four current opt-in scenarios have
   passing covering tests (`test_evidence.py:137`, `:177`, `:219`,
   `test_opt_in.py:172`), verified above. It is a traceability index that
   would mislead a reader. Noted as distinct from the Phase 10 task entries
   the brief exempts as frozen record: this is a change-wide index that
   claims a total, not a phase-history line.

5. **The interface document still does not declare the 17 new ingestion
   fields.** `packages/vantage/src/vantage/service/openapi/v1.yaml:123` types
   `SessionReport.results` items as `{type: object}`. The read side is
   correctly declared (`/runs/{run_id}/result`, `FailureProjection`,
   `ResultDetailResponse` all present and `$ref`ed). Pre-existing shape
   widened by this change; carried unchanged from round 1.

6. **`request.app.state.store` is `Any` at every route call site.**
   Untouched by this change and, as round 1 concluded, unprotected rather
   than broken. Re-confirmed that the mitigation still holds: the port
   contract is inherited by both store test classes and `test_routes_read.py`
   parametrises the store fixture over `["memory", "sqlite"]`, so every
   route-level value assertion runs against the real SQLite row mappers.

7. **Four chain slices exceed the 400-line review budget; round 1 named
   three.** Measured independently, each branch against its own predecessor:
   `06-ingestion` 429 (7.25% over), `07-storage` 527 (31.75% over),
   `09-measurements` 409 (2.25% over), `10-verify-remediation` 445 (11.25%
   over). The fourth is new since round 1 and is **de minimis in substance**:
   393 of its 445 lines are round 1's own `verify-report.md` artifact, leaving
   52 authored lines (46 in `test_routes_runs.py`, 6 in `design.md`). Under
   the "authored text additions plus deletions" rule, slice 10 is well inside
   budget. The remaining sixteen slices are within budget.

8. **Whole-change size continues to run over forecast.** `tasks.md:27`
   forecast ~3,160 changed lines across nine slices. Measured from tracker
   base `51996b4` to `HEAD`: 45 files, 5,002 insertions, 343 deletions =
   **5,345 changed lines across twenty slices**, 69% over forecast and more
   than twice the planned slice count. Round 1 measured 4,487 across fifteen;
   the growth since is the verify remediation and the opt-in rewrite, both of
   which were unplanned responses to findings rather than scope creep.

9. **`tasks.md:179` (task 5.3) still specifies `ensure_ascii=False`.**
   Carried unchanged from round 1. **Not a coverage gap** — I re-confirmed
   `budget.py:73` is the bare `json.dumps(value)` the wire actually uses, and
   a dedicated non-ASCII test now pins that contract at
   `test_report_budget.py:213-235`. Documentation drift only, and it sits in
   a completed phase entry the project treats as frozen record.

10. **Strict-TDD evidence is not in the prescribed table shape.** The
    `apply-progress` artifact carries no "TDD Cycle Evidence" table. The
    substance is present and richer than the table would be — 71 `RED` and 30
    `GREEN` per-task annotations in `tasks.md`, three recorded tamper proofs,
    and a per-session verification block — so this is reported as a format
    deviation rather than a protocol failure. I did not rely on it: the two
    load-bearing guards were re-proved by my own tampering.

**SUGGESTION**

11. `packages/vantage/src/vantage/core/domain/liveness.py:4` still reads "A
    pure function with no caller yet". It now has one:
    `packages/vantage/src/vantage/service/routes/read.py:46` imports
    `derive_presentation`. Carried from round 1 and now demonstrably false
    rather than merely stale. Untouched by this change.

12. `test_report_budget.py:48,56` still state the cost formula as
    `json.dumps(value, ensure_ascii=False)` in the docstring and local
    computation of the encoded-vs-raw test. The value used is ASCII-only so
    the two encodings agree and the test is not wrong, but the stated formula
    is not the one `budget.py` uses. Aligning it would remove the last
    echo of the D74 defect.

13. `test_absent_flag_does_not_suppress_outcome_timings_or_identity`
    (`test_evidence.py:219`) has a live server result in hand and asserts
    outcome, identity and timings, but does not assert `result.failure is
    None`. The scenario's "recorded without a traceback, failure fields or
    captured output" half is currently proved through the registration proxy
    at `test_evidence.py:137` — mechanically sound, since `EvidenceCollector`
    is the only writer of those fields, and tamper 3 confirms the proxy bites.
    One added line would make it end-to-end.

14. `vantage_port_contract.py::test_list_results_projects_failure_evidence_via_failure_projection`
    is an *agreement* test between `list_results` and `project_failure`, not a
    bound check — both sides move together because `sqlite_store.py` binds the
    shared constant into the SQL. The actual bound is pinned by
    `test_projection.py::test_project_failure_bounds_message_to_200_chars_and_flags`.
    Worth a docstring note so the agreement test is not mistaken for the guard.

### Round 1 Findings — Disposition

| # | Round 1 finding | Round 2 disposition |
|---|---|---|
| 1 | BLOCKER — RQ-25 breached at every density | **Resolved as a breach.** Opt-in flip makes the RQ-25-measured path 0.15–0.64%. Residual is WARNING 3 (missing requirement record), not a blocker. |
| 2 | CRITICAL — D75 defended at 1 of 7 fields | **Closed.** Tamper-verified 7/7 fail under the naive assignment. |
| 3 | WARNING — three stale design.md sites | **Closed.** `repr_failure` diagram now `_repr_failure_py`; both 27/29-column sites now 31/33. Re-probed, all accurate. |
| 4 | WARNING — `tasks.md:179` `ensure_ascii=False` | **Persists** as WARNING 9. Documentation only; fix genuinely defended. |
| 5 | WARNING — 17 ingestion fields undeclared in the interface document | **Persists** as WARNING 5, unchanged. |
| 6 | WARNING — `store` is `Any` | **Persists** as WARNING 6; mitigation re-confirmed, unprotected not broken. |
| 7 | WARNING — three slices over review budget | **Persists and grew** to four (WARNING 7); the new one is 52 authored lines. |
| 8 | WARNING — apply-progress task figure stale | **Persists**, figure moved 61 -> 69 while `tasks.md` carries 117. Bookkeeping only; completion is genuinely 117/117. Folded into WARNING 10's artifact-hygiene theme. |
| 9 | WARNING — whole-change size over forecast | **Persists and grew** to 5,345 lines / 20 slices (WARNING 8). |
| 10 | SUGGESTION — `liveness.py` stale docstring | **Persists** as SUGGESTION 11, now demonstrably false. |
| 11 | SUGGESTION — non-ASCII value in budget test | **Partially closed.** A dedicated non-ASCII wire test now exists at `test_report_budget.py:213-235`; the older test's docstring remains (SUGGESTION 12). |
| 12 | SUGGESTION — agreement test naming | **Persists** as SUGGESTION 14. |
| — | Reported flaky `test_capability_probe_404_...` | **Not reproduced** in 15 isolated runs plus two full-suite runs and an xdist run. Not carried as a finding. |
| — | Superseded by the opt-in flip | Round 1's opt-out scenarios and the failure-count-cap question are both closed by the polarity flip and OQ-11. |

Two new items appear this round, both introduced by the ini removal that
fixed a real defect: WARNING 1 and WARNING 2. Both are documentation.

### Verdict

**PASS WITH WARNINGS**

All 117 tasks complete, all 12 requirements and all 35 scenarios compliant
with passing runtime evidence, every gate green, and both of round 1's
blocking findings closed — the D75 CRITICAL by a fix I tamper-verified at all
seven fields, and the RQ-25 BLOCKER by a product decision that makes the
default path measurably compliant. Ten warnings and four suggestions remain;
none blocks archive. The two most actionable are documentation defects
introduced by the otherwise-correct ini removal: `pytest --help` and one
docstring still advertise an ini surface that no longer exists and that this
change's own spec forbids.
