# Run Metadata Specification

## Purpose

Defines user-declared configuration values captured from the test repository
and stored per run: the declaration surface, path containment, the
plugin-side bounding rules, the fault-tolerance posture for a malformed
declaration, and the write-once storage contract. New capability — the first
surface describing what is under test, distinct from every existing `run`
column, which describes the machine and the test repository. The declaration
file's own opt-in gate and its RQ-2 differential inertness are defined in
`opt-in-activation`; server-side document parsing and its failure taxonomy
are defined in `session-ingestion`; the table shape is defined in
`recording-schema`.

**Component:** across the boundary — `pytest-vantage` reads, validates and
bounds the declaration and the files it names, and ships raw UTF-8 content
without parsing it (RQ-24, ADR-9); `vantage.service` parses declared
documents and extracts declared keys; `vantage.storage` persists them.

## Requirements

### Requirement: Declaration file location and format

The system MUST read a declaration from `vantage-metadata.json` at the test
repository's `rootpath`, parsed with the standard library's `json` module,
naming which files to read and which keys to take from each. A malformed
declaration file MUST NOT fail the run's ingestion; the run MUST still be
recorded, with metadata capture contributing nothing for that session.

**Verification: Test.**

#### Scenario: A well-formed declaration is read
- GIVEN a `vantage-metadata.json` at rootpath naming one file and one key
- WHEN a session runs with metadata capture enabled
- THEN the named file is read and the named key is captured for that run

#### Scenario: A malformed declaration does not fail the run
- GIVEN a `vantage-metadata.json` that is not valid JSON
- WHEN a session runs with metadata capture enabled
- THEN the run is still recorded and no metadata is captured for that session

### Requirement: Missing declaration file emits a warning

Where metadata capture is enabled and no `vantage-metadata.json` exists at
rootpath, the system MUST emit a pytest warning, so a deliberate flag met
with nothing is visibly a misconfiguration rather than a silent no-op.

**Verification: Test.**

#### Scenario: The flag alone, with nothing declared, warns
- GIVEN metadata capture enabled and no `vantage-metadata.json` at rootpath
- WHEN the session runs
- THEN a pytest warning is emitted and the run proceeds unaffected otherwise

#### Scenario: A declaration present emits no such warning
- GIVEN metadata capture enabled and a `vantage-metadata.json` present at rootpath
- WHEN the session runs
- THEN no missing-declaration warning is emitted

### Requirement: Declared paths are contained under rootpath

Every declared path MUST resolve strictly under `rootpath`. Symlinks MUST be
resolved before the containment check runs. An absolute path or a path that
resolves outside `rootpath` MUST be rejected outright, never clamped into
containment.

**Verification: Test.**

#### Scenario: An absolute path is rejected
- GIVEN a declaration naming an absolute path
- WHEN the session runs with metadata capture enabled
- THEN that path is rejected and marked, and no file outside rootpath is read

#### Scenario: A `..` escape is rejected
- GIVEN a declaration naming a path that resolves outside rootpath via `..`
- WHEN the session runs with metadata capture enabled
- THEN that path is rejected rather than clamped to rootpath

#### Scenario: A symlink escape is rejected after resolution
- GIVEN a declared path that is a symlink inside rootpath pointing outside it
- WHEN containment is checked
- THEN the symlink is resolved before the check runs, and the path is rejected

### Requirement: The read surface is auditable

The run record MUST name every declared file that was actually read, storing
the path exactly as declared (rootpath-relative), not the resolved path.

**Verification: Test.**

#### Scenario: A read file is named on the run
- GIVEN a declaration naming one file that is successfully read
- WHEN the run is recorded
- THEN the run names that file by its declared, rootpath-relative path

### Requirement: Declared files are bounded, dropped whole

Each declared file MUST be bounded to 64 KiB of UTF-8 before it is included
in the report. A file exceeding this bound MUST be dropped in its entirety,
never truncated, and marked declared-but-uncaptured. The metadata section of
a session report MUST additionally be bounded on its total JSON-encoded
bytes before the request is built; when that budget is exhausted, files MUST
be dropped whole, each one marked, rather than the section being truncated.

**Verification: Test.**

#### Scenario: An oversized file is dropped whole, not truncated
- GIVEN a declared file exceeding 64 KiB of UTF-8
- WHEN the session report is built
- THEN that file's content is entirely absent from the report and the run marks it declared-but-uncaptured

#### Scenario: The metadata section budget drops files, not bytes
- GIVEN declared files whose combined content would exceed the metadata section's per-report budget
- WHEN the session report is built
- THEN files are dropped whole until the section fits, and every dropped file is marked

### Requirement: Non-UTF-8 declared files are rejected before encoding

A declared file that is not UTF-8 decodable MUST be rejected as uncapturable
before the report is JSON-encoded, rather than causing the report to fail
encoding.

**Verification: Test.**

#### Scenario: A non-UTF-8 file is marked uncapturable
- GIVEN a declared file containing bytes that are not valid UTF-8
- WHEN the session report is built
- THEN that file is marked uncapturable and the report is still built and sent

### Requirement: Metadata is a write-once fact, no backfill

Each captured key/value pair MUST be written once, at ingestion, and never
updated thereafter. Only keys named in the declaration MUST be stored; a key
declared after a run was recorded MUST NOT be backfilled onto that run.

