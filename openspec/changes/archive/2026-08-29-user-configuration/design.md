# Design: User-configuration surface (test sections as first tenant)

> **Identifier vocabulary.** No numeric requirement identifiers are minted here
> (CLAUDE.md, decided 2026-08-18). Obligations anchor to the **capability** and
> its **scenario**; existing `RQ-xx` identifiers are cited only where the
> obligation already carries one. Decisions continue the project's single
> running sequence: `failure-capture` closed at **D81**, so this change opens at
> **D82**.
>
> **Every path, section name and number in this document is invented.**
> Synthetic data only; public repository (CLAUDE.md, Constraints).
>
> **Probed, not remembered** (D80's process note). Everything this document
> asserts about existing code was read this session:
> `core/domain/liveness.py` (`PRESENTATIONS` is a `frozenset` of plain strings,
> never an `Enum`), `core/ports/storage.py` (`MAX_PAGE_ITEMS = 200`,
> `MAX_IDENTITY_CHARS = 1024`, ten `Protocol` methods), `storage/schema.sql`
> (ten tables, fourteen indexes, no `COLLATE` clause anywhere, the
> `INSERT OR IGNORE ... 'schema_version', '2'` stamp as the last statement),
> `storage/connection.py` (`_SCHEMA_VERSION = 2`, `_check_schema_version`),
> `storage/sqlite_store.py` (`BEGIN IMMEDIATE` under `self._lock`, the
> existence-probe-then-upsert shape of `record_session`), `storage/memory.py`,
> `service/{app,schemas,errors}.py`, `service/routes/{read,capabilities}.py`,
> `service/openapi/v1.yaml` (nine operations, `read`/`write` tags),
> `tests/vantage_port_contract.py` and `tests/test_read_only_surface.py`.

## Technical Approach

The proposal's five resolved product questions are inputs, not subjects. What
this design settles is where each obligation lives so that the layering rule
(ADR-0003, RQ-26, RQ-24) holds without a single new abstraction.

The shape is a straight line through three layers that already exist:

- **Storage stays generic and dumb.** One table, four columns, and a port that
  moves `str` values it never interprets. `vantage.storage` learns nothing about
  what a section is, so it needs no JSON parsing and no validation (D82, D83).
- **The core stays pure and stdlib-only.** One new module holds the entire
  section vocabulary: the reserved bucket name, the prefix rule, the
  longest-prefix match, and the arithmetic. It is `derive_presentation`'s
  shape applied to a second question (D84, D85).
- **The service owns every shape.** Pydantic validates the stored JSON on the
  way out and on the way in; the routes convert to core dataclasses before the
  core is ever called, exactly as `routes/runs.py` already converts a
  `RunReport` to an `Execution` (D83, D87).

Two consequences worth naming up front. `schema.sql` gains a table, so
`_SCHEMA_VERSION` goes `2 → 3` and ADR-0013's refusal engages for the first time
since it was written — that lands in the **first** slice, so the one database
recreation happens at the start of the chain rather than in its middle (D82).
And the section name never travels in a URL path segment: D54 already settled
that a value which may contain `/` cannot, and a section name may (D87).

---

## Architecture Decisions

### D82 — `user_setting`: one table, schema version 3, no new index

The table goes into `schema.sql` immediately after `result_artifact` and before
the `-- Indexes` block, so the file keeps its "every table, then every index,
then the stamp" order:

```sql
-- ---------------------------------------------------------------------------
-- user_setting -- namespaced, server-persisted user preferences. Generic
-- storage, specific validation: `value` is JSON text this schema does not
-- describe and this adapter never parses; each namespace's shape is validated
-- by an ordinary Pydantic model in `vantage.service` (D83).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_setting (
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
```

| Choice | Why |
| --- | --- |
| `PRIMARY KEY (namespace, key)` | The one uniqueness rule this feature has, stated in SQL rather than in application code. Its implicit index is left-anchored on `namespace`, so `WHERE namespace = ?` is served by it |
| **No new index** | The only query is that one equality on the PK's leading column. `test_case(file_path)` gets no index either — see D86 |
| Not `WITHOUT ROWID` | A textbook candidate, and rejected: no other table in this schema uses it, the table is bounded at 200 rows (D89), and one table with a different storage class is a difference a reader has to explain |
| `updated_at` stored, not on the wire | The stored fact is cheap now and unrecoverable later — the same argument `docs/schema-manifest.md` records for `run.received_at`. No scenario reads it back yet, and adding it to a response later is additive |

**`_SCHEMA_VERSION` becomes `3` and `schema.sql`'s last statement stamps `'3'`
in the same edit.** `connection.py`'s own comment states the invariant: the two
must move together or `open_database` refuses its own fresh schema. No migration
is written and no `ALTER TABLE` is issued — ADR-0013 decided this in writing,
and this change is the first to pay its price rather than the first to reopen
it. **No new ADR** (D90).

`docs/schema-manifest.md` gains: a `### user_setting` table section with the
four columns (driver `user-configuration`, populated `M2`), the header count
corrected from **ten tables** to **eleven** in both the manifest and
`schema.sql`'s own header comment, the index count left at **fourteen**, and the
`meta` section's stamp value corrected to `3`. RQ-29 verifies the manifest by
Inspection, so this is the deliverable, not a note about one.

### D83 — The port moves opaque JSON text; only `vantage.service` knows its shape

RQ-24 and RQ-26 forbid Pydantic in `vantage.storage`. The cheapest way to honour
that is not to hide the parsing — it is to move nothing that needs parsing:

```python
# vantage/core/ports/storage.py -- beside Page, RunListEntry, ...
@dataclass(frozen=True, slots=True)
class UserSetting:
    """One row of `user_setting`. `value` is JSON TEXT this layer never
    parses -- the namespace's own Pydantic model in `vantage.service` is the
    only thing that knows its shape (design.md D83)."""

    namespace: str
    key: str
    value: str
    updated_at: datetime
```

| Layer | What it does with `value` |
| --- | --- |
| `vantage.storage` | Binds it as a parameter, reads it back as `str`. Never imports `json` for it |
| `vantage.core` | Never sees it. `derive_section` takes `SectionDefinition`, not JSON |
| `vantage.service` | `SectionValue(prefix=...).model_dump_json()` on write; `SectionValue.model_validate_json(row.value)` on read, then builds the core dataclass |

Alternatives rejected:

| Option | Why not |
| --- | --- |
| Store a typed `dict` and let the adapter `json.dumps` it | Puts the encoding decision in two adapters instead of one place, and the in-memory double would have to reproduce it to stay honest |
| Parse in the core with stdlib `json` | The core would then own a wire format. RQ-26 permits it and the layering rule does not: a JSON document is a boundary artifact, and the boundary is `vantage.service` |
| A `TypedDict` on the port | Same coupling as above with none of Pydantic's validation, and it would make the port namespace-aware |

**A stored value that fails validation is a named failure, not a traceback.**
A hand-edited database can hold `{"prefix": 7}`. The route raises
`UnreadableSettingError` (D89) — status `500`, the one rejection body shape,
naming the namespace and key and never the value. Skipping the row silently was
rejected: a section would vanish and its tests would move to `unassigned` with
nothing anywhere saying why, which is the ambiguous-degraded-state failure
ADR-0013 rejected for a mismatched database.

### D84 — `vantage.core.domain.sections`: the whole vocabulary, stdlib only

```python
# vantage/core/domain/sections.py -- imports nothing outside the stdlib (RQ-26)

UNASSIGNED = "unassigned"
"""The reserved bucket name. A module-level plain `str`, never an `Enum` and
never a one-member class -- `liveness.PRESENTATIONS` and `result.OUTCOMES`
already record why on this project's 3.10 floor, and a third shape for the
same kind of vocabulary is one shape too many."""

SECTION_NAME_MAX_CHARS = 120
SECTION_PREFIX_MAX_CHARS = 1024
MAX_SECTIONS = 200

@dataclass(frozen=True, slots=True)
class SectionDefinition:
    name: str
    prefix: str

def normalize_prefix(prefix: str) -> str:
    """Strip surrounding whitespace and coerce exactly one trailing `/`."""

def derive_section(file_path: str, sections: Sequence[SectionDefinition]) -> str:
    """The longest matching `prefix` wins; `UNASSIGNED` when none matches."""
```

Three rules, each a defect if got wrong:

- **`normalize_prefix` runs on write, `derive_section` matches byte-exactly.**
  `tests/SectA` is stored as `tests/SectA/`, so it can never match
  `tests/SectAlpha/test_x.py`. Matching is `str.startswith`, case-sensitive —
  which agrees with the schema by construction, because `schema.sql` declares no
  `COLLATE` clause anywhere and `file_path` therefore uses SQLite's default
  `BINARY` collation.
- **Longest-prefix-wins is implemented by sorting candidates on
  `len(prefix)` descending and returning the first match.** No write-time
  overlap check exists, by decision: `tests/` and `tests/SectA/` coexisting is
  the broad-then-narrow editing workflow.
- **A prefix that is exactly the file path's own directory still matches**, and
  a prefix equal to the whole path with a trailing slash matches nothing. That
  is the coercion's cost, stated rather than discovered: sections group
  directories, not individual files.

Ties (two sections with the identical prefix) cannot occur through the API for
one namespace, because two names may share a prefix but `derive_section` then
has two equally-long candidates. The rule is stated rather than left to sort
stability: among equal-length matching prefixes, the **alphabetically first
name** wins, so the answer never depends on row order in either adapter.

### D85 — The aggregate: four numbers per bucket, one identity a client can check

```python
@dataclass(frozen=True, slots=True)
class SectionSummary:
    name: str
    total: int             # every result in this section, skipped included
    measured: int          # the pass-percentage denominator
    passing: int           # the pass-percentage numerator
    pass_percentage: float | None

@dataclass(frozen=True, slots=True)
class RunSectionSummary:
    items: tuple[SectionSummary, ...]     # alphabetical by name
    unassigned: SectionSummary            # its own field, never in `items`

def summarize_sections(
    case_outcomes: Iterable[tuple[str, str]],
    sections: Sequence[SectionDefinition],
) -> RunSectionSummary: ...
```

| Quantity | Definition |
| --- | --- |
| `passing` | `passed + xfailed` |
| `measured` | `passed + failed + error + xfailed + xpassed` |
| `total` | all six outcomes |
| `pass_percentage` | `round(100 * passing / measured, 1)`, or `None` when `measured == 0` |

**`measured` is on the wire and that is the point.** With only `total` and
`passing`, a client cannot check the percentage, because `skipped` is excluded
from the denominator and the client has no way to see how many were skipped. The
six-value vocabulary makes `total - measured` **exactly** the skipped count, so
the two published identities are:

```
sum(item.total for item in items) + unassigned.total == the run's result count
item.passing / item.measured                         == item.pass_percentage / 100
```

Nothing can lie by omission, which is the whole reason the `unassigned` bucket
is present-even-when-empty in its own field rather than optional in the list.

**Rounding happens once, in the core.** `85 / 90` is `94.4`, which is what the
success criterion states literally; a client wanting more precision divides
`passing` by `measured`, which are exact integers. Rounding in the route instead
would put the same arithmetic in two places and make the pure function's own
tests assert a value the wire does not carry.

**`None`, never `0.0` or `100.0`, when `measured` is zero** — an empty run, and
equally a section in which every test was skipped. Both are "nothing was
measured", and both are the same wire value.

### D86 — Four port methods, both adapters, and no index on `test_case.file_path`

```python
class ExecutionStore(Protocol):
    ...
    def list_settings(self, namespace: str) -> Sequence[UserSetting]: ...
    def upsert_setting(
        self, namespace: str, key: str, *, value: str, updated_at: datetime
    ) -> bool: ...
    def delete_setting(self, namespace: str, key: str) -> bool: ...
    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]: ...
```

`upsert_setting` returns True only on a true first insert and `delete_setting`
returns False for a key that was not there — the same booleans `record_session`
and `touch_last_contact` already return, for the same reason: the route needs
`201` versus `200`, and `404` versus `204`.

SQL, following the module's existing shape (bound parameters only, never
interpolation):

