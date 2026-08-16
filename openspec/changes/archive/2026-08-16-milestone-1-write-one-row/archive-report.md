# Archive Report: milestone-1-write-one-row

**Archived**: 2026-08-16
**Archived to**: `openspec/changes/archive/2026-08-16-milestone-1-write-one-row/`
**Branch**: `milestone-1-write-one-row-pr14`, tip `dc5ce4a` at archive time
**Artifact store**: hybrid (OpenSpec files + Engram)

## Final state at close

- **61/61 tasks complete** across eight phases and fourteen PR slices plus three
  follow-up branches (`pr7b`, `pr9b`, `pr12b`). `tasks.md` (archived) carries zero
  `- [ ]` lines; verified mechanically (`rg -c '^\s*- \[ \]'` = 0, `rg -c '^\s*- \[x\]'` = 61)
  before this archive moved the folder.
- **108 tests pass**, independently re-run in this archive session at the actual
  branch tip `dc5ce4a` (not the verify-report's recorded tip — see "Contradictions
  found" below): `uv run --extra dev pytest -q` → `108 passed in 11.04s`.
  `ruff format --check .` → 49 files already formatted. `ruff check .` → all checks
  passed. `mypy .` → no issues in 49 source files. `deptry .` → no issues, 48 files
  scanned. All four re-confirmed locally during archive, not merely carried over
  from the verification snapshot.
- **CI green on all twelve jobs** at the tip, per the orchestrator's launch-prompt
  final-state facts (rank 3 in the Final-State Authority hierarchy) — including the
  eight-way 3.10–3.13 × xdist matrix, the Python-3.9-install-refused job, the
  clean-environment install-diff job, and `networking-disabled` with a positive
  control (2 packets) and the suite's own counter (0). This session did not
  independently query `gh run list` for a run against `dc5ce4a` — CI status for that
  exact commit is carried from the launch prompt, not re-verified here.
- **Verification ran three rounds.** Round 1 (`8b0d666`): fail, 2 CRITICAL. Round 2
  (`a5f18f1`): fail, 2 new CRITICAL (C3, C4). Round 3 (verified tip `9691444`): fail
  by the admission contract's technicality only — `verdict: fail`, `blockers: 0`,
  `critical_findings: 0`, `requirements: 13/16`, `scenarios: 41/45` — the shortfall
  is 4 scenarios deferred **by design** to Milestone 2 (RQ-3 result counting,
  RQ-31.1/RQ-38.1 partial against accepted caveats), not defects. One report,
  updated in place across all three rounds (Engram observation **35**,
  `openspec/changes/.../verify-report.md`), not three separate reports.
- **Round 3 closed C3 and C4** (both CRITICAL findings from round 2) with verified,
  not merely claimed, evidence: C4 by moving the RQ-28 assertion into the kernel's
  own `VANTAGE_NONET` packet counter with a fail-closed positive control; C3 by a
  mechanical commit-table check (`git log --oneline 670b613..HEAD` matched
  `tasks.md`'s table row-for-row at that point).
- **Round 3 also recorded six new findings in the two commits since round 2**: N1
  (WARNING — the RQ-28 assertion was fail-open, no `pipefail`), N2 (WARNING — no
  `ip6tables`, IPv4-only measurement not stated), N4 (WARNING, discharged in the
  report itself), N3/N5/N6 (SUGGESTION). None blocked archive; round 3's own
  adjudication named a home for every caveat (specs, CI comments, docstrings,
  `tasks.md`, `proposal.md`, `design.md`, `CLAUDE.md`/`openspec/config.yaml`).

## Contradictions found during this archive session

Three verification rounds each caught the record disagreeing with the tree
(round 1: one unrecorded commit; round 2: four; round 3's own commit table: the
mechanism to keep catching it). This archive session looked for a fourth instance
rather than assuming round 3 cleared it, and found one substantive case and one
minor one.

### 1. The verify-report's recorded tip is one commit behind the actual archived tip

`verify-report.md` (both the file and Engram observation 35) states
**"Tip verified: `9691444`"** for round 3. The actual branch tip at archive time —
and the commit this archive operates on — is **`dc5ce4a`**, one commit later,
titled *"fix: close round 3's fail-open assertion and record the archive
caveats"*.

`dc5ce4a` is not idle documentation: it changes `.github/workflows/ci.yml` (adds
`set -o pipefail` and an `ip6tables` rule — closing round 3's own N1 and N2
findings), `packages/pytest-vantage/tests/test_run_report.py` (adds a precondition
assertion — closing round 3's S5), `CLAUDE.md` and `openspec/config.yaml` (closing
S4's stale "tree is being reset" guidance), and `proposal.md` (closing S3's
`RQ-01`→`RQ-1` typo). It also is the commit that first wrote the round-3 report
body and the "Known open items at archive" section into the tracked
`verify-report.md`/`tasks.md` files — at commit `9691444` those files on disk still
carried round-2's content (`blockers: 2`, tip `a5f18f1`); the round-3 content
existed only in Engram (saved 18:05:08) until `dc5ce4a` landed it in the repo
(committed 19:48:54).

**No formal fourth `sdd-verify` round was run against `dc5ce4a` specifically.**
This archive session independently re-ran the four fast local gates against
`dc5ce4a` (pytest 108, ruff format, ruff check, mypy, deptry — all clean, see
above) as a partial substitute, but did not re-run `gh run list` against
`dc5ce4a`'s own CI run or a full `sdd-verify` cycle. The orchestrator's launch
prompt asserts CI is green at "the tip" as an explicit final-state fact, which
this report carries per the Final-State Authority hierarchy (rank 3, outranking
the verify-report snapshot's rank-4 claim) — but the discrepancy between the
verify-report's literal "tip verified" text and the true archived tip is recorded
here rather than silently resolved, per that same hierarchy's instruction for
contradictions between ranked sources.

**Assessment: does not block archive.** `dc5ce4a`'s changes are exactly the
closure of findings round 3 itself flagged as non-blocking (N1, N2 = WARNING; S3,
S4, S5 = SUGGESTION); the diff is small (7 files, 394+/291-, no production `src`
change beyond the CI workflow and one test assertion) and self-consistent with
round 3's own adjudication list. But it is real, unverified-by-a-formal-round
work, and a future reader should know that "round 3 verified the tip" is not
quite true — round 3 verified `9691444`; `dc5ce4a` closed round 3's own warnings
one commit later and was checked only by this archive session's lighter gate.

### 2. `proposal.md`'s own status field was never flipped to Accepted

`proposal.md` line 3 still reads **"Status: for review"** at archive time, even
though downstream artifacts (the design.md Engram observation, and Engram
observation 8's own note) describe it as "accepted... on 2026-08-14", and the
change has since gone through implementation, three verification rounds, and now
archive. Minor — does not affect scope or correctness — but the document's own
header disagrees with everything built on top of it, and no artifact edits this
during any of the fourteen PR slices. Recorded here rather than silently fixed,
since correcting artifact prose is not in this phase's scope.

### 3. Engram's `sdd/milestone-1-write-one-row/proposal` topic is stale (pre-existing, already self-documented)

`mem_search`/`mem_get_observation` for the proposal topic returns only
**observation 8**, explicitly self-marked `SUPERSEDED — stale Milestone 1
proposal, do not use`. It names the wrong requirement ids (`REQ-1-xx`/`NFR-1-xx`
vs. the real `RQ-1`…`RQ-42`), the wrong architecture (hexagonal vs. clean), and
the wrong packaging (four packages vs. two), and directs the reader to
`proposal.md` on disk as the real source of truth — which is what this archive
report does. This was caught and documented by `sdd-spec` in the same
observation, not newly discovered here; it is repeated in this report only
because the Task instructions asked for artifacts read via `mem_search` +
`mem_get_observation`, and the proposal artifact returned by that path is not
usable as-is. **No corrected proposal observation was ever saved to Engram under
this topic key** — the hybrid dual-write for the proposal artifact was never
completed on the Engram side, only on the file side. Not fixed here (out of this
phase's scope), but flagged so a future reader does not repeat the same trap.

## Specs synced

`openspec/specs/` held only `.gitkeep.md` before this archive — this is the
**first merge** into the main specs directory. All eight capability domains were
**full specs** (delta spec = full spec, since no main spec existed to merge
into), copied mechanically (`cp`, verified `diff -r` empty per domain — see
Mechanical Copy Evidence below), not synthesized as deltas:

| Domain | Action | Requirements |
| --- | --- | --- |
| `architecture-boundaries` | Created | RQ-24, RQ-26, RQ-30 |
| `opt-in-activation` | Created | RQ-2 |
| `recording-fault-tolerance` | Created | RQ-21, RQ-37 |
| `recording-schema` | Created | RQ-29 |
| `run-recording` | Created | RQ-1, RQ-31, RQ-3, RQ-38 (criterion 1 only) |
| `runtime-support` | Created | RQ-27, RQ-28 |
| `session-ingestion` | Created (NEW capability, no prior coverage) | RQ-41, RQ-42 |
| `storage-permissions` | Created (NEW capability, no prior coverage) | RQ-40 |

**Layout decision**: `openspec/specs/{domain}/spec.md`, one directory per capability
domain, mirroring the change's own `specs/{domain}/spec.md` layout exactly and the
structure documented in `openspec-convention.md`. Chosen deliberately over
flattening into fewer files, because (a) the domain names are already established
naming used throughout `design.md` and `tasks.md` and changing them at archive time
would break that traceability; (b) each domain maps to one architectural component
boundary (`vantage/server`, `pytest-vantage/plugin`, or "across the boundary") per
the spec's own "Component:" header, so one file per domain keeps that boundary
visible in the source-of-truth layout too; (c) this is the first-ever merge into
`openspec/specs/`, so the layout choice here sets precedent for every future
change's spec merges — establishing it as one-directory-per-domain now avoids a
later, disruptive restructure once more domains accumulate.

Known gap **carried forward, not fixed here**: `docs/adr/0005`/`0006` still record
"twelve indexes" in ADR-5's own text at the time design.md was written (later
corrected on the *documentation* side by PR14 task 8.2 and the `8b0d666` closing
commit — but the *specs* directory itself carries no index-count assertion, so
this does not create a disagreement inside `openspec/specs/`).

## Mechanical Copy Evidence

### Spec sync — `diff -r` per domain (source: change folder before move; destination: `openspec/specs/`)

```
OK architecture-boundaries
OK opt-in-activation
OK recording-fault-tolerance
OK recording-schema
OK run-recording
OK runtime-support
OK session-ingestion
OK storage-permissions
overall status: 0
```

Combined tree diff (`diff -r openspec/changes/milestone-1-write-one-row/specs
openspec/specs --exclude=.gitkeep.md`) after cleaning up one stray directory
created by an initial zsh word-splitting mistake (`mkdir -p
"openspec/specs/$domains"` with an unquoted-in-effect space-joined variable — an
operator error caught and corrected before any content copy relied on it, not a
truncation): **empty, exit 0**.

### Archive move — `diff -r` (pre-move recursive snapshot vs. archived folder)

```
$ diff -r "$snapshot_root/source" "openspec/changes/archive/2026-08-16-milestone-1-write-one-row"
$ echo "diff exit: $?"
diff exit: 0
```

Empty output, exit 0. `git mv` succeeded (rename-detected in `git status`, not a
delete+add). Source directory `openspec/changes/milestone-1-write-one-row/`
confirmed absent after the move, before the diff was taken.

## Archive contents

- `proposal.md` ✅ (see contradiction #2 above — status field stale)
- `specs/` ✅ (8 domains, all now also in `openspec/specs/`)
- `design.md` ✅
- `exploration.md` ✅ (carried along, not required by the convention table but was
  present in the source change folder and moved with it)
- `tasks.md` ✅ (61/61 tasks complete, "Known open items at archive" section intact
  — see below)
- `verify-report.md` ✅ (round 3, in place; see contradiction #1 above for its
  tip-lag against the true archived commit)
- `archive-report.md` ✅ (this file, additive)

## Known open items carried into the archive (from `tasks.md`, preserved verbatim in substance)

Round 3 judged this change archivable **provided these are written down**. They
are not to-dos someone forgot; they are the honest boundary of what this
milestone proves. Reproduced here so the archive report itself carries them, not
only the archived `tasks.md`:

**Scope of the RQ-28 demonstration.** The measurement counts TCP only —
non-TCP is blocked but uncounted (the suite resolves one unresolvable host over
UDP/DNS). `--gid-owner` sees only the suite's own process group; it cannot see a
helper daemon's egress, though inspection found no such path exists in this tree.
Honest claim: no non-loopback TCP connection was attempted by any process in the
suite's group, on a rule proven live in the same job.

**Requirements proven at a narrower scope than their scenario states.**
RQ-38.1 is tested at the storage layer (two threads into one store), not through
the server as `design.md` envisaged (two threads POSTing into one uvicorn
instance) — the idempotency guarantee is where the test is, the server path is
inferred. RQ-31.1's scenario names "a session of at least two seconds"; the test
runs a trivial one-test suite, proving ordering, not duration. RQ-3 criteria 1 and
3 are deferred to Milestone 2 and recorded in `design.md` but **not** in
`specs/run-recording/spec.md` (now `openspec/specs/run-recording/spec.md`) — the
spec is the more likely document to be read, and it does not carry the deferral
note RQ-38's does in the same file.

**Assertions weaker than they look.** `test_every_recorder_hook_is_fault_isolated`
asserts each hook carries `__wrapped__` — "wrapped by something", not specifically
by `fault_isolated`; a different decorator would satisfy it. `Recorder`'s
`finished_at > started_at` would pass against a faked constant clock offset —
judged acceptable, not a regression, and left as-is. `isoformat_utc` hardcodes
`+00:00` and converts nothing, so a non-UTC datetime would be mislabelled; every
call site in this tree passes UTC.

**Behaviour with no requirement covering it.** A session killed with SIGKILL
leaves no row at all — none of the forty-two requirements covers it; needs a
Notion decision, not an implementation fix. A collection error exits 2 and is
recorded `interrupted: true`, indistinguishable from Ctrl-C (confirmed by
assertion in `test_failed_collection_still_writes_one_row`, itself strengthened by
the `dc5ce4a` fix noted in contradiction #1). The XDG database default is a Linux
convention; Windows/macOS fall back to `~/.local/share/vantage/vantage.db`,
recorded in ADR-0010 as a known consequence, not decided. All six `RunReport`
fields are required and extras are forbidden, so `run` cannot gain a field without
forcing `/api/v2`.

**CI.** The `networking-disabled` job grants blanket `NOPASSWD:ALL` to write its
sudoers rule — acceptable on a disposable runner, not a pattern to copy elsewhere.

## Observation IDs read for this archive (traceability)

| Artifact | Engram ID | Notes |
| --- | --- | --- |
| proposal | 8 | Self-marked SUPERSEDED; file on disk (`proposal.md`, archived) used as source of truth instead — see contradiction #3 |
| spec | 15 | Full delta-specs summary; matches the 8 files merged |
| design | 16 | Full design, ADR-9 rewrite, schema manifest |
| tasks | 17 | Points to `tasks.md` as the non-duplicated authoritative record; this session read the file directly for the Task Completion Gate and the "Known open items" section |
| verify-report | 35 | Round 3, tip `9691444` — see contradiction #1 for its lag against the true archived tip `dc5ce4a` |

## Native Review Receipt Gate

`reviewGate` was not present in any structured status surfaced to this session;
no review artifact (`sdd/milestone-1-write-one-row/review/*`) was searched for or
read, per the gate's own rule that an absent `reviewGate` needs no investigation.
Archive proceeded under ordinary repository policy.

## Task Completion Gate

`tasks.md` (pre-move): `rg -c '^\s*- \[ \]'` → 0; `rg -c '^\s*- \[x\]'` → 61.
Gate passed with no reconciliation needed — no stale checkboxes found.

## SDD Cycle Complete

The change has been fully planned, implemented, verified (three rounds), and
archived. `openspec/specs/` now carries the eight capability domains this
milestone defined, as the first-ever merge into that directory. Two honest gaps
are recorded above (the verify-report's tip lag against `dc5ce4a`, and the
proposal's stale status field) rather than silently resolved. Ready for
Milestone 2.
