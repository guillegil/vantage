# Version Control Context Specification

## Purpose

Defines what a run records about the git repository it ran in, across the
three cases that partition the space with no gap: readable (RQ-10), absent
(RQ-23), and present-but-unreadable (RQ-39). All three record the run; none
of them refuses to, and none of them changes pytest's exit status.

**Component:** across the boundary — `pytest-vantage`'s `vcs.py` performs one
bounded `git` read per session, controller-only; `vantage.service` maps the
`vcs` section onto `VcsContext`; `vantage.storage` persists the six existing
`vcs_*` columns (schema unchanged).

## Requirements

### Requirement: Readable repository context (RQ-10)

Where the project directory is a git repository whose information can be
read, the server MUST record for each run the commit hash, the branch name,
the first line of the commit message, and whether the working tree held
uncommitted changes to a tracked file.

**Verification: Test**, against real repositories built via `pytester`/`tmp_path` — not mocked git output.

#### Scenario: Dirty working tree is marked dirty (RQ-10.1)
- GIVEN a repository with uncommitted changes to a tracked file
- WHEN a session is recorded
- THEN the run is marked as having a dirty working tree

#### Scenario: Clean working tree matches an independent read (RQ-10.2)
- GIVEN a repository whose working tree is clean
- WHEN a session is recorded
- THEN the run is not marked dirty, and its commit hash matches `git rev-parse HEAD` read independently of the plugin

#### Scenario: Detached HEAD records the commit with a null branch (RQ-10.3)
- GIVEN a repository in detached HEAD state
- WHEN a session is recorded
- THEN the commit hash is recorded and the branch name is null

#### Scenario: A repository with no commits yet stores a null commit (RQ-10.4)
- GIVEN a repository holding no commits yet (`git init` only)
- WHEN a session is recorded
- THEN the run is stored and its commit hash is null

### Requirement: Absent repository (RQ-23)

Where the project directory is not a git repository, the server MUST record
the run with all six vcs fields null and MUST NOT emit any warning for that
absence — complaining about something optional being missing trains people
to ignore warnings, including the ones that matter (RQ-21).

**RQ-23 criterion 2** — that the run appears in a run list alongside runs
from repositories — **cannot be demonstrated by this change**: there is no
run list, because `read-api` has not landed. It is verified here only by
**Inspection** at the storage level, and is **not claimed as met**: the run
is present and retrievable via `count_executions`/`get_execution` with all
six vcs fields null, indistinguishable in storage from any other run.
Promote to Test/Demonstration once `read-api` exposes a run list.

#### Scenario: Not a git repository records nulls (RQ-23.1)
- GIVEN a directory that is not a git repository
- WHEN a session is recorded
- THEN the run is stored and its commit hash, branch, commit subject and dirty flag are all null

#### Scenario: Absent repository emits no warning (RQ-23.1)
- GIVEN a directory that is not a git repository
- WHEN a session is recorded
- THEN no warning is emitted

#### Scenario: Absent repository's run is retrievable in storage, pending a run list (RQ-23.2 — Inspection, awaiting `read-api`)
- GIVEN a run recorded from a directory that is not a git repository
- WHEN that run is retrieved directly via `count_executions` and `get_execution`
- THEN the run is present with all six vcs fields null
- AND this stands in for "appears in the run list" only until `read-api` can demonstrate the criterion through an actual list; it is not claimed as met by this change

### Requirement: Unreadable repository (RQ-39)

If the project directory is a git repository whose version-control
information cannot be read, then the server MUST record the run with all six
vcs fields null, and this MUST NOT change the exit status pytest would
otherwise have had.

**Verification: Test**, except the permissions case, which is **Inspection,
skip-if-root** — `chmod 000 .git` is a no-op for a process running as root,
and CI containers run as root; a test that cannot fail proves nothing and
MUST NOT be counted as Test. The missing-`git` case MUST scrub `PATH` of a
real `git` executable (e.g. `monkeypatch.setenv`) — asserting a
`mock.patch("subprocess.run")` was called proves the mock, not the
`FileNotFoundError` behavior.

#### Scenario: A `.git` entry that is not a valid repository records nulls (RQ-39.1)
- GIVEN a directory containing a `.git` entry that is not a valid repository
- WHEN a session is recorded
- THEN the run is stored with all six vcs fields null

