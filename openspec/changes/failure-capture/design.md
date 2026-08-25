# Design: Failure Capture

> **Identifier vocabulary.** No numeric requirement identifiers are minted here
> (CLAUDE.md, decided 2026-08-18). Every obligation is anchored to the
> **capability** and, where one exists, the **scenario** that owns it. Existing
> `RQ-xx` identifiers are cited only where the obligation already carries one.
> Decisions continue the project's single running sequence: `read-api` closed at
> **D67**, so this change opens at **D68**.
>
> **Every traceback, path and message in this document is invented.** Synthetic
> data only; public repository (CLAUDE.md, Constraints).

## Technical Approach

The proposal's order — plugin first, wire second, storage third, read surface
fourth, proof last — survives contact with the code, with one structural
correction it did not foresee.

**The rendering cannot happen where the recorder lives.** `item` and `excinfo`
exist only in the process that ran the test. Under xdist that is a *worker*, and
`plugin.py::pytest_configure` returns before anything registers when
`hasattr(config, "workerinput")` — so today nothing at all runs on a worker.
Failure evidence therefore needs a second registered plugin object that does run
there, and that is D68, the decision everything else in the plugin hangs off.

Everything downstream then follows established patterns rather than new ones.
The evidence rides to the controller as a plain dict on the report, the way
`wasxfail` already rides. The per-report budget is spent at assembly time on
encoded bytes, before the request exists, because a 413 costs the whole session
and `truncate()` is structurally too late. The server keeps the 64 KiB bound and
the flags exactly as it does for `commit_subject` (D49), with one deliberate
asymmetry: the flag becomes a disjunction, because only the client knows about a
budget drop (D75). The lean-list rule gets a display-bounded `failure_message`
and a projection type with no heavy field at all — `VcsProjection`'s shape (D59,
D60), applied to results. The single-result endpoint takes its identity as a
query value, inheriting D54 verbatim rather than re-arguing it.

`schema.sql` is byte-unchanged and `meta.schema_version` stays `'2'`. That is
not a coincidence; it is what RQ-29 and ADR-0005 were written to buy, and this
change is the first to collect it.

---

## Architecture Decisions

### D68 — Failure evidence is collected by a second plugin object that runs on xdist workers

**The problem the proposal did not name.** `pytest_runtest_makereport` fires in
the process that ran the test. Under `-n 4` that is a worker.
`plugin.py::pytest_configure`'s first statement is
`if hasattr(config, "workerinput"): return` — the RQ-27/RQ-1 guard that stops
four workers each registering a `Recorder`. So a hookwrapper on `Recorder` would
never fire under xdist, and the whole change would silently record nothing on
the configuration the CI matrix exercises.

| Option | Verdict |
| --- | --- |
| Hookwrapper on `Recorder` | **Impossible.** `Recorder` is controller-only by construction |
| Hookwrapper on `plugin.py` (always imported) | **Rejected.** That module is declared inert — "no hook capable of any side effect" — because pytest fires any `pytest_*` hook it finds on a registered plugin. A makereport wrapper there runs in every pytest session in the world, activated or not. CON-05 and RQ-2 hold together *because* of that split |
| Re-render on the controller from the forwarded report | **Impossible.** No `item`, no `excinfo`, no source |
| Store `report.longreprtext` and accept `--tb` dependence | **Settled against** — proposal Q1 |
| **A second plugin object registered on both controller and workers** | **Chosen** |

```python
# pytest_vantage/evidence.py  (new)
class EvidenceCollector:
    """One hookwrapper, no I/O, no state beyond two session-constant values."""
    def __init__(self, config: pytest.Config) -> None:
        self._config = config
        self._disabled = False
        self._capture_disabled = config.getoption("capture") == "no"   # D71
```

`pytest_configure` gains a worker branch **before** the existing early return:

```
worker (hasattr workerinput):  activation? → register EvidenceCollector → return
                               (no preflight, no capability probe, no Recorder,
                                no socket of any kind)
controller:                    activation? → register EvidenceCollector
                               → preflight → capability probe → register Recorder
```

Three properties this must have, and how each is obtained:

1. **No worker opens a socket.** The worker branch returns before
   `_preflight_reachable`. `-n 16` still makes exactly one preflight and one
   capability probe, from the controller. A test asserts the worker path
   registers no `Recorder`.
2. **A rendering failure cannot disable reporting.** `EvidenceCollector` carries
   its own `_disabled` flag, so `boundary._isolated`'s per-instance latch
   applies unchanged — the same reason `liveness_isolated` exists rather than
   reusing `fault_isolated`'s flag (D29).
3. **The decorator cannot wrap the hookwrapper itself.** `fault_isolated`
   returns `None` on exception; a hookwrapper that returns instead of yielding
   breaks pluggy. So the `yield` is never inside a `try`, and the isolation is a
   bare `try/except Exception` around the post-yield body only — the same shape
   ADR-0014 condition 2 requires of `vcs.py`, for the same reason.

```python
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(self, item, call):
    outcome = yield                      # never inside try — pluggy contract
    if self._disabled:
        return
    try:
        report = outcome.get_result()
        report.vantage_evidence = _extract(item, call, report, self._capture_disabled)
    except Exception as exc:             # never BaseException — RQ-21, RQ-31
        self._disabled = True
        _warn(self._config, f"vantage: error while capturing failure evidence: {exc}")
```

**`hookwrapper=True`, not `wrapper=True`.** The floor is `pytest>=8.0` and the
installed version is 9.1.1; the old form works on both, the new one does not
exist below pluggy 1.3. If a future pytest removes it, the migration is two
lines (`result = yield` / `return result`) and the 3.10–3.13 matrix is what
surfaces it.

**How the evidence crosses the xdist wire.** `report.vantage_evidence` is a flat
`dict[str, str | int | bool | None]`. `TestReport._to_json` copies `__dict__`
and `TestReport.__init__(**extra)` does `self.__dict__.update(extra)`, which is
exactly the mechanism `wasxfail` already round-trips through. Every value is
JSON-primitive, so `json.dumps` on the execnet channel cannot choke. A public
attribute name, not `_vantage_evidence`: an underscore would suggest it is
private to an object it does not belong to. The xdist-serialization test the
proposal already scopes is what proves this rather than assuming it.

`Recorder.pytest_runtest_logreport` is unchanged — `accumulate` stores the whole
report, so the evidence arrives with it and RQ-12's existing dedup filter still
applies to exactly one thing.

### D69 — What is rendered, and where each field comes from

| Column | Source | Cost |
| --- | --- | --- |
| `failure_type` | `call.excinfo.typename` | free |
| `failure_message` | `call.excinfo.exconly()` | free |
| `failure_repr` | `repr(call.excinfo.value)` | free |
| `traceback` | `str(item._repr_failure_py(excinfo, style="long"))` | **the second rendering** |
| `failure_path` / `failure_lineno` | the rendered repr's `reprcrash.path` / `.lineno` | free, given the rendering |

**Corrected against the installed pytest (9.1.1), Phase 3.** This table
originally read `item.repr_failure(excinfo, style="long")`. That signature no
longer exists: `Function.repr_failure` (the override actually used for a test
item) dropped the `style` keyword and reads `config.getoption("tbstyle")`
internally instead, which would reintroduce the exact `--tb` dependence this
capability exists to remove, and calling it with `style=` raises `TypeError`.
The shipped code (`evidence.py::_failure_fields`) calls
`item._repr_failure_py(excinfo, style="long")` — the method both
`Node.repr_failure` (still accepts `style`) and `Function.repr_failure`
delegate to, and `Function` does not override it — inside its own deliberately broad
`try/except Exception`, per D69's own guarded-extraction rule below. This is
the first of three places in this document that stated an existing artifact's
shape from memory rather than probing it; see the note at the end of D80 for
the other two and the process fix.

