```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:25027fabfd4b05673aafe0c3c2250e14b0c96fe8caee7edfd80cebd954216fea
verdict: fail
blockers: 1
critical_findings: 2
requirements: 9/16
scenarios: 35/45
test_command: uv run --extra dev pytest -q
test_exit_code: 0
test_output_hash: sha256:76ac4c60897a28265f3c3916bbdc6a5b32a6989e8907dc19364c7538e631345f
build_command: uv build --wheel --all-packages -o dist
build_exit_code: 0
build_output_hash: sha256:dc6a72b6caec1751db2d61dadd8e9a4901849c762ce4ec7a1ae0c480e35adc29
```

## Verification Report

**Change**: milestone-1-write-one-row
**Tip verified**: `8b0d666` on `milestone-1-write-one-row-pr14` (working tree clean)
**Version**: 8 capability specs, 16 requirements, 45 scenarios
**Mode**: Strict TDD

### Completeness

| Metric | Value |
|---|---|
| Tasks total | 62 |
| Tasks complete | 62 |
| Tasks incomplete | 0 (`rg -c '^- \[ \]' tasks.md` = 0) |

### Build & Tests Execution

All five gates run for real on the tip. All green.

| Gate | Command | Exit | Result |
|---|---|---|---|
| Tests | `uv run --extra dev pytest -q` | 0 | 107 passed in 10.62s |
| Tests (xdist) | `uv run --extra dev pytest -q -n 4` | 0 | 107 passed in 5.25s |
| Format | `ruff format --check .` | 0 | 49 files already formatted |
| Lint | `ruff check .` | 0 | All checks passed |
| Types | `mypy .` | 0 | no issues in 49 source files |
| Deps | `deptry .` | 0 | no dependency issues (48 files) |
| Build | `uv build --wheel --all-packages` | 0 | both wheels built |

**Coverage**: not configured in this workspace — skipped, not a failure.

### Spec Compliance Matrix

Statuses: COMPLIANT (covering test passed at runtime), DEMONSTRATED (non-Test
verification method, artefact of record exists AND was executed), UNDEMONSTRATED
(artefact exists, never executed), PARTIAL, DEFERRED.

#### `run-recording` (RQ-1, RQ-31, RQ-3, RQ-38) — 10 scenarios

| Req | Scenario | Code | Evidence | Result |
|---|---|---|---|---|
| RQ-1 | First invocation, empty db | `recorder.py::Recorder`, `routes/runs.py` | `test_run_report.py::test_completed_session_writes_one_row_with_ordered_timestamps` | COMPLIANT |
| RQ-1 | Second gets distinct id | `Recorder.__init__` `uuid4().hex` | `test_run_report.py::test_second_invocation_gets_a_distinct_identifier` | COMPLIANT |
| RQ-1 | Zero-test collection | `plugin.py::pytest_configure` | `test_run_report.py::test_zero_test_collection_still_writes_one_row` | COMPLIANT |
| RQ-1 | Failed collection | same | `test_run_report.py::test_failed_collection_still_writes_one_row` | COMPLIANT (weak — see S5) |
| RQ-31 | Completed session, end > start | `recorder.py::pytest_sessionfinish` | `test_completed_session_writes_one_row_with_ordered_timestamps` | PARTIAL (W5) |
| RQ-31 | Interrupted → null end | `_NULL_FINISH_EXIT_STATUSES` | `test_run_report.py::test_sigint_leaves_start_time_and_null_end_time` (real SIGINT to a real subprocess) | COMPLIANT |
| RQ-3 | Server killed mid-write (SIGKILL) | — | none | DEFERRED (W2) |
| RQ-3 | Report truncated in transit | `service/errors.py` ClientDisconnect → 400 `incomplete_body` | `test_rejection.py::test_truncated_body_raw_socket` (raw socket, `count_executions()==0`) | COMPLIANT |
| RQ-3 | Normal report fully present | ingestion path | none direct | DEFERRED (W2) |
| RQ-38.1 | Two concurrent sessions | `sqlite_store.py` process lock + `BEGIN IMMEDIATE` | `test_concurrency.py::test_two_concurrent_sessions_both_leave_a_run_entry` | PARTIAL (W1) |

