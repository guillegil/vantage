```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:76712c7f375a5283f79010bb5e4633690af26d588636bc9b4cb12d2dc6067296
verdict: fail
blockers: 2
critical_findings: 2
requirements: 12/16
scenarios: 40/45
test_command: uv run --extra dev pytest -q
test_exit_code: 0
test_output_hash: sha256:4d4b5c1d880a58f7b071a7945f5f2524dbfa1a6e9dd3957309ec0283b7dcd451
build_command: uv build --wheel --all-packages -o dist
build_exit_code: 0
build_output_hash: sha256:f2f3c5e1f8ecb9750f5f4e83656ca5832eac798357198243aa22519ab0727136
```

## Verification Report — round 2

**Change**: milestone-1-write-one-row
**Tip verified**: `a5f18f1` on `milestone-1-write-one-row-pr14`, working tree clean,
identical to `origin/milestone-1-write-one-row-pr14` (`git diff` empty)
**Version**: 8 capability specs, 16 requirements, 45 scenarios
**Mode**: Strict TDD
**Round**: 2. Round 1 verified `8b0d666` and returned `fail` with 2 CRITICAL.
This document replaces that one in place.

### What changed since round 1

Four commits landed after the round-1 tip. **No production source changed in any of
them** (`git diff --name-only 8b0d666..a5f18f1 | rg /src/` is empty). The whole delta
is `.github/workflows/ci.yml`, two test files, `tasks.md` and this report.

| Commit | Touches | Effect |
|---|---|---|
| `e9dff96` | ci.yml, tasks.md, verify-report.md, 2 test files | closes C1, W3, W4; first W6 attempt |
| `76bf43c` | ci.yml, `test_failure_paths.py` | `stdin=DEVNULL` cross-version fix; owner-based iptables rule; `timeout-minutes` on every job |
| `0f74cef` | ci.yml | sudoers rule so `sudo -g` does not prompt |
| `a5f18f1` | ci.yml | absolute `uv` path under sudo's `secure_path` |

### Completeness

| Metric | Value |
|---|---|
| Tasks total | 62 |
| Tasks complete | 62 |
| Tasks incomplete | 0 (`rg -c '^- \[ \]' tasks.md` = 0) |

### Build & Tests Execution — all seven gates re-run on this tip

| Gate | Command | Exit | Result |
|---|---|---|---|
| Tests | `uv run --extra dev pytest -q` | 0 | **108 passed in 11.02s** |
| Tests (xdist) | `uv run --extra dev pytest -q -n 4` | 0 | **108 passed in 5.41s** |
| Format | `uv run --extra dev ruff format --check .` | 0 | 49 files already formatted |
| Lint | `uv run --extra dev ruff check .` | 0 | All checks passed! |
| Types | `uv run --extra dev mypy .` | 0 | Success: no issues found in 49 source files |
| Deps | `uv run --extra dev deptry .` | 0 | Success! No dependency issues found (48 files) |
| Build | `uv build --wheel --all-packages -o dist` | 0 | `pytest_vantage-0.1.0` + `vantage-0.1.0` wheels |

107 → 108 tests: the one addition is W3's closure. **Coverage**: not configured in this
workspace — skipped, not a failure.

### CI evidence — verified directly, not accepted on report

`gh run list` / `gh run view`. The final run is **31960833652**, conclusion `success`,
event `push`, workflow `CI`, `headBranch` `milestone-1-write-one-row-pr14`,
`headSha` **`a5f18f15b6ed18a4bdbfce8ddecc0cedafd5f8a8`** — byte-identical to the local
tip and to `origin/…-pr14`. Because the trigger is `push`, the workflow GitHub executed
is the one at that commit, i.e. the one in this tree.

All **12** jobs green. The 29-second wall clock is parallel scheduling, not a skipped
matrix — every job is individually listed with its own duration and ID.

| Job | ID | Time | Non-vacuity check performed |
|---|---|---|---|
| `test (3.10, with)` … `(3.13, with)` | ×4 | 21-24s | log shows `plugins: vantage-0.1.0, anyio, xdist-3.8.0`; **108 passed** |
| `test (3.10, without)` … `(3.13, without)` | ×4 | 21-24s | `Uninstalled 1 package`; plugins line **has no xdist**; **107 passed, 1 skipped** |
| `python-3-9-install-refused` | 95198457591 | 10s | log shows uv's real refusal text: *"the current Python version (3.9.25) does not satisfy Python>=3.10,<3.14"* — refused by `requires-python`, not by an unrelated error |
| `networking-disabled` | 95198457579 | 22s | suite ran under `sudo -g nonet`; **108 passed in 13.13s** |
| `clean-environment-install` | 95198457419 | 7s | diff asserts exactly 1 added / 0 removed |
| `quality` | 95198457431 | 15s | ruff format, ruff check, mypy --strict, deptry, build |

