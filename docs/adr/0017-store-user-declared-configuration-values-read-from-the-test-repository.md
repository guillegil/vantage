# 17. Store user-declared configuration values read from the test repository

Date: 2026-09-02

## Status

Proposed

## Context

Every context column `run` carries today — `python_version`, `pytest_version`,
`command_line`, `root_dir`, `vcs_*` — describes **the machine and the test
repository**. Not one describes **what is under test**. For teams whose test
repository is separate from the thing it exercises — hardware and firmware
verification, primarily — the meaningful version of the thing under test is
declared in a file inside the test repo. A git commit identifies the *tests*;
it says nothing about the firmware they ran against. Those teams cannot
currently group a run history by the only axis they care about.

**ADR-0016 already drew the boundary this decision sits against, and drew it
explicitly.** Its closing paragraph reads: *"This authorises storing text the
*test process* produced. It does not authorise storing the host environment,
the values on the recorded command line, log records, or test artefacts. Each
of those is its own decision and inherits these four conditions rather than a
pre-granted answer."* A file declared in and read from the test repository is
a test artefact. ADR-0016 withholds authorisation here rather than granting
it, and this decision is what supplies it — deliberately, not by omission.

**Two independent filters agree an ADR is required, not one.** The first is
the authorisation gap above. The second is reversal cost: under ADR-0013, an
older schema is refused at open, not migrated. Dropping a populated
`run_metadata` therefore means a `schema_version` bump, and a bump refuses
every existing database rather than migrating it — recorded history lost. Far
beyond a sprint to revert, which is the filter CLAUDE.md sets for needing an
ADR at all. `user-configuration` needed no ADR because `user_setting` holds
preferences a user typed into Vantage; this holds bytes read off their disk
without them typing anything into the product at all. Different risk class,
different answer.

**Why the precedent does not transfer whole.** RQ-2's opt-in boundary and its
design-level extension — no committed file may be the *means* by which
capture is enabled — hold unchanged: with the flag absent, the declaration's
presence produces zero reads, zero connection attempts, and a byte-identical
tree. What does not transfer is the *hazard shape*. `--vantage-failure-text`
widens among fields already in memory because pytest put them there. This
declaration additionally chooses **which filesystem reads happen at all**,
which moves the risk from "recording silently switched on" to "a colleague
added a line to a committed file, and my machine now reads a path and
uploads its bytes". Condition C2 below (paths resolve local to the test
repository, reviewed in a PR before the server ever sees them) answers a
*hostile server* directing reads. It does **not** answer a *colleague with
commit rights* adding a declaration line: after that merges, every checkout
reads the named path and uploads it, and nothing here claims otherwise. C3
and C5 exist because that second hazard is real and unmitigated by C2 alone:
C3 bounds what can be reached even by a legitimate declaration entry, and C5
makes the read surface auditable after the fact, which is the only thing
available once the read has already happened.

## Decision

**Vantage reads files that a test repository's own declaration names, and
stores the top-level scalar values it declares from them, under five
conditions that hold together and are not separable.**

**What is authorised, stated narrowly.** Reading files the test repository
*itself* names in a declaration committed to that repository, and storing
declared **top-level scalar** values read from them. Never the file bodies —
a content-addressed store for raw bodies is a future decision, not this one
— and never a key the declaration did not name. The declaration is data, not
code: it selects paths and keys, and does nothing else.

1. **Its own opt-in invocation flag, with no ini equivalent.** Consistent
   with RQ-2 and ADR-0016's condition 4, a committed configuration file may
   never be the means by which capture is enabled. Capture is absent unless
   a session's invocation asks for it, and the shipped `--help` actively
   denies an ini equivalent rather than merely omitting one.
2. **The declaration is local to the test repository, and reviewed in a
   PR.** The server never dictates which files the plugin reads — a
   compromised server would otherwise be able to ask for `~/.ssh/id_rsa`.
   This answers a hostile *server*. It does not, and cannot, answer a
   colleague with commit rights adding a declaration line — see Context.
3. **Every declared path resolves strictly under `rootpath`.** Absolute
   paths and post-resolution escapes are rejected, never clamped, and
   symlinks are resolved **before** the containment check runs — a
   committed symlink otherwise defeats containment entirely, since a
   pre-resolution check would compare the link's own path, not its target.
4. **Bounded twice, and the breach behaviour inverts ADR-0016's own rule.**
   Bounded plugin-side, per file and per report section, before the request
   is built, and bounded again server-side, per stored value. A breach at
   any layer **drops the unit whole**; nothing on this path is ever
   truncated. A truncated structured document is a syntactically different
   document that can parse into confidently wrong values, and a truncated
   value in an equality-queried column is a false value — silent wrongness
   in both cases, which is worse than an absence the run records honestly.
5. **The run names which files were read.** The read surface is auditable
   after the fact, from the recorded run, and not only reviewable before the
   fact from the committed declaration.