#### `opt-in-activation` (RQ-2) — 3 scenarios

| Req | Scenario | Code | Evidence | Result |
|---|---|---|---|---|
| RQ-2 | No connection without the option | `plugin.py::_activation_requested` | `test_opt_in.py::test_no_connection_is_attempted_with_no_recording_option` (patches `socket.create_connection` to raise) | COMPLIANT |
| RQ-2 | Identical trees with/without plugin | inert-by-default module split | `test_opt_in.py::test_project_tree_is_byte_identical_with_plugin_absent` (differential vs `-p no:vantage`, `PYTHONDONTWRITEBYTECODE=1`) | COMPLIANT |
| RQ-2 | No server needed, no warning either | same | exit status half covered; **no-warning half unasserted** | PARTIAL (W4) |

#### `recording-fault-tolerance` (RQ-21, RQ-37) — 9 scenarios

| Req | Scenario | Code | Evidence | Result |
|---|---|---|---|---|
| RQ-21 | Passing suite survives internal error | `boundary.py::fault_isolated` | `test_failure_paths.py::test_reporting_error_preserves_passing_exit_status_and_warns_once` | COMPLIANT |
| RQ-21 | Failing suite still reports failure | same | `test_reporting_error_preserves_failing_exit_status_and_warns_once` (ret==1) | COMPLIANT |
| RQ-21 | Accepts then closes | `transport.py` raises → boundary warns | `test_server_accepts_then_closes_without_responding` | COMPLIANT |
| RQ-21 | Accepts and never answers | `urlopen(timeout=)` | `test_server_accepts_and_never_answers_finishes_within_timeout_plus_five_seconds` (measures elapsed < 6.0s) | COMPLIANT |
| RQ-21 | Every hook is fault-isolated | `@fault_isolated` on both `Recorder` hooks | `test_fault_isolated_catches_exception_and_latches_after_first_failure` — unit test on a stand-in class, not on `Recorder` | PARTIAL (W3) |
| RQ-37 | Nothing listening | `plugin.py::_preflight_reachable` | `test_closed_port_warns_naming_the_address_and_runs_unrecorded` | COMPLIANT |
| RQ-37 | Host does not resolve | `except OSError` covers `gaierror` | `test_unresolvable_host_warns_naming_the_address_and_runs_unrecorded` | COMPLIANT |
| RQ-37 | Server drops out mid-session | boundary catches transport raise | `test_server_dropped_mid_session_preserves_exit_status_and_warns_once` | COMPLIANT |
| RQ-37 | One warning, not one per test | preflight returns before registration | `test_two_hundred_tests_produce_exactly_one_warning_naming_the_address` | COMPLIANT |

#### `architecture-boundaries` (RQ-24, RQ-26, RQ-30) — 7 scenarios