```sql
-- _LIST_SETTINGS: alphabetical by key == alphabetical by section name (D85).
SELECT namespace, key, value, updated_at
FROM user_setting WHERE namespace = ? ORDER BY key

-- _UPSERT_SETTING: the existence probe precedes it inside the same
-- BEGIN IMMEDIATE, because `rowcount` cannot distinguish insert from update
-- under DO UPDATE (D26's reason, unchanged).
INSERT INTO user_setting (namespace, key, value, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT (namespace, key) DO UPDATE SET
    value = excluded.value,
    updated_at = excluded.updated_at

-- _DELETE_SETTING: rowcount == 1 is the "it existed" answer.
DELETE FROM user_setting WHERE namespace = ? AND key = ?

-- _SELECT_RUN_CASE_OUTCOMES: the per-run aggregate read (D85).
SELECT tc.file_path, r.outcome
FROM result r
JOIN test_case tc ON tc.id = r.test_case_id
WHERE r.run_id = ?
```

`updated_at` is written through `_fixed_width_isoformat`, not bare
`.isoformat()`: nothing compares it lexicographically today, and the moment
something does — "which section changed last" is the obvious next question — a
variable-width TEXT timestamp is the exact trap D27/D33 already paid for once.

**No index on `test_case.file_path`, and this is deliberate.** The exploration
verified the access path: `_SELECT_RUN_CASE_OUTCOMES` needs every result for one
`run_id` (served by `idx_result_run_id`) joined to `test_case` by its `INTEGER
PRIMARY KEY`. Classification happens in `derive_section`, not in SQL, so no
prefix range scan exists to index. An index would only serve a cross-run "all
history under section X" query, which nothing in this change builds; adding it
now would be an index whose justification is a query that does not exist.