**This is entity-attribute-value, and the justification is stated rather
than left for a future reader to reconstruct.** EAV is normally a smell —
`user_setting` avoided it by keeping its value opaque, because nothing
queries inside it. Here it is justified because the key space is genuinely
user-owned and unknowable in advance: Vantage cannot enumerate
`firmware_version` as a column, because every team declares its own keys,
and the alternative shape — one opaque JSON blob per run — turns the
product's own query, "which runs carried this key and value", into a full
table scan with no index to serve it. EAV stops being laziness the moment
the columns cannot be known in advance, and that is the case here.

**A malformed declared document must not fail the run's ingestion.** This is
part of the decision, not merely a consequence of it. The start-write's job
is to make the run observable at all (RQ-44); a parse failure degrades to
"this file contributed no keys, and the run records that it failed", with
the run row written regardless. Interrupting ingestion over a co-worker's
typo in a configuration file would trade an observability guarantee for a
feature that exists to add context to it — the wrong trade, every time.

**What this does not authorise.** Not the host environment. Not arbitrary
file bodies — only the scalar values a declaration explicitly names. Not
server-directed reads of any kind — the server never names a path, only the
test repository's own committed declaration does. Not web-side editing of
the declaration or the files it names; editing a test-repository file from
the web interface is a separate capability with its own authorisation
question, not a consequence of this one. Not backfill: a key declared after
a run already exists has no value for that run, and none is invented for it.

## Consequences

- **Vantage will read and upload a file a co-worker named, on some machine,
  at some point.** Stated, not mitigated — the same posture ADR-0016 took
  for failure text. What bounds the exposure is that the declaration is
  committed, reviewed and auditable after the fact, not that the read
  itself is inherently safe. A configuration file is exactly where
  credentials live by convention, and this decision does not change that.
- **Reversal cost is why this ADR is mandatory in the first place.** Under
  ADR-0013, dropping a populated `run_metadata` requires a `schema_version`
  bump, and a bump refuses existing databases rather than migrating them —
  recorded history lost. `schema_version` moves 3 → 4, and every developer
  or CI database still open at version 3 is recreated once, the first time
  a build at this version opens it.
- **The overhead is O(1) per session, and the number is measured, not
  asserted.** The read cost is bounded — a fixed number of `stat`+`open`
  pairs of a bounded size, once, at session start — independent of test
  count, failure count, or worker count, which is a structurally different
  shape from failure-text capture's O(failures) cost. That shape is a
  reason to expect a good number against RQ-25's budget; it is not a
  substitute for measuring one, and the same re-measure obligation
  `version-control-context` and `failure-evidence` already carry applies
  here on the same harness.
- **Cumulative growth is unbounded, and nothing here prunes it.** The same
  posture ADR-0016 took for failure text: retention and pruning are named as
  a separate future change, not invented in this one.
- **The data has a horizon, and it is published rather than implied.** A
  run recorded before a key was ever declared has no value for that key, by
  design (no backfill), and a query for that key states how many runs
  predate it rather than presenting a silently shortened history as if it
  were the complete one.

## Alternatives rejected

**Store nothing; let teams correlate firmware version by hand outside
Vantage.** Costs nothing to keep and closes the authorisation question by
never opening it. Rejected because it leaves the motivating question
unanswerable inside the product this repository is building: a team cannot
ask "which runs used firmware 2.1?" of a database that never recorded the
answer, and the gap is exactly the one this decision exists to close.

**Store the whole declared document as an opaque blob, like
`user_setting.value`.** Free of the EAV question entirely. Rejected on the
same grounds `user-configuration` used to keep `user_setting.value` opaque
in the first place, applied in reverse: that column is opaque *because*
nothing queries inside it, and here querying inside it is the product. An
opaque blob would force a `json_extract` full scan for the one operation —
`WHERE key = ? AND value = ?` — this feature exists to serve with an index.

**Let the server name which files to read from a project it has never
seen.** Simpler for the plugin — no local declaration to parse — and
rejected outright. A server-directed read is precisely the hazard C2 exists
to foreclose: a compromised or careless server could ask an arbitrary
checkout for an arbitrary path, with no local review step ever standing
between the request and the read.

**Truncate an oversized declared value or document, as ADR-0016 does for
failure text.** Tempting for consistency with the sibling decision, and
rejected for the opposite reason condition 4 states in full: failure text
degrades gracefully under truncation because a cut traceback is still a
shorter true traceback, while a cut structured document or a cut
equality-queried value is a different, confidently wrong one. Consistency
with ADR-0016's *conditions* was kept; consistency with its *truncation
mechanism* was not, because the payloads do not share the property that
mechanism depends on.

**Defer the ADR to the slice that actually adds the read filter, matching
the proposal's original plan of landing it last.** Rejected once the
irreversibility of `schema_version` 3 → 4 was traced through ADR-0013: that
bump is the point of no easy return, and an authorisation arriving after a
database has already been opened at the new version is not an
authorisation, it is a formality. This decision lands before the bump that
depends on it, not after.

Bound to: ADR-0005, ADR-0009, ADR-0013, ADR-0014, ADR-0016, RQ-2, RQ-24,
RQ-25, RQ-26, RQ-28, RQ-29, RQ-44, and the `run-metadata`,
`opt-in-activation`, `session-ingestion`, `recording-schema` and
`history-read-api` capabilities.
