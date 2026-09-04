# Verification Report: run-metadata-capture (re-verification)

**Phase:** `sdd-verify` · **Date:** 2026-09-04 · **Verdict: PASS WITH WARNINGS**
**Tip verified:** `ft/run-metadata-capture-12-verify-fixups` @ `afad467c`, clean worktree.
**Previous verdict:** FAIL (2 critical, 4 warning, 4 suggestion) @ `339f5ca`.
**Chain:** 20 PRs (#88-#107), strictly linear (`339f5ca` confirmed ancestor of `afad467`), nothing merged.
**Artifact store:** hybrid — also saved to Engram as `sdd/run-metadata-capture/verify-report` (obs 145, updated in place).

| | |
| --- | --- |
| Blockers | **0** |
| Findings | 0 critical · 3 warning (**all pre-existing**) · 4 suggestion |
| Requirements | **15 / 15** |
| Scenarios | **33 / 33** |
| Tasks | 75 / 75 |
| Tests | **746 passed**, 0 failed, 0 skipped |

## Gates

| Gate | Command | Result |
| --- | --- | --- |
| Tests | `uv run pytest -q` | 746 passed, 12 expected warnings, exit 0 |
| Types | `uv run mypy .` | Success, 94 source files, exit 0 |
| Lint | `uv run ruff check .` | All checks passed |
| Format | `uv run ruff format --check .` | 94 files already formatted |
| Dependencies | `uv run deptry .` | No issues, 93 files |
| Coverage | — | Not measured, deliberately (`coverage_threshold: 0`) |

Previous tip measured 739 tests; delta **+7**, matching the remediation slice's claim of 7 added and 0 removed.

**Marker filters still fail closed:**

| Filter | Selected | Deselected |
| --- | ---: | ---: |
| `req(id="RQ-2")` | 7 | 739 |
| `req(id="RQ-24")` | 4 | 742 |
| `req(id="RQ-25")` | 5 | 741 |
| `req(id="RQ-29")` | 1 | 745 |
| `req(id="RQ-12")` | 2 | 744 |
| `req(id="RQ-999")` *(negative control)* | **0** | 746 |

---

## Blocker re-verification

### CRITICAL-1 — NUL byte crashes the session → **CLOSED**

Verified independently, not accepted on report.

**The catch was genuinely load-bearing.** `Path.resolve()` on a NUL-bearing path still raises
`ValueError` on 3.13.15, and `isinstance(e, (OSError, RuntimeError))` is `False` for it. The
previously enumerated tuple did not cover it.

**The fix holds at both boundaries:**

- `resolve_declared_path(root, "a\0b.json")` → `None`, no traceback.
- `read_declaration` refuses the declaration outright with exactly one `VantageWarning`.
- End-to-end: a real pytest session with a NUL-bearing declaration exits 0, `INTERNALERROR`
  absent. The previous reproduction was `INTERNALERROR`, return code 3, zero tests run.
- Unchanged rejection paths still work; containment suite 11/11.

**New-defect assessment of the bare `except Exception`** — the specific concern raised:

- **`KeyboardInterrupt` and `SystemExit` derive from `BaseException`, not `Exception`, and were
  confirmed by injection to still propagate.** Ctrl-C during a session is not swallowed. This is
  the property that would have made the change dangerous, and it holds.
- The `try` block holds `Path.resolve`, `is_relative_to` and `is_file` only — no resource is
  held, so broad catching leaks nothing.
- **Accepted cost:** a programming bug inside the block is now swallowed, confirmed by
  injection. It degrades to `None`, which `_read_declared_file` converts into a marked
  `path_rejected` / `not_found` status row — observable and auditable, not silent. Recorded as
  SUGGESTION-C, not a defect.

### CRITICAL-2 — the filter did not use its index → **CLOSED**

`EXPLAIN QUERY PLAN` run against the **production query object imported from source**
(`vantage.storage.sqlite_store._LIST_RUNS_BY_METADATA`), never a transcription, on a database
created through the production `SqliteExecutionStore` path.

Plan — identical on an empty database, at 5,000 runs / 15,200 metadata rows, and after `ANALYZE`:

```text
SEARCH run USING INDEX sqlite_autoindex_run_1 (id=?)
LIST SUBQUERY 1
SEARCH rm USING INDEX idx_run_metadata_key_value (key=? AND value=?)
USE TEMP B-TREE FOR ORDER BY
```

The outer `SCAN run` is gone and the purpose-built index is seeked on the full `(key, value)`
pair. The horizon query is unchanged and still correct.

**The regression test discriminates, and that was proved rather than assumed.** Reverting the
query to its `WHERE EXISTS` form reproduces the previous report's bad plan exactly, and
`test_list_runs_by_metadata_uses_the_key_value_index` fails on it and passes on the current
form. The test interpolates the imported production constant, so it cannot drift from what the
system runs.

**Row equivalence and duplicates** — the specific concern raised:

- **1,020 `(query, params)` pairs** compared between the new `IN (SELECT …)` and the old
  correlated `EXISTS`, across 3 populated keys, 50 values each, an absent key, an absent value,
  and 4 limit/offset combinations. **600 pairs returned non-empty; 18,450 rows; 0 mismatches.**
- **The non-empty assertion was not decoration.** An earlier attempt at this check silently
  seeded 0 metadata rows — an `INSERT OR IGNORE` swallowing a `NOT NULL` violation on
  `source_file` — and would have reported a vacuous 0-mismatch pass over an empty table.
- **No duplicates:** 15,000 rows across 150 filters, 0 repeated run ids. The enabling invariant
  was measured directly — the maximum number of `run_metadata` rows sharing a `(run_id, key)`
  pair is **1**, which is `PRIMARY KEY (run_id, key)` doing exactly the work the absent
  `DISTINCT` relies on.
- **Status filtering preserved:** with 200 `value IS NULL` rows present, all four probe values
  returned 0 rows. SQL `NULL` still never equals a bound string.

### WARNING-1 — `MAX_METADATA_KEY_CHARS` enforced nowhere → **CLOSED**

Resolved by the route the user chose: enforced in `read_declaration`, no new status class, no
schema change. Verified behaviourally at the boundary:

| Declared key length | `read_declaration` | Warnings |
| ---: | --- | ---: |
| 1024 (exactly `MAX`) | accepted | 0 |
| 1025 (`MAX` + 1) | `None`, whole declaration refused | 1 |
| 2000 | `None`, whole declaration refused | 1 |

Off-by-one correct (`> MAX`, not `>= MAX`). Cross-package sync measured: plugin
`MAX_DECLARED_KEY_CHARS` = 1024 = server `MAX_METADATA_KEY_CHARS`, pinned by a test-only import
in `test_the_mirrored_key_char_bound_matches_the_server`. The constant now gates real behaviour.

### The two "already accurate, left unedited" prose claims → **VERIFIED TRUE**

Both read at the current tip rather than taken on trust.

| Location | Verdict |
| --- | --- |
| `core/ports/storage.py` — `list_runs` docstring | **True at this tip**, both halves confirmed by the two plan runs |
| `storage/memory.py` — metadata-filter comment | **True at this tip**; the NULL-exclusion half confirmed by the 0-row result over 200 `value IS NULL` rows |

**Nuance worth recording**, because it is not quite what the apply note says: the port docstring
was **false at `339f5ca` and made true by the CRITICAL-2 rewrite itself**, rather than having
been accurate all along. The end state is correct either way and no edit was needed, so this is
a wording imprecision in the apply note, not a skipped correction.

---

## Spec compliance — 15/15 requirements, 33/33 scenarios

Counts measured by read-back of the five spec files. Both requirements that carried a violated
MUST at `339f5ca` are now satisfied with runtime evidence.

| Capability | Req | Scen | Verdict |
| --- | ---: | ---: | --- |
| `run-metadata` | 8 | 14 | Compliant — **R3 was VIOLATED, now COMPLIANT** (containment 11/11 incl. NUL) |
| `session-ingestion` | 4 | 10 | Compliant |
| `opt-in-activation` | 1 | 3 | Compliant |
| `recording-schema` | 1 | 3 | Compliant — Inspection, bidirectional manifest/live diff |
| `history-read-api` | 1 | 3 | Compliant — **R1 was VIOLATED, now COMPLIANT** (plan test added) |

`run-metadata` R7 is **strengthened**: `test_replaying_metadata_with_a_different_value_does_not_backfill`
asserts the first value survives *and* differs from the second — a real no-backfill claim rather
than the idempotence tautology it replaces.

Live schema re-measured: **13 tables, 15 named indexes, 139 columns, `schema_version = 4`**.

## Correctness and coherence

| Area | Status |
| --- | --- |
| RQ-24 plugin boundary | Intact — deps are `["pytest>=8.0"]`; **0** `vantage.*` imports in plugin source; the two new server-constant imports are test-only |
| RQ-26 core purity | Intact — architecture test green |
| D97 never-raise | Restored; the docstring now argues the fail-closed **shape** rather than enumerating exception types |
| D100 filter served by the index | Now true in implementation **and** prose |
| SUGGESTION-2 refactor | Correct — the deliberate asymmetry against `runs.py`'s wider allow-list is preserved and documented |

## TDD compliance — 7/7

RED was independently **reproduced**, not merely reported: reverting the query yields the exact
bad plan and the new test fails on it. Key-length bound triangulated at 1024/1025/2000.

## Assertion quality

All 7 tests added by the remediation slice audited. No tautologies, no ghost loops, no assertion
without a production call. The two strongest assert discriminating properties — the plan test
asserts a positive (index present) **and** a negative (PK autoindex absent); the no-backfill test
asserts both equality to the first write and inequality to the second.

---

## Findings

### Warnings — 3, all pre-existing, none blocking

**WARNING-2 — `docs/schema-manifest.md` historical block is stale.** Dated 2026-08-15, states
10 tables / 13 indexes / 125 columns against a live 13 / 15 / 139. Already stale before this
change (it omits `user_setting`). The manifest **body** is current and machine-checked
bidirectionally, which is stronger evidence than the prose, so RQ-29's Inspection deliverable is
satisfied. *Routing:* relabel as a dated historical record. Belongs on `main`.

**WARNING-3 — RQ-25 2% budget contradiction.** `version-control-context/spec.md:152-159` claims
results are "still inside RQ-25's 2% budget in every one of the four measured cases" while
recording 4.11% and 4.17% — arithmetically false. This change's own spec records the
contradiction honestly rather than repeating it. *Routing:* human decision — correct the claim,
and decide whether RQ-25's budget becomes a capability requirement (reusing the existing ID,
minting no new `RQ-xx`) or stops being cited. Outside this chain.