**Two divergences from the proposal, both spec-neutral** (no scenario names a
source):

**`failure_message` comes from `excinfo.exconly()`, not from
`longrepr.reprcrash.message`.** They are the same string — `FormattedExcinfo`
builds `ReprFileLocation` with `excinfo.exconly()` — but `exconly()` needs no
rendering and cannot be absent, so the message survives every branch of D70
including the one where rendering itself failed. Taking a value from the object
that owns it rather than from a rendering of that object is cheaper and has one
fewer failure mode.

**`failure_repr` is `repr(excinfo.value)`, the exception object's own
representation.** The proposal left its source unstated. This choice gives three
genuinely different granularities, none derivable from another:

| Field | Answers | Independent of |
| --- | --- | --- |
| `failure_message` | what pytest would print in the short summary | — |
| `failure_repr` | what the exception object *is* | pytest's renderer entirely |
| `traceback` | how execution got there | — |

It is also why `failure_repr_truncated` exists in `schema.sql`: an assertion over
a large structure produces a `repr` in the megabytes.

**`str(...)` is the public rendering path.** `TerminalRepr.__str__` writes
through a `TerminalWriter` with `hasmarkup = False` — the identical mechanism
`BaseReport.longreprtext` uses, reached without importing `_pytest._io`. No
private module is imported anywhere in this change.

**The rendered repr is never assigned to `report.longrepr`.** Doing so would
change what the terminal prints, which is the user's output and not this
plugin's to alter. The rendering is a throwaway; only its text and its
`reprcrash` are read.

**Every free-listed call above is still guarded.** `repr()`, `exconly()` and
`repr_failure()` all execute user code — a broken `__repr__`, an unreadable
source file, a `__str__` that raises. Each field is extracted in its own
`try/except Exception` that yields `None` for that field alone, so one hostile
object costs one column, not the whole result. D68's latch is the outer net, not
the first one.

**Which phase's evidence is recorded.** A test can produce evidence in setup,
call and teardown. There is exactly one precedence in this codebase —
`capture.derive_outcome`'s nine rows (D17) — and this selection is keyed off its
*result*, never restated:

| Derived outcome | Evidence taken from |
| --- | --- |
| `error` | setup if setup failed, else teardown |
| `failed` | call |
| `skipped` | setup if setup skipped, else call |
| `xfailed` | call |
| `xpassed`, `passed` | none |

Captured output is the exception: it is the concatenation of all three phases
(D71), because that is a record of what ran, not of what went wrong.

### D70 — The non-exception shapes: branch on the report, never on `.reprcrash`

`report.longrepr` is a `(path, lineno, reason)` **tuple** for a skip. Naive
`.reprcrash` access raises `AttributeError`, and the `failure-evidence` scenario
*A skipped test does not crash the recorder* exists to falsify exactly that.

The branch is driven by the report and the `excinfo`, in this fixed order:

| Order | Test | Recorded |
| --- | --- | --- |
| 1 | `call.excinfo is None` | no failure fields at all; captured output only |
| 2 | `hasattr(report, "wasxfail")` | `xfail_reason = report.wasxfail`; no failure fields, no traceback |
| 3 | `report.outcome == "skipped"` | `skip_reason`; no failure fields, no traceback |
| 4 | otherwise | the full D69 set |

Three rules that are not obvious and are each a defect if got wrong:

- **`hasattr(report, "wasxfail")`, never its truthiness.** pytest sets the
  reason to `""` for a bare `@pytest.mark.xfail`. `capture.derive_outcome`
  already documents this and this branch reuses the rule rather than restating
  it — the forbidden idiom is `report.wasxfail or None`, which erases a genuine
  empty reason.
- **Row 2 precedes row 3** because an xfail arrives with `outcome == "skipped"`
  *and* `wasxfail`, and `xfail_reason` and `skip_reason` are different columns.
- **`skip_reason` is read as `longrepr[2]` behind
  `isinstance(longrepr, tuple) and len(longrepr) == 3`**, with `str(excinfo.value)`
  as the guarded fallback. It is stored **verbatim**, including pytest's own
  `"Skipped: "` prefix where present. Stripping that prefix would be a second
  parser of pytest's display text and would silently disagree with pytest the
  first time the prefix changes.

**Rows 2 and 3 store no traceback, deliberately.** A suite with 400 expected
failures would otherwise spend the entire per-report budget on evidence nobody
asked for, and the `failure-evidence` requirement states the obligation as
"record its skip or xfail reason **instead of** these failure fields".

### D71 — Captured output: phase attribution is dropped; absence is decided by the capture mode

**Attribution is dropped.** Options and why the others lose:

| Option | Why not |
| --- | --- |
| In-band phase headers (`--- captured stdout (setup) ---`) | A test that prints that exact line forges a phase boundary. That is the same forgeability objection that made the truncation marker out-of-band (proposal, correction 2), and it burns budget bytes on delimiters |
| Structural attribution (three columns, or JSON in one) | `schema.sql` declares exactly `captured_stdout` and `captured_stderr`, and *no schema change* is settled. JSON in a text column is unqueryable, breaks the 64 KiB byte bound's meaning, and collides with empty-versus-absent |
| **Concatenate in phase order, no marker** | **Chosen** |

What is paid: a reader cannot tell a fixture's `print` from the test's own. What
is kept: **order**. Setup output precedes call output precedes teardown output,
so "what the code was doing beforehand" is still readable positionally — which
is most of what the attribution was for. The limitation is recorded in the
capability spec rather than discovered, and structured log capture (`result_log`)
is already the named future change that would carry attribution properly.

**Empty versus absent cannot be decided from the report.** `report.capstdout`
returns `""` in three different situations, and `report.sections` does not
separate them either — pytest calls `add_report_section` only `if out:`, so a
silent test with capture *on* produces no section, exactly like a session with
capture off.

The distinguisher is therefore the **session's capture mode**, read once at
`EvidenceCollector.__init__`:

```python
self._capture_disabled = config.getoption("capture") == "no"     # -s / --capture=no
```

| Session | `captured_stdout` | Meaning |
| --- | --- | --- |
| `--capture=no` / `-s` | `None` | never captured |
| default, test printed nothing | `""` | captured, and empty |
| default, test printed | the joined text | — |

`None` is produced by a branch on `_capture_disabled`, never by `text or None` —
the forbidden idiom this module family names (RQ-5.2, RQ-9.3).

**One honest limit, recorded not hidden.** A test using the `capsys`/`capfd`
fixture *consumes* its own buffer with `readouterr()`, so that output never
reaches a report section and Vantage stores `""`. Nothing in pytest's public
surface lets an observer see consumed output. Documented in the capability spec
as a known gap.

### D72 — The opt-in: `--vantage-failure-text`, absent by default, monotone by construction

**Revised 2026-08-25, after Phase 9's own RQ-25 measurement.** This decision
originally shipped as an opt-out (`--vantage-no-failure-text`, capture on by
default). The measurement that decision itself called for (D79) found
failure-text capture breaches RQ-25's 2% overhead budget **at every failure
density tested, not only the pathological all-failing case** — ten failing
tests out of a thousand already cost 3.45% of a recording-off baseline (see
`failure-evidence` → Measurements). An opt-out cannot fix that: the default
path is the one everybody runs, and the default path was the expensive one.
The decision is superseded in place, not renumbered: it is still D72, the
surface it touches is still `pytest_vantage/config.py` and `plugin.py`'s two
option registrations, and the reasoning below replaces the opt-out's, rather
than sitting beside it as a second history.

**Capture is now opt-in and absent unless the invocation asks for it.**