The "without xdist" leg is genuinely without xdist and genuinely different (107+1 skipped
vs 108) — the matrix is not eight copies of the same run.

The matrix also earned its keep on first contact: 3.10, 3.11 and 3.12 all failed, 3.13
passed. `pytester.popen` opens a stdin pipe and closes it; `communicate()` calls
`flush()` on it before 3.13. `stdin=subprocess.DEVNULL` is the correct minimal fix and
weakens nothing.

### Re-assessment of the two round-1 CRITICAL findings

**C1 — closed, verified.** `tasks.md` L860-893 now carries a "Closing commit —
`8b0d666`" subsection naming both corrected files, why they sat outside task 8.2's
two-file scope, and why they were closed rather than carried past archive. The record
now matches the tree. **But the same defect recurred one generation later — see C3.**

**C2 — closed in substance, 3 of its 4 scenarios genuinely demonstrated.** The branches
are pushed (`origin` now carries `pr11`…`pr14`), CI has executed for real, and the run's
head commit is the tip. RQ-27's two scenarios and RQ-28's first scenario are now backed
by named, inspected job logs. **RQ-28's second scenario is not — see C4.** This is a
much narrower finding than round-1 C2, and it is a different one: not "the artefact
never ran" but "the artefact that ran does not measure what the scenario asks for".

### Are RQ-27 and RQ-28 demonstrated? (Demonstration, not Test)

| Requirement | Scenario | Demonstrated? | Run / job |
|---|---|---|---|
| RQ-27 | CI matrix green, 8 combinations | **YES** | run `31960833652`, jobs `test (3.10\|3.11\|3.12\|3.13, with\|without)` — all 8 green, xdist presence/absence confirmed in each log |
| RQ-27 | 3.9 install refused, not broken at import | **YES** | run `31960833652`, job `python-3-9-install-refused` (95198457591); refusal text names `requires-python` |
| RQ-28 | Recording succeeds with networking disabled | **YES, with a caveat** | run `31960833652`, job `networking-disabled` (95198457579), 108 passed with all non-loopback egress rejected for the suite's group |
| RQ-28 | No outbound connection beyond the local server is **attempted** | **NO** | same job — it blocks, it does not observe. See C4. |

RQ-27 is demonstrated without reservation. RQ-28 is half demonstrated.

### Spec Compliance Matrix (deltas from round 1 only)

Unchanged rows are as recorded in round 1 and were re-confirmed by the 108-test run.

| Req | Scenario | Round 1 | Round 2 | Why |
|---|---|---|---|---|
| RQ-2 | No server needed, no warning either | PARTIAL (W4) | **COMPLIANT** | `assert_outcomes(passed=1, warnings=0)` — the omitted count was the whole gap |
| RQ-21 | Every hook is fault-isolated | PARTIAL (W3) | **COMPLIANT** | `test_every_recorder_hook_is_fault_isolated` enumerates `dir(Recorder)` for `pytest_*` and asserts `__wrapped__` on each, with a non-vacuity guard |
| RQ-27 | CI matrix green | UNDEMONSTRATED | **DEMONSTRATED** | run 31960833652 |
| RQ-27 | 3.9 refused | UNDEMONSTRATED | **DEMONSTRATED** | job 95198457591 |
| RQ-28 | Recording with networking disabled | UNDEMONSTRATED | **DEMONSTRATED** | job 95198457579 |
| RQ-28 | No outbound connection attempted | UNDEMONSTRATED | **UNDEMONSTRATED** | C4 — the job rejects, it does not log |
| RQ-31 | Completed session, end > start | PARTIAL (W5) | PARTIAL (W5) | unchanged |
| RQ-38.1 | Two concurrent sessions | PARTIAL (W1) | PARTIAL (W1) | unchanged |
| RQ-3.1 / RQ-3.3 | 500-result counts | DEFERRED | DEFERRED | unchanged |

