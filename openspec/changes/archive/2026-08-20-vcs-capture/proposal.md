# Proposal: VCS Capture

## Intent

Six `vcs_*` columns have existed in `schema.sql` since Milestone 1 and **nothing
writes any of them** — `rg vcs_ packages/vantage/src` finds them only in the
schema file. RQ-15, the test-history endpoint whose own rationale calls it *"the
endpoint the whole product exists to serve"*, requires every history entry to
carry the commit it ran on.

`read-api`'s question round settled this on 2026-08-19: **VCS capture goes
first**, so the flagship endpoint is not born with a Must Have recorded as
half-delivered. That decision is closed and is not reopened here.

This change makes the plugin read the repository once per session and report it
as a `vcs` sibling section, and makes the server persist it. It closes three
requirements that partition the space with no gap:

| RQ | Case | Obligation |
|---|---|---|
| RQ-10 | repository present and readable | commit, branch, subject first line, dirty flag |
| RQ-23 | not a repository at all | all null, run still recorded, **no warning** |
| RQ-39 | repository present but unreadable | all null, run still recorded, **exit status unchanged** |

## Scope

### In scope

- A new `pytest_vantage/vcs.py`: one bounded `git` read per session, controller
  only, returning an immutable snapshot; **never raises**
- The `vcs` sibling section on `SessionReport` (already named in its own
  docstring as a planned Milestone 3 section); a `VcsReport` model with
  `extra="forbid"`
- A `VcsContext` value object on `vantage.core.domain`, carried by `Execution`
- `_UPSERT_RUN` grows from eight columns to fourteen; `_row_to_execution` and the
  memory adapter follow; `vantage_port_contract.py` gains the scenarios
- RQ-25 measurement of the added per-session cost, committed as numbers in the
  spec (the RQ-3 / `run-recording` precedent)
- ADR on the plugin executing a subprocess (see *Does this earn an ADR?*)
- `docs/schema-manifest.md`: six rows move from *populated M3* to *populated*

### Out of scope

- **The schema does not change.** All six columns already exist, nullable, with
  the correct defaults. No migration, no `schema_version` bump.
- RQ-11 machine context, RQ-35, `environment` as a section — a separate change
  even though it is the adjacent envelope slot
- Any read path. RQ-23 criterion 2 ("the run appears in the run list") **cannot
  be verified here** — see *Verification forecast*
- Mercurial, Subversion, Jujutsu. RQ-10 says *git*
- Submodules, worktrees-of-worktrees, `GIT_DIR` redirection as supported
  configurations — they must not crash, they need not be understood
- Extending `GET /api/v1/capabilities`. See *Wire shape*

## Capabilities

### New Capabilities
- `version-control-context`: what a run records about the repository it ran in
  across all three cases — readable (RQ-10), absent (RQ-23), unreadable (RQ-39)
  — including that all three record the run and none of them changes the exit
  status

### Modified Capabilities
- `session-ingestion`: the envelope accepts a `vcs` sibling section, and the run
  upsert persists six more columns. The endpoint contract itself is unchanged.
- `recording-fault-tolerance`: the plugin gains a **third** isolation path, and
  unlike `fault_isolated` and `liveness_isolated` it **must not latch and must
  not warn on the ordinary case**. That is a new fact about how this plugin
  isolates and belongs in the spec that owns RQ-21.

## Approach

### Reading git: subprocess, not a hand-written `.git` parser

RQ-24 and ADR-4 leave two candidates, and RQ-39's own criterion 2 — *"a git
repository and no git executable on PATH"* — presupposes the answer: the
requirement was written against a system that shells out. Weighed honestly:

| | `subprocess` + `git` | parse `.git` by hand |
|---|---|---|
| RQ-24 | stdlib only ✅ | stdlib only ✅ |
| RQ-39.2 | the requirement's own case | a case that could not arise, so criterion 2 becomes untestable |
| Dirty tree | `git` already knows | reimplementing index-vs-worktree comparison, mtime races, `.gitignore`, `core.autocrlf` |
| Packed refs, packed objects, `reftable` | git's problem | our problem, forever |
| Cost | one process, ~10–60 ms | no process |
| Failure surface | process spawn, PATH, timeout, decoding | a compatibility surface that grows with every git release |