**Verification: Test.**

#### Scenario: A captured value is never updated after ingestion
- GIVEN a run whose metadata was recorded at ingestion
- WHEN the same run is later queried
- THEN its metadata values are unchanged from what was written at ingestion

#### Scenario: A key declared later has no value on earlier runs
- GIVEN a run recorded before a key was added to the declaration
- WHEN that key is later declared and a new run is recorded
- THEN the earlier run carries no value for that key

### Requirement: Session-start overhead is measured (RQ-25)

The session-start cost of reading, validating and bounding the declared
files MUST be measured against RQ-25's overhead budget, on the same harness
used for `version-control-context`, and the measured number MUST be
committed to this spec rather than assumed or asserted.

**Verification: Analysis** — a measurement against a budget, not a pass/fail
assertion.

#### Scenario: Overhead is measured and recorded as a number
- GIVEN a fixture and harness equivalent to `version-control-context`'s own measurement
- WHEN metadata capture's session-start cost is measured with the flag enabled
- THEN the measured overhead is committed to this spec as a number, alongside whether it fits the remaining RQ-25 budget

**Measurements (RQ-25):** Measured 2026-09-04 on
`Linux-6.18.33.2-microsoft-standard-WSL2-x86_64` (WSL2), git 2.55.0, Python
3.13.15, via `scripts/measure_metadata_overhead.py` — five interleaved A/B
paired runs per profile per repository (A = `--vantage` alone; B =
`--vantage --vantage-metadata` against the worst legitimate declaration:
`MAX_DECLARED_FILES` = 16 files at `MAX_DECLARED_FILE_BYTES` = 8 KiB each),
medians reported, never means. A third arm, C (`--vantage --vantage-metadata`
with no declaration present at all — Q3's warn-only path), was measured
separately afterward for context, since it needs the declaration file
absent rather than present, so it is not part of the same interleaved pairs.

| Repository | Profile | A, baseline (median) | B, worst-case (median) | Delta (B−A) | % of A | C, no declaration (median) | Delta (C−A) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| This repository | 1,000 × ~10 ms | 11.305 s | 11.285 s | −20.5 ms | −0.18% | 11.263 s | −42.1 ms |
| This repository | 1,000 × ~1 ms | 1.678 s | 1.664 s | −14.2 ms | −0.85% | 1.684 s | +5.4 ms |
| Synthetic (20,000 files) | 1,000 × ~10 ms | 11.347 s | 11.320 s | −26.5 ms | −0.23% | 11.335 s | −11.4 ms |
| Synthetic (20,000 files) | 1,000 × ~1 ms | 1.717 s | 1.746 s | +29.4 ms | +1.71% | 1.713 s | −3.6 ms |

**Pre-measurement forecast** (design.md D102, recorded so the result can
visibly disagree with it): under 2 ms once per session — under 0.02% of the
10 ms profile, under 0.12% of the 1 ms profile.

**The measured delta is indistinguishable from noise at this sample size,
and that is the honest result, not a defect of the measurement.** Three of
the four B−A deltas are negative — metadata capture cannot make a session
faster, so a negative delta is measurement jitter, not a real effect. The
deltas span −42.1 ms to +29.4 ms, straddling zero, exactly the shape expected
when a true effect (D102's own <2 ms forecast) sits an order of magnitude
below the process-spawn variance a five-pair, subprocess-per-run benchmark
can resolve. `vcs.capture`'s own git-process cost (6.12–27.56 ms,
`version-control-context`'s Measurements) was large enough to clear that
noise floor at this same sample size; a projected <2 ms cost is not. The
forecast is not falsified by this measurement, and it is not confirmed by
it either — that distinction is recorded rather than collapsed into a false
"holds."

**What this measurement is compared against, stated plainly.** RQ-25's own
normative text — the 2% budget these percentages are read against — does
not exist anywhere in this repository. It was a Notion requirement, and
unlike every other identifier this project still cites, no capability spec
ever picked its text up before the corpus was retired (2026-08-28,
CLAUDE.md); only every other document's *reference* to a "2% budget"
survives it. Two of those references currently disagree with each other
about the *conclusion* their own numbers support: this document's sibling,
`openspec/specs/version-control-context/spec.md`, reads its own 4.11%/4.17%
1 ms-profile results as "still inside RQ-25's 2% budget," which is
arithmetically false for those two rows, while `docs/open-questions.md`
reads the same numbers as a breach and computes the remaining headroom from
that reading. This paragraph does not resolve that disagreement, and does
not edit either document — which of two contradictory in-repo statements is
correct is a human decision, not one available to make silently inside an
unrelated measurement paragraph. What this paragraph states without
ambiguity: **this change's own added cost, whatever it turns out to be once
resolvable, rides on top of a baseline** (`version-control-context`'s
git-capture overhead) **that is already at 4.11%–4.17% of the 1 ms profile
before metadata capture is added at all** — over any 2% reading of the
budget — while holding, with headroom, on the 10 ms profile (0.29%–1.50%
baseline).

**A future change to the declaration read or its bounds MUST re-run
`scripts/measure_metadata_overhead.py` and update this paragraph** — the
same obligation `version-control-context`'s and `run-recording`'s own
Measurements paragraphs state for their own numbers.
