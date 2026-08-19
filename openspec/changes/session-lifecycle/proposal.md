# Proposal: Session lifecycle — a run row exists while the session is still alive

## Intent

**No run row exists until the session ends.** `Recorder` sends exactly one POST,
from `pytest_sessionfinish`. A SIGKILL'd session therefore leaves *no row at all*,
so there is nothing to present, nothing to age, and nothing to correct later.

RQ-31.3 and RQ-44 are **the same structural gap, not two jobs**. So are two
criteria nobody noticed were dropped:

| Obligation | Status today |
| --- | --- |
| RQ-1.5 — a running session's entry already exists, start time, null end | **Missing from `openspec/specs/run-recording/spec.md`** |
| RQ-1.6 — a SIGKILL'd session's entry is present, start time, null end | **Missing** |
| RQ-31.3 — SIGKILL leaves start time, null end, **no interrupt reason** | **Missing** |
| RQ-44 — abandoned reads back as abandoned, not as running | No implementation |

RQ-1.5/1.6 and RQ-31.3 were never in the Milestone-1 delta either. `openspec/config.yaml`
forbids silently dropping a criterion, so restoring all three is part of this change,
not cleanup after it.

## Scope

### In Scope

- **Start-write.** `Recorder` implements `pytest_sessionstart` and POSTs the run to
  the existing `/api/v1/runs` with `finished_at: null`, no `results`. No new API
  version and no new request fields — that shape is already legal against
  `RunReport`/`SessionReport`.
- **Monotonic upsert (required regardless of every other choice).** `_INSERT_RUN`
  (`packages/vantage/src/vantage/storage/sqlite_store.py:51-57`) is
  `ON CONFLICT(id) DO NOTHING`. Once a start-write exists, the finish-write arrives
  with the same `id` and is **discarded** — `finished_at`, `exit_status`,
  `interrupted`, `interrupt_reason` and every result row lost without an error. It
  becomes `ON CONFLICT DO UPDATE` with a monotonic guard mirroring the `test_case`
  `MAX`/`CASE` pattern in the same file. **A stale or reordered start-write MUST
  never null out a recorded finish.**
- **`last_contact_at`**, a new nullable column (see below), maintained by a heartbeat.
- **`POST /api/v1/runs/{id}/heartbeat`**, its own narrow endpoint. Not a field on the
  existing envelope: there, `finished_at: null` cannot distinguish "still running"
  from "erase the finish already recorded".
- **Activity-driven beats** off `pytest_runtest_logreport` — already controller-only
  under xdist (RQ-12) and already fault-isolated.
- **A narrower failure mode for a failed heartbeat.** It MUST NOT inherit
  `boundary.fault_isolated`'s shared latch, which today disables every further hook
  on the instance. A server that blinks for one second must not cost someone 2,000
  results.
- **The grace period is measured from LAST CONTACT, never from the run's start.**
- Restoring RQ-1.5, RQ-1.6 and RQ-31.3 to the specs; correcting the two scenarios
  a start-write makes false; rewriting RQ-3's Analysis argument.

### Out of Scope

- **Any read endpoint.** Write side only. This change makes the *data* correct;
  RQ-31.3 is fully satisfied, RQ-44's data obligations are satisfied, and RQ-44's
  read-back criteria are demonstrated when the read API exists. That API deserves
  its own design.
- A stored `status`/`abandoned` column. RQ-44.4 forbids inventing a field for an
  end that never happened; abandonment is derived, and this change ships only the
  derivation helper the future read path will call.
- A daemon timer thread. See *Known gap*.
- A migration framework. ADR-5 refused one deliberately.

## Capabilities

### New Capabilities

- `session-liveness`: a run entry exists from session start; last contact is
  maintained while the session runs; abandonment is derived from last contact and a
  server-side grace period. Owns the heartbeat operation's wire contract.

### Modified Capabilities

- `run-recording`: **ADD** RQ-1.5, RQ-1.6, RQ-31.3 (restoring dropped criteria).
  **MODIFY** RQ-3.2 — `spec.md:98` asserts a truncated-in-transit report leaves
  *no run entry present*. True only because everything arrives in one POST today.
  With a start-write the row is already there, correctly, with a null `finished_at`.
  **MODIFY** RQ-3's Analysis argument (below).