**Subprocess.** Trading one process for a compatibility surface we would own
permanently is not a trade, and RQ-39.2 already assumes it.

### The subprocess is a bounded, fail-closed boundary

Modelled on `transport.fetch_capabilities`, which *is* the boundary rather than
delegating to a decorator (design D40). Every invocation carries:

- `timeout=` — a hung `git` is RQ-21 criterion 4's exact failure in a new place.
  `git status` on a network filesystem, or behind a `credential.helper`, can
  block indefinitely.
- `stdin=subprocess.DEVNULL` — git must not be able to prompt
- `cwd=config.rootpath`, `errors="replace"` on decoding — a commit message is
  not required to be UTF-8, and a `UnicodeDecodeError` inside an observability
  tool is not acceptable
- `shutil.which("git")` checked first, and `FileNotFoundError` caught anyway —
  belt and braces for RQ-39.2 across platforms

Any failure of any kind returns the all-null snapshot. The function catches
`Exception`, never `BaseException` (the `boundary.py` rule: `KeyboardInterrupt`
must still reach pytest's `wrap_session` for RQ-31).

### Which isolation path — neither existing one

**Neither `fault_isolated` nor `liveness_isolated`, and this is not a detail.**
Both latch: one failure disables every later call sharing the flag. Reusing
either would mean *an unreadable repository silently stops the session's results
or heartbeats from being reported at all* — turning RQ-39 (record the run with
nulls) into "record nothing", which is precisely the wrong implementation RQ-23
criterion 2 was written to catch.

VCS capture instead uses its own **non-latching, self-contained** boundary
inside `vcs.py`. Nothing else on the Recorder is affected by a git failure, and
a git failure produces exactly the data RQ-23 and RQ-39 demand: nulls.

Warning policy differs by case, per RQ-23's requirement notes (*"a tool that
complains about something optional being missing trains people to ignore its
warnings"*): **absent repository — silent**. Unreadable repository — see Q3.

### Where and when capture runs

Controller only, by construction: `pytest_configure`'s `workerinput` guard
already returns before a `Recorder` is ever constructed under xdist, so any
Recorder-owned code is controller-only without a second guard.

**Once, at `Recorder.__init__` (i.e. during `pytest_configure`, after activation
and preflight have already succeeded, so RQ-2 is untouched).** Two reasons:

1. RQ-10 criterion 1's dirty flag must describe *the tree that produced the
   results*. A test that writes into the repository would flip a
   finish-time reading; a start-time reading cannot be wrong that way.
2. It is safe there only because the function cannot raise. An exception in
   `pytest_configure` is pytest's own INTERNALERROR with exit status 3 — RQ-21
   criterion 5's named failure.

The immutable snapshot is then reused by both reports. It is **not** re-read.

### Wire shape

`SessionReport` grows `vcs: VcsReport | None = None`. `RunReport` stays
`extra="forbid"` and is **not touched** — its own docstring already reserves
`vcs` as a sibling *section*, and that asymmetry is the mechanism, not a
shortcut.

**An older server tolerates this with no version bump and no capability gate.**
`SessionReport` is `extra="ignore"`, so a server that predates this change drops
the `vcs` key and records the run exactly as it does today. The degradation is
lossless *for that server* and harmless — unlike the session-lifecycle case,
where a start-write against a server that cannot finish it would half-record.

**`GET /api/v1/capabilities` is therefore not extended.** The
`plugin-server-compatibility` probe exists because `session_lifecycle` needed a
gate; VCS needs none, and adding a capability flag nothing branches on would
imply a gate that does not exist. The probe's single `session_lifecycle` answer
stays as-is.

The snapshot rides on **both** the start report and the finish report — same
values, so the monotonic upsert is idempotent. Against an older server the start
report is not sent at all (`lifecycle_available` is false), and the finish
report still carries `vcs`. That is why both, not just the start write. It also
means an abandoned run (RQ-44) keeps its commit against a current server.

### Trust: `safe_segment` does not apply

`errors.py::safe_segment` is an allow-list for **echoing client-chosen text back
in a response** — a rejection body, or `Acknowledgement.ignored`. A commit
subject is never echoed; it is *stored data*, in the same class as
`interrupt_reason`, which already crosses this boundary unfiltered.

Sanitising it would destroy fidelity for no gain: the subject exists to be read
by a human later. The real controls are (a) a length bound, (b) never echoing it
in an acknowledgement or error body, and (c) output encoding at whatever renders
it — an ADR-8 web-interface concern, not an ingestion one. RQ-40's rationale
already names *"branch names, commit messages"* as content the 0600 file mode
protects, so this is the posture the project already chose.

Not a new trust boundary either: the user's own test code already runs from that
tree with strictly more privilege than `git status` has.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `pytest_vantage/vcs.py` | **New** | the bounded reader and its snapshot type |
| `pytest_vantage/recorder.py` | Modified | hold the snapshot; add `vcs` to both reports |
| `pytest_vantage/boundary.py` | **Unchanged** | deliberately — see *Which isolation path* |
| `pytest_vantage/plugin.py` | Unchanged | the xdist guard already covers this |
| `vantage/core/domain/execution.py` | Modified | `VcsContext` value object; `Execution.vcs` |
| `vantage/service/schemas.py` | Modified | `VcsReport`; `SessionReport.vcs` |
| `vantage/service/routes/runs.py` | Modified | `_to_execution` maps the section |
| `vantage/storage/sqlite_store.py` | Modified | `_UPSERT_RUN` 8 → 14 columns; `_row_to_execution` |
| `vantage/storage/memory.py` | Modified | in lockstep |
| `vantage/storage/schema.sql` | **Unchanged** | the columns already exist |
| `vantage/service/routes/capabilities.py` | **Unchanged** | no new capability flag |
| `tests/vantage_port_contract.py` | Modified | new contract scenarios |
| `docs/schema-manifest.md` | Modified | six rows change *Populated* |
| `docs/adr/00NN-*.md` | New | see below |

## Does this earn an ADR?

Applying `CLAUDE.md`'s reversal-cost filter honestly, most of the decisions here
do **not**: the wire section is additive and already anticipated, the columns
already exist, and capture-at-start-vs-finish is a one-line move.

One does. **The plugin executes an external process for the first time.**
Everything `pytest-vantage` has done until now is stdlib socket and JSON work
inside its own process. Spawning `git` in someone else's test environment is a
posture change, and reversing it means writing and owning a `.git` parser — well
past a sprint. That earns an ADR.

**Numbering:** `read-api` has already reserved **ADR-0014** in its landed
proposal. `vcs-capture` lands first but takes **ADR-0015** rather than forcing an
edit to a proposal that has not merged. Flagged rather than assumed — see Q5.

## Delivery forecast (500-line review budget)

| # | Slice | Forecast | Risk |
|---|---|---|---|
| 1 | `vcs.py` reader + real-repository fixtures for all nine criteria | ~420 | Medium — fixture-heavy, the RQ-39 cases are the hard ones |
| 2 | Recorder wiring, both reports, exit-status preservation, non-latching proof | ~320 | Medium |
| 3 | Server: `VcsReport`, `VcsContext`, `_to_execution`, both adapters, port contract | ~425 | High — two adapters in lockstep, biggest surface |
| 4 | RQ-25 measurement + committed numbers, ADR, schema-manifest, traceability | ~280 | Low |

**Total ~1,445 across 4 slices. No slice exceeds 500.**
`chain_strategy: feature-branch-chain`, branching off `ft/read-api-proposal`.

## Verification forecast

The `session-lifecycle` lesson was that task decomposition captured
implementation decisions and skipped the test-layer plan, so three criteria
shipped written-but-unproven. Every criterion below has its method named now.

| Criterion | Method | Why, and what the fixture actually is |
|---|---|---|
| RQ-10.1 dirty tree | **Test** | real repo via `tmp_path`, commit, then modify a tracked file |
| RQ-10.2 clean tree + hash | **Test** | assert against `git rev-parse HEAD` read independently |
| RQ-10.3 detached HEAD | **Test** | `git checkout <sha>`; commit recorded, branch **null** |
| RQ-10.4 no commits yet | **Test** | `git init` and nothing else; commit **null**, run stored |
| RQ-23.1 not a repository | **Test** | bare `tmp_path`; all six null, **and no warning emitted** |
| RQ-23.2 appears in run list | **Blocked → Inspection** | *there is no run list.* Verified at the storage level (`count_executions`, `get_execution`) and recorded in the spec as **awaiting `read-api`**, promoted to Test when it lands. Not claimed as met. |
| RQ-39.1 corrupt `.git` | **Test** | a real `.git` **file** containing garbage, and a `.git` directory with a truncated `HEAD` — not a mock |
| RQ-39.2 no `git` on PATH | **Test** | `monkeypatch.setenv("PATH", str(empty_dir))` for real. **Not** `mock.patch("subprocess.run")` asserting the mock was called — that proves nothing about `FileNotFoundError`. |
| RQ-39.3 exit status preserved | **Test** | **both arms**: a passing suite exits 0 *and* a failing suite exits 1. RQ-21's rationale names the second as the one a naive boundary swallows. |
| RQ-39 permissions case | **Inspection**, skip-if-root | `chmod 000 .git` is a no-op as root, and CI containers run as root. A test that silently passes because it could not fail is the `session-lifecycle` defect again. |
| RQ-25 overhead | **Analysis** | interleaved paired runs, medians, numbers committed to the spec. Must report the git cost *separately* from the report cost, and separately for `--untracked-files=no` vs full status. |
| RQ-24 still holds | **Test** (existing) | the clean-environment install check already runs; `subprocess`/`shutil` are stdlib |

**Forecast for RQ-25 before measuring**: `git status` is the expensive call — it
stats the working tree. RQ-10.1 says *"uncommitted changes to a **tracked**
file"*, so untracked files must not count, which permits
`--untracked-files=no` — materially cheaper on a large tree, and *more* correct
than the default. Expected total: one process spawn plus 2–4 git plumbing calls,
**~10–60 ms once per session**. Against RQ-25's profile (1,000 tests × 10 ms =
~10 s), 60 ms is ~0.6% — inside the 2% budget. RQ-25 criterion 2 (request count
independent of test count) is untouched: this adds **zero** requests.

