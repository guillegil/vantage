```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:618d49fa07643cb8764fcd748987f9a52437547be312cff26a5fe0026a0ba2ec
verdict: fail
blockers: 0
critical_findings: 0
requirements: 13/16
scenarios: 41/45
test_command: uv run --extra dev pytest -q
test_exit_code: 0
test_output_hash: sha256:6d904d34f755e18ce9b1a3ed70a6fad986368070e99fa55ceac96114621eca98
build_command: uv build --wheel --all-packages -o dist
build_exit_code: 0
build_output_hash: sha256:f2f3c5e1f8ecb9750f5f4e83656ca5832eac798357198243aa22519ab0727136
```

## Verification Report — round 3

**Change**: milestone-1-write-one-row
**Tip verified**: `9691444` on `milestone-1-write-one-row-pr14`, working tree clean,
identical to `origin/milestone-1-write-one-row-pr14` (`git ls-remote` matches)
**Version**: 8 capability specs, 16 requirements, 45 scenarios
**Mode**: Strict TDD
**Round**: 3. Round 1 verified `8b0d666` (fail, 2 CRITICAL). Round 2 verified `a5f18f1`
(fail, 2 CRITICAL: C3, C4). This document replaces both in place — one report, not three.

Round 3 is deliberately narrow: confirm C3 and C4 are closed, look adversarially at the
two new commits, and decide archive readiness.

### What changed since round 2

Two commits. `git diff --name-only a5f18f1..HEAD` is three files: `.github/workflows/ci.yml`,
`tasks.md`, `verify-report.md`. **No production source and no test file changed** — the
`src/` and `tests/` filter returns nothing. The 108 tests verified in round 2 are the same
108 tests here.

| Commit | Touches | Effect |
|---|---|---|
| `c181719` | ci.yml, tasks.md, verify-report.md | closes C4 (counter + positive control) and C3 (superseded paragraph, commit table) |
| `9691444` | tasks.md | closes the recording recursion; adds the two missing rows and the invariant |

### Completeness

| Metric | Value |
|---|---|
| Tasks total | 61 |
| Tasks complete | 61 |
| Tasks incomplete | 0 |

Round 2's report said 62. `grep -c '^- \[x\]' tasks.md` = 61, `grep -c '^- \[ \]'` = 0,
`grep -cE '^[[:space:]]*- \[.\]'` = 61. **Round 2's total was wrong by one; this is my
error, corrected here.** The conclusion is unaffected: nothing is unchecked.

### Build & Tests Execution — all seven gates re-run on this tip

| Gate | Command | Exit | Result |
|---|---|---|---|
| Tests | `uv run --extra dev pytest -q` | 0 | **108 passed in 11.32s** |
| Tests (xdist) | `uv run --extra dev pytest -q -n 4` | 0 | **108 passed in 5.30s** |
| Format | `uv run --extra dev ruff format --check .` | 0 | 49 files already formatted |
| Lint | `uv run --extra dev ruff check .` | 0 | All checks passed! |
| Types | `uv run --extra dev mypy .` | 0 | Success: no issues found in 49 source files |
| Deps | `uv run --extra dev deptry .` | 0 | Success! No dependency issues found (48 files scanned) |
| Build | `uv build --wheel --all-packages -o dist` | 0 | `pytest_vantage-0.1.0` + `vantage-0.1.0` wheels |

The build output hash is byte-identical to round 2's
(`f2f3c5e1f8ecb9750f5f4e83656ca5832eac798357198243aa22519ab0727136`) — the build is
reproducible across rounds. **Coverage**: not configured in this workspace — skipped, not
a failure.

### CI evidence — verified directly with `gh`, not accepted on report

The session named run **31961652741** (head `c181719`). That run is green, but it is not
the tip. **`gh run list` shows a later run, `31961831576`, whose `headSha` is
`9691444fd0ede3de063059bda2b00076064bbdc5` — the exact tip.** All 12 jobs `success`.
Round 3 verifies against that run, because a run on the parent proves the parent.

