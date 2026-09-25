"""The per-report failure-text budget: the cap mirrored from the server,
pinned so a drift fails the build rather than causing 413s in production,
and `spend_failure_text_budget`'s one-pass, execution-order, field-priority
spending with drop-whole semantics.

The import of `vantage.service.errors` is test-only; `budget.py` itself must
not depend on the server package.
"""

from __future__ import annotations

import json

import pytest
import pytest_vantage.budget as budget_module
from pytest_vantage.budget import (
    _REPORT_BYTES_CAP,
    MAX_FAILURE_TEXT_BYTES,
    _encoded_cost,
    spend_failure_text_budget,
)
from vantage.service.errors import MAX_REPORT_BYTES


def test_the_mirrored_cap_matches_the_server() -> None:
    """`_REPORT_BYTES_CAP` mirrors the server's `MAX_REPORT_BYTES`, which the
    plugin cannot import. A mirror that drifts high produces 413s that reject
    whole sessions, so it is pinned against the server's real value.
    """
    assert _REPORT_BYTES_CAP == MAX_REPORT_BYTES
    assert MAX_FAILURE_TEXT_BYTES * 2 == MAX_REPORT_BYTES


# --- spend_failure_text_budget ------------------------------------------------


def test_spend_budget_charges_encoded_json_bytes_not_raw_len(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cost charged against the budget is the JSON-encoded size, escapes
    and quotes included, never `len(value)`. A quote/newline-heavy value
    makes the two differ (raw length 5, encoded length 9): a budget one byte
    short of the encoded cost drops the field; exactly the encoded cost
    keeps it whole.
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
    """Allocation is first-come in the order `entries` already carries. Of
    three identical results with room for exactly one, the first stays whole
    and the other two drop because they come later.
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
    """Within one result, fields are charged in the order `failure_message`,
    `failure_repr`, `traceback`, `captured_stdout`, `captured_stderr`. A
    budget that fits exactly `failure_message` keeps it and drops
    `traceback` in the same result.
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
    """`failure_type`, `failure_path`, `failure_lineno`, `skip_reason` and
    `xfail_reason` are never charged: a budget of zero leaves every one of
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
    """A field dropped for budget is flagged, not missing: the wire shape is
    `{"traceback": null, "traceback_truncated": true}` -- the same flag the
    64 KiB per-field bound uses.
    """
    monkeypatch.setattr(budget_module, "MAX_FAILURE_TEXT_BYTES", 0)
    entries: list[dict[str, object]] = [{"traceback": "some traceback text"}]

    spend_failure_text_budget(entries)

    assert entries[0] == {"traceback": None, "traceback_truncated": True}
    assert json.loads(json.dumps(entries[0])) == {"traceback": None, "traceback_truncated": True}


def test_a_session_within_budget_sets_no_exhaustion_flags() -> None:
    """Every field here is well inside the real `MAX_FAILURE_TEXT_BYTES`, so
    nothing is dropped and no `*_truncated` key appears at all -- not even
    set to `False`.
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
    """A field that does not fit is dropped whole, never sliced to fit the
    remaining budget: a 2,000-character traceback against a 100-byte budget
    becomes `None`, not a 100-byte slice of itself.
    """
    big_traceback = "T" * 2000
    monkeypatch.setattr(budget_module, "MAX_FAILURE_TEXT_BYTES", 100)
    entries: list[dict[str, object]] = [{"traceback": big_traceback}]

    spend_failure_text_budget(entries)

    assert entries[0]["traceback"] is None
    assert entries[0]["traceback_truncated"] is True


def test_the_budget_charges_exactly_what_transport_will_put_on_the_wire() -> None:
    """`_encoded_cost` must encode the way `transport.send` does:
    `json.dumps(report)` with the default `ensure_ascii=True`.

    Measuring with `ensure_ascii=False` understates every code point above
    0x7F, because the wire spends a six-byte `\\uXXXX` escape per code unit
    where UTF-8 spends two to four. A suite with non-English assertion
    messages would then pass the budget and still exceed `MAX_REPORT_BYTES`,
    losing the whole session. Only the non-ASCII case separates the two
    encodings; the ASCII case is a baseline.
    """
    ascii_value = "assertion failed: expected 3, got 4"
    assert _encoded_cost(ascii_value) == len(json.dumps(ascii_value).encode("utf-8"))

    # Synthetic, deliberately spanning the three widening cases: two-byte
    # accents, three-byte CJK, and an astral pair encoded as two escapes.
    non_ascii_value = "aserción fallida: año 2026 -- 期待値 3 -- boom 🔥"
    wire_cost = len(json.dumps(non_ascii_value).encode("utf-8"))
    assert _encoded_cost(non_ascii_value) == wire_cost
    assert wire_cost > len(json.dumps(non_ascii_value, ensure_ascii=False).encode("utf-8"))
