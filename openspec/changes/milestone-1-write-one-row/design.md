# Design: Milestone 1 — Write one row

**Change:** `milestone-1-write-one-row` · **Phase:** 1
**Authoritative inputs:** `proposal.md` (accepted), the six capability specs under `specs/`,
PROJ-1 and `RQ-1…RQ-39` in Notion, ADR-3…ADR-6.
**Not inputs:** `exploration.md` (superseded), `openspec/config.yaml`'s `context` block (stale —
it says hexagonal, `NFR-1-xx`, SQLAlchemy), the Engram observation that preceded this one.

> This document exceeds the usual design size budget on purpose. The RQ-29 column manifest is a
> deliverable verified by **Inspection**, not a by-product, so it is reproduced here in full rather
> than summarised.

## Technical Approach

Four packages in one uv workspace (ADR-4). `vantage-core` holds two stdlib-only things — the
storage **port** as a `typing.Protocol` (ADR-3) and pure **option resolution** — and nothing else.
`vantage-storage` holds `schema.sql`, the stdlib `sqlite3` writer and an in-memory writer, both
satisfying the port structurally, neither imported by the core. `vantage-pytest` splits an
always-loaded, inert option module from a recorder object registered only on activation.
`vantage-service` is a skeleton.

Two independent decisions drive everything else: **activation is a command-line flag**, and
**failure has two disjoint paths** — one before recording is possible (RQ-37, in `pytest_configure`)
and one during recording (RQ-21, a boundary on every recorder hook).

---

## Architecture Decisions

### D1 — Default database path: `<rootdir>/.vantage/vantage.db`

**Choice.** Confirmed as proposed. The last precedence step resolves to `.vantage/vantage.db`
anchored on pytest's **rootdir**, not `os.getcwd()`. The directory is created only after activation,
and a `.gitignore` containing `*` is written into it at creation with `open(path, "x")`
(`FileExistsError` ignored). The project's own `.gitignore` is never touched.

| Option | Trade-off | Verdict |
| --- | --- | --- |
| `<rootdir>/.vantage/vantage.db` | One history per checkout; visible; user must not commit it | **Chosen** |
| No default at all | Zero surprise, but every invocation needs a path, and `--vantage` alone would then be meaningless | Rejected |
| User cache (`~/.cache/vantage/…`) | Survives `rm -rf` of the project, but merges checkouts and is documented-deletable | Rejected |
| `.pytest_cache/vantage.db` | Free `.gitignore`, but `--cache-clear` destroys user history | Rejected |

**Rationale.** RQ-10 records commit, branch and dirty flag; that record is only meaningful relative
to the checkout it came from. A machine-global location merges two clones, two worktrees and two CI
checkouts of the same repository into one history and needs disambiguation columns to undo the
merge. Worse, `~/.cache` is by contract *safe to delete* — storing longitudinal history in a
location whose documented semantics are "may be swept" contradicts the product's whole claim.
`.pytest_cache` is owned by pytest and cleared by `--cache-clear`.

**RQ-2 is untouched**: resolution is pure and creation is lazy. Nothing on this path executes unless
`--vantage` or `--vantage-db` appeared in `sys.argv`. Anchoring on rootdir rather than cwd means the
same project resolves the same database whether pytest is invoked from the root or a subdirectory.
Relative values from any source resolve against rootdir for the same reason.

Reversal cost is high once users hold data, so this ships as **ADR-7**.

### D2 — Activation and configuration are separate objects

`--vantage` (store_true) activates. `--vantage-db=PATH` implies activation. `VANTAGE_DB` and the
`vantage_db` ini key configure only — neither can turn recording on. The recorder announces the
resolved path **and its source** in `pytest_report_header`, so a `VANTAGE_DB` left in a shell
profile is visible in the first lines of output rather than silently steering writes.

The proposal's configuration table lists a *truncation limit* among the project-config settings.
RQ-22 as amended on 2026-08-14 fixes that limit at 64 KiB and removed the configurable wording, so
**no ini key for it is declared** (D12). The database path remains the only configurable value this
milestone introduces.

### D3 — Timestamps: `finished_at` is written only on an orderly finish

`pytest_sessionfinish` fires even on Ctrl-C (pytest's `wrap_session` calls it from a `finally` with
`ExitCode.INTERRUPTED`). The run-recording spec requires an interrupted session to leave a **null**
end time, so the recorder writes `finished_at` **iff** `exitstatus not in {2, 3}`
(`INTERRUPTED`, `INTERNAL_ERROR`). `exit_status` and `interrupted` are written in every case.

A reader can therefore distinguish three endings: orderly (`finished_at` set), interrupted
(`finished_at` null, `exit_status` = 2), and hard-killed (both null — sessionfinish never ran).
Rejected: writing `finished_at` unconditionally (contradicts the spec) and never writing it outside
an `atexit` handler (an `atexit` hook does not run under SIGKILL either, and adds a second writer).