| Job | ID | Conclusion |
|---|---|---|
| `test (3.10\|3.11\|3.12\|3.13, with\|without)` | ×8 | success |
| `python-3-9-install-refused` | 95200846392 | success |
| `networking-disabled` | 95200846401 | success |
| `clean-environment-install` | 95200846436 | success |
| `quality` | 95200846509 | success |

Branch run history, for the record: `31958927951` cancelled (the 26-minute hang),
`31960093651` failure, `31960708544` failure, `31960833652` success, `31961652741` success,
`31961831576` success.

### RQ-28 scenario 2 — demonstrated or not

**DEMONSTRATED, with a named scope.**

Run **31961831576**, job **`networking-disabled` (95200846401)**, three log lines in order:

```text
17:31:12.7682239Z  packets the control attempt got rejected: 2
17:31:26.5815429Z  ============================= 108 passed in 12.90s ==============================
17:31:26.6837761Z  non-loopback TCP connections the suite attempted: 0
```

Each of round 2's four objections, re-adjudicated:

1. **"Rejection is not observation" — CLOSED.** The rejecting TCP rule now lives in its own
   chain (`VANTAGE_NONET`), and the job reads that chain's packet counter back after the
   suite. The counter is the scenario's subject.
2. **"The suite cannot fail on a rejected attempt" — CLOSED, and closed in the right place.**
   The objection was that the *suite* is structurally incapable of being the observer:
   `plugin.py:96` is a bare `except OSError: return False`, so REJECT, NXDOMAIN and a closed
   port are one code path, and `fault_isolated` (`boundary.py:93`) does the same on the send
   path by design. A weaker fix would have added an assertion inside the suite. This one moved
   the observation into the kernel's packet counter, which no application-level exception
   handler can reach. That is the correct structural answer, not a workaround.
3. **"No positive control" — CLOSED, and it is fail-closed.** A deliberate connect to
   `203.0.113.1:80` (TEST-NET-3, RFC 5737 — reserved, never routable) runs first, from inside
   the blocked group, and the step exits 1 unless the counter moved. It read **2**. The
   counter is then zeroed immediately before the suite (`iptables -Z VANTAGE_NONET &&` in the
   same `&&` chain as the run), so the zero read 14 seconds later is bounded by a proven-live
   rule on both sides.
4. **"`--gid-owner` cannot see egress performed on the suite's behalf by a process outside the
   group" — still structurally true, but no longer material for TCP.** I re-checked the
   outbound surface directly rather than reasoning from the commit message. The only paths
   that leave this process are `socket.create_connection` in `_preflight_reachable`
   (`plugin.py:95`) and `urllib` in `transport.py:34`. Both are in-process; both inherit the
   `nonet` gid through `sudo -g`, which sets the primary group for the whole process tree. There
   is no proxy, agent or helper daemon that could connect on the suite's behalf. The one genuine
   out-of-group carrier is the system stub resolver, and that is DNS over UDP — which the job
   documents as blocked-but-uncounted, deliberately, because the suite resolves one unresolvable
   host on purpose. A name lookup is not a connection to an address, and that lookup targets
   RFC 2606's reserved `.invalid` TLD. **Objection 4 survives as a scoping statement, not as a
   defect.**

**What the counter does not cover, and must be recorded:** it is IPv4 (see N2), TCP (by design,
documented in the job), and in-group (objection 4) only. The honest claim the archive should
carry is: *no non-loopback IPv4 TCP connection was attempted by any process in the suite's
group, measured on a rule proven live in the same job.* That is a real demonstration of
scenario 2 for the only class of connection this system can make. Every address the suite
actually dials is `127.0.0.1`; `example.com` is monkeypatched at
`pytest_vantage.plugin.socket.create_connection` and never dialled
(`test_failure_paths.py:363`); `this-host-does-not-exist.invalid` fails in `getaddrinfo`
before any connect.

