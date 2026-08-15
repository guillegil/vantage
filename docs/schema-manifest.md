# Schema Manifest (RQ-29 deliverable)

> **Source of truth for the schema is `docs/schema-manifest.md` and
> `packages/vantage/src/vantage/storage/schema.sql` together — the two are
> compared against each other, not against Notion.** This file is the
> column-by-column record RQ-29 verifies **by Inspection**; the SQL file is
> its implementation, applied whole by `vantage/storage/connection.py`
> (PR4) the first time a database is created. Every `CREATE` statement in
> `schema.sql` is `IF NOT EXISTS`, which is what makes reopening an existing
> database issue no schema-altering statement (RQ-29.2).

## Conventions

- Timestamps are ISO-8601 UTC `TEXT` (design.md D13); booleans are
  `INTEGER` 0/1; JSON-shaped values are `TEXT` written with stdlib `json`.
- **†** marks a column whose content is unbounded by nature. Every †
  column carries a sibling `<name>_truncated INTEGER NOT NULL DEFAULT 0`,
  listed alongside it rather than as a separate row. The cap is a fixed
  64 KiB of UTF-8 with an in-band marker counted inside the budget; the
  sibling flag is the machine-authoritative answer, unforgeable because a
  test's output cannot write a column. Milestone 1 populates no such
  field — what lands now is the schema that makes it expressible;
  Milestone 2 adds the writer that populates the flag.
- A driver of **`—`** means *no requirement drives this column*: a
  non-derivable session fact retained under ADR-5. There are exactly
  **five** of them in the whole schema, listed together in their own
  section below so an inspection judges them rather than discovers them
  scattered through the tables.
- `M1`/`M2`/`M3`/`Phase 2` in the **Populated** column names the milestone
  or phase whose writer first populates that column. Milestone 1 creates
  every table below; it populates only the columns marked **M1**.

## The five `—` (no-requirement) columns

| Column | Table | Why it is retained without a requirement driving it |
| --- | --- | --- |
| `received_at` | `run` | The server's receipt clock (design.md D1/D3) — the moment of receipt cannot be reconstructed later once lost, and ADR-9 created the second clock this column exists to record. |
| `collected_count` | `run` | Not derivable from `result`: a `-x` run can collect a hundred tests and record three. |
| `first_seen_at` | `test_case` | Cannot be recovered once the run that first saw a test is pruned. |
| `started_at` | `result` | Per-result timestamps are the only way to reconstruct a timeline once xdist runs tests in parallel. |
| `finished_at` | `result` | Same reason as `result.started_at`. |

Derivable values are deliberately **not** stored: a run-level outcome
counter, run duration and an "expected failure" boolean were all dropped
because `result` already answers them, and ADR-5 does not ask for
redundant storage.

## Tables

**Ten tables, thirteen indexes.** `PRAGMA foreign_keys=ON` is set by every
connection in `vantage/storage/connection.py` (PR4), not by `schema.sql`
itself — SQLite ignores unenforced foreign keys by default, so the
`REFERENCES` clauses below only take effect once a connection turns the
pragma on.

### `meta`

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `key` | TEXT PK | — (structural, not a session fact) | at creation |
| `value` | TEXT NOT NULL | — (structural, not a session fact) | at creation |

Rows: `schema_version`, `created_at`, `created_by`. ADR-5 forbids a
migration *framework*, not a version stamp — without `schema_version` a
future migration cannot identify what it is migrating.

*Not counted among the five `—` columns above*: `meta` is a structural
table describing the schema itself, not a session fact, and Milestone 1's
`schema.sql` creates it but does not populate its rows (that lands with
`connection.py`, PR4).

### `run` — the only table Milestone 1 populates

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
| `vcs_dirty` | INTEGER NULL, **no default** | RQ-10 / RQ-23 / RQ-39 | M3 |
| `vcs_root` | TEXT NULL | RQ-10 / RQ-23 / RQ-39 | M3 |
| `collected_count` | INTEGER NULL | — | M2 |

**The five `vcs_*` columns are driven by three requirements, not one.**
RQ-10 says what to write when a repository is present and readable;
RQ-23 covers a project directory that is not a repository at all; RQ-39
covers a repository present but unreadable. The three partition the space
with no gap, and all three record the run — which is why the product
rule (every invocation gets a row) is not weakened by a missing
repository.