**Compliance summary**: **40/45** scenarios (was 35/45). **12/16** requirements complete
(was 9/16). Remaining: RQ-3 (2 deferred to M2), RQ-31 (W5), RQ-38 (W1), RQ-28 (C4).

### Traceability Invariant (CLAUDE.md)

Re-run with literal `grep -r` for all 16 ids; every one reaches its proving artefact.
Counts range 9 files (RQ-31, RQ-38) to 53 (RQ-2). RQ-28 reaches
`.github/workflows/ci.yml` L80/L110/L111/L127 — note again that `rg` skips dotted
directories by default, so `rg RQ-28` from the root misses it while `grep -r` (the
literal invariant) does not. **Verdict on the invariant: holds.**

### TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD evidence reported | Yes | RED/GREEN pairs across the chain, re-confirmed |
| All tasks have tests | Yes | except documentation-only phases 7/8 (Standard mode, correct) |
| RED confirmed | Yes | every named test file exists |
| GREEN confirmed | Yes | 108/108 pass at the tip, serial and under `-n 4`, and on 3.10-3.13 in CI |
| Triangulation | Adequate | unchanged from round 1 |
| Safety net | Yes | full suite re-run recorded at each slice |

### Assertion Quality — adversarial pass on the four post-round-1 commits

The question asked was whether a fix rushed to make CI green weakened an assertion.
Every changed line was read. **It did not.** Both test edits move in the strengthening
direction:

- `test_opt_in.py`: `assert_outcomes(passed=1)` → `assert_outcomes(passed=1, warnings=0)`.
  Strictly stronger; the comment states exactly why the omitted argument was the gap.
- `test_failure_paths.py` +22: a **new** test, not a relaxation. Its non-vacuity guard
  (`assert hooks, "…the check would pass vacuously"`) is the right guard for an
  enumerate-and-assert test.
- `test_failure_paths.py` +7 (`76bf43c`): a comment and `stdin=subprocess.DEVNULL`. No
  assertion touched. Independently confirmed the surrounding test still asserts exit
  status preservation and exactly one warning.

I re-verified W3's premise by introspection rather than trusting the commit message:
`Recorder` exposes exactly `pytest_report_header` and `pytest_sessionfinish`, both carry
`__wrapped__`, and `fault_isolated` applies `functools.wraps`. The test is load-bearing.

**Assertion quality: 0 CRITICAL, 0 WARNING** in the new code. (Round 1's two warnings,
W4 and W5, were one closure and one still-open item; W5 is a scenario-GIVEN mismatch,
not an assertion defect.)

### Issues Found

#### CRITICAL

**C3 — the round-1 C1 defect recurred, on the same artefact, about RQ-28.**

`tasks.md` L731-749 (the PR13 landed summary) still reads:

> The job itself (`sudo iptables -A OUTPUT -o lo -j ACCEPT` then `-A OUTPUT -j REJECT`,
> then the full suite with `--no-sync`) is standard practice on GitHub-hosted Ubuntu
> runners … but is the one job in this PR that rests on that standard practice rather
> than on a local reproduction.

Three things in that passage are now false at the tree:

1. **The rule quoted is not the rule in the tree.** `ci.yml` L114 is
   `sudo iptables -A OUTPUT -m owner --gid-owner nonet ! -o lo -j REJECT`. The
   two-rule ACCEPT/REJECT form was deleted by `76bf43c`.
2. **"standard practice" was disproven, not confirmed.** That exact rule hung run
   `31958927951` for 26 minutes until it was cancelled by hand. The record still
   presents it as the safe conventional choice.
3. **"rests on standard practice rather than a local reproduction"** — it now rests on
   four real CI executions, three red and one green.

Additionally, **none of the four commits `e9dff96`, `76bf43c`, `0f74cef`, `a5f18f1` is
recorded in any SDD artifact** — no task, no landed summary, no "Flagged, Not Actioned"
entry. `76bf43c` modified a test file, so this is not documentation-only drift. This is
structurally the same failure C1 named: work lands after the record is written, and the
record silently becomes false. The irony is on the page — the section closing C1 ends
"Recorded here because the verification pass found this commit contradicting this very
section", and the very next commit re-opened the gap.

Severity matches round 1's C1 by the same standard: RQ-28 is a **Must** verified by
**Demonstration**, and this is the record of that demonstration. Archiving freezes a
record that misquotes the demonstrating artefact and denies that it ever ran.
Documentation-only, minutes to fix.

