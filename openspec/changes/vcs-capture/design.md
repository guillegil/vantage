# Design: VCS capture — the plugin reads the repository once, the server records it

> Decisions continue the numbering used by the archived changes and by
> `plugin-server-compatibility` (D38–D42) and run **D43–D52**. Size: this document
> deliberately exceeds the 800-word phase budget, for the same reason the two previous
> designs did — the exact argv, the exact failure discrimination, the exact conflict
> branch and the exact truncation ownership are the parts that cost a rewrite if left
> to apply time.

## Technical Approach

One new module reads git; everything else is wiring.

`pytest_vantage/vcs.py` performs a bounded sequence of `git` invocations once per
session, at `Recorder.__init__`, and returns an immutable snapshot. **It cannot raise.**
The snapshot rides on both the start report and the finish report as a `vcs` sibling
section, and the server maps it onto a `VcsContext` value object which the storage
adapters persist into the six `vcs_*` columns that have existed, unwritten, since
Milestone 1.

Five layers, in the delivery order below:

1. **The reader** (D43–D46) — argv, ordering, failure discrimination, environment.
2. **The plugin wiring** (D51) — snapshot held once, serialised twice, warning emitted once.
3. **The wire** (D47) — `VcsReport`, `SessionReport.vcs`, and the one field that is
   deliberately *not* on it.
4. **The server and storage** (D48, D49) — `VcsContext`, truncation ownership, and a
   conflict branch that cannot null a recorded value.
5. **Measurement, ADR and docs** (D52).

Specs: `version-control-context` (new — RQ-10, RQ-23, RQ-39),
`recording-fault-tolerance` (VCS capture isolation), `session-ingestion` (optional
`vcs` section). The schema is **unchanged** — all six columns already exist, nullable,
with the right defaults, so there is no `schema_version` bump and ADR-0013's refusal
gate is not engaged.

---

## Architecture Decisions

### D43 — `vcs.py` is its own fail-closed boundary, not a `boundary.py` decorator

`fault_isolated` and `liveness_isolated` both **latch** — `_isolated`'s own docstring
says a later call through the same flag "does not even invoke the hook body again".
Reusing either would let a git failure silently stop results or heartbeats being
reported at all, turning RQ-39's *record the run with nulls* into *record nothing* —
the exact wrong implementation RQ-23 criterion 2 was written to catch. `boundary.py`
is therefore **unchanged**, deliberately.

`vcs.py` follows `transport.fetch_capabilities` instead (D40): it *is* the boundary
rather than delegating to one, and it never propagates.

```python
@dataclass(frozen=True, slots=True)
class VcsSnapshot:
    commit: str | None = None
    branch: str | None = None
    commit_subject: str | None = None
    dirty: bool | None = None
    root: str | None = None
    warning: str | None = None      # what to say, or None to stay silent

_EMPTY = VcsSnapshot()

def capture(rootpath: Path) -> VcsSnapshot: ...   # never raises
```

The exceptions it swallows, named exhaustively because "catch `Exception`" is not a
design:

| Raised by | Exception | Meaning |
| --- | --- | --- |
| a missing `git` binary | `FileNotFoundError` | RQ-39.2 — caught even though `shutil.which` already checked, because `which` and `exec` disagree on Windows and a `PATH` entry can vanish between them |
| a `git` that will not finish | `subprocess.TimeoutExpired` | D44's deadline |
| a non-executable `git`, a deleted `cwd`, a permission refusal, a fork failure | `OSError` (`PermissionError`, `NotADirectoryError`, `BlockingIOError` are subclasses) | RQ-39's permissions case |
| `check=True`, if it were ever used | `CalledProcessError` | not used — exit status is read off `returncode`; the class is caught anyway so a later `check=True` cannot fail open |
| decoding stdout | `UnicodeDecodeError`, `LookupError` | a commit message need not be UTF-8; `errors="replace"` already prevents the first, and `LookupError` covers a broken codec registry |
| anything else | `Exception` | the outer net |

**`Exception`, never `BaseException`** — `KeyboardInterrupt` and `SystemExit` must still
reach pytest's `wrap_session` (RQ-31), the same rule `boundary._isolated` states.

The warning is *returned*, not emitted: `vcs.py` never touches `pytest.Config`, and
"exactly one warning naming the cause" (RQ-39 scenario) becomes a property of there
being exactly one call site, not of a latch.

### D44 — Five invocations, in this order, under one 5-second budget

`shell=False` always; argv is a list of literal constants; **no value derived from the
repository, the environment or the user is ever an argv element.** `cwd=rootpath` —
never `git -C <string>`, never a relative path.

| # | argv | Reads | On failure |
| --- | --- | --- | --- |
| 1 | `git rev-parse --show-toplevel` | `root` | **gate** — the whole snapshot is null |
| 2 | `git rev-parse --verify --quiet HEAD` | `commit` | `commit` null; continue (RQ-10.4) |
| 3 | `git symbolic-ref --quiet --short HEAD` | `branch` | `branch` null; continue (RQ-10.3) |
| 4 | `git show --no-patch --no-show-signature --format=%s HEAD` | `commit_subject` | subject null; continue. **Skipped entirely when 2 returned null** — a repository with no commits has no subject and pays no process |
| 5 | `git status --porcelain --untracked-files=no` | `dirty` | `dirty` null (never `False`); continue |