The risk is criterion 3's profile — 1,000 tests × 1 ms = ~1 s, where 60 ms is
6%. The requirement's own notes already flag that a fixed session cost dominates
fast suites and that criterion 3 exists to *record the number whether or not the
2% holds*. It must be recorded, not defended.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `git status` dominates on a large monorepo, blowing RQ-25 | Medium | `--untracked-files=no`; measure before committing numbers; Q1 offers a documented opt-out |
| A hung `git` turns RQ-21.4 into a hang in a new place | Medium | every invocation carries a `timeout=` and `stdin=DEVNULL`; a bound must be *chosen* (Q2), not defaulted |
| Reusing an existing latching decorator kills results reporting on a git failure | **High if unstated** | stated as a named non-decision above; slice 2 proves non-latching explicitly |
| RQ-39.2's test mocks `subprocess` instead of scrubbing `PATH` | High | named in the verification table as the wrong fixture |
| Permissions fixture passes vacuously as root in CI | High | skip-if-root, recorded as Inspection, not counted as Test |
| Non-UTF-8 commit message crashes the plugin | Low | `errors="replace"`; a fixture with a latin-1 subject |
| A second report clobbers non-null `vcs_*` with nulls | Medium | same snapshot in both reports; the upsert's monotonic behaviour must be re-checked for these six columns in slice 3 |
| `config.rootpath` ≠ git toplevel in a monorepo | Medium | `vcs_root` exists for exactly this; record git's own `--show-toplevel`, never assume it equals rootdir |
| Empty string written where null is required | Medium | the archived M1 design already forbids it by name; contract-suite scenario asserts SQL `NULL` |
| RQ-23.2 quietly recorded as met | Medium | recorded as blocked-on-`read-api` in the spec |

