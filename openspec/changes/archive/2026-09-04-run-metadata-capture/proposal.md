# Proposal: run-metadata-capture

Capture user-declared configuration values from the test repository and store them per run, so a
reader can ask **"which runs ran `firmware_version` 2.1?"** and get an indexed answer.

**Phase:** `sdd-propose` · **Change:** `run-metadata-capture` · **Date:** 2026-09-02
**Artifact store:** hybrid — also saved to Engram as `sdd/run-metadata-capture/proposal`

## Intent

Every context column on `run` today — `python_version`, `pytest_version`, `command_line`,
`root_dir`, `vcs_*` — describes **the machine and the test repository**. Not one describes **what
is under test**. That is the gap.

For teams whose test repository is separate from the development repository — hardware and
firmware verification, primarily — the meaningful version of the thing under test is declared in
a file inside the test repo. A git commit identifies the *tests*; it says nothing about the
firmware they ran against. Those teams cannot currently group a run history by the only axis they
care about.

## Scope

### In scope

1. A new opt-in invocation flag that enables metadata capture (own flag, per ADR-0016 precedent).
2. A **declaration**, local to the test repository, naming which files to read and which keys to
   take from each.
3. Plugin-side: read the declared files at session start, bound them, ship them on the existing
   start-write. The plugin **does not parse the declared documents**.
4. Server-side: parse declared documents, extract only declared keys, normalise, persist.
5. `run_metadata(run_id, key, value, source_file)` + `(key, value)` index; `schema_version` 3 → 4.
6. A minimal read filter: exact `key=value` equality on the runs list surface.
7. ADR-0017 and a `docs/schema-manifest.md` section with corrected counts.

Item 6 is the last slice and the **first thing to cut** if the chain overruns. Cutting it ships
storage nobody can query yet — stated plainly rather than hidden.

### Out of scope

