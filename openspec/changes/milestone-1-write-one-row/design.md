# Design: Milestone 1 — Write one row

**Change:** `milestone-1-write-one-row` · **Phase:** 1
**Authoritative inputs:** `proposal.md` (accepted), `specs/requirements.md` (the sixteen
requirements verbatim, mirrored from Notion 2026-08-15), ADR-3, ADR-4, ADR-5, ADR-6, ADR-9,
`docs/architecture.md`, `CLAUDE.md`.
**Not inputs:** the previous revision of this file (written for a plugin that opened SQLite
directly — superseded by ADR-9), ADR-7 (**Deprecated**), `exploration.md`, the Engram
observation `sdd/milestone-1-write-one-row/proposal` id 8 (marked superseded in place).

> **Rewritten 2026-08-15.** The previous design assumed the plugin owned the write. ADR-9
> replaced that: the plugin reports over HTTP and the server performs every write. The
> **column manifest** and the **storage port** are carried forward with adjustments; the
> plugin design, the failure paths, the concurrency handling and the database location are
> replaced outright.
>
> This document exceeds the 800-word design budget on purpose. The RQ-29 column manifest is
> a deliverable verified by **Inspection**, not a by-product, so it is reproduced in full
> rather than summarised.

## Technical Approach

Two distributions from one repository (ADR-4). `pytest-vantage` assembles one JSON session
report and POSTs it once, over `urllib`, to `POST /api/v1/runs`. `vantage` validates it at
the boundary with Pydantic v2, converts it to a stdlib `Execution` dataclass, and writes it
through the `ExecutionStore` port (ADR-3) into SQLite (ADR-6) whose schema was created whole
at first use (ADR-5).

```
pytest + pytest_vantage
    │  one request per session, at sessionfinish
    ▼
POST /api/v1/runs ──► vantage.service   Pydantic v2 validation, rejection shaping
                            │           (the only package RQ-24 does not constrain)
                            ▼
                      vantage.core      Execution, Identity, ExecutionStore Protocol,
                            │           server config resolution — stdlib only (RQ-26)
                            ▼
                      vantage.storage   SqliteExecutionStore │ InMemoryExecutionStore
                            │           one process-wide write lock, one transaction
                            ▼
                          SQLite
```

Four facts drive everything below.

1. **The server sees a session exactly once, after it finished.** There is no
   start-then-update pair any more, so the port collapses to a single write.
2. **Two clocks now exist** — the client's and the server's — and they can disagree.
3. **Idempotency is settled by identity**, because the identifier is already client-generated
   with `uuid4`.
4. **A hang is worse than a failure.** Every network operation carries an explicit bound.

---

## Architecture Decisions

### D1 — The session report is an envelope with one named `run` section

**Choice.** The body is a JSON object whose members are *sections*, not run fields:

```json
{
  "run": {
    "id": "3f2a…32 lowercase hex…",
    "started_at": "2026-08-15T09:14:02.481930+00:00",
    "finished_at": "2026-08-15T09:14:47.002118+00:00",
    "exit_status": 0,
    "interrupted": false,
    "interrupt_reason": null
  }
}
```

Milestone 2 adds `"results": [...]`, Milestone 3 adds `"environment": {...}` and
`"vcs": {...}` — as **sibling sections**, never as new top-level scalars.

| Option | Trade-off | Verdict |
| --- | --- | --- |
| Namespaced sections (`{"run": {…}}`) | one indirection today; run fields stay a closed set while the envelope stays open | **Chosen** |
| Flat body (`{"id":…, "started_at":…, "results":[…]}`) | shortest now; but then "unknown top-level key" cannot distinguish an unrecognised *run field* from an unrecognised *section*, so the forward-compatibility rule below becomes undecidable | Rejected |
| A `report_version` integer beside `/api/v1` | records which payload revision wrote a row | Rejected — two version numbers for one contract. The path version is the only version; `run.vantage_version` (RQ-11, M3) already records the writer more precisely |

**Forward compatibility is asymmetric and deliberate.** Unknown **sections** are ignored
(`extra="ignore"` on the envelope) so a 1.2 plugin can meet a 1.5 server, which ADR-4 calls
the everyday case. Unknown or missing **fields inside `run`** are a rejection
(`extra="forbid"` on the run model), because those are the fields RQ-42 exists to police.
Silently dropping data is not free, so the acknowledgement lists what it ignored (D3).

**Timestamps** are ISO-8601 UTC text with an explicit `+00:00` offset and microsecond
precision — fixed width, so lexicographic order is chronological order. Produced with
`datetime.now(timezone.utc)`; **never `datetime.UTC`**, which is 3.11+ and the floor is 3.10.

**`finished_at` is `null` iff the session did not end in an orderly way.** The plugin writes
it when `exitstatus not in {2, 3}` (`INTERRUPTED`, `INTERNAL_ERROR`). `interrupted` and
`exit_status` are always sent. That is RQ-31 criterion 2, and note it is now a *plugin*
decision, not a writer that skipped an UPDATE.

### D2 — Reporting runs on the xdist controller only

**Choice.** `pytest_configure` returns without registering the recorder when
`hasattr(config, "workerinput")`.

**Rationale.** Under xdist every worker is a full pytest session with its own
`pytest_sessionstart`/`pytest_sessionfinish`. Unguarded, `-n 4` would produce **five** run
rows for one invocation and break RQ-1's "exactly one run entry" — while RQ-27 requires the
suite to pass *with* xdist, so the two requirements collide unless this guard exists. The
predicate is the same worker-input attribute CLAUDE.md names for RQ-12; applying it to the
run row now means Milestone 3's result de-duplication reuses it rather than inventing it.
The guard also stops the preflight probe (D6) firing once per worker.

### D3 — `POST /api/v1/runs`, idempotent by primary key

| Case | Status | Body |
| --- | --- | --- |
| First submission of this `run.id` | `201 Created` | `{"run_id": "…", "status": "created", "ignored": []}` |
| Replay of a `run.id` already stored | `200 OK` | `{"run_id": "…", "status": "duplicate", "ignored": []}` |
| Unversioned path (`/runs`, `/api/runs`) | `404 Not Found` | — |

**Idempotency is settled by identity, in the INSERT.** `run.id` is the primary key and the
write is `INSERT … ON CONFLICT(id) DO NOTHING`; `record_execution` returns whether a row was
created, and that boolean alone decides 201 vs 200. No `SELECT` precedes it, so there is no
check-then-act race between two concurrent replays.

| Option | Trade-off | Verdict |
| --- | --- | --- |
| Primary-key conflict | the client already generates a `uuid4`, so the identity *is* the idempotency key | **Chosen** |
| An `Idempotency-Key` header + a dedupe table | works for bodies without a natural key | Rejected — a second key to keep in sync with the first, and a table nothing else needs |
| `409 Conflict` on replay | loud | Rejected — an ordinary retry after a timeout is not an error, and the plugin would have to special-case a status meaning "you already succeeded" |

**A replay whose body differs is not merged and not an error: the first write wins and the
response is `duplicate`.** Detecting the difference would need a stored payload digest, which
is a column no requirement asks for. Last-write-wins was rejected outright — it would let a
retry overwrite a committed run.

**No `Location` header.** It would point at `/api/v1/runs/{id}`, which does not exist until
the read API in Milestone 4; a header pointing at a 404 is worse than no header.

**RQ-41 criterion 3 is served by absence, not by a rule.** There is no unversioned route and
no redirect from one — a 301 to the versioned path would be *serving* the request, which the
criterion forbids. The router mounts under `/api/v1` and nothing else exists.

### D4 — The plugin does not retry in Milestone 1

**Choice.** One request, one attempt. Idempotency is an endpoint guarantee proved by endpoint
tests (RQ-41.2), not by plugin behaviour.

**Rationale.** A retry after a timeout is precisely the case where the server is slow, so it
makes the user wait twice at the end of every suite — and RQ-21 criterion 4 bounds the whole
thing at *the configured timeout plus five seconds*, which leaves no room for a second full
timeout. A retry stays cheap to add later exactly because the identifier is client-generated
and the endpoint is already idempotent.

