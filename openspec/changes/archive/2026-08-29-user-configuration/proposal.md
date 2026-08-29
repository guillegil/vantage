# Proposal: User-configuration surface (test sections as first tenant)

## Intent

Vantage has no user-facing configuration and no aggregate anywhere in its API.
A user cannot group tests or see how a group is doing. This change adds a
server-persisted, user-managed preference surface and lands **test sections** as
its first tenant, not its whole shape. A section maps a test file-path prefix to
a name; the headline outcome is a pass percentage per section for a run.

## Scope

### In scope

- `user_setting(namespace, key, value, updated_at)`, PK `(namespace, key)`; `_SCHEMA_VERSION` 2 → 3
- Section read/upsert/delete under `namespace='test_sections'`
- Pure core `derive_section` (longest prefix wins) and per-section counts
- `GET /api/v1/runs/{run_id}/sections`, with an always-present `unassigned` bucket
- `docs/schema-manifest.md` traceability entries; port parity in both stores

### Out of scope

- Any change to `pytest-vantage` (RQ-24, ADR-9) — sections are read-side only
- The web UI (ADR-8); this delivers the server capability it will consume
- Authentication, deployment docs, migrations, a generic configuration framework
- An index on `test_case.file_path` — no queried path needs one yet

## Decisions (fixed inputs, recorded not reopened)

| Decision | Rationale |
|---|---|
| Derived at read time, never stored on a row | A section is current taxonomy, not history. Renaming re-groups everything instantly, no backfill. Mirrors `derive_presentation`. |
| Server-side, not in the browser | A frontend has no persistence surviving a device change, and `MAX_PAGE_ITEMS = 200` with zero API aggregates makes the percentage uncomputable client-side. |
| **User data, not operational configuration** | A server-side TOML needing shell access and a restart was proposed and rejected. Host/port/database are set once by whoever deploys; sections change weekly, by a different person, for a different reason. |
| A SQLite table, not a server-written file | A file has every concurrency and atomicity problem of a database and none of its guarantees; SQLite is already here (ADR-6). |
| Namespace + JSON value | Generic storage, specific validation: each namespace's `value` is validated by an ordinary Pydantic model in `vantage.service`. No CRUD generator, no schema-in-JSON layer. |
| Longest-prefix-wins, no write-time overlap check | `tests/` and `tests/SectA/` coexisting is the broad-then-narrow editing workflow, not an error. |
| `unassigned` always on the wire, reserved name | Sections plus unassigned must equal the run's result count, so a client can verify the percentages do not lie by omission. |
| Bump to schema version 3 and refuse mismatches | ADR-0013 decided this policy and made the bump a checklist item. **No new ADR**; the manifest is where it is recorded. |

## Pass percentage

Numerator `passed + xfailed`. Denominator `passed + failed + error + xfailed +
xpassed`. `skipped` is excluded from the denominator entirely — a skipped test
is not a failure and must not drag the percentage down. `xpassed` counts in the
denominator but not as a pass: a test expected to fail that did not is a signal
worth surfacing, not one to absorb silently.

Worked example — 80 passed, 5 xfailed, 2 xpassed, 3 failed, 10 skipped →
85 / 90 = **94.4 %**.

## Capabilities

### New Capabilities

- `user-configuration`: namespaced, server-persisted user preferences — list, upsert, delete, reserved names, persistence across restart
- `test-sections`: section definitions, read-time derivation, the unassigned bucket, and the per-run pass-percentage aggregate

### Modified Capabilities

- `recording-schema`: schema version 3 and the `user_setting` table join RQ-29's completeness and refusal obligations

## ADR

None. The table shape is revertible well inside a sprint — drop the table,
delete one module — which is CLAUDE.md's own filter. Revisit when a second
namespace lands and the shape stops being provisional.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `core/domain/sections.py` | New | Pure derivation and counting, stdlib only (RQ-26) |
| `core/ports/storage.py` | Modified | New `ExecutionStore` protocol methods |
| `storage/sqlite_store.py`, `storage/memory.py` | Modified | Parity enforced by `vantage_port_contract.py` |
| `storage/schema.sql`, `storage/connection.py` | Modified | New table; `_SCHEMA_VERSION` 3 |
| `service/schemas.py`, `errors.py`, `routes/` | Modified | New router, response and rejection shapes |
| `docs/schema-manifest.md` | Modified | New table's columns |
| `packages/pytest-vantage` | Untouched | Must show an empty diff |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| First user-facing write surface sits behind no authentication | High | Named, not solved here; `service/cli.py:warn_if_bound_wide` already documents the exposure. Must be answered before any non-local deployment. |
| Operators are told to recreate their database, and no deployment documentation exists to say so | High | The refusal message names version found, version required and the path; written guidance deferred |
| The namespace shape generalises past its one tenant | Medium | Validation stays namespace-specific; no framework is built |
| Historical runs whose paths match no current section | Low | They land in `unassigned` automatically — the same accepted trade-off as ADR-0012 |

## Rollback

Revert the commit. The table is additive and nothing else reads it, so no other
data is touched. `_SCHEMA_VERSION` returns to 2; anyone who already opened a
version-3 database recreates it, exactly as ADR-0013 prescribes.

## Dependencies

None external. Builds only on ADR-0006 (stdlib sqlite3), ADR-0013 (refusal) and
`derive_presentation`.

## Changed-line forecast

Estimated 450–600 changed lines against the 400-line review budget — **high**.
Chained slices recommended: (1) schema, table and store parity; (2) core
derivation and counting; (3) service routes and interface document.

## Success Criteria

- [ ] A section survives a server restart and reads back identically
- [ ] Renaming a section re-groups all history with no backfill and no write to any run or result row
- [ ] Section totals plus `unassigned` equal the run's total result count for every run
- [ ] The worked example yields 94.4 %
- [ ] `unassigned` is rejected as a user-supplied section name
- [ ] Both stores satisfy the port contract and the AST architecture test passes
- [ ] `packages/pytest-vantage` has an empty diff

## Resolved product questions

The proposal phase surfaced five product unknowns. All five are now decided and
are fixed inputs for spec, design and apply — none of them may be re-derived.

| Question | Decision | Reasoning |
|---|---|---|
| Deleting a section | Immediate and silent server-side. Its tests fall into `unassigned` on the next read, across all history at once. | The direct consequence of read-time derivation. A confirmation dialog is a `web/` concern, not a server obligation. |
| Prefix interpretation | **Coerce a trailing `/` on write; match case-sensitively and byte-exactly thereafter.** A user entering `tests/SectA` stores `tests/SectA/`. | Exact bytes with no coercion lets `tests/SectA` bleed into `tests/SectAlpha/test_x.py` — a silent mis-grouping that produces plausible-looking wrong numbers. Coercing the slash closes that without pretending Linux paths are case-insensitive. |
| Empty run (`0/0`) | `pass_percentage` is `null` on the wire when the denominator is zero. Never `0.0`, never `100.0`. | A zero reads as catastrophe and a hundred reads as success; both are lies about a run that measured nothing. `null` is the only value a client can render as "no data". |
| Section name constraints | Non-empty after stripping whitespace; case-sensitive; bounded length; `unassigned` reserved case-insensitively. | The name is the primary key within the namespace, so this is a data-shape rule, not styling. The reserved word is matched case-insensitively so `Unassigned` cannot collide with the bucket either. |
| Ordering in the summary response | Alphabetical by section name. `unassigned` is carried in its own field, outside the list. | Stable between runs, so the reader's eye learns the layout. The server does not editorialise about which section matters; a client holding the numbers can reorder freely. |