## Rollback Plan

Every slice is additive and the schema is untouched, which makes rollback
unusually clean.

1. **Per slice**: revert the branch. No migration to undo.
2. **Data already written**: the six columns return to being written by nothing.
   Nothing reads them yet — `read-api` has not landed — so reverted rows are
   indistinguishable from pre-change rows to every consumer that exists.
3. **Wire**: a reverted plugin sends no `vcs` section and `SessionReport` is
   `extra="ignore"`; a reverted *server* ignores a section a newer plugin still
   sends. Both skew directions are already safe by design.
4. **Partial rollback if RQ-25 fails**: dropping only the dirty check is *not* a
   real escape — RQ-10.1 is the criterion its own notes call the one that
   carries the weight. The honest partial rollback is an opt-out flag (Q1) or
   reverting the change entirely.
5. **ADR**: supersede, never edit (`CLAUDE.md`).

## Dependencies

- **`git` at run time, optionally.** New, and deliberately soft: its absence is
  RQ-39.2, a supported state, not a failure.
- No new distribution. `subprocess`, `shutil`, `dataclasses` are stdlib
  (RQ-24, RQ-26 hold).
- Python floor 3.10 — no `StrEnum`, no `datetime.UTC`, no `str`-mixin `Enum`.
- **Blocks `read-api`'s RQ-15 criterion 1.** That is the whole reason for the
  ordering.