`get_run_case_outcomes` is **not paginated**, unlike every other list method. It
is an aggregate input, not a response: its cardinality is the run's result count
and its projection is two short columns. `get_results` already reads a run
unpaginated for the same reason. Consequence, accepted and recorded: a run with
tens of thousands of results materialises that many small tuples for one
request.

Port and both adapters move in **one commit** — a `Protocol` method that lands
without both breaks `mypy --strict` at every call site.

### D87 — The HTTP surface: a section name is never a path segment

D54 decided that a value which may contain `/` cannot ride in a path segment: an
encoded slash is decoded before any route matcher runs. A section name is
user-chosen text and may contain one. That reasoning transfers verbatim, so the
explore sketch's `PUT /config/sections/{name}` is **rejected** and the name
travels as a body field on write and as a query value on delete.

| Method and path | Tag | Answers |
| --- | --- | --- |
| `GET /api/v1/config/sections` | `read` | `200` `SectionListResponse` |
| `POST /api/v1/config/sections` | `write` | `201` created, `200` updated, `422` on any name/prefix rejection |
| `DELETE /api/v1/config/sections?name=<value>` | `write` | `204`, or `404 unknown_section` |
| `GET /api/v1/runs/{run_id}/sections` | `read` | `200` `RunSectionSummaryResponse`, or `404 unknown_run` |