### C3 — closed, or closed in appearance?

**Closed, genuinely.** Three independent checks:

1. **The superseded paragraph.** `tasks.md` L731-744 still carries the original PR13 text —
   correctly, as history — and L746-752 immediately marks it *"Superseded — the job has since
   run four times, and the shape described above was wrong"*, quotes the deleted rule
   (`-A OUTPUT -o lo -j ACCEPT` then `-A OUTPUT -j REJECT`) as deleted, and retracts the
   "standard practice" claim by naming the 26-minute hang that disproved it. All three
   statements round 2 called false are now explicitly retracted at the point of use. Marking
   superseded is better than deleting: deleting would erase why the wrong shape was chosen.
2. **The commit record.** All four previously-unrecorded commits (`e9dff96`, `76bf43c`,
   `0f74cef`, `a5f18f1`) now have rows at L907-911, with one-line accounts I spot-checked
   against their diffs.
3. **The mechanical check, run.** `git log --oneline 670b613..HEAD` yields exactly seven
   commits: `9691444`, `c181719`, `a5f18f1`, `0f74cef`, `76bf43c`, `e9dff96`, `8b0d666`. The
   table (L907-913) has exactly seven rows covering exactly those seven — six by hash, the tip
   by subject line `docs(tasks): close the recording recursion`, which matches `9691444`'s
   subject exactly. **The invariant holds at the tip, including for both commits that landed
   after the table was first written**: `c181719`'s row and the tip's row were both added by
   `9691444`, which is precisely why `c181719` could carry its own hash and `9691444` could not.

The recursion fix is correct and I would not have proposed a better one. One documented
weakness, not a defect: an `--amend` preserving the subject leaves the row matching while
pointing at different content. That tradeoff is stated in the file.

Residue: N3 and N4 below.

### C4 — closed, or closed in appearance?

**Closed in substance**, for the reasons in the RQ-28 section: the assertion moved from a
place that could not fail to a place the suite cannot influence, and it is bracketed by a
fail-closed positive control. Residue is N1 and N2 below — both scoping and robustness, and
neither can manufacture the observed result, because the chain was proven armed at 17:31:12
and read zero at 17:31:26 with nothing privileged running in between.

### New defects in the two new commits

**N1 (WARNING) — the assertion that carries the scenario is fail-open; the positive control
is fail-closed.**

```bash
attempted="$(sudo iptables -L VANTAGE_NONET -v -n -x | awk '/REJECT/ {print $1; exit}')"
if [ "${attempted:-0}" -ne 0 ]; then ... exit 1; fi
```

The step shell is `/usr/bin/bash -e` (confirmed in the job log), with **no `pipefail`**. A
pipeline's exit status is `awk`'s, and `awk` succeeds on empty input, so a failing or
empty-reading `iptables -L` leaves `attempted` empty, `${attempted:-0}` becomes `0`, and the
step **passes**. The identical construct in the positive control is safe only because it
compares `-lt 1`, which an empty read fails. So the one assertion that can silently read
nothing and call it success is the one the requirement rests on. The positive control bounds
this in practice — the chain demonstrably existed and counted fourteen seconds earlier, and
only an unprivileged pytest ran in between — which is why this is a WARNING and not a repeat
of C4. One-line closure: `set -o pipefail` and reject an empty read explicitly.

**N2 (WARNING) — no `ip6tables`; the measurement is IPv4-only and nothing says so.**
`grep -c ip6tables .github/workflows/ci.yml` = 0. A TCP connection over IPv6 would be neither
blocked nor counted, and the job would still print zero. Nothing in the suite exercises it
(every dialled address is an IPv4 literal or fails before connect), and GitHub-hosted runners
have no IPv6 egress, so this is not a live hole. But the comment block explains the TCP /
non-TCP split carefully and is silent on address family, which lets a reader take the counter
for a complete one.

