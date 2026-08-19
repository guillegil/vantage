# Design: Session lifecycle — a run row exists while the session is still alive

> Decisions continue the archived `capture-test-results` numbering (D15–D24) and run
> **D25–D37**. Size: this document deliberately exceeds the 800-word phase budget, for
> the same reason the previous design did — the exact SQL, the exact failure-path
> separation and the exact refusal ordering are the parts that cost a rewrite if left
> to apply time.

## Technical Approach

Three writes now describe one session where one did before: a **start-write** at
`pytest_sessionstart`, **heartbeats** while it runs, and the existing **finish-write**
at `pytest_sessionfinish`. Everything else in this design exists because those three
share one row.

The change lands in four layers, and the order is the proposal's slice order:

1. **Storage** learns to update a run it already has, monotonically, so the finish-write
   is not discarded (D25) and so `created` still means what the route thinks it means (D26).
2. **The plugin** grows a second, narrower failure path — one that warns once and never
   takes result accumulation down with it (D29, D30) — and a second, shorter timeout (D31).
3. **The schema** gains `last_contact_at` and, with it, the first use of `meta.schema_version`
   as a refusal gate rather than a decoration (D28).
4. **The server** gains a heartbeat endpoint (D33) and `vantage.core` gains the pure
   abandonment derivation the read API will call (D34).

Specs: `run-recording` (RQ-1.5/1.6, RQ-31.3, RQ-3), `session-ingestion` (RQ-42.3),
`recording-schema` (RQ-29), `recording-fault-tolerance` (RQ-21), `session-liveness` (new).

---

## Architecture Decisions

### D25 — The upsert guard keys off `exit_status`, never `finished_at`

`_INSERT_RUN` (`sqlite_store.py:51`) is `ON CONFLICT(id) DO NOTHING`. With a start-write
in front of it, the finish-write carries the same `id` and is discarded whole — `finished_at`,
`exit_status`, `interrupted`, `interrupt_reason` and every result row, with no error and a
`200 duplicate` acknowledgement telling the plugin all is well. This is the one change
required by the mere existence of a start-write.

The obvious discriminator for "this report is a finish report" is `finished_at IS NOT NULL`,
and it is **wrong**. A Ctrl-C session reports `exit_status: 2` with `finished_at: null`
(`recorder.py:34`, `_NULL_FINISH_EXIT_STATUSES`) — gating on `finished_at` would silently
drop `interrupted` and `interrupt_reason` for exactly the case RQ-31.2 and RQ-44.3 exist to
capture. The correct discriminator is `exit_status`: a start-write sends `null`, every
finish-write sends an integer.

```sql
_UPSERT_RUN = """
    INSERT INTO run (
        id, received_at, last_contact_at, started_at, finished_at,
        exit_status, interrupted, interrupt_reason
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        finished_at      = excluded.finished_at,
        exit_status      = excluded.exit_status,
        interrupted      = excluded.interrupted,
        interrupt_reason = excluded.interrupt_reason
     WHERE run.exit_status IS NULL AND excluded.exit_status IS NOT NULL
"""
```

The `WHERE` on the `DO UPDATE` is the monotonic guard, the same idea as the `MAX`/`CASE`
clause `_UPSERT_TEST_CASE` already uses two statements below, in the form SQLite offers for
a predicate that governs the whole row rather than one column. One predicate cannot drift
out of sync with itself the way four parallel `CASE` expressions can.

Four orderings, all of which must hold and all of which are acceptance criteria:

| Arrival | `run.exit_status` | `excluded.exit_status` | Effect |
| --- | --- | --- | --- |
| start, then finish | NULL | int | finish applies in full |
| **finish, then start (reordered)** | int | NULL | **no-op — the recorded finish survives** |
| finish, then finish (replay, RQ-41) | int | int | no-op, first finish wins |
| start, then start (duplicate) | NULL | NULL | no-op |

`received_at` and `started_at` are **never** advanced on the conflict path. `received_at`
stays the first-receipt clock — the proposal rejected overloading it by name, and leaving it
alone is how that rejection is enforced rather than merely stated. `started_at` staying put
is what makes RQ-44.4's "the start time it was recorded with is unchanged" literally true.

Two consequences worth stating so they are not discovered: a suppressed second finish still
runs `_INSERT_RESULT`, whose own `ON CONFLICT DO NOTHING` makes a replay a no-op (RQ-41,
unchanged); and a reordered start-write carries no `results`, so `if results:` is false and
neither the catalogue upsert nor the result insert executes. D22's fixed statement order is
unchanged.

