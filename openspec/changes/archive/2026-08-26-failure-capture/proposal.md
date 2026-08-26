# Proposal: Failure Capture

> **Written 2026-08-22**, against `main` with `read-api` archived
> (`openspec/changes/archive/2026-08-22-read-api/`). Two obligations, proposed
> together because they answer one question from two sides: **why** a test
> failed (the traceback, plus the failure's type and location) and **what it
> printed** (the captured output). No new `RQ-xx` identifier is minted
> (CLAUDE.md, decided 2026-08-18); `RQ-32`, `RQ-16`, `RQ-22`, `RQ-8` and
> `RQ-35` are cited only as prior art from the frozen legacy corpus, which is
> authoritative of nothing.
>
> **Question round answered 2026-08-22.** Four decisions were taken and are
> folded into the body below; the record of them and their reasoning is in
> *Decisions* at the end, replacing the open questions. The worst-case
> report-size arithmetic in *The tension the brief did not name* was wrong on
> first writing and has been corrected, with the working shown inline.

## Intent

Vantage records that a test failed and nothing about the failure.
`vantage.core.domain.result.Result` carries identity, outcome, timings and
`worker_id` — sixteen values reach the wire — and not one of them says *why*.
pytest builds a failure representation during the run and it dies with the
process.

The product exists to answer "this test failed four times this month — is it
the same failure or four different ones?" Today that question cannot be asked
of the database at all.

## Two corrections to the brief, both evidence-backed

**1. This change does not touch `schema.sql`, and ADR-0013 does not fire.**
`packages/vantage/src/vantage/storage/schema.sql` already declares
`failure_type`, `failure_message`, `failure_message_truncated`,
`failure_path`, `failure_lineno`, `failure_repr`, `failure_repr_truncated`,
`traceback`, `traceback_truncated`, `captured_stdout`,
`captured_stdout_truncated`, `captured_stderr` and
`captured_stderr_truncated` on `result` (lines 128–145), because RQ-29 and
ADR-5 create the *complete* schema at first use. Index 5,
`idx_result_failure_path_lineno`, already exists for the grouping this change
delivers. Only the SQL column lists change —
`sqlite_store._INSERT_RESULT` names fourteen columns and
`_SELECT_RESULTS_FOR_RUN` sixteen. Consequences: `meta.schema_version` stays
`2`, no database is refused, and an existing database keeps every row it holds
with `NULL` in the new columns. **This is exactly the payoff RQ-29 was written
to buy, collected for the first time.** It must be *verified*, not assumed:
the change owes a test that an existing database opens unrefused and reads
back its pre-change rows.

**2. The truncation marker in this repository is out-of-band, not in-band.**
`vantage.service.truncation.truncate()` cuts to `MAX_TEXT_FIELD_BYTES`
(64 KiB, on UTF-8 *bytes*, at a character boundary) and returns a `bool`; the
schema carries a sibling `<name>_truncated` column. Legacy RQ-22 said the
stored value "ends with a truncation marker". The implemented shape is a
deliberate improvement and this change adopts it unchanged: RQ-22's own third
criterion demands the marker be distinguishable from text the traceback
contained, and **any in-band sentinel can be forged by traceback content**,
while a sibling column cannot. `truncate()` is reused as-is. No second
truncation rule is invented.

## The tension the brief did not name: the 1 MiB report cap

`service/errors.py` sets `MAX_REPORT_BYTES = 1024 * 1024`, enforced by
`_read_bounded_body` *while streaming*, raising `PayloadTooLargeError` (413).
`run-recording` requires a session be observable in full or not at all, and
`session-ingestion` requires a report the server cannot accept to store
**nothing** — including its run and every other result it carried.

**The mechanism, stated sharply, because it is the sharpest thing here.**
`truncation.py` lives in `vantage.service`. **Truncation is server-side.** The
plugin cannot import it — RQ-24 forbids `pytest-vantage` any dependency but
pytest and the standard library — so the plugin sends the text *untruncated*,
the body breaches 1 MiB mid-stream, and the rejection discards the whole
session, run included. **Per-field truncation arrives after the point at which
it could have helped.** That is why the per-report budget must be spent in the
plugin, and why it is a scope item rather than a design detail.

### The arithmetic, corrected

An earlier draft of this proposal said "sixteen richly-failing tests exhaust
1 MiB". That figure was `1 MiB ÷ 64 KiB` — **one field at its maximum, not one
result**. It was wrong, and with the failure location and type now in scope
there are five unbounded-by-nature text fields per result, not three. The
working, so a reader can check it:

| Quantity | Bytes |
|---|---|
| `MAX_REPORT_BYTES` | 1,048,576 |
| Unbounded text fields per result — `traceback`, `captured_stdout`, `captured_stderr`, `failure_message`, `failure_repr` | 5 × 65,536 = **327,680** |
| Worst-case failed results that fit in an empty report | 1,048,576 ÷ 327,680 = 3.2 → **three fit** (983,040); the fourth breaches (1,310,720) |
| Headroom in a 500-result session (`run-recording` measures its body at 252,511 bytes) | 1,048,576 − 252,511 = **796,065** |
| Worst-case failed results that fit in *that* session | 796,065 ÷ 327,680 = 2.4 → **two fit** (655,360); the third breaches (983,040) |

`failure_type`, `failure_path` and `failure_lineno` are short by nature and are
not counted above.

**And that is the optimistic reading**, because those are byte counts of the
*text*, while the wire carries the text **JSON-escaped**. A traceback is
newline-, quote- and indentation-heavy; every `\n` costs two bytes encoded and
every control character six. The budget must therefore be spent on **encoded**
bytes, measured, not on character or byte counts of the raw string.

So: **three richly-failing tests, not sixteen, can cost a session everything.**
Per-field truncation is necessary and nowhere near sufficient. The budget
needs its own visible exhaustion flag per dropped field — same principle as
the truncation flag: silently absent evidence is worse than evidence marked
absent.

`run-recording`'s **Measurements** paragraph also binds us directly: *"Future
changes to the result schema or the batch-insert strategy MUST re-run the
measurement test ... and justify any material increase."* That re-run is in
scope.

## Where the text comes from, and what it costs

`pytest.TestReport` carries the rendered text, on pytest's own `BaseReport`
(verified in `_pytest/reports.py`, pytest 9.1.1): `longreprtext` renders
`longrepr` through a markup-free `TerminalWriter`; `capstdout`/`capstderr`
join the `Captured stdout*`/`Captured stderr*` sections.

**Decision Q1: the stored traceback is invariant to the user's `--tb` flag.**
`longreprtext` returns whatever pytest already rendered *for the terminal*, so
`--tb=no` stores nothing and `--tb=line` stores one line — `longrepr` is
reduced at build time, so the frames are gone before
`pytest_runtest_logreport` ever sees the report. A record whose completeness
depends on a *display* flag is not a record, and the failure mode is silent:
the database looks healthy, is empty of evidence, and nobody finds out until
the day they need it. The plugin therefore takes a
`pytest_runtest_makereport` hookwrapper and calls
`item.repr_failure(excinfo, style="long")` itself. Still pytest-only, so
RQ-24 holds. **The obligation is settled and in scope; the mechanism detail is
design's.**

What it costs, said plainly: a **second rendering per failed test**, on top of
the one pytest already did for the terminal. Rendering a long-style traceback
walks the frames and reads source, so this is not free against RQ-25's
recording-overhead budget — and `version-control-context`'s measurements have
already spent part of that budget. The overhead must be measured against the
current numbers, not the pre-VCS ones.

**Decision Q3 rides on Q1 and is nearly free because of it.** The same
hookwrapper holds `call.excinfo`, which carries `typename` directly, and
`longrepr.reprcrash` carries `message`, `path` and `lineno`. `failure_type`,
`failure_message`, `failure_path`, `failure_lineno` and `failure_repr` come
from the object already in hand. Q1 is what makes Q3 cheap.

Two remaining costs, neither of them free:

- **`capstdout` merges phases.** It concatenates setup, call and teardown
  sections with no separator and no attribution; `report.sections` keeps the
  phase in the section name. "What the code was doing beforehand" is weakened
  by a merge that cannot say *which* phase printed. Design's call.
- **Empty is not absent.** `capstdout` returns `""` both when the test printed
  nothing and when capture was disabled with `-s`. Collapsing those is the
  exact `x or None` defect RQ-5.2 and RQ-9.3 exist to prevent, and this module
  family names it as a forbidden idiom. The distinction must be preserved.

## Sensitive data: redaction deferred, opt-out shipped, said out loud

`assert response == expected` fails by printing both values. If the response
carried a token, the token is now in the database.

**Redaction is out of scope, and the honest interim position is that Vantage
will store credentials.** RQ-35's mechanism does not transfer: it matches
*option names* on a command line, and a traceback is free-form text with no
grammar to match against. Content-scanning arbitrary output is an unbounded
problem this change cannot close, and a redactor that misses once is worse
than none, because it converts a known hazard into a claimed guarantee.

Two facts bound the harm without excusing it. The store is local and
owner-only (`storage-permissions`), and nothing leaves the machine (RQ-28) —
so the reader is the same person whose terminal already showed the secret.
What genuinely changes is **lifetime**: a transient scrollback becomes a file
with no expiry that someone may later copy, back up, or attach to a bug
report. That is a new exposure, and it is this change's to disclose.

**Decision Q2: an opt-out ships alongside the disclosure.** Without one, the
only way to avoid unredacted storage is to not use the plugin at all, which
makes the disclosure an announcement rather than a choice. The shape must
respect RQ-2's opt-in rule: **activation stays an invocation flag and never a
committed configuration file that silently changes behaviour for everyone who
checks the repository out.** The precise invariant — a configuration value may
narrow what an already-activated session records, but nothing in a file may
ever *enable* capture — is stated here and its shape settled in design;
`config.py`'s existing CLI-over-environment-over-ini precedence
(`resolve_server_address`, `resolve_report_timeout`) is the pattern to follow.

Also delivered: the disclosure written into the capability spec and the
README, and **OQ-11** opened in `docs/open-questions.md` — the first new open
question since OQ-10, with OQ-1…OQ-10 all answered.

## A gap this change must close to stay honest

`history-read-api` → *Lean list projections* requires the excluded fields stay
*"reachable via the corresponding single-item endpoint"*. **There is no
single-result endpoint.** `GET /api/v1/runs/{run_id}/results` is a list;
`read.py` has no sibling detail route. Storing failure text without adding one
would satisfy the exclusion half while breaking its complement, and would
store data no reader can reach. A single-result read path is therefore in
scope, not optional. Settled, not a question.

## Scope

### In scope

- **Traceback capture invariant to `--tb`** (Q1): a
  `pytest_runtest_makereport` hookwrapper rendering `style="long"`, so a
  three-frame assertion names all three frames regardless of invocation flags
- **Failure location, type and message** (Q3): `failure_type` from
  `excinfo.typename`; `failure_message`, `failure_path`, `failure_lineno` from
  `longrepr.reprcrash`; `failure_repr` — separately queryable, so twenty tests
  failing at one source line come back as one group (index 5 already exists)
- Captured stdout and stderr, with empty distinguished from absent
- **A per-report failure-text budget in the plugin**, spent on JSON-**encoded**
  bytes before the request is built, with a visible budget-exhausted flag per
  dropped field
- **An opt-out** (Q2) that disables failure-text capture for a session, whose
  activation shape cannot become a silent config-file enable (RQ-2)
- Wire contract: the new optional `results[]` fields on `ResultReport`
  (`extra="allow"` already tolerates a newer plugin against an older server;
  the new fields must carry defaults so an older plugin is not rejected — a
  deliberate exception to that module's every-field-required rule)
- Server-side bounding through the existing `truncate()`/`MAX_TEXT_FIELD_BYTES`
  and the sibling `_truncated` flags — the server owns the bound, as it
  already does for `commit_subject` (design D49)
- `Result` gains the fields; `sqlite_store` and `memory` grow the same columns
  in lockstep; `vantage_port_contract.py` gains scenarios
- A single-result read path carrying the full record
- `history-read-api` → *Lean list projections*: the traceback/captured-output
  exclusion promoted **Inspection → Test**, and the comment in
  `test_routes_read.py::test_results_route_returns_paginated_envelope` retired
  by making its assertion possible (it says today: *"This stays Inspection,
  honestly, until failure capture lands a `traceback` field on `Result`; only
  then does `ResultItemResponse` omitting it become a claim this test can
  falsify"*)
- Hand-written `vantage/service/openapi/v1.yaml` updated by hand, never
  derived; the existing drift test enforces it
- `run-recording`'s Measurements re-run and justified; recording overhead
  re-measured against the current numbers
- The unrefused-existing-database test; the xdist-serialization test
- ADR-0016 (below); OQ-11; `docs/schema-manifest.md` updated

### Out of scope

- **Redaction of any kind**, command-line or content — see above and OQ-11
- **Retention, pruning, vacuum or any cumulative size policy** (Q4). Every
  individual write is bounded; the database is not. A CI machine recording
  every failing run accumulates up to 320 KiB per failed test, indefinitely.
  That is stated as a consequence and named as a separate future change; no
  policy is invented here
- **Structured frames** — one text blob per result, no `result_frame` table
- Log capture (`result_log`), artefacts (`artifact`), environment capture
- Any view, grouping UI, diffing or flake scoring over the captured text; this
  change makes grouping *queryable*, it does not present it
- `schema.sql`, `meta.schema_version`, ADR-0013's refusal path — unchanged by
  construction, only tested

## Capabilities

### New Capabilities

- `failure-evidence`: what a failed result records — the traceback rendered
  independently of display flags, the failure's type, message and location as
  separately queryable values, and the captured stdout and stderr — together
  with the 64 KiB per-field bound and its out-of-band flag, the per-report
  encoded-byte budget and its exhaustion flag, empty-versus-absent, the
  opt-out and its opt-in-rule constraint, and the disclosure that stored
  failure text is unredacted.

### Modified Capabilities

- `history-read-api`: *Lean list projections* — the traceback/captured-output
  exclusion half moves from **Inspection** to **Test** and its "cannot fail
  today" scenario is retired; legacy RQ-16's criterion (500 results each
  carrying a 40 KB traceback, response below 500 KB) becomes constructible for
  the first time. Gains a *Single result detail* requirement, without which
  the lean-list rule's own complement half is unmet.
- `session-ingestion`: the `results[]` contract grows the new optional fields;
  the two version-skew directions and the 1 MiB / whole-report-rejection
  interaction are stated where the writes live.
- `run-recording`: its **Measurements** paragraph is re-run and restated;
  the paragraph's own text obliges this.
- `result-capture`: Purpose gains a cross-reference to `failure-evidence`, so
  a reader of "what a result records" finds the failure evidence too.
  **Cross-reference, both directions**: `failure-evidence` names
  `result-capture` and `history-read-api` → *Lean list projections* verbatim.

`api-interface-document` is **not** modified: its existing drift requirement
already covers a new path, and the document is an affected file, not a changed
obligation.

**Forecast for `sdd-spec`**: ~10 requirements, ~26 scenarios — up from ~8/~22
on first writing, because Q1 makes `--tb` invariance its own obligation, Q3
adds failure location and type, and Q2 adds the opt-out.

## Approach

Plugin first, wire second, storage third, read surface fourth, proof last.
Capture moves one hook earlier than first planned — into
`pytest_runtest_makereport`, where `excinfo` still exists — because that is
the only place both the `--tb`-invariant rendering and the crash location can
be obtained. The budget is spent at assembly time on encoded bytes, before the
request exists, because a 413 costs the whole session and server-side
truncation is structurally too late to prevent it. The server keeps ownership
of the byte bound and the flags exactly as it does for `commit_subject`. Both
storage adapters move together — the contract suite forces agreement. The read
surface lands with the single-result path so the lean-list rule is never
briefly false.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `pytest_vantage/recorder.py` | Modified | `pytest_runtest_makereport` hookwrapper (Q1), fault-isolated like every other hook |
| `pytest_vantage/capture.py` | Modified | `style="long"` rendering, `reprcrash` fields, captured output, empty≠absent, encoded-byte budget |
| `pytest_vantage/config.py` | Modified | the capture opt-out (Q2), under the existing precedence rule |
| `vantage/core/domain/result.py` | Modified | `Result` gains the fields and their flags |
| `vantage/service/schemas.py` | Modified | `ResultReport` optional fields; `ResultItemResponse` still excludes the heavy ones; a new detail model |
| `vantage/service/routes/runs.py` | Modified | apply `truncate()`; map to `Result` |
| `vantage/service/routes/read.py` | Modified | single-result route; `_result_item` exclusion becomes falsifiable |
| `vantage/service/openapi/v1.yaml` | Modified | hand-written, never derived |
| `vantage/storage/{sqlite_store,memory}.py` | Modified | insert/select column lists, both adapters |
| `packages/vantage/tests/vantage_port_contract.py` | Modified | new scenarios |
| `packages/vantage/tests/test_routes_read.py` | Modified | the task-7.6 Inspection comment retired |
| `openspec/specs/history-read-api/spec.md` | Modified | exclusion promoted to Test |
| `docs/adr/0016-*.md` | New | see below |
| `docs/open-questions.md`, `docs/schema-manifest.md`, `README` | Modified | OQ-11, columns now populated, disclosure, opt-out documented |
| `schema.sql` | **Unchanged** | every column already exists (RQ-29) |

## ADR

**ADR-0016 — Store pytest's rendered failure text, bounded and unredacted.**
It earns one on the reversal-cost filter. Reversing means dropping populated
columns, which under ADR-0013 means a `schema_version` bump, which means every
existing database is *refused rather than migrated* — recorded history is
lost. It also retracts a stated privacy position. That is well beyond a sprint
to revert. `sdd-design` writes it; this proposal only names it.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| A failing session breaches 1 MiB and is dropped **whole** — three worst-case failures suffice, and server-side truncation is structurally too late | **High** | encoded-byte budget spent in the plugin, plus an explicit test at the boundary; this is the change's first-class obligation |
| A credential lands in the database | **Medium–High** | not mitigated — disclosed. OQ-11, spec text, README, plus the Q2 opt-out |
| The second rendering (Q1) eats RQ-25's recording-overhead budget | **Medium–High** | measure against current numbers, not pre-VCS ones; `version-control-context` already spent part of it |
| `longrepr` is not always an exception repr — for a skip it is a `(path, lineno, reason)` tuple, so naive `.reprcrash` access raises | Medium | `skip_reason`/`xfail_reason` columns exist for that shape; the non-failure branches must be handled explicitly, not assumed |
| Silent truncation read as a complete traceback | Medium | the `_truncated` sibling flag; a read model that drops it is a defect, same rule `commit_subject_truncated` already follows |
| Cumulative database growth is unbounded | Medium | **accepted, not solved** (Q4): per-write bounds only, stated as a consequence, named as a separate future change |
| The opt-out drifts into a config-file enable, breaking RQ-2 | Medium | the invariant is spec-level: a file may narrow, never enable; RQ-2's differential test is the guard |
| Version skew rejects an older plugin (new required fields) | Medium | fields carry defaults; both directions tested |
| Phase-merged captured output misattributes which phase printed | Medium | design; `report.sections` keeps the attribution if wanted |
| Failure text does not survive xdist report serialization | Low | asserted on the existing 3.10–3.13 × xdist matrix |
| Peak-memory regression on ingestion | Low–Medium | Measurements re-run is mandatory, not optional |
| Two adapters drift | Low | contract suite |

## Rollback Plan

1. **Per slice**: revert the branch. No schema statement was issued and
   `meta.schema_version` was never bumped, so a database written by the
   reverted build still opens — the columns simply stop being populated.
2. **Already-recorded failure text survives a revert as unread data.** If it
   must be *erased*, that is a `DELETE`/`VACUUM`, not a revert, and no tool
   ships for it here — which is the same gap Q4 leaves open deliberately.
3. **ADR-0016**: supersede rather than edit (CLAUDE.md); reopen OQ-11.
4. **The `history-read-api` promotion**: reverting restores the Inspection
   scenario and the `test_routes_read.py` comment verbatim.
5. **Measurements**: a text revert.

## Dependencies

- **None blocking.** `read-api` is archived; the read surface, the interface
  document, the drift test and `truncate()` all exist.
- No new third-party distribution. `pytest-vantage` stays pytest + stdlib
  (RQ-24) — `repr_failure`, `excinfo.typename` and `reprcrash` are all
  pytest's own; `vantage.core` and `vantage.storage` stay stdlib-only;
  Pydantic stays in `vantage.service`.
- **No blocked-on decisions remain for the spec phase.** Q1–Q4 are answered
  below; the phase-attribution question and the opt-out's exact surface are
  design's, and neither changes the shape of a spec.

## Delivery forecast (400-line review budget)

| # | Slice | Est. |
|---|---|---|
| 1 | `pytest_runtest_makereport` wrapper + `style="long"` rendering (Q1) | ~360 |
| 2 | Failure type/message/path/lineno/repr from `excinfo`/`reprcrash` (Q3) | ~380 |
| 3 | Captured stdout/stderr, empty≠absent | ~300 |
| 4 | Per-report encoded-byte budget + exhaustion flags | ~350 |
| 5 | Capture opt-out under the opt-in rule (Q2) | ~220 |
| 6 | Ingestion: optional fields, `truncate()`, `Result`, mapping | **~400** |
| 7 | Storage: both adapters, port contract scenarios | ~380 |
| 8 | Single-result route + list exclusion promoted + `v1.yaml` | **~420** |
| 9 | Measurements + overhead re-run, docs, ADR-0016, OQ-11 | ~300 |

**~3,110 lines across 9 slices**, up from ~2,090 across 6 — Q1 adds a hook and
a rendering path, Q3 adds five fields end to end, Q2 adds a configuration
surface. **Two slices exceed the 400-line budget and one sits exactly on it.**
This is a forecast, not a slicing — `sdd-tasks` owns that under `auto-chain`,
and this phase neither re-cut it nor accepted a `size:exception`.

```
Decision needed before apply: Yes
Chained PRs recommended: Yes
400-line budget risk: High
```

**Verification forecast:**

| Obligation | Method | Why |
|---|---|---|
| A three-frame failure names all three frames under `--tb=no` and `--tb=line` | **Test** | the invariance is the obligation; parametrise over the styles |
| Twenty tests failing at one line group as one | **Test** | legacy RQ-8's criterion, now constructible; index 5 exists |
| The location is the raising site, not the test function's first line | Test | |
| Oversized text bounded, flag set | Test | `truncate()` already has a suite to extend |
| Per-report budget keeps a heavy session under 1 MiB | Test | the 413 is observable |
| Empty output ≠ absent output | Test | `-s` versus a silent test |
| The opt-out suppresses capture; no file can enable it | **Test** | RQ-2's differential form is the precedent |
| List responses exclude the heavy fields | **Test** (was Inspection) | this change is what makes it falsifiable |
| Full record reachable on the single-result path | Test | |
| Existing database opens unrefused | Test | ADR-0013's non-firing, proven not assumed |
| Body size, peak memory, recording overhead | **Analysis** | `run-recording`'s and RQ-25's measurement obligations — numbers, not assertions |
| Stored failure text is unredacted and disclosed | **Inspection** | a documentation property; there is no assertion for "we told the truth" |
| Failure text survives xdist | Demonstration | the CI matrix |

## Success Criteria

- [ ] A failed result read back over HTTP says *why*, with all three frames,
      **whatever `--tb` the session was invoked with**
- [ ] Twenty tests failing at one source line come back as one group, by query
      rather than by eye
- [ ] The captured output is there beside it, and an empty one is
      distinguishable from an absent one
- [ ] A 10 MB traceback is stored bounded, with its flag, and never silently
- [ ] A session of many large failures is still recorded whole, and anything
      dropped for budget is flagged, not missing
- [ ] Someone who cannot accept unredacted storage can turn capture off
      without abandoning Vantage, and no committed file can turn it back on
- [ ] `test_routes_read.py`'s Inspection comment is deleted because the
      assertion it describes now exists and can fail
- [ ] `schema.sql` is byte-unchanged and an existing database opens unrefused
- [ ] `v1.yaml` describes the new path, by hand, and the drift test agrees
- [ ] Body size, peak memory and recording overhead are re-measured and any
      increase is justified in the spec
- [ ] OQ-11 exists and says plainly that stored failure text may contain
      credentials

---

## Decisions — 2026-08-22

The question round is closed. Four decisions, with the reasoning that produced
them, recorded here rather than left standing as open questions.

**Q1 — The stored traceback MUST be invariant to the user's `--tb` flag.
Decided: yes.** `longreprtext` returns whatever pytest already rendered for
the terminal, so `--tb=no` stores nothing and `--tb=line` stores one line. The
failure mode is silent: the database looks healthy, is empty of evidence, and
nobody discovers it until the day they need it. **A record whose completeness
depends on a *display* flag is not a record.** The plugin takes the
`pytest_runtest_makereport` hookwrapper and calls
`item.repr_failure(excinfo, style="long")`. Still pytest-only, so RQ-24 holds.
The cost — a second rendering per failed test, charged against RQ-25's
overhead budget — is accepted and stated. The obligation is in scope; the
mechanism detail stays design's.

**Q2 — An opt-out ships alongside the disclosure. Decided: yes.** Redaction is
deferred, so unredacted failure text will be stored. Without an opt-out the
only way to avoid that is to not use the plugin at all, which makes the
disclosure an announcement rather than a choice. The shape must respect RQ-2:
activation is by invocation flag, never by a committed configuration file that
silently changes behaviour for everyone who checks the repository out. The
spec-level invariant is that a configuration value may **narrow** what an
already-activated session records and may never **enable** capture.

**Q3 — Failure location and type come along. Decided: in scope.**
`failure_type`, `failure_message`, `failure_repr` and the path/lineno columns
already exist in `schema.sql`, index 5 exists to group by them, and
`excinfo.typename` plus `longrepr.reprcrash` supply them at the same hook,
from the same object Q1 already puts in hand. Decisive reason: the question
this change exists to answer — *is it the same failure or four different
ones?* — is **answered** by grouping on type and message, and only
**illustrated** by the traceback. Shipping the traceback alone would sell the
feature on a claim it does not deliver and leave a reader to eyeball blobs.

**Q4 — Total database growth is not this change's problem. Decided: out of
scope.** Every individual write is bounded — 64 KiB per text field, and a
per-report budget above that. Cumulative growth is stated honestly as a
consequence: a CI machine recording every failing run accumulates up to
320 KiB per failed test, indefinitely, and nothing here prunes it. Retention
is named as a separate future change. No retention or vacuum policy is
invented in this one.

**Settled without being asked — the single-result read endpoint is in scope.**
`history-read-api`'s lean-list rule requires excluded fields stay reachable
via the corresponding single-item endpoint, and no such endpoint exists for
results. Storing failure text without adding one satisfies half a requirement
and breaks its complement.

**Arithmetic corrected.** The first draft's "sixteen richly-failing tests
exhaust 1 MiB" was `1 MiB ÷ 64 KiB` — one field at maximum, not one result.
The corrected worst case is **three** results in an empty report and **two**
in a 500-result session, before JSON escaping; the working is shown inline in
*The arithmetic, corrected* above.
