# Design: run-metadata-capture

> **Identifier vocabulary.** No numeric requirement identifiers are minted here
> (CLAUDE.md, decided 2026-08-18). Obligations anchor to the **capability** and
> its **scenario**; existing `RQ-xx` identifiers are cited only where the
> obligation already carries one. Decisions continue the project's single running
> sequence: `user-configuration` closed at **D90**, so this change opens at
> **D91**.
>
> **Every path, key name and file name in this document is invented.** Synthetic
> data only; public repository (CLAUDE.md, Constraints).
>
> **Probed, not remembered** (D80's process note). Everything this document
> asserts about existing code was read this session:
> `pytest_vantage/{plugin,config,recorder,budget,vcs}.py`,
> `vantage/service/{schemas,truncation,errors}.py`,
> `vantage/service/routes/{runs,read}.py`, `vantage/core/ports/storage.py`,
> `vantage/core/domain/sections.py`, `vantage/storage/{schema.sql,connection.py,
> sqlite_store.py,memory.py}`, `scripts/measure_vcs_overhead.py`,
> `docs/adr/0016-store-pytest-s-rendered-failure-text-bounded-and-unredacted.md`,
> `openspec/specs/version-control-context/spec.md`, `openspec/config.yaml`.
>
> **Size.** `sdd-design`'s generic 800-word budget is not applied. The project's
> own precedent (`archive/2026-08-29-user-configuration/design.md`, ~5,000 words)
> is the stronger template, and the skill's own rule — *use the project's ACTUAL
> patterns* — resolves the conflict in its favour.

## Technical Approach

The four resolved product answers and D-a…D-k are inputs, not subjects. What this
design settles is the two things the proposal handed over — the port shape (P-6)
and transactional atomicity — plus the seven surfaces that had no precedent
anywhere in this codebase: a declaration format, a filesystem-read security
boundary, a two-layer byte bound over structured documents, a marked-absence
representation, a parse-error taxonomy, a server-side parser, and an ADR.

The shape is a straight line through four layers that already exist, with exactly
two new modules:

- **The plugin reads and ships, and parses only its own declaration.** One new
  module, `pytest_vantage/metadata.py`, holding the declaration reader, the path
  containment check and the byte budget. Standard library only — `json`,
  `pathlib`, nothing else (RQ-24). It never parses a *declared* document.
- **The core stays pure and stdlib-only.** One vocabulary module and three
  frozen dataclasses on the port. No logic beyond the vocabulary itself (RQ-26).
- **The service owns every parser.** One new module, `service/metadata_parse.py`,
  is the single place `yaml` is imported. Nothing there may reject the report.
- **Storage stays generic and dumb.** Two tables and one extra keyword on
  `record_session`, written inside the transaction that already exists.

Two consequences worth naming up front. `schema.sql` gains **two** tables, not
one, because absence must be marked at two levels — a file that produced no keys
has no key to hang a row on (D91, D95). And **ADR-0017 lands in slice 1, not
slice 5** as the proposal planned: slice 1 is the commit ADR-0013 makes
irreversible, and an authorisation that arrives four PRs after the point of no
return is not an authorisation (D101).

---

## Architecture Decisions

### D91 — Two tables, schema version 4, one index

`run_metadata` alone cannot express "this file was declared and produced nothing",
because its primary key is `(run_id, key)` and a dropped file contributes no key.
C5 ("the run record names which files were read") is a **file-level** fact. So the
declaration's two levels each get a table, placed after `user_setting` and before
the `-- Indexes` block, keeping `schema.sql`'s "every table, then every index,
then the stamp" order:

```sql
-- ---------------------------------------------------------------------------
-- run_metadata_file -- one row per DECLARED file, captured or not. This table
-- is the audit surface C5 requires and the "absence is marked" record for a
-- file that contributed no keys at all (design.md D91, D95). `source_file` is
-- the DECLARED, rootpath-relative path exactly as written (P-1) -- never the
-- resolved one, which is absolute and can carry a username (ADR-0016).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_metadata_file (
    run_id       TEXT NOT NULL REFERENCES run (id),
    source_file  TEXT NOT NULL,
    content_type TEXT NOT NULL CHECK (content_type IN ('json', 'yaml', 'toml')),
    status       TEXT NOT NULL CHECK (status IN (
                     'captured', 'not_found', 'path_rejected', 'too_large',
                     'not_text', 'unreadable', 'over_budget', 'malformed')),
    PRIMARY KEY (run_id, source_file)
);

-- ---------------------------------------------------------------------------
-- run_metadata -- one row per DECLARED key. `value` is NULL whenever `status`
-- is not 'captured': a declared-but-uncaptured key is a row, never a missing
-- row (design.md D95). All values are TEXT, numbers included (D-c) --
-- comparison is string equality, and the declaration names keys, not types.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_metadata (
    run_id       TEXT NOT NULL REFERENCES run (id),
    key          TEXT NOT NULL,
    value        TEXT NULL,
    source_file  TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN (
                     'captured', 'absent', 'not_scalar', 'value_too_large',
                     'source_unavailable')),
    PRIMARY KEY (run_id, key)
);

CREATE INDEX IF NOT EXISTS idx_run_metadata_key_value
    ON run_metadata (key, value);                                   -- 15
```

| Choice | Why |
| --- | --- |
| `PRIMARY KEY (run_id, key)` | D-a's uniqueness rule in SQL. Its implicit index is left-anchored on `run_id`, so the per-run read is served without a second index — D82's `user_setting` argument, unchanged |
| `PRIMARY KEY (run_id, source_file)` | Same, for the file table. No index on it either, same reason |
| One new index, `(key, value)` | D-a. This is the product: `WHERE key = ? AND value = ?` is the whole feature, and a `json_extract` full scan over a blob is what D-a rejected. It also serves `WHERE key = ?` alone, left-anchored, which is what Q2's horizon count needs (D100) |
| `content_type` accepts `'toml'` now | RQ-29 and ADR-0005: the schema is complete at first use, including values nothing populates yet. Q4 defers the *parser*, not the column's vocabulary — adding TOML later must not bump the schema version |
| `CHECK` on both `status` columns | `result.outcome` sets the precedent. **Consequence, stated:** the SQL `CHECK`, the core `frozenset` (D92) and the Pydantic model must agree, and keeping those three in step is a task in its own right — exactly the note `design.md` already carries for `OUTCOMES` |
| `value TEXT NULL` | The one column in either table that is nullable, and deliberately: NULL is what makes "declared but not captured" representable at all. `status` says which |
| No `WITHOUT ROWID` | D82's answer, unchanged: no other table uses it, and one table with a different storage class is a difference a reader has to explain |

**`_SCHEMA_VERSION` becomes `4` and `schema.sql`'s last statement stamps `'4'` in
the same edit.** `connection.py:39-42`'s own comment states the invariant. No
migration, no `ALTER TABLE` — ADR-0013 governs, and this is the second change to
pay its price rather than the first to reopen it.

Counts to correct, measured against the current tree (11 tables / 130 columns /
14 indexes, `_SCHEMA_VERSION = 3`, stamp `'3'` at `schema.sql:296`):

| Quantity | Before | After |
| --- | --- | --- |
| Tables | 11 | **13** |
| Columns | 130 | **139** (`run_metadata` 5 + `run_metadata_file` 4) |
| Indexes | 14 | **15** |
| `meta.schema_version` | `3` | **`4`** |

`docs/schema-manifest.md` gains two `###` table sections and these corrected
header counts; `schema.sql`'s own header comment and its `-- Indexes` block
comment ("fourteen in total") both move. RQ-29 verifies the manifest by
Inspection, so this is the deliverable, not a note about one.
`packages/vantage/tests/test_schema_manifest.py:216-221` hardcodes the old three
numbers and **fails by design** until updated in the same commit.

### D92 — `vantage-metadata.json`: the declaration, and a flat key space

Q1 settled the carrier. This settles its shape. One file at the test repository
root, parsed by the plugin with stdlib `json`:

```json
{
  "version": 1,
  "files": [
    {
      "path": "config/firmware.yaml",
      "format": "yaml",
      "keys": ["firmware_version", "board_revision"]
    },
    {
      "path": "build/manifest.json",
      "format": "json",
      "keys": ["toolchain"]
    }
  ]
}
```

**A key is a flat name at the declared document's top level. There is no path
expression, no dotted path, no JSONPath.** The stored key and the document key
are the same string.

| Alternative | Why not |
| --- | --- |
| Dotted paths (`build.toolchain.version`) | Invents a grammar, at a security boundary, with its own escaping question the moment a real document has a key containing `.`. D-h moved parsing to the server precisely to avoid inventing parsers in the plugin; a *path* grammar puts a second one back in it |
| JSONPath / a query language | A dependency, or a hand-written parser. RQ-24 forbids the first in the plugin and the second is worse |
| A `{stored_key: document_key}` object form alongside the list | Two accepted shapes for one field is two parse-error classes for one feature. Deferred, not refused: a future object form is a strictly *widening* parse of the same field, addable without a schema or wire change — the same argument Q4 used to defer TOML |

The cost is stated rather than discovered: **a value nested inside a declared
document is not reachable in this slice**, and two files declaring the same
top-level key cannot both be captured. The motivating case — `firmware_version:
2.1` at the root of a firmware manifest — is served exactly.

**`format` is required and explicit, never inferred from the file extension.**
Q1's winning argument was that the declaration is a security-relevant surface
reviewed in a PR; a reviewer must see which parser each file is handed rather than
infer it from a name that may lie. Classifying a file by its path is precisely the
executable/documentation-classification hazard the threat matrix names, and this
design refuses it. Admissible values are `"json"` and `"yaml"` in this slice;
`"toml"` is a one-value widening of the enum later, with no other change.

**The plugin's declaration parse: what is rejected, what is warned.** The
declaration is the plugin's own file, so unlike a *declared document* it may be
refused outright — refusing it captures nothing, and capturing nothing is exactly
what the flag-absent path already does. Every one of these emits **one** pytest
warning through the existing `_warn` and captures nothing:

| Declaration condition | Why refused, not partially honoured |
| --- | --- |
| File absent | **Q3's answer.** The flag is a deliberate act; silence rewards a typo with a green run and no data |
| Not valid JSON, or not an object | Nothing can be salvaged from a document whose shape is unknown |
| `version` absent or not `1` | Forward compatibility has to start somewhere, and a silent partial read of a version this build does not know is the quiet wrongness Q2 refused elsewhere |
| `files` absent, not a list, or longer than `MAX_DECLARED_FILES` | See D94 |
| An entry missing `path`, `format` or `keys`, or with an unknown `format` | Half a declaration is not a declaration |
| `path` longer than `MAX_DECLARED_PATH_CHARS` | See D94 |
| The same stored key declared by two entries | The key space is flat and globally unique per run — `PRIMARY KEY (run_id, key)` (D91). Detected purely from the declaration, before any file is opened |
| More than `MAX_METADATA_ENTRIES` keys in total | See D94 |

This is the **one** place this change departs from `recording-fault-tolerance`'s
silent posture, it is the departure Q3 authorised, and it is bounded to a
misconfiguration of the declaration itself. A malformed **declared document** is a
different thing entirely and never warns, never rejects, and never fails
ingestion (D97).

### D93 — Path containment: resolve both sides, then compare lexically

This is a security boundary (C4) and the check is stated as code because prose
loses the ordering that makes it correct.

```python
# pytest_vantage/metadata.py -- stdlib only (RQ-24)
def resolve_declared_path(rootpath: Path, declared: str) -> Path | None:
    """Return the resolved target, or None if `declared` is rejected.
    REJECTED, never clamped (C4) -- a clamped path is a path the reviewer
    did not review."""
    candidate = PurePath(declared)
    if candidate.is_absolute() or candidate.drive or candidate.anchor:
        return None
    if ".." in candidate.parts:
        return None
    try:
        root = rootpath.resolve()                    # the ANCHOR, first
        target = (root / candidate).resolve()        # strict=False; symlinks
        if not target.is_relative_to(root):          # 3.9+; pure, lexical
            return None
        if target == root or not target.is_file():
            return None
    except (OSError, RuntimeError):
        return None
    return target
```

**Why each line is where it is.**

- `Path.resolve()` is the stdlib call this boundary rests on. With the default
  `strict=False` it makes the path absolute and **resolves every symlink in every
  component**. On POSIX that is `os.path.realpath` semantics. On Windows it
  resolves through `nt._getfinalpathname`, which follows symlinks **and junctions
  and directory reparse points** and strips the `\\?\` prefix; a component that
  does not exist is resolved as far as it does and the remainder appended
  lexically. There is no platform in the supported set where a symlink survives
  it.
- **The root is resolved too, and that is not decoration.** Resolve only the
  candidate and every legitimate path appears to escape the moment `rootpath` is
  itself reached through a symlink — `/tmp` is `/private/tmp` on macOS. Resolve
  only the root and a committed symlink inside the tree pointing at
  `~/.ssh/id_rsa` sails through a lexical check. Both sides, or neither works.
- `is_relative_to()` is **purely lexical** and touches no filesystem, which is the
  property that matters: every symlink was already resolved, so nothing on disk
  can change the answer between the resolve and the check. It is 3.9+, inside the
  3.10 floor. On Windows `PureWindowsPath` compares case-folded parts, matching
  NTFS, so containment cannot be bypassed by changing case.
- The `..` pre-check is **redundant** with resolve-then-contain and is kept
  anyway: it rejects the obvious case without touching the filesystem at all, and
  it lets the plugin name which rule fired.
- `is_file()` rejects directories, sockets, device nodes and **FIFOs**. That last
  one is concrete, not theoretical: `open()` on a FIFO blocks until a writer
  appears, and at `pytest_sessionstart` that hangs the user's suite forever — the
  exact opposite of `recording-fault-tolerance`.
- `RuntimeError` is caught alongside `OSError` because on the interpreters in the
  supported 3.10–3.13 range that predate `resolve()`'s reimplementation over
  `os.path.realpath`, a symlink loop raises `RuntimeError`, not `OSError`.
  Catching only `OSError` leaves a crash path reachable from a committed file.

**Residual risk, named rather than hidden.** Between `resolve()` and `open()` a
component can be replaced by a symlink — a TOCTOU window this design does not
close. It is accepted because D-g's and C4's threat model is a hostile *server*
and a co-worker's *committed* declaration, not a concurrent local attacker who
already holds write access to the checkout — an attacker with that access simply
commits the secret directly. Closing it needs per-component `O_NOFOLLOW` or
`openat2(RESOLVE_BENEATH)`, neither of which is portable across the supported
platforms.

**The server re-validates shape, and cannot re-validate containment.** It has no
access to the client's filesystem. It checks what it can, cheaply, so a buggy or
hostile plugin cannot get an absolute path into the database: `source_file` must
be ≤ `MAX_DECLARED_PATH_CHARS`, not absolute, and free of `..`. A path failing
that is dropped — never a rejection (D97).

### D94 — Six bounds, each derived from a number this repository already argued

| Constant | Value | Where | Derivation |
| --- | --- | --- | --- |
| `MAX_DECLARED_FILE_BYTES` | `8 * 1024` = 8,192 | plugin, raw UTF-8 bytes read | The largest a declared file may be such that **four** of them at full size still fit `MAX_METADATA_SECTION_BYTES`. This is what keeps the two bounds coherent rather than one admitting what the other always rejects. Concretely ~200 lines of YAML at 40 characters — above a firmware or board manifest, below any document a human is not maintaining by hand |
| `MAX_METADATA_SECTION_BYTES` | `32 * 1024` = 32,768 | plugin, **JSON-encoded** bytes | `4 × MAX_DECLARED_FILE_BYTES`, and `MAX_REPORT_BYTES // 32`. See the finish-write arithmetic below |
| `MAX_DECLARED_FILES` | `16` | plugin, declaration parse | A bound on the **read surface**, which is what C4/C5 make security-relevant — the byte budget already bounds bytes. Four times what the section can carry at full size, so it never binds before the byte budget on a legitimate declaration and fires only against a declaration naming paths in bulk. It is also the `stat`+`open` syscall count this design commits to at session start (D99) |
| `MAX_DECLARED_PATH_CHARS` | `1024` | plugin and server | `MAX_IDENTITY_CHARS` / `SECTION_PREFIX_MAX_CHARS` — already argued in this codebase (D89) as the bound on a path-shaped client value |
| `MAX_METADATA_VALUE_BYTES` | `1024` | server, per value | `MAX_IDENTITY_CHARS`'s value, for D89's reason: a short, indexed, client-supplied string. **Not** `MAX_TEXT_FIELD_BYTES` — a 64 KiB value in a `(key, value)` index is bloat with no query value (P-2). Bytes, not characters, because `truncation.py`'s module docstring already establishes that a character slice can store twice the intended amount |
| `MAX_METADATA_ENTRIES` | `200` | plugin and server | `MAX_PAGE_ITEMS`. A run's metadata is presented unpaginated on the run detail, so the cap on stored entries **is** the bound on that response — D89's argument for `MAX_SECTIONS`, unchanged |

**The section budget's arithmetic, both directions.**

*Start-write.* Worst case beside metadata: `vcs.commit_subject` is bounded
plugin-side at `_MAX_SUBJECT_BYTES = 64 * 1024 + 1024` = 66,560 raw bytes
(`vcs.py:99`), and `json.dumps` is called bare, so `ensure_ascii` spends six bytes
per escaped unit where UTF-8 spends two — a **3× maximum expansion**, giving
199,680 encoded bytes, plus ~1,000 for `run`, `branch` and `root`. With metadata:
`32,768 + 200,704 = 233,472`, **22% of `MAX_REPORT_BYTES`**. The start-write
cannot breach the cap even when every bounded component is simultaneously at its
own worst case — which matters more here than anywhere, because per RQ-44 a lost
start-write leaves the run with no row at all.

*Finish-write.* `budget.py` derives `MAX_FAILURE_TEXT_BYTES = MAX_REPORT_BYTES //
2` = 524,288 and, from `run-recording`'s measured ~505 bytes of non-failure body
per result, claims headroom to roughly 1,000 results. Metadata rides this write
too (D96), so that headroom falls to `(524,288 − 32,768) ÷ 505 ≈ **973
results**` — a 6% reduction, still nearly double the 500-result session that
Measurements paragraph exercises. **`budget.py`'s module docstring gains one
sentence recording this**, in the same slice. A derived invariant that another
module documents is not allowed to drift silently.

**Spending is measured exactly as `budget.py` measures it.** `_encoded_cost`'s
rule — `len(json.dumps(value).encode("utf-8"))`, with **no `ensure_ascii=False`**
— is reused verbatim. Its docstring records why: charging `ensure_ascii=False`
understates every codepoint above 0x7F by 1.30×–1.84× measured, so a declaration
whose values are not English would pass the budget and still breach the cap. A
declared document is exactly the kind of file that carries non-ASCII.

### D95 — Drop whole, never truncate; absence is a row, not a missing row

Two rules, and the second is the one the proposal asked this phase to design.

**Rule 1 — nothing on this path is ever truncated.** `truncate()` is not called,
imported or reachable from any metadata code. A truncated structured document is a
*syntactically different document* that may parse into confidently wrong values,
and a truncated value in an equality-queried column is a *false value*. Both
failures are silent, which is what makes them worse than absence. Every breach —
per-file, per-section, per-value — drops the whole unit.

**Rule 2 — every declared thing gets a row, whether or not it was captured.**

```
declared file  ──→ exactly one run_metadata_file row, always, with a status
declared key   ──→ exactly one run_metadata row,      always, with a status
                   value IS NULL whenever status != 'captured'
```

This is what makes the three states a reader must distinguish actually
distinguishable:

| A reader sees | Means |
| --- | --- |
| No `run_metadata` row for `firmware_version` on this run | **Not declared.** The question was not asked for this run |
| A row with `status = 'captured'` and a `value` | Declared and captured |
| A row with `value IS NULL` and a `status` | **Declared and dropped**, and the status says by which rule |

A key whose file failed at file level gets `status = 'source_unavailable'` — a
key-level restatement of a file-level fact, pointing at the
`run_metadata_file` row that carries the reason. The alternative, writing no key
row when the file failed, was rejected because it makes "declared but the file was
too large" indistinguishable from "never declared", which is the exact failure
this rule exists to prevent — and it would silently inflate Q2's horizon count
(D100).

`value IS NULL` rows do enter `idx_run_metadata_key_value`. That is harmless: the
equality query is `WHERE key = ? AND value = ?` and never matches NULL, and the
same index is what serves the horizon count's `WHERE key = ?`.

### D96 — The wire: `metadata` is a third top-level section, on **both** writes

```json
{
  "run": { "...": "..." },
  "vcs": { "...": "..." },
  "metadata": {
    "declaration": "vantage-metadata.json",
    "files": [
      { "path": "config/firmware.yaml", "format": "yaml", "status": "captured",
        "keys": ["firmware_version", "board_revision"],
        "content": "firmware_version: \"2.1\"\nboard_revision: C\n" },
      { "path": "build/manifest.json", "format": "json", "status": "too_large",
        "keys": ["toolchain"], "content": null }
    ]
  }
}
```

**Metadata follows `_vcs_section()`'s D51 freeze rule, and it follows it in
full:** captured once in `Recorder.__init__`, never re-read, and the *identical
serialised bytes* go into the start report and the finish report. Both halves of
that rule are kept, not just the first.

| Option | Verdict |
| --- | --- |
| **Both writes, identical frozen bytes** | **Chosen** |
| Start-write only | Rejected. `pytest_sessionstart` returns *before sending anything* when `_lifecycle_available` is `False` (`recorder.py:215-222`), so metadata would be silently unrecordable against any server whose capability probe merely timed out. A lost start-write would likewise leave the run with no metadata and no marker — the run row still arrives via the finish-write, so the gap would be invisible |
| Full section on start, manifest-without-content on finish | Rejected. A third serialisation rule beside `vcs`'s (both writes) and `results`' (finish only), for a saving D94 already shows is affordable. A reader of `recorder.py` would have to learn which of three rules each section follows |

**Version skew is safe, and by the documented mechanism rather than by luck.**
`SessionReport` is `extra="ignore"` (`schemas.py:208`), and its module docstring
(lines 21–26) states this is exactly what unnamed sibling sections are for: an
older `vantage` drops an unrecognised `metadata` key rather than rejecting the
report. `RunReport` and `VcsReport` are `extra="forbid"` for the opposite
direction. `MetadataReport` and `MetadataFileReport` take `extra="forbid"` too,
matching `VcsReport`: an unknown field *inside* the section means the two sides
disagree about what a metadata snapshot is.

**One rule constrains those models absolutely, and it is a trap worth naming.**
`VcsReport` uses `max_length=64` on `commit`, and a Pydantic constraint that fails
raises `InvalidReportError` — a `422` that rejects **the whole session**. No
constraint of that kind may appear anywhere in the metadata section. Its models
declare permissive types only; **every bound is applied by the normalizer, which
drops rather than rejects.** That is the concrete mechanism behind D97's
must-not-fail-the-run rule, and getting it wrong is a one-line mistake that
converts a co-worker's typo into a lost test session.

### D97 — The parse-error taxonomy: eleven classes, none of which fails ingestion

> **The governing rule.** A malformed declared document MUST NOT fail the run's
> ingestion. The start-write's job is to make the run observable (RQ-44). A parse
> failure degrades to "this file contributed no keys, and the run records that it
> failed", with the run row written regardless.

That is `recording-fault-tolerance`'s existing posture — the plugin never breaks
the suite — applied for the first time to the server side.

| # | Class | Detected | Plugin does | Server does | User sees | Recorded |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Declared path missing on disk | Plugin | Ships the entry with `content: null` | Writes rows | Nothing during the run | file `not_found`; keys `source_unavailable` |
| 2 | Path rejected — absolute, `..`, escape, symlink escape, not a regular file | Plugin (D93) | Ships the entry, never opens it | Writes rows | Nothing during the run | file `path_rejected`; keys `source_unavailable` |
| 3 | Over `MAX_DECLARED_FILE_BYTES` | Plugin | Drops content whole | Writes rows | Nothing | file `too_large`; keys `source_unavailable` |
| 4 | Not UTF-8 decodable | Plugin (P-3) | Drops before JSON encoding | Writes rows | Nothing | file `not_text`; keys `source_unavailable` |
| 5 | `OSError` on open or read — permissions, I/O | Plugin | Drops content whole | Writes rows | Nothing | file `unreadable`; keys `source_unavailable` |
| 6 | Section budget exhausted | Plugin (D94) | Drops remaining files whole, in declaration order | Writes rows | Nothing | file `over_budget`; keys `source_unavailable` |
| 7 | Document malformed — parser raised | **Server** | — | No keys from that file | Nothing | file `malformed`; keys `source_unavailable` |
| 8 | Declared key absent from a well-formed document | **Server** | — | No value | Nothing | key `absent` |
| 9 | Declared key present but non-scalar (list/mapping) | **Server** (P-5) | — | Not serialised into `value` | Nothing | key `not_scalar` |
| 10 | Value over `MAX_METADATA_VALUE_BYTES` | **Server** (P-2) | — | Never truncated | Nothing | key `value_too_large` |
| 11 | `source_file` fails the server's shape re-check (D93) | **Server** | — | Entry dropped entirely | Nothing | Nothing — a well-behaved plugin cannot produce this |

Class 5 is an **eighth plugin-side class the proposal's seven did not name**. A
declared file the process cannot read — mode `0600` owned by another user, an
unreadable mount — is neither missing nor oversized nor binary, and without its
own status it would fall through to a bare exception at `pytest_sessionstart`.

Rows 1–6 warn nothing, deliberately, and that is the boundary Q3 drew: the *flag
with no declaration file* is a misconfiguration and warns (D92); a *declared file
that could not be captured* is data the run records, and interrupting the user's
suite over it is what `recording-fault-tolerance` refuses.

**Parsing lives in one module, `service/metadata_parse.py`, and it is the only
place `yaml` is imported.** That is what makes `deptry` and the architecture test
able to state where the dependency is, rather than infer it.

**YAML is parsed with `yaml.compose()`, never `yaml.safe_load()`.** This is not
style. `compose` builds the **node graph** and stops; the design then walks the
top-level mapping and takes only `ScalarNode` values. Three properties follow, and
the third is the one that is not obvious:

1. No Python object is ever constructed, so the `!!python/object/apply` class of
   remote code execution is not merely unreached, it is unreachable.
   `yaml.safe_load` also blocks this; `yaml.load` does not, and must never appear.
2. Non-scalar values (class 9) fall out for free — a `SequenceNode` or
   `MappingNode` is simply not a `ScalarNode`, so P-5 needs no separate check.
3. **The alias-expansion bomb is defused.** `safe_load` offers no depth or
   expansion limit, and a few hundred bytes of nested YAML aliases expands to
   gigabytes during construction. A node graph *shares* aliases instead of
   expanding them, so the 8 KiB input bound actually bounds the work. The input
   bound alone does not: expansion is exponential in source size.

JSON uses stdlib `json.loads`, which has no object-construction and no alias
hazard — but deep nesting raises **`RecursionError`, not `JSONDecodeError`**, and
8,192 bytes of `[` exceeds Python's default recursion limit several times over.
Both are caught, plus `yaml.YAMLError`, and all three become class 7.

### D98 — P-6 confirmed, with the exploration's stated cost removed

**Metadata rides `record_session`, as **one** new keyword parameter with a
default.** Exploration Option 1, chosen — but the parameter is a single frozen
aggregate rather than the two collections the two tables would suggest, and it has
a default, which removes Option 1's only named disadvantage.

```python
# vantage/core/ports/storage.py -- beside UserSetting, stdlib only (RQ-26)

@dataclass(frozen=True, slots=True)
class MetadataFile:
    """One row of `run_metadata_file`. `source_file` is the DECLARED path (P-1)."""
    source_file: str
    content_type: str
    status: str

@dataclass(frozen=True, slots=True)
class MetadataEntry:
    """One row of `run_metadata`. `value` is None whenever `status` is not
    'captured' -- a declared-but-uncaptured key is a row (design.md D95)."""
    key: str
    value: str | None
    source_file: str
    status: str

@dataclass(frozen=True, slots=True)
class RunMetadata:
    files: tuple[MetadataFile, ...] = ()
    entries: tuple[MetadataEntry, ...] = ()

EMPTY_RUN_METADATA = RunMetadata()

class ExecutionStore(Protocol):
    def record_session(
        self,
        execution: Execution,
        *,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
    ) -> bool: ...
```

| Option | Tradeoff | Decision |
| --- | --- | --- |
| **Extend `record_session`** (explore Option 1) | One write path, one transaction. D-b classifies this as a run **fact** with the run row's own lifecycle, and `vcs` already rides inside the same call rather than as a side channel | **Chosen** |
| New port methods, `list_run_metadata`/`write_run_metadata` (Option 2) | Two storage calls per ingested run, needing their own transaction-boundary decision. The `user_setting` analogy is only partial: that table is mutable and namespaced; this is write-once and run-scoped | Rejected |
| Two parameters, `metadata_files=` and `metadata_entries=` | Widens the signature twice for one concept, and lets a caller pass entries without their files — a state the "absence is marked" rule forbids and the type system would not | Rejected |

**The default is what pays for the choice.** The exploration listed Option 1's cost
as "every caller needs updating to keep compiling under `mypy --strict`" —
`routes/runs.py`, both adapters, `vantage_port_contract.py`,
`scripts/measure_history_latency.py`. With `metadata: RunMetadata =
EMPTY_RUN_METADATA` only the two **implementations** change; not one **call site**
does. The cost the exploration priced does not arise.

**Transactional atomicity, stated explicitly as the proposal required.**

- **The boundary is the existing one.** `SqliteExecutionStore.record_session`
  already runs five statements inside one `with self._lock: BEGIN IMMEDIATE …
  COMMIT`, with `except BaseException: ROLLBACK; raise`
  (`sqlite_store.py:912-948`). Two `executemany` calls are appended **after** the
  result insert — required, not tidy: `PRAGMA foreign_keys=ON` is set on every
  connection, and both new tables reference `run(id)`, which the run upsert
  created earlier in the same transaction.
- **Both statements are `INSERT OR IGNORE`**, on their primary keys. That is what
  makes D-b's "written once, never updated" true *mechanically* rather than by
  convention, and it is the same idempotence `_INSERT_RESULT` already relies on.
  It also makes arrival order irrelevant: the finish-write's identical section is
  a no-op when the start-write landed, and is the whole record when it did not.
  `created` is deliberately **not** the discriminator.
- **What a partial failure leaves behind: nothing.** There is one transaction, so
  a metadata insert that raises rolls back the run row and the results with it —
  the full session, atomically absent. That is the correct blast radius, and it is
  the concrete reason Option 2 was rejected: two calls make "a run row that
  silently claims nothing about metadata" reachable, which would turn D95's
  marked-absence guarantee into a lie the storage layer tells.
- **This does not contradict D97**, and the reason is the layering. Parsing,
  bounding and validation happen entirely in `vantage.service` **before**
  `record_session` is called; storage receives only well-formed, already-bounded
  rows. A parse failure is a `status` string on a row, never an exception. By the
  time the transaction runs, the only remaining failure mode is a genuine storage
  fault — a full disk, a corrupt file — which **should** fail the whole write.
- `InMemoryExecutionStore` mirrors this with one `dict[tuple[str, str],
  MetadataEntry]` and one `dict[tuple[str, str], MetadataFile]`, using
  `setdefault` where SQLite uses `OR IGNORE` — the same second-mechanism
  discipline `_normalized_vcs` already applies, so the two adapters cannot drift.

### D99 — `--vantage-metadata`: the flag, its gate, and where the read happens

Registered in the existing `group.addoption` block (`plugin.py:47-96`), shaped
exactly like `--vantage-failure-text`:

```python
group.addoption(
    "--vantage-metadata",
    action="store_true",
    default=False,
    help=(
        "Read the files named by vantage-metadata.json in the project root and "
        "record the declared keys with this session. Absent by default -- capture "
        "never happens unless this flag is given, there is no ini equivalent, and "
        "the flag cannot activate recording on its own (design.md D99). Declared "
        "files are read from disk and their declared values are stored; a "
        "configuration file is where credentials live by convention."
    ),
)
```

```python
# pytest_vantage/config.py -- no ini parameter, no env parameter, by construction
def resolve_metadata_capture(*, activated: bool, cli_opt_in: bool) -> bool:
    return activated and cli_opt_in
```

The same monotone conjunction as `resolve_failure_text_capture`, and the same
structural guarantee: **the signature carries no ini and no environment
parameter**, so a committed configuration file cannot be the means by which
capture is enabled. `_metadata_capture_requested` short-circuits on
`_activation_requested` before touching the opt-in surface, mirroring
`plugin.py:157-158`, and is called on **both** xdist branches — the opt-in is
session-wide.

**Where the read happens, and why it happens exactly once.** `Recorder.__init__`,
beside `self._vcs = _capture_vcs(...)`, gated on a new keyword the controller
passes in:

```python
Recorder(config, address, timeout,
         lifecycle_available=lifecycle_available,
         metadata_requested=_metadata_capture_requested(config))
```

A `Recorder` is constructed only on the controller and only after activation and
the preflight succeed, so the activation gate is *structural* and
`metadata_requested` is the second gate C2 names. **No `Recorder` is ever
constructed on an xdist worker** (`plugin.py:214-217`), so the declaration and its
files are read exactly once per session regardless of worker count — which is what
keeps the RQ-25 cost O(1) per session under xdist too, and it needs no
`workerinput` check of its own.

C1 and C3 are inherited verbatim: the differential tree-identity test from
`test_opt_in.py`, and the shipped-`--help` assertion that "there is no ini
equivalent" is present while "or the ini equivalent is given" is absent.

### D100 — The read filter, and Q2's horizon published rather than implied

`list_runs` is **extended**, not joined by a second method: it is the same page
over the same total order with a `WHERE EXISTS` added, and a parallel method would
duplicate the `LIMIT n+1` / `has_more` mechanism D61 already settled.

```
GET /api/v1/runs?metadata_key=firmware_version&metadata_value=2.1
```

**Two query parameters, never one `key=value` string.** A value may contain `=`,
and D54/D87 already decided that a compound whose parts are not separable cannot
ride in one segment. Both or neither: one without the other is
`InvalidMetadataFilterError`, `422`, `invalid_metadata_filter` — the one new
rejection kind this change adds, on the read path, where a `422` costs a query and
not a session.

```python
def list_runs(self, *, limit: int, offset: int,
              metadata_key: str | None = None,
              metadata_value: str | None = None) -> Page[RunListEntry]: ...

def count_runs_predating_metadata_key(self, key: str) -> int: ...
```

**Q2's answer, defined precisely.** Let `first_seen` be `MIN(run.started_at)` over
runs holding **any** `run_metadata` row for this key, of any status — which is why
D95's declared-but-dropped rows must exist, since without them a run whose file
was too large would be miscounted as predating the declaration. Then `predating`
is the count of runs with `started_at < first_seen`. When no run has ever carried
the key, `first_seen` is undefined and `predating` is the **total** run count —
honest, and it reads as "every run predates this key; it has never been declared".
Both queries are served by `idx_run_metadata_key_value`, but not the same way:
`list_runs`'s filter seeks the full `(key, value)` pair directly off the index
(implemented as `WHERE id IN (SELECT run_id FROM run_metadata WHERE key = ? AND
value = ?)`, not a correlated `WHERE EXISTS` — the latter lets the planner anchor
on `run_metadata`'s own `(run_id, key)` primary key instead and never touch this
index at all, found and fixed at `sdd-verify`), while `count_runs_predating_
metadata_key` seeks the same index left-anchored on `key` alone and then joins to
`run(started_at)` through `idx_run_started_at`.

`RunListResponse` gains `metadata_horizon: {"key": …, "predating": N} | null`,
`null` when no filter was given. Additive, and it inherits two obligations
automatically: `service/openapi/v1.yaml` must be hand-edited or
`api-interface-document`'s drift check fails in both directions, and
`test_read_only_surface.py` needs the binding-table entry for the widened `read`
path.

### D101 — ADR-0017: Nygard, imperative title, and it lands in slice 1

P-7 settled that an ADR is required and the orchestrator verified its ground —
ADR-0016's closing paragraph withholds authorisation for "test artefacts" by name
(lines 94–97). This settles its form, contents and position.

**Title:** *17. Store user-declared configuration values read from the test
repository.* Imperative, naming the decision rather than the problem.

**Format: Nygard, plus an `Alternatives rejected` section — ADR-0016's own
shape.** MADR was considered and rejected: the contentious part of this decision
is not a choice among options (the options were settled in `sdd-propose` and by
the user's four answers) but the **conditions** under which reading a user's files
is authorised at all, which is Nygard's Decision section doing exactly its job. A
reader will hold this ADR beside ADR-0016, whose conditions it inherits, and two
formats for two decisions in the same family is one format too many.

**Its `Decision` section must contain, and is not complete without:**

1. **What is authorised**, stated narrowly: reading files that the test repository
   *itself* names, and storing declared **top-level scalar** values from them.
   Never the file bodies (D-k defers those to the Phase 2 content-addressed
   `artifact` table), never a key the declaration did not name.
2. **The five conditions, held together and not separable**, in ADR-0016's own
   register — its own decision states its four that way, and this one inherits
   rather than restates them: (C1) its own opt-in invocation flag, no ini
   equivalent, and a `--help` that actively denies one; (C2) the declaration is
   local to the test repository and reviewed in a PR, and the server never
   dictates which files are read; (C3) every declared path resolves strictly under
   `rootpath`, with symlinks resolved *before* the containment check and escapes
   **rejected, never clamped**; (C4) bounded twice, plugin-side before the request
   is built and server-side per value, with breaches dropping whole and never
   truncating; (C5) the run records which files were read, so the read surface is
   auditable after the fact and not only reviewable before it.
3. **The EAV justification, named rather than left to be discovered** (D-e). This
   is entity-attribute-value; it is normally a smell; it is justified here because
   the key space is genuinely user-owned and unknowable in advance — Vantage
   cannot enumerate `firmware_version`, because every team invents its own — and
   the alternative shape, an opaque JSON blob, turns the product's own query into
   a full scan with no index. EAV stops being laziness when the columns cannot be
   known in advance.
4. **The must-not-fail-the-run rule** as part of the decision, not a consequence
   of it: a malformed declared document never fails ingestion.
5. **What this does not authorise**, in ADR-0016's closing register: not the host
   environment, not arbitrary file bodies, not server-directed reads, not web-side
   editing of the declaration, not backfill of earlier runs.

**Its `Consequences` section must contain:**

- **Vantage will read and upload a file a co-worker named, on some machine, at
  some point.** Stated, not mitigated — ADR-0016's posture. What bounds it is that
  the declaration is committed, reviewed and recorded, not that the read is safe.
- **Reversal cost, which is why this ADR is mandatory at all.** Under ADR-0013,
  dropping a populated `run_metadata` means a `schema_version` bump, and a bump
  **refuses existing databases rather than migrating them** — recorded history
  lost. Far beyond CLAUDE.md's sprint filter.
- `schema_version` 3 → 4; every developer database is recreated once, at slice 1.
- **RQ-25 is O(1) per session and the number is measured, not asserted** (D102
  below), with the same re-measure obligation the other two Measurements
  paragraphs carry.
- Cumulative growth is unbounded and nothing here prunes it — the same posture
  ADR-0016 took for failure text: named as a separate future change, not invented
  in this one.
- The data has a **horizon**, and it is published rather than implied: a query for
  a key excludes runs recorded before it was declared, and the read surface says
  how many (Q2, D100).
- Bound to: ADR-0005, ADR-0009, ADR-0013, **ADR-0014** — which drew the plugin's
  execution boundary for a *subprocess*, where this draws it for a *filesystem
  read* — ADR-0016 (the conditions inherited), RQ-2, RQ-24, RQ-25, RQ-26, RQ-28,
  RQ-29, RQ-44, and the `run-metadata`, `opt-in-activation`, `session-ingestion`,
  `recording-schema` and `history-read-api` capabilities.

**It lands in slice 1, not slice 5 — a correction to the proposal's plan.** The
proposal's own rollback section says slice 1 is the point of no easy return, since
a database opened at version 4 is refused by a downgraded build. An authorisation
that arrives four PRs after the decision became irreversible is not an
authorisation. Its `Status` is `Proposed` in slice 1's PR and `Accepted` when that
PR merges, per CLAUDE.md.

### D102 — RQ-25: O(1) per session, measured on the `vcs` harness

**The cost shape is the argument, and it is structurally different from failure
text.** At most `MAX_DECLARED_FILES` (16) `stat`+`open`+`read` pairs of at most 8
KiB each, once, in `Recorder.__init__`; one `json.loads` of a small declaration;
one `_encoded_cost` pass over at most 32 KiB. Independent of test count, of
failure count, and — because no `Recorder` is built on a worker (D99) — of worker
count. Failure-text capture was O(failures) and breached the budget at *every*
density tested; this cannot, by construction. That is a reason to expect a good
number, not a substitute for measuring one.

**How it is measured.** A new `scripts/measure_metadata_overhead.py`, built by
copying `scripts/measure_vcs_overhead.py`'s harness rather than inventing a
second one: the same two RQ-25 profiles (1,000 × ~10 ms for criterion 1, 1,000 ×
~1 ms for criterion 3), the same five interleaved A/B/A/B pairs, **medians
reported, never means**, the same in-process `_LiveServer` over
`InMemoryExecutionStore`. One deliberate change: the A arm is `--vantage` alone
and the B arm is `--vantage --vantage-metadata`, so the delta isolates *this*
change rather than recording as a whole. A third arm runs the worst legitimate
declaration — 16 files at 8 KiB — so the bound is priced, not only the typical
case.

**What budget remains, from the numbers `version-control-context`'s Measurements
paragraph actually records** (measured 2026-08-20, transcribed here, not
estimated):

| Profile | Worst measured overhead today | RQ-25 budget | Remaining |
| --- | --- | --- | --- |
| 1,000 × ~10 ms, this repository | 0.29% | 2% | 1.71 points |
| 1,000 × ~10 ms, synthetic 20,000-file repository | **1.50%** | 2% | **0.50 points** |
| 1,000 × ~1 ms, this repository | 4.17% | 2% | **already breached** |
| 1,000 × ~1 ms, synthetic repository | 4.11% | 2% | **already breached** |

The honest framing, which the spec paragraph must carry: on the 10 ms profile
there is **half a percentage point** of headroom in the worst repository, not a
comfortable margin. On the 1 ms profile the budget is **already breached before
this change starts**, so the obligation is to record whether this change worsens
an already-breached figure — not to pretend there is room.

**Pre-measurement forecast, recorded so the result can visibly disagree with it**
(D52's discipline): under **2 ms once per session** — an order of magnitude below
`vcs.capture`'s measured 6.12 ms / 27.56 ms, because no subprocess is spawned —
i.e. under 0.02% of the 10 ms profile and under 0.12% of the 1 ms profile.

The `run-metadata` capability spec gains its own Measurements paragraph carrying
the measured medians and the same sentence the other two specs carry: *a future
change to the declaration read or its bounds MUST re-run this script and update
this paragraph.*

---

## Data Flow

```
  PLUGIN -- once, in Recorder.__init__ (D96, D99)
  ─────────────────────────────────────────────────────────────────────────────
  --vantage AND --vantage-metadata                       ← both, or nothing (D99)
        │  (no Recorder is built on an xdist worker → read happens once)
        ▼
  read <rootpath>/vantage-metadata.json  with stdlib json          (Q1, D92)
        │  absent / malformed / version≠1 / duplicate key → ONE warning,
        │  capture nothing                                          (Q3, D92)
        ▼
  for each declared entry, in declaration order, ≤ MAX_DECLARED_FILES:
        │  resolve_declared_path: reject absolute, "..",            (C4, D93)
        │    resolve BOTH root and candidate, is_relative_to,
        │    is_file — REJECTED, never clamped
        │  read ≤ MAX_DECLARED_FILE_BYTES, decode UTF-8            (D94, P-3)
        │  charge _encoded_cost against MAX_METADATA_SECTION_BYTES  (D94)
        │  any failure → status on the entry, content=None; never raises (D97)
        ▼
  frozen snapshot → _metadata_section()  ← same bytes both reports (D51, D96)
        ▼
  POST /api/v1/runs  {"run": …, "vcs": …, "metadata": …}     ← start AND finish

  SERVER -- routes/runs.py::create_run (D96, D97)
  ─────────────────────────────────────────────────────────────────────────────
  SessionReport.model_validate       ← extra="ignore" envelope; NO constraint
        │                              on the metadata section may reject (D96)
        ▼
  _to_run_metadata(payload.metadata)                    ← _to_vcs_context's shape
        │  re-check source_file shape: ≤1024, not absolute, no ".."      (D93)
        │  metadata_parse.parse(content, content_type):                  (D97)
        │     json  → json.loads      (catch JSONDecodeError, RecursionError)
        │     yaml  → yaml.compose    (NEVER safe_load; node graph, ScalarNode
        │                              only → no construction, no alias bomb)
        │  per declared key: captured | absent | not_scalar |
        │                    value_too_large | source_unavailable        (D95)
        │  drop-whole everywhere; truncate() is never called             (D95)
        ▼
  RunMetadata(files=…, entries=…)          ← frozen stdlib dataclasses    (D98)
        ▼
  store.record_session(execution, results=…, received_at=…, metadata=…)  (D98)
        │  ONE BEGIN IMMEDIATE: probe, run upsert, catalogue, results,
        │  INSERT OR IGNORE run_metadata_file, INSERT OR IGNORE run_metadata
        │  any failure → ROLLBACK of the WHOLE session, nothing partial
        ▼
  201 created | 200 duplicate     ← a malformed document never changes this

  READ (D100)
  ─────────────────────────────────────────────────────────────────────────────
  GET /api/v1/runs?metadata_key=K&metadata_value=V
        │  one without the other → 422 invalid_metadata_filter
        ▼
  store.list_runs(..., metadata_key=K, metadata_value=V)  ← idx (key, value)
  store.count_runs_predating_metadata_key(K)              ← MIN(started_at) (Q2)
        ▼
  RunListResponse{items, has_more, metadata_horizon: {key, predating}}
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `packages/vantage/src/vantage/storage/schema.sql` | Modify | `run_metadata_file`, `run_metadata`, index 15; header counts 11→13 tables, 14→15 indexes; stamp `'3'`→`'4'` (D91) |
| `packages/vantage/src/vantage/storage/connection.py` | Modify | `_SCHEMA_VERSION = 4` (D91) |
| `packages/vantage/src/vantage/core/ports/storage.py` | Modify | `MetadataFile`, `MetadataEntry`, `RunMetadata`, `EMPTY_RUN_METADATA`; `record_session`'s defaulted `metadata=` keyword; `list_runs` filter params; `count_runs_predating_metadata_key` (D98, D100) |
| `packages/vantage/src/vantage/core/domain/metadata.py` | **Create** | `FILE_STATUSES` / `KEY_STATUSES` as module-level `frozenset`s of plain `str` — never an `Enum` (`liveness.py`'s measured 3.10-vs-3.13 `__format__` reason); `MAX_METADATA_VALUE_BYTES`, `MAX_METADATA_KEY_CHARS`, `MAX_METADATA_ENTRIES` (D94, D95) |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Modify | Two `INSERT OR IGNORE` constants inside the existing transaction; filtered `list_runs`; the horizon count (D98, D100) |
| `packages/vantage/src/vantage/storage/memory.py` | Modify | The same, second mechanism: two dicts with `setdefault` mirroring `OR IGNORE` (D98) |
| `packages/vantage/src/vantage/service/metadata_parse.py` | **Create** | The **only** module importing `yaml`. `yaml.compose` + `ScalarNode` walk; `json.loads`; catches `YAMLError`, `JSONDecodeError`, `RecursionError` (D97) |
| `packages/vantage/src/vantage/service/schemas.py` | Modify | `MetadataFileReport`, `MetadataReport`, `SessionReport.metadata`; `RunListResponse.metadata_horizon`. **No length or pattern constraint anywhere in the metadata section** (D96) |
| `packages/vantage/src/vantage/service/routes/runs.py` | Modify | `_to_run_metadata`, following `_to_vcs_context`'s shape; the `metadata=` argument (D97, D98) |
| `packages/vantage/src/vantage/service/routes/read.py` | Modify | Two query params on `list_runs`, the horizon field (D100) |
| `packages/vantage/src/vantage/service/errors.py` | Modify | `InvalidMetadataFilterError` (422) and its `__all__` entry — the only new rejection kind, and it is on the read path (D100) |
| `packages/vantage/src/vantage/service/openapi/v1.yaml` | Modify | The widened `GET /runs` operation, hand-written (D100) |
| `packages/pytest-vantage/src/pytest_vantage/metadata.py` | **Create** | `DECLARATION_FILENAME`, the four plugin bounds, `read_declaration`, `resolve_declared_path`, `capture_metadata`, the budget pass. `json` and `pathlib` only (RQ-24, D92, D93, D94) |
| `packages/pytest-vantage/src/pytest_vantage/plugin.py` | Modify | `--vantage-metadata`; `_metadata_capture_requested`; the new `Recorder` keyword on both xdist branches (D99) |
| `packages/pytest-vantage/src/pytest_vantage/config.py` | Modify | `resolve_metadata_capture` — no ini parameter, no env parameter (D99) |
| `packages/pytest-vantage/src/pytest_vantage/recorder.py` | Modify | `self._metadata` in `__init__`; `_metadata_section()`; the third key on **both** reports (D96) |
| `packages/pytest-vantage/src/pytest_vantage/budget.py` | Modify | **Docstring only.** One sentence recording that the finish-write's derived headroom falls from ~1,038 to ~973 results (D94) |
| `scripts/measure_metadata_overhead.py` | **Create** | The `measure_vcs_overhead.py` harness, A = `--vantage`, B = `--vantage --vantage-metadata` (D102) |
| `packages/vantage/tests/test_schema_manifest.py` | Modify | 11/130/14 → 13/139/15 — **fails by design** until updated (D91) |
| `packages/vantage/tests/vantage_port_contract.py` | Modify | Metadata round-trip, `OR IGNORE` write-once, the filter, the horizon — inherited by both adapters |
| `packages/vantage/tests/test_read_only_surface.py` | Modify | Binding-table entry for the widened `read` path (D100) |
| `docs/schema-manifest.md` | Modify | Two `###` sections; corrected counts; `meta` stamp `4` (D91) |
| `docs/adr/0017-store-user-declared-configuration-values-read-from-the-test-repository.md` | **Create** | Nygard + `Alternatives rejected`; slice 1 (D101) |
| `packages/vantage/pyproject.toml` | Modify | `PyYAML` — `vantage.service` only, `deptry`-visible (Q4) |

## Interfaces / Contracts

```json
// vantage-metadata.json, at the test repository root                    (D92)
{"version": 1,
 "files": [{"path": "config/firmware.yaml", "format": "yaml",
            "keys": ["firmware_version", "board_revision"]}]}
```

```
POST /api/v1/runs   -- the third section, on the start AND finish write  (D96)
{"run": {...}, "vcs": {...},
 "metadata": {"declaration": "vantage-metadata.json",
              "files": [{"path": "config/firmware.yaml", "format": "yaml",
                         "status": "captured",
                         "keys": ["firmware_version", "board_revision"],
                         "content": "firmware_version: \"2.1\"\n..."}]}}
→ 201 / 200, unchanged. A malformed declared document NEVER changes this. (D97)

GET /api/v1/runs?metadata_key=firmware_version&metadata_value=2.1        (D100)
→ 200 {"items": [...], "has_more": false,
       "metadata_horizon": {"key": "firmware_version", "predating": 47}}
→ 200 {"items": [...], "has_more": false, "metadata_horizon": null}   (no filter)
→ 422 {"error": "invalid_metadata_filter", "detail": "...",
       "fields": ["metadata_value"]}                       (one without the other)
```

Stored rows for the worked example, exactly — the `board_revision` file was over
the per-file bound, so both its key and its file are marked (D95):

```
run_metadata_file: (run, "config/firmware.yaml", "yaml", "captured")
                   (run, "build/manifest.json",  "json", "too_large")
run_metadata:      (run, "firmware_version", "2.1", "config/firmware.yaml", "captured")
                   (run, "board_revision",   "C",   "config/firmware.yaml", "captured")
                   (run, "toolchain",        NULL,  "build/manifest.json",  "source_unavailable")
```

## Testing Strategy

Strict TDD, RED first. New tests carry no `req` marker; each names its capability
and scenario in its docstring, which is what `grep` has to find. The requirements
genuinely touched (RQ-2, RQ-24, RQ-25, RQ-26, RQ-29, RQ-44) are verified by guards
that already exist, plus the new measurement.

| Layer | What | Approach |
| --- | --- | --- |
| Unit (plugin) | `resolve_declared_path`: absolute, `..`, a **symlink** pointing outside, a symlink loop, a directory, a **FIFO**, a path equal to the root — each **rejected, not clamped**; a legitimate nested path accepted; a root reached through a symlink still accepts its own children | Real `tmp_path` trees with real `os.symlink` and `os.mkfifo`; skipped, never passed vacuously, where the platform lacks them |
| Unit (plugin) | `read_declaration`: absent, non-JSON, non-object, wrong `version`, missing field, unknown `format`, duplicate key, over `MAX_DECLARED_FILES`, over `MAX_METADATA_ENTRIES` — each captures nothing and warns exactly **once** | Hand-built files; `_warn` call recorder |
| Unit (plugin) | The budget: a file at the per-file bound is kept; one byte over is dropped **whole** and marked `too_large`; files past the section budget are marked `over_budget` in declaration order; the encoded cost of a non-ASCII document is charged at its `ensure_ascii` size | `test_report_budget.py`'s shapes, reused |
| Test (plugin) | **C1**: a declaration file present with the flag absent produces a byte-identical tree and zero connections | `test_opt_in.py`'s differential shape, unchanged |
| Test (plugin) | **C2**: the declaration is opened **zero** times when either gate is closed | `_CallRecorder` wrapping the real open, `test_vcs.py`'s shape |
| Test (plugin) | **C3**: shipped `--help` contains "there is no ini equivalent" and does **not** contain "or the ini equivalent is given" | `test_the_shipped_help_text_advertises_no_ini_equivalent`'s shape |
| Test (plugin) | The identical metadata bytes appear in the start report and the finish report | Two captured requests, byte comparison |
| Unit (server) | `metadata_parse`: malformed YAML, malformed JSON, **1,000-deep JSON nesting** (`RecursionError`), a **YAML alias bomb** that `safe_load` would expand and `compose` does not, a non-scalar value, an absent key, an over-bound value | Pure function over literal strings; the bomb test asserts bounded wall time, not only the outcome |
| Integration (server) | A malformed declared document still yields `201` and a written run row | ASGI in-process; **RQ-44's rule, proven not asserted** |
| Integration (server) | A metadata section with an oversized/absolute/`..` `source_file` is dropped, never rejected — no `422` reaches the client | The D96 trap, made a falsifier |
| Contract (both adapters) | Metadata round-trip; `OR IGNORE` write-once across a start-then-finish pair; a finish-only session records the same rows; the filter; the horizon count including the never-declared case | `vantage_port_contract.py`, inherited by both stores |
| Test (storage) | A version-3 database is refused with a message naming both versions and the path; a fresh one opens and stamps `4` | ADR-0013 proven, not assumed |
| Integration (server) | Every one of D97's eleven classes produces its exact `status` pair | One test per row; the table is the test list |
| Inspection | `docs/schema-manifest.md` describes both tables column for column, and 13/139/15 match `schema.sql` | RQ-29's established method |
| Analysis | `pytest-vantage`'s dependency set is unchanged; `deptry` sees `PyYAML` declared and used in `vantage` only; the AST architecture test still finds `vantage.core` importing nothing | RQ-24, RQ-26; the clean-environment install check unchanged |
| Demonstration | `scripts/measure_metadata_overhead.py`'s medians transcribed into the capability spec's Measurements paragraph | RQ-25; a measurement with a method, not an assertion (D102) |

## Threat Matrix

`references/threat-matrix.md`'s five rows are **all N/A**: this change spawns no
process, composes no command, automates no VCS or PR action, and — by D92's
explicit refusal to infer `format` from a file extension — classifies no file by
its path or content.

| Boundary | Applicability |
| --- | --- |
| Documentation-like paths | **N/A** — `format` is declared explicitly and never inferred from a name (D92) |
| Git repository selection | **N/A** — no process is spawned; `rootpath` comes from pytest |
| Commit state | **N/A** — nothing inspects an index or worktree |
| Push state | **N/A** |
| PR commands | **N/A** |

Boundaries this change **does** add, recorded as notes rather than invented rows —
the archived `user-configuration` design's own convention. Each carries a RED test
above:

- **Arbitrary filesystem read directed by a committed file.** The largest new
  surface in this change. Response: D93's containment, D99's two gates, D94's
  count and byte bounds, and C5's audit rows. Residual TOCTOU named in D93 and
  accepted against a stated threat model.
- **Symlink escape.** Response: resolve **both** sides before a purely lexical
  containment check. RED test with a real symlink pointing outside `rootpath`.
- **Blocking open on a non-regular file.** A committed FIFO would hang the suite
  at `pytest_sessionstart` forever. Response: `is_file()`. RED test with a real
  FIFO.
- **Arbitrary code execution through a YAML parser.** `yaml.load` constructs
  Python objects from a file a co-worker committed. Response: `yaml.compose`,
  which never constructs at all — strictly stronger than `safe_load`. RED test
  asserting a `!!python/object/apply` document yields `malformed` and executes
  nothing.
- **Resource exhaustion through alias expansion or deep nesting.** Response:
  `compose` shares aliases rather than expanding them; `RecursionError` is caught.
  RED tests for both, asserting bounded wall time.
- **A parse failure destroying a test session.** Response: no Pydantic constraint
  in the metadata section, and the normalizer drops rather than rejects (D96).
  RED test asserting `201` for a report whose metadata section is entirely
  garbage.
- **Client-chosen text reaching SQL, and reaching a rejection body.** Response:
  bound parameters only; `fields` built through the existing
  `_fields_from_errors`/`safe_segment` path, never interpolating a submitted key.
  RED test: a quoting-shaped key round-trips byte-identically and a CR/LF key
  never appears in a response body.
- **Unauthenticated write surface.** Unchanged and not solved here — the same
  exposure `service/cli.py::warn_if_bound_wide` already documents for ingestion.

## Migration / Rollout

**No migration, by decision.** ADR-0013 governs: a database stamped `3` is refused
at open with a message naming the version found, the version required and the
path. No DDL runs against it. Every developer database is recreated once, at slice
1 — which is why the schema bump leads the chain rather than landing in its
middle, and why ADR-0017 leads with it (D101).

**The proposal's 1,400–1,900 forecast is corrected upward here, and the reason is
this design, not optimism.** The proposal priced one table; D91 needs two, because
absence has to be marked at two levels. D101 moves the ADR forward. D97's eleven
classes are eleven integration tests. The precedent for this kind of correction is
consistent: `user-configuration` forecast 450–600 and measured ~1,090;
`failure-capture` forecast ~390 and measured 796. The estimate below is derived
per file, not scaled.

| # | Slice | Content | Est. | Depends on |
| --- | --- | --- | --- | --- |
| 1 | Schema + authorisation | Both tables, index 15, `_SCHEMA_VERSION = 4`, the three port dataclasses, `record_session`'s defaulted keyword, both adapters, port-contract tests, the refusal test, `docs/schema-manifest.md`, **ADR-0017** (D91, D98, D101) | ~360 | — |
| 2 | Core vocabulary | `core/domain/metadata.py` and its unit suite. Pure, no I/O, independent of slice 1 (D94, D95) | ~180 | — |
| 3 | Plugin flag | `--vantage-metadata`, `resolve_metadata_capture`, the `--help` denial, the differential C1/C2/C3 tests, Q3's warning (D92, D99) | ~300 | — |
| 4 | Path containment | `metadata.py`'s declaration reader and `resolve_declared_path`, with the full adversarial suite — symlink, loop, FIFO, directory, absolute, `..` (D92, D93) | ~300 | 3 |
| 5 | Read, bound, ship | File read, per-file and section bounds, `_metadata_section()`, both reports, `budget.py`'s docstring (D94, D96) | ~280 | 2, 4 |
| 6 | Server parse and ingest | `MetadataReport`, `metadata_parse.py`, `_to_run_metadata`, all eleven taxonomy tests, PyYAML + `deptry` (D96, D97) | ~400 | 1, 2, 5 |
| 7 | Read filter and measurement | The two query params, Q2's horizon, `v1.yaml`, the read-only binding, `scripts/measure_metadata_overhead.py`, the Measurements paragraph, README (D100, D102) | ~340 | 1, 6 |

**~2,160 lines across seven slices; none exceeds 400.**
`chain_strategy: feature-branch-chain`; rollback in reverse chain order.

```
Decision needed before apply: Yes
Chained PRs recommended: Yes
400-line budget risk: High
```

Slices 2 and 3 are independent of everything and may land in any order. Slices 4
and 5 cannot precede 3, and 6 cannot precede 1, 2 or 5 — a route cannot call a
port method that does not exist without breaking `mypy --strict`, and the server
cannot parse a section the plugin does not send. Those are dependencies, not
preferences.

**Slice 7 is the declared cut point**, per the proposal. Cutting it ships storage
nobody can query yet — stated plainly rather than hidden — and it also cuts
D102's measurement, which is the worse loss of the two. If the chain is at risk,
cut the read filter and **keep** `scripts/measure_metadata_overhead.py`.

**Rollback.** Slices 2–7 are ordinary branch reverts; nothing persists that a
revert cannot undo, and unflagged sessions were never affected. Slice 1 before any
database is opened at version 4 reverts cleanly to `_SCHEMA_VERSION = 3`. Slice 1
**after** a user has opened a version-4 database is **not revertible in place**: a
downgraded build refuses that database, and the recovery is forward-only —
supersede, do not revert — or the user deletes the store and loses history. That
asymmetry is the reversal cost D101 makes ADR-0017 mandatory for, and the
practical consequence stands as the proposal wrote it: slice 1 merging is the
point of no easy return.

## Open Questions

None blocks `sdd-tasks`.

- [ ] **Nested keys.** D92 ships flat top-level keys only. The `{stored_key:
      document_key}` object form is a strictly widening parse of the same field
      and needs no schema or wire change — the first team that asks adds it.
- [ ] **TOML.** Q4 deferred it; `content_type`'s `CHECK` already admits `'toml'`
      (D91), so adding `tomli` later is a parser and an enum value, not a version
      bump.
- [ ] **The TOCTOU window between `resolve()` and `open()`** (D93). Accepted
      against a stated threat model. Closing it needs `openat2(RESOLVE_BENEATH)`
      or per-component `O_NOFOLLOW`, neither portable across 3.10–3.13 on the
      supported platforms.
- [ ] **Retention and pruning of `run_metadata`.** Named, not invented here — the
      same posture ADR-0016 took for failure text.
- [ ] **Backfill.** Ruled out by D-d and published as Q2's horizon instead (D100).
      The first request for it is a change, not a bug.
- [ ] **`docs/schema-manifest.md`'s "Comparison recorded" narrative block** (lines
      364–403) says "Table count 10" / "Index count 13" and was already stale
      before this change. **Not this change's obligation**, but a reviewer will
      attribute it here — call it out in slice 1's PR description.
- [ ] **The 1 ms profile's RQ-25 budget is already breached** at 4.11%/4.17%
      before this change starts (D102). Whether that is a defect of the budget or
      of the plugin is a question this change records rather than answers.