`POST`-as-upsert rather than `PUT`: `POST /api/v1/runs` is already this project's
idempotent-by-key create-or-update returning `201`/`200`, and `PUT` on a
collection URL conventionally replaces the collection, which is not what this
does.

**All four live in one new module, `service/routes/sections.py`,** mounted by
`create_app` with the same `prefix="/api/v1"` as every other router. Splitting
the aggregate into `routes/read.py` was rejected: it would put the derivation
two modules away from the definitions it derives against, and the four routes
share the namespace constant, the definition loader and the rejection types.

`TEST_SECTIONS_NAMESPACE = "test_sections"` is a module constant in that file.
The namespace string is service vocabulary; the store takes it as a parameter
and attaches no meaning to it.

Two obligations the new routes inherit automatically, named so they are not
rediscovered: `api-interface-document`'s drift check fails in both directions
unless all four operations are hand-written into `service/openapi/v1.yaml`, and
`test_read_only_surface.py` asserts every `read`-tagged path has a binding-table
entry, so the two new `read` paths need one each. The two `write` paths need
none, and tagging either of them `read` makes that harness fail — which is the
falsifier, not an accident.

**One cheap typing improvement, taken only inside new code.**
`request.app.state.store` resolves to `Any` at every existing call site (a known
latent gap). The new handlers bind it once as
`store: ExecutionStore = request.app.state.store`, which restores checking
inside the new module without touching a single existing route. Not expanded
further; the existing sites stay as they are.

### D88 — Section definitions are read from the store on every request, never cached

