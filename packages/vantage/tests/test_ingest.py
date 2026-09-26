"""`vantage.ingestion.ingest`: a decoded report validated, converted and
recorded in a store handed to it, with no HTTP anywhere. The server's route
and the local store both call it; what the route adds on top is in
`test_ingestion.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from memory_store import InMemoryExecutionStore
from vantage.ingestion import Ingested, ingest
from vantage.ingestion.decode import decode_json
from vantage.ingestion.errors import InvalidJsonError, InvalidReportError, RejectionError

_RECEIVED_AT = datetime(2026, 8, 15, 9, 15, tzinfo=timezone.utc)


def _report(run_id: str = "a" * 32, **sections: Any) -> dict[str, Any]:
    return {
        "run": {
            "id": run_id,
            "started_at": "2026-08-15T09:14:02.481930+00:00",
            "finished_at": "2026-08-15T09:14:47.002118+00:00",
            "exit_status": 0,
            "interrupted": False,
            "interrupt_reason": None,
        },
        **sections,
    }


def _result(node_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "file_path": node_id.partition("::")[0],
        "class_name": None,
        "function_name": node_id.rpartition("::")[2],
        "param_id": None,
        "outcome": "passed",
        "duration": 0.01,
        "started_at": None,
        "finished_at": None,
        "setup_outcome": "passed",
        "call_outcome": "passed",
        "teardown_outcome": "passed",
        "setup_duration": None,
        "call_duration": None,
        "teardown_duration": None,
        "worker_id": None,
        **extra,
    }


def test_a_report_is_recorded_and_says_it_created_the_run() -> None:
    store = InMemoryExecutionStore()

    ingested = ingest(
        _report(results=[_result("tests/test_a.py::test_x")]), store, received_at=_RECEIVED_AT
    )

    assert ingested == Ingested(run_id="a" * 32, created=True, ignored=())
    execution = store.get_execution("a" * 32)
    assert execution is not None
    assert execution.exit_status == 0
    assert [result.identity.node_id for result in store.get_results("a" * 32)] == [
        "tests/test_a.py::test_x"
    ]


def test_a_second_report_for_the_same_run_is_not_a_creation() -> None:
    store = InMemoryExecutionStore()
    ingest(_report(), store, received_at=_RECEIVED_AT)

    again = ingest(_report(), store, received_at=_RECEIVED_AT)

    assert again.created is False
    assert store.count_executions() == 1


def test_an_unknown_key_on_a_result_is_recorded_and_named_once() -> None:
    store = InMemoryExecutionStore()
    results = [
        _result("tests/test_a.py::test_x", marker="slow"),
        _result("tests/test_a.py::test_y", marker="fast", **{"not a name": 1}),
    ]

    ingested = ingest(_report(results=results), store, received_at=_RECEIVED_AT)

    assert ingested.ignored == ("results[].marker", "results[].<unnamed>")
    assert store.count_results() == 2


def test_a_report_that_is_not_a_session_report_is_refused_before_the_store_is_touched() -> None:
    store = InMemoryExecutionStore()
    report = _report()
    del report["run"]["started_at"]

    with pytest.raises(InvalidReportError) as refused:
        ingest(report, store, received_at=_RECEIVED_AT)

    assert (refused.value.status_code, refused.value.error) == (422, "invalid_report")
    assert refused.value.fields == ["run.started_at"]
    assert store.count_executions() == 0


def test_a_payload_that_is_not_an_object_is_refused_naming_no_field() -> None:
    with pytest.raises(InvalidReportError) as refused:
        ingest([1, 2], InMemoryExecutionStore(), received_at=_RECEIVED_AT)

    assert refused.value.fields == []


def test_what_the_store_raises_reaches_the_caller_unchanged() -> None:
    """A store's own refusal is its to shape: the server answers a
    `RejectionError` a store raises with that store's status."""

    class _UnavailableError(RejectionError):
        status_code = 503
        error = "unavailable"

    class _RefusingStore(InMemoryExecutionStore):
        def record_session(self, *args: Any, **kwargs: Any) -> bool:
            raise _UnavailableError("not now")

    with pytest.raises(_UnavailableError):
        ingest(_report(), _RefusingStore(), received_at=_RECEIVED_AT)


def test_received_at_is_the_time_the_caller_gives() -> None:
    store = InMemoryExecutionStore()

    ingest(_report(), store, received_at=_RECEIVED_AT)

    detail = store.get_run_detail("a" * 32)
    assert detail is not None
    assert detail.last_contact_at == _RECEIVED_AT


def test_decode_json_refuses_what_is_not_strict_json_as_a_plain_exception() -> None:
    for body in (b"{", b'{"a": NaN}', '"\ud800"'.encode("utf-8", "surrogatepass")):
        with pytest.raises(InvalidJsonError) as refused:
            decode_json(body)
        assert (refused.value.status_code, refused.value.error) == (400, "invalid_json")


def test_decode_json_makes_report_text_storable() -> None:
    body = b'{"a\\u0000": "\\ud800 and \\u0000"}'

    assert decode_json(body, replace_lone_surrogates=True) == {"a�": "� and �"}