### D4 — `plugin.py` / `recorder.py` split

pytest fires **any** `pytest_*` hook it finds on a registered plugin, so the always-imported module
must carry no hook that could touch disk.

| Module | Hooks | Loaded |
| --- | --- | --- |
| `vantage_pytest/plugin.py` | `pytest_addoption`, `pytest_configure` — **only these two** | always, via `pytest11` |
| `vantage_pytest/recorder.py` (`Recorder`) | `pytest_sessionstart`, `pytest_sessionfinish`, `pytest_report_header`, `pytest_unconfigure` | only on activation, via `config.pluginmanager.register(recorder, name="vantage-recorder")` |

`pytest_report_header` lives on the recorder, not on `plugin.py`, so the inert module is exactly two
hooks and the "is it inert?" question has a one-line answer.

The run row is INSERTed in **`pytest_sessionstart`**, which fires after `pytest_configure` and
therefore does reach a plugin registered during configure. Because it precedes collection, a
zero-test run, a failed-collection run and a Ctrl-C during collection all leave a row —
exactly the four run-recording scenarios.

### D5 — Two failure paths, in two different places

| | RQ-37 · cannot record at all | RQ-21 · raised while recording |
| --- | --- | --- |
| Lives in | `plugin.pytest_configure`, one `try/except Exception` around resolve → mkdir → connect → PRAGMAs → `schema.sql` → construct recorder | `vantage_pytest/boundary.py`, a decorator on every `Recorder` hook |
| On failure | warn **naming the path**, do **not** register the recorder, return | warn once, set `self._disabled`, return `None` |
| Session then | runs with no recorder registered at all — zero further overhead | runs with a no-op recorder |
| Exit status | untouched | untouched — `session.exitstatus` is never assigned |

Opening the database, applying PRAGMAs and executing the DDL happen **in configure**, so a read-only
directory, a missing directory or a corrupt file is discovered before any test runs. Only faults
after that point are RQ-21's.

Two details that decide the tests: the boundary catches `Exception`, never `BaseException`, so
`KeyboardInterrupt` and `SystemExit` still propagate; and it latches on first failure so Milestone
2's per-test hook cannot emit ten thousand warnings.

**Warning delivery.** Both paths call one `_warn(config, message)` helper emitting a dedicated
`VantageWarning(UserWarning)`. A user running `filterwarnings = ["error"]` would otherwise turn
Vantage's own warning into an exception — harmless inside the RQ-21 boundary, fatal on the RQ-37
path at configure time. `_warn` therefore wraps its own `warnings.warn` and falls back to writing
through the terminal reporter (then `sys.stderr`) if raising is what warning does here.
`VantageWarning` subclasses `UserWarning`, not `pytest.PytestWarning`, so `-W error::pytest.PytestWarning`
does not capture it.

### D6 — Option resolution in `vantage-core`

Pure functions over optional strings and a `pathlib.Path`. No pytest type appears in any signature,
so every precedence rule and the RQ-2 boundary are testable with no pytest session — see
*Interfaces* below.

### D7 — Architecture test: static `ast` walk with a vacuity guard

`packages/vantage-core/tests/test_architecture.py` walks every `*.py` under `vantage_core.__path__`,
`ast.parse`s it, and collects `Import` / `ImportFrom`. A top-level module name is allowed iff it is
in `sys.stdlib_module_names` (3.10+, so it matches the floor exactly) or starts with `vantage_core`.
Relative `ImportFrom` (`level > 0`) resolves to `vantage_core` and is allowed.

**No `TYPE_CHECKING` exemption.** An import inside `if TYPE_CHECKING:` is still a static import of a
third-party name, and the core has nothing outside stdlib worth type-checking against.

`test_core_package_is_not_empty` asserts the walk visited at least three modules, saw at least one
import statement, and that `ports/storage.py` and `config/resolution.py` were among the files
visited — so the walk cannot pass having examined nothing, and cannot pass having examined only
`__init__.py`.

The walker is **not** shared with the other packages. CLAUDE.md names three non-redundant guards and
this is only the first: `deptry` catches an undeclared third-party import in source, and the
clean-environment install check catches what reaches a user's environment. Duplicating the walker
into three test trees, or shipping it inside a distribution, buys nothing those two do not already
cover.

### D8 — Concurrency (RQ-38 criterion 1)

