"""The report's size: `spend_failure_text_budget`'s failures-first,
field-by-field spending, with the server's per-field cut applied before
anything is charged; and `split_results`, which spreads a large session over
several reports. `test_server_contract.py` pins the caps to the server's.

The end-to-end tests run a real session against a real server
(`vantage_server`) and read back what it stored. The imports of `vantage`
are test-only; `budget.py` itself must not depend on the server package.
"""

from __future__ import annotations

import json

import pytest
import pytest_vantage.budget as budget_module
from pytest_vantage import transport
from pytest_vantage.budget import (
    _FIELD_BYTES_CAP,
    encoded_cost,
    spend_failure_text_budget,
    split_results,
)
from vantage.service.errors import MAX_REPORT_BYTES
from vantage_test_server import VantageTestServer

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
        budget_module, "MAX_FAILURE_TEXT_BYTES", encoded_cost(chatter) + encoded_cost(message) - 1
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
        2 * encoded_cost(message) + encoded_cost(traceback_text),
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
        budget_module, "MAX_FAILURE_TEXT_BYTES", encoded_cost(kept) + encoded_cost(filler)
    )
    entries: list[dict[str, object]] = [
        {"outcome": "failed", "captured_stdout": big_stdout, "captured_stderr": filler}
    ]

    spend_failure_text_budget(entries)

    assert entries[0]["captured_stdout"] == kept
    assert entries[0]["captured_stdout_truncated"] is True
    assert entries[0]["captured_stderr"] == filler
    assert "captured_stderr_truncated" not in entries[0]