**Invocation 1 is all-or-nothing; 2–5 are field-by-field.** That split is not a
preference — RQ-10.3 *is* invocation 3 failing while 2, 4 and 5 succeed, and RQ-10.4 *is*
invocation 2 failing while 3 and 5 succeed. An all-or-nothing result would fail both
criteria. Conversely, once invocation 1 has failed there is no repository to describe
and nothing after it could be trusted.

Flag choices worth stating:

- **`--untracked-files=no`.** RQ-10.1 says *tracked*. Untracked files must not count, so
  the cheaper flag is also the more faithful one — it skips the directory walk that
  dominates `git status` on a large tree.
- **`symbolic-ref --quiet --short`, not `rev-parse --abbrev-ref`.** `--abbrev-ref`
  answers the literal string `HEAD` when detached, which would have to be compared
  against a magic value; `symbolic-ref` exits 1 with empty stdout, which is an exit
  status. It also succeeds on an unborn branch, so a `git init`-only repository records
  `branch = "main"` alongside `commit = None` — more information than RQ-10.4 asks for
  and contradicted by nothing in it.
- **`--no-show-signature`.** A user with `log.showSignature = true` would otherwise have
  `git show` invoke gpg, which can reach a pinentry and block. This is a hang, and the
  flag removes it rather than relying on the deadline to survive it.
- **`--no-patch` / `--format=%s`.** `%s` is git's subject: the first paragraph, folded to
  a single line. It can never contain a newline. The plugin cuts at the first `\n`
  anyway (D49), so the stored value is structurally incapable of carrying CR/LF into a
  log line.

**The 5 seconds is a budget for the whole capture, not per invocation.** Five
invocations each bounded at 5 s is a 25-second session cost in a pathological
environment, and RQ-21 criterion 4 bounds the session at *timeout + 5 s*. So:

```python
deadline = time.monotonic() + _CAPTURE_BUDGET_SECONDS   # 5.0
remaining = max(_MIN_SLICE, deadline - time.monotonic())  # per invocation
```

The first timeout ends the capture. This makes the fault-tolerance spec's "the git
subprocess is terminated at 5 seconds, and the session is not otherwise delayed"
literally true of the session, not merely of one process.

Every invocation carries `stdin=subprocess.DEVNULL`, `capture_output=True`,
`text=True, encoding="utf-8", errors="replace"`, and `env=` from D46.

**Rejected: `git status --porcelain=v2 --branch --untracked-files=no`**, which answers
commit, branch and dirty in one process instead of four. It is materially cheaper and
it is the documented fallback if D52's measurement demands it — but it trades three
exit statuses for a parser over the literals `(detached)` and `(initial)`, and it
requires git ≥ 2.11. Exit statuses do not change between git releases; output formats
do. Measure first, per Q1's discipline.

### D45 — The four "the other side did not answer" cases, and the two that look alike

| Case | What git does | How the code tells | Warns? |
| --- | --- | --- | --- |
| **Not a repository** | invocation 1 exits `128` | gate failed **and** no `.git` entry at `rootpath` | **no** (RQ-23) |
| **Corrupt repository** | invocation 1 exits `128` | gate failed **and** a `.git` entry exists at `rootpath` | **once** (Q3) |
| **Detached HEAD** | 1, 2, 4, 5 succeed; 3 exits `1` with empty stdout | exit status of invocation 3 | no |
| **No commits yet** | 1, 3, 5 succeed; 2 exits `1` (`--quiet`) | exit status of invocation 2 | no |

The first two are indistinguishable by exit status — both are `128` from the same
command. **The discriminator is a stdlib filesystem check, not stderr text:**
`(rootpath / ".git").exists()`. Parsing stderr would couple the warning policy to
git's message wording, which changes between releases; an exists-check spawns nothing
and cannot drift.

Two honest imprecisions this accepts, both erring towards silence:

- A repository whose corruption lives *above* `rootpath`, or one reached through
  `GIT_DIR` redirection, reads as *absent* and stays silent. The proposal puts
  submodules, nested worktrees and `GIT_DIR` redirection out of scope — must not crash,
  need not be understood.
- A `safe.directory` refusal (git ≥ 2.35.2, `fatal: detected dubious ownership`) exits
  `128` with `.git` present, so it **warns**. In a container running as a different uid
  from the checkout's owner, that is once per session, forever — the shape Q3 wanted to
  avoid. Accepted, because the alternative is matching `dubious ownership` in stderr,
  and a substring match is a compatibility surface. `LC_ALL=C` (D46) means git emits the
  untranslated English message, so the refinement stays available without redesign if
  the false warning turns out to matter. Recorded as an open question, not smuggled.

A **timeout** is a fifth case and is not covered by RQ-23 or RQ-39's own wording. It
warns, in the corrupt bucket's voice ("could not read the git repository"): it is rare,
it is actionable, and it is not the CI-image-without-git case Q3 protected. Missing
`git` remains silent, per Q3.

### D46 — Inherit the environment and override the hazardous keys; do not scrub it