| Mechanism | Setting | Why |
| --- | --- | --- |
| Journal mode | `PRAGMA journal_mode=WAL` at creation | one writer + many readers; the two sessions do not block each other reading |
| Lock wait | `sqlite3.connect(path, timeout=5.0)` | the second writer waits rather than failing instantly |
| Transactions | `isolation_level=None` + explicit `BEGIN IMMEDIATE` | takes the write lock up front; a deferred transaction that upgrades mid-way is the classic two-writer `SQLITE_BUSY` deadlock |
| Identity | `uuid4().hex` | two sessions starting in the same second cannot collide — this is why the id is neither a timestamp nor an autoincrement |
| Schema race | every statement `IF NOT EXISTS`, whole `schema.sql` inside one `BEGIN IMMEDIATE`…`COMMIT` | two processes creating the same fresh database is atomic, not a partial schema |
| Directory race | `mkdir(parents=True, exist_ok=True)`, `.gitignore` via `open(…, "x")` | idempotent |

**WAL fallback.** WAL needs shared memory and fails on some network filesystems. If the pragma
raises, or reads back as anything other than `wal`, the writer continues in `delete` mode. That is a
degradation, not a failure, and emits no warning.

**Degradation into RQ-37.** A `database is locked` that survives the 5 s timeout **during
`pytest_configure`** is RQ-37's path: warn naming the path, do not register, run unrecorded. The
same error later, on the sessionfinish UPDATE, is RQ-21's path: warn once, disable, exit status
untouched. The split is by *when*, not by exception type.

### D9 — The `req` marker is declared exactly once

`[tool.pytest.ini_options]` exists **only** in the workspace-root `pyproject.toml`, carrying
`markers = ["req(id): the requirement this test verifies"]`, `--strict-markers`, and
`pythonpath = ["packages/vantage-core/tests"]`. No package `pyproject.toml` declares
`[tool.pytest.ini_options]`.

This works across four packages because pytest reads exactly one ini file — the one at rootdir.
`uv run pytest packages/vantage-pytest` from the workspace root keeps rootdir at the workspace root,
so the marker is declared for every package. A package that later added its own
`[tool.pytest.ini_options]` would silently become the rootdir config when run from inside it, and
every `@pytest.mark.req` in that package would fail collection. A guard test scans each package
`pyproject.toml` for the literal string `[tool.pytest.ini_options]` and fails if it appears. It is a
text scan, not a TOML parse, because `tomllib` does not exist on 3.10 and `tomli` is third-party.

**The invariant is `grep`, not `-m`.** CLAUDE.md's rule is that `grep -r "RQ-12"` reaches the thing
that proves RQ-12. `pytest -m req` selects all of them; `-m 'req("RQ-12")'` depends on the installed
pytest supporting marker arguments in `-m` expressions and is documented as convenience, not as the
guarantee.

### D10 — Timestamps stored as ISO-8601 UTC text

`YYYY-MM-DDTHH:MM:SS.ffffff+00:00`, fixed width, so lexicographic ordering is chronological
ordering. Rejected: epoch floats (unreadable when a user opens the file in `sqlite3`, and RQ-29's
verification method is *inspection by a human*) and naive local time (two machines in one database
become unorderable).

### D11 — The RQ-30 contract suite is a shared, non-shipped test module

The port contract suite lives at `packages/vantage-core/tests/vantage_port_contract.py`. It is a
plain module — not `test_*.py`, so pytest does not collect it directly — defining
`ExecutionStoreContract`, which exercises behaviour purely through the `ExecutionStore` Protocol and
never names an implementation. `packages/vantage-storage/tests/test_sqlite_store.py` and
`test_memory_store.py` each subclass it and supply a `store` fixture. The root `pythonpath` ini
setting makes the import work from either package.

**Rejected:** shipping the contract inside `vantage_core/` — it imports `pytest`, so the AST walk of
D7 would fail it, and RQ-24 would be breached in the one package that may depend on nothing.
It lives in `tests/`, which the walk never visits and no distribution contains.

`InMemoryExecutionStore` lives in `vantage-storage`, per the proposal's package table — an adapter
inside the core would invert the dependency rule ADR-3 exists to enforce.

### D12 — Truncation: a fixed 64 KiB cap, an in-band marker, and an out-of-band flag

RQ-22 as amended fixes the limit at 64 KiB and requires the marker to sit **within the stored
value**. Its third criterion requires that marker to be distinguishable from text the traceback
itself contained — and no in-band sentinel can be unforgeable, because a traceback may legitimately
print any string, including ours. So the design uses both halves and assigns each a job:

| Half | Where | Job |
| --- | --- | --- |
| In-band marker | appended to the stored value | satisfies "mark **within** the stored value"; tells a human reading the file in `sqlite3` that text is missing |
| `<column>_truncated` INTEGER | sibling column | the machine-authoritative answer; unforgeable because a test's output cannot write a column |

Criterion 3 is therefore satisfied structurally: a 1 KiB traceback that happens to *contain* the
marker string is stored whole, carries `…_truncated = 0`, and is reported untruncated. Criterion 2
falls out of the same rule — under the limit, nothing is appended and the flag stays 0.

