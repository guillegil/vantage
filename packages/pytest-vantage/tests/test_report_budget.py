"""The per-report failure-text budget: the caps mirrored from the server,
pinned so a drift fails the build rather than causing 413s in production,
and `spend_failure_text_budget`'s failures-first, field-by-field spending,
with the server's per-field cut applied before anything is charged.

The imports of `vantage.service` are test-only; `budget.py` itself must not
depend on the server package.
"""

from __future__ import annotations

import json

import pytest
import pytest_vantage.budget as budget_module
from pytest_vantage.budget import (
    _FIELD_BYTES_CAP,
    _REPORT_BYTES_CAP,
    MAX_FAILURE_TEXT_BYTES,
    _encoded_cost,
    spend_failure_text_budget,
)
from vantage.service.errors import MAX_REPORT_BYTES
from vantage.service.truncation import MAX_TEXT_FIELD_BYTES


def test_the_mirrored_caps_match_the_server() -> None:
    """`_REPORT_BYTES_CAP` and `_FIELD_BYTES_CAP` mirror the server's
    `MAX_REPORT_BYTES` and `MAX_TEXT_FIELD_BYTES`, which the plugin cannot
    import. A report cap that drifts high produces 413s that reject whole
    sessions, and a field cap that drifts sends or drops text the server
    would treat differently, so both are pinned against the server's values.
    """
    assert _REPORT_BYTES_CAP == MAX_REPORT_BYTES
    assert MAX_FAILURE_TEXT_BYTES * 2 == MAX_REPORT_BYTES
    assert _FIELD_BYTES_CAP == MAX_TEXT_FIELD_BYTES


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
    """Among results of the same kind, allocation is first-come in the
    order `entries` already carries. Of three identical results with room
    for exactly one, the first stays whole and the other two drop because
    they come later.
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


def test_a_passing_tests_output_never_starves_a_later_failure_of_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Captured output is kept for every result that ran, passing ones
    included. It is charged only after every failure's evidence, so a
    chatty passing test earlier in the run cannot use up the budget a later
    failure needs to say what broke.
    """
    chatter = "p" * 1000
    message = "assert 3 == 4"
    monkeypatch.setattr(
        budget_module, "MAX_FAILURE_TEXT_BYTES", _encoded_cost(chatter) + _encoded_cost(message) - 1
    )
    entries: list[dict[str, object]] = [
        {"outcome": "passed", "captured_stdout": chatter},
        {"outcome": "failed", "failure_message": message},
    ]

    spend_failure_text_budget(entries)

    assert entries[1] == {"outcome": "failed", "failure_message": message}
    assert entries[0] == {
        "outcome": "passed",
        "captured_stdout": None,
        "captured_stdout_truncated": True,
    }


def test_every_failure_message_is_charged_before_any_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Across results, not only within one. The budget has room for two
    messages and one traceback; spent result by result, the first failure's
    traceback and output would use it all before the second's message.
    Spent field by field, both failures keep their message, the first keeps
    its traceback, and the rest is dropped.
    """
    message = "M" * 50
    traceback_text = "T" * 80
    output = "S" * 50
    monkeypatch.setattr(
        budget_module,
        "MAX_FAILURE_TEXT_BYTES",
        2 * _encoded_cost(message) + _encoded_cost(traceback_text),
    )
    entries: list[dict[str, object]] = [
        {
            "outcome": "failed",
            "failure_message": message,
            "traceback": traceback_text,
            "captured_stdout": output,
        },
        {"outcome": "error", "failure_message": message, "traceback": traceback_text},
    ]

    spend_failure_text_budget(entries)

    assert entries[0] == {
        "outcome": "failed",
        "failure_message": message,
        "traceback": traceback_text,
        "captured_stdout": None,
        "captured_stdout_truncated": True,
    }
    assert entries[1] == {
        "outcome": "error",
        "failure_message": message,
        "traceback": None,
        "traceback_truncated": True,
    }


def test_a_field_is_cut_to_the_servers_field_bound_before_it_is_charged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server keeps at most `_FIELD_BYTES_CAP` bytes of any field, so
    the rest is never sent and never charged. The cut falls on a character
    boundary: the two-byte character straddling it is dropped whole. Only
    the cut value's cost is charged, which leaves exactly enough of the
    budget for a later field.
    """
    big_stdout = "a" + "é" * 100_000  # 200,001 bytes of UTF-8
    kept = "a" + "é" * 32_767  # 65,535 bytes: the next "é" would straddle the bound
    filler = "x" * 1000
    monkeypatch.setattr(
        budget_module, "MAX_FAILURE_TEXT_BYTES", _encoded_cost(kept) + _encoded_cost(filler)
    )
    entries: list[dict[str, object]] = [
        {"outcome": "failed", "captured_stdout": big_stdout, "captured_stderr": filler}
    ]

    spend_failure_text_budget(entries)

    assert entries[0]["captured_stdout"] == kept
    assert entries[0]["captured_stdout_truncated"] is True
    assert entries[0]["captured_stderr"] == filler
    assert "captured_stderr_truncated" not in entries[0]


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