**N3 (SUGGESTION) — `tasks.md` L746 says the job "has since run four times"; it has now run
six.** Same shape as C3 — a count written at one instant going stale — at negligible severity,
and the invariant section covers only the commit table, not this sentence.

**N4 (WARNING, discharged by this report) — the demonstration evidence for RQ-28 scenario 2
was not recorded in any SDD artifact.** `tasks.md`'s new section "RQ-28's second scenario, and
why a green job was not enough" describes the *mechanism* and names no run, no job and no log
line, though the row for `a5f18f1` sets the precedent by naming `31960833652`. RQ-28 is a
**Must** verified by **Demonstration**; a demonstration whose evidence is not identifiable is
the latent form of round-1's C2. **This report discharges it**: run `31961831576`, job
`95200846401` and the three exact log lines are recorded above, and this report is itself a
persisted SDD artifact. Recommended but not required: copy the run ID into that `tasks.md`
section so it is durable in the file the implementer reads.

**N5 (SUGGESTION) — the positive control validates one destination.** A pre-existing `ACCEPT`
earlier in `OUTPUT` for some other destination would shadow the counting rule for it. On a
GitHub-hosted runner `OUTPUT` is policy-ACCEPT and effectively empty, and the job never dumps
`iptables -L OUTPUT` to prove it. Speculative; one `iptables -L OUTPUT -v -n -x` echoed once
would settle it for the record.

**N6 (SUGGESTION) — round 2's task total was 61, not 62.** My error; corrected above.

### Are RQ-27 and RQ-28 demonstrated? (Demonstration, not Test)

| Requirement | Scenario | Round 2 | Round 3 | Run / job |
|---|---|---|---|---|
| RQ-27 | CI matrix green, 8 combinations | YES | **YES** | run `31961831576`, 8 `test (…)` jobs green |
| RQ-27 | 3.9 install refused, not broken at import | YES | **YES** | job `python-3-9-install-refused` (95200846392) |
| RQ-28 | Recording succeeds with networking disabled | YES | **YES** | job `networking-disabled` (95200846401), 108 passed |
| RQ-28 | No outbound connection beyond the local server is **attempted** | NO | **YES, scoped** | same job; control 2, counter 0. IPv4/TCP/in-group — see N1, N2 |

### Spec Compliance Matrix (deltas from round 2 only)

| Req | Scenario | Round 2 | Round 3 | Why |
|---|---|---|---|---|
| RQ-28 | No outbound connection attempted | UNDEMONSTRATED | **DEMONSTRATED (scoped)** | counter read back, positive control armed it |
| RQ-31 | Completed session, end > start | PARTIAL (W5) | PARTIAL (W5) | unchanged; no test file changed since round 2 |
| RQ-38.1 | Two concurrent sessions | PARTIAL (W1) | PARTIAL (W1) | unchanged |
| RQ-3.1 / RQ-3.3 | 500-result counts | DEFERRED | DEFERRED | scoped out in the spec's own prose |

All other rows are as recorded in round 2 and were re-confirmed by the 108-test run at this tip.

**Compliance summary**: **41/45** scenarios (was 40). **13/16** requirements complete (was 12).
Remaining: RQ-3 (2 scenarios deferred to M2 by the spec text), RQ-31 (W5), RQ-38 (W1).

### Traceability Invariant (CLAUDE.md)

Re-run with literal `grep -r` for the actual sixteen ids in the specs — RQ-1, RQ-2, RQ-3,
RQ-21, RQ-24, RQ-26, RQ-27, RQ-28, RQ-29, RQ-30, RQ-31, RQ-37, RQ-38, RQ-40, RQ-41, RQ-42.
Every one reaches its proving artefact; counts range 9 files (RQ-31, RQ-38, RQ-41) to 53
(RQ-2). RQ-28 reaches `.github/workflows/ci.yml`. Note again that `rg` skips dotted
directories by default, so `rg RQ-28` from the root misses the workflow while `grep -r` — the
literal invariant — does not. **Verdict on the invariant: holds.**

