"""The report's size: the failure-text budget, and the split of a large
session's results over several reports.

The server rejects a report body larger than
`vantage.service.errors.MAX_REPORT_BYTES` outright, losing the whole
session. `_REPORT_BYTES_CAP` mirrors that value because the plugin must not
import the server package; `test_report_budget.py` pins the two together,
since a mirror that drifts high produces rejections of whole sessions.

Every result costs a few hundred bytes before any failure text, so a large
enough suite exceeds the cap on results alone. `split_results` divides them
into slices that each fit one report, and `recorder.py` sends one report
per slice for the same run.

`MAX_FAILURE_TEXT_BYTES` is half the cap, a total across the session.
`spend_failure_text_budget` runs between `assemble_results(...)` and
`send(...)` in `recorder.py::pytest_sessionfinish`, charging each budgeted
field's encoded JSON cost against a shared remainder, failures first:

1. the failure evidence of failed and errored results, then their captured
   output;
2. then the same fields of every other result -- an xfail's traceback, a
   passing test's output -- which describe nothing that went wrong.

Within each group every result's `failure_message` is charged before any
result's `failure_repr`, and so on down `_BUDGETED_FIELDS`.

Each field is first cut the way the server cuts it, to `_FIELD_BYTES_CAP`
bytes of UTF-8 at a character boundary, with its `<field>_truncated` flag
set: the server would discard the rest on arrival, so it is never sent or
charged. A field that still does not fit the remaining budget is dropped
whole and flagged. `failure_type`, `failure_path`, `failure_lineno`,
`skip_reason` and `xfail_reason` are never charged or dropped: they are
short, and failures are grouped by source line.
"""

from __future__ import annotations

import json

_REPORT_BYTES_CAP = 1024 * 1024  # mirrors vantage.service.errors.MAX_REPORT_BYTES
MAX_FAILURE_TEXT_BYTES = _REPORT_BYTES_CAP // 2
_FIELD_BYTES_CAP = 64 * 1024  # mirrors vantage.service.truncation.MAX_TEXT_FIELD_BYTES

# Smallest and most informative first: "what broke?" is answered for as many
# results as possible before "how did it get there?" is answered for any.
_BUDGETED_FIELDS = (
    "failure_message",
    "failure_repr",
    "traceback",
    "captured_stdout",
    "captured_stderr",
)

# The outcomes whose failure text explains something that went wrong.
_FAILING_OUTCOMES = frozenset({"failed", "error"})


def _encoded_cost(value: object) -> int:
    """The JSON-encoded byte cost of `value` alone -- what the wire will
    carry for this field, escapes and quotes included. A traceback is
    newline- and quote-heavy, so a raw `len(str)` understates it.

    No `ensure_ascii` argument, deliberately: `transport.send` calls
    `json.dumps(report)` with the default, so the wire spends a six-byte
    `\\uXXXX` escape per code unit where UTF-8 would spend two to four.
    Measuring with `ensure_ascii=False` would understate non-English text
    (1.3x to 1.8x) and let a report pass the budget yet exceed the server's
    cap.
    """
    return len(json.dumps(value).encode("utf-8"))


def _cut_to_field_cap(value: str) -> tuple[str, bool]:
    """`value` cut to at most `_FIELD_BYTES_CAP` bytes of UTF-8, the way
    the server's own bound cuts it: a character straddling the cut is
    dropped whole, never sent mangled. Returns whether a cut happened.

    `surrogatepass`, because text decoded with `surrogateescape` -- a file
    name that is not valid UTF-8, quoted in a message -- carries lone
    surrogates that strict UTF-8 refuses to encode. A cut drops them.
    """
    encoded = value.encode("utf-8", errors="surrogatepass")
    if len(encoded) <= _FIELD_BYTES_CAP:
        return value, False
    return encoded[:_FIELD_BYTES_CAP].decode("utf-8", errors="ignore"), True


def spend_failure_text_budget(entries: list[dict[str, object]]) -> None:
    """Spend `MAX_FAILURE_TEXT_BYTES` across `entries` in place, failures
    first and field by field (see the module docstring for the order).

    A string field longer than `_FIELD_BYTES_CAP` is cut and flagged
    `<field>_truncated`; its cost after the cut is what is charged. If that
    cost fits the remaining budget, the field is kept. Otherwise it is
    dropped whole -- set to `None` -- and flagged, giving the wire shape
    `{"traceback": null, "traceback_truncated": true}`. A field absent
    from an entry (e.g. a skipped result, which carries no failure fields)
    is neither charged nor dropped.

    Reads `MAX_FAILURE_TEXT_BYTES` from this module's own namespace at call
    time, not a bound default, so a test can `monkeypatch` it directly.
    """
    remaining = MAX_FAILURE_TEXT_BYTES
    failing = [entry for entry in entries if entry.get("outcome") in _FAILING_OUTCOMES]
    others = [entry for entry in entries if entry.get("outcome") not in _FAILING_OUTCOMES]
    for group in (failing, others):
        for field in _BUDGETED_FIELDS:
            for entry in group:
                if field not in entry:
                    continue
                value = entry[field]
                if isinstance(value, str):
                    value, cut = _cut_to_field_cap(value)
                    if cut:
                        entry[field] = value
                        entry[f"{field}_truncated"] = True
                cost = _encoded_cost(value)
                if cost <= remaining:
                    remaining -= cost
                else:
                    entry[field] = None
                    entry[f"{field}_truncated"] = True


def split_results(
    results: list[dict[str, object]], *, envelope_bytes: int
) -> tuple[list[list[dict[str, object]]], int]:
    """Split `results`, in order, into consecutive slices that each fit one
    report under `_REPORT_BYTES_CAP`, next to report sections that encode
    to `envelope_bytes` with an empty `results` list.

    `json.dumps` joins list items with `", "`, so a report carrying n
    results costs `envelope_bytes`, plus each result's own cost, plus two
    bytes per separator; charging a separator to every result overstates
    that by two bytes at most.

    Returns the slices -- at least one, possibly empty, so a finish report
    always goes out -- and how many results were left out because they
    cannot fit even in a report of their own.

    Reads `_REPORT_BYTES_CAP` at call time, so a test can `monkeypatch` it.
    """
    room = _REPORT_BYTES_CAP - envelope_bytes
    slices: list[list[dict[str, object]]] = [[]]
    used = 0
    left_out = 0
    for entry in results:
        cost = _encoded_cost(entry) + len(", ")
        if cost > room:
            left_out += 1
            continue
        if used + cost > room:
            slices.append([])
            used = 0
        slices[-1].append(entry)
        used += cost
    return slices, left_out


__all__ = ["MAX_FAILURE_TEXT_BYTES", "spend_failure_text_budget", "split_results"]
