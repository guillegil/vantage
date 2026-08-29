# Exploration: user-configuration surface (sections as first tenant)

## Current State

Vantage today has **zero user-facing configuration and zero write endpoints
other than ingestion** (`POST /api/v1/runs`, `POST /api/v1/runs/{id}/heartbeat`).
There is also **zero aggregate computation anywhere** in the API -- no counts,
no percentages, nothing beyond raw rows and pages (`MAX_PAGE_ITEMS = 200`,
`core/ports/storage.py:24`, clamped inside the store, unclamped by callers).

Relevant precedents already in the tree:

- **Read-time derivation of a presentation value from stored facts**:
  `vantage.core.domain.liveness.derive_presentation`
  (`packages/vantage/src/vantage/core/domain/liveness.py:32`) is a pure
  function (`Execution`, `last_contact_at`, `now`, `grace` -> one of four
  strings), called from `service/routes/read.py`'s `_run_list_item`. Nothing
  about a run's presentation is ever stored -- exactly the shape conclusion 1
  (sections derived at read time) asks to reuse.
- **Discovery over a hardcoded client assumption**: `GET /api/v1/capabilities`
  (`service/routes/capabilities.py`) answers `{"session_lifecycle": true}` --
  today a single boolean, but it establishes the "ask the server what it
  supports" pattern conclusion 5 wants to extend to sections.
- **Schema-version refusal, not migration**: `storage/connection.py`
  (`_SCHEMA_VERSION = 2`, `_check_schema_version`) refuses to open a database
  whose `meta.schema_version` differs. `schema.sql` only runs at *creation*
  (gated on the `meta` sentinel table being absent) -- reopening an existing
  database issues **no DDL at all**, so adding a table to `schema.sql` is
  invisible to any database that already has a `meta` row. This is exactly the
  trap ADR-0013 named for `run.last_contact_at`, and it recurs identically for
  any new configuration table.
- **No index on `test_case.file_path`.** `schema.sql` declares 14 indexes;
  none cover `file_path`. `idx_result_run_id` and `idx_result_test_case_id` do
  exist, and `test_case.id` is an `INTEGER PRIMARY KEY`.
- **No `COLLATE` clause anywhere in `schema.sql`.** Every TEXT column,
  including `file_path`, uses SQLite's default `BINARY` collation.
- **`test_case.stable_id`** is `NOT NULL UNIQUE` and, per ADR-0012, currently
  stores the identical string as `node_id` (the full pytest node id, including
  its path segment) -- "a rename or a move splits a test's history, and that
  split stays visible rather than being disguised... This is accepted, not
  merely tolerated" (ADR-0012).
- **No authentication in front of the server**
  (`service/cli.py:warn_if_bound_wide`) and **no deployment documentation** at
  all (no systemd unit, no Dockerfile, no CI guidance) -- both named as risks
  below, not solved here.

## Verification of the design conclusions already reached

1. **Sections derived at read time, never stored -- CONFIRMED as the right
   pattern**, and it is not a novel choice for this codebase:
   `derive_presentation` is the working precedent for "a label computed from
   stored facts at the moment of a read, never persisted." Renaming a section
   only requires re-running the same pure function against unchanged rows.
2. **Rules live server-side -- CONFIRMED**, and for a second reason beyond the
   one already given: the browser has no reachable code path to any
   `file_path`/`outcome` pair except through the HTTP API, and the API today
   returns zero aggregates (verified: no count/percentage field exists in any
   response schema in `service/schemas.py`).
3. **Rules are user data, not operational configuration -- CONFIRMED.** There
   is no existing config-file mechanism to reuse or extend even if this were
   rejected: `service/cli.py` takes `--database`/`--host`/`--port` as CLI
   flags/env, resolved once at process start (`core/config/resolution.py`).
   Bolting a second, hot-reloadable config file onto that model, read by a
   long-running `uvicorn` process with no file-watch mechanism today, would be
   new infrastructure invented for this feature alone.
4. **New SQLite table, not a server-maintained file -- CONFIRMED**, and
   directly reinforced by ADR-0006 (stdlib `sqlite3`, no ORM) and the project's
   own established position that SQLite already solves the
   concurrency/atomicity a hand-rolled file store would have to reinvent.
5. **Discovery, not hardcoding -- CONFIRMED as consistent** with
   `GET /api/v1/capabilities`, though sections need their own route: the
   capabilities endpoint is typed as `dict[str, bool]` on purpose (D38-D40:
   "exactly one thing a client needs to know today"); sections carry a
   `name`/`prefix` pair each, which does not fit that shape without overloading
   it.