Mechanics: the cap is **65 536 bytes of UTF-8**, not characters, measured on the encoded value;
the cut lands on a codepoint boundary so a multi-byte sequence is never split; and the marker is
counted *inside* the budget, so a stored value never exceeds 64 KiB. `MAX_TEXT_BYTES` and the marker
live in `vantage_core` as constants, and one `vantage_storage` writer helper applies them to **every**
text column, so no field can blow the budget by being overlooked.

Only columns whose content is unbounded by nature — free-form text produced by user code or the
environment — carry the sibling flag; they are marked **†** in the manifest. A hostname, a path or an
ISO timestamp is capped and marked in-band like everything else, but gains no queryable flag, because
nothing will ever filter on whether a hostname was truncated.

The 64 KiB figure is recorded in RQ-22's rationale as an unmeasured assumption to revisit. Nothing in
this design depends on the number, only on its being fixed: it is one constant in one module.

**Milestone 1 populates no text field subject to this**, so the helper itself lands with Milestone 2.
What lands now is the schema that makes it expressible.

---

## Data Flow

```
sys.argv ──► plugin.pytest_addoption      (declares --vantage, --vantage-db, ini keys)
                     │
                     ▼
             plugin.pytest_configure ─────────────────── RQ-37 boundary ──────┐
                     │                                                        │
        core.is_activated(flag, cli_db)                                       │
                     │ False ──► return; nothing registered, nothing on disk  │
                     │ True                                                   │
                     ▼                                                        │
        core.resolve_database_path(cli, env, ini, rootdir) ──► DatabaseLocation│
                     │                                                        │
                     ▼                                                        │
        storage.open_database(path)  mkdir · .gitignore · connect ·           │
                     │               PRAGMAs · BEGIN IMMEDIATE schema.sql     │
                     ▼                                                        │
        pluginmanager.register(Recorder(store, location))                     │
                     │                                          any Exception ┘
                     ▼                                          └─► warn(path), no recorder
        Recorder.pytest_sessionstart ──► store.start_execution(Execution)   INSERT INTO run
                     │                                     ▲
             (collection, tests …)                         │ RQ-21 boundary on every hook
                     ▼                                     │
        Recorder.pytest_sessionfinish(exitstatus) ────► store.finish_execution(...)
                     │                                       UPDATE run SET finished_at, exit_status
                     ▼
        Recorder.pytest_unconfigure ──► store.close()
```

Failure sequence, RQ-37 (read-only directory):

```
configure ──► resolve ──► mkdir ──► connect ✗ OperationalError
   └─► _warn("vantage: cannot record to /ro/.vantage/vantage.db: …")
   └─► return  ──► session runs, exit status = whatever the suite says
```

Failure sequence, RQ-21 (write raises mid-session, failing suite):

```
sessionfinish ──► store.finish_execution ✗ RuntimeError
   └─► boundary: warn once, self._disabled = True, return None
   └─► pytest computes exit status from the suite ──► 1
```

---

## Schema Manifest (RQ-29 deliverable)

Ships as `docs/schema-manifest.md`; `packages/vantage-storage/src/vantage_storage/schema.sql` is its
implementation. **Inspection** compares a freshly created database against this table. A companion
test (`test_schema_manifest.py`) mechanises the same comparison via `PRAGMA table_info` so the
manifest cannot rot silently — the test is a rot-detector, not the verification of record.

Conventions:

- Timestamps are ISO-8601 UTC `TEXT` (D10); booleans are `INTEGER` 0/1; JSON-shaped values are `TEXT`
  written with stdlib `json`.
- **†** marks a column whose content is unbounded by nature. Every † column carries a sibling
  `<name>_truncated` `INTEGER NOT NULL DEFAULT 0` (D12); they are not listed individually.
- A driver of **`—`** means *no requirement drives this column*. It is a non-derivable session fact
  retained under ADR-5, and there are exactly three of them in the whole schema — listed together
  below so an inspection can judge them rather than discover them. Every other column names the
  requirement it exists for. Derivable values are **not** stored: run-level outcome counters,
  run duration and an `expected failure` boolean were all dropped because `result` already answers
  them, and ADR-5's migration-avoidance argument is about facts that cannot be reconstructed.

The four `—` columns are `run.collected_count`, `test_case.first_seen_at`, `result.started_at` and
`result.finished_at`. Collected count is not derivable from `result` (a `-x` run collects a hundred
and records three); per-result timestamps are the only way to reconstruct a timeline once xdist runs
tests in parallel; and `first_seen_at` cannot be recovered once the run that first saw the test is
pruned.

**Milestone 1 creates all ten tables and populates only the marked `run` columns.**

### `meta`

| Column | Type | Notes |
| --- | --- | --- |
| `key` | TEXT PK | rows: `schema_version`, `created_at`, `created_by` |
| `value` | TEXT NOT NULL | |

