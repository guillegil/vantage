# Tasks: Milestone 1 — Write one row

Rewritten 2026-08-15 against ADR-9 (plugin reports over HTTP, server owns every
write). Supersedes the previous `tasks.md`, which planned the plugin writing
SQLite directly. Grouped by the proposal's eight capabilities; ordered by the
design's slices A1→A2→B→C→D1→D2→E, so the server's ingestion endpoint (B)
exists before the plugin has anything to report to (D2). Strict TDD: every
behaviour task is a RED (failing test, written first) / GREEN (implementation)
pair. `uv run --extra dev pytest` is the test command throughout.

> **Size note.** The skill's generic 530-word/1-2-line-per-task budget is
> waived here: 45 scenarios, 16 requirements, two components and an explicit
> per-slice line forecast cannot fit it without dropping traceability or the
> threat-matrix RED tests the user required. Tasks stay 1-2 lines each; the
> document as a whole is longer.

> **Revision 2 (2026-08-15).** Three slices (then PR2, PR6, PR9) forecast over
> the 400-line budget. **That forecast was itself new risk**, distinct from
> the chain-strategy question the session preflight already resolved
> (`chained` / `feature-branch-chain`) — an over-budget slice is exactly what
> `ask-on-risk` exists to catch, and the earlier report was wrong to treat the
> preflight's resolution as already covering it. The question was put to the
> user; they chose to cut all three. This revision applies those cuts: the
> chain grows from 11 PRs to **14**. `openspec/config.yaml`'s stale context
> block, previously flagged below, has since been corrected upstream and is
> no longer flagged.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~3,590 authored lines across 14 PRs (range per PR below) |
| 400-line budget risk | Medium (13 of 14 slices now fit the budget; PR2 alone still exceeds it, by design) |
| Chained PRs recommended | Yes |
| Suggested split | PR1 → PR2 → … → PR14, feature-branch-chain |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Medium

**On `Decision needed before apply: No`.** This is not because the ask-on-risk
question didn't apply — it did, and the previous revision was wrong to say
otherwise. Three over-budget slices were new risk under `ask-on-risk`; that
risk was surfaced, the question was asked, and the user answered: apply all
three cuts. It reads `No` now because the decision has been **taken**, not
because none was needed.