### D5 — Validation is Pydantic v2 in `vantage.service`, and truncation is caught before the schema

**Where.** `packages/vantage/src/vantage/service/schemas.py`. Pydantic v2 is the project's
standard for system boundaries (CLAUDE.md) and `vantage` is the one distribution RQ-24 does
not constrain. The validated model is converted to the core's `Execution` dataclass **before**
the port is touched, so no Pydantic type crosses into `vantage.core` (RQ-26, RQ-30.2).

**Distinguishing a truncated body from a well-formed one** (RQ-42.3) works because the two
failures happen at two different layers, and the transport layer is consulted first:

| Failure | Detected by | Status | Body |
| --- | --- | --- | --- |
| Fewer bytes than `Content-Length`, peer closed | ASGI body read raises `ClientDisconnect` | `400` | `{"error":"incomplete_body", …}` |
| Complete bytes, cut mid-JSON | JSON parse | `400` | `{"error":"invalid_json", …}` |
| Body over the cap | length check before the read completes | `413` | `{"error":"payload_too_large", …}` |
| Wrong or absent `Content-Type` | media-type check | `415` | `{"error":"unsupported_media_type", …}` |
| Valid JSON, missing/extra/ill-typed `run` field | Pydantic `ValidationError` | `422` | `{"error":"invalid_report", "fields":[…]}` |

`400` says *I could not read what you sent*; `422` says *I read it and it is wrong*. The
distinction RQ-42.3 asks for is therefore visible at the protocol level, not only in the body.

**Both forms of truncation are tested**, because they are genuinely different: a raw socket
that promises 500 bytes, sends 200 and closes (→ `incomplete_body`), and a body whose
`Content-Length` matches bytes that stop mid-object (→ `invalid_json`).

**The provable half of RQ-42.3 is "stores nothing".** When the peer has already vanished the
rejection response may not be deliverable at all; the assertion of record is that the `run`
table stays empty.

**The default FastAPI 422 handler is replaced** (`service/errors.py`). Criterion 4 forbids
internal identifiers and tracebacks, and the stock handler echoes the submitted `input` back
and emits Pydantic's internal `type` strings. The Vantage shape is
`{"error": <code>, "detail": <sentence>, "fields": ["run.started_at", …]}` — dotted field
paths only, never the input, never an exception class, never a traceback. One handler covers
every rejection so no route can answer in a different shape.

**Nothing is written before the whole body is validated.** There is no streaming parse and no
partial-parse path, which is what makes RQ-3 achievable rather than aspirational.

### D6 — The plugin's three failure paths

| | RQ-37 · cannot reach it | RQ-21 · error while reporting | RQ-21.4 · accepts and never answers |
| --- | --- | --- | --- |
| Where | `plugin.pytest_configure`, a **preflight TCP probe** | `pytest_vantage/boundary.py`, a decorator on every `Recorder` hook | the `timeout=` on the report socket |
| Trigger | `ConnectionRefusedError` (nothing listening), `socket.gaierror` (host does not resolve) | any `Exception` from the reporting path | `socket.timeout` / `URLError` |
| Response | warn **naming the address**, do not register the recorder, return | warn once, latch `_disabled`, return `None` | warn once, abandon the report |
| Session then | runs with no recorder registered — zero further overhead | runs with a no-op recorder | finishes normally |
| Exit status | untouched | untouched — `session.exitstatus` is never assigned | untouched |

**The preflight is a TCP connect, not an HTTP request.**

| Option | Trade-off | Verdict |
| --- | --- | --- |
| `socket.create_connection((host, port), timeout)` then close | answers exactly RQ-37.1 and RQ-37.2, assumes no protocol and no route | **Chosen** |
| `GET /api/v1/health` | also proves the app is up | Rejected — adds a second endpoint to Milestone 1, and an older server that lacks it would look *unreachable*, which is the version skew ADR-4 warns about |
| No preflight; discover at report time | one fewer connection | Rejected — RQ-37 wants the warning before the suite scrolls past it, and a whole suite would run believing it was recorded |

The probe sends no bytes and runs only after activation, so RQ-2 is untouched.

**RQ-37 criterion 3 is mechanically RQ-21's path.** A server that dies *after* the preflight
passed fails at report time; the boundary catches it and the warning names the address. One
`_warn(config, message)` helper serves both paths, emitting a dedicated
`VantageWarning(UserWarning)` — not a `pytest.PytestWarning`, so `-W error::pytest.PytestWarning`
does not capture it — and falling back to the terminal reporter, then `sys.stderr`, if
`filterwarnings = ["error"]` turns warning into raising. The boundary catches `Exception`,
never `BaseException`, so `KeyboardInterrupt` and `SystemExit` still propagate, and it
**latches on first failure**, which is what makes RQ-37.4 (200 tests, exactly one warning)
and RQ-21.5 true.

**Three bounded budgets.**

| Budget | Default | Applies to |
| --- | --- | --- |
| `connect_timeout` | `min(2.0, report_timeout)` | the preflight probe |
| `report_timeout` | `10.0 s`, settable | every socket operation of the POST |
| attempts | `1` (D4) | the whole report |

`urlopen(timeout=t)` bounds each individual socket operation, so "accepts the connection and
never responds" is a read that receives nothing and trips at `t`. Worst-case exposure is
therefore `report_timeout`, inside RQ-21.4's *timeout plus five seconds*.

**Accepted limitation, stated rather than hidden:** a server that trickles one byte just
under the timeout can hold the connection longer than `t`. A wall-clock deadline would need
a second thread (Python cannot kill one, so it would go on holding the socket) or
`signal.alarm` (main-thread and POSIX only, and it would collide with pytest's own signal
handling). Neither is worth it for a case RQ-21.4 does not name.

### D7 — Batching: one request, at `pytest_sessionfinish`

RQ-25's criterion is that the request count is independent of the test count. Milestone 1
sends **exactly one request per recorded session**, assembled in memory and sent from
`pytest_sessionfinish`. Milestone 1 carries no per-test data, so this is trivially true now;
what matters is that the *shape* is the one Milestone 2's results must fit.

**A session killed before it sends.**

| Ending | What happens | Requirement |
| --- | --- | --- |
| Orderly | report sent with `finished_at` set | RQ-31.1 |
| Ctrl-C (SIGINT) | pytest's `wrap_session` calls `pytest_sessionfinish` from a `finally` with `ExitCode.INTERRUPTED`, so the report **is** sent, with `finished_at: null`, `interrupted: true`, `exit_status: 2` | RQ-31.2 |
| SIGKILL, power loss | **nothing is sent; the run is lost entirely** | — |

The last row is a real regression against the superseded file-writing design, which would at
least have left a started-but-unfinished row. ADR-9's consequences already name it ("the
suite ran, the server was down, it is gone"). No stated RQ-1 criterion covers a SIGKILLed
session — all four describe invocations that reach an ending — so this is accepted, recorded,
and listed under *Open Questions*.

**Rejected: a start notification plus a finish update** (`POST` at sessionstart, `PATCH
/api/v1/runs/{id}` at finish). It would restore the killed-session row and is what Phase 3's
live monitoring needs — and it would still satisfy RQ-25, since two is also independent of
the test count. Rejected here because it doubles the failure surface, adds a second endpoint,
and puts a network round trip *before the first test*, which is exactly where a user notices
latency. The `POST`/`PATCH` split is named now as the seam Phase 3 reopens.

### D8 — Concurrency is a process-wide write lock over one connection (RQ-38.1)

ADR-9 moved WAL, `BEGIN IMMEDIATE` and busy timeouts behind the server's storage port. They
land in `vantage/storage/connection.py` and `sqlite_store.py`.