| Surface | Value | Can it enable? |
| --- | --- | --- |
| `--vantage` | `store_true` | **This and only this activates recording** (RQ-2, unchanged) |
| `--vantage-failure-text` | `store_true`, default `False` | Yes — but only capture, never recording itself |
| ini `vantage_failure_text` | bool, default `false` | Yes, identically — either source alone is enough |
| environment variable | **none defined** | — |

**Composition** is a single monotone disjunction:

```python
def resolve_failure_text_capture(*, activated: bool, cli_opt_in: bool, ini_opt_in: bool) -> bool:
    return activated and (cli_opt_in or ini_opt_in)
```

Three properties, each deliberate:

1. **No source can turn recording's own `False` into a `True`.** The function
   remains monotone in every argument — the property test carried forward
   unchanged, `resolve(...) <= activated` for every one of the eight input
   combinations — but where the opt-out was monotone-*decreasing* in its two
   narrowing sources, the opt-in is monotone-*increasing* in `cli_opt_in` and
   `ini_opt_in`: each can only ever ADD capture to an already-activated
   session, never remove it, and neither can activate recording on its own.
   **The opt-in is monotone too, precisely because it only ever adds** — the
   same shape of guarantee the opt-out gave by only ever removing, mirrored
   rather than lost.
2. **Both names now carry the plain capability, not a negation.** There is no
   syntactic form that reads as a refusal by accident, because there is
   nothing left to refuse by default — capture already defaults to absent.
3. **No environment variable.** RQ-2's rationale is unchanged by the flip: an
   environment variable is invisible in the command line RQ-11 records. For an
   opt-in that means a run whose stored evidence is *present* with nothing in
   its own history to explain why it was requested — the same blind spot the
   opt-out's version of this argument named, mirrored onto the other polarity.
   `resolve_report_timeout` already defines none, for a weaker reason than
   this one.

**A failure-count cap was considered and rejected instead of the polarity
flip.** The arithmetic: RQ-25 leaves roughly 55 ms of headroom per session
after `version-control-context`'s own spend, and a single rendered failure
costs 32–48 ms depending on density — the budget admits **roughly four
failures per session** before it is spent. A cap that low is not a
failure-capture feature; it is a feature that fails on the fifth test in an
otherwise-healthy suite. Absent-by-default has no such ceiling, because the
common case pays nothing at all rather than a small, arbitrary amount.

**What the opt-in actually does: `EvidenceCollector` is registered only when
requested.** Not a flag checked per test — the hookwrapper does not exist
until then. Consequence worth stating: the *default* session now pays
**zero** of D69's second-rendering cost, so the absent-by-default posture is
itself the RQ-25 lever, not a flag someone has to remember to pass. Outcome,
timings and identity are untouched either way, because `Recorder` never
consulted the collector for them.

**RQ-2's differential test is the guard**, in its established form, carried
forward unchanged in shape: run the suite once with the ini value present and
once absent, with no flag on either invocation, and assert failure-text
capture behaves identically — a committed configuration file still cannot be
the means by which capture, or recording itself, turns on.

### D73 — `MAX_FAILURE_TEXT_BYTES = 512 * 1024`, mirrored in the plugin, pinned by a test

**The number.** Exactly `MAX_REPORT_BYTES // 2` — half the server's 1 MiB
report cap. The derivation, from numbers this repository already measured:

| Quantity | Value |
| --- | --- |
| `MAX_REPORT_BYTES` (`service/errors.py:32`) | 1,048,576 |
| Measured non-failure body, 500-result session (`run-recording` → Measurements) | 252,511 bytes ≈ **505 bytes/result** |
| Budget | **524,288** |
| Non-failure body the other half accommodates | 524,288 ÷ 505 ≈ **1,038 results** |

So for any session up to ~1,000 results — comfortably above the 500-result
session `run-recording` measures — a report that respects the budget cannot
breach the cap.

**The limit this does not fix, stated rather than hidden.** A 3,000-result
session encodes ~1.5 MB with *zero* failure text and is rejected today, before
this change and after it. The budget bounds failure text; it does not bound the
report. That pre-existing condition is named here and is the natural subject of
a separate change (chunked or streamed ingestion), not something this one
silently appears to have solved.

**Why a fixed constant and not the remaining headroom.** A dynamic budget —
`MAX_REPORT_BYTES − measured_skeleton − margin` — adapts to any session size and
was seriously considered. Rejected on two counts: it costs a second full encode
pass over every result, and it makes the budget a number no document can state,
which leaves `failure-evidence`'s *A session within budget carries no exhaustion
flags* scenario untestable without reproducing the arithmetic inside the test.
A budget the spec can name is worth more than a budget that adapts.

**Where it lives, and how the mirror is kept honest.** RQ-24 forbids
`pytest-vantage` importing `vantage.service.truncation` or
`vantage.service.errors`, so:

```python
# packages/pytest-vantage/src/pytest_vantage/budget.py  (new)
_REPORT_BYTES_CAP = 1024 * 1024      # mirrors vantage.service.errors.MAX_REPORT_BYTES
MAX_FAILURE_TEXT_BYTES = _REPORT_BYTES_CAP // 2
```

The duplicated-constant shape has a precedent — `core/config/resolution.py`'s
`_BEAT_INTERVAL_HINT_SECONDS` mirrors `recorder._BEAT_INTERVAL_SECONDS` across
the same boundary. But that comment says a divergence "changes the multiple,
never correctness", and **here a divergence is exactly correctness**: a mirror
that drifts high produces 413s that reject whole sessions. So this mirror gets
what that one did not — a test:

```python
# packages/pytest-vantage/tests/test_report_budget.py
from vantage.service.errors import MAX_REPORT_BYTES        # test-only import
from pytest_vantage.budget import _REPORT_BYTES_CAP, MAX_FAILURE_TEXT_BYTES

def test_the_mirrored_cap_matches_the_server():
    assert _REPORT_BYTES_CAP == MAX_REPORT_BYTES
    assert MAX_FAILURE_TEXT_BYTES * 2 == MAX_REPORT_BYTES
```

The import is dev-only and lives in a test file; `test_plugin_imports.py` remains
the guard that no shipped module reaches across. The clean-environment install
check (CLAUDE.md, Quality gates) is what proves the shipped wheel still stands
alone.

### D74 — How the budget is spent: one pass, execution order, field priority, dropped whole

```
for entry in results (execution order — the order tests ran):
    for field in (failure_message, failure_repr, traceback,
                  captured_stdout, captured_stderr):
        cost = len(json.dumps(value).encode("utf-8"))
        if cost <= remaining:  remaining -= cost
        else:                  value = None; <field>_truncated = True
```

**Encoded bytes, measured, not `len(str)`.** `json.dumps` of the value itself is
what the wire will carry for that field, escapes and quotes included — a
traceback is newline- and quote-heavy, so a raw byte count understates it by a
third or more.

**Corrected in Phase 5 (fix `cf008f7`): no `ensure_ascii` argument, not
`ensure_ascii=False`.** This snippet originally specified `ensure_ascii=False`
on the grounds that it "matches `transport.send`'s `json.dumps` default" — that
claim was checked against nothing; `transport.send` calls `json.dumps(report)`
bare, whose default is `ensure_ascii=True`. Charging `False` here understated
every codepoint above `0x7F`, measured 1.30x for Spanish accents, 1.84x for
Japanese, 1.65x for emoji: a suite whose assertion messages are not English
would pass this budget and still breach `MAX_REPORT_BYTES` on the wire,
losing the whole session, run included — the exact failure this budget exists
to prevent. `budget.py::_encoded_cost` charges the bare-`json.dumps` cost, no
argument, so the measurement and the encoding agree for real. The second of
three places this document stated an existing artifact's shape from memory;
see the note at the end of D80 for the third and the process fix.