- **Blocked-on decisions**: Q1–Q3 below. Specs should not be written until they
  are answered.

## Success Criteria

- [ ] A run recorded from a clean repository carries a commit hash matching
      `git rev-parse HEAD`, its branch, its subject line, and `dirty = 0`
- [ ] A run from a dirty tree is marked dirty; from a detached HEAD, branch is
      null; from a repository with no commits, commit is null and the run stores
- [ ] A run from a non-repository stores with six SQL `NULL`s and **emits no
      warning**
- [ ] With `PATH` scrubbed of `git`, a passing suite exits 0 and a failing suite
      exits 1, and both runs are stored with null VCS fields
- [ ] A git failure disables nothing else: results and heartbeats for that same
      session still report
- [ ] The added per-session cost exists as a committed number in the spec,
      measured on both RQ-25 profiles, not as a `print()`
- [ ] RQ-23 criterion 2 is recorded as awaiting `read-api`, not claimed
- [ ] `POST /api/v1/runs` from a *pre-change* plugin still returns 201 unchanged

---

## Proposal question round

These are decisions a human must make. None has a safe default, and none is
papered over above.

**Q1 — Is VCS capture unconditional, or does it get an opt-out?**
RQ-10 is *Must Have · Optional feature*, and the "optional feature" clause is
about the repository being present, not about the user choosing. But a monorepo
where `git status` costs 400 ms would pay it on every session with no way out,
and RQ-25 criterion 3 already warns that a fixed session cost dominates fast
suites. Options: (a) unconditional — one fewer flag, and RQ-10 reads as
unconditional; (b) a `--vantage-no-vcs` escape that records nulls, which risks
becoming the flag people set once and forget, silently degrading the flagship
endpoint; (c) unconditional now, add the flag only if measurement in slice 4
shows it is needed. **Which?**

**Q2 — What is the git subprocess timeout, in seconds?**
It must be a number, and it is not the report timeout (that governs a network
round trip) nor the liveness timeout (derived for heartbeats). A hung `git` is
RQ-21 criterion 4's failure mode in a new place, and criterion 4 bounds the
whole session at *timeout + 5 s*. Candidates: 2.0 s (matches
`_MAX_CONNECT_TIMEOUT`, and a local git read that takes longer is pathological),
or 5.0 s (survives a cold cache on a large repo). Cheap to change, but it is a
committed number in a spec scenario, so it must be chosen deliberately.