ADR-5 forbids a migration *framework*, not a version stamp. Without `schema_version` a future
migration cannot even identify what it is migrating; with it, a later reader can refuse a database it
does not understand instead of misreading it. Populated at creation.

### `run`

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | TEXT PK | RQ-1 | **M1** |
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

**The five `vcs_*` columns are driven by three requirements, not one.** RQ-10 says what to write when
a repository is present and readable; RQ-23 covers a project directory that is not a repository at
all; RQ-39 covers a repository that is present but unreadable — corrupt index, missing `git` binary,
permissions. The three partition the space with no gap, and **all three record the run**, which is why
this milestone's product rule (every invocation gets a row) is not weakened by a missing repository.

Two schema consequences follow, and they are writer obligations for Milestone 3 recorded here because
the schema has to permit them:

- Every `vcs_*` column is `NULL`-able and **must be written as SQL `NULL`, never as an empty string**.
  An empty string is indistinguishable from a branch name that failed to read, which is exactly the
  ambiguity RQ-23 and RQ-39 exist to remove.
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

**RQ-13 is served by never deleting, not by a retirement column.** An earlier draft of this manifest
carried a `retired_at` column and attributed it to RQ-23; both were wrong. RQ-23 is about a run
recorded outside a repository and lives on `run.vcs_*`. RQ-13 is retention, and retention here means
the entry simply stays: "removed from the codebase" is read off `last_seen_at` being older than the
newest run, so a column recording the moment the disappearance was *noticed* adds nothing and would
need clearing on the test's return.

That return is RQ-13's second criterion and it is a **schema** obligation, not only a writer one:
`node_id` is `UNIQUE`, and the Milestone 2 writer upserts —
`INSERT … ON CONFLICT(node_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, …` — so a test
that disappears and comes back **reuses its entry** and advances its timestamp. Inserting a second
row would split one test's life in two and lose the connection across the gap, which is precisely the
failure the criterion names. A `retired_at` column would have made that upsert harder, not easier.

`node_id` is the Phase 1 identity key because "a test with the same identifier" means the pytest node
id at this phase. It is the wrong key for a test that moves file, which is exactly what Phase 2's
`stable_id` exists to fix; when it lands it supersedes `node_id` as the conflict target.

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

**`outcome` is composite, and that is the whole of RQ-4.** The requirement is that the recorded
outcome reflects *every* phase the test produced, so `outcome` is derived from the three phase
outcomes rather than copied from the call phase:

```sql
CHECK (outcome IN ('passed','failed','error','skipped','xfailed','xpassed'))
```

The mirror case that decides the implementation is a test whose body **passes** and whose teardown
**raises**: its outcome is not `passed`. The call phase has already reported success by then, which is
why an implementation that writes the outcome on `pytest_runtest_logreport(when="call")` gets it
wrong. Milestone 2 must therefore hold the three phase reports and resolve `outcome` at teardown, not
stream it. The other four criteria — a fixture raising before the body (`error`),
`@pytest.mark.skip` (`skipped`), an xfail that fails (`xfailed`), an xfail that passes (`xpassed`) —
all fall out of the same resolution step.

`skip_reason` and `xfail_reason` stay bound to RQ-4 because they are what makes those two outcomes
readable. `was_expected_failure` was dropped: `outcome IN ('xfailed','xpassed')` already carries it.
`duration` is the sum of the three phase durations, stored under RQ-5 for query cost rather than
recomputed per row.

`UNIQUE(run_id, node_id, attempt)` is the schema-level backstop for RQ-12: under xdist every result
arrives twice, once from the worker and once from the controller. The filter is the worker-input
attribute on the config object (Milestone 3); this constraint makes a de-duplication bug a loud
`IntegrityError` instead of a silently doubled history. `attempt` keeps rerun plugins from colliding
with it.

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

`content_hash` TEXT PK · `algorithm` TEXT NOT NULL DEFAULT `'sha256'` · `size_bytes` INTEGER NOT
NULL · `media_type` TEXT NULL · `content` BLOB NULL · `external_path` TEXT NULL · `first_stored_at`
TEXT NOT NULL. Content-addressing is the point: the same screenshot produced by two hundred runs is
stored once.

### `result_artifact` (Phase 2)

`id` INTEGER PK · `result_id` → `result(id)` · `content_hash` → `artifact(content_hash)` · `label`
TEXT NOT NULL · `phase` TEXT NULL · `created_at` TEXT NOT NULL ·
`UNIQUE(result_id, content_hash, label)`.

### Indexes

`run(started_at)` · `result(run_id)` · `result(test_case_id)` · **`result(failure_path,
failure_lineno)`** · `result(outcome)` · **`test_case(node_id)` UNIQUE** · `test_case(last_seen_at)` ·
`result_log(result_id, sequence)` · `result_log(result_id, level_no)` ·
`result_marker(result_id, name)` · `result_parameter(result_id)` · `result_artifact(content_hash)`.

