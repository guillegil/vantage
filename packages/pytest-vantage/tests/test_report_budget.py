"""The per-report failure-text budget (design.md D73, D74): the constant
mirrored from the server, pinned by a test so a drift is a build failure
rather than a silent 413 in production, and `spend_failure_text_budget`'s
one-pass, execution-order, field-priority spending with drop-whole
semantics.

Standard library and `pytest` only in production (`budget.py` itself,
RQ-24) -- this test file's cross-package import of `vantage.service.errors`
is test-only, the same established pattern `vantage_test_server.py` and
`test_run_report.py` already use to reach `vantage.core`/`vantage.service`
from this distribution's own tests.
"""

from __future__ import annotations

import json

import pytest
import pytest_vantage.budget as budget_module
from pytest_vantage.budget import (
    _REPORT_BYTES_CAP,
    MAX_FAILURE_TEXT_BYTES,
    spend_failure_text_budget,
)
from vantage.service.errors import MAX_REPORT_BYTES


def test_the_mirrored_cap_matches_the_server() -> None:
    """design.md D73: `_REPORT_BYTES_CAP` mirrors the server's own
    `MAX_REPORT_BYTES` across the RQ-24 boundary this plugin cannot import
    across directly -- a divergence here IS a correctness bug (a mirror that
    drifts high produces 413s that reject whole sessions), so the mirror is
    pinned against the server's real value rather than trusted to stay in
    sync by convention alone.
    """
    assert _REPORT_BYTES_CAP == MAX_REPORT_BYTES
    assert MAX_FAILURE_TEXT_BYTES * 2 == MAX_REPORT_BYTES


# --- spend_failure_text_budget (design.md D74) -------------------------------