**`storage/memory.py` has the identical bug and the proposal's Affected Areas table omits
it.** `InMemoryExecutionStore.record_session` does `if created: self._executions[identity] = execution`
— first-write-wins, exactly `DO NOTHING`. RQ-30 and the shared contract suite require both
adapters to agree, so the same `stored.exit_status is None and execution.exit_status is not None`
guard lands there in the same slice.

### D26 — `created` can no longer be read off `rowcount`; one existence probe

`record_session` decides its boolean from `cursor.rowcount == 1` and the route turns it into
`201` vs `200` (`runs.py:218`). Under `DO NOTHING` that works: a conflict changes zero rows.
Under `DO UPDATE` an applied finish also changes one row, so `rowcount` would report a
finish-write as a creation and answer `201` for a run that already existed.

`RETURNING` is unavailable (SQLite ≥ 3.35, above the 3.10 floor — `sqlite_store.py`'s
module docstring already rules it out). `last_insert_rowid()` happens to move only on a real
insert, but betting a wire-visible status code on an undocumented interaction between UPSERT
and an implicit rowid is not a guarantee worth taking.

**One `SELECT 1 FROM run WHERE id = ?` immediately after `BEGIN IMMEDIATE`, before the
insert.** The module docstring argues against `SELECT`-then-`INSERT`, and that argument is
about a check performed *outside* the write transaction: two connections both passing the
`SELECT` and both attempting the `INSERT`. There is no such window here. `BEGIN IMMEDIATE`
is the first statement and takes the `RESERVED` lock for the whole transaction, and
`self._lock` serialises the server's threadpool, so the probe reads under precisely the lock
the insert will write under. The docstring gains a sentence saying so; the invariant it
protects is unchanged. Cost: one primary-key point lookup per write, and D22 becomes five
statements in one transaction rather than four.

`Acknowledgement.status` keeps its two values. A finish-write following a start-write answers
`200 duplicate`, which is imprecise — it did apply data. Adding a third value is wire-visible,
no scenario asks for it, and the plugin never reads the body (`transport.py:61` parses and
discards it). Left as an open question for the read API, not resolved here by inventing
vocabulary.

### D27 — `last_contact_at` is set by the creating report, not left null until the first beat

`_UPSERT_RUN`'s insert branch writes `last_contact_at = received_at`; the conflict branch
leaves it alone. So every run row has a last contact from the instant it exists, and the
derivation (D34) is a pure function of one column with no fallback in the normal path.

This does **not** measure grace from the run's start. `received_at` is the *server's* clock
at the moment the start-write arrived — a contact. `started_at` is the *client's* clock and
is never used for staleness, so a machine with a skewed clock cannot make its run look fresh
or stale. That distinction is the whole reason the two columns exist separately.

The finish-write does not advance `last_contact_at`: a finished or interrupted run
short-circuits the derivation before the clock is ever consulted, so advancing it would be
machinery with no observable effect.

The column stays `TEXT NULL` as the spec delta fixes it. Nothing this code writes leaves it
null; nullability is what keeps the rollback free (revert the code and the column sits
unread), and the derivation still handles `None` as a defensive branch for a row this code
did not write.

Comparison is lexicographic (`last_contact_at < ?` in the touch statement), which is sound
only at fixed width. `datetime.isoformat()` omits microseconds when they are exactly zero,
so the server writes this column through a fixed-width helper mirroring
`pytest_vantage.recorder.isoformat_utc`. **The same latent width hazard already exists for
`test_case.last_seen_at`'s `MAX` (D20) and is out of scope here — named, not fixed.**

### D28 — `meta.schema_version` does not exist yet; the stamp ships in `schema.sql`

The refusal policy has a hole underneath it that neither the proposal nor the spec could see:
**nothing populates `meta`.** `schema.sql:30` says "Populated at creation, not by this file"
and `docs/schema-manifest.md:69` says that "lands with `connection.py`, PR4" — `connection.py`
never writes a row. Every Milestone-1 database therefore has an empty `meta` table, and a
refusal that reads `schema_version` finds nothing rather than `1`.

So the design is:

- `_SCHEMA_VERSION = 2` in `connection.py`.
- **`schema.sql` stamps its own version**: `INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '2');`
  as the last statement of the file. `connection.py` already runs the whole file inside one
  `BEGIN IMMEDIATE`…`COMMIT` (`_apply_schema`), so the stamp is atomic with the schema it
  describes and no crash can leave a schema without one. `OR IGNORE` is the DML register of
  the same `IF NOT EXISTS` idempotence the rest of the file relies on, which keeps
  `test_reapplying_schema_sql_issues_no_error_and_no_count_change` passing.
- `created_at` and `created_by` are written by `connection.py` after creation, best-effort.
  Nothing keys off them, so losing them to a crash costs nothing.
- **The refusal, on open:**

  | Read of `meta.schema_version` | Meaning | Action |
  | --- | --- | --- |
  | absent, or not an integer | a database created before this change | refuse |
  | `< 2` | an older Phase 1 release | refuse |
  | `== 2` | current | open |
  | `> 2` | written by a newer build | refuse |

  Refusing the newer direction too is one rule instead of two: a build that does not know a
  column cannot honour whatever invariant the build that added it assumed. The spec only
  demands the older direction; this is a superset, stated rather than smuggled.

- `SchemaVersionError(RuntimeError)` names the version found, the version required, and the
  path, and says to recreate the database. It **closes the connection before raising**, and
  issues no DDL — refusing is not altering, which is the whole argument that keeps RQ-29.2
  literally true.
- **Where**: in `open_database`, replacing `if not _schema_already_applied(conn): _apply_schema(conn)`
  with an explicit two-branch form — applied ⇒ check version, not applied ⇒ apply schema. The
  pragmas still run first, so a refused database gets `-wal`/`-shm` sidecars created before it
  is rejected. Harmless, no schema statement, and named here so it is not mistaken for a defect.
- **Who sees it**: `open_database` is reached from `SqliteExecutionStore.__init__`, called by
  `service/cli.py:92` at start-up. `main` catches `SchemaVersionError` and exits non-zero with
  the message on stderr, in the same shape it already uses for
  `DatabaseDirectoryNotWritableError` (`cli.py:86-88`) — a traceback is not an operator message.
  The operator learns once, at start, not per request.

### D29 — Two failure paths, two flags, two warnings

`fault_isolated` latches on `self._disabled`: once tripped, **every** further hook on that
instance is a silent no-op (`boundary.py:89`). That is correct for the reporting path and
catastrophic for a heartbeat — one blinking second would cost a two-hour suite its results.

`boundary.py` gains a second decorator built from the same implementation, parameterised by
the flag it reads and the message it emits:

```python
def _isolated(flag: str, description: str) -> Callable[[F], F]: ...

fault_isolated    = _isolated("_disabled", "error while reporting")
liveness_isolated = _isolated("_liveness_disabled", "error while reporting session liveness")
```

`fault_isolated`'s name, behaviour and message are unchanged, so no existing test moves.
`liveness_isolated` reads and sets only `_liveness_disabled`, never `_disabled`, and never
consults it — the two paths are independent in both directions.

It **does** latch, on its own flag, and that is deliberate rather than a compromise: a server
that failed one beat has almost certainly failed the next, and 240 further attempts across a
two-hour suite would each cost a bounded 2.0 s stall for no information. Latching the beat
gives RQ-37's "one warning per session" shape for free — there is only ever one failure to
warn about. What it must never do, and does not, is disable result accumulation,
`pytest_sessionfinish`, or the finish-write, each of which keeps its own independent attempt
with its own timeout.

**The warning budget is one per path, and there are two paths.** A session where both fail
emits two warnings, one naming reporting and one naming liveness. That is correct and must
not be "fixed" into one: they are two different failures with two different remedies, and
RQ-21's existing scenarios count warnings within the reporting path while the new
fault-tolerance scenarios count them within the liveness path.

### D30 — The beat lives inside the hook body, after accumulation, and clocks itself first

The trap: `pytest_runtest_logreport` is already wrapped by `fault_isolated`, so a beat that
raises anywhere in that hook's body is caught by the **outer** decorator, which latches
`_disabled` and stops result accumulation for the rest of the session. A decorator on the
hook cannot prevent that. The beat must therefore be isolated *inside* the body, on its own
helper:

```python
@fault_isolated
def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
    accumulate(self._results, report)   # first, always
    self._maybe_beat()                  # wrapped separately; cannot raise

@liveness_isolated
def _maybe_beat(self) -> None:
    now = time.monotonic()
    if now - self._last_beat_at < _BEAT_INTERVAL_SECONDS:
        return
    self._last_beat_at = now            # before the send, not after
    send_heartbeat(self._address, self._run_id, timeout=self._liveness_timeout)
```