| Deferred | Why |
| --- | --- |
| Web-side editing of test-repo files | Postponed by the user (Engram #130) |
| A `vantage-client` daemon; remote execution | Category change, not a feature; needs auth first and its own ADR |
| Any bench / hardware entity | Missing entity acknowledged in #130; designed as inventory when built, not as a recorder accessory |
| Backfill of earlier runs | D-d — accepted consequence |
| Numeric or range comparison | D-c — string equality only |
| Storing raw file bodies | D-k — the Phase 2 content-addressed `artifact` table is its natural home |
| Retention / pruning of `run_metadata` | Same posture ADR-0016 took: named, not invented here |

## Decisions carried in (settled before this phase, not re-litigated)

| # | Decision | Rationale |
| --- | --- | --- |
| D-a | Narrow relational table, not a JSON blob. `run_metadata(run_id, key, value, source_file)`, PK `(run_id, key)`, index `(key, value)` | `user_setting.value` is opaque JSON *because* nothing queries inside it. Here, querying inside it **is** the product. A blob forces `json_extract` full scans with no index. Rule: opaque JSON when read whole; columns when filtered on |
| D-b | A **fact** of the run, not a preference. Written once at ingestion, never updated | Same family as `vcs_commit`. Consistent with the facts-vs-preferences split established in `user-configuration` |
| D-c | All values stored as `TEXT`, numbers included. Comparison is string equality | YAML/JSON/TOML type differently and the user declares keys, not types |
| D-d | Only user-declared keys stored, not the whole parsed document. No backfill | Accepted consequence: a key declared later has no history for earlier runs |
| D-e | This is EAV, and the ADR must say why it is justified | The key space is genuinely user-owned. Vantage cannot know `firmware_version` exists because every team invents its own. EAV stops being laziness when the columns cannot be known in advance |
| D-f | Its own opt-in flag | ADR-0016 made `--vantage-failure-text` separate because "if the response carried a token, the token is now in the database". A `config.yml` is the same risk class and worse — configuration files are where credentials live by convention. Recording ≠ attaching files |
| D-g | The file list lives **local**, in the test repo, reviewed in a PR. The server never dictates which files the plugin reads. Paths resolve under `rootpath`; no absolutes, no `..` | A compromised server would otherwise ask for `~/.ssh/id_rsa` |
| D-h | The plugin does not parse declared documents. It ships content + `content_type`; the server parses, where Pydantic already lives | Keeps RQ-24 intact (no PyYAML in the plugin) and sidesteps `tomllib` being 3.11+ while the floor is 3.10 |
| D-i | Rides the existing start-write (`recorder.py` `pytest_sessionstart`), beside `_vcs_section()` | Where `_capture_vcs` already sends context; makes the data visible while the run is still running |
| D-j | `schema_version` 3 → 4 | ADR-0013: an older database is refused at open, not migrated |
| D-k | The Phase 2 content-addressed `artifact` table is the future home for raw file bodies | The same `fw_version.json` across 200 runs stored once. Not built now; noted so it is not reinvented |

## Argument 1 — the RQ-2 boundary

**Claim: the precedent transfers on the WHETHER axis and does not transfer on the WHAT axis. The
WHAT axis needs two conditions no existing precedent supplies.**

RQ-2's literal text (`opt-in-activation/spec.md:14-17`) only forbids attempting a connection when
no recording option is present. The broader "no committed file may be the means by which capture
is enabled" rule is a design-level extension, established in `plugin.py:127-137`,
`failure-evidence/spec.md:206-214`, and ADR-0016 condition 4. `vantage_server`'s ini option is the
working precedent for a committed file that configures **WHERE**, never **WHETHER**.

**Where the precedent holds.** The declaration configures WHAT is attached, never WHETHER anything
is. With the flag absent, the declaration's presence produces zero reads, zero connection
attempts, and a byte-identical tree. That is RQ-2's own differential criterion, satisfied
unchanged. Nothing about a *read* weakens it: RQ-2 governs activation, and no read activates
anything.

**Where it does not.** `--vantage-failure-text` widens among fields **already in memory** because
pytest put them there. The declaration additionally chooses **which filesystem reads happen at
all**. That is a strictly larger widening in kind, and it moves the hazard: the risk is no longer
"recording silently switched on" but "a colleague added a line to a committed file and my machine
now reads a path and uploads its bytes". D-g answers this for a hostile *server*. It does not
answer it for a co-worker with commit rights. Treating the failure-text conditions as sufficient
here would leave the read surface unbounded, and *that* is what would break the precedent — not
the existence of a read.

**Conditions under which the design stays compliant.** C1–C3 are inherited; C4–C5 are new and pay
for the extension.

| # | Condition | Verified by |
| --- | --- | --- |
| C1 | Flag absent → declaration never opened, no connection, byte-identical tree | Differential test, same shape as `test_opt_in.py` |
| C2 | Declaration read only after the activation gate **and** the metadata gate pass | Mirrors the `plugin.py:157-158` short-circuit; call-recorder test |
| C3 | No ini equivalent for the flag; the shipped `--help` **actively denies** one | Same assertion shape as `test_the_shipped_help_text_advertises_no_ini_equivalent` |
| C4 | **New.** Every declared path resolves strictly under `rootpath`. Absolute paths and post-resolution escapes are **rejected, never clamped**. Symlinks are resolved *before* the containment check | New tests; no precedent — a committed symlink otherwise defeats containment entirely |
| C5 | **New.** The run record names which files were read (`source_file`), so the read surface is auditable after the fact rather than only reviewable before it | Storage + read-surface tests |

**Consequence for the declaration's own format.** D-g forbids the server from telling the plugin
what to read, so the plugin must understand the declaration **itself**. D-h keeps parsers out of
the plugin, and RQ-24 forbids PyYAML there. Those three together leave exactly two admissible
declaration carriers: **pytest's own ini surface** (pytest parses it; the plugin calls `getini`)
or **stdlib-parseable JSON**. YAML is excluded as a declaration carrier — though it remains
available for the *declared* documents, which the server parses. This constrains an open question
the exploration left fully open.

## Argument 2 — size and parsing failure

**Claim: adopt ADR-0016 condition 2's two-layer bound unchanged, but invert its truncation rule.
Structured documents and equality-queried values must be dropped whole, never truncated.**

`MAX_REPORT_BYTES = 1 MiB` bounds the **whole request body**, start-write included, enforced
per-chunk. No per-file bound exists. ADR-0016 condition 2 already settled the shape: bound twice,
plugin-side on JSON-encoded bytes *before the request is built*, and server-side per field —
"because the server's 1 MiB report cap rejects the whole session, and per-field truncation runs
after the point at which it could have prevented that". Adopt it; do not reinvent it.

**What differs, and why plugin-side is not optional here.** For failure text, breaching the cap
loses the finish-write — results. Here it breaches the **start-write**, and per RQ-44 an
unrecorded start-write leaves the run with no row at all: not observable as running, abandoned or
interrupted. A server-only bound is insufficient **by construction**, because by the time the
server can reject, the run has already ceased to exist.

**What differs, and why truncation is wrong here.** `truncate()` degrades free text gracefully — a
cut traceback is still a shorter true traceback. Neither of this change's payloads behaves that
way:

- A truncated YAML/TOML/JSON document is a *syntactically different document*. It may parse
  successfully into **confidently wrong values**. Silent wrongness is worse than absence.
- A truncated value in an equality-queried column is a **false value**. `(key, value)` exists to
  serve exact matching; a truncated version string matches nothing and misleads a reader who sees
  it.

| Layer | Bound | On breach |
| --- | --- | --- |
| Plugin, per declared file | 64 KiB of UTF-8 (adopt `MAX_TEXT_FIELD_BYTES`; a declaration target needing more is not a config file) | Drop the file **whole**, record it as declared-but-uncaptured |
| Plugin, per report | Metadata section budget spent on JSON-encoded bytes before the request is built (proposal: 256 KiB) | Drop files until it fits, each one marked |
| Server, per value | Small dedicated bound (proposal: 1 KiB) — **not** 64 KiB; a 64 KiB value in a `(key, value)` index is bloat with no query value | Record the key as uncapturable; never store truncated |
| Server, whole body | Existing 1 MiB cap unchanged | Outer backstop only |

**Absent is marked absent, never merely missing** — ADR-0016's rule, inherited. A declared file
that was not captured must be visible on the run.

**Parsing error taxonomy (entirely new — this codebase parses no documents today).** Parsing lives
in `vantage.service`, where third-party dependencies are allowed. The governing rule:

> A malformed declared document MUST NOT fail the run's ingestion. The start-write's job is to
> make the run observable (RQ-44). A parse failure degrades to "this file contributed no keys, and
> the run records that it failed", with the run row written regardless.

That is `recording-fault-tolerance`'s existing posture — the plugin never breaks the suite —
applied for the first time to the server side.

| Class | Detected | Outcome |
| --- | --- | --- |
| Declared path missing on disk | Plugin | Marked; run proceeds |
| Path rejected (absolute, escapes `rootpath`, symlink escape) | Plugin | Marked; run proceeds. **Never** silently clamped |
| File over the per-file bound | Plugin | Dropped whole, marked |
| Not UTF-8 decodable | Plugin | Rejected as uncapturable before JSON encoding — declaration targets are text by definition |
| Document malformed | Server | No keys from that file; marked |
| Declared key absent from a well-formed document | Server | Marked absent for that run |
| Declared key present but **non-scalar** (list/mapping) | Server | Key marked uncapturable. **Not** JSON-serialised into the value — that would store something present but unqueryable, the exact failure D-a rejects |

**Runtime floor.** `tomllib` is 3.11+ and the floor is 3.10, so TOML needs `tomli` server-side.
YAML needs PyYAML server-side. Both are permitted in `vantage.service` only and both are new
`deptry`-visible server dependencies. JSON needs nothing. Which formats ship in the first slice is
escalated below — it is the difference between zero new dependencies and two.

## Decisions made in this phase

| # | Decision | Rationale |
| --- | --- | --- |
| P-1 | `source_file` stores the **declared** path, rootpath-relative, exactly as written | It is what a human recognises and greps the declaration for. The resolved path is machine-specific and absolute — ADR-0016 already flagged absolute paths as carrying usernames. Store declared, validate against resolved |
| P-2 | `run_metadata.value` gets a small dedicated bound (~1 KiB), not `MAX_TEXT_FIELD_BYTES` | The column is queried by equality; a truncated value is a false value |
| P-3 | Non-UTF-8 declared files are rejected at the plugin, before encoding | The transport is JSON-only; a binary declaration target is a misconfiguration, not a payload |
| P-4 | Declaration carrier is restricted to pytest's ini surface or stdlib JSON | Forced by D-g + D-h + RQ-24 (see Argument 1) |
| P-5 | Non-scalar declared values are marked uncapturable, not serialised | Present-but-unqueryable is the failure D-a exists to prevent |
| P-6 | **Proposed direction, `sdd-design` confirms:** metadata rides inside `record_session` (explore Option 1) | D-b makes it a run fact, and `vcs` already rides inside the same call. One transaction avoids the partial-failure question Option 2 creates by construction. Cost: a wider signature, and every caller updated for `mypy --strict` |
| P-7 | **This change needs a new ADR (0017)** | Two independent filters agree. (a) ADR-0016 closes by explicitly *withholding* authorisation: "It does not authorise storing the host environment, the values on the recorded command line, log records, or **test artefacts**. Each of those is its own decision and inherits these four conditions rather than a pre-granted answer." A declared repository file is a test artefact. (b) Reversal cost: under ADR-0013, dropping a populated `run_metadata` means a `schema_version` bump, and a bump **refuses existing databases rather than migrating them** — recorded history lost. Far beyond a sprint. `user-configuration` needed no ADR because `user_setting` holds preferences a user typed into Vantage; this holds bytes read off their disk without them typing anything. Different risk class, different answer |

## Capabilities

### New capabilities

- `run-metadata`: user-declared configuration values captured from the test repository and stored
  per run — the declaration surface, the opt-in gate, path containment, bounds, the parse-error
  taxonomy, and the storage contract.

### Modified capabilities

- `opt-in-activation`: a second capture flag with its own gate and its own active `--help` denial
  of an ini equivalent; RQ-2's differential criterion extended to cover the declaration file's
  inertness.
- `session-ingestion`: a third top-level wire section beside `run`/`vcs`; new per-file and
  per-section bounds; the parse-failure taxonomy and its must-not-fail-the-run rule.
- `recording-schema`: `run_metadata` table + `(key, value)` index; `schema_version` 3 → 4.
- `history-read-api`: exact `key=value` filtering on the runs list surface (final slice).

## Approach

| Step | Where | What |
| --- | --- | --- |
| 1 | `vantage/storage/schema.sql`, `connection.py`, both adapters, `core/ports/storage.py` | Table + index after the last table, before the index block (D82 precedent); `_SCHEMA_VERSION` bumped in the same commit as the stamp; frozen stdlib dataclass on the port |
| 2 | `pytest_vantage/plugin.py`, `config.py` | Flag registration in the existing `group.addoption` block; a `resolve_*_capture`-shaped pure function with **no ini and no env parameter in its signature**; called identically on both xdist branches |
| 3 | `pytest_vantage/recorder.py` | Read + validate + bound the declaration once in `__init__`; serialise a frozen snapshot in a `_metadata_section()` mirroring `_vcs_section()` |
| 4 | `vantage/service/schemas.py`, `routes/runs.py` | New report model; a normalizer following `_to_vcs_context`'s shape but parsing a nested document — novel |
| 5 | `vantage/service/routes/` + read surface | Equality filter over the `(key, value)` index |
| 6 | `docs/adr/0017-*.md`, `docs/schema-manifest.md`, README | ADR; new `### run_metadata` section; corrected table/column/index counts |

## Affected areas

| Area | Impact | Note |
| --- | --- | --- |
| `packages/pytest-vantage/src/pytest_vantage/{plugin,config,recorder}.py` | Modified | Flag, gate, session-start read |
| `packages/pytest-vantage/src/pytest_vantage/transport.py` | Unchanged | JSON-only wire confirms D-h's "raw bytes" means UTF-8 text in JSON |
| `packages/vantage/src/vantage/service/{schemas,routes/runs,errors,truncation}.py` | Modified | Report model, parsing, bounds |
| `packages/vantage/src/vantage/storage/{schema.sql,connection.py,sqlite_store.py,memory.py}` | Modified | Both adapters move in one commit (D86) |
| `packages/vantage/src/vantage/core/ports/storage.py` | Modified | New dataclass; `record_session` signature (P-6) |
| `packages/vantage/tests/test_schema_manifest.py` | Modified | Hardcodes 11 tables / 130 columns / 14 indexes — **fails by design** until updated |
| `packages/vantage/tests/test_architecture.py` | Verified | RQ-26/RQ-30 purity walk must still pass |
| `docs/schema-manifest.md`, `docs/adr/0017-*.md`, `README` | New/Modified | Pre-existing drift in the manifest's narrative block (lines 364-403, "Table count 10") is **not** this change's obligation, but a reviewer may attribute it here — call it out in the PR |

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Line budget overruns badly | **High** | See forecast. `feature-branch-chain`, 5 slices; slice 5 is the declared cut point |
| Symlink escape defeats path containment | Medium | C4 — resolve before containment check; explicit test |
| A parse failure kills the start-write and the run vanishes (RQ-44) | Medium | Must-not-fail-the-run rule; plugin-side bound *before* the request is built |
| Truncation silently produces wrong values | Medium | P-2 / drop-whole rule; no `truncate()` on this path |
| RQ-25 overhead: a new session-start filesystem read | Medium | Cost is **O(1) per session**, not O(failures) as failure-text was — but `version-control-context` already spent part of the budget, so this must be **measured** on the same harness, not asserted |
| Two new server dependencies (`tomli`, PyYAML) | Medium | Escalated as Q4 — JSON-only ships with zero new dependencies |
| RQ-24 breached by a declaration parser in the plugin | Low | P-4 restricts the carrier to pytest's ini surface or stdlib JSON |
| ADR-0017 stalls the chain | Low | Write it in slice 5; slices 1–4 are implementation of settled decisions |

## Changed-line forecast — honest

**1,400–1,900 changed lines across 5 chained PR slices. Expect the upper half.**

The closest precedent, `user-configuration`, forecast 450–600 and **measured ~1,090** — an
under-forecast by ~1.9×, for one table plus port methods plus routes plus docs. This change is
strictly larger: it adds everything that change had, plus a new plugin flag surface, a new
filesystem-read-and-validate surface, a server-side parsing layer with no precedent anywhere in
the codebase, and an ADR.

| Slice | Content | Est. |
| --- | --- | --- |
| 1 | Schema, index, `_SCHEMA_VERSION` 3→4, both adapters, port dataclass, manifest + counts | ~350 |
| 2 | Plugin flag, resolver, `--help` denial, differential opt-in tests | ~300 |
| 3 | Declaration parse, path containment + symlink, file read, bounding, wire section | ~400 |
| 4 | Server report model, parsing, error taxonomy, normalizer, ingestion tests | ~400 |
| 5 | Read filter, ADR-0017, README | ~300 |

`Decision needed before apply: Yes` · `Chained PRs recommended: Yes` · `400-line budget risk: High`

## Rollback plan

Rollback cost is **not symmetric across the slices**, and that is the plan's main content.

- **Slices 2–5 (plugin, server, read filter, docs):** ordinary revert. Nothing persists that a
  revert cannot undo. The flag disappears and unflagged sessions were never affected.
- **Slice 1 (schema) before any database is populated:** revert the commit, restore
  `_SCHEMA_VERSION` to 3. Clean.
- **Slice 1 after a user's database has been opened at version 4:** **not revertible in place.**
  ADR-0013 refuses an older schema rather than migrating it, so a downgraded Vantage refuses that
  database. The recovery is forward-only — supersede, do not revert — or the user deletes the
  store and loses history. This is exactly the reversal cost that makes P-7's ADR mandatory.

Practical consequence: land slice 1 **behind** the rest of the chain if the chain is at risk of
being abandoned, or accept that slice 1 merging is the point of no easy return.

## Success criteria

- [ ] With the flag absent, a suite run with a declaration file present produces a **byte-identical
      tree** to one without it, and attempts zero connections (C1, differential test).
- [ ] The declaration file is opened **zero times** when either gate is closed (C2, call recorder).
- [ ] The shipped `--help` contains an active denial of an ini equivalent; the phrase "or the ini
      equivalent is given" is asserted **absent** (C3).
- [ ] An absolute path, a `..` escape, and a **symlink** escape are each rejected, not clamped (C4).
- [ ] A declared file larger than the per-file bound is dropped whole, the run is still recorded,
      and the run reports it as declared-but-uncaptured.
- [ ] A malformed declared document does not prevent the run row from being written (RQ-44 holds).
- [ ] A non-scalar declared value is marked uncapturable, never serialised into `value`.
- [ ] `run_metadata` values are never stored truncated.
- [ ] Session-start overhead with the flag set is **measured** against RQ-25's budget on the same
      harness `version-control-context` used, and the number is written down — not asserted.
- [ ] `test_schema_manifest.py` and `docs/schema-manifest.md` agree on the new counts.
- [ ] `test_architecture.py` still passes: `vantage.core` imports nothing; `pytest-vantage` gains
      no third-party dependency (RQ-24).
- [ ] ADR-0017 exists, is bound to ADR-0013/0016 and RQ-2/24/25/29/44, and states the EAV
      justification (D-e).

## Counts

**Measured in this phase:** 18 capability specs in `openspec/specs/`; 14 ADRs, highest `0016` — so
the new one is **0017**. `MAX_TEXT_FIELD_BYTES = 64 * 1024` confirmed in `truncation.py:20`;
ADR-0016's test-artefact non-authorisation confirmed at its closing paragraph (lines 95-97).

**Carried from the verified exploration:** 11 tables / 130 columns / 14 indexes, `_SCHEMA_VERSION
= 3`; 157 plugin tests across 14 files; 302 server tests across 25 files.

## Questions for the user

These are genuine product decisions. A reasonable person could want either answer and the code
cannot decide them. Answer, skip (defaults below apply), or ask for a second round.

**Q1 — Where does the declaration live?** P-4 narrowed the field to two admissible carriers.

| Option | Reads like | Costs |
| --- | --- | --- |
| **A.** pytest's ini surface (`pyproject.toml` / `pytest.ini`) via `config.getini` | `vantage_metadata = fw.json:firmware_version,hw_rev` | Zero new parsing; pytest already parses it. Awkward to read for several files; ini's flat types force a delimiter grammar |
| **B.** A dedicated `vantage-metadata.json` in the test repo | A readable nested object | Far more readable, self-documenting, obviously PR-reviewable. One more file in the repo root; JSON has no comments |

*Default if you skip:* **B** — the declaration is a security-relevant surface reviewed in a PR, and
readability serves review more than terseness serves typing.

**Q2 — What does a query return for runs recorded before a key was declared?** D-d rules out
backfill, so these runs genuinely have no value for that key.

- **A.** Silently excluded from `firmware_version=2.1`. Simple, but a reader sees a shorter history
  and may conclude those runs did not match, rather than that the question was not asked yet.
- **B.** Excluded from the match, but the surface reports "N runs predate this key". Honest about
  the horizon; more work on the read surface.

*Default if you skip:* **B** — a silently shortened history is the kind of quiet wrongness this
project has consistently refused elsewhere.

**Q3 — Flag set, no declaration found. Silent, or a warning?**

- **A.** Silent no-op — consistent with `recording-fault-tolerance`'s posture that the plugin never
  intrudes on the suite.
- **B.** A pytest warning — the user explicitly asked for something and got nothing, which is
  almost always a misconfiguration, not an intent.

*Default if you skip:* **B** — the flag is a deliberate act, and silence rewards a typo with a
green run and no data.

**Q4 — Which declared-document formats ship in the first slice?**

- **A.** JSON only. **Zero new server dependencies.** Excludes YAML, which is where most hardware
  teams actually keep this.
- **B.** JSON + YAML. Adds PyYAML to `vantage.service` (permitted there; `deptry`-visible).
- **C.** JSON + YAML + TOML. Also adds `tomli`, because `tomllib` is 3.11+ and the floor is 3.10.

*Default if you skip:* **B** — YAML is the format the motivating case actually uses, and TOML can
be added later without a schema change or a wire change.

---

## Resolved product questions

Answered by the user on 2026-09-02. Every question resolved to the proposal's own default;
the reasoning below is the proposal's, confirmed rather than overridden.

| # | Question | Answer | Consequence |
| --- | --- | --- | --- |
| Q1 | Where the declaration lives | **A dedicated `vantage-metadata.json`** in the test repository root, parsed by the plugin with stdlib `json` | The declaration is a security-relevant surface reviewed in a PR; readability serves review more than terseness serves typing. Rules out the ini carrier and its invented delimiter grammar. JSON has no comments — accepted. |
| Q2 | Query semantics for runs predating a key | **Excluded from the match, and the surface reports "N runs predate this key"** | The read surface carries an extra obligation. A silently shortened history is the quiet wrongness this project has refused everywhere else; the horizon of the data is stated rather than implied. |
| Q3 | Flag set, no declaration file found | **A pytest warning** | Setting the flag is a deliberate act. Silence would reward a typo with a green run and no data. Departs from `recording-fault-tolerance`'s silent posture deliberately, and only for this misconfiguration case — a *malformed* declaration still never fails ingestion. |
| Q4 | Declared-document formats in the first slice | **JSON + YAML** | Adds PyYAML to `vantage.service` only — permitted there, `deptry`-visible, and never reachable from `pytest-vantage` or `vantage.core`. YAML is the format the motivating hardware case actually uses. TOML is addable later without a schema or wire change, so `tomli` is not taken on for the 3.10 floor. |

**Unchanged by these answers:** the declaration carrier being plugin-parsed with stdlib `json`
(Q1) is exactly the constraint P-4 derived from D-g + D-h + RQ-24 — the answer confirms the
derivation rather than competing with it. YAML remains excluded as a *declaration carrier* and
permitted as a *declared document*, parsed server-side.