- `session-ingestion`: **MODIFY** RQ-42.3 — same false assertion ("the run table
  stays empty"). New truth: a rejected *finish* report stores nothing **from that
  report**; a run entry created by an accepted earlier start-write legitimately
  remains, showing an unfinished run.
- `recording-schema`: **MODIFY** RQ-29 — `last_contact_at`, its index, and the
  existing-database policy.
- `recording-fault-tolerance`: **MODIFY** RQ-21 — a failed heartbeat warns once and
  keeps result accumulation alive, rather than latching the instance off.

## RQ-3's atomicity proof, not RQ-3 itself

RQ-3's text is about **atomicity, not transaction count** — a second transaction does
not violate it. But the *proof chosen for this repository* does assume one commit:
`run-recording/spec.md:73-92` argues from a single `BEGIN IMMEDIATE`…`COMMIT`, and
names `test_five_hundred_results_reach_storage_in_one_commit` as its premise. It even
names its own invalidator: *"any change that splits the session write across more than
one transaction."* This change is exactly that.

**Replacement:** the unit of atomicity becomes **each report**, not the session. Two
transactions per session (start, finish); each is one `BEGIN IMMEDIATE`…`COMMIT`, so a
SIGKILL still lands either side of a commit and there is still no third position. The
premise test is rewritten to assert **exactly one commit per accepted report** — one
for the start-write, one for the finish-write carrying all 500 result rows and the run
update — rather than one per session. The row-count assertions stay, for the same
reason they exist today.

## The schema change is the expensive part

| Item | Value |
| --- | --- |
| Column | `run.last_contact_at` |
| Type / nullability | `TEXT NULL` — ISO-8601 UTC, matching every other timestamp |
| Index | `idx_run_last_contact_at ON run (last_contact_at)`, created now per ADR-5's precedent for `idx_run_received_at` — takes the manifest from 13 indexes to 14 |
| Not a redefinition of `received_at` | Explicit beats overloading a column that means one thing in old rows and another in new ones |
| `docs/schema-manifest.md` | Must gain the column and the corrected index count — it is RQ-29's Inspection deliverable |

**This is the first schema change since the reset.** ADR-5 and RQ-29 say the schema is
created complete at first use and that no Phase 1 release alters an existing database.
RQ-29's second scenario is literal: *opening an existing database issues no
schema-altering statement*. `CREATE TABLE IF NOT EXISTS` will not add a column to a
Milestone-1 database, so every write referencing `last_contact_at` would fail with
`no such column`.

Three ways out, honestly:

| Option | Cost |
| --- | --- |
| i. Ship it and let old databases break | Silent, ugly failure. Rejected. |
| ii. Idempotent `ALTER TABLE … ADD COLUMN` | Violates RQ-29.2 literally and is step one of the migration framework ADR-5 refused. RQ-29 exists **because** having one available is what makes casual schema changes feel affordable. |
| iii. **Recommended** — bump `meta.schema_version` and **refuse** to open an older database with a message naming the version and telling the operator to recreate it | Refusing is not altering, so RQ-29.2 holds literally. `meta.schema_version` is the seam ADR-5 created for exactly this and has never been used. Pre-1.0, synthetic data only, no deployments. |

**Does this earn an ADR? Yes — but for (iii), not for the column.** Applying
`CLAUDE.md`'s reversal-cost filter honestly: a nullable, unread column is minutes to
revert and earns nothing. The *policy* — "Phase 1 evolves its schema by refusing older
databases rather than migrating them" — is what binds every later schema change, and
reversing it once users hold databases costs far more than a sprint. One ADR, in the
design phase, one PR, titled in imperative mood.

## Known gap — named, not solved

**No pytest hook fires during a single test's body.** `pytest_runtest_logreport` gives
one "alive as of test start" beat and then nothing until call/teardown land. One test
running six hours produces no beat and its run reads as abandoned while it is still
running. This is a **documented, accepted limitation mitigated by a generous grace
period** — not something this change covers.

The alternative, a daemon timer thread, would be the plugin's first concurrency: a
thread's uncaught exception never reaches pytest's hook machinery, so it needs a whole
new fault-isolation path, and proving it fires needs sleep-based tests across the
3.10–3.13 × xdist matrix — the exact flakiness RQ-3.1 already chose Analysis to avoid.
It stays available as a later, separate change.

**Rejected, recorded so nobody re-proposes it:** server-side inference with no client
beat. The plugin sends discrete short-lived POSTs (ADR-9), never a connection the
server could watch for disconnection. There is no signal to infer from.

## RQ-25 is not violated

"Exactly one HTTP request per session" is a **design choice, not a requirement**.
RQ-25 criterion 2 requires the request count to be **independent of the test count** —
not equal to one. A time- or activity-driven beat satisfies it: its count scales with
wall-clock duration. The binding constraint is criterion 1, ≤2% added wall-clock on
1,000 tests of ~10 ms — a ~10-second suite, which emits **zero or one** beat under any
sane interval. `result-capture/spec.md:17` ("sends no additional request per test")
also survives: a heartbeat is not per-test.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Modified | `_INSERT_RUN` → monotonic `ON CONFLICT DO UPDATE`; last-contact touch |
| `packages/vantage/src/vantage/storage/schema.sql` | Modified | `last_contact_at`, `idx_run_last_contact_at`, `schema_version` bump |
| `packages/vantage/src/vantage/storage/connection.py` | Modified | Refuse an older `schema_version` with a named message |
| `packages/vantage/src/vantage/service/routes/runs.py` | Modified | `POST /api/v1/runs/{id}/heartbeat` |
| `packages/vantage/src/vantage/service/schemas.py` | Modified | Heartbeat request/response models (Pydantic stays service-only) |
| `packages/vantage/src/vantage/core/` | Modified | Storage-port method for the touch; abandonment derivation (stdlib only, RQ-26) |
| `packages/pytest-vantage/src/pytest_vantage/recorder.py` | Modified | `pytest_sessionstart` start-write; activity-driven beat |
| `packages/pytest-vantage/src/pytest_vantage/boundary.py` | Modified | Non-latching failure mode for the heartbeat path |
| `docs/schema-manifest.md` | Modified | Column and index inventory |
| `docs/adr/NNNN-*.md` | New | The schema-evolution policy |

## Changed-line forecast vs the 500-line review budget

**Forecast: ~1,100 changed lines (±200). This exceeds 500 and is not being hidden.**
Delivered as a feature-branch chain of four slices, each with a clear start, finish,
verification and rollback:

| # | Slice | Est. | Independently deliverable? |
| --- | --- | --- | --- |
| 1 | Monotonic upsert + RQ-3 Analysis rewrite + RQ-3.2/RQ-42.3 `MODIFIED` deltas | ~200 | Yes — no behavioural change while only one write exists |
| 2 | Start-write: `pytest_sessionstart`, RQ-1.5/1.6, RQ-31.3 restored | ~300 | Yes — **depends on slice 1 for correctness** |
| 3 | Schema: `last_contact_at`, index, manifest, `schema_version` refusal, ADR | ~250 | Yes — column present, unpopulated |
| 4 | Heartbeat: endpoint, activity-driven beat, narrow failure mode, derivation helper | ~360 | Yes |

`400-line budget risk: High` (against the session's 500). Every slice sits under 500.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| A stale/reordered start-write nulls a recorded finish | Med | Monotonic guard is the slice-1 acceptance criterion, tested with an explicitly reordered pair — not an implementation detail |
| Slice 1 reverted alone, leaving slice 2's finish-writes silently dropped | Low | Chain order is load-bearing: revert 2 before 1, stated in the rollback plan |
| An old database meets the new column | Med | Refuse-and-name (iii); a Milestone-1 database is caught at open, not at write |
| A failed heartbeat costs the session's results | Low | Its own non-latching failure mode is a first-class deliverable, not a refinement |
| Grace period mis-set → a long suite reads as abandoned | Med | Measured from last contact, never from start; default derived from the beat interval, so it is bounded and independent of suite length |
| A single very long test reads as abandoned while running | Med | Accepted, documented limitation; generous grace period |
| Heartbeat traffic breaches RQ-25.1 | Low | Beat is a `time.monotonic()` comparison; zero beats fire on RQ-25's own ~10-second measured profile |

## Rollback Plan

Schema-affecting, so per slice, in reverse chain order:

- **Slice 4** — delete the route and the beat. `last_contact_at` stays behind, null,
  read by nothing. No data migration.
- **Slice 3** — revert `schema.sql`, the manifest and the `schema_version` bump. A
  database created by the shipped version carries one extra nullable column the
  reverted code never names; SQLite does not care. **No data loss, no `ALTER TABLE`.**
  The ADR is *superseded, never edited or deleted*, per `CLAUDE.md`.
- **Slice 2** — remove `pytest_sessionstart`. Rows already created by a start-write
  keep a null `finished_at` and read correctly as unfinished. No cleanup.
- **Slice 1** — revert to `ON CONFLICT DO NOTHING`. **Only safe once slice 2 is
  reverted**; doing it first resurrects the silent-drop bug against live start-writes.

Pre-1.0, synthetic data only, no deployment: rollback cost is bounded to this
repository.

## Dependencies

- None external. All of pytest, `urllib`, `sqlite3` and the existing FastAPI surface
  are already in the tree.
- Constraints held throughout: `pytest-vantage` takes pytest and the stdlib only
  (RQ-24, ADR-4); `vantage.core` is stdlib-only (RQ-26); Pydantic is service-only; the
  plugin never opens a database (ADR-9); recording is opt-in via the flag, never a
  config file (RQ-2); Python floor 3.10 — no `StrEnum`, no `datetime.UTC`.
- **No new `RQ-xx` identifiers.** New obligations are named by capability and
  scenario, per the 2026-08-18 decision.

## Success Criteria

- [ ] A SIGKILL'd session leaves a run entry with a start time, a null end time and
      **no interrupt reason** — RQ-31.3 fully satisfied, and restored to the spec.
- [ ] RQ-1.5 and RQ-1.6 are back in `run-recording/spec.md` with passing scenarios.
- [ ] A finish-write following a start-write applies in full; a reordered start-write
      never nulls a recorded finish.
- [ ] `last_contact_at` advances while a suite runs and stops when the process dies.
- [ ] A run whose last contact is older than the grace period derives as *abandoned*;
      a Ctrl-C run derives as *interrupted*; a fresh one derives as *running*. Grace
      is measured from last contact.
- [ ] A heartbeat failing mid-session still lets that session's results be recorded,
      with one warning.
- [ ] RQ-3.2 and RQ-42.3 carry explicit `MODIFIED` deltas — no scenario in
      `openspec/specs/` contradicts shipped behaviour.
- [ ] RQ-3's Analysis argument and its premise test describe per-report atomicity.
- [ ] `docs/schema-manifest.md` matches a freshly created schema (RQ-29 Inspection).
- [ ] An ADR records the schema-evolution policy, `Proposed` in the PR.
- [ ] Every slice lands under 500 changed lines.

## Proposal question round

Written rather than asked — this phase ran without an interactive channel. These are
product questions, not harness mechanics; **none blocks `sdd-spec`**, but each will be
answered by `sdd-design` if left alone. Correct any of them, or ask for a second round.

1. **Existing-database policy.** The proposal recommends (iii): refuse an older
   `schema_version` with a named message. Is a hard refusal acceptable operator
   behaviour pre-1.0, or should a Milestone-1 database keep opening read-only?
2. **Grace period default and configurability.** Proposed: a **server-side** default
   of ~15 minutes, expressed as a multiple of the beat interval so it stays bounded
   and independent of suite length. Configurable, or fixed? It belongs to the server —
   the plugin is not running any more, which is the entire point.
3. **Beat interval and its own timeout.** Proposed: ~30 s interval, ~2.0 s bounded
   timeout mirroring the existing `_MAX_CONNECT_TIMEOUT` preflight pattern rather than
   reusing the full `--vantage-timeout` used for the final report. Right numbers?
4. **Warning volume for the non-latching heartbeat mode.** RQ-37 already insists on
   "one warning, not one per test". Proposed: one warning per session for heartbeat
   failure, independent of the reporting-path latch. Confirm one, not one per beat.
5. **Slice 3 before slice 4?** Shipping the column unpopulated makes the schema/ADR
   decision reviewable on its own, away from the heartbeat mechanics. Acceptable, or
   should the schema land with the code that uses it?

## Answers to the question round — 2026-08-19

1. **Existing-database policy: (iii), refuse and name.** Bump `meta.schema_version` and
   refuse to open an older database, naming the version and telling the operator to
   recreate it. Refusing is not altering, so RQ-29.2 stays literally true and ADR-5 is
   untouched. Pre-1.0, synthetic data only, no deployments — the cost lands on nobody
   today, and the policy it sets is the thing the ADR exists to record. `ALTER TABLE …
   ADD COLUMN` was considered and rejected by name: it is step one of the migration
   framework ADR-5 refused, and RQ-29 exists precisely because having one available is
   what makes a schema change feel affordable.

2. **Grace period: configurable, server-side. Not a choice — RQ-44 states it.** The
   requirement reads "no report has arrived for it *within a configured grace period*".
   Configurable is the obligation, not a preference. ~15 minutes as the default,
   expressed as a multiple of the beat interval so it stays bounded and independent of
   suite length. It belongs to the server because the plugin is not running any more,
   which is the entire point of the requirement.

3. **Beat interval ~30 s, with its own ~2.0 s bounded timeout.** Accepted as proposed.
   The heartbeat must not reuse `--vantage-timeout`, which is sized for the final report:
   a beat that blocks for the report timeout would put that cost in the inner loop RQ-25
   protects. Mirroring `_MAX_CONNECT_TIMEOUT`'s existing preflight pattern is the right
   precedent. These numbers are a starting point and the design may move them with a
   stated reason; what is fixed is that the beat has a bound of its own.

4. **One warning per session for heartbeat failure.** Confirmed, and RQ-37 already
   requires this shape — "one warning, not one per test". A beat every 30 seconds across
   a two-hour suite is 240 chances to be noisy, which is exactly how an observability
   tool teaches people to ignore its output. Independent of the reporting-path latch, per
   the maintainer's decision that a failed heartbeat must not cost the session's results.

5. **Slice 3 before slice 4, as proposed.** The column ships unpopulated so the schema
   and ADR decision is reviewable on its own, away from the heartbeat mechanics. Those
   are two unrelated things to be wrong about, and reviewing them together makes both
   harder to judge.

**Assumptions carried into the proposal** (say so if any is wrong): abandonment is
derived, never stored; the derivation helper ships without a caller; no read endpoint
appears in this change; and the six-hour-single-test gap is accepted rather than
designed around.