**Allocation policy: first-come, in execution order.**

| Policy | Why not |
| --- | --- |
| Even split across failed results | Needs the failed count first, then a water-filling redistribution pass to avoid truncating small tracebacks that would have fit. And half a traceback is not half as useful — a rendering cut mid-frame usually answers nothing |
| Smallest-need-first (maximise complete failures) | Needs a sort, and the dropped set is then an arbitrary subset a reader cannot predict |
| **Execution order, first-come** | **Chosen** |

Execution order is the order a human reads a failing run in; the first failure is
the one people investigate, and a cascade's later failures are usually copies of
it. It is one pass, no sort, no second encode, and trivially deterministic — a
property *A session within budget carries no exhaustion flags* needs in order to
be a stable test.

**Field priority within a result** is smallest-and-most-informative first, so the
question "what broke?" is answered for as many results as possible before the
question "how did it get there?" is answered for any. `failure_type`,
`failure_path`, `failure_lineno`, `skip_reason` and `xfail_reason` are **not
charged and never dropped**: they are short by nature (a class name, a path, an
integer), and they are the columns index 5 groups on — dropping them would
disable the grouping this change exists to deliver.

**A field is dropped whole, never cut.** Two reasons, both structural:

1. Cutting is already what `_truncated` means at 64 KiB. A *second* cutting rule
   at a different threshold would put two meanings behind one boolean and make
   "how much of this is real?" unanswerable.
2. Cutting correctly means slicing UTF-8 at a character boundary — the logic
   `truncation.truncate` owns, on the far side of the RQ-24 boundary. Dropping
   needs none of it. The boundary is respected by not needing to cross it, not by
   copying code across it.

Consequence, accepted: a 100 KiB traceback arriving with 64 KiB remaining leaves
that 64 KiB unspent unless a later field fits it. The alternative is duplicating
`truncate()`; this is cheaper.

**"Dropped" on the wire is exactly:**

```json
{"traceback": null, "traceback_truncated": true}
```

against `{"traceback": null, "traceback_truncated": false}` for a result that
never had one. The flag means *what you are reading is not complete* — true
whether the field was cut at 64 KiB or dropped at the budget — and it preserves
the empty-versus-absent distinction without a second column. No new column is
introduced, and `schema.sql` stays byte-unchanged.

### D75 — The truncation flag is a disjunction of the client's and the server's

`truncate(None)` returns `(None, False)`. So a server that assigns
`stored_flag = server_flag` **clears** a budget drop the client reported, and the
`failure-evidence` scenario *A field dropped for budget is flagged, not missing*
fails silently. The server must therefore OR:

```python
message, server_truncated = truncate(item.failure_message)
message_truncated = bool(item.failure_message_truncated) or server_truncated
```

**This is a deliberate divergence from D49**, where `VcsReport` carries *no*
`commit_subject_truncated` field at all and the server owns the flag
exclusively. The asymmetry is correct because the situations differ: a commit
subject is one short field that the client always sends whole, so the server is
the only possible truncator. Failure text can be reduced on both sides, and only
the client knows about the budget.

**The trust is one-directional and that is the security argument.** The server
may only *set* the flag from the client's value, never clear it. A buggy or
hostile client can claim a truncation that did not happen — cost: a reader opens
a detail page for nothing. It cannot claim a completeness that did not happen,
which is the failure mode that matters. `ResultReport`'s new boolean fields
default to `False`, so an older plugin that sends none is indistinguishable from
one that sends `false`, and both are correct.

### D76 — The lean-list gap: `failure_message` gets a display width; the heavy three are excluded structurally

**The hole, stated.** `history-read-api` → *Lean list projections* excludes, by
its literal text, only "traceback and captured output". `failure_message` and
`failure_repr` are each bounded only at 64 KiB. The moment `Result` carries
them, a 200-item list response can carry 200 × 64 KiB of each and "lean" stops
being true. Nothing leaks today only because `Result` has no failure fields at
all.

| Field | In a results list | Why |
| --- | --- | --- |
| `failure_type` | whole | a class name; short by nature, the same class of field as `commit`/`branch` |
| `failure_path`, `failure_lineno` | whole | a path and an integer; and they are what index 5 groups on |
| `failure_message` | **first `LIST_FAILURE_MESSAGE_CHARS` characters**, flag travels | a failure message is exactly what a reader wants in a list of failures |
| `failure_repr` | **excluded** | by name the *full* representation; a `repr` of a large structure is the megabyte case |
| `traceback` | **excluded** | the requirement's own text |
| `captured_stdout`, `captured_stderr` | **excluded** | the requirement's own text |
| `skip_reason`, `xfail_reason` | whole | short by nature; a skip reason is a sentence someone wrote |

**Outright exclusion of `failure_message` was rejected**, and this is the point
of the decision: a list of failures with no messages forces a reader to open
every one to find out whether they are the same failure, which is the exact work
the product exists to remove. The precedent for bounding rather than excluding
is `commit_subject` (D60), and it is followed exactly, including the flag's
widened meaning in a list:

| Where | `failure_message` | `failure_message_truncated` means |
| --- | --- | --- |
| results list | first 200 characters | *this is not the whole stored message* — capture-truncated **or** budget-dropped **or** display-bounded |
| single result | the whole stored value | *the message exceeded the 64 KiB bound, or was dropped for budget* |

**`LIST_FAILURE_MESSAGE_CHARS = 200`**, beside `LIST_COMMIT_SUBJECT_CHARS` in
`vantage/core/domain/projection.py`. Derived, not guessed:

- The value is `excinfo.exconly()` — `ExceptionType: message`. A qualified
  exception name routinely spends 20–40 characters before the discriminating
  content starts, so D60's 120 would leave under 80 characters of actual
  message. 200 keeps the type plus a usable head.
- Page arithmetic: 200 items × 200 characters × 4 bytes is ≈156 KiB in the worst
  multi-byte case and ≈40 KiB for ASCII; with the other result fields at
  ~350 bytes each the worst-case page is ≈226 KiB, against legacy RQ-16's
  "below 500 KB" criterion for a 500-result response.
- **Characters, not bytes**, for D57's reason unchanged: SQLite's
  `substr`/`length` count characters on TEXT and Python's slicing counts
  characters on `str`, so the two mechanisms agree by construction.

In SQL, with `?` bound to `LIST_FAILURE_MESSAGE_CHARS`, the identical shape
`_LIST_RUNS` already uses for the commit subject:

```sql
substr(r.failure_message, 1, ?)                                AS failure_message,
CASE WHEN r.failure_message_truncated = 1
       OR COALESCE(length(r.failure_message) > ?, 0) = 1
     THEN 1 ELSE 0 END                                         AS failure_message_truncated
```

`COALESCE` is load-bearing for the same reason as in `_LIST_RUNS`:
`length(NULL) > 200` is `NULL`, not `0`, and a null message must produce a
`false` flag rather than a null one.

**Why the promoted-to-Test exclusion is genuinely falsifiable now, and how it
differs from D59's structural exclusion.** The list query does not select the
heavy columns, so the store never materialises what the wire will not carry
(D57), and `ResultListEntry` has no field to put them in (D77) — that is the
*defence*. The *proof* is a byte-level assertion: a fixture stores a result whose
traceback contains a distinctive synthetic sentinel, and the test asserts the
sentinel is absent from the raw list response body and present in the raw
single-result body. That test goes red the moment someone widens the projection.
`test_routes_read.py`'s task-7.6 comment — "this stays Inspection, honestly,
until failure capture lands a `traceback` field on `Result`" — is deleted,
because the condition it names is now met.