**C4 — the `networking-disabled` job passes for a weaker reason than RQ-28's second
scenario asks for. It cannot detect what that scenario is about.**

The scenario is: *GIVEN the system running with **outbound connections logged**, WHEN a
suite is recorded, THEN no connection to any address other than the configured local
server is **attempted**.*

The job neither logs nor observes. It installs a REJECT rule and reads nothing back.
Four independent reasons the green result does not establish the scenario:

1. **Rejection is not observation.** The scenario constrains *attempts*. `-j REJECT`
   with no `-j LOG` companion, and no post-run read of the rule's byte/packet counters,
   produces no record of whether anything was attempted.
2. **The suite is structurally incapable of failing when an attempt is rejected.**
   `plugin.py:94-98` — `_preflight_reachable` wraps `socket.create_connection` in a bare
   `except OSError: return False`. A REJECTed connect raises `OSError`; so does
   NXDOMAIN; so does a closed port. All three are the same code path, and all three
   leave the suite green. `fault_isolated` (RQ-21) does the same for the send path by
   design. `tasks.md` L740-744 already says this out loud about the
   `this-host-does-not-exist.invalid` test. A stray outbound attempt would therefore be
   swallowed, warned about once, and pass.
3. **No positive control.** Nothing proves the rule was ever armed or ever matched a
   packet. A green job is equally consistent with "rule correct, nothing attempted" and
   "rule silently not matching" — for instance if `sudo -g` stopped setting the egid the
   owner match keys on. The job's three prior failures were all sudo/PATH plumbing; the
   rule's *matching behaviour* has never been shown to do anything.
4. **The rule's reach is narrower than the scenario's claim.** `-m owner --gid-owner`
   matches only sockets owned by the `nonet` group. Egress performed on the suite's
   behalf by another process — the system stub resolver being the obvious one — is
   outside the group and outside the rule.

None of this makes scenario 1 wrong: the suite really did record over loopback with all
non-loopback egress rejected for its own group, and the loopback exemption is faithful
to RQ-28's "beyond the configured local server". Scenario 1 stands. Scenario 2 does not.

**Closure is small and closes the W-level gap at the same time**: after the suite, run
`sudo iptables -L OUTPUT -v -n -x` and assert the REJECT rule's packet counter is **0**.
That converts "blocked" into "logged and observed", which is literally the scenario's
GIVEN. Pair it with a positive control — `sudo -g nonet curl -m 5 https://example.com`
must fail **and** must increment that counter — so the zero is meaningful rather than
vacuous.

#### WARNING

Round-1 warnings, re-adjudicated:

| # | Round 1 | Round 2 | Basis |
|---|---|---|---|
| W1 | RQ-38 tested a layer below the spec | **OPEN, unchanged** | `test_concurrency.py:30-39` still drives `store.record_execution` directly; no server, no HTTP |
| W2 | RQ-3's deferral in design.md but not the spec | **OPEN, unchanged** | `run-recording/spec.md` L88-89 carries RQ-38's deferral explicitly; RQ-3 at L57 still carries none |
| W3 | No test that *every* hook is fault-isolated | **CLOSED** | new enumerating test, premise independently re-verified |
| W4 | RQ-2's "no warning" half unasserted | **CLOSED** | `warnings=0` |
| W5 | RQ-31.1's "at least two seconds" GIVEN unmet | **OPEN, unchanged** | spec L48 still says two seconds; fixture suite is still one trivial test |
| W6 | `networking-disabled` will fail for a runner reason | **CLOSED — and it was correct** | it did fail, exactly as predicted, hanging run `31958927951` for 26 minutes. The group-owner rewrite is a better fix than the `ESTABLISHED` exemption round 1 suggested, because the runner agent reconnects and a reconnect is a NEW connection |

New at this round:

- **W7 — the `networking-disabled` job grants blanket passwordless sudo.**
  `ci.yml` L118-119 writes `<user> ALL=(ALL:ALL) NOPASSWD:ALL` to
  `/etc/sudoers.d/99-vantage-nonet`. Only `NOPASSWD: /usr/bin/sudo -g nonet …` is
  needed. On an ephemeral runner that already has passwordless root this is not a
  privilege escalation and not a blocker, but it is wider than the comment above it
  claims ("running as another GROUP") and would be wrong to copy into a persistent
  runner.