`git` reads user and system configuration, so the invocations are not deterministic by
default. The tempting answer — clear the environment and pass only `PATH` — is **wrong**,
and expensively so: without `HOME`, git reads no global config, so `safe.directory` is
never seen and a perfectly readable repository owned by another uid becomes
`fatal: detected dubious ownership`. We are reading the user's repository as the user;
their configuration is part of the truth we are trying to record.

So: start from `os.environ`, override the keys that can make git **interactive, slow, or
mutating**.

| Key | Value | Why |
| --- | --- | --- |
| `GIT_TERMINAL_PROMPT` | `0` | git must never prompt, even with a tty |
| `GIT_ASKPASS` | `""` | no GUI credential dialog |
| `SSH_ASKPASS` | removed | same, via ssh |
| `GIT_OPTIONAL_LOCKS` | `0` | **`git status` must not take `index.lock`.** An observability tool has no business writing the user's index, and on a network filesystem or a read-only checkout taking that lock is exactly where the hang happens |
| `GIT_PAGER`, `PAGER` | `cat` | belt and braces; git already skips the pager off a tty |
| `LC_ALL` | `C` | stable, untranslated diagnostics |

`credential.helper` is reachable only from a command that contacts a remote, and none of
D44's five does. `stdin=DEVNULL` closes the remaining prompt path. The residual risk is a
grandchild (a `core.fsmonitor` hook, a pinentry) surviving `subprocess.run`'s kill of its
direct child on POSIX; the deadline still returns control to pytest, and `fsmonitor` is
deliberately **not** disabled — it makes `status` faster, and the budget already bounds it.

### D47 — The wire: five fields on the section, six columns in the row

```python
class VcsReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commit: str | None = Field(max_length=64)
    branch: str | None
    commit_subject: str | None
    dirty: bool | None
    root: str | None

class SessionReport(BaseModel):
    ...
    vcs: VcsReport | None = None
```

- **`extra="forbid"` on the section**, matching `RunReport`: an unknown field *inside*
  `vcs` means the two sides disagree about what a VCS snapshot is. The envelope's
  `extra="ignore"` is what carries the skew, exactly as `schemas.py`'s own module
  docstring already argues for `results`.
- **No `commit_subject_truncated` on the wire.** That is Q4's answer made structural: the
  server owns the bound and the flag, and there is no field for the plugin to claim it
  with. ADR-9 already settled that the server makes every judgement about what was
  written.
- **`max_length`, never `pattern=r"^[0-9a-f]{40}$"`.** A SHA-256 repository produces 64
  hex characters, and a pattern anchored at 40 would `422` that report and lose the whole
  run over a field nothing reads yet.
- Every field required with no default, matching `RunReport`'s rule — a field the client
  forgot is a rejection, not a silently substituted null.

**The section rides on both reports** and always carries explicit values, including all
five nulls. Omitting the section when there is no repository would make "I looked and
found nothing" indistinguishable from "I am a plugin that predates this change", and the
server's no-section branch exists for the second case only. Against a server without
`session_lifecycle` the start report is not sent at all, so the finish report must carry
it; against a current server the start report carrying it is what gives an abandoned run
(RQ-44) its commit. Both reports carry the identical snapshot, so the upsert is
idempotent.

No capability gate: `SessionReport` is `extra="ignore"`, so an older server drops the key
and records exactly as today. `routes/capabilities.py` is **unchanged** — a flag nothing
branches on would advertise a gate that does not exist.

### D48 — The conflict branch coalesces per column; nulls never clobber

`_UPSERT_RUN` grows from 8 columns to 14. The insert branch is mechanical. **The conflict
branch is where the bug lives**, and it is the same monotonicity problem `exit_status`
already solved: a second report that carries no VCS data must not null a recorded one.

The existing row-level guard cannot express this. `WHERE run.exit_status IS NULL AND
excluded.exit_status IS NOT NULL` governs the whole row — it is right for the finish
fields and says nothing useful about six columns whose freshness is not keyed to
`exit_status`. A second row-level predicate cannot make some columns advance and others
hold. So the guard is **per column**:

```sql
ON CONFLICT(id) DO UPDATE SET
    finished_at      = excluded.finished_at,
    exit_status      = excluded.exit_status,
    interrupted      = excluded.interrupted,
    interrupt_reason = excluded.interrupt_reason,
    vcs_commit         = COALESCE(excluded.vcs_commit,         run.vcs_commit),
    vcs_branch         = COALESCE(excluded.vcs_branch,         run.vcs_branch),
    vcs_commit_subject = COALESCE(excluded.vcs_commit_subject, run.vcs_commit_subject),
    vcs_dirty          = COALESCE(excluded.vcs_dirty,          run.vcs_dirty),
    vcs_root           = COALESCE(excluded.vcs_root,           run.vcs_root),
    vcs_commit_subject_truncated =
        CASE WHEN excluded.vcs_commit_subject IS NOT NULL
             THEN excluded.vcs_commit_subject_truncated
             ELSE run.vcs_commit_subject_truncated END
 WHERE run.exit_status IS NULL AND excluded.exit_status IS NOT NULL
```

`COALESCE` is monotonic in the only direction that matters: null → value, never value →
null. A non-null incoming value *does* overwrite, which is correct — in one session both
reports carry the identical snapshot, so the write is a no-op, and a genuinely later
non-null is newer information. The `CASE` on the flag keeps it travelling with the
subject it describes, so it can never end up set for a subject that came from the other
report.