Two Milestone 3 writer obligations the schema must permit, both already
satisfied by the types above: every `vcs_*` column must be written as SQL
`NULL`, never an empty string (an empty string is indistinguishable from
a branch name that failed to read); and `vcs_dirty` has no default,
because defaulting to 0 would make a run recorded outside a repository
claim a clean working tree.

### `test_case` — the RQ-13 catalogue

Python-side type is `Identity`, never `TestIdentity` (pytest would collect
it as a test class).

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | — (surrogate key) | M2 |
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

`node_id`'s `UNIQUE` constraint is enforced by the named index
`idx_test_case_node_id`, not an inline column constraint — deliberately,
so it is one of the thirteen indexes this manifest counts rather than an
anonymous `sqlite_autoindex_*`. `stable_id`'s `UNIQUE` stays inline; it is
not one of the thirteen counted indexes (Phase 2 has not landed).

**RQ-13 is served by never deleting, not by a retirement column.**
"Removed from the codebase" is read off `last_seen_at` being older than
the newest run, so a column recording when the disappearance was
*noticed* would add nothing. "Reused on return" (RQ-13's second
criterion) is a schema obligation: `node_id` is `UNIQUE`, and the
Milestone 2 writer upserts against it
(`INSERT … ON CONFLICT(node_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, …`)
so a test that disappears and comes back reuses its entry rather than
splitting its history in two.

### `result`

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | — (surrogate key) | M2 |
| `run_id` | TEXT NOT NULL → `run(id)` | RQ-4 | M2 |
| `test_case_id` | INTEGER NOT NULL → `test_case(id)` | RQ-13 | M2 |
| `node_id` | TEXT NOT NULL | RQ-9 | M2 |
| `attempt` | INTEGER NOT NULL DEFAULT 0 | RQ-12 | M2 |
| `outcome` | TEXT NOT NULL, `CHECK`'d | **RQ-4** | M2 |
| `duration` | REAL NULL | RQ-5 | M2 |
| `started_at` | TEXT NULL | — | M2 |
| `finished_at` | TEXT NULL | — | M2 |
| `setup_outcome` | TEXT NULL | RQ-5 | M2 |
| `call_outcome` | TEXT NULL | RQ-5 | M2 |
| `teardown_outcome` | TEXT NULL | RQ-5 | M2 |
| `setup_duration` | REAL NULL | RQ-5 | M2 |
| `call_duration` | REAL NULL | RQ-5 | M2 |
| `teardown_duration` | REAL NULL | RQ-5 | M2 |
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

**`outcome` is composite, and that is the whole of RQ-4:**

```sql
CHECK (outcome IN ('passed','failed','error','skipped','xfailed','xpassed'))
```

The mirror case that decides the implementation is a test whose body
**passes** and whose teardown **raises**: its outcome is not `passed`.
The call phase has already reported success by then, which is why an
implementation writing `outcome` on `pytest_runtest_logreport(when="call")`
gets it wrong — Milestone 2 must hold the three phase reports and resolve
`outcome` at teardown, not stream it.

`UNIQUE(run_id, node_id, attempt)` is the schema-level backstop for
RQ-12: under xdist every result arrives twice, once from the worker and
once from the controller. The filter is the worker-input attribute on the
config object (design.md D2, extended to results in Milestone 3); this
constraint turns a de-duplication bug into a loud `IntegrityError` rather
than a silently doubled history. `attempt` keeps rerun plugins from
colliding with it.

### `result_marker` (RQ-7)

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | — (surrogate key) | M2 |
| `result_id` | INTEGER NOT NULL → `result(id)` | RQ-7 | M2 |
| `name` | TEXT NOT NULL | RQ-7 | M2 |
| `args` † | TEXT NULL (JSON) | RQ-7 | M2 |
| `kwargs` † | TEXT NULL (JSON) | RQ-7 | M2 |
| `origin` | TEXT NOT NULL, `CHECK`'d (`function`, `class`, `module`, `package`, `session`, `config`) | RQ-7 | M2 |

### `result_parameter` (RQ-6)

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | — (surrogate key) | M2 |
| `result_id` | INTEGER NOT NULL → `result(id)` | RQ-6 | M2 |
| `name` | TEXT NOT NULL | RQ-6 | M2 |
| `position` | INTEGER NOT NULL | RQ-6 | M2 |
| `value_repr` † | TEXT NULL | RQ-6 | M2 |
| `value_type` | TEXT NULL | RQ-6 | M2 |