The failure index is not decoration: RQ-8's acceptance criterion is that twenty tests failing at one
source line come back as one group, which is a `GROUP BY failure_path, failure_lineno` and needs the
index to stay usable at a year of history. `test_case(node_id)` is `UNIQUE` because RQ-13's
reuse-on-return is enforced there, and `test_case(last_seen_at)` serves the query that reads RQ-13
retention from the other side — which tests have stopped being observed.

**Ten tables, twelve indexes.** `PRAGMA foreign_keys=ON` on every connection.

---

## Interfaces / Contracts

```python
# packages/vantage-core/src/vantage_core/domain/execution.py
@dataclass(frozen=True, slots=True)
class Execution:
    """One pytest invocation. NOT `TestExecution` — pytest would collect it."""
    id: str
    started_at: datetime

# packages/vantage-core/src/vantage_core/ports/storage.py
class ExecutionStore(Protocol):
    def start_execution(self, execution: Execution) -> None: ...
    def finish_execution(
        self, execution_id: str, *,
        finished_at: datetime | None, exit_status: int | None,
        interrupted: bool, interrupt_reason: str | None,
    ) -> None: ...
    def close(self) -> None: ...

# packages/vantage-core/src/vantage_core/config/resolution.py
class PathSource(str, Enum):          # not StrEnum — that is 3.11+, and the floor is 3.10
    CLI = "cli"; ENV = "env"; INI = "ini"; DEFAULT = "default"

@dataclass(frozen=True, slots=True)
class DatabaseLocation:
    path: Path
    source: PathSource

DEFAULT_RELATIVE_PATH = PurePosixPath(".vantage/vantage.db")

# vantage_core/domain/text.py — RQ-22, D12. Fixed, not configurable.
MAX_TEXT_BYTES = 64 * 1024                      # 65_536 bytes of UTF-8, not characters
TRUNCATION_MARKER = "\n[vantage: truncated]"    # counted inside the budget

def is_activated(*, flag: bool, cli_path: str | None) -> bool: ...
def resolve_database_path(
    *, cli: str | None, env: str | None, ini: str | None, project_root: Path
) -> DatabaseLocation: ...
```

Resolution rules: the first source whose value is neither `None` nor empty-after-strip wins; `~` is
expanded; a relative value resolves against `project_root`, never against cwd; the fallback is
`project_root / ".vantage" / "vantage.db"`. No pytest type appears anywhere — `Path` and `Enum` are
stdlib, so RQ-26 holds and the precedence table is testable with plain function calls.

`str, Enum` rather than `StrEnum` is deliberate: `StrEnum` landed in 3.11 and RQ-27's floor is 3.10.
The deleted tree failed on exactly this.