#### Scenario: A corrupt repository warns exactly once
- GIVEN a directory containing a `.git` entry that is not a valid repository
- WHEN a session is recorded
- THEN exactly one warning is emitted, naming the cause

#### Scenario: No git executable on PATH records nulls silently (RQ-39.2)
- GIVEN a git repository and `PATH` scrubbed of any `git` executable
- WHEN a session is recorded
- THEN the run is stored with all six vcs fields null and no warning is emitted

#### Scenario: A passing suite's exit status survives an unreadable repository (RQ-39.3)
- GIVEN a git repository whose version-control information cannot be read
- WHEN a suite of passing tests is run
- THEN pytest exits with status 0

#### Scenario: A failing suite's exit status survives an unreadable repository (RQ-39.3)
- GIVEN a git repository whose version-control information cannot be read
- WHEN a suite containing one failing test is run
- THEN pytest exits with status 1

#### Scenario: A permissions-restricted repository, skip-if-root
- GIVEN a `.git` directory made unreadable via `chmod 000`, and the test process not running as root
- WHEN a session is recorded
- THEN the run is stored with all six vcs fields null
- AND when the process runs as root, this scenario is skipped rather than passing vacuously

**Measurements (RQ-25):** Measured 2026-08-20 on
`Linux-6.18.33.2-microsoft-standard-WSL2-x86_64` (WSL2), git 2.55.0, Python
3.13.15, via `scripts/measure_vcs_overhead.py` — five interleaved A/B/A/B…
paired runs per profile per repository, medians reported, never means.

- **Git cost** (the whole five-invocation `capture()` read, D44): 6.12 ms
  median in this repository; 27.56 ms median in a generated synthetic
  repository of 20,000 tracked files.
- **D44's flag choice, justified by a number**: `git status --porcelain
  --untracked-files=no` measures 1.76 ms (this repository) / 17.71 ms
  (synthetic repository) against 1.94 ms / 23.56 ms for the default `git
  status --porcelain` — cheaper in both repositories, and by a wider margin
  as tracked-file count grows.
- **Whole-session overhead**, recording on vs off, git cost included:

  | Repository | Profile | OFF (median) | ON (median) | Delta | % of OFF | Git-only | Report-only remainder |
  | --- | --- | --- | --- | --- | --- | --- | --- |
  | This repository | 1,000 × ~10 ms | 11.003 s | 11.035 s | 31.4 ms | 0.29% | 6.1 ms | 25.3 ms |
  | This repository | 1,000 × ~1 ms | 1.656 s | 1.725 s | 69.1 ms | 4.17% | 6.1 ms | 62.9 ms |
  | Synthetic (20,000 files) | 1,000 × ~10 ms | 10.981 s | 11.146 s | 164.8 ms | 1.50% | 27.6 ms | 137.2 ms |
  | Synthetic (20,000 files) | 1,000 × ~1 ms | 1.673 s | 1.742 s | 68.8 ms | 4.11% | 27.6 ms | 41.2 ms |

**Pre-measurement forecast** (design.md D52, recorded so the result can
visibly disagree with it): ~10–60 ms once per session; ~0.6% of the 10 s
profile (inside the 2% RQ-25 budget); ~6% of the 1 s profile.

**The result disagrees with the forecast in both directions, and is
recorded as measured, not adjusted to match it.** The synthetic
repository's 10 ms-profile overhead (1.50%) is roughly 2.5× the ~0.6%
forecast — closer to the 2% budget than expected, driven by the larger
tracked-file count's `git status` cost — while still inside RQ-25's 2%
budget in every one of the four measured cases. Both 1 ms-profile results
(4.11%, 4.17%) came in below the ~6% forecast rather than above it. RQ-25's
own text asks for the number to be recorded whether or not the 2% holds;
here it holds in all four cases measured, but not with the margin the
forecast implied for a large repository's slower suite.

**A future change to `vcs.py`'s argv or invocation count MUST re-run
`scripts/measure_vcs_overhead.py` and update this paragraph** — the same
obligation `run-recording/spec.md`'s own Measurements paragraph states for
its result-schema and batch-insert numbers.