### `result_log` (Phase 2 — structured per-test logs, filterable by severity)

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | — (surrogate key) | Phase 2 |
| `result_id` | INTEGER NOT NULL → `result(id)` | Phase 2 | Phase 2 |
| `sequence` | INTEGER NOT NULL | Phase 2 | Phase 2 |
| `phase` | TEXT, `CHECK`'d (`setup`, `call`, `teardown`) | Phase 2 | Phase 2 |
| `created_at` | TEXT NOT NULL | Phase 2 | Phase 2 |
| `level_no` | INTEGER NOT NULL | Phase 2 | Phase 2 |
| `level_name` | TEXT NOT NULL | Phase 2 | Phase 2 |
| `logger_name` | TEXT NULL | Phase 2 | Phase 2 |
| `message` † | TEXT NOT NULL | Phase 2 | Phase 2 |
| `path` | TEXT NULL | Phase 2 | Phase 2 |
| `lineno` | INTEGER NULL | Phase 2 | Phase 2 |

`level_no` is the numeric column because *filterable by severity* means
`WHERE level_no >= 30`; `level_name` alone would force the filter into
application code.

### `result_fixture` (Phase 2)

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | — (surrogate key) | Phase 2 |
| `result_id` | INTEGER NOT NULL → `result(id)` | Phase 2 | Phase 2 |
| `name` | TEXT NOT NULL | Phase 2 | Phase 2 |
| `scope` | TEXT NULL | Phase 2 | Phase 2 |
| `position` | INTEGER NOT NULL | Phase 2 | Phase 2 |

### `artifact` (Phase 2 — content-addressed)

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `content_hash` | TEXT PK | Phase 2 | Phase 2 |
| `algorithm` | TEXT NOT NULL DEFAULT `'sha256'` | Phase 2 | Phase 2 |
| `size_bytes` | INTEGER NOT NULL | Phase 2 | Phase 2 |
| `media_type` | TEXT NULL | Phase 2 | Phase 2 |
| `content` | BLOB NULL | Phase 2 | Phase 2 |
| `external_path` | TEXT NULL | Phase 2 | Phase 2 |
| `first_stored_at` | TEXT NOT NULL | Phase 2 | Phase 2 |

Content-addressing is the point: the same screenshot produced by two
hundred runs is stored once. Its on-disk store is the `artifacts/`
directory design.md D9 creates at `0700` (RQ-40.2).

### `result_artifact` (Phase 2)

| Column | Type | Driver | Populated |
| --- | --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | — (surrogate key) | Phase 2 |
| `result_id` | INTEGER NOT NULL → `result(id)` | Phase 2 | Phase 2 |
| `content_hash` | TEXT NOT NULL → `artifact(content_hash)` | Phase 2 | Phase 2 |
| `label` | TEXT NOT NULL | Phase 2 | Phase 2 |
| `phase` | TEXT NULL | Phase 2 | Phase 2 |
| `created_at` | TEXT NOT NULL | Phase 2 | Phase 2 |

`UNIQUE(result_id, content_hash, label)`.

## Indexes

Thirteen, all created `IF NOT EXISTS` in `schema.sql`:

1. `run(started_at)`
2. **`run(received_at)`**
3. `result(run_id)`
4. `result(test_case_id)`
5. **`result(failure_path, failure_lineno)`**
6. `result(outcome)`
7. **`test_case(node_id)` UNIQUE**
8. `test_case(last_seen_at)`
9. `result_log(result_id, sequence)`
10. `result_log(result_id, level_no)`
11. `result_marker(result_id, name)`
12. `result_parameter(result_id)`
13. `result_artifact(content_hash)`

The failure index (5) is not decoration: RQ-8's criterion is that twenty
tests failing at one source line come back as one group, a
`GROUP BY failure_path, failure_lineno`. `run(received_at)` (2) is the
arrival-order index the Milestone 4 read API needs, created now per
ADR-5 rather than later.

<!-- RQ-29 -->
## Comparison recorded (RQ-29 verification of record)

**Performed 2026-08-15**, against `packages/vantage/src/vantage/storage/schema.sql`
as it lands in this PR. Method: a throwaway `sqlite3.connect(":memory:")`
database with `schema.sql` applied via `executescript`, then `PRAGMA
table_info(<table>)` per table and `PRAGMA index_list(<table>)` per table,
compared column-by-column and index-by-index against this manifest.