---

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `src/vantage/**`, `tests/**` | Delete | Phase 3 product on SQLAlchemy/Alembic/Pydantic; own commit, head of slice A |
| `pyproject.toml` | Rewrite | uv workspace root; shared ruff / mypy-strict / deptry; the **only** `[tool.pytest.ini_options]` — `markers`, `--strict-markers`, `pythonpath` |
| `README.md`, `docs/architecture.md` | Rewrite | describe four packages and clean architecture, not the deleted plan |
| `docs/adr/0003…0006-*.md` | Create | mirror the four Notion ADRs into the repo, which is their source of truth |
| `docs/adr/0007-store-the-database-under-the-project-root.md` | Create | D1; `Proposed` in the PR, `Accepted` on merge |
| `docs/schema-manifest.md` | Create | the RQ-29 inspection artifact |
| `.pre-commit-config.yaml` | Create | pre-commit and pre-push stages per CLAUDE.md's gate table |
| `.github/workflows/ci.yml` | Create | 3.10–3.13 × {with, without} xdist; offline job (RQ-28); clean-env install (RQ-24); ruff/mypy/deptry/build |
| `.github/workflows/audit.yml` | Create | weekly `pip-audit` |
| `specs/.gitkeep` | Create | one-way mirror target from Notion |
| `packages/vantage-core/pyproject.toml` | Create | `dependencies = []`; `requires-python = ">=3.10,<3.14"` |
| `…/src/vantage_core/domain/execution.py` | Create | `Execution`, `Identity` |
| `…/src/vantage_core/domain/text.py` | Create | `MAX_TEXT_BYTES`, `TRUNCATION_MARKER` (D12); the helper that applies them lands with M2 |
| `…/src/vantage_core/ports/storage.py` | Create | `ExecutionStore` Protocol |
| `…/src/vantage_core/config/resolution.py` | Create | D6 |
| `…/tests/test_architecture.py`, `importwalk.py` | Create | D7 + vacuity guard |
| `…/tests/test_resolution.py` | Create | precedence table, no pytest session |
| `…/tests/vantage_port_contract.py` | Create | D11 — shared, not shipped |
| `packages/vantage-storage/pyproject.toml` | Create | depends on `vantage-core` only |
| `…/src/vantage_storage/schema.sql` | Create | the manifest above |
| `…/src/vantage_storage/connection.py` | Create | mkdir, `.gitignore`, PRAGMAs, WAL fallback, DDL |
| `…/src/vantage_storage/sqlite_store.py` | Create | `SqliteExecutionStore` |
| `…/src/vantage_storage/memory.py` | Create | `InMemoryExecutionStore` |
| `…/tests/test_sqlite_store.py`, `test_memory_store.py` | Create | both subclass the contract (RQ-30) |
| `…/tests/test_schema_manifest.py` | Create | manifest ↔ `PRAGMA table_info` rot-detector |
| `packages/vantage-pytest/pyproject.toml` | Create | `[project.entry-points.pytest11] vantage = "vantage_pytest.plugin"` |
| `…/src/vantage_pytest/plugin.py` | Create | D4 — two hooks, RQ-37 boundary |
| `…/src/vantage_pytest/recorder.py` | Create | D4 — four hooks, all wrapped |
| `…/src/vantage_pytest/boundary.py` | Create | D5 — decorator + `_warn` + `VantageWarning` |
| `…/tests/conftest.py` | Create | `pytest_plugins = ["pytester"]` |
| `…/tests/test_opt_in.py` | Create | RQ-2 differential tree test |
| `…/tests/test_run_entry.py` | Create | RQ-1, RQ-31 including SIGINT |
| `…/tests/test_failure_paths.py` | Create | RQ-21 (exit 0 **and** exit 1), RQ-37 (three causes) |
| `…/tests/test_concurrency.py` | Create | RQ-38 criterion 1 |
| `packages/vantage-service/pyproject.toml`, `src/vantage_service/__init__.py` | Create | skeleton only |

---

## Testing Strategy

Strict TDD; test command `uv run --extra dev pytest`. Every verifying test carries
`@pytest.mark.req("RQ-nn")`.

| Layer | What | How |
| --- | --- | --- |
| Unit (core) | precedence, activation, relative-path anchoring, empty-string-as-absent | plain calls, no pytest session |
| Unit (core) | RQ-26 + vacuity | `ast` walk over `vantage_core` |
| Contract (storage) | port behaviour | `ExecutionStoreContract` × {sqlite, in-memory} — RQ-30 |
| Inspection (storage) | RQ-29 | manifest ↔ `PRAGMA table_info`; plus reopening a database and asserting no DDL |
| Integration (plugin) | RQ-1, RQ-31 | `pytester.runpytest_subprocess`; zero-test, failed-collection, completed, SIGINT |
| Integration (plugin) | RQ-2 | differential — run bare, run `-p no:vantage`, compare tree hashes |
| Integration (plugin) | RQ-21 | monkeypatched store raising, on a passing suite (exit 0) **and** a failing one (exit 1) |
| Integration (plugin) | RQ-37 | read-only dir, missing dir, corrupt file — exit 0, one warning naming the path |
| Integration (plugin) | RQ-38 | two `subprocess` sessions launched together; assert two rows |
| CI (not tests) | RQ-24, RQ-27, RQ-28 | clean-env install diff; 3.10–3.13 × xdist matrix; network-disabled job — each block carries its RQ id in a comment |

RQ-2's assertion is **differential**, never absolute: pytest itself writes `.pytest_cache` and
`__pycache__`, so "no file created" is unsatisfiable and only "identical to the same run with the
plugin disabled" is true.

**Not tested at this milestone, by design.** RQ-4's phase resolution, RQ-13's reuse-on-return upsert,
RQ-22's truncation and RQ-23/RQ-39's null version-control fields are writer behaviours for Milestones
2 and 3. What this milestone owes them is a schema that permits them — a `UNIQUE(node_id)` to upsert
against, `NULL`-able `vcs_*` with no default, `†` flags to write into, and an `outcome` vocabulary
wide enough for `error` and `xpassed`. Their tests belong to the milestone that populates the
columns; putting them here would test a schema against no writer.

---

## Threat Matrix

The plugin executes inside every user pytest process, and its tests spawn subprocesses — a
process-integration boundary, so the matrix applies.