def test_text_carrying_a_lone_surrogate_is_measured_without_raising() -> None:
    """A message built from a file name that is not valid UTF-8 carries the
    lone surrogates `surrogateescape` decodes it to, which strict UTF-8
    cannot encode. The budget still measures it, and `json.dumps` escapes it
    on the wire, so it must never raise and cost the whole finish-write.
    """
    message = "missing /data/caf\udce9.csv"
    entries: list[dict[str, object]] = [
        {"outcome": "failed", "failure_message": message},
        {"outcome": "failed", "failure_message": "\udce9" * (_FIELD_BYTES_CAP // 2)},
    ]

    spend_failure_text_budget(entries)

    assert entries[0] == {"outcome": "failed", "failure_message": message}
    assert entries[1]["failure_message_truncated"] is True


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
    """`encoded_cost` must encode the way `transport.send` does:
    `json.dumps(report)` with the default `ensure_ascii=True`.

    Measuring with `ensure_ascii=False` understates every code point above
    0x7F, because the wire spends a six-byte `\\uXXXX` escape per code unit
    where UTF-8 spends two to four. A suite with non-English assertion
    messages would then pass the budget and still exceed `MAX_REPORT_BYTES`,
    losing the whole session. Only the non-ASCII case separates the two
    encodings; the ASCII case is a baseline.
    """
    ascii_value = "assertion failed: expected 3, got 4"
    assert encoded_cost(ascii_value) == len(json.dumps(ascii_value).encode("utf-8"))

    # Synthetic, deliberately spanning the three widening cases: two-byte
    # accents, three-byte CJK, and an astral pair encoded as two escapes.
    non_ascii_value = "aserción fallida: año 2026 -- 期待値 3 -- boom 🔥"
    wire_cost = len(json.dumps(non_ascii_value).encode("utf-8"))
    assert encoded_cost(non_ascii_value) == wire_cost
    assert wire_cost > len(json.dumps(non_ascii_value, ensure_ascii=False).encode("utf-8"))


# --- split_results -----------------------------------------------------------

_ENVELOPE: dict[str, object] = {"run": {"id": "a" * 32, "exit_status": None}}


def _report_bytes(results: list[dict[str, object]]) -> int:
    """What `transport.send` would put on the wire for `results` inside
    `_ENVELOPE`."""
    return len(json.dumps({**_ENVELOPE, "results": results}).encode("utf-8"))


def test_split_results_fills_each_report_up_to_the_cap_keeping_execution_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every slice fits one report, the slices together hold every result
    in its original order, and each slice is full: the next result would
    not have fit in it.
    """
    cap = 2_000
    monkeypatch.setattr(budget_module, "_REPORT_BYTES_CAP", cap)
    results: list[dict[str, object]] = [
        {"node_id": f"test_x.py::test_{i}", "captured_stdout": "o" * (i * 37 % 150)}
        for i in range(60)
    ]

    slices, left_out = split_results(results, envelope_bytes=_report_bytes([]))

    assert left_out == 0
    assert len(slices) > 1
    assert [entry for chunk in slices for entry in chunk] == results
    assert all(_report_bytes(chunk) <= cap for chunk in slices)
    # Up to the two bytes the separator accounting overstates by.
    assert all(
        _report_bytes(chunk + following[:1]) > cap - len(", ")
        for chunk, following in zip(slices, slices[1:])
    )


def test_a_result_too_large_for_any_report_is_left_out_and_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(budget_module, "_REPORT_BYTES_CAP", 1_000)
    first: dict[str, object] = {"node_id": "test_x.py::test_a"}
    oversized: dict[str, object] = {"node_id": "test_x.py::test_b[" + "b" * 2_000 + "]"}
    last: dict[str, object] = {"node_id": "test_x.py::test_c"}

    slices, left_out = split_results([first, oversized, last], envelope_bytes=_report_bytes([]))

    assert slices == [[first, last]]
    assert left_out == 1


def test_a_session_without_results_still_gets_one_report() -> None:
    assert split_results([], envelope_bytes=_report_bytes([])) == ([[]], 0)


# --- End to end: what the server stores ------------------------------------


@pytest.fixture
def wire_sizes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, object], int]]:
    """Every report the session sends, with its size on the wire, recorded
    on its way through the real `transport.send`."""
    sent: list[tuple[dict[str, object], int]] = []

    def _measure_then_send(address: str, report: dict[str, object], *, timeout: float) -> None:
        sent.append((report, len(json.dumps(report).encode("utf-8"))))
        transport.send(address, report, timeout=timeout)

    monkeypatch.setattr("pytest_vantage.recorder.send", _measure_then_send)
    return sent


def test_a_suite_too_large_for_one_report_is_recorded_whole(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    wire_sizes: list[tuple[dict[str, object], int]],
) -> None:
    """A thousand passing tests with long parameter ids report well over
    the server's body cap on results alone, with no failure text at all.
    The results are split over several reports for the same run, each
    within the cap; only the last one finishes the run.
    """
    pytester.makepyfile(
        test_large="import pytest\n\n\n"
        "@pytest.mark.parametrize('n', range(1000), ids=lambda n: f'{n:04d}-' + 'x' * 400)\n"
        "def test_p(n):\n    assert True\n"
    )

    result = pytester.runpytest("--vantage", f"--vantage-server={vantage_server.address}")

    result.assert_outcomes(passed=1000)
    assert "VantageWarning" not in result.stdout.str()
    _start, *finish_parts = wire_sizes
    assert len(finish_parts) > 1
    assert all(size <= MAX_REPORT_BYTES for _report, size in finish_parts)
    assert [report["run"]["exit_status"] for report, _size in finish_parts] == [  # type: ignore[index]
        *[None] * (len(finish_parts) - 1),
        0,
    ]
    (execution,) = vantage_server.executions()
    assert execution.finished_at is not None
    assert execution.exit_status == 0
    assert len(vantage_server.results()) == 1000


def test_a_result_too_large_for_any_report_costs_only_itself_and_one_warning(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
) -> None:
    """A skip reason is never charged against the failure-text budget, so
    one of over a megabyte makes a result no report can carry. That result
    is left out with one warning; the rest of the session is recorded and
    the run finishes, instead of the whole report being refused.
    """
    pytester.makepyfile(
        test_sample="import pytest\n\n\n"
        "def test_kept():\n    assert True\n\n\n"
        "def test_huge_skip():\n    pytest.skip('s' * 1_100_000)\n"
    )

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    result.assert_outcomes(passed=1, skipped=1)
    output = result.stdout.str()
    assert output.count("VantageWarning:") == 1
    assert "1 test result(s) too large for any report were left out" in output
    (execution,) = vantage_server.executions()
    assert execution.finished_at is not None
    assert [stored.identity.node_id for stored in vantage_server.results()] == [
        "test_sample.py::test_kept"
    ]


def test_a_long_session_with_failure_text_keeps_every_result_and_failure_message(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    wire_sizes: list[tuple[dict[str, object], int]],
) -> None:
    """A thousand passing tests that print, then twenty failures: the
    passing output alone is more than the failure-text budget, and the
    session more than one report. Every result is stored, and every failure
    keeps its message whole, because failures are charged before the
    output of the tests that passed.
    """
    pytester.makepyfile(
        test_chatty="import pytest\n\n\n"
        "@pytest.mark.parametrize('n', range(1000))\n"
        "def test_p(n):\n    print('p' * 700)\n",
        test_zz_failing="import pytest\n\n\n"
        "@pytest.mark.parametrize('n', range(20))\n"
        "def test_f(n):\n    assert False, f'failure {n}: ' + 'm' * 1400\n",
    )

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    result.assert_outcomes(passed=1000, failed=20)
    assert "VantageWarning" not in result.stdout.str()
    assert all(size <= MAX_REPORT_BYTES for _report, size in wire_sizes)
    (execution,) = vantage_server.executions()
    assert execution.finished_at is not None
    stored = vantage_server.results()
    assert len(stored) == 1020
    failures = [entry.failure for entry in stored if entry.outcome == "failed"]
    assert len(failures) == 20
    assert all(
        failure is not None
        and failure.failure_message is not None
        and "failure " in failure.failure_message
        and not failure.failure_message_truncated
        for failure in failures
    )


def test_a_session_of_many_large_failures_stays_within_one_report(
    pytester: pytest.Pytester,
    vantage_server: VantageTestServer,
    wire_sizes: list[tuple[dict[str, object], int]],
) -> None:
    """Ten tests each raising an 80,000-character message would, unbounded,
    carry about 2.4 MB of failure text: the message is rendered into
    `failure_message`, `failure_repr` and `traceback` independently. The
    budget holds it to half the cap, so the session still fits one report.
    """
    body = "\n".join(
        f"def test_{i}():\n    raise AssertionError('X' * 80_000)\n" for i in range(10)
    )
    pytester.makepyfile(test_many_large_failures=body)

    result = pytester.runpytest(
        "--vantage", f"--vantage-server={vantage_server.address}", "--vantage-failure-text"
    )

    result.assert_outcomes(failed=10)
    _start, (finish_report, finish_size) = wire_sizes
    assert finish_size <= MAX_REPORT_BYTES
    assert any(entry.get("failure_message") for entry in finish_report["results"])  # type: ignore[attr-defined]
    assert len(vantage_server.results()) == 10
