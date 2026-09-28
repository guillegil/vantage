"""`vantage.ingestion.ingest`: a decoded report validated, converted and
recorded in a store handed to it, with no HTTP anywhere. The server's route
and the local store both call it; what the route adds on top is in
`test_ingestion.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from memory_store import InMemoryExecutionStore
from vantage.core.domain.projects import DEFAULT_PROJECT, Project
from vantage.core.ports.storage import ProjectExistsError, UnknownProjectError
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


def _project_of(store: InMemoryExecutionStore, run_id: str = "a" * 32) -> str:
    detail = store.get_run_detail(run_id)
    assert detail is not None
    return detail.project


@pytest.mark.parametrize(
    "sections",
    [{}, {"project": None}, {"project": DEFAULT_PROJECT}],
    ids=["absent", "null", "default"],
)
def test_a_report_naming_no_project_is_recorded_in_default(sections: dict[str, Any]) -> None:
    """A plugin that predates projects, or any client that names none,
    records where every run went before there were projects."""
    store = InMemoryExecutionStore()

    ingest(_report(**sections), store, received_at=_RECEIVED_AT)

    assert _project_of(store) == DEFAULT_PROJECT
    assert [p.name for p in store.list_projects()] == [DEFAULT_PROJECT]


def test_a_report_is_recorded_in_the_project_it_names() -> None:
    store = InMemoryExecutionStore()
    store.create_project("alpha", created_at=_RECEIVED_AT)

    ingest(_report(project="alpha"), store, received_at=_RECEIVED_AT)

    assert _project_of(store) == "alpha"
    assert store.list_runs(limit=10, offset=0, project=DEFAULT_PROJECT).items == ()


def test_a_report_into_a_project_the_store_lacks_is_refused_and_makes_none() -> None:
    """Without `create_missing_project` a report never makes a project: on
    a server only an admin does."""
    store = InMemoryExecutionStore()

    with pytest.raises(UnknownProjectError):
        ingest(_report(project="alpha"), store, received_at=_RECEIVED_AT)

    assert store.count_executions() == 0
    assert store.get_project("alpha") is None


@pytest.mark.parametrize(
    "project",
    ["", "Bad", "a" * 65, 5, "../x", "a\x00", "a\ufffd"],
    ids=["empty", "upper-case", "65-chars", "number", "path", "nul", "replacement"],
)
def test_a_project_that_is_no_project_name_is_refused_before_the_store_is_touched(
    project: object,
) -> None:
    store = InMemoryExecutionStore()

    with pytest.raises(InvalidReportError) as refused:
        ingest(
            _report(project=project), store, received_at=_RECEIVED_AT, create_missing_project=True
        )

    assert refused.value.fields == ["project"]
    assert store.count_executions() == 0
    assert [p.name for p in store.list_projects()] == [DEFAULT_PROJECT]


def test_create_missing_project_makes_the_project_once_at_the_first_report() -> None:
    store = InMemoryExecutionStore()
    later = _RECEIVED_AT + timedelta(minutes=1)

    first = ingest(
        _report("a" * 32, project="alpha"),
        store,
        received_at=_RECEIVED_AT,
        create_missing_project=True,
    )
    second = ingest(
        _report("b" * 32, project="alpha"), store, received_at=later, create_missing_project=True
    )

    assert (first.created, second.created) == (True, True)
    assert list(store.list_projects()) == [
        Project(name="alpha", created_at=_RECEIVED_AT),
        store.get_project(DEFAULT_PROJECT),
    ]
    assert (_project_of(store, "a" * 32), _project_of(store, "b" * 32)) == ("alpha", "alpha")


def test_create_missing_project_leaves_an_existing_project_as_it_is() -> None:
    store = InMemoryExecutionStore()
    existing = store.create_project("alpha", created_at=_RECEIVED_AT - timedelta(days=1))

    ingest(_report(project="alpha"), store, received_at=_RECEIVED_AT, create_missing_project=True)

    assert store.get_project("alpha") == existing
    assert _project_of(store) == "alpha"


def test_create_missing_project_tolerates_another_writer_making_it_first() -> None:
    """Two sessions storing into one local file can both find the project
    missing; the one that loses the race records its run all the same."""

    class _RacedStore(InMemoryExecutionStore):
        def create_project(self, name: str, *, created_at: datetime) -> Project:
            super().create_project(name, created_at=created_at - timedelta(seconds=1))
            raise ProjectExistsError(name)

    store = _RacedStore()

    ingested = ingest(
        _report(project="alpha"), store, received_at=_RECEIVED_AT, create_missing_project=True
    )

    assert ingested.created is True
    assert _project_of(store) == "alpha"
    assert [p.name for p in store.list_projects()] == ["alpha", DEFAULT_PROJECT]


# --- Admitting a report ---------------------------------------------------------------


class _RefusedError(Exception):
    """What a caller's `admit` raises for a report it will not have stored."""