| Boundary | Applicability | Design response | Planned RED tests |
| --- | --- | --- | --- |
| Documentation-like paths | **N/A** — Milestone 1 classifies and executes no file; `schema.sql` is read as data by `sqlite3.executescript` from inside the installed package, never from a user-supplied path | — | — |
| Git repository selection | **N/A** — RQ-10's git columns exist but nothing populates them until Milestone 3; no `git` subprocess is invoked | — | — |
| Commit state | **N/A** — same reason | — | — |
| Push state | **N/A** — the plugin never touches a remote | — | — |
| PR commands | **N/A** — no PR automation in the product | — | — |
| **Process integration (added row)** | **Applicable** — the plugin loads into every pytest process on the machine once installed | inert-by-default split (D4); boundary catches `Exception`, never `BaseException`, so `KeyboardInterrupt`/`SystemExit` propagate; `session.exitstatus` never assigned | RQ-2 differential; RQ-21 exit 0 and exit 1; "every recorder hook is fault-isolated" |
| **Path authority (added row)** | **Applicable** — a database path arrives from CLI, env or `pyproject.toml` | resolution is pure and creates nothing; relative values anchor on rootdir, never cwd; creation is confined to `pytest_configure` inside the RQ-37 boundary | RQ-37 read-only dir, missing dir, corrupt file; relative-path anchoring test |
| **Concurrent writers (added row)** | **Applicable** — one database, many sessions | WAL, `BEGIN IMMEDIATE`, 5 s busy timeout, uuid4 ids, `IF NOT EXISTS` DDL in one transaction (D8) | RQ-38 two concurrent sessions; a lock-timeout test asserting degradation into the RQ-37 path |

---

## Migration / Rollout

No data migration — nothing is published and no user holds data. ADR-5 keeps a migration framework
out of Phase 1; `meta.schema_version` is the stamp that makes the first migration possible later
without being one.

Delivery is a **feature-branch chain**: A → tracker, B → A, C → B; only the tracker merges to `main`.
Within a slice, commits are work units — behaviour with its tests — never `models`/`services`/`tests`
layering.

| Slice | Work units | Rollback boundary |
| --- | --- | --- |
| **A** | (1) deletion, alone; (2) workspace root + four package `pyproject.toml` + `specs/`; (3) `req` marker and its guard test; (4) pre-commit; (5) CI matrix, offline job, clean-env install; (6) weekly audit; (7) ADR-3…0007 + README/architecture rewrite | revert restores `src/vantage` intact |
| **B** | (1) `Execution`/`Identity` + `ExecutionStore` port; (2) architecture test + vacuity guard; (3) option resolution + precedence tests; (4) `schema.sql` + `docs/schema-manifest.md` + manifest test; (5) contract suite + in-memory adapter; (6) sqlite store against the same contract | new files under two packages only |
| **C** | (1) entry point + inert `plugin.py` + RQ-2; (2) recorder + run row + RQ-1/RQ-31; (3) RQ-37 path; (4) RQ-21 boundary; (5) RQ-38 concurrency | new files under `vantage-pytest` plus one entry-point line |

Slice C was estimated at ~390 against a 400-line budget before RQ-31/37/38 were counted. The cut is
already chosen and does not need improvising: **C** keeps work units 1–2, **C2** takes 3–5 (both
failure paths and concurrency). `sdd-tasks` decides on its own forecast, not on this estimate.

---

## Open Questions

- [x] **RQ-4, RQ-22 and RQ-23 reconciled** against the verbatim texts as amended 2026-08-14. Three
      corrections landed: `test_case.retired_at` was bound to RQ-23 and is **deleted** (RQ-13 is
      served by never deleting plus a `UNIQUE(node_id)` upsert); `run.truncation_limit` is **deleted**
      because RQ-22's limit is a fixed 64 KiB, not a configurable one; and RQ-4 is the composite
      per-test `outcome`, not the run-level counters, which are **deleted** as derivable. The
      version-control columns are now bound to RQ-10 / RQ-23 / RQ-39 together.
- [ ] **RQ-39 and RQ-23 are missing from the proposal's requirement tables.** RQ-39 appears only in
      the deferred list (Milestone 3) and RQ-23 appears in neither the in-scope nor the deferred list,
      yet both constrain columns this milestone creates. The proposal should name them alongside
      RQ-10 as the three that partition version-control recording.
- [ ] **The proposal's configuration table still lists a truncation limit as project config.** RQ-22
      as amended fixes it at 64 KiB, so that row should go; `vantage-pytest` declares no ini key for
      it (D2, D12). Page size and retention are untouched by this.
- [ ] **Two Notion edits carried from the proposal** — RQ-2's notes should name `--vantage` as the
      activation option and absorb FT-1's reproducibility argument. Still unmade.
- [ ] **RQ-38's second criterion** should be annotated on the requirement as carried to Milestone 2,
      so this milestone is not read as fully verifying it.
- [ ] **`openspec/config.yaml` is stale** — its `context` block says hexagonal, `NFR-1-xx`,
      SQLAlchemy and one package, and its `rules.design` cite `NFR-1-03`/`NFR-1-01`. It should be
      rewritten to RQ ids and clean architecture in slice A, or it will keep misdirecting phases.
