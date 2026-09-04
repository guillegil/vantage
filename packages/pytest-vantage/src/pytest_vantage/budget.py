"""The per-report failure-text budget (design.md D73, D74).

`_REPORT_BYTES_CAP` mirrors `vantage.service.errors.MAX_REPORT_BYTES`
across the RQ-24 boundary this plugin cannot import across directly --
`pytest_vantage` imports nothing beyond pytest and the standard library, and
`vantage.service` is exactly the kind of module that constraint forbids.
`MAX_FAILURE_TEXT_BYTES` is half of it: the derivation, from numbers this
repository already measured (`run-recording` -> Measurements, ~505 bytes of
non-failure body per result), is that a report respecting this budget cannot
breach the server's whole-report cap for any session up to roughly 1,000
results -- comfortably above the 500-result session that Measurements
paragraph exercises.

A divergence between the two constants IS a correctness bug -- a mirror that
drifts high produces 413s that reject whole sessions -- so
`test_report_budget.py::test_the_mirrored_cap_matches_the_server` pins both
against the server's real value via a test-only cross-package import; that
import lives in the test file, never here.

**This ~1,000-result headroom derivation predates `run-metadata-capture`
(design.md D94).** That change's `metadata` section rides the finish-write
too (`recorder.py::_metadata_section`), spending up to
`pytest_vantage.metadata.MAX_METADATA_SECTION_BYTES` (32,768 bytes) of the
same `MAX_REPORT_BYTES` cap this module's headroom is computed against.
The derivation above is otherwise unchanged -- `(524,288 - 32,768) / 505 ≈
973` results -- a 6% reduction from ~1,038, still comfortably above the
500-result session Measurements exercises. A derived invariant another
module's arithmetic depends on is not allowed to drift silently just
because the module that changed it lives elsewhere.

`spend_failure_text_budget` is called between `assemble_results(...)` and
`send(...)` in `recorder.py::pytest_sessionfinish` -- one pass, in execution
order (the order the `results[]` entries already carry), charging each
budgeted field's encoded JSON cost against a shared remainder. A field that
does not fit is dropped WHOLE and its `<field>_truncated` flag is set --
never cut, because cutting at a second, different threshold is already what
the 64 KiB per-field bound (server-side, D75) means, and dropping needs none
of that bound's UTF-8-boundary-aware slicing logic, which lives on the far
side of the RQ-24 boundary this module cannot cross. `failure_type`,
`failure_path`, `failure_lineno`, `skip_reason` and `xfail_reason` are never
charged and never dropped -- short by nature, and the columns index 5 groups
on; disabling that grouping to save bytes on a field that costs almost
nothing would be a bad trade.

Standard library only (RQ-24) -- `json` and nothing else.
"""

from __future__ import annotations

import json

_REPORT_BYTES_CAP = 1024 * 1024  # mirrors vantage.service.errors.MAX_REPORT_BYTES
MAX_FAILURE_TEXT_BYTES = _REPORT_BYTES_CAP // 2

# design.md D74: smallest-and-most-informative first -- "what broke?" is
# answered for as many results as possible before "how did it get there?"
# is answered for any. Fixed order, never re-derived per entry.
_BUDGETED_FIELDS = (
    "failure_message",
    "failure_repr",
    "traceback",
    "captured_stdout",
    "captured_stderr",
)


def _encoded_cost(value: object) -> int:
    """The JSON-encoded byte cost of `value` alone -- what the wire will
    carry for this field, escapes and quotes included (design.md D74). A
    traceback is newline- and quote-heavy, so a raw `len(str)` understates
    it by a third or more; this measures the actual encoded bytes instead.

    **No `ensure_ascii` argument, deliberately**: `transport.send` calls
    `json.dumps(report)` bare, so the wire spends a six-byte escape per
    unit -- twelve for an astral pair -- wherever UTF-8 would spend two to
    four. Charging `ensure_ascii=False` here, which design.md D74's own
    pseudocode specifies and which is wrong, understates every codepoint
    above 0x7F: measured 1.30x for Spanish accents, 1.84x for Japanese,
    1.65x for emoji. A suite whose assertion messages are not English
    would then pass the budget and still breach `MAX_REPORT_BYTES`, losing
    the whole session, run included -- the exact failure this budget
    exists to prevent. The wire is what decides.
    """
    return len(json.dumps(value).encode("utf-8"))


def spend_failure_text_budget(entries: list[dict[str, object]]) -> None:
    """Spend `MAX_FAILURE_TEXT_BYTES` across `entries` in place, in the
    execution order they already carry (design.md D74).

    For each entry, in `_BUDGETED_FIELDS` priority order: if the field's
    encoded cost fits the remaining budget, it is charged and left
    untouched. Otherwise the field is dropped whole -- set to `None` -- and
    `<field>_truncated` is set to `True` on the same entry, the exact wire
    shape `{"traceback": null, "traceback_truncated": true}` the
    *A field dropped for budget is flagged, not missing* scenario names. A
    field absent from a given entry (e.g. a skipped result, which carries no
    failure fields at all) is neither charged nor dropped.

    Reads `MAX_FAILURE_TEXT_BYTES` from this module's own namespace at call
    time, not a bound default, so a test can `monkeypatch` it directly.
    """
    remaining = MAX_FAILURE_TEXT_BYTES
    for entry in entries:
        for field in _BUDGETED_FIELDS:
            if field not in entry:
                continue
            cost = _encoded_cost(entry[field])
            if cost <= remaining:
                remaining -= cost
            else:
                entry[field] = None
                entry[f"{field}_truncated"] = True


__all__ = ["MAX_FAILURE_TEXT_BYTES", "spend_failure_text_budget"]