| Mechanism | Setting | Why |
| --- | --- | --- |
| Handler style | the route is `def`, not `async def` | Starlette runs it in the threadpool, so a blocking write never stalls the event loop |
| Connection | one per process, `check_same_thread=False` | a connection per request reopens the file and loses the warm WAL state |
| Serialisation | one `threading.Lock`, held across the whole transaction | in-process contention resolves without `SQLITE_BUSY` retry churn, and the transaction boundary is one obvious block |
| Transactions | `isolation_level=None` + explicit `BEGIN IMMEDIATE` … `COMMIT` | takes the write lock up front; a deferred transaction that upgrades mid-way is the classic two-writer deadlock |
| Lock wait | `sqlite3.connect(path, timeout=5.0)` | a *second process* waits rather than failing instantly |
| Journal | `PRAGMA journal_mode=WAL` at creation | one writer, many readers; the Milestone 4 read API does not block ingestion |
| Durability | `PRAGMA synchronous=FULL` | RQ-3 asks for all-or-nothing; one fsync per *session* is a cost nothing notices |
| Integrity | `PRAGMA foreign_keys=ON` on every connection | the satellite tables mean it from Milestone 2 |
| Identity | client `uuid4().hex` | two sessions starting in the same second cannot collide — RQ-38.1 |
| Schema race | every statement `IF NOT EXISTS`, the whole `schema.sql` inside one `BEGIN IMMEDIATE` | two processes creating the same fresh database get an atomic schema, not a partial one |

**Both locks are needed and neither is redundant.** The Python lock controls contention
inside the process; SQLite's locking is the only thing that still holds if an operator runs
two servers against one file, which no in-process lock can influence.

**WAL fallback.** WAL needs shared memory and fails on some network filesystems. If the
pragma raises, or reads back as anything but `wal`, the server continues in `delete` mode.
That is a degradation, not a failure, and is logged once at startup.