Three details are load-bearing:

- **`accumulate` runs first.** A beat failure can then never precede the accumulation of the
  report that triggered it, even if `liveness_isolated` were ever holed.
- **`time.monotonic()`, never wall clock.** An NTP step or a DST transition must not fire a
  beat storm or suppress beats for an hour.
- **`_last_beat_at` is assigned before the send.** Assigning after would let a slow or failing
  send be retried on the very next report, converting one stall per interval into one per test.

`_BEAT_INTERVAL_SECONDS = 30.0`, as proposed. RQ-25's measured profile — 1,000 tests at ~10 ms,
a ~10-second suite — fires zero beats, so the added wall-clock against the requirement's own
measurement is a `time.monotonic()` subtraction per report.

The documented gap stands unchanged and unmitigated by anything here: no pytest hook fires
during a test's body, so a single very long test produces no beat while it runs
(`session-liveness`, requirement 3). The timer thread that would close it, and server-side
inference, are both already rejected with reasons in the proposal — referenced, not re-argued.

### D31 — Three requests, two timeouts

| Request | Bound | Why this one |
| --- | --- | --- |
| finish-write (`pytest_sessionfinish`) | `--vantage-timeout` → `vantage_timeout` → 10.0 (`resolve_report_timeout`, unchanged) | carries the whole payload, up to `MAX_REPORT_BYTES`; it is the one request whose loss is data loss |
| start-write (`pytest_sessionstart`) | `resolve_liveness_timeout(report_timeout)` | fixed ~200-byte payload, and it blocks the session **before the first test runs**; a 10 s stall at t=0 is the "slow or stuck?" failure ADR-9 names |
| heartbeat | `resolve_liveness_timeout(report_timeout)` | sits inside the per-test loop RQ-25 protects |

`config.py` gains `resolve_liveness_timeout(report_timeout) -> min(_MAX_SHORT_TIMEOUT, report_timeout)`
with `_MAX_SHORT_TIMEOUT = 2.0`. The `min` is the one move from the proposal's number and the
reason is stated: `--vantage-timeout` is a ceiling the *user* chose, the liveness bound is a
ceiling this *design* imposes, and taking the smaller honours both — a user who asks for 0.5 s
gets 0.5 s everywhere and never more than they asked for.

`plugin.py`'s `_MAX_CONNECT_TIMEOUT = 2.0` stays where it is, unshared. It is the same number
for a related reason, but the preflight is not a liveness request, and folding them into one
constant would make a future change to either silently change the other.