def test_spend_budget_charges_encoded_json_bytes_not_raw_len(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """design.md D74: the cost charged against the budget is
    `len(json.dumps(value, ensure_ascii=False).encode("utf-8"))` -- the
    encoded wire representation, escapes and quotes included -- never
    `len(value)`. A quote/newline-heavy value makes the two numbers
    genuinely differ (raw length 5, encoded length 9): budgeting to exactly
    one byte short of the ENCODED cost still drops the field; budgeting to
    the encoded cost keeps it whole.
    """
    value = 'a"b\nc'
    encoded_cost = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    assert encoded_cost == 9  # pins the arithmetic itself, not just implies it
    assert encoded_cost != len(value)  # the raw length (5) is a different number

    monkeypatch.setattr(budget_module, "MAX_FAILURE_TEXT_BYTES", encoded_cost - 1)
    short_of_budget: list[dict[str, object]] = [{"traceback": value}]
    spend_failure_text_budget(short_of_budget)
    assert short_of_budget[0]["traceback"] is None
    assert short_of_budget[0]["traceback_truncated"] is True

    monkeypatch.setattr(budget_module, "MAX_FAILURE_TEXT_BYTES", encoded_cost)
    exact_budget: list[dict[str, object]] = [{"traceback": value}]
    spend_failure_text_budget(exact_budget)
    assert exact_budget[0]["traceback"] == value
    assert "traceback_truncated" not in exact_budget[0]


def test_spend_budget_is_execution_order_first_come(monkeypatch: pytest.MonkeyPatch) -> None:
    """design.md D74: allocation is first-come in execution order -- the
    order `entries` already carries. Three identical over-budget results,
    with room for exactly one whole one: the FIRST stays whole, the second
    and third -- despite being identical in size -- drop, because they come
    later, never because of any property of their own content.
    """
    big = "T" * 100
    cost = len(json.dumps(big, ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(budget_module, "MAX_FAILURE_TEXT_BYTES", cost)  # room for exactly one
    entries: list[dict[str, object]] = [{"traceback": big}, {"traceback": big}, {"traceback": big}]

    spend_failure_text_budget(entries)

    assert entries[0]["traceback"] == big
    assert "traceback_truncated" not in entries[0]
    assert entries[1]["traceback"] is None
    assert entries[1]["traceback_truncated"] is True
    assert entries[2]["traceback"] is None
    assert entries[2]["traceback_truncated"] is True


def test_spend_budget_field_priority_within_a_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """design.md D74: field priority within one result is
    `(failure_message, failure_repr, traceback, captured_stdout,
    captured_stderr)` -- smallest-and-most-informative first. A budget sized
    to fit exactly `failure_message` and nothing more leaves `traceback`
    dropped in the SAME result, proving the order fields are charged in,
    not merely that some field somewhere gets dropped.
    """
    message = "M" * 50
    traceback_text = "T" * 50
    message_cost = len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(budget_module, "MAX_FAILURE_TEXT_BYTES", message_cost)
    entries: list[dict[str, object]] = [{"failure_message": message, "traceback": traceback_text}]

    spend_failure_text_budget(entries)

    assert entries[0]["failure_message"] == message
    assert "failure_message_truncated" not in entries[0]
    assert entries[0]["traceback"] is None
    assert entries[0]["traceback_truncated"] is True


def test_short_fields_are_never_charged_or_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """design.md D74: `failure_type`, `failure_path`, `failure_lineno`,
    `skip_reason` and `xfail_reason` are outside the charged set entirely --
    short by nature, and the columns index 5 groups on. A budget of zero,
    fully exhausted before this entry is even reached, leaves every one of
    them untouched.
    """
    monkeypatch.setattr(budget_module, "MAX_FAILURE_TEXT_BYTES", 0)
    entries: list[dict[str, object]] = [
        {
            "failure_type": "AssertionError",
            "failure_path": "tests/test_thing.py",
            "failure_lineno": 42,
            "skip_reason": "not ready",
            "xfail_reason": "known bug",
        }
    ]

    spend_failure_text_budget(entries)

    assert entries[0]["failure_type"] == "AssertionError"
    assert entries[0]["failure_path"] == "tests/test_thing.py"
    assert entries[0]["failure_lineno"] == 42
    assert entries[0]["skip_reason"] == "not ready"
    assert entries[0]["xfail_reason"] == "known bug"
    assert not any(key.endswith("_truncated") for key in entries[0])


def test_a_dropped_field_is_null_with_its_truncated_flag_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure-evidence -> Per-report failure-text budget -> A field dropped
    for budget is flagged, not missing (design.md D74): the exact wire shape
    is `{"traceback": null, "traceback_truncated": true}` -- the same
    out-of-band flag the 64 KiB per-field bound uses, not a new column.
    """
    monkeypatch.setattr(budget_module, "MAX_FAILURE_TEXT_BYTES", 0)
    entries: list[dict[str, object]] = [{"traceback": "some traceback text"}]

    spend_failure_text_budget(entries)

    assert entries[0] == {"traceback": None, "traceback_truncated": True}
    assert json.loads(json.dumps(entries[0])) == {"traceback": None, "traceback_truncated": True}


def test_a_session_within_budget_sets_no_exhaustion_flags() -> None:
    """failure-evidence -> Per-report failure-text budget -> A session
    within budget carries no exhaustion flags: every field here is tiny,
    well inside the real `MAX_FAILURE_TEXT_BYTES`, so nothing is charged
    away and no `*_truncated` key appears at all -- not even set to
    `False`, matching the "absent means never flagged by the budget pass"
    convention `resolve_failure_text_capture`'s sibling fields already use.
    """
    entries: list[dict[str, object]] = [
        {
            "failure_message": "short",
            "traceback": "also short",
            "captured_stdout": "",
            "captured_stderr": None,
        },
        {"skip_reason": "skipped"},
    ]

    spend_failure_text_budget(entries)

    assert entries[0] == {
        "failure_message": "short",
        "traceback": "also short",
        "captured_stdout": "",
        "captured_stderr": None,
    }
    assert entries[1] == {"skip_reason": "skipped"}


def test_a_field_is_dropped_whole_never_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    """design.md D74: a field that does not fit is dropped WHOLE, never
    sliced to fit the remaining budget -- cutting is already what the 64
    KiB per-field bound means, and a second cutting rule at a different
    threshold would put two meanings behind one boolean. A 2,000-character
    traceback (comfortably under the unrelated 64 KiB bound) against a
    100-byte remainder becomes `None`, not a 100-byte slice of itself.
    """
    big_traceback = "T" * 2000
    monkeypatch.setattr(budget_module, "MAX_FAILURE_TEXT_BYTES", 100)
    entries: list[dict[str, object]] = [{"traceback": big_traceback}]

    spend_failure_text_budget(entries)

    assert entries[0]["traceback"] is None
    assert entries[0]["traceback_truncated"] is True