### TDD Compliance

| Check | Result | Details |
|---|---|---|
| TDD evidence reported | Yes | RED/GREEN pairs across the chain, unchanged since round 2 |
| All tasks have tests | Yes | except documentation-only phases 7/8 (Standard mode, correct) |
| RED confirmed | Yes | every named test file exists |
| GREEN confirmed | Yes | 108/108 at the tip, serial and `-n 4`, and on 3.10-3.13 in CI |
| Triangulation | Adequate | unchanged |
| Safety net | Yes | full suite re-run recorded at each slice |

### Test Layer Distribution

| Layer | Tests | Notes |
|---|---|---|
| Unit | majority | direct calls into `plugin`, `boundary`, `config`, `storage` |
| Integration | substantial | `pytester` subprocess runs against a real local `VantageTestServer` |
| E2E / CI demonstration | 12 jobs | run `31961831576` |
| **Total** | **108** | serial and under `-n 4` |

### Assertion Quality

No test file changed since round 2 (`git diff --name-only a5f18f1..HEAD` contains no test
path), so round 2's adversarial pass stands unmodified: **0 CRITICAL, 0 WARNING**. The two
new commits touch CI and documentation only and weaken no assertion — the CI change adds two
assertions where there were none.

### Quality Metrics

**Linter**: no errors (`ruff check` clean, `ruff format --check` clean on 49 files).
**Type checker**: no errors (`mypy` strict, 49 source files).
**Dependencies**: `deptry` clean over 48 files.

### Archive adjudication — every open finding

Nothing on this list blocks. Each is either a caveat with a named home, or withdrawn.

| # | Finding | Verdict | Where it must be written |
|---|---|---|---|
| W1 | RQ-38 verified at the store layer, not through HTTP | **caveat** | `specs/run-recording/spec.md`, under the RQ-38 requirement |
| W2 | RQ-3's deferral less explicit than RQ-38's | **caveat** | `specs/run-recording/spec.md`, RQ-3's closing parenthetical |
| W5 | RQ-31.1's "at least two seconds" GIVEN unmet | **caveat** | `specs/run-recording/spec.md` at that scenario, or `tasks.md` known-open items |
| W7 | blanket `NOPASSWD:ALL` in the networking job | **caveat** | comment on that line in `.github/workflows/ci.yml` |
| W8 | `__wrapped__` proves "wrapped by something" | **caveat** | docstring of `test_every_recorder_hook_is_fault_isolated` (`test_failure_paths.py:562`) |
| N1 | final assertion fail-open (no `pipefail`) | **caveat** | `.github/workflows/ci.yml` at the assert step |
| N2 | no `ip6tables`; measurement is IPv4-only | **caveat** | same comment block |
| N4 | RQ-28.2 demonstration evidence uncited | **caveat — discharged here** | this report; optionally `tasks.md`'s RQ-28 section |
| S1 | `isoformat_utc` labels naive datetimes UTC | **caveat** | its docstring, `recorder.py:35` |
| S2 | collection error exits 2, recorded `interrupted` | **caveat** | `tasks.md` known-open items |
| S3 | `proposal.md` L255 `grep -r "RQ-01"` matches nothing; 15 criteria unchecked | **caveat** | `proposal.md` — the id typo is a trivial fix, the boxes tick at archive |
| S4 | `CLAUDE.md:71` / `openspec/config.yaml:45` still say the tree "is being reset" | **caveat** | those two files; actively misleading to the next agent, worth fixing at archive |
| S5 | collection-failure test never asserts collection failed | **caveat** | `test_run_report.py:143`; one `assert result.ret == 2` closes it |
| S6 | `design.md` Open Questions unchecked, two stale | **caveat** | `design.md` |
| N3 | "run four times" is now six | **caveat** | `tasks.md` L746 |
| N5 | positive control validates one destination | **caveat** | optional `iptables -L OUTPUT` dump for the record |
| N6 | round 2's task total off by one | **caveat — discharged here** | this report |
| S7 | `timeout-minutes: 15` on every job | **not a finding — withdrawn** | it is a good property, recorded as a note, not an issue |