The reordered case is unchanged: a start-write arriving after a finish fails the
row-level `WHERE` and changes nothing, VCS columns included.

**The in-memory adapter needs the same rule at field level, and "coalesce the whole
object" is not the same rule.** `stored.vcs if execution.vcs is None else execution.vcs`
diverges from per-column `COALESCE` the moment one report carries a partial snapshot. So
`vantage.core.domain.execution` gains the merge as pure domain logic:

```python
@dataclass(frozen=True, slots=True)
class VcsContext:
    commit: str | None
    branch: str | None
    commit_subject: str | None
    commit_subject_truncated: bool
    dirty: bool | None
    root: str | None

    def merged_over(self, previous: VcsContext | None) -> VcsContext: ...
```

SQL does `COALESCE`, memory calls `merged_over`; the shared contract suite is what proves
they agree. That is RQ-30's entire point — two mechanisms, one port — not duplication.

Two normalisation rules, applied at exactly one place each so the adapters cannot drift:

- **`_to_execution` maps `vcs=None` when the section is absent *or* every field in it is
  null.** A run recorded outside a repository therefore reads back as `execution.vcs is
  None`, not as a `VcsContext` of nulls.
- **`_row_to_execution` uses the same all-null test** on the five value columns.

`vcs_dirty` is `INTEGER NULL` **with no default** (the manifest already requires it):
written as `1`, `0` or `NULL`, and never `0` for "unknown" — defaulting to 0 would have a
run recorded outside a repository claim a clean working tree. Nothing writes an empty
string: the plugin sends `None`, and a contract scenario asserts SQL `NULL` via
`typeof(...) = 'null'` rather than falsy-equality, which `""` would also satisfy.

### D49 — 64 KiB, on bytes, at the server — and the plugin's cap must sit *above* it

**Where**: the server, in one pure helper reached from `routes/runs.py`. **What**: RQ-22's
uniform 64 KiB, not a subject-specific 1 KiB.

| Option | Tradeoff | Verdict |
| --- | --- | --- |
| **64 KiB, uniform (RQ-22)** | absurdly generous for one line — but only ever reached by input that is pathological at *any* bound, and it is the number RQ-22 already fixes for every stored text field | **chosen** |
| 1 KiB, subject-specific | proportionate, and it would be the project's first per-field bound — a second truncation rule in a codebase that has one, with a second constant to keep consistent, to cut a value no real repository produces | rejected |

This change therefore ships the project's **first** truncation implementation
(`rg _truncated packages/vantage/src` finds the columns and no writer). It is built so
RQ-22 can adopt it unchanged: `MAX_TEXT_FIELD_BYTES = 64 * 1024` and
`truncate(value) -> tuple[str | None, bool]`.

**The bound is on UTF-8 bytes, cut at a character boundary.** RQ-22 says 64 KiB, a byte
quantity; `value[:65536]` counts characters and can store four times that. Encode, slice,
`decode(errors="ignore")` — never split a multi-byte character.

**The plugin's own cap is a size guard only, and it must sit above the server's bound.**
This is the defect the Q4 split invites: if the plugin cut at, say, 8 KiB, a 100 KiB
subject would arrive already short, the server would see nothing to truncate, and
`vcs_commit_subject_truncated = 0` would be a lie the server had no way to detect. So
`_MAX_SUBJECT_BYTES = 64 * 1024 + 1024` — anything the server would truncate still arrives
over the server's bound, so the flag is never a false zero, while a pathological
repository still cannot push a ~1 MiB `MAX_REPORT_BYTES` envelope. The plugin also cuts at
the first `\n` before capping.

`errors.py::safe_segment` is **not** applied. It is an allow-list for echoing
client-chosen text back in a response body; a commit subject is stored data, the same
class as `interrupt_reason`, and it is never echoed in an acknowledgement or a rejection.
Output encoding belongs to whatever renders it (ADR-8). RQ-22's in-value marker
(criterion 1) is **not** implemented here — this change sets the sibling flag only, and
RQ-22 is a Should Have with no writer; recorded as an open question so RQ-22's change does
not discover a half-rule.

### D50 — What a container, a network filesystem, another Python and a prompting git do

The two changes that just closed were verified against the spec and defeated by the
environment. Asked here, up front:

| Question | Answer |
| --- | --- |
| A container **without git** | `shutil.which("git")` is `None` → **zero processes spawned**, all null, **silent** (Q3). `FileNotFoundError` still caught (D43) |
| A repository on a **network filesystem** | `GIT_OPTIONAL_LOCKS=0` keeps `status` off `index.lock`; the whole-capture deadline bounds the stall; a slow-but-healthy read that exceeds 5 s degrades to nulls — the cost Q2 accepted deliberately |
| A **git that prompts** | `stdin=DEVNULL` + `GIT_TERMINAL_PROMPT=0` + `GIT_ASKPASS=""`; no invocation contacts a remote; `--no-show-signature` removes the gpg/pinentry path |
| A **commit subject with a newline** | `%s` folds the first paragraph to one line; the plugin cuts at the first `\n` regardless; the value is stored, never echoed, so it cannot forge a log line |
| A **non-UTF-8 commit message** | `encoding="utf-8", errors="replace"`; a latin-1 subject fixture proves it |
| **Another Python** (3.10–3.13) | frozen stdlib dataclass; no `StrEnum`, no `datetime.UTC`, no `str`-mixin `Enum` — `f"{X.A}"` differs across the supported range (D34's correction) |
| **Another timezone** | nothing in this change stores a timestamp |
| **An older server** | `SessionReport` is `extra="ignore"`; the key is dropped and the run records as today |
| **A SHA-256 repository** | `max_length=64`, no 40-hex pattern (D47) |
| **Running as root in CI** | the permissions scenario is Inspection, skip-if-root (spec-fixed) — `chmod 000` is a no-op as root and a test that cannot fail proves nothing. The `safe.directory` false warning is named in D45 |

### D51 — Capture at `Recorder.__init__`; the Recorder warns; `plugin.py` is unchanged

```python
def __init__(self, config, address, timeout, *, lifecycle_available=False):
    ...
    self._vcs = vcs.capture(Path(str(config.rootpath)))
    if self._vcs.warning is not None:
        _warn(config, f"vantage: {self._vcs.warning}")
```

- **At construction, not at finish.** RQ-10.1's dirty flag must describe the tree that
  produced the results; a test that writes into the repository would flip a finish-time
  reading, and a start-time reading cannot be wrong that way.
- **Safe there only because `capture` cannot raise.** `Recorder.__init__` runs inside
  `pytest_configure` and is not hook-wrapped; an exception there is pytest's own
  INTERNALERROR with exit status 3 — RQ-21 criterion 5's named failure.
- **After activation and the preflight**, because `plugin.py` constructs the Recorder
  last. RQ-2 is untouched: without `--vantage`, no process is spawned.
- **Controller-only for free.** `pytest_configure`'s `workerinput` guard is its first
  statement and returns before a `Recorder` exists, so no worker ever spawns `git`
  (D36). `plugin.py` needs no second guard and is **unchanged**.
- `_warn` is already used from `pytest_configure` (the preflight failure) and already
  falls back to the terminal reporter and then stderr, so emitting this early is
  precedent, not a new hazard.
- The snapshot is **not re-read**. One private `_vcs_section()` serialises it for both
  reports, so the two can never disagree — the same reason `_started_at` is captured in
  `__init__` (D32).

### D52 — Exactly one decision here earns an ADR

`openspec/config.yaml` and CLAUDE.md set the filter: more than a sprint to reverse.

| Decision | Reversal cost | Verdict |
| --- | --- | --- |
| **`pytest-vantage` may execute an external process** | reversing means writing and owning a `.git` parser — packed refs, packed objects, `reftable`, index-vs-worktree comparison, mtime races, `core.autocrlf` — and it makes RQ-39 criterion 2 untestable. **Far more than a sprint** | **ADR-0014 — "Execute git from the plugin as a bounded, fail-closed subprocess"**, Nygard, `Proposed` in the PR |
| 5-second whole-capture budget | one constant | design note (D44) |
| Its own non-latching boundary | one function; `boundary.py` untouched | design note (D43) |
| Capture at `Recorder.__init__` | one line | design note (D51) |
| `COALESCE` conflict branch | one `SET` list | design note (D48) |
| 64 KiB, server-owned | one constant and one call site | design note (D49) |
| The section on both reports | one dict key | design note (D47) |

**This change takes 0014; `read-api` takes 0015** (Q5). The ADR is scoped narrowly to
"the plugin may spawn an external process, bounded and fail-closed" — not broadly to "how
the plugin reads its host environment", which would pre-bind the RQ-11 machine-context
change that has not been designed.

No existing ADR is restated: ADR-3 (clean architecture, `Protocol` ports), ADR-4 (two
distributions, an HTTP boundary), ADR-5 (complete schema, no migration framework), ADR-6
(stdlib `sqlite3`), ADR-8 (the web interface owns output encoding), ADR-9 (the server owns
every write) and ADR-0013 (refuse an older schema version) are referenced and relied on,
never re-argued.

---

## Data Flow

```
  pytest_configure ── xdist controller only (D36) ── activation, preflight, capability probe
        │
        └── Recorder.__init__ ── vcs.capture(config.rootpath)            (D51)
                │                   1 rev-parse --show-toplevel   ← gate, all-or-nothing
                │                   2 rev-parse --verify --quiet HEAD
                │                   3 symbolic-ref --quiet --short HEAD
                │                   4 show --no-patch --no-show-signature --format=%s
                │                   5 status --porcelain --untracked-files=no
                │                   one 5 s budget across all five (D44)
                │                   never raises; returns VcsSnapshot + optional warning
                └── _warn(...) at most once, naming the cause          (D45)

  pytest_sessionstart ──► POST /runs {run, vcs}      ← not sent when lifecycle unavailable
  pytest_sessionfinish ─► POST /runs {run, results, vcs}   ← always sent

        │  SessionReport (extra="ignore") ─ older server drops `vcs`, records as today
        ▼
  _to_execution ──► truncate(subject) @ 64 KiB bytes, sets the flag      (D49)
              └──► VcsContext | None   (None when the section is absent or all-null)
        ▼
  record_session ──► _UPSERT_RUN, 14 columns
                       insert branch : writes all six
                       conflict branch: COALESCE per column + CASE on the flag  (D48)
                                        under the unchanged exit_status WHERE
        ▼
  _row_to_execution ──► VcsContext | None, same all-null rule
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `packages/pytest-vantage/src/pytest_vantage/vcs.py` | **Create** | `VcsSnapshot`, `capture(rootpath)`; the whole boundary (D43–D46) |
| `packages/pytest-vantage/src/pytest_vantage/recorder.py` | Modify | hold the snapshot, warn once, `_vcs_section()` on both reports (D51) |
| `packages/pytest-vantage/src/pytest_vantage/boundary.py` | **Unchanged** | deliberately — both existing decorators latch (D43) |
| `packages/pytest-vantage/src/pytest_vantage/plugin.py` | **Unchanged** | the xdist guard already covers this (D51) |
| `packages/vantage/src/vantage/core/domain/execution.py` | Modify | `VcsContext`, `merged_over`, `Execution.vcs` (D48) |
| `packages/vantage/src/vantage/service/schemas.py` | Modify | `VcsReport`; `SessionReport.vcs` (D47) |
| `packages/vantage/src/vantage/service/truncation.py` | **Create** | `MAX_TEXT_FIELD_BYTES`, `truncate(value)` — RQ-22's first writer (D49) |
| `packages/vantage/src/vantage/service/routes/runs.py` | Modify | `_to_execution` maps and normalises the section; applies truncation |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Modify | `_UPSERT_RUN` 8 → 14 columns; `_SELECT_RUN`; `_row_to_execution` (D48) |
| `packages/vantage/src/vantage/storage/memory.py` | Modify | `merged_over` on the conflict branch; second mechanism, not a copy |
| `packages/vantage/src/vantage/storage/schema.sql` | **Unchanged** | all six columns already exist, nullable, correct defaults |
| `packages/vantage/src/vantage/service/routes/capabilities.py` | **Unchanged** | no gate, so no flag (D47) |
| `packages/vantage/tests/vantage_port_contract.py` | Modify | the D48 scenarios, against both adapters |
| `scripts/measure_vcs_overhead.py` | **Create** | the RQ-25 harness (Verification, below) |
| `docs/schema-manifest.md` | Modify | five `vcs_*` rows move `M3` → `M1`-style *populated*; see the note below |
| `docs/adr/0014-execute-git-from-the-plugin-as-a-bounded-fail-closed-subprocess.md` | **Create** | the one decision past the filter (D52) |

**The manifest holds five `vcs_*` rows for six columns.** `vcs_commit_subject` carries a
`†`, the manifest's convention for a field with a sibling `_truncated` flag, so
`vcs_commit_subject_truncated` has no row of its own. Five rows change, six columns become
populated; the manifest's own dagger note ("Milestone 2 adds the writer that populates the
flag") is what this change actually makes true, and it moves with them. The column and
index counts are **unchanged**, so
`test_schema_manifest.py::test_fresh_database_matches_the_recorded_ground_truth` needs no
new numbers.

## Interfaces / Contracts

```python
# pytest_vantage/vcs.py — stdlib only: subprocess, shutil, os, time, dataclasses, pathlib
def capture(rootpath: Path) -> VcsSnapshot:
    """One bounded git read. Never raises. All-null on any failure of the gate."""

# vantage/core/domain/execution.py — stdlib only (RQ-26)
@dataclass(frozen=True, slots=True)
class VcsContext:
    commit: str | None
    branch: str | None
    commit_subject: str | None
    commit_subject_truncated: bool
    dirty: bool | None
    root: str | None
    def merged_over(self, previous: VcsContext | None) -> VcsContext: ...

@dataclass(frozen=True, slots=True)
class Execution:
    ...
    vcs: VcsContext | None = None      # appended with a default; every existing
                                       # construction site keeps working

# vantage/service/truncation.py
MAX_TEXT_FIELD_BYTES = 64 * 1024
def truncate(value: str | None) -> tuple[str | None, bool]:
    """Cut to MAX_TEXT_FIELD_BYTES of UTF-8, on a character boundary."""
```

Wire section, both reports:

```json
"vcs": {"commit": "…", "branch": "main", "commit_subject": "…",
        "dirty": false, "root": "/abs/path"}
```

The storage **port** (`core/ports/storage.py`) is **unchanged** — `record_session` already
takes an `Execution`, and `vcs` rides on it.

## Testing Strategy

Strict TDD, RED first. Every verifying test carries `@pytest.mark.req(id="RQ-xx")` for the
identifiers that exist; new obligations are named by capability and scenario — **no new
`RQ-xx` is minted**. Two fixture shapes are fixed by the spec and are not negotiable at
apply time.

| Obligation | Method | Why, and what the fixture actually is |
| --- | --- | --- |
| RQ-10.1 dirty tree | **Test** | real repo in `tmp_path`; commit, then modify a **tracked** file. A second arm: a **staged-only** change is also dirty |
| RQ-10.2 clean tree + hash | **Test** | assert against `git rev-parse HEAD` read independently of the plugin |
| RQ-10.3 detached HEAD | **Test** | `git checkout <sha>`; commit recorded, branch **null** |
| RQ-10.4 no commits yet | **Test** | `git init` only; commit **null**, run stored, invocation 4 never spawned |
| RQ-23.1 not a repository | **Test** | bare `tmp_path`; five nulls **and no warning emitted** |
| RQ-23.2 appears in a run list | **Inspection**, blocked | there is no run list. Verified at storage level (`count_executions`, `get_execution`) and recorded in the spec as awaiting `read-api`. **Not claimed as met** |
| RQ-39.1 corrupt `.git` | **Test** | a real `.git` **file** containing garbage, and a `.git` directory with a truncated `HEAD` — not a mock. Asserts nulls **and exactly one** warning |
| RQ-39.2 no `git` on PATH | **Test** | `monkeypatch.setenv("PATH", str(empty_dir))` for real. **Never** `mock.patch("subprocess.run")` — that proves the mock, not `FileNotFoundError` |
| RQ-39.3 exit status preserved | **Test**, **both arms** | a passing suite exits 0 *and* a failing suite exits 1. RQ-21's rationale names the second as the one a naive boundary swallows |
| RQ-39 permissions case | **Inspection**, skip-if-root | `chmod 000 .git` is a no-op as root and CI containers run as root; a test that cannot fail is the `session-lifecycle` defect again |
| VCS capture isolation (non-latching) | **Test** | `vcs.capture` patched to raise; assert exit 0, every result recorded, heartbeats unaffected, `_disabled` and `_liveness_disabled` both still `False` |
| 5-second bound | **Test** | a fake `git` shim on `PATH` that sleeps; assert the capture returns inside the budget and the snapshot is all-null |
| Whole-capture budget, not per-invocation | **Test** | a shim that sleeps 3 s on every invocation; assert **one** budget was consumed, not five |
| Truncation | **Test** | a 100 KiB subject → stored ≤ 64 KiB of UTF-8, flag `1`; a 1 KiB subject → stored whole, flag `0`; a multi-byte character on the boundary is not split |
| Plugin cap sits above the server bound | **Test** | a >64 KiB subject arrives long enough that the server sets the flag — the D49 false-zero defect, asserted directly |
| Conflict branch (both adapters) | **Contract** | a second report with a null section leaves recorded values intact; a start-then-finish pair applies once; a reordered start still changes nothing; `typeof(vcs_branch) = 'null'`, never `''` |
| `vcs=None` normalisation | **Contract** | an absent section and an all-null section both read back `execution.vcs is None`, in both adapters |
| Endpoint acceptance | **Test** | a report with `vcs` persists six columns; a report without one still records; no capability probe required first |
| Backward skew | **Test** | a pre-change report shape (no `vcs`) still returns 201 |
| Non-UTF-8 subject | **Test** | a commit authored with a latin-1 message; the plugin does not crash and the stored value is intact-or-replaced, never an exception |
| Newline safety | **Test** | a multi-line first paragraph stores as one line |
| Argv discipline | **Inspection** | every invocation is a literal list, `shell=False`, `cwd=rootpath`, `stdin=DEVNULL`, and no value from the repository or the environment is ever an argv element |
| Monorepo root | **Test** | rootdir inside a subdirectory records git's `--show-toplevel`, not `config.rootpath` |
| RQ-24 still holds | **Test** (existing) | clean-environment install check; `subprocess`/`shutil` are stdlib, no new distribution |
| RQ-26 still holds | **Test** (existing) | AST architecture walk — `VcsContext` adds no import to `vantage.core` |
| RQ-25 process count | **Test** | ≤ 5 `git` invocations per session, **zero per test**, zero HTTP requests added |
| RQ-25 overhead | **Analysis** | below |

### The measurement is a number in the spec, not a `print()`

`run-recording/spec.md` sets the precedent: a **Measurements** paragraph carrying real
figures (252,511 bytes body, ~2,021,039 bytes peak) attached to a named artefact. This
change follows it.

- **Harness**: `scripts/measure_vcs_overhead.py`, run by hand, not a CI test — five paired
  runs **interleaved** (A/B/A/B…), medians not means. A ten-minute benchmark in the
  3.10–3.13 × xdist matrix would be a check people learn to skip.
- **Profiles**, both of RQ-25's: criterion 1's (1,000 tests × ~10 ms, ~10 s suite) and
  criterion 3's (1,000 tests × ~1 ms, ~1 s suite), where a fixed session cost dominates and
  the requirement's own notes say the number is recorded *whether or not* the 2 % holds.
- **Repositories**, both: this repository, and a synthetic one with ≥ 20,000 tracked files
  (generated — synthetic data only, CLAUDE.md).
- **Reported separately**: the git cost from the report cost, and
  `status --untracked-files=no` from the default `status`, so the D44 flag choice is
  justified by a number rather than an argument.
- **Committed**: the medians, the machine, the git version, the Python version and the
  date, transcribed into `version-control-context/spec.md`. A future change to the argv or
  the invocation count MUST re-run it — stated in the spec, as `run-recording` states it.
- Pre-measurement forecast, so the result can disagree with it: ~10–60 ms once per
  session; ~0.6 % of the 10 s profile (inside the 2 % budget), ~6 % of the 1 s profile.
  Zero added requests, so RQ-25 criterion 2 is untouched.

## Threat Matrix

`references/threat-matrix.md` is **Applicable** — this is the first change in the project
to add a subprocess and a VCS boundary.

| Boundary | Adversarial cases | Applicability | Design response | Planned RED tests |
| --- | --- | --- | --- | --- |
| Documentation-like paths | `requirements.txt`, executable Markdown, `README.sh` | **N/A** — nothing is classified by content and nothing but `git` is executed; `vcs.py` reads no project file | — | — |
| Git repository selection | `git -C`, relative paths, absolute paths | **Applicable** | `cwd=rootpath` only; never `-C`; never a relative path; **no user- or repository-derived value is ever an argv element**; git's own `--show-toplevel` is recorded rather than assumed equal to rootdir | monorepo subdirectory records the toplevel; a non-repository rootpath records nulls; an argv-discipline inspection over the module |
| Commit state | staged, `commit -a`, empty index | **Applicable** | `--porcelain --untracked-files=no` reports staged and unstaged changes to tracked files and ignores untracked ones (RQ-10.1); an unborn HEAD is `commit = None`, not an error | staged-only ⇒ dirty; worktree-only ⇒ dirty; untracked-only ⇒ **clean**; `git init` only ⇒ commit null, run stored |
| Push state | tracking branch, first push, explicit refspec | **N/A** — nothing pushes, no remote or refspec is resolved, and no invocation touches the network | — | — |
| PR commands | explicit `--head`, environment prefix, composed commands | **N/A** — no PR automation, no composed command, no shell; `shell=False` and argv is always a list | — | — |

Two boundaries this change adds that the matrix has no row for, recorded as notes rather
than invented rows:

- **Process liveness.** A `git` that never returns is RQ-21 criterion 4's failure in a new
  place. Response: one 5-second budget across the whole capture (D44), `stdin=DEVNULL`,
  `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=""`, `GIT_OPTIONAL_LOCKS=0`. RED test: a sleeping
  `git` shim on `PATH`.
- **Untrusted subprocess output.** `git`'s stdout is not trusted to be UTF-8, short, or
  single-line. Response: `errors="replace"`, cut at the first `\n`, capped above the
  server's bound (D49), stored and never echoed into a response body. RED tests: latin-1
  subject; multi-line paragraph; oversized subject.

## Migration / Rollout

**No migration.** The schema is untouched, no `schema_version` bump, ADR-0013's refusal
gate is not engaged, and rollback leaves six columns written by nothing — indistinguishable
from a pre-change row to every consumer that exists, because nothing reads them until
`read-api` lands.

The proposal forecast four slices. **This design forecasts five**, because slice 3 came out
at ~470 lines with no headroom, and `session-lifecycle`'s lesson was that an
under-forecast slice splits at apply time anyway — six slices instead of four, discovered
late.

| # | Slice | Est. lines | Independently deliverable |
| --- | --- | --- | --- |
| 1 | `vcs.py` + real-repository fixtures for every RQ-10/23/39 case (D43–D46, D50) | ~430 | Yes — nothing calls it yet |
| 2 | Recorder wiring, both reports, exit-status both arms, non-latching proof, process count (D51) | ~330 | Yes — an older server ignores the section |
| 3 | Domain `VcsContext` + `merged_over`, `VcsReport`, `_to_execution` normalisation, `truncation.py` (D47, D49) | ~260 | Yes — the route maps a section nothing persists yet |
| 4 | `_UPSERT_RUN` + `_row_to_execution` + memory adapter + port contract scenarios (D48) | ~250 | Yes — **depends on slice 3** |
| 5 | RQ-25 measurement and committed numbers, ADR-0014, schema-manifest, traceability | ~280 | Yes |

~1,550 lines across five slices; **no slice exceeds the 500-line review budget.**
`chain_strategy: feature-branch-chain`, on `ft/vcs-capture`. Rollback is in reverse chain
order.

Server before plugin is **not** required here, unlike `session-lifecycle`: an older server
ignores the section and records exactly as today, which is why no capability gate exists
(D47). Slices 1–2 may therefore land ahead of 3–4 without a half-recorded state.

## Open Questions

None blocks `sdd-tasks`.

- [ ] A `safe.directory` refusal warns once per session in a container whose uid differs
      from the checkout's owner (D45). Matching `dubious ownership` in stderr would fix it
      and would couple the warning policy to git's message text; `LC_ALL=C` keeps that
      refinement available without redesign.
- [ ] RQ-22 asks for a truncation marker *inside* the value (its criterion 1); this change
      sets the sibling flag only (D49). RQ-22's own change decides whether the two coexist.
- [ ] `VcsReport` is `extra="forbid"` (D47). If the section ever needs enrichment — a tag,
      a remote — the answer is `ResultReport`'s `extra="allow"` plus
      `Acknowledgement.ignored`, decided when there is something to add, not now.
- [ ] `git status --porcelain=v2 --branch` would collapse four invocations into one (D44).
      Held until slice 5's measurement says whether it is needed.
- [ ] `Acknowledgement.status` still says `"duplicate"` for a finish-write that applied
      real data (D26). Unchanged by this change, still open for `read-api`.