### D77 — Domain shape: two nested types on `Result`, one projection type for lists

Nesting follows `Execution.vcs: VcsContext | None`, the house pattern, rather
than adding seventeen flat fields to a dataclass that has twelve:

```python
# vantage/core/domain/result.py — stdlib only (RQ-26)
@dataclass(frozen=True, slots=True)
class FailureEvidence:
    failure_type: str | None
    failure_message: str | None
    failure_message_truncated: bool
    failure_path: str | None
    failure_lineno: int | None
    failure_repr: str | None
    failure_repr_truncated: bool
    traceback: str | None
    traceback_truncated: bool
    skip_reason: str | None
    skip_reason_truncated: bool
    xfail_reason: str | None
    xfail_reason_truncated: bool

@dataclass(frozen=True, slots=True)
class CapturedOutput:
    stdout: str | None            # None = never captured; "" = captured, empty
    stdout_truncated: bool
    stderr: str | None
    stderr_truncated: bool

@dataclass(frozen=True, slots=True)
class Result:
    ...                                   # the existing twelve, unchanged
    failure: FailureEvidence | None       # all-null → None (D48's rule, inherited)
    captured: CapturedOutput              # never None — see below
```

**The asymmetry is deliberate.** `failure` normalises to `None` when every field
in it is null-or-false, exactly as `_to_vcs_context` normalises an all-null
`vcs` section (D48) — a failure either happened or it did not. `captured` is
**never** `None`, because the absent-versus-empty distinction the spec demands
lives *inside* it, in the `str | None` fields; collapsing an all-null
`CapturedOutput` to `None` would put that same distinction in two places, and
two encodings of one fact drift.

`Result.__post_init__` keeps its existing `OUTCOMES` check and gains nothing:
the evidence fields have no cross-field invariant the dataclass can enforce
(a `None` value with a `True` flag is the legitimate budget-drop shape).

**The list type is separate, per D58** — one type used for both would make
`failure_message` mean different things depending on which call produced it:

```python
# vantage/core/domain/projection.py — beside VcsProjection
LIST_FAILURE_MESSAGE_CHARS = 200

@dataclass(frozen=True, slots=True)
class FailureProjection:
    failure_type: str | None
    failure_message: str | None          # bounded to LIST_FAILURE_MESSAGE_CHARS
    failure_message_truncated: bool      # the disjunction of D76
    failure_path: str | None
    failure_lineno: int | None
    skip_reason: str | None
    xfail_reason: str | None
    # no traceback, no failure_repr, no captured output — absent by construction

def project_failure(failure: FailureEvidence | None) -> FailureProjection | None: ...
```

`project_failure` is the reference implementation in Python of the rule the
SQLite adapter states in SQL, and the contract suite holds the two to agreement
— the same two-mechanism pattern as `merged_over`/`COALESCE` (D48) and
`project_vcs`/`substr` (D59).

`list_results` changes its return type from `Page[Result]` to
`Page[ResultListEntry]` (identity, outcome, timings, worker id, and
`failure: FailureProjection | None`). One caller, `routes/read.py`, plus the
contract suite. `get_results` keeps returning `Sequence[Result]`, unchanged and
un-deprecated, as it did through `read-api`.

**Port and adapters move in one commit.** A `Protocol` method that lands without
both adapters breaks `mypy --strict` at every call site (CLAUDE.md, Ports).

### D78 — The single-result endpoint: identity as a query value, D54 inherited

```
GET /api/v1/runs/{run_id}/result?node_id=<value>
```

**Not re-argued.** D54 already decided that a pytest node id cannot travel in a
path segment — an encoded `/` is decoded before any route matcher runs, and
`{identity:path}` succeeds only as a property of the deployment. That reasoning
transfers unchanged; this decision only records that the same answer applies and
that `node_id` is again bounded at `MAX_IDENTITY_CHARS` with an
`InvalidIdentityError`-shaped rejection, never a proxy `414`.

| Aspect | Decision |
| --- | --- |
| Path | `/runs/{run_id}/result` — singular; no ambiguity against the plural `/results` literal |
| Identity | `?node_id=`, `max_length=MAX_IDENTITY_CHARS` |
| Unknown `run_id` | `404`, existing `UnknownRunError` |
| Known run, unknown `node_id` | `404`, **new** `UnknownResultError` — `errors.py` asks for one shape per rejection *kind*, and "no result with that identity in that run" is a distinct kind |
| Port method | `get_result(execution_id: str, *, node_id: str) -> Result | None`, both adapters |
| Response | `ResultDetailResponse` — every field, built **field by field**, never `from_attributes` |
| Interface document | a new `read`-tagged operation in `v1.yaml`, hand-written |

Two inherited consequences worth naming so they are not rediscovered:

- **The new path automatically enlists in the read-only proof** (D53, D65). It
  needs a binding-table entry producing valid parameters from the fixture, and
  the harness asserts every `read` path has one — so forgetting the binding is a
  red test, not a silent skip.
- **`api-interface-document`'s drift check covers it for free.** Adding the route
  without adding it to `v1.yaml` fails `mounted - declared`; adding it to
  `v1.yaml` without mounting it fails the other direction.

`GET /api/v1/runs/{run_id}/results` (the list) and `GET /tests/history` are
otherwise unchanged apart from D76's projection.

### D79 — RQ-25's overhead: the new axis is failure density, and the baseline is current `main`

`scripts/measure_vcs_overhead.py`'s profiles are 1,000 **passing** tests, so they
measure none of D69's cost. A new script, `scripts/measure_failure_capture_overhead.py`,
follows that harness exactly — five interleaved A/B/A/B paired runs per cell,
medians reported, never means — with three changes:

| Aspect | Decision |
| --- | --- |
| Baseline (A) | current `main`: recording **on**, VCS capture **on**, failure capture absent — the default, no invocation flag given |
| Treatment (B) | the identical session with failure capture opted in via `--vantage-failure-text` |
| Why that baseline | it isolates *this* change's cost. `version-control-context`'s numbers already spent part of the budget; measuring against recording-off would re-measure their cost and hide this one |
| Also reported | the recording-off comparison, so the number stays commensurable with `version-control-context`'s existing table |
| **New axis: failure density** | 1,000 tests at ~10 ms with **1%**, **10%** and **100%** failing |
| **New axis: display flag** | default `--tb=auto` and `--tb=no`, because they are not the same cost — see below |
| Reported | per-failed-test rendering cost in ms; whole-session overhead as % of the OFF median |

**The headroom this is checked against is a number, not a feeling.** From
`version-control-context` → Measurements and read-api D63, unchanged because
nothing has spent it since: the synthetic-repository 10 ms profile records
11.146 s ON against 10.981 s OFF, so RQ-25's 2% allows 219.6 ms and 164.8 ms is
already spent. **≈55 ms per session remains.** That divides by the failure
count: 5.5 ms per rendering at ten failures, 0.55 ms at a hundred.