### Scope Discipline

| Question | Answer |
|---|---|
| Anything land that no task asked for? | Yes — both new commits, and **both are now recorded** in `tasks.md`'s commit table. |
| Any spec scenario unimplemented while its task is marked done? | No. |
| Unchecked tasks? | Zero. |
| Production source changed since round 2? | **No.** CI and documents only. |

### Issues Found

**CRITICAL**: None.
**WARNING**: W1, W2, W5, W7, W8, N1, N2, N4 — all adjudicated as caveats above.
**SUGGESTION**: S1-S6, N3, N5, N6. S7 withdrawn.

### Verdict

**No blockers. Zero CRITICAL. The change is archive-ready once the caveats above are
written down.**

**Read the envelope carefully: `verdict: fail` with `blockers: 0` and
`critical_findings: 0` is not a third blocker.** I first submitted this report as
`verdict: pass` and `gentle-ai sdd-verify-validate` denied admission with *"passing verdict
contradicts failing or incomplete evidence"*. The admission contract forbids a passing
verdict whenever `scenarios` or `requirements` are short of their totals, regardless of why
they are short. Four of forty-five scenarios are short here, and all four are short **by
design**: two RQ-3 scenarios count 500 results in a milestone whose own spec text says it
writes none, and RQ-31.1 and RQ-38.1 are PARTIAL against recorded, accepted caveats (W5, W1).
The honest reading of this envelope is *"complete and correct for its declared scope, with
scope deliberately short of the full spec"* — which is exactly what a Milestone 1 of five
should look like. Round 2's `fail` meant two blockers; this one means four deferred
scenarios.

Both round-2 blockers are closed on evidence I re-verified myself at the exact tip, not on
the report. C3's fix is better than deletion would have been, and its mechanical check passes
including for the two commits that landed after the table was written. C4's fix put the
assertion in the one place the suite cannot influence and armed it with a fail-closed control.

RQ-28 scenario 2 is demonstrated, scoped to IPv4 TCP by processes in the suite's group — and
that scope covers every connection this system is capable of making, because the only outbound
code paths in the tree are two in-process stdlib calls.

Round 1 and round 2 each found a real blocker. **Round 3 does not.** The eight WARNINGs and
nine SUGGESTIONs are caveats with named homes, not defects that make the change dishonest to
close.

---

## Coda: the commit this report was written into

Added at archive time, because the archive pass found this report describing a
commit that is not the one being archived — the fourth instance of the same
defect the three rounds each caught.

This report says **Tip verified: `9691444`**. That was true of the *analysis*,
not of the *file*: at `9691444` the tracked copy still held round 2's text
(`blockers: 2`, tip `a5f18f1`). Round 3's content reached the tree one commit
later, in **`dc5ce4a`** — so this report has never, until now, described the
commit it lives in.

`dc5ce4a` is not idle. It closes round 3's own findings: **N1**, the fail-open
counter read that could have reported success without reading anything;
**N2**, the missing `ip6tables` rule that left an address family blocked but
unmeasured; **S3**, **S4** and **S5**. Round 3 classified all five as caveats
rather than blockers, and they were fixed instead of merely recorded.

**No fourth formal verification round ran against `dc5ce4a`.** What exists for
it instead:

- all seven local gates re-run green — 108 tests serially and under `-n 4`,
  `ruff format --check`, `ruff check`, `mypy` (49 files), `deptry` (48 files),
  both wheels
- CI run **31962736969** on `dc5ce4a`: **success**, all twelve jobs, with
  `networking-disabled` logging a positive control of 2 packets and a suite
  counter of 0

That is evidence, not a verification round, and the difference is recorded here
rather than smoothed over. The archive commit `85b61fb` sits one further commit
on and changes no code — it moves this folder and merges the specs.
