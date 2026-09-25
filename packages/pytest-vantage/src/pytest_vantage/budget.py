"""The per-report failure-text budget.

The server rejects a report body larger than
`vantage.service.errors.MAX_REPORT_BYTES` outright, losing the whole
session. `_REPORT_BYTES_CAP` mirrors that value because the plugin must not
import the server package; `test_report_budget.py` pins the two together,
since a mirror that drifts high produces rejections of whole sessions.

`MAX_FAILURE_TEXT_BYTES` is half the cap; the rest of the report is not
measured. At roughly 520 bytes of non-failure body per result, plus the
metadata section's own 32 KiB bound
(`pytest_vantage.metadata.MAX_METADATA_SECTION_BYTES`), a report that spends
the whole budget fits the cap only up to about 940 results.

`spend_failure_text_budget` runs between `assemble_results(...)` and
`send(...)` in `recorder.py::pytest_sessionfinish`, charging each budgeted
field's encoded JSON cost against a shared remainder in execution order. A
field that does not fit is dropped whole and its `<field>_truncated` flag
set, never cut: cutting is the server's 64 KiB per-field bound, whose
UTF-8-boundary-aware slicing lives in the server package. `failure_type`,
`failure_path`, `failure_lineno`, `skip_reason` and `xfail_reason` are never
charged or dropped: they are short, and failures are grouped by source line.
"""

from __future__ import annotations

import json

_REPORT_BYTES_CAP = 1024 * 1024  # mirrors vantage.service.errors.MAX_REPORT_BYTES
MAX_FAILURE_TEXT_BYTES = _REPORT_BYTES_CAP // 2

# Smallest and most informative first: "what broke?" is answered for as many
# results as possible before "how did it get there?" is answered for any.
_BUDGETED_FIELDS = (
    "failure_message",
    "failure_repr",
    "traceback",
    "captured_stdout",
    "captured_stderr",
)


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


def spend_failure_text_budget(entries: list[dict[str, object]]) -> None:
    """Spend `MAX_FAILURE_TEXT_BYTES` across `entries` in place, in the
    execution order they already carry.

    For each entry, in `_BUDGETED_FIELDS` priority order: if the field's
    encoded cost fits the remaining budget, it is charged and left
    untouched. Otherwise the field is dropped whole -- set to `None` -- and
    `<field>_truncated` is set to `True` on the same entry, giving the wire
    shape `{"traceback": null, "traceback_truncated": true}`. A field absent
    from an entry (e.g. a skipped result, which carries no failure fields)
    is neither charged nor dropped.

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