**PR2 stays over budget on purpose.** RQ-29's verification method is
Inspection, and the inspection *is* the comparison between `schema.sql` and
`docs/schema-manifest.md` — splitting the schema from the manifest it is
checked against would leave one PR with nothing to verify it and a later PR
describing something already merged unreviewed, making RQ-29 unverifiable at
the moment its own PR lands. The mechanised rot-detector is separable because
it supports the inspection rather than being the inspection (already tagged
with a plain comment rather than `@pytest.mark.req`, for the same reason). Its
own PR (PR3) is not what is pushing PR2 over budget; PR2's ~450 lines are
`schema.sql` (~170) plus the manifest itself (~280), and the manifest's size
is ADR-5's accepted cost. Say so plainly rather than trimming it.

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|-----------------------|-----------------|-------------------|
| A1 | `Execution`/`Identity`, `ExecutionStore` Protocol, architecture test, in-memory adapter | PR1 (~380 ln, Medium) | `uv run --extra dev pytest packages/vantage/tests/test_architecture.py packages/vantage/tests/test_execution.py packages/vantage/tests/test_memory_store.py` | N/A — pure unit/static-analysis, no server or subprocess needed | delete `core/domain/`, `core/ports/`, `storage/memory.py`; nothing else references them yet |
| A2a | `schema.sql` + `docs/schema-manifest.md` — RQ-29's verification of record, together | PR2 (~450 ln, **High, accepted** — ADR-5's manifest cost, not to be trimmed) | N/A — Inspection deliverable; verified by the comparison recorded in the manifest itself, not by a test (the rot-detector that mechanises the same comparison ships in PR3) | N/A — no runtime behaviour | delete `schema.sql` and the manifest; A1 stands alone unaffected |
| A2b | Mechanised rot-detector supporting RQ-29 (not the proof itself) | PR3 (~110 ln, Low) | `uv run --extra dev pytest packages/vantage/tests/test_schema_manifest.py` | N/A — parses the manifest and applies `schema.sql` to an in-memory `sqlite3` connection directly, no live server, no `open_database` | delete the rot-detector test; PR2's manifest+schema stand as the verification of record on their own |
| A2c | `connection.py` — DDL application in one transaction, D9 permissions, 0600 creation order | PR4 (~260 ln, Medium) | `uv run --extra dev pytest packages/vantage/tests/test_permissions.py packages/vantage/tests/test_connection.py` | N/A — umask-fixture and reopen-assertion unit tests, POSIX-only | delete `connection.py`; PR2's `schema.sql` stands unapplied, PR3's rot-detector still runs against raw `executescript` |
| A2d | `SqliteExecutionStore`, write lock, concurrency (RQ-30.1 completed, RQ-38.1) | PR5 (~210 ln, Medium) | `uv run --extra dev pytest packages/vantage/tests/test_sqlite_store.py packages/vantage/tests/test_concurrency.py` | N/A — in-process threads against a tmp-path sqlite file | delete `sqlite_store.py`; in-memory adapter (PR1) still satisfies the contract |
| B1 | Pydantic schemas, app factory, `POST /api/v1/runs` (RQ-41) | PR6 (~260 ln, Medium) | `uv run --extra dev pytest packages/vantage/tests/test_ingestion.py` | `TestClient` over an in-memory store — in-process, no real socket | delete `service/schemas.py`, `service/routes/`, `service/app.py`; storage/core stand alone |
| B2a | 422-handler replacement + basic rejections (RQ-42) | PR7 (~250 ln, Medium) | `uv run --extra dev pytest packages/vantage/tests/test_rejection.py -k "not truncated"` | `TestClient`, in-process | delete `service/errors.py`; PR6's happy path (201/200) still serves |
| B2b | Raw-socket truncation test, `docs/api/v1-ingestion.md`, ADR-0011 (RQ-42, RQ-3.2) | PR8 (~200 ln, Low/Medium) | `uv run --extra dev pytest packages/vantage/tests/test_rejection.py -k truncated` | Raw TCP socket against a `TestClient`-backed ASGI app, in-process | delete the truncation test, the doc, the ADR; PR7's rejection shape stands on its own |
| C | Server config resolution, `vantage serve` CLI, ADR-0010 | PR9 (~260 ln, Medium) | `uv run --extra dev pytest packages/vantage/tests/test_resolution.py` | `uv run vantage serve` manually against an ephemeral port — N/A for CI (covered by PR13's matrix) | delete `cli.py`, `config/resolution.py`; app factory still takes an injected store |
| D1 | Inert plugin: options, xdist guard, scheme allow-list, RQ-2 differential | PR10 (~340 ln, Medium) | `uv run --extra dev pytest packages/pytest-vantage/tests/test_opt_in.py packages/pytest-vantage/tests/test_xdist_guard.py packages/pytest-vantage/tests/test_plugin_imports.py` | `pytester.runpytest_subprocess`, differential (with/without `-p no:vantage`) | revert `plugin.py` to its current two-hook inert form; delete `config.py` |
| D2a | Recorder, transport, RQ-1/RQ-31 happy path (incl. xdist end-to-end) | PR11 (~260 ln, Medium) | `uv run --extra dev pytest packages/pytest-vantage/tests/test_run_report.py` | `pytester.runpytest_subprocess` against a real `uvicorn` instance on an ephemeral port, incl. `-n 4` | delete `recorder.py`, `transport.py`; PR10's inert plugin still passes RQ-2 |
| D2b | RQ-37/RQ-21 boundary and timeout paths | PR12 (~260 ln, Medium) | `uv run --extra dev pytest packages/pytest-vantage/tests/test_failure_paths.py` | `pytester.runpytest_subprocess` against stub servers (closed port, unresolvable host, accept-then-close, accept-never-answer) | delete `boundary.py` and the preflight in `plugin.py`; PR11's happy path still works when the server actually answers |
| E | Pre-commit, CI matrix, offline job, clean-env install check, weekly audit | PR13 (~260 ln, Medium) | `uv run --extra dev pytest` (full suite, invoked by CI) | The CI matrix itself is the runtime harness — 3.10–3.13 × xdist, offline, clean-env install | delete `.pre-commit-config.yaml`, `.github/workflows/{ci,audit}.yml`; no code depends on them |
| F | `docs/architecture.md` update; correct ADR-0005/0006 paths and index count | PR14 (~90 ln, Low) | N/A — documentation only | N/A — no runtime behaviour changes | revert the three doc files independently of any PR above |

---

## Phase 1: A1 — Domain & Port (`vantage`, server) — PR1

- [x] 1.1 RED — `packages/vantage/tests/importwalk.py` (shared `ast` walker) + `packages/vantage/tests/test_architecture.py`: assert every `vantage.core` import resolves to stdlib (RQ-26.1) and the non-vacuity guard — ≥3 modules examined, `domain/execution.py` and `ports/storage.py` among them (RQ-26.2). Fails today (files don't exist). `@pytest.mark.req("RQ-26")`.
- [x] 1.2 GREEN — Create `core/domain/execution.py` (`Identity`, `Execution` frozen/slotted dataclasses per the design's Interfaces section) and `core/ports/storage.py` (`ExecutionStore` Protocol). 1.1 passes.
- [x] 1.3 RED — `test_execution.py`: `Identity` rejects anything but 32 lowercase hex chars; `Execution.finished_at` is nullable and the type is frozen.
- [x] 1.4 GREEN — `__post_init__` validation on `Identity` (satisfied by 1.2's dataclass; this task closes any gap 1.3 finds).
- [x] 1.5 RED — `vantage_port_contract.py` (`ExecutionStoreContract`, shared fixture module, never `test_*`, never shipped) + `test_memory_store.py`: core suite passes against an in-memory adapter (RQ-30.1, in-memory half); core package imports no storage implementation (RQ-30.2 — also covered by 1.1's D10 sibling-subpackage rejection). `@pytest.mark.req("RQ-30")`.
- [x] 1.6 GREEN — `storage/memory.py::InMemoryExecutionStore`. 1.5 passes (RQ-30.1 fully proven once PR5 adds the sqlite half).

## Phase 2: A2 — Schema & SQLite Adapter (`vantage`, server) — PR2–PR5

### A2a — Schema & Manifest (PR2)

- [ ] 2.1 Inspection (RQ-29, not Test) — Write `packages/vantage/src/vantage/storage/schema.sql` (ten tables, thirteen indexes per the design's manifest) **and** `docs/schema-manifest.md` together, as one deliverable; record the fresh-database-vs-manifest comparison in the manifest itself. Comment `<!-- RQ-29 -->` on the comparison section — this is the verification of record, not a test, and it is not split from the schema it verifies.

### A2b — Rot-detector (PR3)

- [ ] 2.2 RED — `test_schema_manifest.py`: parses the manifest's column table, applies `schema.sql` via a plain `sqlite3.connect(":memory:").executescript(...)` (not `open_database` — no dependency on connection.py/PR4), and compares against `PRAGMA table_info` per table. Fails: the comparison helper does not exist yet. Comment `# Rot-detector supporting RQ-29 (Inspection at docs/schema-manifest.md is the verification of record)`, no `pytest.mark.req` (Inspection is not a Test-type verification).
- [ ] 2.3 GREEN — implement the manifest-parsing/comparison helper (lives in the test module; never shipped). 2.2 passes.

### A2c — Connection & Permissions (PR4)

- [ ] 2.4 RED — `test_connection.py`: `open_database` applies `schema.sql` inside one `BEGIN IMMEDIATE`, every DDL statement `IF NOT EXISTS`; reopening an existing database issues no DDL (RQ-29.2). Fails: `connection.py` doesn't exist.
- [ ] 2.5 RED — **design risk, 0600 creation order (D9)**: `test_permissions.py::test_database_file_created_0600_before_connect` — umask-022 fixture; patch `sqlite3.connect` to snapshot the file's mode at call time, assert it is already `0600` (i.e. `os.open(O_CREAT|O_EXCL|O_RDWR, 0o600)` ran and closed before `connect` ever touches the path). `@pytest.mark.req("RQ-40")`.
- [ ] 2.6 GREEN — `connection.py::open_database` per D9: `os.makedirs(parent, 0o700, exist_ok=True)` + explicit `os.chmod(parent, 0o700)`; `os.open(O_CREAT|O_EXCL|O_RDWR, 0o600)` then close then `sqlite3.connect`; DDL applied in one `BEGIN IMMEDIATE`; `artifacts/` created the same way; existing file → `os.stat`, warn if `mode & 0o077`, continue. 2.4 and 2.5 pass.
- [ ] 2.7 RED — `test_permissions.py` remaining scenarios: artefact-store dir `0700`; existing `0644` db still records + warns naming the mode; `-wal`/`-shm` sidecars asserted `0600` under umask 022. `@pytest.mark.req("RQ-40")`.
- [ ] 2.8 GREEN — closes 2.7 (covered by 2.6); add explicit sidecar `chmod` fallback if the sidecar-mode assertion fails on the test platform.

### A2d — SQLite Adapter & Concurrency (PR5)

- [ ] 2.9 RED — `test_sqlite_store.py`: `SqliteExecutionStore` against `ExecutionStoreContract` from 1.5 — completes RQ-30.1 (both adapters now pass the same contract).
- [ ] 2.10 GREEN — `sqlite_store.py::record_execution` — `INSERT … ON CONFLICT(id) DO NOTHING`, boolean return from the INSERT's own row count (D3, no preceding `SELECT`).
- [ ] 2.11 RED — `test_concurrency.py`: two threads POSTing distinct ids into one store instance → two rows, distinct identifiers. `@pytest.mark.req("RQ-38")` (criterion 1 only, per scope).
- [ ] 2.12 GREEN — process-wide `threading.Lock` held across the transaction; `isolation_level=None` + explicit `BEGIN IMMEDIATE … COMMIT`; `PRAGMA journal_mode=WAL` with delete-mode fallback logged once; `synchronous=FULL`; `foreign_keys=ON`; `sqlite3.connect(path, timeout=5.0)`.

## Phase 3: B — Session Ingestion Endpoint (`vantage`, server) — PR6–PR8

### B1 — Ingestion Happy Path (PR6)

- [ ] 3.1 RED — `test_ingestion.py`: well-formed report → `201` + run stored with identifier in body; replay of the same `run.id` → `200 duplicate`, still one row; unversioned path (`/runs`, `/api/runs`) → `404`. Against an injected `InMemoryExecutionStore` (no dependency on PR2–PR5 — matches the design's ordering note). `@pytest.mark.req("RQ-41")`.
- [ ] 3.2 GREEN — `service/schemas.py` (`RunReport` with `extra="forbid"`, envelope `SessionReport` with `extra="ignore"`, `Acknowledgement`); `service/routes/runs.py` (`POST /api/v1/runs`, D3 idempotency); `service/app.py` (factory taking an injected `ExecutionStore`, mounts `/api/v1` only, nothing unversioned).

### B2a — 422-Handler & Basic Rejections (PR7)

- [ ] 3.3 RED — **design risk, FastAPI's default 422 handler leaks input**: `test_rejection.py::test_422_response_never_echoes_input_or_pydantic_types` — asserts the response body has no `"input"` key, no exception class name, no traceback text. `@pytest.mark.req("RQ-42")`.
- [ ] 3.4 GREEN — `service/errors.py`: replace FastAPI's `RequestValidationError` handler; every rejection shapes as `{"error": <code>, "detail": <sentence>, "fields": ["run.started_at", …]}` — dotted paths only.
- [ ] 3.5 RED — `test_rejection.py` remaining basic scenarios: missing field → `422` naming `run.started_at`; non-JSON body → `400 invalid_json`; oversized body → `413`, `count_executions() == 0`; wrong/absent `Content-Type` → `415`. `@pytest.mark.req("RQ-42")`.
- [ ] 3.6 GREEN — media-type check before body read; `MAX_REPORT_BYTES` cap enforced before the read completes; JSON parse-error handling — all routed through `errors.py`'s single shape.

### B2b — Truncation & Contract Docs (PR8)

- [ ] 3.7 RED — `test_rejection.py::test_truncated_body_raw_socket`: a raw socket promises N bytes, sends fewer, closes; ASGI `ClientDisconnect` → `400 incomplete_body`; `count_executions() == 0`. `@pytest.mark.req("RQ-42")` and `@pytest.mark.req("RQ-3")` (criterion 2).
- [ ] 3.8 GREEN — `ClientDisconnect` handling in the body-read path; nothing is written before the whole body is validated (no streaming parse, no partial-parse path).
- [ ] 3.9 Write `docs/api/v1-ingestion.md` — the plugin↔server contract published per ADR-4.
- [ ] 3.10 Write `docs/adr/0011-serve-the-ingestion-api-with-fastapi-on-uvicorn.md` — Nygard, `Status: Proposed`.

## Phase 4: C — Server Configuration & CLI (`vantage`, server) — PR9

- [ ] 4.1 RED — `test_resolution.py`: `resolve_server_config` precedence `--database` > `VANTAGE_DATABASE` > `$XDG_DATA_HOME/vantage/vantage.db` (default `~/.local/share/vantage/vantage.db`) — plain function calls, no server, no I/O.
- [ ] 4.2 GREEN — `core/config/resolution.py`: `ConfigSource(str, Enum)` — **never `StrEnum`**; frozen `ServerConfig`; `resolve_server_config(...)`, pure.
- [ ] 4.3 RED — **threat-matrix "Path authority"**: resolution creates no directory as a side effect; a read-only parent fails loudly at startup, not silently at first write.
- [ ] 4.4 GREEN — closed by 4.2's purity (no I/O in resolution) + a startup check in `cli.py` that surfaces the read-only-parent failure before serving.
- [ ] 4.5 RED — **threat-matrix "Network exposure"**: `--host 0.0.0.0` emits a startup warning naming the missing authentication; the default (`127.0.0.1`) does not.
- [ ] 4.6 GREEN — `service/cli.py::main` — argparse (`--database`, `--host`, `--port`), default bind `127.0.0.1:8765`; `[project.scripts] vantage = "vantage.service.cli:main"` and `fastapi`/`uvicorn` deps added to `packages/vantage/pyproject.toml`.
- [ ] 4.7 Write `docs/adr/0010-store-the-server-database-in-the-user-data-directory.md` — Nygard, `Status: Proposed`.

## Phase 5: D1 — Inert Plugin (`pytest-vantage`) — PR10

- [ ] 5.1 RED — `test_opt_in.py`: differential — run bare vs `-p no:vantage`, byte-identical project trees; socket-level assertion that no connection is attempted with no recording option present. `@pytest.mark.req("RQ-2")`.
- [ ] 5.2 GREEN — `plugin.py::pytest_addoption` (`--vantage`, `--vantage-server=URL`, `--vantage-timeout=S`; ini `vantage_server`/`vantage_timeout`; env `VANTAGE_SERVER`) + `pytest_configure` activation check only — no recorder registered yet, no socket opened.
- [ ] 5.3 RED — **design risk, D2's xdist guard**: `test_xdist_guard.py::test_worker_input_returns_before_registration` — a config double carrying `workerinput` (simulating an xdist worker); assert `pytest_configure` returns before any registration or preflight socket call. `@pytest.mark.req("RQ-1")` (protects "exactly one run entry" under `-n 4`) and `@pytest.mark.req("RQ-27")` (the xdist half of the matrix).
- [ ] 5.4 GREEN — `plugin.py::pytest_configure`: `if hasattr(config, "workerinput"): return` before the activation check.
- [ ] 5.5 RED — **threat-matrix "Outbound request target"**: address resolution refuses `file:///etc/passwd`, `ftp://…`, and a bare host with no scheme, each with a named rejection message.
- [ ] 5.6 GREEN — `config.py::resolve_and_validate_address` — `http`/`https` scheme allow-list only.
- [ ] 5.7 RED — `test_plugin_imports.py`: shared `importwalk` (from `packages/vantage/tests/importwalk.py`, via the root `pythonpath`) over `pytest_vantage` — every import resolves to stdlib or `pytest`. `@pytest.mark.req("RQ-24")` (criterion 2).
- [ ] 5.8 GREEN — verification-only; 5.2/5.6 introduce no non-stdlib/non-pytest import, so 5.7 passes without further change.

## Phase 6: D2 — Reporting & Failure Paths (`pytest-vantage`) — PR11–PR12

### D2a — Recorder, Transport & Happy Path (PR11)

- [ ] 6.1 RED — `test_run_report.py`: `pytester.runpytest_subprocess` against a real `vantage` server (uvicorn, ephemeral port, in-memory adapter) — completed session (RQ-1.1, RQ-31.1: end time later than start); zero-test collection (RQ-1.3); failed collection (RQ-1.4); Ctrl-C/SIGINT (RQ-31.2: start time, null end). `@pytest.mark.req("RQ-1")`, `@pytest.mark.req("RQ-31")`.
- [ ] 6.2 GREEN — `recorder.py::Recorder` (`pytest_sessionfinish`, `pytest_report_header`) assembles the `{"run": {...}}` envelope per D1: `datetime.now(timezone.utc)`, **never `datetime.UTC`**, fixed-width ISO-8601 with `+00:00`; `finished_at` null iff `exitstatus in {2, 3}`. `transport.py::send` — one `urllib` POST at `pytest_sessionfinish`, never per test (RQ-25's shape).
- [ ] 6.3 RED — reuse 5.1's differential plus an assertion that `Recorder` is registered iff `--vantage`/equivalent is present and the preflight succeeded.
- [ ] 6.4 GREEN — `plugin.py`: `pluginmanager.register(Recorder(address, timeout))` wired in after a successful preflight (PR12's 6.8).
- [ ] 6.5 RED — end-to-end xdist check: `-n 4` against a real server leaves exactly one run entry (ties 5.3's unit guard to a real subprocess). `@pytest.mark.req("RQ-1")`, `@pytest.mark.req("RQ-27")`.
- [ ] 6.6 GREEN — verification-only; expected to pass from 5.4 + 6.2 with no further change.

### D2b — RQ-37/RQ-21 Boundary & Timeout (PR12)

- [ ] 6.7 RED — `test_failure_paths.py` (RQ-37): closed port → `ConnectionRefusedError`; unresolvable host → `socket.gaierror`; server killed after configure but before the report (RQ-37.3); 200-test suite → exactly one warning naming the address (RQ-37.4). `@pytest.mark.req("RQ-37")`.
- [ ] 6.8 GREEN — `plugin.py` preflight: `socket.create_connection(addr, connect_timeout).close()`; on failure, `_warn(config, message)` naming the address, no recorder registered.
- [ ] 6.9 RED — `test_failure_paths.py` (RQ-21): reporting path patched to raise on a passing suite (exit 0, one warning) and a failing suite (exit 1, one warning); server accepts-then-closes (exit 0, one warning); server accepts-and-never-answers (exit 0 within `timeout + 5s`); every `Recorder` hook patched to raise (exit 0, no internal error surfaced). `@pytest.mark.req("RQ-21")`.
- [ ] 6.10 GREEN — `boundary.py`: decorator on every `Recorder` hook catching `Exception` (never `BaseException` — `KeyboardInterrupt`/`SystemExit` propagate); warns once, latches `self._disabled`, never assigns `session.exitstatus`; `VantageWarning(UserWarning)` with the terminal-reporter → `sys.stderr` fallback chain. `transport.py`: `report_timeout` (default `10.0s`) bounds every socket operation via `urlopen(timeout=t)`.
- [ ] 6.11 RED — **threat-matrix "Untrusted response"**: a stub server returning 100 MB, non-JSON, or a bare `500` — response read is bounded, and a malformed acknowledgement is a warning, never an exception.
- [ ] 6.12 GREEN — `transport.py::send` — `resp.read(MAX_RESPONSE_BYTES)` (64 KiB), defensive JSON parse of the acknowledgement.

## Phase 7: E — Quality Gates (both — never planned before this milestone) — PR13

- [ ] 7.1 Create `.pre-commit-config.yaml` — `ruff format`, `ruff check --fix`, hygiene hooks, modified-files-only (< 2-3s budget per CLAUDE.md); a pre-push stage adding `mypy --strict` (whole project) + fast unit tests.
- [ ] 7.2 Create `.github/workflows/ci.yml`: 3.10–3.13 × {with, without} xdist matrix (`# RQ-27`); a Python-3.9-install-refused job (`# RQ-27`); a networking-disabled job (`# RQ-28`); a clean-environment install-diff job asserting `pytest-vantage` adds exactly one distribution (`# RQ-24`); `ruff`/`mypy --strict`/`deptry`/`uv build --wheel`.
- [ ] 7.3 Create `.github/workflows/audit.yml` — weekly `pip-audit`.
- [ ] 7.4 Inspection (not Test) — confirm every CI block from 7.2 carries its RQ id in a comment, per CLAUDE.md's "grep -r finds the thing that proves it" rule applied to non-test verification.

## Phase 8: F — Docs & Corrections (both) — PR14

- [ ] 8.1 Modify `docs/architecture.md` — add the ingestion contract and the server-configuration section (D11, D12).
- [ ] 8.2 Modify `docs/adr/0005-…md` and `docs/adr/0006-…md` — correct `vantage-storage/src/vantage_storage/…` paths to the ADR-4 layout; correct ADR-5's index count 12 → 13. Both remain `Status: Proposed`; content only, no status change.

---

## Flagged, Not Actioned

- **ADR-0005/0006 vs. the manifest, until PR14/8.2 lands.** Between PR2's schema-manifest landing and PR14's correction landing, RQ-29's Inspection has two disagreeing sources: ADR-5's prose ("twelve indexes", old paths) and `docs/schema-manifest.md` (thirteen indexes, ADR-4 paths). Trust the manifest during that window; PR14 is the fix, not a re-scope of PR2.