@pytest.mark.parametrize(
    ("sections", "project"),
    [({}, DEFAULT_PROJECT), ({"project": None}, DEFAULT_PROJECT), ({"project": "alpha"}, "alpha")],
    ids=["absent", "null", "named"],
)
def test_admit_is_given_the_reports_project_before_anything_is_stored(
    sections: dict[str, Any], project: str
) -> None:
    """Once the report is valid and converted -- a report naming no project
    names `default` -- and before anything is made or recorded, the project
    it missed included."""
    store = InMemoryExecutionStore()
    seen: list[tuple[str, int, bool]] = []

    def admit(name: str) -> None:
        seen.append((name, store.count_executions(), store.get_project(name) is None))

    ingest(
        _report(**sections),
        store,
        received_at=_RECEIVED_AT,
        create_missing_project=True,
        admit=admit,
    )

    assert seen == [(project, 0, project != DEFAULT_PROJECT)]
    assert _project_of(store) == project


def test_what_admit_raises_passes_through_and_nothing_is_stored() -> None:
    store = InMemoryExecutionStore()

    def admit(name: str) -> None:
        raise _RefusedError(name)

    with pytest.raises(_RefusedError, match="alpha"):
        ingest(
            _report(project="alpha", results=[_result("tests/test_a.py::test_x")]),
            store,
            received_at=_RECEIVED_AT,
            create_missing_project=True,
            admit=admit,
        )

    assert store.count_executions() == 0
    assert store.count_results() == 0
    assert store.get_project("alpha") is None


def test_admit_refuses_a_report_before_the_store_can() -> None:
    """A report of another sender's run, filed in another project, is one
    the store refuses for either reason; `admit` is asked first, so a
    sender it refuses never learns which, and the run is left as it was."""
    store = InMemoryExecutionStore()
    for project in ("alpha", "beta"):
        store.create_project(project, created_at=_RECEIVED_AT)
    ingest(_report(project="beta"), store, received_at=_RECEIVED_AT, recorded_by="alice")
    before = store.get_run_detail("a" * 32)

    def admit(name: str) -> None:
        raise _RefusedError(name)

    with pytest.raises(_RefusedError, match="alpha"):
        ingest(
            _report(project="alpha", results=[_result("tests/test_a.py::test_x")]),
            store,
            received_at=_RECEIVED_AT,
            recorded_by="bob",
            admit=admit,
        )

    assert store.get_run_detail("a" * 32) == before
    assert store.count_results() == 0


@pytest.mark.parametrize(
    "report",
    [
        {"run": {"id": "a" * 32}},
        _report(project="Bad"),
        _report(results=[_result("tests/test_a.py::test_x", outcome="unheard-of")]),
        [1, 2],
    ],
    ids=["incomplete-run", "impossible-project", "bad-result", "not-an-object"],
)
def test_a_report_that_is_not_valid_never_reaches_admit(report: object) -> None:
    """A caller's check of who may record where is never asked about a
    report the server would refuse anyway."""
    store = InMemoryExecutionStore()
    asked: list[str] = []

    with pytest.raises(InvalidReportError):
        ingest(report, store, received_at=_RECEIVED_AT, admit=asked.append)

    assert asked == []
    assert store.count_executions() == 0


def test_decode_json_refuses_what_is_not_strict_json_as_a_plain_exception() -> None:
    for body in (b"{", b'{"a": NaN}', '"\ud800"'.encode("utf-8", "surrogatepass")):
        with pytest.raises(InvalidJsonError) as refused:
            decode_json(body)
        assert (refused.value.status_code, refused.value.error) == (400, "invalid_json")


def test_decode_json_makes_report_text_storable() -> None:
    body = b'{"a\\u0000": "\\ud800 and \\u0000"}'

    assert decode_json(body, replace_lone_surrogates=True) == {"a�": "� and �"}
