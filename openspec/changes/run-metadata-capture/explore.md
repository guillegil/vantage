# Exploration: run-metadata-capture

User-declared configuration values captured per run.

**Phase:** `sdd-explore` · **Change:** `run-metadata-capture` · **Date:** 2026-09-02
**Artifact store:** hybrid — also saved to Engram as `sdd/run-metadata-capture/explore`

## Current State

**Start-write path (D-i's ride-along point).** `Recorder.__init__`
(`packages/pytest-vantage/src/pytest_vantage/recorder.py:165-186`) captures VCS once via
`_capture_vcs(Path(str(config.rootpath)))` and stores the snapshot on `self._vcs`.
`pytest_sessionstart` (line 200-234, `@liveness_isolated`) builds
`report = {"run": {...}, "vcs": self._vcs_section()}` and calls
`send(self._address, report, timeout=self._liveness_timeout)`. `_vcs_section()` (line
188-198) serialises the frozen snapshot: `commit`, `branch`, `commit_subject`, `dirty`,
`root`. A metadata section would be a third top-level key alongside `run`/`vcs`, built and
frozen the same way — captured once, reused verbatim, per the D51 rule the docstring cites.

**Transport is JSON-only.** `transport.send`
(`packages/pytest-vantage/src/pytest_vantage/transport.py:36-61`) does
`json.dumps(report).encode("utf-8")` and POSTs with `Content-Type: application/json`. There
is no multipart or binary path. D-h's "ships raw file bytes" therefore means UTF-8 text
embedded in the JSON envelope — fine for YAML/JSON/TOML, all textual formats — not literal
arbitrary binary. A non-UTF-8-decodable declared file is an open question nothing today
answers.

**Server ingestion.** `create_run` (`packages/vantage/src/vantage/service/routes/runs.py:317-349`)
is the single endpoint both the start-write and the finish-write hit.
`_require_json_media_type` rejects anything but `application/json`. `_read_bounded_body`
(line 282-314) streams the body and raises `PayloadTooLargeError` the instant the running
total exceeds `MAX_REPORT_BYTES = 1024 * 1024` (1 MiB,
`packages/vantage/src/vantage/service/errors.py:32`) — enforced per-chunk, not via
`Content-Length`.

**This cap applies to the whole request body, start-write included.** A large declared
config file competes with the `run`/`vcs` section for the same 1 MiB budget — exactly the
failure mode `test_vcs.py`'s `test_a_huge_commit_subject_is_bounded_before_it_reaches_the_wire`
documents for a 200 KB commit subject: "a large enough one pushes the report past
MAX_REPORT_BYTES, which the server rejects as a unit. Every result in that session would be
lost." No per-file or per-attachment bound exists today; only the whole-report cap and the
unrelated 64 KiB `MAX_TEXT_FIELD_BYTES` per-string-field bound (`truncation.py`), which
nothing currently applies to a whole file's content.

`_to_vcs_context` and `_to_failure_evidence` (`runs.py:109-135, 150-206`) are the two
existing normalizer shapes: parse the wire section, apply `truncate()` where relevant,
collapse an all-null shape to `None`. A metadata normalizer would follow the same shape but
is novel in one way: D-h says the plugin does not parse, so `vantage.service` parses the file
content into key/value pairs — **parsing logic this codebase does not have anywhere yet**.
Existing normalizers only reshape already-atomic scalar fields; none parses a nested
document.

**Opt-in flag machinery — closest precedent is `--vantage-failure-text`.**
`pytest_addoption` (`plugin.py:47-96`) registers CLI options in one `group.addoption` block;
`--vantage-failure-text` (line 74-86) is `action="store_true", default=False`, with help text
naming the risk. The active-denial sentence `"there is no ini equivalent"` is asserted
present in the rendered `--help` output by
`test_the_shipped_help_text_advertises_no_ini_equivalent` (`test_opt_in.py:194-220`), and
`"or the ini equivalent is given"` is asserted absent.

`resolve_failure_text_capture` (`packages/pytest-vantage/src/pytest_vantage/config.py:86-108`)
is a pure function with **no ini parameter and no env parameter in its signature at all** —
`activated and cli_opt_in`, monotone increasing in `cli_opt_in`, bounded by `activated`.
`_failure_text_capture_requested` (`plugin.py:140-162`) short-circuits to `False` before ever
touching the opt-in surface if `_activation_requested` (i.e. `--vantage`) is absent, and is
called identically on both the xdist worker branch and the controller branch of
`pytest_configure` (`plugin.py:165-239`) — the opt-in is session-wide, not controller-only.

## RQ-2 boundary — precise evidence

RQ-2's spec text (`openspec/specs/opt-in-activation/spec.md:14-17`) is narrow:

> Where no recording option is present in the pytest invocation, the plugin MUST attempt no
> connection to the server.

That literal text says nothing about configuration files. The broader "no committed file may
enable X" principle is a **design-level extension** of RQ-2, established three times, not
restated in RQ-2 itself:

1. `_activation_requested`'s docstring (`plugin.py:127-137`): "A config file committed by one
   person must never silently enable recording for everyone who checks the project out."
2. `openspec/specs/failure-evidence/spec.md` — "Capture is opt-in, absent by default"
   (lines 206-214): *"A configuration value MAY narrow what an already-activated session
   records, but no committed configuration file MAY be the means by which capture is enabled
   — the same invariant RQ-2 already holds for recording itself, and for the same reason."*
   Its own scenario (lines 239-242): "A committed configuration file cannot enable capture on
   its own", verified differentially exactly as RQ-2 is.
3. ADR-0016 condition 4 (lines 83-92): *"Consistent with RQ-2, a committed configuration file
   may **widen** what an already-activated session records and may never be the means by
   which capture, or recording itself, is enabled — there is no configuration syntax that
   activates recording on its own."*

The existing `vantage_server` ini option is the working precedent: its own help string reads
*"Configures WHERE; never activates recording (RQ-2)."*

Applying this to D-g: the declaration file plays the "widening" role — configuring WHAT,
never WHETHER. That is consistent with the established pattern **provided** (not yet decided):

- the declaration file's mere presence, with the new flag absent, produces a byte-identical
  tree and zero connection attempts — the same differential test shape RQ-2 already uses;
- the file is read only after the new flag's own gate passes, mirroring the short-circuit at
  `plugin.py:157-158`;
- the flag has no ini equivalent, and the shipped `--help` text actively denies one.

**The genuinely open part**, for which the failure-text precedent has no analogue: the
declared file does not merely toggle *which already-in-memory fields to include* — it names
*which filesystem reads happen at all*. Whether that distinction matters to RQ-2 compliance
is settled by no existing precedent and must be argued explicitly in design, not assumed to
transfer.

## Affected Areas

| Location | Why |
| --- | --- |
| `pytest_vantage/recorder.py:165-234` | `__init__`, `_vcs_section`, `pytest_sessionstart` — where a metadata snapshot is captured once and serialised, mirroring `_vcs_section` (D-i, D-b) |
| `pytest_vantage/plugin.py:47-239` | new flag registration, activation gate, both xdist branches of `pytest_configure` (D-f) |
| `pytest_vantage/config.py:55-123` | a new `resolve_*_capture`-shaped pure function, no ini/env parameters (D72 precedent) |
| `pytest_vantage/transport.py:36-89` | JSON-only wire — confirms D-h's "raw bytes" is UTF-8 text in JSON |
| `vantage/service/routes/runs.py:42-350` | `create_run`, `_read_bounded_body`, normalizer shape to imitate; new parsing logic with no analogue |
| `vantage/service/schemas.py` | `RunReport`, `VcsReport` — needs a new report model; D-h places all parsing here |
| `vantage/service/errors.py:32,147-154` | `MAX_REPORT_BYTES`, `PayloadTooLargeError` — the one existing size gate |
| `vantage/service/truncation.py` | whether `run_metadata.value` gets the 64 KiB bound, a different one, or none is open |
| `vantage/storage/schema.sql` (297 lines) | new table + index after the last table, before the index block, per D82's `user_setting` precedent; stamp at line 296 |
| `vantage/storage/connection.py:42` | `_SCHEMA_VERSION = 3` -> `4`, same commit as the stamp (D-j) |
| `vantage/core/ports/storage.py:120-149` | `UserSetting` structural precedent; `ExecutionStore` Protocol — `record_session` has no natural third collection parameter yet |
| `vantage/storage/sqlite_store.py`, `memory.py` | both adapters move in one commit (D86) |
| `vantage/tests/test_architecture.py` | RQ-26/RQ-30 purity walk — any new dataclass is plain stdlib in `vantage.core` |
| `vantage/tests/test_schema_manifest.py:216-221` | hardcodes 11 tables / 130 columns / 14 indexes — must be updated or it fails by design |
| `docs/schema-manifest.md` (434 lines) | needs a `### run_metadata` section |
| `pytest-vantage/tests/test_vcs.py` (377 lines) | closest analogue: real fixtures, `_CallRecorder` wrapping real calls, explicit budget tests, "huge value must not reach the wire" |
| `pytest-vantage/tests/test_opt_in.py` (221 lines) | exact shapes to replicate: tree-identity differential, ini-inertness, hand-built config double, shipped-help active denial |

**Pre-existing drift found, not this change's obligation.** `docs/schema-manifest.md`'s
"Comparison recorded" narrative section (lines 364-403) says "Table count 10" / "Index count
13", performed 2026-08-15 — already stale before this change, predating `user_setting`. The
machine-checked column and index tables elsewhere in the same file are current; only that one
narrative block drifted.

## Precedent: the most recent new-table change

`user-configuration` (archived 2026-08-29), decisions D82-D90, is the direct template: one
table placed after the last existing table and before the index block; `_SCHEMA_VERSION`
bumped in the same commit as the stamp; a plain frozen dataclass on the port that storage
never parses; new `Protocol` methods implemented in **both** adapters in one commit;
`docs/schema-manifest.md` updated with the new section and corrected counts; no new ADR
(reversal cost judged well inside a sprint).

That change forecast ~450-600 lines and measured **~1,090 across 4 chained slices**, with
"400-line budget risk: High". This change's surface — new table, new flag, new parsing layer,
path-traversal validation — is at least as large.

## Counts measured (grep, not estimated)

| Scope | Count |
| --- | --- |
| `packages/pytest-vantage/tests/` | **157** test functions across **14** files |
| `packages/vantage/tests/` | **302** test functions across **25** files |
| `@pytest.mark.req(id=...)` repo-wide | **165** across 27 files — includes non-code hits (`CLAUDE.md`, `README.md`, two archived `design.md`). Do not cite as "traced requirements" without filtering to `packages/**/tests` |
| Schema ground truth | **11** tables, **130** columns, **14** indexes; `_SCHEMA_VERSION = 3` |

Largest test files: `vantage_port_contract.py` 54, `test_routes_read.py` 34,
`test_ingestion.py` 25, `test_rejection.py` 23, `test_failure_paths.py` 36,
`test_run_report.py` 21.

## Approaches

The settled decisions fix the table shape, the opt-in flag and the plugin-does-not-parse
rule. The open structural choice is how the port exposes the new table.

**Option 1 — extend `record_session` with a third collection parameter.**
`record_session(self, execution, *, results, received_at) -> bool` gains
`metadata: Sequence[MetadataEntry]`, written inside the same transaction as the `run` and
`result` rows.

- **Pros:** one write path, one transaction; mirrors how `vcs` already rides inside
  `Execution`; fits D-b's "run fact, written once, never updated" — the same lifecycle as the
  run row, unlike `user_setting`, which is mutable and namespaced.
- **Cons:** widens an already-large signature; every caller (both adapters,
  `vantage_port_contract.py`, `scripts/measure_history_latency.py`) needs updating to keep
  compiling under `mypy --strict`.
- **Effort:** Medium.

**Option 2 — new port methods analogous to `list_settings`/`upsert_setting`.**

- **Pros:** no change to the stable `record_session`; isolates the new concern.
- **Cons:** two storage calls per ingested run instead of one atomic write, needing its own
  transaction-boundary decision — does a partial failure between the two leave an
  inconsistent run row? Option 1 avoids this by construction. The `user_setting` analogy is
  only partial: that table is genuinely mutable and namespaced; this data is write-once and
  run-scoped.
- **Effort:** Medium-High.

**Leaning (not decided — `sdd-design`'s call):** Option 1, because D-b explicitly classifies
this as a run fact and `vcs` already rides inside the same call rather than as a side channel.
Flag the transactional-atomicity question explicitly in design regardless of the choice.

## Risks

- **The RQ-2 boundary is a design judgment, not a settled fact.** RQ-2's literal text only
  forbids a connection attempt absent a recording option. Applying the "no committed file
  enables X" precedent to a file that names *filesystem paths to read* — not merely fields to
  widen — is a genuine extension of that precedent and must be argued.
- **No existing size bound applies to an attached file's content.** Only the 1 MiB
  whole-report cap and the unrelated 64 KiB per-string-field bound exist. A large declared
  file silently blows the whole start-write. Consequence is worse than for the finish-write:
  an unrecorded start-write leaves the run unobservable (RQ-44 territory).
- **No parsing precedent exists anywhere in this codebase.** Every existing wire normalizer
  reshapes already-atomic scalars. Document parsing and its error handling for malformed
  YAML/TOML/JSON is entirely unwritten and untested.
- **`docs/schema-manifest.md`'s narrative block is already stale.** Adding `run_metadata` does
  not worsen it, but a reviewer may attribute pre-existing drift to this change.
- **Line-count risk is High by the closest precedent** — `user-configuration` measured ~1,090
  across 4 chained slices from a 450-600 forecast. Expect chained slices under
  `feature-branch-chain`.

## Open Questions — explicitly not decided here

1. The declaration file's format and name. No precedent exists for "a declared list of files
   to attach"; only ini-shaped committed config exists today.
2. Missing-key semantics on read: does a query for `firmware_version=2.1` silently exclude
   pre-declaration runs, or return them with a null marker?
3. Value size limits for `run_metadata.value`. No existing convention obviously transfers —
   EAV values here are expected short, unlike the 64 KiB free-text fields the current bound
   targets.
4. Does `source_file` store the **declared** path or the **resolved** one, after
   rootpath-relative validation and `..` rejection?
5. Non-UTF-8-decodable declared files: the JSON-only transport requires the whole report to be
   `json.dumps`-able; a binary file cannot travel as `str` without an encoding step D-h does
   not specify.
6. Does metadata ride inside `record_session` (Option 1) or via new port methods (Option 2)?
   A transactional-atomicity question, not yet argued.
7. Interaction between the new flag and the declaration file: does the flag alone, with no
   declaration file, do nothing observable, or is that a warning-worthy misconfiguration? No
   existing test covers "flag present, nothing to widen."

## Ready for Proposal

**Yes.** Scope is bounded by the settled decisions, the codebase precedents are concrete and
directly reusable (`_capture_vcs`/`_vcs_section`, `--vantage-failure-text`/
`resolve_failure_text_capture`, `user_setting`/D82-D90), and every undecided question is
enumerated above rather than silently resolved.

`sdd-propose` should treat **the RQ-2 boundary argument** and **the file-size and
parsing-error-handling design** as the two items needing the most explicit reasoning, since
neither has a clean one-to-one precedent to copy.