**Q3 — Does an unreadable repository (RQ-39) warn?**
RQ-23's requirement notes are explicit that an *absent* repository must **not**
warn — complaining about something optional trains people to ignore warnings,
including RQ-21's. RQ-39 says nothing either way, and its case is genuinely
different: a corrupt `.git` or a missing `git` binary is a condition the user
might want to know about. But a CI image without `git` would warn on every
session forever. Options: (a) silent, matching RQ-23 exactly; (b) warn once per
session, naming the cause; (c) silent for "no git binary", warn for "corrupt
repository". **Which?**

**Q4 — Where is the commit subject bounded, and to what?**
`vcs_commit_subject_truncated` exists, so a bound was anticipated. Two
sub-decisions:
*How much* — RQ-22's uniform 64 KiB (consistent, but absurd for one line), or a
subject-specific bound such as 1 KiB (proportionate, but a second truncation
rule in a codebase with one)?
*By whom* — the plugin, bounding what crosses the wire (keeps the payload small,
but makes the truncation flag a client-supplied claim the server must trust), or
the server, as the single truncation authority (consistent with RQ-22 being a
storage obligation, but the plugin must still cap something so a pathological
repository cannot approach `MAX_REPORT_BYTES`)?

**Q5 — ADR number, and does the ADR cover more than the subprocess?**
The proposal takes **ADR-0015**, leaving 0014 reserved for `read-api` even
though this change merges first, to avoid editing a proposal that has not
landed. Confirm, or renumber (`vcs-capture` → 0014, `read-api` → 0015).
Separately: should the ADR be scoped narrowly to *"the plugin may spawn an
external process, bounded and fail-closed"*, or broadly to *"how
`pytest-vantage` reads its host environment"*, which would pre-bind the RQ-11
machine-context change that follows? The narrow scope is honest about what has
actually been decided; the broad one avoids a second ADR in three weeks.

**Q6 — Is `read-api`'s scope line stale on merge?**
`read-api`'s proposal lists *"VCS capture (`vcs_*`)"* under **Out of scope** and
its RQ-15 row says criterion 1 is unreachable. Its own answer round then
reverses that. Should `vcs-capture` update `read-api`'s proposal on merge, or is
`read-api` re-proposed after this lands? Leaving both as they are means the
repository holds a proposal contradicted by its own appendix.

## Answers to the question round — 2026-08-19

**Q1 — Unconditional, and measure before adding an escape hatch.** No
`--vantage-no-vcs` flag ships with this change. One slice measures the real cost
with numbers, and a flag is added only if that measurement shows it is needed —
at which point the flag justifies itself instead of being a guess. A flag that
exists gets set once, on a day somebody is in a hurry, and is never unset; months
later a project's history has no commits and nobody remembers why. Adding an
option later is cheap. Removing one people already depend on is not.

**Q2 — 5 seconds.** Not the preflight's 2.0 s. A cold cache on a large repository
is a slow-but-**healthy** read, and a 2-second bound would turn it into null
fields — losing the data with nothing actually broken. The 5 seconds are paid
only when git is genuinely hung, which is rare; impatient nulls are paid every
time the repository is large, which is not.

**Q3 — Warn, split by cause.** A corrupt `.git` warns once: it is rare and
probably worth knowing about. A missing `git` executable does not: in a CI image
without git that would warn on every session forever, and teaching people to
ignore warnings breaks RQ-21's warning too, which is the one that matters. An
absent repository stays silent, as RQ-23's own notes require.

**Q4 — The server is the authority; the plugin caps only for size.** The server
decides the bound and owns `vcs_commit_subject_truncated`. The plugin truncates
beforehand solely so a pathological repository cannot push the report toward
`MAX_REPORT_BYTES`. If the plugin owned the flag it would be a client claim the
server had to trust, and ADR-9 already settled that the server performs every
write and every judgement about what was written.

**Q5 — This change takes ADR-0014.** Numbers follow the order decisions are
accepted, not the order proposals are drafted, and `vcs-capture` merges first.
`read-api` takes 0015.

**Q6 — `read-api`'s proposal is corrected when this merges.** It currently lists
VCS capture under Out of Scope and calls RQ-15 criterion 1 unreachable, and its
own answer appendix reverses both. A repository holding a proposal contradicted
by its own appendix is worse than either version alone.