| Req | Scenario | Code | Evidence | Result |
|---|---|---|---|---|
| RQ-24 | Clean install adds one distribution | `pytest-vantage/pyproject.toml` `dependencies = ["pytest>=8.0"]` | CI job `clean-environment-install` (never run) **+ manual reproduction recorded in Engram obs #34** — wheel built, fresh 3.12 venv, exactly `pytest-vantage==0.1.0` added | DEMONSTRATED |
| RQ-24 | Every import resolves to stdlib/pytest | 6 plugin modules | `test_plugin_imports.py::test_every_plugin_import_resolves_to_stdlib_or_pytest` (mutation-proven, obs #33: `import httpx2` caught at exact line) | COMPLIANT |
| RQ-24 | Declared dependencies name only pytest | `pyproject.toml:17` | same CI job + obs #34 reproduction | DEMONSTRATED |
| RQ-26 | Every core import is stdlib | `vantage/core/**` | `test_architecture.py::test_every_core_import_resolves_to_the_standard_library` | COMPLIANT |
| RQ-26 | Analysis is not vacuous | `importwalk` | `test_architecture.py::test_the_walk_is_not_vacuous` + 2 sibling-import guards | COMPLIANT |
| RQ-30 | Core suite passes against in-memory adapter | `storage/memory.py` vs `core/ports/storage.py` Protocol | `vantage_port_contract.py::ExecutionStoreContract` × {`test_memory_store.py`, `test_sqlite_store.py`} — 8 tests | COMPLIANT |
| RQ-30 | Core has no storage-implementation import | `core/` | `test_architecture.py::test_every_core_import_resolves_to_the_standard_library` (marked RQ-26 + RQ-30) | COMPLIANT |

#### `recording-schema` (RQ-29 — Inspection) — 2 scenarios

| Req | Scenario | Artefact of record | Supporting | Result |
|---|---|---|---|---|
| RQ-29 | Fresh db matches the column manifest | `docs/schema-manifest.md` §"Comparison recorded (RQ-29 verification of record)" at L314–315, carrying the required `<!-- RQ-29 -->` marker | `test_schema_manifest.py` rot-detector — 9 tests, bidirectional diff with 6 deliberate self-checks proving the diff is not vacuous | DEMONSTRATED |
| RQ-29 | Reopening issues no schema-altering statement | `storage/schema.sql` all `IF NOT EXISTS`; `connection.py` | `test_connection.py::test_reopening_an_existing_database_issues_no_ddl` + `test_every_ddl_statement_in_schema_sql_declares_if_not_exists` + a self-check that the check catches a bare `CREATE TABLE` | DEMONSTRATED |

Independently re-verified during this phase: executing `schema.sql` against
`sqlite3.connect(":memory:")` yields **13** `idx_*` indexes (12 `CREATE INDEX` +
`idx_test_case_node_id`, which is `CREATE UNIQUE INDEX` and therefore invisible to a
substring grep) and **10** documented tables. The manifest, design.md and the
corrected ADR-5 all now agree.

#### `runtime-support` (RQ-27, RQ-28 — Demonstration) — 4 scenarios

| Req | Scenario | Artefact of record | Executed? | Result |
|---|---|---|---|---|
| RQ-27 | CI matrix green, 8 combinations | `.github/workflows/ci.yml` job `test`, 3.10–3.13 × {with,without} xdist, each block carrying `# RQ-27` | **NO** | UNDEMONSTRATED (C2) |
| RQ-27 | 3.9 install refused, not broken at import | `ci.yml` job `python-3-9-install-refused`; `requires-python = ">=3.10,<3.14"` | **NO** | UNDEMONSTRATED (C2) |
| RQ-28 | Recording succeeds with networking disabled | `ci.yml` job `networking-disabled` (iptables OUTPUT REJECT except lo) | **NO** | UNDEMONSTRATED (C2, W6) |
| RQ-28 | No outbound connection beyond local server | same job | **NO** | UNDEMONSTRATED (C2) |

#### `session-ingestion` (RQ-41, RQ-42) — 7 scenarios

| Req | Scenario | Code | Evidence | Result |
|---|---|---|---|---|
| RQ-41 | Well-formed report stored + acknowledged | `service/routes/runs.py`, `schemas.py` | `test_ingestion.py::test_well_formed_report_is_stored_and_acknowledged` | COMPLIANT |
| RQ-41 | Retried report is idempotent | `record_execution` returns `created: bool` | `test_ingestion.py::test_retried_report_is_idempotent` (201 then 200, one row) | COMPLIANT |
| RQ-41 | Unversioned path refused | `app.py` mounts `/api/v1` only | `test_ingestion.py::test_unversioned_path_is_refused[/runs, /api/runs]` | COMPLIANT |
| RQ-42 | Missing required field | `service/errors.py` allow-list shaping | `test_rejection.py::test_missing_field_is_422_naming_the_field` | COMPLIANT |
| RQ-42 | Invalid JSON | media-type + parse guard | `test_rejection.py::test_non_json_body_is_400` (+ `_is_415` ×2, `_is_413`) | COMPLIANT |
| RQ-42 | Body truncated midway | `ClientDisconnect` → 400 | `test_rejection.py::test_truncated_body_raw_socket` | COMPLIANT |
| RQ-42 | Names the cause, safely | allow-list, never pydantic's dicts | `test_422_response_never_echoes_input_or_pydantic_types`, `test_forbidden_extra_field_name_is_not_echoed`, `test_forbidden_extra_field_cannot_amplify_the_response` | COMPLIANT |

#### `storage-permissions` (RQ-40) — 3 scenarios

| Req | Scenario | Code | Evidence | Result |
|---|---|---|---|---|
| RQ-40 | Database file mode 0600 | `connection.py` `O_CREAT\|O_EXCL` at 0600 **before** `sqlite3.connect` | `test_permissions.py::test_database_file_created_0600_before_connect` (umask 022 fixture) | COMPLIANT |
| RQ-40 | Artefact store dir 0700 | `connection.py` mkdir mode | `test_permissions.py::test_artifact_store_directory_created_0700` | COMPLIANT |
| RQ-40 | Existing 0644 db records + warns | warn path | `test_permissions.py::test_existing_permissive_database_still_records_and_warns` (+ `test_wal_and_shm_sidecars_created_0600`) | COMPLIANT |

**Compliance summary**: 35/45 scenarios have execution evidence; 4 undemonstrated
(RQ-27, RQ-28), 2 deferred (RQ-3.1, RQ-3.3), 4 partial.

### Traceability Invariant (CLAUDE.md)

`grep -r "RQ-nn"` reaches the proving artefact for **all 16** requirements in scope.

- 15 of 16 carry `@pytest.mark.req("RQ-nn")` on at least one passing test.
- RQ-28 (Demonstration) carries its id in three comments inside `.github/workflows/ci.yml` — as CLAUDE.md prescribes for a non-test verification. Note `rg` skips dotted directories by default: plain `rg RQ-28` finds it only because the path is given; a bare `rg RQ-28` from the root does **not**. `grep -r` (the literal invariant) does.
- RQ-29 (Inspection) deliberately carries no marker; its id is in `docs/schema-manifest.md` L314 (`<!-- RQ-29 -->`), `schema.sql` L1/L7, `connection.py` L2/L24, and both test module docstrings, each explaining the omission. This is correct per "Verification methods are not all tests".
- RQ-3 carries one marker (`test_truncated_body_raw_socket`); criteria 1 and 3 have none, consistent with their deferral.

**Verdict on the invariant: holds.**

### Correctness (Static Evidence)

| Requirement | Status | Notes |
|---|---|---|
| RQ-2 trap (flag, not config) | Implemented correctly | `_activation_requested` reads `config.getoption("vantage")` **only**. `vantage_server`/`vantage_timeout` ini values and `VANTAGE_SERVER` env configure WHERE, never whether. Verified by reading `plugin.py:101-111`. |
| RQ-24 trap (third-party only) | Implemented correctly | `vantage` distributions depending on each other is permitted and happens; the walk and the CI diff both target third-party only. |
| RQ-12 trap (xdist double delivery) | N/A this milestone | RQ-12 is not in this change's spec set. The dedup mechanism is present anyway: `hasattr(config, "workerinput")` is the **first statement** of `pytest_configure`, and `schema.sql:103` records the `UNIQUE(..., attempt)` backstop for M2. Mutation-proven load-bearing (obs #33). |
| RQ-29 trap (complete schema, no migrations) | Implemented correctly | 10 tables / 13 indexes at first use including M2/M3 columns; no migration framework. |
| "No domain class starts with Test" | Held | `Execution`/`Identity`; the only `Test*` classes are `TestInMemoryExecutionStore`/`TestSqliteExecutionStore`, which are pytest collection targets by design. |

### Coherence (Design)

| Design decision | Followed? | Notes |
|---|---|---|
| D1 fixed-width ISO-8601 | Yes | `strftime("%f")`, mutation-proven load-bearing (obs #33) |
| D2 xdist guard first statement | Yes | `plugin.py:142` |
| D6 preflight + `min(2.0, timeout)` | Yes | `_MAX_CONNECT_TIMEOUT` |
| D7 one POST per session | Yes | `pytest_sessionfinish` only |
| D9 owner-only DDL | Yes | `O_CREAT\|O_EXCL` before connect |
| D11 pure config resolution | Yes | `resolution.py` creates nothing |
| Test strategy: RQ-38.1 "two threads POSTing into one uvicorn instance" | **No** | delivered as two threads calling `store.record_execution` directly — W1 |
| Test strategy: RQ-21 "every hook patched to raise" | **No** | delivered as a decorator unit test on a stand-in class — W3 |

### TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD evidence reported | Yes | RED/GREEN commit pairs visible across the whole chain (e.g. `0b61fe5` RED → `b7b0a43` GREEN; `eb77c57` RED → `2fa512c` GREEN) |
| All tasks have tests | Yes | except the two documentation-only phases (7/8), correctly run in Standard mode |
| RED confirmed | Yes | every named test file exists |
| GREEN confirmed | Yes | 107/107 pass at the tip, serial and under `-n 4` |
| Triangulation | Adequate | `Identity` 6 cases; rejection 9 cases; rot-detector 6 self-checks |
| Safety net | Yes | full suite re-run recorded at each slice |

### Test Layer Distribution

| Layer | Tests | Notes |
|---|---|---|
| Unit | ~48 | domain, resolution, boundary, importwalk, manifest parsing |
| Integration (in-process) | ~30 | `TestClient`, `pytester.runpytest`, threads-on-sqlite |
| End-to-end (real subprocess + real HTTP) | ~15 | `runpytest_subprocess` against a live `vantage_server` on an ephemeral port; raw sockets; a real SIGINT |
| **Total** | **107** | |

The end-to-end layer is genuinely real — real subprocesses, real sockets, real signals,
no mock of either side of the HTTP boundary. This is above the norm.

### Assertion Quality

No tautologies, no ghost loops, no assertion-without-production-call, no
mock-heavy tests. Empty-collection assertions (`count_executions() == 0`) all have
companion non-empty tests in the same file. Two weak spots only, both listed as
warnings below (W4, W5). **Assertion quality: 0 CRITICAL, 2 WARNING.**

### Issues Found

#### CRITICAL

**C1 — The change's own record of what it did is false at the tip, and the tip
commit is authorised by no task.**

`8b0d666` ("docs: close the last two pre-restructure inconsistencies") edits
`docs/adr/0003-…md` (+17/-6) and `docs/schema-manifest.md` (+47/-22). It is the
current tip. It is recorded in **no** SDD artifact, and it directly contradicts two
records that are still in the tree:

- `tasks.md` §"Flagged, Not Actioned" L862-872 states ADR-0003 is "not actioned here
  … Follow-up" and schema-manifest's forward-reference is "reported as a follow-up …
  not fixed here". Both **were** fixed by `8b0d666`.
- Engram apply-progress (obs #27) lists exactly three PR14 commits (`77a0fee`,
  `f0d1e89`, `670b613`) and states both items were "Reported, not fixed — outside
  8.2's exact two-file scope. Follow-up."

Task 8.2's scope is exactly two files (`docs/adr/0005-…`, `docs/adr/0006-…`).
`8b0d666` touches two different files. The edits themselves are correct and
well-argued — schema-manifest's note had indeed become an instruction to distrust
two files that are now right — but archiving now would freeze a record that says
work was deferred which was in fact done. **This is apply-fixable and is the single
thing I would fix before archive.**

**C2 — RQ-27 and RQ-28 have zero execution evidence, and the artefact carrying
them is not even on the remote.**

Four scenarios across two **MUST** requirements rest entirely on
`.github/workflows/ci.yml`. That workflow has never executed:

- `origin` carries branches only up to `milestone-1-write-one-row-pr10`. Branches
  `pr11`–`pr14` are **unpushed**. The new workflow landed in `543ae2e` (PR13).
- Every branch that *is* on origin — including `origin/main` — carries the **old**
  single-job workflow (`push: branches: [main]`, `mypy src`, `uv build --wheel`),
  which is itself broken: `src/` no longer exists and the workspace root has no
  `[build-system]`.

So the RQ-27 matrix, the 3.9-refused job, the `networking-disabled` job and the
`clean-environment-install` job have collectively checked zero commits of this
65-commit chain. This is broader than the known open item, which named only
`networking-disabled`.

Partial mitigation, and it is real: RQ-24's clean-install criterion was reproduced
by hand in a fresh 3.12 venv (Engram obs #34) — exactly `pytest-vantage==0.1.0`
added, nothing removed. RQ-24 is therefore genuinely demonstrated; RQ-27 and RQ-28
are not.

#### WARNING

**W1 — RQ-38's only scenario is tested one layer below where the spec puts it.**
The scenario is "two pytest sessions … against one server". design.md L878 planned
"two threads POSTing distinct ids into one uvicorn instance". What landed
(`test_concurrency.py`) is two threads calling `SqliteExecutionStore.record_execution`
directly — no server, no HTTP, no pytest session. It proves the storage write lock,
which is valuable, but the ingestion path under concurrency (FastAPI/uvicorn
handling two simultaneous POSTs into one store) is untested. A regression that
serialised or dropped a concurrent request at the service layer would not be caught.

**W2 — RQ-3's deferral is recorded in design.md but not in the spec.**
design.md L894 states plainly: "RQ-3 | criterion 2 | criteria 1 and 3 count 500
results → Milestone 2". The spec file says only "(This milestone writes no result
rows, so this requirement is exercised through the run entry …)" — which reads as
*all three still apply via the run entry*. Compare RQ-38 in the **same spec file**,
which does carry an explicit "Only criterion 1 is in scope … carried to Milestone 2".
The inconsistency means a reader of `run-recording/spec.md` alone would score RQ-3
as 1/3 failing rather than 1/1 in-scope passing. design.md L970 already records the
follow-up ("should be annotated on their Notion rows as carried to Milestone 2") and
it is still unchecked.

**W3 — Nothing asserts that every `Recorder` hook is actually `@fault_isolated`.**
RQ-21's fifth scenario is literally "every hook it implements". The covering test
(`test_fault_isolated_catches_exception_and_latches_after_first_failure`) exercises
the decorator against a purpose-built `_Instrumented` stand-in, not against
`Recorder`. The substitution is honestly argued in a 9-line comment (patching an
already-decorated method would bypass the code under test) and the reasoning is
sound. But the residual gap is real: adding a third `pytest_*` hook to `Recorder`
without the decorator would break RQ-21 and **no test would fail**. A cheap closure:
introspect `Recorder` for `pytest_*` attributes and assert each has `__wrapped__`.

**W4 — RQ-2's third scenario is half-asserted.** "THEN it exits with the status it
would have had … **and emits no warning**". `test_no_connection_is_attempted_with_no_recording_option`
ends at `result.assert_outcomes(passed=1)`. Confirmed against the installed
pytest 9.1.1: `assert_outcomes`' `warnings` parameter defaults to `None`, meaning
*not checked*. Passing `warnings=0` would close this in one token.

**W5 — RQ-31's first scenario does not meet its own GIVEN.** The spec says "GIVEN a
session that runs for **at least two seconds**" — chosen so `finished_at > started_at`
is unambiguous. The test runs `def test_it(): assert True`, i.e. milliseconds, so the
assertion holds only at microsecond resolution. This compounds the already-recorded
known limitation (obs #33 mutation 5: substituting `self._started_at + timedelta(seconds=1)`
leaves all 9 tests passing). Making the fixture suite sleep ~2s would satisfy the
scenario as written and narrow the constant-offset hole at the cost of 2s of suite time.

**W6 — the `networking-disabled` job is likely to fail the first time it runs, for a
reason unrelated to Vantage.**
```
sudo iptables -A OUTPUT -o lo -j ACCEPT
sudo iptables -A OUTPUT -j REJECT
```
On a GitHub-hosted runner the `Runner.Worker` process needs outbound HTTPS to the
Actions service to stream logs and report step completion. A blanket `OUTPUT … REJECT`
with no `-m conntrack --ctstate ESTABLISHED` exemption severs that too, not just
Vantage's traffic. The expected symptom is the job hanging or the runner losing
communication — i.e. a red job that says nothing about RQ-28. This was **not** in the
known-limitations record (obs #34 says only that it could not be reproduced in the
sandbox because `unshare --net` and root are unavailable). The usual fix is to allow
established connections and the runner's egress rather than rejecting everything.

#### SUGGESTION

- **S1 — `isoformat_utc` converts nothing.** `recorder.py:44` hardcodes the literal
  `+00:00` via `strftime`. `isoformat_utc(datetime.now())` (naive local) would emit
  local wall-clock time labelled UTC, silently. Every current caller passes
  `datetime.now(timezone.utc)`, so nothing is wrong today; the function's name is a
  promise its body does not keep. One `moment.astimezone(timezone.utc)` closes it.
- **S2 — a collection error is recorded as `interrupted: true`.** Reproduced: a module
  that fails to import exits **2** (`ExitCode.INTERRUPTED`), so `recorder.py` writes
  `interrupted=True, finished_at=None, interrupt_reason=None` — indistinguishable from
  a real Ctrl-C. design.md L253 maps `interrupted: true` to Ctrl-C only and has no row
  for a collection error. No requirement in this milestone constrains the field's
  meaning, so nothing is violated, and `test_failed_collection_still_writes_one_row`
  asserts only the row count, so nothing pins it either way. Worth deciding before M2
  reads the column.
- **S3 — proposal.md's Success criteria are 15 unchecked boxes** (L241-255) even though
  every one is now satisfied or explicitly deferred. Additionally L255 literally reads
  `grep -r "RQ-01"` — with a leading zero, which matches nothing anywhere in the tree
  (ids are `RQ-1`). A stated success criterion that cannot succeed as written.
- **S4 — `openspec/config.yaml`'s `State:` block is stale.** It still says "the tree is
  being reset … `src/vantage` and `tests/` … Do not treat anything under `src/` as a
  pattern to follow." The reset completed; `src/` is gone. `CLAUDE.md`'s "State of the
  tree" section carries the same stale text. Both misdirect the next agent. (The three
  staleness items design.md L980 flagged for this file *were* fixed — it now says
  RQ-1..RQ-42 and two distributions.)
- **S5 — `test_failed_collection_still_writes_one_row` never asserts collection failed.**
  It asserts only `len(executions) == 1`. If the fixture module ever became importable,
  the test would keep passing while proving something else. One `assert result.ret == 2`
  pins it.
- **S6 — design.md's Open Questions are all unchecked, and two are stale.** The
  "six capability specs predate the ADR-9 replan" item (L976) is resolved — there are
  now eight, including `session-ingestion` with RQ-41/RQ-42. The ADR-5/0006 item (L983)
  was resolved by PR14.

### Known open items — confirmation each is genuinely recorded

| Item | Recorded? | Where |
|---|---|---|
| `networking-disabled` (RQ-28) never executed | **Yes, and understated** | tasks.md PR13 landed summary; Engram obs #34. Both frame it as the *one* unexecuted job — in fact the whole workflow has never run (C2), and the job has a probable defect (W6). |
| `finished_at` ordering passes a faked constant offset | **Yes, accurately** | Engram obs #33 mutation 5, with the deliberate not-fixed judgement stated. Compounded by W5. |
| XDG default is a Linux convention; Win/macOS fall back to `~/.local/share` | **Yes** | `docs/adr/0010-store-the-server-database-in-the-user-data-directory.md`; `test_resolution.py::test_default_database_falls_back_to_home_when_xdg_data_home_unset` pins the fallback |
| SIGKILLed session leaves no row; no requirement covers it | **Yes** | design.md L253 (threat table row), L258, and Open Question L962-966 with the escalation path (raise a Notion requirement amendment, do not smuggle in the start-notification pair D7 rejected) |

All four are genuinely recorded rather than silently dropped. The first is recorded
but its scope is understated.

### Deliberately deferred by Phase 1 — and where it is recorded

| Deferred | Recorded at |
|---|---|
| RQ-3 criteria 1 & 3 (500-result counts) → M2 | design.md L894, L970 (**not** in the spec — W2) |
| RQ-38 criteria 2 & 3 (result counts) → M2 | design.md L895 **and** `run-recording/spec.md` L88-89 |
| RQ-28 "the interface is opened" → M5 | design.md L896 **and** `runtime-support/spec.md` L35-38 |
| RQ-4, RQ-13, RQ-22, RQ-23, RQ-39 writer behaviours → M2/M3 | design.md L898-903; schema columns exist and are `NULL`-able with no default |
| No migration framework in Phase 1 | ADR-0005; `meta.schema_version` is the stamp that makes the first migration possible later |
| Plugin-version support commitment | design.md Open Question L967-969 — unanswered |
| Replay with a *different* body | design.md Open Question L987-989 — first-write-wins, silently |

### Scope Discipline

| Question | Answer |
|---|---|
| Anything land that no task asked for? | **Yes — `8b0d666`** (C1). Also a disclosed, justified in-scope extension in PR14: two extra stale package-name references corrected in ADR-0006 beyond the one line the brief listed — that one *is* recorded in apply-progress and tasks.md. |
| Any spec scenario unimplemented while its task is marked done? | **No task claims RQ-3.1/3.3 or the CI execution.** Task 7.2 delivered the workflow, which is what it asked for. The gap is between "the workflow exists" and "the workflow has demonstrated anything" (C2) — not a mis-ticked box. |
| Unchecked tasks? | Zero. |

### Verdict

**FAIL — not archive-ready as it stands. Ready to archive with recorded caveats the moment C1 is corrected.**

This is a `fail` on the admission contract's terms (a passing verdict may not carry
critical findings), not a judgement that the implementation is bad. One blocker, C1,
is a documentation correction measured in minutes. C2 is an evidence gap that no
apply slice can close from this machine.

The implementation is unusually solid: 107 tests green serially and under `-n 4`, all
five gates clean, real subprocesses and real sockets and a real SIGINT rather than
mocks, honest disclosure of substitutions in comments rather than silence, and the
requirement traps in CLAUDE.md all correctly handled. 35 of 45 scenarios have genuine
runtime evidence and the traceability invariant holds for all 16 requirements.

Two things stand between it and a clean archive:

1. **C1 is the one I would fix first.** It is small, it is documentation-only, and it
   is the only place where the change *says something untrue about itself*. Correct
   tasks.md §"Flagged, Not Actioned" and re-save apply-progress so `8b0d666` and its
   two corrections are on the record.
2. **C2 is not fixable by another apply slice** and should be carried as an accepted,
   named caveat: RQ-27 and RQ-28 are asserted, not demonstrated, until the chain is
   pushed and CI runs — at which point W6 says to expect `networking-disabled` to fail
   for a runner-infrastructure reason, not a Vantage one.