`transport.py` gains `send_heartbeat(address, run_id, *, timeout)` rather than a `path`
parameter on `send` — `send`'s contract and docstring stay true, and the two calls differ in
more than their path (D33's body is empty of meaning).

### D32 — A failed start-write degrades to Milestone-1 behaviour, deliberately

RQ-21 forbids failing loudly, so the only question is which quiet behaviour. Two candidates:

| Option | Result |
| --- | --- |
| Latch the instance (today's `fault_isolated`) | a server unavailable for one second at t=0 costs the entire session's results — **strictly worse than the behaviour this change replaces** |
| **Warn once, keep going** | the finish-write still fires, and its upsert simply takes the INSERT branch because no row exists — producing exactly today's row, complete |

The start-write is therefore `@liveness_isolated`, not `@fault_isolated`. Its loss costs the
observability of a session that never finishes (RQ-1.5/1.6, RQ-31.3), never any recorded data,
because the finish-write's insert branch is a complete write on its own. This is a decision:
the degraded state is the *previous* release's behaviour, not a new half-state, which is what
makes it acceptable to degrade silently past the one warning.

`pytest_sessionstart` fires after `pytest_configure`, where `Recorder` is registered, so the
hook is received. `_started_at` is captured in `__init__`, so the start-write and the
finish-write report the identical `started_at` and D25's "leave `started_at` alone" rule is
invisible in practice.

### D33 — Heartbeat wire shape, and the answer for an unknown run

```
POST /api/v1/runs/{run_id}/heartbeat
Content-Type: application/json
{}                                       ← read by nothing

200 {"run_id": "…32 hex…", "status": "acknowledged"}
404 {"error": "unknown_run", "detail": "No run with that identifier has been recorded.", "fields": []}
422 {"error": "invalid_report", …}        ← malformed id, via the existing handler
```

The request body is `{}` and the server never reads it. That is the strongest possible form of
the spec's "MUST NOT accept or apply `finished_at`, `exit_status`, `interrupted` or
`interrupt_reason`": there is no field to send. The body exists at all only so the plugin's
POST is symmetric with `send` and so no intermediary objects to a body-less POST. Because the
body is never read, `MAX_REPORT_BYTES` and the streaming cap are not applicable rather than
bypassed — nothing buffers.

`run_id` is a path parameter with `pattern=r"^[0-9a-f]{32}$"`, so a malformed id is a `422`
through `register_error_handlers`' existing `RequestValidationError` path, with no new code.
The run id is never echoed into a response body.

**Unknown run id ⇒ `404`, not a silent accept.** A beat for an id the server never saw means
the start-write was lost or the database was replaced; accepting it would either manufacture
liveness for a run that does not exist or require inventing a row with no `started_at`. The
plugin's reaction is `liveness_isolated`'s single warning, which is exactly the signal the
operator needs. `UnknownRunError(RejectionError)` with `status_code = 404` slots into
`errors.py`'s existing one-shape rejection machinery.

Storage: `touch_last_contact(execution_id, contacted_at) -> bool` on the port, implemented as
one statement, monotonically guarded for the same reason as D25:

```sql
_TOUCH_LAST_CONTACT = """
    UPDATE run
       SET last_contact_at = ?
     WHERE id = ?
       AND (last_contact_at IS NULL OR last_contact_at < ?)
"""
```

`False` from a zero-`rowcount` update is ambiguous between "unknown run" and "a newer contact
is already recorded", so the route asks `get_execution` for the 404 decision rather than
inferring it from the update — an out-of-order beat on a known run is a `200`, not a `404`.

### D34 — Abandonment is a pure `vantage.core` function, with a named precedence and no caller

`vantage/core/domain/liveness.py`, standard library only (RQ-26):

```python
class RunPresentation(str, Enum):        # never StrEnum — 3.11+, floor is 3.10
    FINISHED = "finished"
    INTERRUPTED = "interrupted"
    ABANDONED = "abandoned"
    RUNNING = "running"

def derive_presentation(
    execution: Execution, *, last_contact_at: datetime | None,
    now: datetime, grace: timedelta,
) -> RunPresentation: ...
```

The precedence **is** the requirement, in this order:

1. `finished_at is not None` → `FINISHED`. An orderly end is an end.
2. `interrupted` → `INTERRUPTED`. RQ-44.3: a report *did* arrive, so it is never abandoned no
   matter how stale its last contact. This must be checked before the clock, and it is not
   shadowed by rule 1 because a Ctrl-C run carries `finished_at is None`.
3. `now - (last_contact_at or started_at) > grace` → `ABANDONED`.
4. otherwise → `RUNNING`.

The `or started_at` in rule 3 is the defensive branch of D27 — unreachable for any row this
code writes, kept for a row written by another adapter or edited by hand.

Nothing invents a stored field (RQ-44.4): the function reads columns that already exist and
returns a value that is never persisted.

**Grace configuration** is server-side, CLI-only, matching `host`/`port`'s precedent:
`--grace-period` seconds, default `900.0`, expressed in code as
`_DEFAULT_GRACE_BEATS = 30 × _BEAT_INTERVAL_HINT_SECONDS = 30.0` so the "multiple of the beat
interval" relationship lives in the source rather than a comment. It reaches
`ServerConfig.grace_period_seconds` and then `create_app(store, *, grace_period_seconds=…)`
→ `app.state.grace_period`.

Two honest notes. The beat interval is declared on both sides of an HTTP boundary that RQ-24
and ADR-9 forbid sharing code across — the same duplication ADR-9 already forces on the outcome
vocabulary. Here the server's copy is only a *hint* used to derive a default, so a divergence
yields a grace period that is a different multiple, never a wrong answer. And
`app.state.grace_period` has **no reader** until the read API lands: it is a deliberate seam,
named here so it is not deleted as dead code.

### D35 — RQ-3's premise tests, renamed and re-loaded

The Analysis argument moves from one commit per *session* to one commit per *report*, so its
premise tests move with it.

| Test | Change |
| --- | --- |
| `test_five_hundred_results_reach_storage_in_one_commit` (`test_rejection.py:356`) | **renamed** `test_finish_report_reaches_storage_in_one_commit`, and it must now assert the finish fields actually landed (`finished_at`, `exit_status`) on top of the existing commit count and row counts — otherwise the rename is cosmetic and a `DO NOTHING` regression passes it |
| — | new sibling running the same finish-write **after** a start-write, asserting one commit, 500 rows, and the applied finish |
| — | new `test_start_write_reaches_storage_in_one_commit`: one commit, one run row, `finished_at IS NULL`, zero result rows |
| — | new `test_reordered_start_write_never_nulls_a_recorded_finish`: the slice-1 acceptance criterion, an explicitly reordered pair |

The `_CommitCountingConnection` wrapper already in that module is reused unchanged. The spec's
**Measurements** paragraph (252,511 bytes body, ~2,021,039 bytes peak) is attached to the old
test name and must travel with the rename, or the spec cites a test that no longer exists.

### D36 — xdist is inherited, not re-implemented — confirmed by reading

`plugin.py:142-143` makes `if hasattr(config, "workerinput"): return` the **first** statement
of `pytest_configure`, before activation, before the preflight, before registration. A worker
therefore never constructs a `Recorder`, so `pytest_sessionstart`, `pytest_runtest_logreport`
and every beat exist only on the controller. RQ-12 holds for all three new behaviours with
zero xdist-specific code, and `pytest-vantage` still imports nothing but pytest and the
standard library (RQ-24, ADR-4).

State it as an invariant rather than a happy accident: moving that registration out from behind
the guard would break RQ-1's "exactly one run entry" *and* multiply beats by the worker count.
`packages/pytest-vantage/tests/test_xdist_guard.py` is the thing that catches it and gains an
assertion that no worker constructs a `Recorder`.

### D37 — Exactly one decision here earns an ADR

`openspec/config.yaml` and CLAUDE.md set the filter: more than a sprint to reverse.

| Decision | Reversal cost | Verdict |
| --- | --- | --- |
| **Phase 1 evolves its schema by refusing older databases rather than migrating them** | binds every later schema change; once anyone holds a database, reversing it means building the migration path ADR-5 declined, retroactively. **Far more than a sprint** | **ADR-0013 — "Refuse databases from an older schema version rather than migrating them"**, Nygard, `Proposed` in the PR, linked to ADR-5 and RQ-29, with `ALTER TABLE … ADD COLUMN` and read-only degradation recorded as rejected |
| `last_contact_at` as a nullable column | `git revert`; the column sits null and unread (proposal rollback plan). **Minutes** | Design note (D27) |
| Heartbeat on its own endpoint | delete a route; nothing is keyed by it | Design note (D33) |
| `exit_status` as the upsert discriminator | one `WHERE` clause | Design note (D25) |
| A second, non-latching failure path | one decorator and one flag | Design note (D29) |
| Grace period CLI-only, no env var | adding an env var later is one line | Design note (D34) |

No existing ADR is restated: ADR-3 (clean architecture, `Protocol` ports), ADR-4 (two
distributions), ADR-5 (complete schema, no migration framework), ADR-6 (stdlib `sqlite3`),
ADR-9 (the server owns every write) and ADR-12 (node-id catalogue key) are referenced and
relied on, never re-argued.

---

## Data Flow

```
  pytest_configure ── xdist controller only (D36) ── register Recorder
        │
  pytest_sessionstart ──► POST /api/v1/runs {run, finished_at:null, no results}
        │                    └─► _UPSERT_RUN inserts; last_contact_at = received_at (D27)
        │                    └─► fails? one warning, keep going (D32)
        │
  pytest_runtest_logreport ──► accumulate(_results)          ← always first (D30)
        │                 └──► _maybe_beat() every ~30 s
        │                        └─► POST /api/v1/runs/{id}/heartbeat  (2.0 s bound)
        │                              └─► UPDATE run SET last_contact_at  (monotonic)
        │                              └─► fails? one warning, beats stop, results do not
        │
  pytest_sessionfinish ──► POST /api/v1/runs {run + results}   (report timeout)
                             └─► _UPSERT_RUN: DO UPDATE ... WHERE exit_status guard (D25)
                             └─► catalogue upsert, resolve, result insert  (D22, unchanged)

  SIGKILL anywhere above ── row already present: started_at, finished_at NULL,
                            no interrupt reason (RQ-31.3), last_contact_at frozen
                            └─► derive_presentation(...) ⇒ ABANDONED past grace (D34)
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Modify | `_INSERT_RUN` → `_UPSERT_RUN` (D25); existence probe (D26); `_TOUCH_LAST_CONTACT` (D33) |
| `packages/vantage/src/vantage/storage/memory.py` | Modify | Same monotonic guard and `touch_last_contact`; second mechanism, not a stub (RQ-30) |
| `packages/vantage/src/vantage/storage/schema.sql` | Modify | `run.last_contact_at TEXT NULL`; `idx_run_last_contact_at`; `INSERT OR IGNORE` version stamp (D28) |
| `packages/vantage/src/vantage/storage/connection.py` | Modify | `_SCHEMA_VERSION`, `SchemaVersionError`, refusal on open, `created_at`/`created_by` rows (D28) |
| `packages/vantage/src/vantage/core/ports/storage.py` | Modify | `touch_last_contact(execution_id, contacted_at) -> bool` |
| `packages/vantage/src/vantage/core/domain/liveness.py` | Create | `RunPresentation`, `derive_presentation` — stdlib only (D34) |
| `packages/vantage/src/vantage/core/config/resolution.py` | Modify | `grace_period_seconds`, `grace_source` on `ServerConfig` |
| `packages/vantage/src/vantage/service/routes/runs.py` | Modify | `POST /runs/{run_id}/heartbeat` (D33) |
| `packages/vantage/src/vantage/service/schemas.py` | Modify | `HeartbeatAcknowledgement` |
| `packages/vantage/src/vantage/service/errors.py` | Modify | `UnknownRunError`, `status_code = 404` |
| `packages/vantage/src/vantage/service/app.py` | Modify | `create_app(store, *, grace_period_seconds=…)`; `app.state.grace_period` |
| `packages/vantage/src/vantage/service/cli.py` | Modify | `--grace-period`; catch `SchemaVersionError` → stderr, exit 1 |
| `packages/pytest-vantage/src/pytest_vantage/recorder.py` | Modify | `pytest_sessionstart`; `_maybe_beat`; beat constants (D30, D32) |
| `packages/pytest-vantage/src/pytest_vantage/boundary.py` | Modify | `_isolated` factory; `liveness_isolated` (D29) |
| `packages/pytest-vantage/src/pytest_vantage/transport.py` | Modify | `send_heartbeat` (D31) |
| `packages/pytest-vantage/src/pytest_vantage/config.py` | Modify | `resolve_liveness_timeout`, `_MAX_SHORT_TIMEOUT` (D31) |
| `packages/pytest-vantage/src/pytest_vantage/plugin.py` | **Unchanged** | The xdist guard is already correct and is what D36 relies on |
| `docs/schema-manifest.md` | Modify | `run.last_contact_at` row; index 14; `meta` note corrected — it is now genuinely populated |
| `docs/adr/0013-refuse-databases-from-an-older-schema-version.md` | Create | The one decision past the reversal-cost filter (D37) |

## Interfaces / Contracts

**Port** (`vantage/core/ports/storage.py`) — one added method:

```python
def touch_last_contact(self, execution_id: str, contacted_at: datetime) -> bool:
    """Advance `execution_id`'s last contact. False if unknown, or if a newer
    contact is already recorded. Never touches the finish fields."""
```

**Schema delta** — `run` gains one column, immediately after `received_at`:

```sql
last_contact_at  TEXT NULL,                                     -- ISO-8601 UTC
CREATE INDEX IF NOT EXISTS idx_run_last_contact_at ON run (last_contact_at);  -- 14
INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '2');
```

Manifest row: `` `last_contact_at` | TEXT NULL | RQ-44 | M2 ``. The schema goes from
**10 tables / 125 columns / 13 indexes** to **10 tables / 126 columns / 14 indexes**, so
`test_schema_manifest.py::test_fresh_database_matches_the_recorded_ground_truth` moves both
numbers. `schema.sql`'s header comment ("thirteen indexes") moves with them.

## Testing Strategy

Strict TDD, RED first; every verifying test carries `@pytest.mark.req(id="RQ-xx")` for the
identifiers that exist. New obligations are named by capability and scenario — **no new
`RQ-xx` is minted.**

| Layer | What | Approach |
| --- | --- | --- |
| Contract (both adapters) | finish-after-start applies; **reordered start never nulls a finish**; replayed finish is a no-op; `touch_last_contact` monotonic + unknown-id `False` | `vantage_port_contract.py` — SQLite and in-memory, one suite (RQ-30) |
| Unit (core) | `derive_presentation` precedence: finished, interrupted-past-grace, abandoned-past-grace, running-inside-grace, null last contact | Table-driven, stdlib `datetime`, no I/O (RQ-44) |
| Unit (storage) | commit counts and applied fields for start-write and finish-write (D35) | `_CommitCountingConnection`, `test_rejection.py` |
| Unit (connection) | version absent ⇒ refused; `<2` ⇒ refused; `>2` ⇒ refused; `==2` ⇒ opens; **refusal issues no DDL**; connection closed | `test_connection.py`, `sqlite_master` snapshot before/after (RQ-29.2) |
| Unit (plugin) | `liveness_isolated` does not set `_disabled`; a failing beat leaves accumulation intact; one warning across many failed beats; `_last_beat_at` advances before the send | Fake transport raising, synthetic reports |
| Integration (service) | heartbeat advances `last_contact_at`; heartbeat cannot alter finish fields; unknown id ⇒ 404; malformed id ⇒ 422; truncated finish after an accepted start-write leaves the start row exactly as written (RQ-42.3, RQ-3.2) | `test_ingestion.py`, `test_rejection.py`, raw-socket truncation |
| E2E | a killed session leaves start time, null end, no interrupt reason (RQ-31.3, RQ-1.6); a running session already has a row (RQ-1.5); a long suite advances last contact; a ~10 s suite emits zero beats (RQ-25) | `pytester` subprocesses against `vantage_test_server.py`, extended to count heartbeats |
| Inspection | manifest ↔ schema, both directions, plus the new counts | `test_schema_manifest.py` (RQ-29) |
| Architecture | `vantage.core` stdlib-only with `liveness.py` added; `pytest-vantage` still imports no third party | Existing `test_architecture.py` AST walk, `deptry`, clean-environment install job |

## Threat Matrix

`references/threat-matrix.md` is **N/A**: no routing selector, shell command, subprocess,
VCS/PR automation, executable-file classification or process-integration boundary is added.
All five rows are N/A for that reason.

The HTTP boundary this change *does* touch stays inside the Milestone-1 matrix, with three
notes rather than new rows: the heartbeat's `run_id` is constrained by a path pattern and is
**never echoed** into a response body; the heartbeat never reads its request body, so the
unbounded-body row is not applicable rather than bypassed; and the refusal message names a
filesystem path but is operator-facing on stderr at start-up, never in an HTTP response.

## Migration / Rollout

**No migration — that is the decision, and it is what ADR-0013 records.** An existing
Milestone-1 database is refused at open with a message naming both versions; the operator
deletes it and a fresh one is created complete. No `ALTER TABLE`, no backfill, no framework.
Pre-1.0, synthetic data only, no deployments.

Rollout is the proposal's four-slice feature-branch chain, server before plugin so the plugin
never reports into a stub:

| # | Slice | Est. lines | Independently deliverable |
| --- | --- | --- | --- |
| 1 | Upsert + probe + memory adapter + RQ-3 premise tests (D25, D26, D35) | ~200 | Yes — no behaviour change while only one write exists |
| 2 | Start-write, `liveness_isolated`, liveness timeout (D29, D31, D32) | ~300 | Yes — **depends on slice 1 for correctness** |
| 3 | Schema, version stamp, refusal, manifest, ADR (D27, D28, D37) | ~250 | Yes — column present, unpopulated |
| 4 | Heartbeat endpoint, beat, derivation, grace (D30, D33, D34) | ~360 | Yes |

~1,110 lines total; every slice is under the 500-line review budget. Rollback is the
proposal's, in reverse chain order — **slice 1 is reverted last**, because reverting it while
slice 2's start-writes are live resurrects the silent-drop bug.

## Open Questions

None blocks `sdd-tasks`.

- [ ] `Acknowledgement.status` says `"duplicate"` for a finish-write that applied real data
      (D26). A third value is wire-visible and no scenario asks for it; revisit with the read API.
- [ ] `test_case.last_seen_at`'s `MAX` compares timestamps written by `datetime.isoformat()`,
      which is variable-width when microseconds are zero (D27). Pre-existing, out of scope,
      recorded so it is not rediscovered as new.
- [ ] The beat interval is declared on both sides of the HTTP boundary with no test
      reconciling them (D34). A divergence changes the grace multiple, never correctness.
- [ ] `app.state.grace_period` ships with no reader (D34). Intentional seam, not dead code.