6. **Unassigned bucket must be visible -- CONFIRMED as necessary** for
   correctness, not just UX: since the aggregate is computed server-side and a
   client never sees raw per-test rows for the aggregate view, any test
   silently dropped from every bucket makes the sum of section percentages not
   add up to the run's actual pass rate, with no way for a client to detect the
   discrepancy.

## Q1 -- Generic shape for the configuration surface

| Option | Description | Pros | Cons | Effort |
|---|---|---|---|---|
| A. Single generic key/value table | `user_preference(key TEXT PRIMARY KEY, value TEXT NOT NULL /*JSON*/, updated_at TEXT NOT NULL)` | Never needs a schema change again, however many preference kinds appear; matches "sections change weekly" cadence with zero DDL cost per addition | No relational constraints (uniqueness of a section's prefix must be enforced in application code, inside the JSON); a flat `key` string has no discovery axis -- `GET /api/v1/config` would return one JSON blob per key with no grouping | Low |
| B. Typed table per preference kind | `section(name TEXT PRIMARY KEY, prefix TEXT NOT NULL, position INTEGER)`, and a new table for every future preference | Full SQL power: real uniqueness constraints, indexable columns, foreign keys if ever needed | Every future preference kind repeats the ADR-0013 schema-version episode (bump `_SCHEMA_VERSION`, refuse older DBs, or finally build the migration ADR-5 declined) -- precisely the "one copy too many" cost pattern the Notion-dump deletion and ADR-4's four-empty-packages reversal both already paid for in this project | Low per kind, but repeats indefinitely |
| C. Hybrid namespace + JSON-document | `user_setting(namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL /*JSON*/, updated_at TEXT NOT NULL, PRIMARY KEY(namespace, key))`; sections = `namespace='test_sections'`, one row per section name | Same "add a preference kind, add zero DDL" property as A; the `namespace` column gives a real discovery axis (`GET /api/v1/config/{namespace}`, mirroring the capabilities precedent) for free; per-row PK on `(namespace, key)` allows per-item upsert/delete without rewriting a whole blob, unlike a single-blob-per-namespace design | Two-level key is marginally more ceremony than A for the one tenant that exists today; the `value` column is still application-validated JSON, not SQL-checked | Low |

**Recommendation: C (hybrid namespace + JSON-document), with an explicit
caution attached.** The instruction to avoid a premature abstraction is taken
seriously here: C is *not* recommended because "generic is always better" -- it
is recommended because its *marginal* cost over A (one extra TEXT column) is
close to zero, while the thing it buys -- never repeating ADR-0013's
refuse-and-recreate episode for the *next* preference kind -- is the single
most expensive recurring cost visible anywhere in this schema's history. B is
the one to actively avoid: it reproduces, for every future preference, exactly
the incremental-migration cost ADR-5 rejected for the domain schema,
deliberately, in writing. The mitigation against over-generalizing: **do not
build a generic config *framework*** (no generic CRUD UI generator, no
schema-in-JSON validation layer) -- each namespace's `value` shape stays
validated by an ordinary, namespace-specific Pydantic model in
`vantage.service`, so this is "generic storage, specific validation," not a
schemaless free-for-all.

## Q2 -- Layering and the pure core function

Reusing the `derive_presentation` shape directly:

- **`vantage.core.domain.sections`** (new module, RQ-26-clean -- stdlib only):
  a `SectionDefinition` frozen dataclass (`name: str`, `prefix: str`), a pure
  function `derive_section(file_path: str, sections: Sequence[SectionDefinition]) -> str`
  returning a section name or a module-level `UNASSIGNED` sentinel constant
  (following the `PRESENTATIONS`-as-`frozenset`/plain-string precedent, not an
  `Enum` -- that module's own docstring already explains why `Enum` is wrong
  twice over on this project's 3.10 floor). A second pure function reduces
  `(file_path, outcome)` pairs into per-section counts and a percentage --
  arithmetic belongs in core for the same reason `derive_presentation` does: it
  is deterministic, stateless, and needs zero I/O to test.
- **`vantage.storage`**: a new adapter method (or a small set of them) on
  `ExecutionStore` to persist and list section definitions -- implemented in
  *both* `SqliteExecutionStore` and the in-memory test double, since the port
  is a `Protocol` both must satisfy (`vantage_port_contract.py` enforces this
  today) -- plus a read method that returns `(file_path, outcome)` pairs for
  one run's results. That read is a plain join on already-indexed columns:
  `result.run_id` (indexed) joined to `test_case.id` (primary key) -- **no new
  index is required** for the per-run aggregate use case.
- **`vantage.service`**: routes call the storage read, call the core pure
  functions, and shape the Pydantic response -- the same division of labor
  `read.py`'s `_run_list_item` already uses for presentation.

## Q3 -- Prefix overlap / precedence rule

`tests/` and `tests/SectA/` both matching the same file is not an edge case to
reject; it is the natural shape of "start broad, narrow down," which is exactly
the workflow conclusion 6's unassigned-bucket-as-UI-entry-point implies (a user
sees an unassigned prefix and carves a more specific section out of a broader
one that already exists).

| Option | Behavior | Verdict |
|---|---|---|
| Longest-prefix-wins | Most specific matching prefix wins, no write-time validation needed | **Recommended** |
| Explicit ordering | A `position` field, first match wins | Rejected: reordering silently reclassifies unrelated tests; needs a UI concept ("reorder these") the brief never asked for |
| Refuse overlapping definitions at write time | Each file matches exactly one section, by construction | Rejected: forbids the broad-then-narrow pattern above; user-hostile for a preference edited "weekly" |

**Recommendation: longest-prefix-wins**, implemented inside `derive_section` by
sorting candidate `SectionDefinition`s by `len(prefix)` descending and
returning the first whose prefix matches, falling back to `UNASSIGNED`. Zero
write-time validation needed -- consistent with conclusion 3's rejection of
anything that feels like a gate.

## Q4 -- API surface sketch

Following `service/schemas.py`'s existing `*Response` naming and the
`RejectionError` shape in `service/errors.py`:

```
GET  /api/v1/config/sections
     -> SectionListResponse { items: [SectionResponse { name: str, prefix: str }] }

PUT  /api/v1/config/sections/{name}
     body: SectionUpsertRequest { prefix: str }
     -> SectionResponse { name: str, prefix: str }         (201 created / 200 updated)

DELETE /api/v1/config/sections/{name}
     -> 204, or 404 UnknownSectionError (new RejectionError subclass,
        same one-shape-per-rejection-kind discipline as UnknownRunError)

GET  /api/v1/runs/{run_id}/sections
     -> RunSectionSummaryResponse {
          items: [SectionSummary { name: str, total: int, passed: int, pass_percentage: float }],
          unassigned: SectionSummary
        }
```

A section named literally `"unassigned"` must be rejected at write time (a new,
small `RejectionError` subclass) so the reserved bucket name can never collide
with a user-defined one.

**Open, not decided here**: what counts toward `passed`/`pass_percentage` given
the six-value `outcome` vocabulary (`passed, failed, error, skipped, xfailed,
xpassed`) -- does `xpassed` count as a pass, does `skipped` inflate or shrink
the denominator? This needs an explicit product decision the same way RQ-44's
start-of-session write did; it must not be invented implicitly inside the
percentage function.

## Q5 -- Is the prefix query index-usable? (verified, not assumed)

Yes, in principle: `file_path` uses SQLite's default `BINARY` collation
(confirmed -- no `COLLATE` clause exists anywhere in `schema.sql`), and
SQLite's query planner can use an index for both the explicit range form
(`file_path >= 'tests/SectA/' AND file_path < 'tests/SectA0'`) and a
left-anchored `GLOB 'tests/SectA/*'` **only** under `BINARY` collation --
which this schema already has.

**But it is currently moot for the primary use case.** The per-run section
aggregate does not need a prefix-range scan at all: it needs every result for
one `run_id` (already served by `idx_result_run_id`) joined to `test_case` by
primary key, with classification done in the core pure function rather than in
SQL. No new index is required for that path. A new index on
`test_case(file_path)` would only become necessary for a *different*,
not-yet-requested query shape -- "every result across all history under section
X" -- and should be added if and when that query is actually built, not
preemptively.

## Q6 -- Unassigned bucket on the wire

Represented as a section-shaped entry with a reserved, write-protected name
(`"unassigned"`), always present in `RunSectionSummaryResponse` even when empty
(`total: 0`), so a client can always compute
`sum(item.total for item in items) + unassigned.total == run's total result count`
without special-casing absence. This is the direct wire consequence of
conclusion 6: hiding it, or making it optional-when-empty, reopens exactly the
"percentages lie by omission" failure the conclusion names.

## Q7 -- Historical runs whose paths match no current section

Nothing breaks. Every such result lands in `unassigned` on the next read,
automatically, because classification runs at read time against the *current*
section list (conclusion 1). This is not a new risk this change introduces --
it is the same accepted trade-off ADR-0012 already made in writing for
renamed/moved tests ("an incomplete history is preferable to a merged one that
silently asserts two different tests are the same test"). Sections inherit that
precedent rather than creating a new one.

## Q8 -- Schema-version decision: cost both ways (not decided here)

Adding any new table is **unavoidably** a schema-affecting change under this
project's own rules: `schema.sql` only runs at database creation (gated on the
`meta` sentinel), so a table added to the file is invisible to every database
that has already been opened once -- the identical trap ADR-0013 named for
`run.last_contact_at`.

- **Option A -- bump `_SCHEMA_VERSION` 2->3, refuse mismatched databases**
  (ADR-0013's own precedent). Cost today is verifiably near zero: ADR-0013
  itself records "the project is pre-1.0, with synthetic data only, no releases
  and no deployments" and this session's own search confirms there is still no
  deployment documentation anywhere in the tree. An operator simply recreates
  the database, as ADR-0013 already tells them to.
- **Option B -- write the first real migration** (an `ALTER TABLE`/conditional
  `CREATE TABLE` path keyed off the stored `schema_version`, finally building
  the seam ADR-5 deliberately left unbuilt). This is explicitly "the moment
  ADR-13 said would eventually arrive" -- but ADR-5's own reasoning for not
  building one yet ("a migration framework's whole cost... buys nothing until
  there is a database worth preserving") is still true today by the same
  evidence. Choosing B now would also require superseding or amending ADR-0013,
  which named this exact fork in writing.

**Recommendation for the proposal phase to weigh, not a decision made here**:
every fact and precedent found in this session points toward Option A being
cheaper and more consistent with existing ADRs, but this is explicitly the open
decision the user has not made, per the brief, and it is surfaced here rather
than resolved.

## Q9 -- Interaction with `stable_id` / Phase 2 identity

No new interaction beyond what ADR-0012 already accepted. `test_case.file_path`
is the input to `derive_section`, and moving a test file changes its `node_id`
(and today, identically, its `stable_id`), which ADR-0012 already decided
**splits history visibly rather than merging it**. A section computed at read
time simply inherits that split: the old node's results (under the old
`file_path`) keep classifying under their historical section, the new node's
results classify under whatever section its new path matches -- exactly the
same "incomplete beats wrong" outcome ADR-0012 chose deliberately. This change
does not need to solve, or even touch, Phase 2 reconciliation to be correct.

## Affected areas

- `packages/vantage/src/vantage/core/domain/sections.py` -- new pure module (core)
- `packages/vantage/src/vantage/core/ports/storage.py` -- new `ExecutionStore` protocol methods
- `packages/vantage/src/vantage/storage/sqlite_store.py` -- new adapter methods, one new table
- `packages/vantage/src/vantage/storage/memory.py` -- parity implementation (port contract)
- `packages/vantage/src/vantage/storage/schema.sql`, `connection.py` (`_SCHEMA_VERSION`) -- new table, version decision
- `packages/vantage/src/vantage/service/schemas.py`, `service/errors.py`, `service/routes/` -- new route module, new response/rejection shapes
- `docs/schema-manifest.md` -- new table's columns need traceability entries
- A new ADR is warranted for the schema-version fork (Q8) -- reversal cost exceeds a sprint per CLAUDE.md's own filter

## Risks (named, not solved here)

- **No authentication in front of the server.** This would be the **first
  user-facing write surface** beyond ingestion; anyone who can route to the
  host can also rewrite section definitions, same exposure
  `warn_if_bound_wide` already documents for ingestion.
- **No deployment documentation.** Whatever the schema-version decision
  becomes, there is no operator-facing guidance today for "recreate the
  database" or "apply a migration."
- **Pass-percentage semantics** (Q4) are undefined against the six-outcome
  vocabulary and must be decided explicitly, not implied by an aggregation
  function.
- **Premature-generalization risk acknowledged**: recommending the hybrid
  namespace table (C) commits to a slightly more general shape than sections
  alone would need; mitigated by keeping validation per-namespace and specific,
  not building a generic config framework.

## Ready for Proposal

**Yes**, with two explicit open decisions the proposal phase must resolve
rather than infer: (1) schema-version bump-and-refuse vs. first migration (Q8),
and (2) pass-percentage semantics over the six-value outcome vocabulary (Q4).