| Option | Verdict |
| --- | --- |
| **Read on each request** | **Chosen** |
| Cache in `app.state`, invalidate on write | Rejected. Correctness depends on every write path remembering to invalidate, and it is wrong by construction the moment a second process shares the SQLite file — the deployment shape this project has not ruled out |
| TTL cache | Rejected. "Takes effect immediately after an edit" becomes "takes effect within N seconds", which is a different requirement |
| Load once at process start | Rejected outright: an edit would need a restart, which is precisely the operational-configuration model the proposal rejected on the record |

The cost is one indexed equality query over a table bounded at 200 rows (D89),
against a request that already reads every result row of a run. Caching would
trade a correctness obligation for a saving too small to measure.

### D89 — One shape per rejection kind; four new kinds and one bound

`service/errors.py` asks for one shape per rejection **kind**, not per route:

| Class | Status | `error` | Raised when |
| --- | --- | --- | --- |
| `InvalidSectionNameError` | `422` | `invalid_section_name` | Empty after `strip()`, or over `SECTION_NAME_MAX_CHARS` |
| `ReservedSectionNameError` | `422` | `reserved_section_name` | `name.strip().casefold() == UNASSIGNED` |
| `InvalidSectionPrefixError` | `422` | `invalid_section_prefix` | Empty after `strip()`, or over `SECTION_PREFIX_MAX_CHARS` |
| `UnknownSectionError` | `404` | `unknown_section` | `DELETE` for a name that is not stored |
| `TooManySectionsError` | `422` | `too_many_sections` | A **new** name would exceed `MAX_SECTIONS` |
| `UnreadableSettingError` | `500` | `unreadable_setting` | A stored `value` fails its namespace's model (D83) |

"Reserved" is a distinct kind from "malformed": the client fixes them
differently, and collapsing both into one code would make a client guess. Both
are `422` because both are the same fact about the request's content — a `409`
would introduce a status class this service has no precedent for.
`UnreadableSettingError` is a `500` routed through the same body builder; that
widens `RejectionError` from "the client is wrong" to "a named failure with one
shape", which is strictly better than a traceback and needs no second mechanism.

`unassigned` is matched **case-insensitively** with `casefold()`, so `Unassigned`
cannot collide with the bucket either. Names are stored and matched
**case-sensitively** thereafter: `Billing` and `billing` are two sections, in the
same way `file_path` comparison is case-sensitive.

**The three bounds, derived rather than guessed:**

| Constant | Value | Derivation |
| --- | --- | --- |
| `SECTION_NAME_MAX_CHARS` | `120` | `LIST_COMMIT_SUBJECT_CHARS`'s display width — a section name is a label read in a list, the same class of value |
| `SECTION_PREFIX_MAX_CHARS` | `1024` | `MAX_IDENTITY_CHARS`, already argued in this codebase as the bound on a path-shaped client value |
| `MAX_SECTIONS` | `200` | `MAX_PAGE_ITEMS`. The summary response carries one entry per section and is not paginated, so the cap on stored sections **is** the bound on that response |

**A count cap is not the overlap validation this design rejected.** Overlap
validation would refuse a legitimate broad-then-narrow edit; a count cap refuses
only unbounded growth of a table any unauthenticated caller can write to. It is
enforced in the route as check-then-act over the definitions it already loaded
— honest about being a race under concurrent writers, and acceptable because it
is an ergonomic and response-size guard, not a security invariant. Updating an
existing name is never refused by it.

### D90 — No ADR, no capability flag, and four slices

| Decision | Reversal cost | Verdict |
| --- | --- | --- |
| The `user_setting` table and its namespace shape | Drop one table, delete one module and one route file. Well inside a sprint | design note |
| Schema version `3` | ADR-0013 already decided the policy and made the bump a checklist item; restating it would be a second copy | design note, manifest entry |
| Read-time derivation | `derive_presentation`'s precedent, not a new position | design note |
| Every other decision above | One module, one route, one constant | design note |

**No new ADR.** CLAUDE.md's filter is reversal cost above a sprint, and nothing
here reaches it. ADR-0003, ADR-0005, ADR-0006, ADR-0008, ADR-0009, ADR-0012 and
ADR-0013 are referenced and relied on, never re-argued.