**WARNING-4 — `_StubServer` latent flake.** `git diff main...afad467 -- test_failure_paths.py`
is **empty** — untouched by the entire chain. Did not fire in the full suite nor in 5 consecutive
dedicated runs (41 passed each). Structural cause unchanged: `_accept_loop` spawns one untracked
daemon thread per connection and `__exit__` joins only the accept loop, so the captured list
records handler-completion order rather than connection order. *Routing:* fix `_StubServer`, not
the test. Belongs on `main`.

### Suggestions — 4, none blocking

**SUGGESTION-A — the plan test does not pin the absence of an outer scan.** It asserts the right
index present and the PK autoindex absent, but not that `SCAN run` is absent. A future rewrite
reaching the right index while reintroducing a full outer scan would pass. `assert "SCAN run"
not in plan_text` closes it.

**SUGGESTION-B — the rewrite is a measured two-sided tradeoff, and the design note claims only
the upside.** The new plan ends in `USE TEMP B-TREE FOR ORDER BY` (full sort of the matched set);
the old one ended in `USE TEMP B-TREE FOR LAST TERM OF ORDER BY` and streamed in
`idx_run_started_at` order with early `LIMIT` termination. Measured at 20,000 runs:

| Case | New | Old |
| --- | ---: | ---: |
| **Selective** filter (34 of 20,000 match) | **0.06 ms** | 14.73 ms |
| Degenerate filter (20,000 of 20,000 match) | 7.35 ms | 0.05 ms |