### Scenario 1 — Fresh database matches the column manifest

| Check | Result |
| --- | --- |
| Table count | **10**, matching this manifest's ten sections: `meta`, `run`, `test_case`, `result`, `result_marker`, `result_parameter`, `result_log`, `result_fixture`, `artifact`, `result_artifact`. |
| Column count per table | `meta` 2, `run` 29, `test_case` 16, `result` 33, `result_marker` 8, `result_parameter` 7, `result_log` 12, `result_fixture` 5, `artifact` 7, `result_artifact` 6 — every one matches this manifest's column list, including every `†` sibling `_truncated` column. |
| Column names, types, nullability, defaults | Match `PRAGMA table_info` exactly for all 125 columns: `notnull`/`dflt_value` from the pragma agree with every "NOT NULL"/"DEFAULT" and every explicitly-nullable column above. `run.vcs_dirty` confirmed nullable with **no** default (`dflt_value IS NULL`), as this manifest requires. |
| Primary keys | `meta.key`, `run.id`, `test_case.id`, `result.id`, `result_marker.id`, `result_parameter.id`, `result_log.id`, `result_fixture.id`, `artifact.content_hash`, `result_artifact.id` — each `pk` flag from `PRAGMA table_info` agrees with this manifest. |
| Index count | **13** named indexes (`idx_*`), matching the thirteen listed above exactly — verified by summing `PRAGMA index_list` per table and excluding the five `sqlite_autoindex_*` entries SQLite creates for inline `PRIMARY KEY`/`UNIQUE` constraints (`meta`, `run`, `test_case.stable_id`, `result`, `result_artifact`), which are not part of the counted thirteen. |
| `test_case(node_id)` uniqueness | Confirmed `UNIQUE` via the named index `idx_test_case_node_id` (`PRAGMA index_list` reports `unique=1`), not an anonymous autoindex — deliberate, so it counts among the thirteen. |
| `CHECK` constraints | `result.outcome` (six-value enum), `result_marker.origin` (six-value enum), `result_log.phase` (three-value enum) present in the compiled schema (`sqlite_master.sql`) and match this manifest's `CHECK` clauses verbatim. |

**Result: every documented column exists, with the documented type,
nullability, default and constraint — Scenario 1 holds.**

### Scenario 2 — Opening an existing database issues no schema-altering statement

| Check | Result |
| --- | --- |
| Re-applying `schema.sql`'s full script against the already-created in-memory database | Succeeds with no error; every statement is `CREATE TABLE IF NOT EXISTS` or `CREATE INDEX IF NOT EXISTS`, so a second application is a no-op against an existing schema, not a second `CREATE`. |
| Table count after re-application | **10**, unchanged. |
| Index count after re-application | **13** named, unchanged. |

**Result: reopening an existing database issues no schema-altering
statement — Scenario 2 holds**, in the sense available to this PR (the
mechanism, `IF NOT EXISTS`, applied twice against the same connection).
The full RQ-29.2 scenario — a second **process**, on a database file
created by an earlier release, opened through `connection.py`'s
`open_database` — is `test_connection.py`'s job in PR4, which this PR
does not include; `connection.py` does not exist yet.

## Known inconsistency — not corrected in this PR

`docs/adr/0005-complete-schema-at-first-use-no-migrations-in-phase-1.md`
says **"all ten tables and twelve indexes"** and references
`vantage-storage/src/vantage_storage/schema.sql` — both from before the
two-distribution restructure (ADR-4) and before this manifest's index
count grew to thirteen. `docs/adr/0006-use-stdlib-sqlite3-and-no-orm.md`
references the same pre-restructure path
(`vantage_storage/connection.py`). Both ADRs are still `Status: Proposed`.

Until PR14 (task 8.2) corrects them, **RQ-29's inspection has two
disagreeing sources: this manifest (thirteen indexes, current paths) and
ADR-5/ADR-6's prose (twelve indexes, pre-restructure paths).** This
manifest and `schema.sql` are the ones an inspector should trust in that
window — the tasks.md `Flagged, Not Actioned` note says the same. This is
exactly the two-disagreeing-sources defect PR14 exists to close; fixing
the ADRs here would be out of this PR's scope (task 2.1 only).