- **W8 — the W3 test asserts "wrapped by something", not "wrapped by `fault_isolated`".**
  `hasattr(getattr(Recorder, name), "__wrapped__")` is satisfied by any
  `functools.wraps`-based decorator. Today that is only `fault_isolated`, and the
  mutation proof holds. A future hook decorated with an unrelated `@wraps` decorator
  would pass this test while breaking RQ-21. Tightening it to compare
  `getattr(Recorder, name).__module__` or to assert the latch behaviour would close it.

#### SUGGESTION

All six round-1 suggestions were re-checked against the tip. **All six remain open**;
none was actioned and none needed to be.

- **S1** — `isoformat_utc` (`recorder.py:44`) still `strftime`s a literal `+00:00` and
  calls no `astimezone`; a naive datetime would be labelled UTC silently. Confirmed: no
  `astimezone` anywhere in the module.
- **S2** — a collection error still exits 2 and is therefore recorded
  `interrupted: true`, indistinguishable from Ctrl-C. `_INTERRUPTED_EXIT_STATUS = 2`,
  `recorder.py:87`.
- **S3** — `proposal.md` still has **15** unchecked success criteria, and L255 still
  reads `grep -r "RQ-01"`, which matches nothing (ids are `RQ-1`).
- **S4** — `openspec/config.yaml:45` and `CLAUDE.md:71` still say the tree "is being
  reset" and to distrust `src/`. The reset completed; `src/` is gone.
- **S5** — `test_failed_collection_still_writes_one_row` (`test_run_report.py:143-151`)
  still asserts only `len(vantage_server.executions()) == 1`, never that collection
  actually failed. One `assert result.ret == 2` pins it.
- **S6** — `design.md`'s Open Questions are all still unchecked and two are still stale.

Newly noted:

- **S7** — every CI job now carries `timeout-minutes: 15`. That is the right lesson from
  the 26-minute hang and worth keeping; noted so it is not removed as noise later.

### Known open items — still genuinely recorded

The four items round 1 confirmed are all still recorded. One changed status: the
`networking-disabled` job is no longer unexecuted, which is precisely why the record
describing it is now wrong (C3). The other three — the `finished_at` constant-offset
weakness, XDG being a Linux convention, and SIGKILL leaving no row — are unchanged and
still carry their recorded judgements.

### Scope Discipline

| Question | Answer |
|---|---|
| Anything land that no task asked for? | **Yes — all four post-round-1 commits.** They are legitimate verification-driven fixes, but none is recorded in any SDD artifact (C3). |
| Any spec scenario unimplemented while its task is marked done? | No. Task 7.2 delivered the workflow. The residual gap (C4) is between "the job runs" and "the job measures the scenario". |
| Unchecked tasks? | Zero. |
| Production source changed since round 1? | **No.** CI, two tests and documents only. |

### Verdict

**FAIL — not archive-ready. Two CRITICAL, both apply-fixable.**

This remains a `fail` on the admission contract's terms — a passing verdict may not
carry critical findings — and not a judgement that the implementation is weak. The
implementation got materially stronger this round: 108 tests green serially, under
`-n 4`, and on four Python versions × two xdist configurations on real GitHub runners;
40 of 45 scenarios with genuine evidence, up from 35; 12 of 16 requirements complete,
up from 9; the traceability invariant still holding for all 16. Round 1's warnings were
closed with strengthened assertions, not weakened ones, and W6 turned out to be a real
defect that cost a 26-minute hang exactly as predicted.

What blocks archive:

1. **C3** — correct `tasks.md`'s PR13 summary so it quotes the rule that is actually in
   the tree and states that the job has now run green, and add a landed-summary entry
   for the four commits `e9dff96`, `76bf43c`, `0f74cef`, `a5f18f1`. Documentation-only.
2. **C4** — either add the counter read plus positive control to `networking-disabled`
   and re-run CI, which would move RQ-28 to fully demonstrated; **or** record RQ-28
   scenario 2 as an explicit, named, accepted caveat in the spec and in `tasks.md`, the
   way RQ-38's criteria 2 and 3 already are. Either is legitimate. Silently treating a
   green block as a demonstration of "no attempt" is not.

W1, W2, W5, W7, W8 and S1-S7 are carryable as recorded caveats and do not block.