The tradeoff is **correct** — cost now scales with matches rather than with total runs, which is
the case the index exists for and the case the MUST is about — and 7.35 ms is not a defect. But
the design note states only the win and is worth completing.

**SUGGESTION-C — `except Exception` is observable but not diagnosable.** A genuine bug inside the
block degrades to a marked status rather than a traceback. Right production behaviour for a
security boundary, and auditable, but a debug-level log of the swallowed exception would make
such a bug diagnosable without weakening the fail-closed posture.

**SUGGESTION-D — scenario traceability annotation is inconsistent.** `session-ingestion` and
`history-read-api` tests carry `*(capability → Requirement → Scenario)*` docstring annotations;
`run-metadata`, `opt-in-activation` and `recording-schema` tests carry none. Coverage is real
either way — confirmed here by named tests — but the mechanical grep-to-scenario audit that works
for two capabilities does not work for the other three.

---

## Verdict

**PASS WITH WARNINGS.** Archive-ready.

Both blockers are independently confirmed closed **against the production artefacts, not the
report**: CRITICAL-2's plan was proved on the query object imported from source and shown to fail
on the reverted form, with row equivalence checked over a dataset verified non-empty first;
CRITICAL-1's fix was proved end-to-end and shown not to swallow `KeyboardInterrupt` or
`SystemExit`.

15/15 requirements and 33/33 scenarios compliant, 75/75 tasks complete, every gate green at 746
passed. The three remaining warnings are pre-existing, are not attributable to this change, and
do not block archive of this chain.