**`uvicorn --workers` defaults to 1 with the SQLite adapter**, and the CLI warns if it is
raised. Multiple worker processes on one SQLite file are *survivable* under the pragmas above
but not recommended; the PostgreSQL adapter (Phase 3, ADR-4's `[postgres]` extra) is what
removes the constraint.

**Rejected:** a single-writer asyncio queue (a background task, an unbounded queue and a
shutdown-drain problem, for one INSERT), and a connection per request.

### D9 — Owner-only permissions live in the connection path, and the file is created before `sqlite3` sees it

`vantage/storage/connection.py::open_database(path)`, POSIX-guarded (`os.name == "posix"`) —
RQ-40's three criteria all say "Given a POSIX machine".

1. `os.makedirs(parent, mode=0o700, exist_ok=True)` **followed by an explicit
   `os.chmod(parent, 0o700)`** — `makedirs`' `mode` is masked by umask, and 022 would leave
   0755.
2. **Create the database file first**, race-free:
   `os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)`, then close the fd, then
   `sqlite3.connect`. `FileExistsError` means it already exists — skip to step 4.
   **This is the load-bearing detail.** `sqlite3.connect` creates the file itself at 0644
   under umask 022, and a `chmod` *after* connect leaves a window in which another user can
   open it. Creating it first with an explicit mode closes the window, which is why
   permissions belong in the connection path and not in a caller.
3. Create `<store_root>/artifacts/` at 0700 by the same explicit-chmod route. Nothing writes
   artefacts until Phase 2, but RQ-40.2 is in scope now and Phase 2 then inherits a correct
   directory instead of creating one at the wrong mode.
4. On an **existing** file, `os.stat`; if `mode & 0o077`, log a warning naming the mode and
   **continue** — RQ-40.3 requires the run to be recorded. Rejected: silently `chmod`-ing a
   file an operator deliberately widened (for a backup group, say) is a surprising side
   effect on data the server does not own.
5. **The WAL sidecars are the trap.** `-wal` and `-shm` carry uncommitted frames and are as
   readable as the database. SQLite propagates the main file's mode to them, but the design
   **verifies that by test rather than trusting it**: a test asserts all three are 0600 under
   umask 022. If the assertion fails on some platform, the fallback is an explicit `chmod`
   of both sidecars after the first write.

### D10 — Architecture test: an `ast` walk that also rejects sibling subpackages

`packages/vantage/tests/test_architecture.py`, standard library only.

A top-level import name is allowed iff it is in `sys.stdlib_module_names` (3.10+, matching
the floor exactly) or resolves under `vantage.core`.

**The new hazard ADR-4 created.** `vantage.core` is now a *subpackage* of the same
distribution as `vantage.storage` and `vantage.service`, so nothing installs them apart and
`from vantage.storage import …` inside the core would be an ordinary intra-package import.
The walk therefore rejects `vantage.storage` and `vantage.service` by name, and **resolves
every relative `ImportFrom` against the module's own package path** before judging it — a
`from ..storage import X` inside `vantage.core` has `level=2` and lands outside the core, so
`level > 0` is not sufficient permission. The superseded design's version did not cover this,
because the packages were separate distributions then.

**No `TYPE_CHECKING` exemption.** An import inside `if TYPE_CHECKING:` is still a static
import of a third-party name, and the core has nothing outside stdlib worth typing against.

**Non-vacuity guard** (RQ-26.2): `test_core_package_is_not_empty` asserts the walk visited at
least three modules, saw at least one import statement, and that `ports/storage.py` and
`domain/execution.py` were among the files opened — so it cannot pass having examined
nothing, and cannot pass having examined only `__init__.py`.

**One shared walker, two allow-lists.** `packages/vantage/tests/importwalk.py` is imported by
`test_architecture.py` (core: stdlib only, RQ-26.1) and by
`packages/pytest-vantage/tests/test_plugin_imports.py` (plugin: stdlib **or `pytest`**,
RQ-24.2), via the root `pythonpath = ["packages/vantage/tests"]` that already exists. The
coupling is acceptable for the same reason ADR-4 rejected two repositories: the cross-boundary
tests need one home. It never ships — `tests/` is in no wheel, and a walker inside
`vantage_core` would import `pytest` and fail its own rule.

The walk is one of three non-redundant guards (CLAUDE.md): `deptry` catches an undeclared
third-party import in source, and the clean-environment install check catches what actually
reaches a user's environment.

### D11 — Server configuration: `$XDG_DATA_HOME/vantage/vantage.db`, bound to loopback

ADR-7 answered the *plugin's* question and is deprecated. This is the server's, and the only
thing that survives from ADR-7 is its closing observation: a run recorded three months ago
cannot be regenerated.

**Database location.**

| Option | Trade-off | Verdict |
| --- | --- | --- |
| `$XDG_DATA_HOME/vantage/vantage.db`, default `~/.local/share/vantage/vantage.db` | user data that no convention sweeps | **Chosen** |
| `$XDG_STATE_HOME` (`~/.local/state/…`) | the spec's own examples are logs and history, which recorded runs resemble | Rejected — the spec frames STATE as *not important enough* for DATA, and a three-month history is |
| `~/.cache/vantage/…` | survives `rm -rf` of a checkout | Rejected — documented as safe to delete, which is ADR-7's surviving argument |
| `./vantage.db` (cwd) | trivial | Rejected — a long-lived process is usually started by a supervisor whose cwd is `/` |

**Precedence:** `--database PATH` > `VANTAGE_DATABASE` > the XDG default. **Environment
configuration is allowed here although RQ-2 forbids it on the plugin**, and the difference is
the threat, not the mechanism: RQ-2 exists to stop a committed value silently enabling
recording in *someone else's* project, whereas the server is installed and started
deliberately by whoever runs it, and env-var configuration is how a container is configured.
No config file in Milestone 1 — legitimate here, but unneeded scope.

**One server, one database, every project mixed together.** There is no project column, and
none is invented: `run.root_dir` (RQ-11, Milestone 3) is the seam that will separate them,
and no Phase 1 read path needs the separation because there is no read path.

**Bind address: `127.0.0.1:8765` by default.** Authentication of the ingestion endpoint is
deferred to Phase 4 (proposal, *Out of scope*), so an unauthenticated **write** endpoint must
not default to `0.0.0.0`. Loopback makes ADR-9's CI-sidecar case work out of the box, and
exposing the server is then a deliberate `--host`. **The server logs a warning at startup
when it binds a non-loopback address**, naming the missing authentication. `8765` is a
memorable unregistered high port; the plugin's default address is the same, so `--vantage`
alone means something.

**Resolution is pure and lives in `vantage/core/config/resolution.py`** — optional strings and
a `Path` in, a frozen `ServerConfig` out, no framework type in any signature, so RQ-26 holds
and the precedence table is testable with plain function calls.

**Two resolutions, two packages, no sharing.** The plugin resolves *the server address it
reports to*; the core resolves *the server's own* database path and bind address. They sit on
opposite sides of an HTTP boundary and cannot share a line of code — the plugin may import
nothing but stdlib and pytest (ADR-9). Its own resolution lives in
`pytest_vantage/config.py`, stdlib only.

> **This clears the reversal-cost bar and should become `docs/adr/0010-store-the-server-database-in-the-user-data-directory.md`** — changing the default once users hold data means orphaned databases or the migration ADR-5 forbids, which is the same argument that made ADR-7 an ADR. The bind default does **not** clear the bar (one line to revert) and stays a design note.

### D12 — FastAPI on uvicorn

| Option | Trade-off | Verdict |
| --- | --- | --- |
| FastAPI + uvicorn | the request model *is* Pydantic v2, which is already the project's boundary standard; the `/api/v1` prefix, the 422 path and OpenAPI come free; ASGI is what Phase 3 streaming needs | **Chosen** |
| Starlette alone | one fewer layer | Rejected — it would mean hand-wiring Pydantic for exactly the surface FastAPI already provides |
| Flask | familiar | Rejected — WSGI, so Phase 3's live monitoring would need a second server |
| stdlib `http.server` | zero dependencies | Rejected — hand-rolled routing, no production concurrency story, and RQ-38 asks for concurrent sessions. `vantage` is the package RQ-24 does not constrain; paying nothing here buys nothing |

`vantage` gains `fastapi` and `uvicorn` as runtime dependencies. `pytest-vantage` gains
nothing, and cannot: it declares `pytest` alone.

> **ADR-worthy: `docs/adr/0011-serve-the-ingestion-api-with-fastapi-on-uvicorn.md`.** ADR-9
> already flagged that the web framework arrives three milestones early; by Milestone 5 the
> read API and the interface sit on it, and reversing then is well past a sprint.

### D13 — Timestamps stored as ISO-8601 UTC text

Carried forward unchanged. `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`, fixed width, so lexicographic
ordering is chronological ordering. Rejected: epoch floats (unreadable when a human opens the
file in `sqlite3`, and RQ-29's verification method is inspection *by a human*) and naive local
time (two machines in one database become unorderable — and under ADR-9 one database now
routinely holds runs from many machines, which makes this stronger than it was).

### D14 — The `req` marker is declared exactly once

Carried forward and **already landed**: `[tool.pytest.ini_options]` exists only in the
workspace-root `pyproject.toml`, and `tests/test_workspace_pytest_ini.py` guards it with a
text scan (not a TOML parse — `tomllib` is 3.11+ and `tomli` is third-party). Its
`EXPECTED_PACKAGE_COUNT = 2` already reflects ADR-4.

**The invariant is `grep`, not `-m`.** CLAUDE.md's rule is that `grep -r "RQ-12"` reaches the
thing that proves RQ-12. `pytest -m req` selects all of them; `-m 'req("RQ-12")'` is
documented as convenience, not as the guarantee.

---

## Data Flow

```
pytest_addoption   --vantage · --vantage-server=URL · --vantage-timeout=S
       │           ini: vantage_server, vantage_timeout · env: VANTAGE_SERVER
       ▼
pytest_configure ───────────────────────────────────────── RQ-37 boundary ─────┐
       │                                                                        │
  hasattr(config, "workerinput") ──► return          (D2: xdist workers)        │
       │                                                                        │
  config.is_activated(flag, cli_url) ──► False ──► return; no probe, no socket  │
       │ True                                                                   │
  config.resolve_server(cli, env, ini) ──► ServerAddress                        │
       │                                                                        │
  socket.create_connection(addr, connect_timeout).close()                       │
       │                                            refused │ gaierror ─────────┤
  pluginmanager.register(Recorder(address, timeout))                            │
       │                                                    └─► _warn(address), no recorder
       ▼
(collection, tests …)
       ▼
Recorder.pytest_sessionfinish(exitstatus) ──► build report ──► POST /api/v1/runs
       │                                              ▲ RQ-21 boundary on every hook
       │                                              │ (also catches RQ-37 criterion 3)
       ▼
  service: media type ▸ size cap ▸ body read ▸ JSON ▸ Pydantic ▸ Execution
       │                    │          │         │        │
       │                  413        400       400      422        ── nothing written ──
       ▼
  store.record_execution(execution, received_at=now)
       │   lock ▸ BEGIN IMMEDIATE ▸ INSERT … ON CONFLICT(id) DO NOTHING ▸ COMMIT
       ▼
  201 {"run_id": …, "status": "created"}   │   200 {"…","status":"duplicate"}
```

Failure sequence, RQ-37 criterion 1 (nothing listening):

```
configure ──► resolve ──► create_connection ✗ ConnectionRefusedError
   └─► _warn("vantage: cannot reach http://127.0.0.1:8765: connection refused")
   └─► return ──► session runs, exit status = whatever the suite says
```

Failure sequence, RQ-21 criterion 2 (report raises, failing suite):

```
sessionfinish ──► transport.send ✗ RuntimeError
   └─► boundary: warn once, self._disabled = True, return None
   └─► pytest computes exit status from the suite ──► 1
```

---

## Schema Manifest (RQ-29 deliverable)

Ships as `docs/schema-manifest.md`; `packages/vantage/src/vantage/storage/schema.sql` is its
implementation. **Inspection** compares a freshly created database against this table. A
companion test (`test_schema_manifest.py`) mechanises the same comparison via
`PRAGMA table_info` so the manifest cannot rot silently — the test is a rot-detector, not the
verification of record.

Conventions:

- Timestamps are ISO-8601 UTC `TEXT` (D13); booleans are `INTEGER` 0/1; JSON-shaped values are
  `TEXT` written with stdlib `json`.
- **†** marks a column whose content is unbounded by nature. Every † column carries a sibling
  `<name>_truncated` `INTEGER NOT NULL DEFAULT 0`; they are not listed individually. The cap
  is a fixed 64 KiB of UTF-8 with an in-band marker counted inside the budget; the sibling
  flag is the machine-authoritative answer, unforgeable because a test's output cannot write
  a column. Milestone 1 populates no such field, so the helper lands with Milestone 2 — what
  lands now is the schema that makes it expressible.
- A driver of **`—`** means *no requirement drives this column*. It is a non-derivable session
  fact retained under ADR-5, and there are exactly **five** of them in the whole schema,
  listed together below so an inspection can judge them rather than discover them. Derivable
  values are not stored: run-level outcome counters, run duration and an `expected failure`
  boolean were all dropped because `result` already answers them.

The five `—` columns are **`run.received_at`**, `run.collected_count`,
`test_case.first_seen_at`, `result.started_at` and `result.finished_at`. Collected count is
not derivable from `result` (a `-x` run collects a hundred and records three); per-result
timestamps are the only way to reconstruct a timeline once xdist runs tests in parallel;
`first_seen_at` cannot be recovered once the run that first saw it is pruned.

**`run.received_at` is the one column ADR-9 added**, and it exists because ADR-9 created a
second clock. `started_at` and `finished_at` are the *client's* facts and RQ-31 requires them
to stay so; but the client's clock is untrusted and unverifiable, so ordering a shared
database by it is wrong the moment one laptop is skewed, and the moment of receipt cannot be
reconstructed later. It is exactly the kind of non-derivable fact ADR-5 says to retain.

**Milestone 1 creates all ten tables and populates only the marked `run` columns.**

### `meta`

| Column | Type | Notes |
| --- | --- | --- |
| `key` | TEXT PK | rows: `schema_version`, `created_at`, `created_by` |
| `value` | TEXT NOT NULL | |

ADR-5 forbids a migration *framework*, not a version stamp. Without `schema_version` a future
migration cannot identify what it is migrating; with it, a later reader can refuse a database
it does not understand instead of misreading it. Populated at creation.

### `run`

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | TEXT PK | RQ-1 | **M1** |
| `received_at` | TEXT NOT NULL | — | **M1** |
| `started_at` | TEXT NOT NULL | RQ-31 | **M1** |
| `finished_at` | TEXT NULL | RQ-31 | **M1** |
| `exit_status` | INTEGER NULL | RQ-31 | **M1** |
| `interrupted` | INTEGER NOT NULL DEFAULT 0 | RQ-31 | **M1** |
| `interrupt_reason` † | TEXT NULL | RQ-31 | **M1** |
| `hostname` | TEXT NULL | RQ-11 | M3 |
| `username` | TEXT NULL | RQ-11 | M3 |
| `python_version` | TEXT NULL | RQ-11 | M3 |
| `pytest_version` | TEXT NULL | RQ-11 | M3 |
| `platform` | TEXT NULL | RQ-11 | M3 |
| `command_line` † | TEXT NULL | RQ-11 | M3 |
| `vantage_version` | TEXT NULL | RQ-11 | M3 |
| `root_dir` | TEXT NULL | RQ-11 | M3 |
| `invocation_dir` | TEXT NULL | RQ-11 | M3 |
| `plugins` † | TEXT NULL (JSON) | RQ-11 | M3 |
| `xdist_enabled` | INTEGER NULL | RQ-12 | M3 |
| `xdist_worker_count` | INTEGER NULL | RQ-12 | M3 |
| `vcs_commit` | TEXT NULL | RQ-10 / RQ-23 / RQ-39 | M3 |
| `vcs_branch` | TEXT NULL | RQ-10 / RQ-23 / RQ-39 | M3 |
| `vcs_commit_subject` † | TEXT NULL | RQ-10 / RQ-23 / RQ-39 | M3 |
| `vcs_dirty` | INTEGER NULL | RQ-10 / RQ-23 / RQ-39 | M3 |
| `vcs_root` | TEXT NULL | RQ-10 / RQ-23 / RQ-39 | M3 |
| `collected_count` | INTEGER NULL | — | M2 |

**The five `vcs_*` columns are driven by three requirements, not one.** RQ-10 says what to
write when a repository is present and readable; RQ-23 covers a project directory that is not
a repository at all; RQ-39 covers a repository present but unreadable. The three partition the
space with no gap, and **all three record the run**, which is why this milestone's product
rule (every invocation gets a row) is not weakened by a missing repository.

Two writer obligations for Milestone 3, recorded here because the schema has to permit them:

- Every `vcs_*` column is `NULL`-able and **must be written as SQL `NULL`, never as an empty
  string**. An empty string is indistinguishable from a branch name that failed to read,
  which is the ambiguity RQ-23 and RQ-39 exist to remove.
- `vcs_dirty` is a nullable `INTEGER` with **no default**. Defaulting it to 0 would make a run
  recorded outside a repository claim a clean working tree.

### `test_case` — the catalogue (RQ-13); Python-side type is `Identity`, never `TestIdentity`

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | | M2 |
| `stable_id` | TEXT NOT NULL UNIQUE | Phase 2 · stable identity | Phase 2 |
| `node_id` | TEXT NOT NULL **UNIQUE** | RQ-9, RQ-13 | M2 |
| `file_path` | TEXT NOT NULL | RQ-9 | M2 |
| `class_name` | TEXT NULL | RQ-9 | M2 |
| `function_name` | TEXT NOT NULL | RQ-9 | M2 |
| `param_id` | TEXT NULL | RQ-9 | M2 |
| `first_seen_at` | TEXT NOT NULL | — | M2 |
| `last_seen_at` | TEXT NOT NULL | RQ-13 | M2 |
| `last_seen_run_id` | TEXT NULL → `run(id)` | RQ-13 | M2 |
| `flake_score` | REAL NULL | Phase 2 · flakiness | Phase 2 |
| `flake_window` | INTEGER NULL | Phase 2 · flakiness | Phase 2 |
| `flake_computed_at` | TEXT NULL | Phase 2 · flakiness | Phase 2 |
| `param_signature` | TEXT NULL | Phase 2 · parameter drift | Phase 2 |
| `param_signature_seen_at` | TEXT NULL | Phase 2 · parameter drift | Phase 2 |
| `param_drift_detected_at` | TEXT NULL | Phase 2 · parameter drift | Phase 2 |

**RQ-13 is served by never deleting, not by a retirement column.** "Removed from the codebase"
is read off `last_seen_at` being older than the newest run, so a column recording when the
disappearance was *noticed* adds nothing and would need clearing on the test's return. That
return is RQ-13's second criterion and it is a **schema** obligation: `node_id` is `UNIQUE`,
and the Milestone 2 writer upserts —
`INSERT … ON CONFLICT(node_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, …` — so a
test that disappears and comes back **reuses its entry**. Inserting a second row would split
one test's life in two, which is precisely the failure the criterion names.

`node_id` is the Phase 1 identity key because "a test with the same identifier" means the
pytest node id at this phase. It is the wrong key for a test that moves file, which is what
Phase 2's `stable_id` exists to fix; when it lands it supersedes `node_id` as the conflict
target.

### `result`

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | | M2 |
| `run_id` | TEXT NOT NULL → `run(id)` | RQ-4 | M2 |
| `test_case_id` | INTEGER NOT NULL → `test_case(id)` | RQ-13 | M2 |
| `node_id` | TEXT NOT NULL | RQ-9 | M2 |
| `attempt` | INTEGER NOT NULL DEFAULT 0 | RQ-12 | M2 |
| `outcome` | TEXT NOT NULL, CHECK'd | **RQ-4** | M2 |
| `duration` | REAL NULL | RQ-5 | M2 |
| `started_at` | TEXT NULL | — | M2 |
| `finished_at` | TEXT NULL | — | M2 |
| `setup_outcome` / `call_outcome` / `teardown_outcome` | TEXT NULL | RQ-5 | M2 |
| `setup_duration` / `call_duration` / `teardown_duration` | REAL NULL | RQ-5 | M2 |
| `failure_type` | TEXT NULL | RQ-8 | M2 |
| `failure_message` † | TEXT NULL | RQ-8 | M2 |
| `failure_path` | TEXT NULL | RQ-8 | M2 |
| `failure_lineno` | INTEGER NULL | RQ-8 | M2 |
| `failure_repr` † | TEXT NULL | RQ-8 | M2 |
| `traceback` † | TEXT NULL | RQ-8 | M2 |
| `skip_reason` † | TEXT NULL | RQ-4 | M2 |
| `xfail_reason` † | TEXT NULL | RQ-4 | M2 |
| `worker_id` | TEXT NULL | RQ-12 | M3 |
| `captured_stdout` † | TEXT NULL | Phase 2 | Phase 2 |
| `captured_stderr` † | TEXT NULL | Phase 2 | Phase 2 |

**`outcome` is composite, and that is the whole of RQ-4.**

```sql
CHECK (outcome IN ('passed','failed','error','skipped','xfailed','xpassed'))
```

The mirror case that decides the implementation is a test whose body **passes** and whose
teardown **raises**: its outcome is not `passed`. The call phase has already reported success
by then, which is why an implementation writing the outcome on
`pytest_runtest_logreport(when="call")` gets it wrong. Milestone 2 must hold the three phase
reports and resolve `outcome` at teardown, not stream it.

`UNIQUE(run_id, node_id, attempt)` is the schema-level backstop for RQ-12: under xdist every
result arrives twice, once from the worker and once from the controller. The filter is the
worker-input attribute on the config object (D2, extended to results in Milestone 3); this
constraint makes a de-duplication bug a loud `IntegrityError` instead of a silently doubled
history. `attempt` keeps rerun plugins from colliding with it.

### `result_marker` (RQ-7)

`id` INTEGER PK · `result_id` → `result(id)` · `name` TEXT NOT NULL · `args` † TEXT NULL (JSON) ·
`kwargs` † TEXT NULL (JSON) · `origin` TEXT NOT NULL
CHECK IN (`function`, `class`, `module`, `package`, `session`, `config`). Populated M2.

### `result_parameter` (RQ-6)

`id` INTEGER PK · `result_id` → `result(id)` · `name` TEXT NOT NULL · `position` INTEGER NOT NULL ·
`value_repr` † TEXT NULL · `value_type` TEXT NULL. Populated M2.

### `result_log` (Phase 2 — structured per-test logs, filterable by severity)

`id` INTEGER PK · `result_id` → `result(id)` · `sequence` INTEGER NOT NULL · `phase` TEXT
CHECK IN (`setup`, `call`, `teardown`) · `created_at` TEXT NOT NULL · `level_no` INTEGER NOT NULL ·
`level_name` TEXT NOT NULL · `logger_name` TEXT NULL · `message` † TEXT NOT NULL ·
`path` TEXT NULL · `lineno` INTEGER NULL.

`level_no` is the numeric column because *filterable by severity* means `WHERE level_no >= 30`;
`level_name` alone forces the filter into application code.

### `result_fixture` (Phase 2)

`id` INTEGER PK · `result_id` → `result(id)` · `name` TEXT NOT NULL · `scope` TEXT NULL ·
`position` INTEGER NOT NULL.

### `artifact` (Phase 2 — content-addressed)

`content_hash` TEXT PK · `algorithm` TEXT NOT NULL DEFAULT `'sha256'` · `size_bytes` INTEGER
NOT NULL · `media_type` TEXT NULL · `content` BLOB NULL · `external_path` TEXT NULL ·
`first_stored_at` TEXT NOT NULL. Content-addressing is the point: the same screenshot produced
by two hundred runs is stored once. Its on-disk store is the `artifacts/` directory D9 creates
at 0700 (RQ-40.2).

### `result_artifact` (Phase 2)

`id` INTEGER PK · `result_id` → `result(id)` · `content_hash` → `artifact(content_hash)` ·
`label` TEXT NOT NULL · `phase` TEXT NULL · `created_at` TEXT NOT NULL ·
`UNIQUE(result_id, content_hash, label)`.

### Indexes

`run(started_at)` · **`run(received_at)`** · `result(run_id)` · `result(test_case_id)` ·
**`result(failure_path, failure_lineno)`** · `result(outcome)` · **`test_case(node_id)` UNIQUE** ·
`test_case(last_seen_at)` · `result_log(result_id, sequence)` ·
`result_log(result_id, level_no)` · `result_marker(result_id, name)` ·
`result_parameter(result_id)` · `result_artifact(content_hash)`.

The failure index is not decoration: RQ-8's criterion is that twenty tests failing at one
source line come back as one group, which is a `GROUP BY failure_path, failure_lineno`.
`run(received_at)` is the arrival-order index the Milestone 4 read API needs and ADR-5 says to
create now rather than later.

**Ten tables, thirteen indexes.** `PRAGMA foreign_keys=ON` on every connection.
ADR-5's prose says "twelve indexes"; it is still `Proposed`, and its count and its
`vantage-storage/src/vantage_storage/…` paths are corrected in the same PR that lands the
schema (see *Open Questions*).

---

## Interfaces / Contracts

```python
# packages/vantage/src/vantage/core/domain/execution.py
@dataclass(frozen=True, slots=True)
class Identity:
    """A run identifier. NOT `TestIdentity` — pytest would collect it."""
    value: str            # 32 lowercase hex characters, a uuid4 with no dashes
    def __post_init__(self) -> None: ...   # raises ValueError on anything else

@dataclass(frozen=True, slots=True)
class Execution:
    """One pytest invocation. NOT `TestExecution` — pytest would collect it."""
    identity: Identity
    started_at: datetime          # timezone-aware, UTC
    finished_at: datetime | None  # None iff the session did not end orderly (D1)
    exit_status: int | None
    interrupted: bool
    interrupt_reason: str | None

# packages/vantage/src/vantage/core/ports/storage.py
class ExecutionStore(Protocol):
    def record_execution(self, execution: Execution, *, received_at: datetime) -> bool:
        """Store it. Return True if a row was created, False if the id was already stored."""
    def get_execution(self, execution_id: str) -> Execution | None: ...
    def count_executions(self) -> int: ...
    def close(self) -> None: ...

# packages/vantage/src/vantage/core/config/resolution.py
class ConfigSource(str, Enum):     # NOT StrEnum — that is 3.11+, and the floor is 3.10
    CLI = "cli"
    ENV = "env"
    DEFAULT = "default"

@dataclass(frozen=True, slots=True)
class ServerConfig:
    database_path: Path
    database_source: ConfigSource
    host: str
    port: int

def resolve_server_config(
    *, cli_database: str | None, env_database: str | None,
    cli_host: str | None, cli_port: int | None, home: Path, xdg_data_home: str | None,
) -> ServerConfig: ...
```

Three notes on the port.

- **`received_at` is a parameter, not a field of `Execution`.** `Execution` is the client's
  report; receipt is the server's observation. Separating them is the two-clocks point (D1),
  and it keeps the clock injectable so the adapters are deterministically testable.
- **The boolean return is the whole idempotency mechanism** (D3). It comes from the INSERT's
  own row count, so no `SELECT` precedes it and no check-then-act race exists.
- **`get_execution` and `count_executions` exist because RQ-30.1 demands it.** The same suite
  must pass against both adapters, which is impossible if the assertions reach around the port
  into SQLite — and RQ-1, RQ-31 and RQ-38.1 are all assertions about what is in the run table.
  They are deliberately unfiltered and unpaginated: this is a Milestone 1 verification
  affordance, and RQ-14's read API (Milestone 4) replaces it with a query object rather than
  growing it. Naming it now stops the read API being invented by accident inside the contract
  suite.

Ingestion contract, published as `docs/api/v1-ingestion.md` because ADR-4 makes the API — not
the version numbers — the contract between the two distributions.

```
POST /api/v1/runs            Content-Type: application/json
201 {"run_id": "<hex32>", "status": "created",   "ignored": []}
200 {"run_id": "<hex32>", "status": "duplicate", "ignored": []}
400 {"error": "incomplete_body" | "invalid_json", "detail": "…"}
413 {"error": "payload_too_large", "detail": "…"}
415 {"error": "unsupported_media_type", "detail": "…"}
422 {"error": "invalid_report", "detail": "…", "fields": ["run.started_at"]}
```

---

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `packages/vantage/src/vantage/core/domain/execution.py` | Create | `Execution`, `Identity` |
| `packages/vantage/src/vantage/core/domain/text.py` | Create | `MAX_TEXT_BYTES`, `TRUNCATION_MARKER`; the helper lands with M2 |
| `packages/vantage/src/vantage/core/ports/storage.py` | Create | `ExecutionStore` Protocol (ADR-3) |
| `packages/vantage/src/vantage/core/config/resolution.py` | Create | D11 — pure, stdlib only |
| `packages/vantage/src/vantage/storage/schema.sql` | Create | the manifest above |
| `packages/vantage/src/vantage/storage/connection.py` | Create | D9 permissions, PRAGMAs, WAL fallback, DDL in one transaction |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Create | `SqliteExecutionStore` + the write lock (D8) |
| `packages/vantage/src/vantage/storage/memory.py` | Create | `InMemoryExecutionStore` (RQ-30) |
| `packages/vantage/src/vantage/service/schemas.py` | Create | Pydantic v2 `SessionReport`, `RunReport`, `Acknowledgement` (D1, D5) |
| `packages/vantage/src/vantage/service/errors.py` | Create | D5 rejection shape; replaces FastAPI's 422 handler |
| `packages/vantage/src/vantage/service/routes/runs.py` | Create | `POST /api/v1/runs` (D3) |
| `packages/vantage/src/vantage/service/app.py` | Create | app factory taking an `ExecutionStore`; body cap; `/api/v1` router |
| `packages/vantage/src/vantage/service/cli.py` | Create | `vantage serve`, argparse, non-loopback warning (D11) |
| `packages/vantage/pyproject.toml` | Modify | add `fastapi`, `uvicorn`; `[project.scripts] vantage = "vantage.service.cli:main"` |
| `packages/vantage/tests/importwalk.py` | Create | D10 shared walker |
| `packages/vantage/tests/test_architecture.py` | Create | RQ-26.1 + RQ-26.2 vacuity guard |
| `packages/vantage/tests/test_resolution.py` | Create | D11 precedence table, plain function calls |
| `packages/vantage/tests/vantage_port_contract.py` | Create | `ExecutionStoreContract` — shared, not `test_*`, never shipped |
| `packages/vantage/tests/test_sqlite_store.py`, `test_memory_store.py` | Create | both subclass the contract (RQ-30.1) |
| `packages/vantage/tests/test_schema_manifest.py` | Create | manifest ↔ `PRAGMA table_info` rot-detector |
| `packages/vantage/tests/test_permissions.py` | Create | RQ-40, POSIX-only |
| `packages/vantage/tests/test_ingestion.py` | Create | RQ-41 (three criteria) |
| `packages/vantage/tests/test_rejection.py` | Create | RQ-42 (four criteria), incl. a raw-socket truncated body |
| `packages/vantage/tests/test_concurrency.py` | Create | RQ-38.1 |
| `packages/pytest-vantage/src/pytest_vantage/plugin.py` | Modify | `pytest_addoption` + `pytest_configure` only; xdist guard (D2); preflight |
| `packages/pytest-vantage/src/pytest_vantage/config.py` | Create | pure address/timeout resolution, stdlib only |
| `packages/pytest-vantage/src/pytest_vantage/recorder.py` | Create | `pytest_sessionfinish`, `pytest_report_header` |
| `packages/pytest-vantage/src/pytest_vantage/transport.py` | Create | `urllib` POST, bounded timeout, bounded response read, scheme allow-list |
| `packages/pytest-vantage/src/pytest_vantage/boundary.py` | Create | D6 decorator, `_warn`, `VantageWarning` |
| `packages/pytest-vantage/tests/conftest.py` | Create | `pytest_plugins = ["pytester"]`; a stub HTTP server fixture |
| `packages/pytest-vantage/tests/test_opt_in.py` | Create | RQ-2, differential |
| `packages/pytest-vantage/tests/test_run_report.py` | Create | RQ-1, RQ-31 including SIGINT, end to end against a real server |
| `packages/pytest-vantage/tests/test_failure_paths.py` | Create | RQ-21 (five criteria), RQ-37 (four criteria) |
| `packages/pytest-vantage/tests/test_plugin_imports.py` | Create | RQ-24.2 via the shared walker |
| `docs/schema-manifest.md` | Create | the RQ-29 inspection artifact |
| `docs/api/v1-ingestion.md` | Create | the plugin↔server contract (ADR-4) |
| `docs/adr/0010-store-the-server-database-in-the-user-data-directory.md` | Create | D11; `Proposed` in the PR, `Accepted` on merge |
| `docs/adr/0011-serve-the-ingestion-api-with-fastapi-on-uvicorn.md` | Create | D12; same lifecycle |
| `docs/adr/0005-…md`, `docs/adr/0006-…md` | Modify | correct `vantage-storage/src/vantage_storage/…` paths to the ADR-4 layout; ADR-5's index count 12 → 13. Both are `Proposed`, so still editable |
| `docs/architecture.md` | Modify | add the ingestion contract and the server-configuration section |
| `.pre-commit-config.yaml` | Create | pre-commit and pre-push stages per CLAUDE.md's gate table |
| `.github/workflows/ci.yml` | Create | 3.10–3.13 × {with, without} xdist; offline job (RQ-28); clean-env install (RQ-24); ruff/mypy/deptry/build |
| `.github/workflows/audit.yml` | Create | weekly `pip-audit` |

---

## Testing Strategy

Strict TDD; test command `uv run --extra dev pytest`. Every verifying test carries
`@pytest.mark.req("RQ-nn")`, and the invariant of record is that `grep -r "RQ-nn"` reaches it.

| Layer | What | How |
| --- | --- | --- |
| Unit (core) | server config precedence, XDG default, `Identity` validation | plain calls, no server, no pytest session |
| Unit (core) | RQ-26.1 + RQ-26.2 vacuity | `ast` walk over `vantage/core`, sibling-subpackage rejection (D10) |
| Contract (storage) | port behaviour, incl. replay returning `False` | `ExecutionStoreContract` × {sqlite, in-memory} — RQ-30.1 |
| Inspection (storage) | RQ-29 | `docs/schema-manifest.md` ↔ `PRAGMA table_info`; plus reopening a database and asserting no DDL is issued (RQ-29.2) |
| Unit (storage) | RQ-40 | umask 022 fixture; assert db `0600`, `artifacts/` `0700`, `-wal`/`-shm` `0600`; an existing `0644` db records and warns. POSIX-only |
| API (service) | RQ-41 | `TestClient` over an in-memory store: 201 then 200 on replay, one row; unversioned path 404 |
| API (service) | RQ-42 | missing field → 422 naming `run.started_at`; non-JSON → 400; **raw socket** promising 500 bytes and sending 200 → nothing stored; response carries no `input`, no exception class, no traceback |
| API (service) | RQ-3 criterion 2 | the truncated-body case above, asserting `count_executions() == 0` |
| Integration (service) | RQ-38.1 | two threads POSTing distinct ids into one uvicorn instance; assert two rows, distinct ids |
| Integration (plugin) | RQ-2 | differential — run bare, run `-p no:vantage`, compare tree hashes; and a socket-level assertion that no connection is attempted |
| Integration (plugin) | RQ-1, RQ-31 | `pytester.runpytest_subprocess` against a real server on an ephemeral port: completed, zero-test, failed-collection, SIGINT |
| Integration (plugin) | RQ-21 | transport patched to raise, on a passing suite (exit 0) and a failing one (exit 1); a stub that accepts then closes; a stub that accepts and never answers, asserting exit within `timeout + 5 s`; every hook patched to raise |
| Integration (plugin) | RQ-37 | closed port, unresolvable host, server killed after configure, and a 200-test suite emitting exactly one warning |
| Integration (plugin) | RQ-24.2 | shared `ast` walker over `pytest_vantage`: stdlib or `pytest` only |
| CI (not tests) | RQ-24.1/.3, RQ-27, RQ-28 | clean-env install diff (exactly one distribution added); 3.10–3.13 × xdist matrix; 3.9 install refused; network-disabled job — each block carries its RQ id in a comment |

RQ-2's assertion is **differential**, never absolute: pytest itself writes `.pytest_cache` and
`__pycache__`, so "no file created" is unsatisfiable and only "identical to the same run with
the plugin disabled" is true.

**Criterion splits this milestone must not overclaim.**

| Requirement | Provable now | Carried |
| --- | --- | --- |
| RQ-3 | criterion 2 (truncated in transit) | criteria 1 and 3 count 500 results → Milestone 2 |
| RQ-38 | criterion 1 (two run entries) | criteria 2 and 3 count results → Milestone 2 |
| RQ-28 | the recording half | "the interface is opened" → Milestone 5 |

**Not tested here, by design.** RQ-4's phase resolution, RQ-13's reuse-on-return upsert,
RQ-22's truncation and RQ-23/RQ-39's null version-control fields are writer behaviours for
Milestones 2 and 3. What this milestone owes them is a schema that permits them — a
`UNIQUE(node_id)` to upsert against, `NULL`-able `vcs_*` with no default, `†` flags to write
into, and an `outcome` vocabulary wide enough for `error` and `xpassed`. Their tests belong to
the milestone that populates the columns.

---

## Threat Matrix

The plugin executes inside every user pytest process, its tests spawn subprocesses, and ADR-9
added a network boundary. The matrix applies.

| Boundary | Applicability | Design response | Planned RED tests |
| --- | --- | --- | --- |
| Documentation-like paths | **N/A** — nothing classifies or executes a file; `schema.sql` is read as data by `sqlite3.executescript` from inside the installed package, never from a user-supplied path | — | — |
| Git repository selection | **N/A** — the `vcs_*` columns exist but nothing populates them until M3; no `git` subprocess is invoked | — | — |
| Commit state | **N/A** — same reason | — | — |
| Push state | **N/A** — nothing touches a remote | — | — |
| PR commands | **N/A** — no PR automation in the product | — | — |
| **Process integration** | **Applicable** — the plugin loads into every pytest process on the machine once installed | inert-by-default split; xdist-worker guard (D2); boundary catches `Exception`, never `BaseException`, so `KeyboardInterrupt`/`SystemExit` propagate; `session.exitstatus` never assigned | RQ-2 differential; RQ-21 exit 0 **and** exit 1; every-hook-raises; `-n 4` leaves one run entry |
| **Outbound request target** | **Applicable** — the plugin POSTs to a URL supplied by CLI, env or ini | scheme allow-list: only `http`/`https`; anything else is refused at resolution with a named message, so `urllib`'s `file:`/`ftp:` handlers are never reached | resolution refuses `file:///etc/passwd`, `ftp://…`, and a bare host with no scheme |
| **Untrusted response** | **Applicable** — the plugin reads bytes from a server it does not control, at the end of a user's suite | bounded `resp.read(MAX_RESPONSE_BYTES)` (64 KiB); the acknowledgement is parsed defensively and a malformed one is a warning, not an exception | a stub returning 100 MB; a stub returning non-JSON; a stub returning 500 |
| **Unbounded request body** | **Applicable** — an unauthenticated write endpoint | `MAX_REPORT_BYTES` cap enforced before the body is fully read → `413`; nothing stored | an oversized body → 413 and `count_executions() == 0` |
| **Network exposure** | **Applicable** — no authentication until Phase 4 | default bind `127.0.0.1`; a non-loopback bind logs a warning naming the missing authentication (D11) | `--host 0.0.0.0` emits the warning; the default does not |
| **Path authority** | **Applicable** — the server's database path arrives from CLI or env | resolution is pure and creates nothing; creation is confined to `open_database` and is the only place that mkdirs or chmods | resolution creates no directory; a read-only parent fails loudly at startup, not silently at write time |
| **Concurrent writers** | **Applicable** — one database, many sessions, possibly many processes | process-wide lock + `BEGIN IMMEDIATE` + WAL + 5 s busy timeout + `IF NOT EXISTS` DDL in one transaction (D8) | RQ-38.1 two concurrent reports; two processes creating the same fresh database |
| **Permission exposure** | **Applicable** — the store holds another project's test history | `O_CREAT|O_EXCL` at `0600` **before** `sqlite3.connect`, so the file never exists at a wider mode; `artifacts/` at `0700`; sidecars asserted at `0600` (D9) | umask-022 fixture asserting all four modes; an existing `0644` database records and warns |

---

## Migration / Rollout

No data migration — nothing is published and no user holds data. ADR-5 keeps a migration
framework out of Phase 1; `meta.schema_version` is the stamp that makes the first migration
possible later without being one.

Delivery is a **feature-branch chain** (`chained`, `feature-branch-chain`, 400 authored lines
per PR): each slice targets its predecessor's branch and only the tracker merges to `main`.
Within a slice, commits are work units — a behaviour with its tests, never
`models`/`services`/`tests` layering — and each is a candidate chained PR of its own if the
forecast says so.

| Slice | Work units | Rollback boundary |
| --- | --- | --- |
| **A1** — domain and port | (1) `Execution` + `Identity`; (2) `ExecutionStore` Protocol; (3) `importwalk` + architecture test + vacuity guard (RQ-26); (4) `ExecutionStoreContract` + `InMemoryExecutionStore` (RQ-30) | new files under `packages/vantage` only |
| **A2** — schema and the sqlite adapter | (1) `schema.sql` + `docs/schema-manifest.md` + rot-detector (RQ-29); (2) `connection.py` with permissions (RQ-40); (3) `SqliteExecutionStore` against the same contract; (4) the write lock and concurrency test (RQ-38.1) | drop the storage module; the in-memory adapter still satisfies the contract |
| **B** — ingestion endpoint | (1) Pydantic schemas + app factory; (2) `POST /api/v1/runs` with idempotency (RQ-41); (3) rejection handlers, body cap, truncation paths (RQ-42, RQ-3.2); (4) `docs/api/v1-ingestion.md` + ADR-0011 | delete `service/`; storage and core stand alone |
| **C** — server configuration and CLI | (1) `resolve_server_config` + precedence tests; (2) `vantage serve` + the non-loopback warning; (3) ADR-0010 | delete `cli.py` and `config/resolution.py`; the app factory still takes an injected store |
| **D1** — the inert plugin | (1) options + resolution + scheme allow-list; (2) the xdist guard; (3) RQ-2 differential | revert to the current two-line `plugin.py` |
| **D2** — reporting and failure paths | (1) recorder + payload + transport, RQ-1/RQ-31 end to end; (2) preflight + RQ-37; (3) boundary + `VantageWarning` + RQ-21 including the timeout | new files under `packages/pytest-vantage` |
| **E** — quality gates | (1) pre-commit; (2) CI matrix + offline job + clean-env install (RQ-24, RQ-27, RQ-28); (3) weekly audit | delete two workflow files |

`sdd-tasks` owns the real forecast; this is the natural cut, not an estimate. A2, B and D2 are
the three most likely to need splitting.

**Order matters in one place only:** B's tests run against an injected `InMemoryExecutionStore`,
so B does not depend on A2 or on C. Everything else follows the chain.

---

## Open Questions

- [ ] **A SIGKILLed session leaves no run entry at all** (D7). ADR-9 accepted the data-loss
      shape; no RQ-1 criterion covers it, so nothing is currently violated. If that becomes
      unacceptable, the answer is the start-notification pair D7 rejected, and it should be
      raised as a requirement amendment in Notion rather than smuggled in as an implementation
      detail.
- [ ] **How many plugin versions the server supports** is a commitment ADR-4 implies and
      nobody has written down. The endpoint is `/api/v1` from the first line, so the seam
      exists; the promise does not.
- [ ] **RQ-38's second and third criteria and RQ-3's first and third** should be annotated on
      their Notion rows as carried to Milestone 2, so this milestone is not read as fully
      verifying either.
- [ ] **RQ-23 and RQ-39 are missing from the proposal's requirement tables** yet both
      constrain `run.vcs_*` columns this milestone creates. The proposal should name them
      alongside RQ-10 as the three that partition version-control recording.
- [ ] **`openspec/changes/milestone-1-write-one-row/specs/` predates the ADR-9 replan.** Six
      capability specs were written on 2026-08-14 against the plugin-writes-SQLite model and
      carry no `RQ-41`/`RQ-42` capability at all. They must be regenerated before
      `sdd-tasks`, or tasks will be derived from a superseded contract.
- [ ] **`openspec/config.yaml` is stale in three places** — it says `RQ-1..RQ-39` (there are
      42), four packages (ADR-4 says two), and `vantage-core`/`vantage-storage`/`vantage-pytest`
      as distribution names. Its `rules` are sound; its `context` block misdirects.
- [ ] **`docs/adr/0005` and `0006` name pre-ADR-4 paths**, and ADR-5 says "twelve indexes"
      where the manifest now has thirteen. Both are `Proposed`, so correctable in the PR that
      lands the schema — but it must actually be done, or the RQ-29 inspection has two
      disagreeing sources.
- [ ] **Should a report whose `run.id` replays with a *different* body be detected?** D3 says
      first-write-wins, silently. Detecting it needs a stored payload digest, which is a
      column no requirement asks for. Revisit when results make a replay's content meaningful.