**Pre-measurement forecast, recorded so the result can visibly disagree with
it** (D52's precedent, which is the reason that discipline exists):

- A `style="long"` rendering that walks frames and reads source is expected in
  the **1–5 ms** range warm, more cold.
- Therefore the **1% profile (10 failures) is expected to fit** the remaining
  55 ms, the **10% profile to be marginal**, and the **100% profile to exceed
  RQ-25's 2% budget**.
- **`--tb=no` is expected to be the more expensive branch**, which is
  counter-intuitive and is why it is measured separately: under the default
  `--tb=auto`, pytest already rendered the failure for the terminal, so the
  source files are warm in the linecache and the second rendering is cheap.
  Under `--tb=no` nothing was rendered, so this change's rendering is the *only*
  one and pays the cold cost — on exactly the flag combination it exists to
  serve.

**What happens if the forecast holds.** RQ-25's own text requires the number to
be recorded whether or not the 2% budget holds, and it is recorded as measured,
never adjusted to match the forecast. A session in which every test fails is
pathological, and **no failure-count cap is invented here** — if the
measurement says one is needed it arrives then, with a number behind it
instead of a guess. That is ADR-0014's own rule about flags, applied to a cap.
**What the measurement actually found, after this section was written: every
density breaches the budget, not only the pathological one — see
`failure-evidence`'s Measurements paragraph — which is why D72 was revised
from an opt-out to an opt-in absent by default, rather than a failure-count
cap being invented after all.**

### D80 — `run-recording`'s Measurements re-run: what changes and what does not

The paragraph obliges its own re-run for "any change to the result schema or the
batch-insert strategy", and `_INSERT_RESULT` goes from 14 bound columns to 31.

**Corrected in Phase 7 (implemented, then Phase 9 documented it here): the
column counts below, and everything derived from them.** This section
originally said `_INSERT_RESULT` widens 14 → 27 and `_SELECT_RESULTS_FOR_RUN`
16 → 29 — an undercount of 13/13 columns against the real 17-field
`FailureEvidence` (13 fields) + `CapturedOutput` (4 fields) set, which was
never re-counted against the actual dataclasses before this table was
written. It also drove this phase's own ~390-line size forecast for Phase 7;
the real single-commit implementation measured 796, forcing the unplanned
PR7a/PR7b split, and PR7a still landed 32% over the 400-line review budget
because the write-path widening could not be deferred out of it.

| Quantity | Value |
| --- | --- |
| `_INSERT_RESULT` / `_SELECT_RESULTS_FOR_RUN` column count | **31**, **33** (14 → 31, 16 → 33) |
| Transactions per report | **unchanged — one.** `executemany` inside the same `BEGIN IMMEDIATE`…`COMMIT`; nothing splits |
| Body size, 500 results **with no failures** | **unchanged, 252,511 bytes** — stronger than "the new keys are `null`": for a result with no failure evidence the seventeen new keys are absent from the wire entirely, never emitted as `null`, so they cost nothing |
| Body size, 500 results **all failing** | measured **794,291 bytes** — see below |
| Server peak memory, one 500-result finish-write request | re-measured **2,880,085 bytes**, up from 2,021,039 (+42.5%) — see `run-recording/spec.md`'s Measurements paragraph for the justification |

**The all-failing bound formula, as originally stated here
(`252,511 + MAX_FAILURE_TEXT_BYTES` = 776,799 bytes), understates the real
number — measured 794,291, 17,492 bytes (2.25%) over.** The formula was
right that the budget bounds the *charged* bytes, and wrong to treat that as
the whole body: `spend_failure_text_budget` charges only the five budgeted
fields' own JSON-encoded **values**, never their key names and punctuation,
and never the twelve non-budgeted columns present on every failing result
(`failure_type`, `failure_path`, `failure_lineno`, `skip_reason`,
`xfail_reason`, and the seven `_truncated` flags) — each short by
construction (D74's own reason for not charging them), but not zero. The
corrected bound is `252,511 + MAX_FAILURE_TEXT_BYTES + (per-key JSON
overhead across 500 results × 17 fields)`, which this document does not
re-derive symbolically; the measured number stands in its place, and it
still leaves 254,285 bytes (24.2%) of headroom under `MAX_REPORT_BYTES`
(1,048,576), so RQ-3's whole-report cap is not at risk from the undercount.
**The re-measurement records the actual number for both cases and justifies
any material increase, as the paragraph requires** — that discipline is what
caught this, rather than the forecast quietly standing in for it.

`test_finish_report_reaches_storage_in_one_commit` also stays the premise of
RQ-3's Analysis argument, so its row-count assertions must still pass with the
wider insert — that is what would catch a batch-insert strategy accidentally
split by the extra columns.

**Process note, covering all three corrections in this document (D69, D74,
this section).** Each stated an existing artifact's shape — a method
signature, a `json.dumps` default, a column count — confidently and from
memory, never probed against the installed pytest, the shipped
`transport.py`, or `schema.sql`/the real dataclasses. All three landed where
the consequence was hidden: a broad `except`, silent arithmetic, or a size
forecast nobody re-derives before implementing against it. The fix is not
these three edits; it is that `sdd-design` must probe every API signature,
column count and dataclass width it names against the installed code, and
cite what it probed, rather than asserting from what the proposal or a
mental model implied. Probing costs minutes at design time; not probing cost
this change an unplanned branch split and an over-budget PR.

### D81 — Exactly one decision earns an ADR

`openspec/config.yaml` → `rules.design` and CLAUDE.md set the filter: more than a
sprint to reverse.

| Decision | Reversal cost | Verdict |
| --- | --- | --- |
| **Store rendered failure text, bounded and unredacted** | Dropping populated columns is, under ADR-0013, a `schema_version` bump — every existing database is **refused rather than migrated**, so recorded history is lost. It also retracts a stated privacy position | **ADR-0016**, Nygard, `Proposed` in the PR |
| Worker-side collector (D68) | One module and one branch in `pytest_configure` | design note |
| `repr(excinfo.value)` as `failure_repr` (D69) | One extraction function; the column already exists | design note |
| Dropping phase attribution (D71) | Recovering it needs `result_log`, already a separate future change | design note |
| The opt-in's name and shape, revised from opt-out (D72) | Two option registrations and a pure function | design note |
| `MAX_FAILURE_TEXT_BYTES` and the mirror (D73) | One constant and one pinning test | design note |
| First-come budget spending (D74) | One loop | design note |
| Flag disjunction (D75) | One `or` and one optional wire field | design note |
| 200-character display width (D76) | One constant, two adapters | design note |
| Single-result path shape (D78) | One route, one document entry; D54 owns the reasoning | design note |

No existing ADR is restated. ADR-0003 (clean architecture, `Protocol` ports),
ADR-0005 (complete schema at first use), ADR-0006 (stdlib `sqlite3`), ADR-0008
(the web interface owns output encoding), ADR-0009 (the server owns every
write), ADR-0011 (FastAPI on uvicorn), ADR-0013 (schema-version refusal),
ADR-0014 (the plugin's execution boundary and its no-flag argument) and ADR-0015
(the named read surface) are referenced and relied on, never re-argued.

---

## Data Flow

```
  WORKER (or the single process when xdist is absent)
  ────────────────────────────────────────────────────────────────────────
  pytest_runtest_makereport(item, call)          EvidenceCollector    (D68)
        │  outcome = yield          ← never inside try (pluggy contract)
        │  branch on report/excinfo, never on .reprcrash             (D70)
        │    excinfo None → nothing | wasxfail → xfail_reason
        │    skipped      → skip_reason (longrepr[2])
        │    otherwise    → typename, exconly(), repr(value),
        │                   str(item._repr_failure_py(excinfo,"long")) (D69)
        │                   reprcrash.path / .lineno
        ▼
  report.vantage_evidence : dict[str, str|int|bool|None]
        │  survives xdist via __dict__ copy + TestReport(**extra)    (D68)
        ▼
  CONTROLLER
  ────────────────────────────────────────────────────────────────────────
  pytest_runtest_logreport → accumulate(_results, report)   [unchanged]
        ▼
  pytest_sessionfinish → assemble_results(...)
        │  select the evidence phase from the DERIVED outcome        (D69)
        │  captured_* = "".join(phases) | None if -s                 (D71)
        ▼
  spend_failure_text_budget(entries)                                 (D74)
        │  execution order → field priority → encoded cost
        │  over budget ⇒ value = null, <field>_truncated = true
        │  MAX_FAILURE_TEXT_BYTES = 512 KiB = MAX_REPORT_BYTES // 2  (D73)
        ▼
  transport.send  ── POST /api/v1/runs, one request, ≤ 1 MiB by construction
        ▼
  SERVER
  ────────────────────────────────────────────────────────────────────────
  _read_bounded_body → 1 MiB cap  [unchanged; now not reached in practice]
        ▼
  ResultReport: new optional fields, every one defaulting to absent
        ▼
  _to_result:  value, server_flag = truncate(value)                  [D49]
               stored_flag = reported_flag OR server_flag            (D75)
        ▼
  record_session → one BEGIN IMMEDIATE … COMMIT   [unchanged]        (D80)

  READ
  ────────────────────────────────────────────────────────────────────────
  GET /runs/{id}/results        → Page[ResultListEntry]
        │  SQL selects NO traceback / repr / captured output         (D76)
        │  substr(failure_message,1,200) + disjunction CASE          (D76)
        ▼
  FailureProjection | None  ── structurally cannot carry the heavy fields

  GET /runs/{id}/result?node_id=…  → Result, every field             (D78)
        │  identity is a query VALUE, D54 inherited
        ▼
  ResultDetailResponse ── field by field, never from_attributes
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `packages/pytest-vantage/src/pytest_vantage/evidence.py` | **Create** | `EvidenceCollector`, the hookwrapper, the branch table, field extraction (D68–D71) |
| `packages/pytest-vantage/src/pytest_vantage/budget.py` | **Create** | `_REPORT_BYTES_CAP`, `MAX_FAILURE_TEXT_BYTES`, `spend_failure_text_budget` (D73, D74) |
| `packages/pytest-vantage/src/pytest_vantage/plugin.py` | Modify | two options; the worker registration branch before the existing early return (D68, D72) |
| `packages/pytest-vantage/src/pytest_vantage/config.py` | Modify | `resolve_failure_text_capture` (D72) |
| `packages/pytest-vantage/src/pytest_vantage/capture.py` | Modify | evidence-phase selection keyed off the derived outcome; the new `results[]` keys (D69, D71) |
| `packages/pytest-vantage/src/pytest_vantage/recorder.py` | Modify | spend the budget between `assemble_results` and `send` (D74) |
| `packages/vantage/src/vantage/core/domain/result.py` | Modify | `FailureEvidence`, `CapturedOutput`, two fields on `Result` (D77) |
| `packages/vantage/src/vantage/core/domain/projection.py` | Modify | `FailureProjection`, `project_failure`, `LIST_FAILURE_MESSAGE_CHARS` (D76, D77) |
| `packages/vantage/src/vantage/core/ports/storage.py` | Modify | `get_result`; `ResultListEntry`; `list_results` return type (D77, D78) |
| `packages/vantage/src/vantage/storage/sqlite_store.py` | Modify | 31-column insert, 33-column select, `substr` projection, `get_result` |
| `packages/vantage/src/vantage/storage/memory.py` | Modify | the same, second mechanism |
| `packages/vantage/src/vantage/storage/schema.sql` | **Unchanged** | every column and index 5 already exist (RQ-29, ADR-0005) |
| `packages/vantage/src/vantage/service/schemas.py` | Modify | `ResultReport` optional fields + flags; `ResultListItemResponse`; `ResultDetailResponse` (D75, D76, D78) |
| `packages/vantage/src/vantage/service/routes/runs.py` | Modify | `_to_result` maps the evidence; the flag disjunction (D75) |
| `packages/vantage/src/vantage/service/routes/read.py` | Modify | the single-result route; `_result_item` reads a projection (D76, D78) |
| `packages/vantage/src/vantage/service/errors.py` | Modify | `UnknownResultError` (D78) |
| `packages/vantage/src/vantage/service/openapi/v1.yaml` | Modify | the new `read`-tagged operation, hand-written, never derived |
| `packages/vantage/tests/vantage_port_contract.py` | Modify | evidence round-trip, the projection, `get_result`, empty≠absent, both adapters |
| `packages/vantage/tests/test_routes_read.py` | Modify | the task-7.6 Inspection comment **deleted**; the sentinel exclusion test (D76) |
| `packages/vantage/tests/test_read_only_surface.py` | Modify | binding-table entry for the new `read` path (D78) |
| `packages/pytest-vantage/tests/test_report_budget.py` | **Create** | the mirrored-constant pinning test (D73) |
| `scripts/measure_failure_capture_overhead.py` | **Create** | the failure-density benchmark (D79) |
| `docs/adr/0016-store-pytest-s-rendered-failure-text-bounded-and-unredacted.md` | **Create** | the one decision past the filter (D81) |
| `openspec/specs/history-read-api/spec.md` | Modify | exclusion promoted to Test; *Single result detail* added |
| `docs/open-questions.md`, `docs/schema-manifest.md`, `README.md` | Modify | OQ-11; columns now populated; the disclosure and the opt-in |

## Interfaces / Contracts

The plugin's wire additions on each `results[]` entry — every one optional on the
server side, every one defaulting to absent (D75):

```json
{
  "node_id": "tests/test_orders.py::test_total_includes_tax",
  "outcome": "failed",
  "failure_type": "AssertionError",
  "failure_message": "AssertionError: assert 1200 == 1320",
  "failure_message_truncated": false,
  "failure_path": "tests/helpers/pricing.py",
  "failure_lineno": 47,
  "failure_repr": "AssertionError('assert 1200 == 1320')",
  "failure_repr_truncated": false,
  "traceback": "tests/test_orders.py:19: in test_total_includes_tax\n    ...",
  "traceback_truncated": false,
  "skip_reason": null,
  "skip_reason_truncated": false,
  "xfail_reason": null,
  "xfail_reason_truncated": false,
  "captured_stdout": "",
  "captured_stdout_truncated": false,
  "captured_stderr": null,
  "captured_stderr_truncated": false
}
```

`"captured_stdout": ""` means captured and empty; `"captured_stderr": null` with
its flag `false` means never captured. `"traceback": null` with
`"traceback_truncated": true` means evidence existed and the budget dropped it.

```
GET /api/v1/runs/{run_id}/result?node_id=tests%2Ftest_orders.py%3A%3Atest_total_includes_tax
→ 200, every field above, unbounded by any display width
→ 404 UnknownRunError      (no such run)
→ 404 UnknownResultError   (run exists, no result with that identity)
→ 422 InvalidIdentityError (node_id absent or over MAX_IDENTITY_CHARS)
```

```
GET /api/v1/runs/{run_id}/results
→ items[].failure = {failure_type, failure_message (≤200 chars),
                     failure_message_truncated, failure_path, failure_lineno,
                     skip_reason, xfail_reason} | null
   — no traceback, no failure_repr, no captured output, at any depth
```

## Testing Strategy

Strict TDD, RED first. **New tests carry no `req` marker** — no numeric
identifiers are minted — and each names its capability and scenario in its
docstring, which is what `grep` has to find.

| Layer | What | Approach |
| --- | --- | --- |
| Unit (plugin) | The D70 branch table: skip tuple `longrepr`, bare `@xfail`, `wasxfail == ""`, an exception whose `__repr__` raises | `pytest.TestReport`-shaped fakes; no subprocess |
| Unit (plugin) | `resolve_failure_text_capture` monotonicity over all eight combinations | Property assertion, not a case list (D72) |
| Unit (plugin) | Budget: exhaustion mid-session, drop-whole, flags set, priority order, a within-budget session setting no flag | `spend_failure_text_budget` on hand-built entries (D74) |
| Unit (plugin) | The mirrored cap equals `MAX_REPORT_BYTES` | Cross-package test-only import (D73) |
| Integration (plugin) | Three frames named under `--tb=no` and `--tb=line`; the location is the helper's raising line | `pytester`, parametrised over `--tb` styles |
| Integration (plugin) | `-s` ⇒ captured output absent; silent test ⇒ `""` | `pytester`, two invocations |
| Integration (plugin) | RQ-2 differential: ini value present vs absent, no flag either way | The established differential form (D72) |
| Unit (core) | `project_failure`: bounding, the disjunction flag, null passthrough, all-null → `None` | Pure function, no fixtures |
| Contract (both adapters) | Evidence round-trip incl. `""`-vs-`None`; `get_result` hit and miss; the bounded message and its flag; 31-column insert | `vantage_port_contract.py`, inherited by both stores |
| Integration (service) | Version skew both directions; the flag disjunction (client `true` + server `false` stores `true`); over-cap report stores nothing | ASGI in-process |
| Integration (service) | The sentinel traceback is absent from the list body and present in the detail body | Substring assertion on raw bytes (D76) |
| Test (storage) | An existing pre-change database opens unrefused and reads back its rows | Fixture DB written by the previous schema path; ADR-0013 proven not assumed |
| Test (document) | Drift both directions; the new path answers 2xx; its read-only binding exists | `test_interface_document.py`, `test_read_only_surface.py` (D78) |
| Demonstration | Failure text survives xdist serialization | The existing 3.10–3.13 × xdist matrix |
| Analysis | Recording overhead per failure-density and `--tb` cell; body size and peak memory | `scripts/measure_failure_capture_overhead.py` + the existing `tracemalloc` test, transcribed (D79, D80) |
| Inspection | The unredacted-storage disclosure in the capability spec and the README | A documentation property; no assertion for "we told the truth" |

**The test that proves D68 rather than assuming it** is the xdist one: it runs a
failing test under `-n 2` and asserts the stored traceback names all three
frames. If the `__dict__` round-trip reasoning were wrong, that test — not a
user's CI — is where it surfaces.

## Threat Matrix

`references/threat-matrix.md` rows are **all N/A**: this change spawns no
process, composes no command, automates no VCS or PR action, and classifies no
file by content.

| Boundary | Applicability |
| --- | --- |
| Documentation-like paths | **N/A** — nothing is classified by content |
| Git repository selection | **N/A** — no process is spawned; ADR-0014's authority is neither used nor widened |
| Commit state / Push state / PR commands | **N/A** — nothing inspects a tree, pushes, or composes a PR command |
| Executable-file classification | **N/A** — no file is executed; `repr_failure` reads source that pytest already read |

Boundaries this change *does* add, recorded as notes rather than invented rows:

- **Arbitrary text from the test process reaching durable storage, verbatim.**
  Response: bounded (64 KiB per field, 512 KiB per report), **not** sanitised —
  ADR-0016 decides that and says so. Output encoding on the way back out is
  ADR-0008's, unchanged: the text is stored data, never routed through
  `errors.py::safe_segment`, which allow-lists *client-chosen* text for echoing
  in a rejection body. RED tests: a traceback containing `</script>`, a null
  byte, and a lone surrogate survive a store/read round-trip byte-identical.
- **A client-chosen identity reaching SQL on a new route.** Response: a bound
  parameter in every query, never interpolated, and length-bounded at
  `MAX_IDENTITY_CHARS` — D54's discipline inherited without change. RED tests: a
  quoting-shaped `node_id`; an over-long one; one echoed in a `422` body.
- **A client-supplied boolean influencing a stored flag** (D75). Response: the
  server may only `or` it, never assign it, so a client can over-report
  incompleteness but never under-report it. RED test: a client sending
  `traceback_truncated: true` with a whole traceback stores `true`; a client
  sending `false` with an oversized traceback still stores `true`.
- **Absolute paths in stored evidence.** `failure_path` for a frame inside
  `site-packages` is an absolute path that can carry a username. Not redacted,
  consistent with ADR-0016; named in the disclosure rather than discovered.
- **A read path that writes.** Response: the new `read`-tagged operation joins
  the digest-pair harness automatically (D53, D65). RED test: the existing
  falsifier — tagging a writing endpoint `read` must make the harness fail.

## Migration / Rollout

**No migration.** `schema.sql` is byte-unchanged, `meta.schema_version` stays
`'2'`, ADR-0013's refusal gate is not engaged, and an existing database opens
with `NULL` in the new columns. That is proven by a test, not assumed.

The proposal forecast ~3,110 lines across nine slices with two over budget and
one exactly on it. **This design keeps nine slices and none exceeds 400**, at a
slightly higher total — D68's worker registration and D78's projection type are
work the proposal did not price.

| # | Slice | Est. | Depends on |
| --- | --- | --- | --- |
| 1 | Domain types: `FailureEvidence`, `CapturedOutput`, `FailureProjection`, `project_failure` + unit tests (D77, D76) | ~300 | — |
| 2 | `EvidenceCollector` registration on controller and workers; the opt-in and its monotonicity test; no rendering yet (D68, D72) | ~340 | — |
| 3 | Rendering and field extraction; the D70 branch table; evidence-phase selection (D69, D70) | ~390 | 2 |
| 4 | Captured output, empty≠absent, phase concatenation (D71) | ~290 | 3 |
| 5 | The budget: constants, pinning test, one-pass spend, drop semantics (D73, D74) | ~370 | 4 |
| 6 | Ingestion: `ResultReport` optional fields, the flag disjunction, `_to_result` (D75) | ~360 | 1 |
| 7 | Storage: both adapters' insert/select/projection, `get_result`, port contract, the unrefused-database test (D76–D78) | ~390 | 1, 6 |
| 8 | Read surface: single-result route, list projection, `v1.yaml`, exclusion promoted to Test, the read-only binding (D76, D78) | ~390 | 7 |
| 9 | Measurements re-run, the overhead script and its committed numbers, docs, ADR-0016, OQ-11 (D79–D81) | ~330 | 8 |

~3,160 lines across nine slices. `chain_strategy: feature-branch-chain`;
rollback in reverse chain order.

```
Decision needed before apply: Yes
Chained PRs recommended: Yes
400-line budget risk: High
```

Slice 5 must land before slice 6: an ingestion test asserting the dropped-field
shape needs a plugin that can produce one. Slice 7 before slice 8: the
single-result route cannot be written before `get_result` exists. Those are
dependencies, not preferences.

**Rollback.** Per slice, a branch revert; no schema statement was issued, so a
database written by the reverted build still opens and the columns simply stop
being populated. Already-recorded failure text survives a revert as unread data;
erasing it is a `DELETE`/`VACUUM` and no tool ships for it here — the same gap
Q4 leaves open deliberately. ADR-0016 is superseded, never edited, and OQ-11
reopens.

## Open Questions

None blocks `sdd-tasks`.

- [ ] The per-report budget bounds failure text, not the report (D73). A session
      above ~1,000 results breaches 1 MiB with zero failure text, before this
      change and after it. Chunked or streamed ingestion is the fix; nothing
      here needs it.
- [ ] Output consumed by a `capsys`/`capfd` fixture is invisible to any
      observer and reads back as `""` (D71). No public pytest surface exposes
      it.
- [ ] Phase attribution is dropped (D71). Recovering it needs `result_log`, a
      structured table already out of scope, not a marker in a text column.
- [ ] If D79's measurement puts the 100%-failing profile past RQ-25's 2%, the
      candidate remedy is a per-session failure-count cap. Not invented now; it
      arrives with a number behind it or not at all (ADR-0014's rule about
      flags).
- [ ] Cumulative database growth stays unbounded (proposal Q4): up to ~320 KiB
      per failed test, indefinitely, with no retention or vacuum tool. Named as
      a separate future change.