**No new `GET /api/v1/capabilities` flag.** That endpoint answers `dict[str,
bool]` and exists to gate a client's behaviour. Nothing branches on "does this
server have sections": a client that asks for them gets `200` from a server that
has them and `404` from one that does not, and D39 already made that `404` the
answer a client reads rather than a transport failure. A flag nothing branches
on advertises a gate that does not exist.

---

## Data Flow

```
  WRITE (definitions)
  ─────────────────────────────────────────────────────────────────────────
  POST /api/v1/config/sections   {name, prefix}
        │  name: strip → non-empty → ≤120 → not casefold("unassigned")  (D89)
        │  prefix: strip → non-empty → ≤1024 → normalize_prefix()       (D84)
        │  count check against MAX_SECTIONS for a NEW name              (D89)
        ▼
  SectionValue(prefix=...).model_dump_json()      ← Pydantic ends here   (D83)
        ▼
  store.upsert_setting("test_sections", name, value=..., updated_at=now) (D86)
        │  probe → INSERT ... ON CONFLICT DO UPDATE, one BEGIN IMMEDIATE
        ▼
  201 SectionResponse (created) | 200 SectionResponse (updated)

  READ (the aggregate)
  ─────────────────────────────────────────────────────────────────────────
  GET /api/v1/runs/{run_id}/sections
        │  store.get_execution(run_id) is None → 404 UnknownRunError
        ▼
  store.list_settings("test_sections")     ← every request, never cached (D88)
        │  SectionValue.model_validate_json(row.value)                   (D83)
        │  → tuple[SectionDefinition, ...]   ← Pydantic ends here
        ▼
  store.get_run_case_outcomes(run_id)  →  [(file_path, outcome), ...]    (D86)
        ▼
  summarize_sections(case_outcomes, definitions)          pure core      (D85)
        │  derive_section: longest prefix wins → name | UNASSIGNED       (D84)
        │  passing = passed+xfailed ; measured = all but skipped
        │  pass_percentage = round(100*passing/measured, 1) | None
        ▼
  RunSectionSummaryResponse  ── items alphabetical, unassigned beside them
                                built field by field, never from_attributes
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `packages/vantage/src/vantage/storage/schema.sql` | Modify | `user_setting`; header count ten → eleven; stamp `'2'` → `'3'` (D82) |
| `packages/vantage/src/vantage/storage/connection.py` | Modify | `_SCHEMA_VERSION = 3` (D82) |
| `packages/vantage/src/vantage/core/ports/storage.py` | Modify | `UserSetting`; four `Protocol` methods (D83, D86) |
| `packages/vantage/src/vantage/core/domain/sections.py` | **Create** | `UNASSIGNED`, bounds, `SectionDefinition`, `normalize_prefix`, `derive_section`, `SectionSummary`, `RunSectionSummary`, `summarize_sections` (D84, D85) |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Modify | Four SQL constants and their methods (D86) |
| `packages/vantage/src/vantage/storage/memory.py` | Modify | The same four, second mechanism: one `dict[tuple[str, str], UserSetting]`, `sorted()` mirroring `ORDER BY key` |
| `packages/vantage/src/vantage/service/routes/sections.py` | **Create** | The four routes, the namespace constant, the definition loader (D87) |
| `packages/vantage/src/vantage/service/schemas.py` | Modify | `SectionValue`, `SectionUpsertRequest`, `SectionResponse`, `SectionListResponse`, `SectionSummaryResponse`, `RunSectionSummaryResponse` |
| `packages/vantage/src/vantage/service/errors.py` | Modify | Six new classes and their `__all__` entries (D89) |
| `packages/vantage/src/vantage/service/app.py` | Modify | One `include_router` line (D87) |
| `packages/vantage/src/vantage/service/openapi/v1.yaml` | Modify | Four hand-written operations, `read`/`write` tagged (D87) |
| `packages/vantage/tests/vantage_port_contract.py` | Modify | Settings round-trip, upsert/delete booleans, ordering, `get_run_case_outcomes`, both adapters |
| `packages/vantage/tests/test_read_only_surface.py` | Modify | Binding-table entries for the two new `read` paths (D87) |
| `docs/schema-manifest.md` | Modify | `user_setting` section; counts; the `meta` stamp value (D82) |
| `packages/pytest-vantage/**` | **Untouched** | Must show an empty diff (RQ-24, ADR-0009) |

## Interfaces / Contracts

```
GET /api/v1/config/sections
→ 200 {"items": [{"name": "Billing", "prefix": "tests/billing/"},
                 {"name": "Checkout", "prefix": "tests/checkout/"}]}

POST /api/v1/config/sections     {"name": "Checkout", "prefix": "tests/checkout"}
→ 201 {"name": "Checkout", "prefix": "tests/checkout/"}     (trailing / coerced)
→ 200 {"name": "Checkout", "prefix": "tests/checkout/"}     (name already stored)
→ 422 {"error": "reserved_section_name", "detail": "...", "fields": ["name"]}
→ 422 {"error": "invalid_section_name" | "invalid_section_prefix"
                | "too_many_sections", ...}

DELETE /api/v1/config/sections?name=Checkout
→ 204  (its tests fall into `unassigned` on the next read, across all history)
→ 404 {"error": "unknown_section", "detail": "...", "fields": []}

GET /api/v1/runs/{run_id}/sections
→ 200 {
    "items": [
      {"name": "Billing",  "total": 100, "measured": 90, "passing": 85,
       "pass_percentage": 94.4},
      {"name": "Checkout", "total":   0, "measured":  0, "passing":  0,
       "pass_percentage": null}
    ],
    "unassigned": {"name": "unassigned", "total": 12, "measured": 12,
                   "passing": 12, "pass_percentage": 100.0}
  }
→ 404 {"error": "unknown_run", ...}
```

The first `items` entry is the proposal's worked example: 80 passed + 5 xfailed
over 80 + 3 failed + 5 xfailed + 2 xpassed, with 10 skipped excluded from
`measured` and present in `total`.

The stored `value` for that section, exactly:

```json
{"prefix": "tests/billing/"}
```

The name is **not** repeated inside the value — it is already the row's `key`,
and two encodings of one fact drift.

## Testing Strategy

Strict TDD, RED first. New tests carry no `req` marker; each names its
capability and scenario in its docstring, which is what `grep` has to find. The
three requirements genuinely touched (RQ-24, RQ-26, RQ-29) are verified by
guards that already exist.

| Layer | What | Approach |
| --- | --- | --- |
| Unit (core) | `normalize_prefix`: coercion, idempotence, `tests/SectA` never matching `tests/SectAlpha/test_x.py` | Pure function, no fixtures |
| Unit (core) | `derive_section`: longest wins, no match → `UNASSIGNED`, equal-length tie broken alphabetically, case sensitivity | Pure function |
| Unit (core) | `summarize_sections`: the worked example yields `94.4`; `measured == 0` yields `None`; `total - measured` equals the skipped count; items alphabetical; `unassigned` present when empty | Pure function over hand-built pairs |
| Contract (both adapters) | Settings round-trip; `upsert_setting` True-then-False; `delete_setting` False for an absent key; `ORDER BY key`; `get_run_case_outcomes` pairs and empty run | `vantage_port_contract.py`, inherited by both stores |
| Test (storage) | A version-2 database is refused with a message naming both versions and the path; a fresh one opens and stamps `3` | ADR-0013 proven, not assumed |
| Integration (service) | `201` then `200` on the same name; `204` then `404` on delete; each rejection kind's exact `error` code; a `500` for an unparseable stored value | ASGI in-process, `InMemoryExecutionStore` injected |
| Integration (service) | Renaming re-groups history with **no write** to any run or result row | Store row counts and `get_results` before and after |
| Integration (service) | Section totals plus `unassigned` equal the run's result count, over a run with results in no section | Identity assertion, not a fixed expected list |
| Test (document) | Drift both directions for all four operations; both new `read` paths have a read-only binding | `test_interface_document.py`, `test_read_only_surface.py` |
| Inspection | `docs/schema-manifest.md` describes `user_setting` column for column, and the counts match `schema.sql` | RQ-29's established method |
| Analysis | `packages/pytest-vantage` diff is empty | RQ-24; the clean-environment install check is unchanged and still passes |

## Threat Matrix

`references/threat-matrix.md` rows are **all N/A**: this change spawns no
process, composes no command, automates no VCS or PR action, and classifies no
file by content.

| Boundary | Applicability |
| --- | --- |
| Documentation-like paths | **N/A** — nothing is classified by content |
| Git repository selection | **N/A** — no process is spawned |
| Commit / Push / PR commands | **N/A** — nothing inspects a tree, pushes, or composes a PR command |
| Executable-file classification | **N/A** — no file is executed |

Boundaries this change *does* add, recorded as notes rather than invented rows:

- **The first user-facing write surface beyond ingestion, behind no
  authentication.** Anyone who can route to the host can rewrite section
  definitions. Named, not solved here; `service/cli.py::warn_if_bound_wide`
  already documents the same exposure for ingestion. It must be answered before
  any non-local deployment.
- **Unbounded growth of a caller-writable table.** Response: `MAX_SECTIONS`
  (D89), which also bounds the summary response.
- **Client-chosen text reaching a rejection body.** Response: `fields` is built
  through the existing `_fields_from_errors`/`safe_segment` path only; no
  rejection detail ever interpolates a submitted name or prefix. RED test: a
  name containing CR/LF and `</script>` is rejected without appearing in the
  response body.
- **Client-chosen text reaching SQL.** Response: bound parameters only, never
  interpolation — the discipline `_resolve_test_case_ids` already follows. RED
  test: a quoting-shaped name round-trips byte-identically.
- **A write route mistagged `read`.** Response: `test_read_only_surface.py`'s
  existing falsifier makes that a red test.

## Migration / Rollout

**No migration, by decision.** ADR-0013 governs: a database stamped `2` is
refused at open with a message naming the version found, the version required
and the path. No DDL runs against it. Every developer database is recreated
once, at slice 1, which is why the schema bump leads the chain instead of
landing in its middle.

**The proposal's 450–600 line forecast is corrected upward here.** The review
budget counts authored additions plus deletions including tests, and this change
is test-heavy in exactly the places the project insists on two mechanisms (the
port contract runs against both adapters; the core arithmetic has its own unit
suite). `failure-capture` recorded the identical undercount in D80 — a ~390-line
forecast that measured 796 — for the same reason. The estimate below is derived
per file, not scaled from the proposal's.

| # | Slice | Est. | Depends on |
| --- | --- | --- | --- |
| 1 | `user_setting`, `_SCHEMA_VERSION = 3`, `UserSetting`, the four port methods, both adapters, port-contract tests, the refusal test, `docs/schema-manifest.md` (D82, D83, D86) | ~280 | — |
| 2 | `core/domain/sections.py` whole, with its unit suite. Pure, no I/O, no dependency on slice 1 (D84, D85) | ~250 | — |
| 3 | Definitions API: `routes/sections.py`'s three CRUD routes, the schemas, the six rejection types, `v1.yaml`, the read-only binding, route tests (D87, D89) | ~330 | 1 |
| 4 | The aggregate: `GET /runs/{run_id}/sections`, its response models, `v1.yaml`, its read-only binding, the identity and worked-example tests (D85, D87) | ~230 | 1, 2, 3 |

~1,090 lines across four slices; none exceeds 400.
`chain_strategy: feature-branch-chain`; rollback in reverse chain order.

```
Decision needed before apply: Yes
Chained PRs recommended: Yes
400-line budget risk: High
```

Slice 2 is independent and may land first if that is more convenient; slices 3
and 4 cannot precede 1, because a route cannot call a port method that does not
exist without breaking `mypy --strict`. Those are dependencies, not preferences.

**Rollback.** Per slice, a branch revert. Reverting slice 1 returns
`_SCHEMA_VERSION` to `2`, and anyone who already opened a version-3 database
recreates it — exactly what ADR-0013 prescribes, in the direction it also
covers. The table is additive and nothing else reads it, so no other data is
touched.

## Open Questions

None blocks `sdd-tasks`.

- [ ] Authentication in front of the write surface. Named as the change's
      highest risk; solved elsewhere, before any non-local deployment.
- [ ] Deployment documentation for "recreate the database". The refusal message
      is the only guidance that exists today.
- [ ] A cross-run "all history under section X" query is out of scope, and is
      the query that would justify an index on `test_case.file_path` (D86).
- [ ] `updated_at` is stored and not published (D82). The first scenario asking
      "which section changed last" adds a response field, not a column.
- [ ] `request.app.state.store` remains `Any` at every pre-existing route call
      site. The new module annotates its own binding (D87); widening the fix is
      a separate change.
