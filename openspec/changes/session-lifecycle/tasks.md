# Tasks: Session lifecycle — a run row exists while the session is still alive

**Change:** `session-lifecycle` · Strict TDD — every behavioural task names its
failing test first. Test command: `uv run --extra dev pytest`.

**No new `RQ-xx` identifiers are minted.** New obligations (start-write,
heartbeat, abandonment) are named by capability/scenario, per `session-liveness`
and the modified deltas in `run-recording`, `session-ingestion`,
`recording-schema`, `recording-fault-tolerance`.

**pytest entry-point registration: nothing to do.** `packages/pytest-vantage/src/pytest_vantage/plugin.py`
is **unchanged** by this design (D36) — `pytest11 = pytest_vantage.plugin` is
already declared, and the controller-only xdist guard at `plugin.py:142-143`
already runs before `Recorder` construction. No user ever edits a `conftest.py`
for this change.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,110 authored (design's own count: slice 1 ~200, slice 2 ~300, slice 3 ~250, slice 4 ~360) |
| 400-line budget risk | Low per slice, High for the change as a whole |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2 → PR 3 → PR 4 |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Low

Every slice is under the 500-line review budget; the change as a whole is not,
which is why it is chained rather than shipped as one PR. Slice boundaries and
order are the design's own (`rollout-and-migration`), not re-derived here.

### Suggested Work Units

Bases: PR 1 → tracker branch (`ft/session-lifecycle`); PR *n* → PR *n−1* branch.

| Unit | Goal | PR | Forecast | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|----------|----------------------|-----------------|-------------------|
| 1 | Monotonic upsert (both adapters) + created-detection probe + RQ-3 premise-test rename/split | PR 1 | ~200 | `uv run --extra dev pytest packages/vantage/tests/vantage_port_contract.py packages/vantage/tests/test_rejection.py` | N/A — no behaviour change while only the finish-write exists; no live server scenario to add yet | **Reverted LAST in the chain, not first** — reverting it while slice 2's start-writes are still live resurrects the silent-drop bug against real data |
| 2 | Plugin start-write (`pytest_sessionstart`), `liveness_isolated`, liveness timeout | PR 2 | ~300 | `uv run --extra dev pytest packages/pytest-vantage/tests/test_failure_paths.py packages/pytest-vantage/tests/test_run_report.py` | `uv run --extra dev pytest --vantage=<addr>` against a live server; a run row exists before the first test executes | Revert removes the hook and the decorator; the finish-write alone reproduces Milestone-1 behaviour exactly |
| 3 | Schema: `last_contact_at` column + index, `meta.schema_version` stamp, open-time refusal, manifest, ADR-0013 | PR 3 | ~250 | `uv run --extra dev pytest packages/vantage/tests/test_connection.py packages/vantage/tests/test_schema_manifest.py` | Delete a schema file created by the pre-change code; confirm the server refuses to start against it, message on stderr | Revert drops the column and refusal; a Milestone-1 database is opened again, matching pre-change behaviour |
| 4 | Heartbeat endpoint, activity-driven beats, `derive_presentation`, grace config | PR 4 | ~360 | `uv run --extra dev pytest packages/vantage/tests/test_ingestion.py packages/vantage/tests/test_rejection.py packages/pytest-vantage/tests/test_failure_paths.py packages/vantage/tests/test_architecture.py -k liveness` | A suite exceeding one heartbeat interval against a live server; confirm `last_contact_at` advances and a ~10 s suite (RQ-25 profile) sends zero beats | Revert drops the route, `touch_last_contact`, `liveness.py` and grace config; `last_contact_at` sits unpopulated, matching slice 3's end state |

---

## Phase 1: Storage — monotonic upsert, created probe, memory adapter (PR 1)

- [x] 1.1 RED `packages/vantage/tests/vantage_port_contract.py`: finish-after-start
      applies in full (start then finish, `exit_status` NULL→int).
- [x] 1.2 RED, same file: **reordered start-after-finish is a no-op** — the
      recorded finish, its exit fields and its result rows are unchanged
      (`run-recording` "A reordered start-write never nulls a recorded finish").
- [x] 1.3 RED, same file: replayed finish (finish-after-finish) is a no-op,
      first finish wins (RQ-41, unchanged semantics, new discriminator).
- [x] 1.4 RED, same file: duplicate start-after-start is a no-op.
- [x] 1.5 RED, same file (D26): `record_session` returns `True` only on a true
      first insert; a finish applied over an existing start-only row, and a
      true duplicate, both return `False`.
- [x] 1.6 RED `packages/vantage/tests/test_rejection.py`: rename
      `test_five_hundred_results_reach_storage_in_one_commit` to
      `test_finish_report_reaches_storage_in_one_commit`; add an assertion that
      `finished_at` and `exit_status` actually landed, on top of the existing
      commit-count and row-count assertions (D35) — the rename alone is
      cosmetic and a `DO NOTHING` regression would still pass it. Carry the
      Measurements paragraph (252,511 bytes body, ~2,021,039 bytes peak) with
      the renamed test, unchanged.
- [x] 1.7 RED, same file: new sibling running that same finish-write **after**
      a prior accepted start-write — asserts one commit, 500 result rows, and
      the finish fields applied.
- [x] 1.8 RED, same file: new `test_start_write_reaches_storage_in_one_commit`
      — one commit, one run row, `finished_at IS NULL`, zero result rows.
- [x] 1.9 RED, same file: new
      `test_reordered_start_write_never_nulls_a_recorded_finish` — the
      slice-1 acceptance criterion, an explicitly reordered pair, using the
      existing `_CommitCountingConnection` wrapper unchanged.
- [x] 1.10 GREEN `packages/vantage/src/vantage/storage/sqlite_store.py`:
      `_INSERT_RUN` → `_UPSERT_RUN` — `ON CONFLICT(id) DO UPDATE SET
      finished_at, exit_status, interrupted, interrupt_reason ... WHERE
      run.exit_status IS NULL AND excluded.exit_status IS NOT NULL` (D25).
      **Do not add `last_contact_at` to this statement yet** — the column
      does not exist until Phase 3; it is added there. `received_at` and
      `started_at` are never written on the conflict path.
- [x] 1.11 GREEN, same file (D26): one `SELECT 1 FROM run WHERE id = ?`
      immediately after `BEGIN IMMEDIATE`, before the write, to determine
      `created` — never `cursor.rowcount`, never `last_insert_rowid()`. Add
      the docstring sentence justifying `SELECT`-then-write under the same
      transaction and lock (no window exists between them here).
- [x] 1.12 GREEN `packages/vantage/src/vantage/storage/memory.py`: replace
      `if created: self._executions[identity] = execution` with the same
      guard, `stored.exit_status is None and execution.exit_status is not
      None`, and the matching created-detection semantics (RQ-30 parity).
- [x] 1.13 REFACTOR: update `sqlite_store.py` and `memory.py` module
      docstrings to cite D25/D26, matching the existing D3/D5/D8 convention.
- [x] 1.14 GREEN gate: `uv run --extra dev pytest packages/vantage/tests/
      vantage_port_contract.py packages/vantage/tests/test_rejection.py
      packages/vantage/tests/test_sqlite_store.py
      packages/vantage/tests/test_memory_store.py`, `uv run mypy .` clean. No
      behaviour change is observable yet — only the finish-write exists.

## Phase 2: Plugin — start-write and the non-latching failure path (PR 2)

- [ ] 2.1 RED `packages/pytest-vantage/tests/test_failure_paths.py`: extend
      `boundary.py`'s decorator factory tests — a failure isolated by the new
      liveness path does **not** set `_disabled` and does **not** consult it;
      the existing `fault_isolated` path is untouched (name, behaviour,
      message all identical, so no existing test moves).
- [ ] 2.2 RED, same file: a failing start-write emits exactly one warning and
      the session still completes and reports its ordinary exit status
      (`recording-fault-tolerance` "A failed heartbeat does not stop result
      recording", applied here to the start-write's own isolated failure).
- [ ] 2.3 RED `packages/pytest-vantage/tests/test_run_report.py` (or the
      module covering `recorder.py`'s hooks): `pytest_sessionstart` sends a
      report with `finished_at: null`, no `results`, and the session's
      `run_id`; `_started_at` captured in `__init__` matches the value later
      sent by `pytest_sessionfinish` (D32's "identical `started_at`" claim).
- [ ] 2.4 RED, same file: the start-write request uses
      `resolve_liveness_timeout(report_timeout)`, not the finish-write's
      `resolve_report_timeout` value, when the two differ.
- [ ] 2.5 GREEN `packages/pytest-vantage/src/pytest_vantage/boundary.py`:
      `_isolated(flag, description)` factory; `fault_isolated =
      _isolated("_disabled", "error while reporting")` (unchanged behaviour);
      `liveness_isolated = _isolated("_liveness_disabled", "error while
      reporting session liveness")` (D29).
- [ ] 2.6 GREEN `packages/pytest-vantage/src/pytest_vantage/config.py`:
      `resolve_liveness_timeout(report_timeout) -> min(_MAX_SHORT_TIMEOUT,
      report_timeout)`, `_MAX_SHORT_TIMEOUT = 2.0` (D31).
- [ ] 2.7 GREEN `packages/pytest-vantage/src/pytest_vantage/recorder.py`: add
      `pytest_sessionstart`, decorated `@liveness_isolated` (D32 — not
      `@fault_isolated`, so a failed start-write degrades to exactly
      Milestone-1 behaviour once the finish-write's insert branch runs).
- [ ] 2.8 GREEN `packages/pytest-vantage/src/pytest_vantage/transport.py`: add
      `send_heartbeat(address, run_id, *, timeout)` **as a distinct function**,
      not a `path` parameter on the existing `send` (D31) — used by Phase 4,
      declared here so the module's shape is settled once.
- [ ] 2.9 Confirm, **by reading**, `plugin.py:142-143` — `if
      hasattr(config, "workerinput"): return` remains the first statement of
      `pytest_configure`, before `Recorder` construction (D36). No edit
      expected; record that the invariant still holds after this slice's hook
      additions.
- [ ] 2.10 GREEN gate: `uv run --extra dev pytest packages/pytest-vantage`,
      confirm the plugin still imports nothing but pytest and the standard
      library (`rg -n '^import|^from' packages/pytest-vantage/src`, RQ-24).

## Phase 3: Schema — `last_contact_at`, version stamp, refusal (PR 3)

- [ ] 3.1 RED `packages/vantage/tests/test_connection.py`: opening a database
      whose `meta` table has no `schema_version` row is refused
      (`SchemaVersionError`), naming the version found (absent) and required.
- [ ] 3.2 RED, same file: `schema_version < 2`, and `schema_version > 2`, are
      both refused, each naming both versions (D28's superset rule).
- [ ] 3.3 RED, same file: a refusal issues **no DDL** — snapshot
      `sqlite_master` before and after the refused open and assert equality
      — and the connection is closed before the error is raised (RQ-29.2).
- [ ] 3.4 RED, same file: opening a database with `schema_version == 2`
      succeeds and applies no schema-altering statement.
- [ ] 3.5 GREEN `packages/vantage/src/vantage/storage/schema.sql`: add
      `run.last_contact_at TEXT NULL` immediately after `received_at`; add
      `CREATE INDEX IF NOT EXISTS idx_run_last_contact_at ON run
      (last_contact_at)` (index 14); append `INSERT OR IGNORE INTO meta (key,
      value) VALUES ('schema_version', '2')` as the file's last statement, so
      it commits atomically with `_apply_schema`'s existing
      `BEGIN IMMEDIATE`…`COMMIT`. Update the header comment's index count.
- [ ] 3.6 GREEN `packages/vantage/src/vantage/storage/connection.py`:
      `_SCHEMA_VERSION = 2`; `SchemaVersionError(RuntimeError)`; replace `if
      not _schema_already_applied(conn): _apply_schema(conn)` with the
      explicit two-branch form — applied ⇒ check version, not applied ⇒
      apply schema; best-effort `created_at`/`created_by` rows in `meta`
      after creation.
- [ ] 3.7 GREEN `packages/vantage/src/vantage/service/cli.py`: catch
      `SchemaVersionError` at start-up (reached via
      `SqliteExecutionStore.__init__` from `cli.py:92`), print the message to
      stderr, exit non-zero — same shape as the existing
      `DatabaseDirectoryNotWritableError` handling.
- [ ] 3.8 Update `docs/schema-manifest.md`: add the `run.last_contact_at`
      row (`TEXT NULL`, RQ-44, M2), index 14, and correct the `meta` table
      note — it is now genuinely populated at creation, not merely reserved.
- [ ] 3.9 RED then GREEN `packages/vantage/tests/test_schema_manifest.py`:
      `test_fresh_database_matches_the_recorded_ground_truth` moves from
      **10 tables / 125 columns / 13 indexes** to **10 tables / 126 columns /
      14 indexes**.
- [ ] 3.10 Write `docs/adr/0013-refuse-databases-from-an-older-schema-version.md`
      — Nygard (Status: Proposed in the PR), imperative title, linked to
      ADR-5 and RQ-29. Record both rejected alternatives named in D37:
      `ALTER TABLE … ADD COLUMN` migration, and opening read-only in
      degraded mode.
- [ ] 3.11 GREEN gate: `uv run --extra dev pytest packages/vantage/tests/
      test_connection.py packages/vantage/tests/test_schema_manifest.py`,
      `git diff --exit-code -- packages/vantage/src/vantage/storage/schema.sql`
      shows only this slice's addition (no unrelated drift).

## Phase 4: Heartbeat, activity-driven beats, abandonment derivation (PR 4)

- [ ] 4.1 RED `packages/vantage/tests/vantage_port_contract.py`:
      `touch_last_contact` advances `last_contact_at` on a known run; a
      second, earlier-or-equal contact leaves it unchanged (monotonic guard,
      D33); an unknown execution id returns `False`.
- [ ] 4.2 RED `packages/vantage/tests/test_ingestion.py`: `POST
      /api/v1/runs/{id}/heartbeat` for a run created by an accepted
      start-write advances `last_contact_at`; the response is `200
      {"run_id": ..., "status": "acknowledged"}`.
- [ ] 4.3 RED, same file: a heartbeat for a run whose finish is already
      recorded leaves `finished_at`, `exit_status`, `interrupted` and
      `interrupt_reason` exactly as recorded — the body is `{}` and read by
      nothing, so there is no field to smuggle a change through.
- [ ] 4.4 RED `packages/vantage/tests/test_rejection.py`: heartbeat for an
      unknown run id → `404 {"error": "unknown_run", ...}`; a malformed id
      (not `^[0-9a-f]{32}$`) → `422` through the existing
      `register_error_handlers` path, no new code.
- [ ] 4.5 GREEN `packages/vantage/src/vantage/core/ports/storage.py`: add
      `touch_last_contact(execution_id: str, contacted_at: datetime) -> bool`
      to the `ExecutionStore` protocol.
- [ ] 4.6 GREEN `packages/vantage/src/vantage/storage/sqlite_store.py`: add
      `_TOUCH_LAST_CONTACT` (`UPDATE run SET last_contact_at = ? WHERE id = ?
      AND (last_contact_at IS NULL OR last_contact_at < ?)`); extend
      `_UPSERT_RUN`'s insert branch to write `last_contact_at = received_at`
      on creation, left alone on the conflict path (D27) — the column now
      exists (Phase 3).
- [ ] 4.7 GREEN `packages/vantage/src/vantage/storage/memory.py`: mirror
      `touch_last_contact` and the insert-time `last_contact_at` write over
      the dict-backed store (RQ-30 parity).
- [ ] 4.8 GREEN `packages/vantage/src/vantage/service/errors.py`:
      `UnknownRunError(RejectionError)`, `status_code = 404`.
- [ ] 4.9 GREEN `packages/vantage/src/vantage/service/schemas.py`:
      `HeartbeatAcknowledgement`.
- [ ] 4.10 GREEN `packages/vantage/src/vantage/service/routes/runs.py`: `POST
      /api/v1/runs/{run_id}/heartbeat`, `run_id: str =
      Path(pattern=r"^[0-9a-f]{32}$")`; resolve the 404 by calling
      `get_execution` rather than inferring it from a zero-`rowcount` update
      (an out-of-order beat on a known run is `200`, not `404`).
- [ ] 4.11 RED `packages/vantage/tests/test_architecture.py` (or a new
      `test_liveness.py`, stdlib only, no I/O): table-driven
      `derive_presentation` — finished (any staleness) → `FINISHED`;
      interrupted (any staleness) → `INTERRUPTED`, checked before the clock;
      past-grace with no finish/interrupt → `ABANDONED`; inside-grace →
      `RUNNING`; `last_contact_at is None` falls back to `started_at`.
- [ ] 4.12 GREEN `packages/vantage/src/vantage/core/domain/liveness.py`
      (new file, standard library only, RQ-26): `class RunPresentation(str,
      Enum)` — **never `StrEnum`**, floor is 3.10 — with `FINISHED`,
      `INTERRUPTED`, `ABANDONED`, `RUNNING`; `derive_presentation(execution,
      *, last_contact_at, now, grace)` implementing the precedence in D34
      exactly (finished → interrupted → grace comparison → running).
- [ ] 4.13 GREEN `packages/vantage/src/vantage/core/config/resolution.py`:
      `grace_period_seconds`, `grace_source` on `ServerConfig`,
      `--grace-period` CLI flag, default `900.0`
      (`_DEFAULT_GRACE_BEATS = 30 × _BEAT_INTERVAL_HINT_SECONDS`).
- [ ] 4.14 GREEN `packages/vantage/src/vantage/service/app.py`:
      `create_app(store, *, grace_period_seconds=...)` →
      `app.state.grace_period` (no reader yet — a named seam, not dead code,
      per D34's open question).
- [ ] 4.15 GREEN `packages/vantage/src/vantage/service/cli.py`: wire
      `--grace-period` through to `create_app`.
- [ ] 4.16 RED `packages/pytest-vantage/tests/test_failure_paths.py`: a
      heartbeat send patched to fail on every attempt across a session with
      multiple heartbeat intervals emits **exactly one** warning, and every
      test result is still recorded (`recording-fault-tolerance` "A failed
      heartbeat warns once, not once per beat").
- [ ] 4.17 RED, same file: a session where **both** the start-write and a
      heartbeat fail emits **two** warnings — one naming reporting, one
      naming liveness. This is correct and must not collapse into one; do
      not "fix" it into a single warning.
- [ ] 4.18 RED `packages/pytest-vantage/tests/test_run_report.py`: a suite
      exceeding one heartbeat interval sends at least one heartbeat before
      the session finishes; a suite of 1,000 ~10 ms tests (RQ-25's measured
      profile) sends zero.
- [ ] 4.19 GREEN `packages/pytest-vantage/src/pytest_vantage/recorder.py`:
      `pytest_runtest_logreport` calls `accumulate(self._results, report)`
      **first, unconditionally**, then a separately-decorated
      `_maybe_beat()` (`@liveness_isolated`) — a decorator on the hook
      itself cannot stop the outer `@fault_isolated` wrapper from catching
      and latching first (D30). `time.monotonic()` only, never wall clock.
      `_last_beat_at` assigned **before** the send, not after.
      `_BEAT_INTERVAL_SECONDS = 30.0`. Uses `send_heartbeat` from Phase 2.
- [ ] 4.20 Confirm, **by reading**, `plugin.py:142-143` a second time — the
      controller-only guard still precedes every hook this slice added,
      including the beat inside `pytest_runtest_logreport` (D36).
- [ ] 4.21 GREEN `packages/pytest-vantage/tests/test_xdist_guard.py`: add
      the assertion that no xdist worker constructs a `Recorder` (D36).
- [ ] 4.22 Final gate: `uv run ruff format . && uv run ruff check --fix .`,
      `uv run mypy .`, `uv run deptry .`; run
      `uv run --extra dev pytest` locally on the interpreter available in
      this environment and the `-n auto` xdist path; **state explicitly**
      that the 3.10–3.13 matrix, the networking-disabled RQ-28 job and the
      clean-environment RQ-24 install check were **not** run locally and are
      left to CI — do not claim a matrix run that did not happen.
- [ ] 4.23 Traceability sweep: `rg "RQ-1\.5|RQ-1\.6|RQ-31\.3|RQ-3\.2|RQ-42\.3|
      RQ-29|RQ-21|RQ-25|RQ-44|RQ-26|RQ-30|RQ-24"` each reach the test that
      proves them; confirm `derive_presentation`'s new obligations carry no
      new `RQ-xx` marker.
