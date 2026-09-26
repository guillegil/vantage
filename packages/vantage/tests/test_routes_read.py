"""The read routes: run list, run detail, results, result detail and test
history.

Runs the app factory (`vantage.service.app.create_app`) against **both**
`ExecutionStore` implementations: the `store` fixture is parametrised, so
every test below executes twice -- once against `InMemoryExecutionStore` and
once against `SqliteExecutionStore`, the adapter that actually ships. Only
the SQLite run catches a row-to-domain mapper (`_row_to_run_list_entry`,
`_row_to_history_entry`, `_row_to_vcs_projection`) that drops a value on its
way to the wire.

Every fixture constructs `Execution`/`VcsContext` directly and seeds the
store through `record_session`, never through the ingestion route: these
tests are about what the read path returns, not how a session was reported.
Grace-period tests set `last_contact_at` and `create_app`'s
`grace_period_seconds` relative to a `now` the test itself computes, so no
clock control (freezegun, `time.sleep`) is needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
from fastapi.testclient import TestClient
from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.projection import LIST_COMMIT_SUBJECT_CHARS, LIST_FAILURE_MESSAGE_CHARS
from vantage.core.domain.result import CaseIdentity, Result
from vantage.core.ports.storage import (
    MAX_IDENTITY_CHARS,
    ExecutionStore,
    HistoryEntry,
    MetadataEntry,
    MetadataFile,
    Page,
    RunMetadata,
)
from vantage.service.app import create_app
from vantage_port_contract import _captured, _failure, _result

# `any_store`, for each adapter in turn.
pytest_plugins = ["store_fixtures"]

_KNOWN_ROOT = "/home/example/very-unique-repo-root-xyz123"

# Distinct, recognisable 40-hex commits, one per fixture that asserts VCS
# values on the wire. Distinct so a swapped or nulled field fails loudly
# instead of coincidentally matching a neighbour's value or a shared default.
_LIST_COMMIT = "c0ffee01" * 5
_HISTORY_NEWER_COMMIT = "beef0002" * 5
_HISTORY_OLDER_COMMIT = "dead0003" * 5
_TRUNCATION_COMMIT = "face0004" * 5
_DETAIL_COMMIT = "abad0005" * 5


def _run_id(seed: int) -> str:
    """A well-formed 32-lowercase-hex identity, unique per `seed`."""
    return f"{seed:032x}"


def _instant(wire_value: str) -> datetime:
    """The instant an ISO-8601 timestamp on the wire denotes.

    Compares timestamps by *instant* rather than by string, so these tests
    assert the value the response carries and not Pydantic's chosen spelling
    of it -- a serializer that switched between `+00:00` and `Z` would
    otherwise fail every timestamp assertion for no behavioural reason. The
    `Z` substitution is for Python 3.10, the oldest supported version, whose
    `fromisoformat` does not accept the military suffix."""
    return datetime.fromisoformat(wire_value.replace("Z", "+00:00"))


def _execution(
    run_id: str,
    *,
    started_at: datetime,
    finished_at: datetime | None = None,
    exit_status: int | None = 0,
    interrupted: bool = False,
    interrupt_reason: str | None = None,
    vcs: VcsContext | None = None,
) -> Execution:
    return Execution(
        identity=Identity(run_id),
        started_at=started_at,
        finished_at=finished_at,
        exit_status=exit_status,
        interrupted=interrupted,
        interrupt_reason=interrupt_reason,
        vcs=vcs,
    )


def _vcs(
    *,
    commit: str | None = "a" * 40,
    branch: str | None = "main",
    commit_subject: str | None = "a commit subject",
    commit_subject_truncated: bool = False,
    dirty: bool | None = False,
    root: str | None = _KNOWN_ROOT,
) -> VcsContext:
    return VcsContext(
        commit=commit,
        branch=branch,
        commit_subject=commit_subject,
        commit_subject_truncated=commit_subject_truncated,
        dirty=dirty,
        root=root,
    )


def _captured_metadata(
    key: str, value: str, *, source_file: str = "config/firmware.yaml"
) -> RunMetadata:
    """One declared, captured `key=value` pair plus the file row that
    accompanies it -- what `store.list_runs_with_metadata_horizon`'s filter
    and its horizon count read."""
    return RunMetadata(
        files=(MetadataFile(source_file=source_file, content_type="yaml", status="captured"),),
        entries=(MetadataEntry(key=key, value=value, source_file=source_file, status="captured"),),
    )


@pytest.fixture
def store(any_store: ExecutionStore) -> ExecutionStore:
    """`store_fixtures.any_store` under the name every test here uses:
    each adapter in turn, fresh for each test."""
    return any_store


@pytest.fixture
def client(store: ExecutionStore) -> TestClient:
    return TestClient(create_app(store))


def _client_with_grace(store: ExecutionStore, grace_period_seconds: float) -> TestClient:
    return TestClient(create_app(store, grace_period_seconds=grace_period_seconds))


class _SpyExecutionStore:
    """A spy exposing only `list_history`, `cast` to `ExecutionStore` at its
    one call site -- the history route calls no other method, and this test
    asserts nothing about the rest of the port."""

    def __init__(self) -> None:
        self.list_history_calls: list[str] = []

    def list_history(self, *, node_id: str, limit: int, offset: int) -> Page[HistoryEntry]:
        self.list_history_calls.append(node_id)
        return Page(items=(), has_more=False)


def test_run_list_returns_items_and_has_more_envelope(
    client: TestClient, store: ExecutionStore
) -> None:
    """The run list returns an `items`/`has_more` envelope whose items carry
    each run's fields and VCS context by value.

    **Shape and value are different properties and this test holds both.**
    The exact key sets assert the shape a field-by-field response model
    produces, never `from_attributes`'s incidental extras. The value
    assertions then assert that every scalar reaches the wire intact: a
    key-set check alone stays green while `_run_list_item` hardcodes
    `finished_at=None` or `_vcs_response` hardcodes `dirty=None`.

    Every fixture value here is deliberately off the default: `started_at`
    and `finished_at` are distinct instants, and `exit_status` is `7` rather
    than the `0` a swap would coincidentally match. `presentation` and
    `interrupted` are asserted on varied fixtures by
    `test_run_list_presentation_and_interruption_are_per_run`."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(1)
    started_at = now - timedelta(hours=1)
    finished_at = now - timedelta(minutes=17)
    store.record_session(
        _execution(
            run_id,
            started_at=started_at,
            finished_at=finished_at,
            exit_status=7,
            vcs=_vcs(
                commit=_LIST_COMMIT,
                branch="release/list-envelope",
                commit_subject="bound the run list envelope",
                commit_subject_truncated=False,
                dirty=True,
            ),
        ),
        results=[],
        received_at=started_at,
    )

    response = client.get("/api/v1/runs")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "has_more", "metadata_horizon"}
    assert isinstance(body["has_more"], bool)
    assert body["has_more"] is False
    assert body["metadata_horizon"] is None
    item = body["items"][0]
    assert set(item.keys()) == {
        "id",
        "started_at",
        "finished_at",
        "exit_status",
        "interrupted",
        "presentation",
        "vcs",
    }
    assert item["id"] == run_id
    assert _instant(item["started_at"]) == started_at
    assert _instant(item["finished_at"]) == finished_at
    assert item["exit_status"] == 7
    assert item["interrupted"] is False
    assert item["presentation"] == "finished"
    assert set(item["vcs"].keys()) == {
        "commit",
        "branch",
        "commit_subject",
        "commit_subject_truncated",
        "dirty",
    }
    assert item["vcs"] == {
        "commit": _LIST_COMMIT,
        "branch": "release/list-envelope",
        "commit_subject": "bound the run list envelope",
        "commit_subject_truncated": False,
        "dirty": True,
    }


def test_run_list_presentation_and_interruption_are_per_run(
    store: ExecutionStore,
) -> None:
    """The run list derives each run's presentation, including abandoned.

    The other liveness tests go through run *detail*, so without this one
    `_run_list_item` could hardcode `presentation="finished"` unnoticed. One
    run per branch of the derivation, in one list response, makes that fail:
    a constant cannot be right for four runs at once. Likewise only a run
    that *is* interrupted can catch `interrupted` being hardcoded false."""
    now = datetime.now(timezone.utc)
    finished_run = _run_id(40)
    interrupted_run = _run_id(41)
    abandoned_run = _run_id(42)
    running_run = _run_id(43)
    store.record_session(
        _execution(
            finished_run,
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=2, minutes=50),
        ),
        results=[],
        received_at=now - timedelta(hours=3),
    )
    store.record_session(
        _execution(
            interrupted_run,
            started_at=now - timedelta(hours=2),
            finished_at=None,
            exit_status=2,
            interrupted=True,
            interrupt_reason="KeyboardInterrupt",
        ),
        results=[],
        received_at=now - timedelta(hours=2),
    )
    store.record_session(
        _execution(
            abandoned_run,
            started_at=now - timedelta(minutes=90),
            finished_at=None,
            exit_status=None,
        ),
        results=[],
        received_at=now - timedelta(minutes=90),
    )
    store.record_session(
        _execution(
            running_run,
            started_at=now - timedelta(seconds=30),
            finished_at=None,
            exit_status=None,
        ),
        results=[],
        received_at=now - timedelta(seconds=5),
    )
    client = _client_with_grace(store, grace_period_seconds=60)

    response = client.get("/api/v1/runs")

    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()["items"]}
    assert {run: items[run]["presentation"] for run in items} == {
        finished_run: "finished",
        interrupted_run: "interrupted",
        abandoned_run: "abandoned",
        running_run: "running",
    }
    assert items[interrupted_run]["interrupted"] is True
    assert items[abandoned_run]["interrupted"] is False


def test_run_list_response_contains_no_vcs_root_anywhere(
    client: TestClient, store: ExecutionStore
) -> None:
    """The repository root appears nowhere in a run list response. A
    substring assertion on the raw serialized body.

    **Structurally unfalsifiable today**, like the history-route
    counterpart: on the list path the source object is a `VcsProjection`,
    which has no `root` field at all, and both adapters additionally strip
    the context off the entry itself (`replace(execution, vcs=None)`). Kept
    as a regression guard against a future list entry that carries a full
    `VcsContext`. `test_run_detail_response_contains_no_vcs_root` is the
    test that can actually fail."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(2)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now, vcs=_vcs()),
        results=[],
        received_at=now - timedelta(hours=1),
    )

    response = client.get("/api/v1/runs")

    assert _KNOWN_ROOT not in response.text


def test_run_list_rejects_non_positive_limit(client: TestClient) -> None:
    """Zero and negative limits are not page sizes."""
    zero = client.get("/api/v1/runs", params={"limit": 0})
    negative = client.get("/api/v1/runs", params={"limit": -1})

    assert zero.status_code == 422
    assert negative.status_code == 422


def test_run_list_caps_at_200_at_the_route(client: TestClient, store: ExecutionStore) -> None:
    """201 stored runs, no `limit` supplied -- the cap must hold through the
    HTTP layer, not only the port."""
    now = datetime.now(timezone.utc)
    for seed in range(201):
        store.record_session(
            _execution(
                _run_id(seed),
                started_at=now - timedelta(seconds=201 - seed),
                finished_at=now,
            ),
            results=[],
            received_at=now,
        )

    response = client.get("/api/v1/runs")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 200
    assert body["has_more"] is True


def test_run_list_clamps_an_over_cap_limit_rather_than_rejecting_it(
    client: TestClient, store: ExecutionStore
) -> None:
    """A list response never exceeds 200 items, even when a caller asks for
    more; the no-`limit` cap test only exercises the default.

    A caller asking for 500 gets 200 and a 200 status, not a 422: someone
    requesting a large page wants data, not a rejection. This is the
    behaviour `openapi/v1.yaml` states, and it is why that parameter
    declares no `maximum` -- a `maximum` would assert a constraint the
    server does not enforce, and a strict generated client would refuse a
    request this server answers."""
    now = datetime.now(timezone.utc)
    for seed in range(201):
        store.record_session(
            _execution(
                _run_id(seed),
                started_at=now - timedelta(seconds=201 - seed),
                finished_at=now,
            ),
            results=[],
            received_at=now,
        )

    response = client.get("/api/v1/runs", params={"limit": 500})

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 200
    assert body["has_more"] is True


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/runs", {}),
        ("/api/v1/runs", {"metadata_key": "k", "metadata_value": "v"}),
        ("/api/v1/runs/{run_id}/results", {}),
        ("/api/v1/tests/history", {"node_id": "t.py::test_x"}),
    ],
    ids=["runs", "runs-filtered", "results", "history"],
)
def test_an_offset_beyond_int64_is_422_and_the_int64_maximum_is_an_empty_page(
    client: TestClient, store: ExecutionStore, path: str, params: dict[str, str]
) -> None:
    """SQLite binds an integer as signed 64-bit, so a larger offset would
    fail inside the query as a bare `500` on one adapter and succeed on the
    other. It is a shaped `422` on both; the largest bindable offset is a
    valid, empty page."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(80)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[_result("t.py::test_x")],
        received_at=now - timedelta(hours=1),
    )
    url = path.format(run_id=run_id)

    beyond = client.get(url, params={**params, "offset": 2**63})
    at_max = client.get(url, params={**params, "offset": 2**63 - 1})

    assert beyond.status_code == 422
    assert beyond.json()["fields"] == ["query.offset"]
    assert at_max.status_code == 200
    assert at_max.json()["items"] == []
    assert at_max.json()["has_more"] is False


def test_run_detail_returns_full_untruncated_subject(
    client: TestClient, store: ExecutionStore
) -> None:
    """The full record stays reachable via run detail: a 200-character
    stored subject, well past the 120-character list display width, comes
    back whole and untruncated on the detail path."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(5)
    subject = "s" * 200
    store.record_session(
        _execution(
            run_id,
            started_at=now - timedelta(hours=1),
            finished_at=now,
            vcs=_vcs(commit_subject=subject, commit_subject_truncated=False),
        ),
        results=[],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(f"/api/v1/runs/{run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["vcs"]["commit_subject"] == subject
    assert len(body["vcs"]["commit_subject"]) == 200
    assert body["vcs"]["commit_subject_truncated"] is False


def test_run_detail_response_contains_no_vcs_root(
    client: TestClient, store: ExecutionStore
) -> None:
    """The repository root appears nowhere in a run detail response.

    Unlike the list and history counterparts this one can actually fail: the
    detail path's source object is the full `VcsContext`, which *does* carry
    `root`. The only thing keeping it off the wire is `RunVcsResponse`
    having no `root` field and `_vcs_response` naming its five fields
    explicitly, never `model_validate(..., from_attributes=True)`."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(6)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now, vcs=_vcs()),
        results=[],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(f"/api/v1/runs/{run_id}")

    assert _KNOWN_ROOT not in response.text


def test_run_detail_carries_every_stored_field_by_value(
    client: TestClient, store: ExecutionStore
) -> None:
    """Run detail carries the full record: every one of the eight detail
    fields, asserted by value.

    Two runs rather than one, because a single fixture cannot populate both
    halves of the record: an orderly run carries `finished_at` and no
    `interrupt_reason`, a Ctrl-C run carries the reason and no
    `finished_at`, and a builder that hardcoded either to `None` would still
    satisfy whichever run happens to be null there.

    The two runs also disagree on `id`, `started_at` and `exit_status`, so
    `id=<constant>` fails on whichever run it is not, and a `started_at`
    shifted by a fixed offset fails on both."""
    now = datetime.now(timezone.utc)
    orderly_run = _run_id(50)
    orderly_started_at = now - timedelta(hours=4)
    orderly_finished_at = now - timedelta(hours=3, minutes=11)
    ctrl_c_run = _run_id(51)
    ctrl_c_started_at = now - timedelta(hours=2, minutes=7)
    ctrl_c_reason = "KeyboardInterrupt during tests/test_slow.py::test_waits"
    store.record_session(
        _execution(
            orderly_run,
            started_at=orderly_started_at,
            finished_at=orderly_finished_at,
            exit_status=3,
            vcs=_vcs(
                commit=_DETAIL_COMMIT,
                branch="release/detail-fidelity",
                commit_subject="carry the whole record to the detail wire",
                commit_subject_truncated=True,
                dirty=True,
            ),
        ),
        results=[],
        received_at=orderly_started_at,
    )
    store.record_session(
        _execution(
            ctrl_c_run,
            started_at=ctrl_c_started_at,
            finished_at=None,
            exit_status=2,
            interrupted=True,
            interrupt_reason=ctrl_c_reason,
            vcs=None,
        ),
        results=[],
        received_at=ctrl_c_started_at,
    )

    orderly = client.get(f"/api/v1/runs/{orderly_run}").json()
    ctrl_c = client.get(f"/api/v1/runs/{ctrl_c_run}").json()

    assert set(orderly.keys()) == {
        "id",
        "started_at",
        "finished_at",
        "exit_status",
        "interrupted",
        "interrupt_reason",
        "presentation",
        "vcs",
    }
    assert orderly["id"] == orderly_run
    assert _instant(orderly["started_at"]) == orderly_started_at
    assert _instant(orderly["finished_at"]) == orderly_finished_at
    assert orderly["exit_status"] == 3
    assert orderly["interrupted"] is False
    assert orderly["interrupt_reason"] is None
    assert orderly["presentation"] == "finished"
    # The detail path's five VCS fields, by value: detail is the only route
    # that reads a full `VcsContext` rather than a `VcsProjection`, so
    # nothing else on the wire covers `_row_to_vcs_context`.
    # `commit_subject_truncated` is `True` here beside an unshortened
    # subject, which only capture-time truncation can produce -- the detail
    # path never applies display bounding.
    assert orderly["vcs"] == {
        "commit": _DETAIL_COMMIT,
        "branch": "release/detail-fidelity",
        "commit_subject": "carry the whole record to the detail wire",
        "commit_subject_truncated": True,
        "dirty": True,
    }

    assert ctrl_c["id"] == ctrl_c_run
    assert _instant(ctrl_c["started_at"]) == ctrl_c_started_at
    assert ctrl_c["finished_at"] is None
    assert ctrl_c["exit_status"] == 2
    assert ctrl_c["interrupted"] is True
    assert ctrl_c["interrupt_reason"] == ctrl_c_reason
    assert ctrl_c["presentation"] == "interrupted"
    assert ctrl_c["vcs"] is None


def test_run_detail_unknown_id_is_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/runs/{_run_id(999)}")

    assert response.status_code == 404


def test_abandoned_run_reads_back_as_abandoned(store: ExecutionStore) -> None:
    """A run past its grace period reads back as abandoned. No clock
    control: `last_contact_at` is stamped old relative to a `now` this test
    computes itself, and the app's grace period is configured short."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(8)
    old_contact = now - timedelta(hours=2)
    store.record_session(
        _execution(run_id, started_at=old_contact, finished_at=None, exit_status=None),
        results=[],
        received_at=old_contact,
    )
    client = _client_with_grace(store, grace_period_seconds=60)

    response = client.get(f"/api/v1/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["presentation"] == "abandoned"


def test_running_run_reads_back_as_running(store: ExecutionStore) -> None:
    """A run inside its grace period reads back as running."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(9)
    recent_contact = now - timedelta(seconds=5)
    store.record_session(
        _execution(run_id, started_at=recent_contact, finished_at=None, exit_status=None),
        results=[],
        received_at=recent_contact,
    )
    client = _client_with_grace(store, grace_period_seconds=3600)

    response = client.get(f"/api/v1/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["presentation"] == "running"


def test_interrupted_run_reads_back_as_interrupted(store: ExecutionStore) -> None:
    """A Ctrl-C interrupted run reads back as interrupted, not abandoned.
    `last_contact_at` is stamped just as stale as the abandoned fixture, and
    the grace period just as short -- the only difference is
    `interrupted=True`, which must win regardless of staleness."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(10)
    old_contact = now - timedelta(hours=2)
    store.record_session(
        _execution(
            run_id,
            started_at=old_contact,
            finished_at=None,
            exit_status=None,
            interrupted=True,
            interrupt_reason="ctrl-c",
        ),
        results=[],
        received_at=old_contact,
    )
    client = _client_with_grace(store, grace_period_seconds=60)

    response = client.get(f"/api/v1/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["presentation"] == "interrupted"


def test_abandonment_invents_no_stored_field(store: ExecutionStore) -> None:
    """Reading a run as abandoned writes nothing. Reads the row back
    directly via `store.get_execution`, not through the response body -- a
    derived presentation must not mutate what was recorded."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(11)
    original_started_at = now - timedelta(hours=2)
    store.record_session(
        _execution(run_id, started_at=original_started_at, finished_at=None, exit_status=None),
        results=[],
        received_at=original_started_at,
    )
    client = _client_with_grace(store, grace_period_seconds=60)

    response = client.get(f"/api/v1/runs/{run_id}")
    assert response.json()["presentation"] == "abandoned"

    execution = store.get_execution(run_id)
    assert execution is not None
    assert execution.started_at == original_started_at
    assert execution.finished_at is None


def test_results_route_returns_paginated_envelope(
    client: TestClient, store: ExecutionStore
) -> None:
    now = datetime.now(timezone.utc)
    run_id = _run_id(20)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[_result("tests/test_a.py::test_one")],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(f"/api/v1/runs/{run_id}/results")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "has_more"}
    assert body["has_more"] is False
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["node_id"] == "tests/test_a.py::test_one"
    assert item["outcome"] == "passed"


_SENTINEL_TRACEBACK = "SENTINEL-TRACEBACK-8f6c1a"


def test_results_route_response_excludes_traceback_and_captured_output_sentinel(
    client: TestClient, store: ExecutionStore
) -> None:
    """List responses exclude traceback and captured output. A distinctive
    sentinel planted in the stored traceback, failure repr and captured
    stdout must be absent from the raw list response body -- asserted
    against the raw text, not a parsed model, because a parsed model can
    only show fields someone thought to check for."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(70)
    failure = _failure(traceback=_SENTINEL_TRACEBACK, failure_repr=_SENTINEL_TRACEBACK)
    captured = _captured(stdout=_SENTINEL_TRACEBACK)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[_result("t.py::test_x", outcome="failed", failure=failure, captured=captured)],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(f"/api/v1/runs/{run_id}/results")

    assert response.status_code == 200
    assert _SENTINEL_TRACEBACK not in response.text


def test_results_route_includes_bounded_failure_message_and_disjunction_flag(
    client: TestClient, store: ExecutionStore
) -> None:
    """A stored message over `LIST_FAILURE_MESSAGE_CHARS` arrives in the list
    entry bounded to the first 200 characters, with the disjunction flag
    set."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(71)
    long_message = "E" * (LIST_FAILURE_MESSAGE_CHARS + 51)
    failure = _failure(failure_message=long_message, failure_message_truncated=False)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[_result("t.py::test_x", outcome="failed", failure=failure)],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(f"/api/v1/runs/{run_id}/results")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["failure"]["failure_message"] == long_message[:LIST_FAILURE_MESSAGE_CHARS]
    assert item["failure"]["failure_message_truncated"] is True


def test_result_detail_route_returns_full_record(client: TestClient, store: ExecutionStore) -> None:
    """The single-result route returns the full stored record."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(72)
    node_id = "t.py::test_x"
    failure = _failure(traceback=_SENTINEL_TRACEBACK)
    captured = _captured(stdout="out", stderr="err")
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[_result(node_id, outcome="failed", failure=failure, captured=captured)],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(f"/api/v1/runs/{run_id}/result", params={"node_id": node_id})

    assert response.status_code == 200
    body = response.json()
    assert body["traceback"] == _SENTINEL_TRACEBACK
    assert body["captured_stdout"] == "out"
    assert body["captured_stderr"] == "err"
    assert body["failure_type"] == failure.failure_type
    assert body["failure_message"] == failure.failure_message
    assert body["failure_path"] == failure.failure_path
    assert body["failure_lineno"] == failure.failure_lineno
    assert body["failure_repr"] == failure.failure_repr


def test_result_detail_truncation_flag_travels_with_the_field(
    client: TestClient, store: ExecutionStore
) -> None:
    """A bounded field's truncation flag travels with it on the
    single-result route."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(73)
    node_id = "t.py::test_x"
    failure = _failure(traceback="a truncated traceback", traceback_truncated=True)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[_result(node_id, outcome="failed", failure=failure)],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(f"/api/v1/runs/{run_id}/result", params={"node_id": node_id})

    assert response.status_code == 200
    body = response.json()
    assert body["traceback"] == "a truncated traceback"
    assert body["traceback_truncated"] is True


def test_result_detail_unknown_run_id_is_404_unknown_run_error(client: TestClient) -> None:
    response = client.get(f"/api/v1/runs/{_run_id(999)}/result", params={"node_id": "t.py::test_x"})

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_run"


def test_result_detail_unknown_node_id_is_404_unknown_result_error(
    client: TestClient, store: ExecutionStore
) -> None:
    """A known run with an unknown `node_id` is a distinct error kind from
    `UnknownRunError`."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(74)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(
        f"/api/v1/runs/{run_id}/result", params={"node_id": "t.py::test_never_ran"}
    )

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_result"


def test_result_detail_unknown_identifier_leaves_stored_data_unchanged(
    client: TestClient, store: ExecutionStore
) -> None:
    """An unknown result identifier leaves stored data unchanged. The table
    is read directly via the store, not through the response body -- a 404
    must not create, alter or remove any row."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(75)
    node_id = "t.py::test_x"
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[_result(node_id)],
        received_at=now - timedelta(hours=1),
    )
    before_results = store.get_results(run_id)
    before_detail = store.get_run_detail(run_id)

    response = client.get(
        f"/api/v1/runs/{run_id}/result", params={"node_id": "t.py::test_never_ran"}
    )

    assert response.status_code == 404
    assert store.get_results(run_id) == before_results
    assert store.count_results() == 1
    assert store.get_run_detail(run_id) == before_detail


def test_result_detail_missing_node_id_is_422(client: TestClient) -> None:
    response = client.get(f"/api/v1/runs/{_run_id(76)}/result")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_identity"


def test_every_listed_node_id_is_readable_through_detail_and_history(
    client: TestClient, store: ExecutionStore
) -> None:
    """A stored node id can be longer than `MAX_IDENTITY_CHARS`: pytest
    never shortens a parametrize id, so a test parametrised with a long SQL
    string gets one. Whatever `/results` lists must be readable back by
    that exact value through `/result` and `/tests/history`, or a result's
    traceback and history are stored but unreachable."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(77)
    long_node_id = "tests/test_q.py::test_query[" + "SELECT 1 UNION ALL " * 60 + "]"
    assert len(long_node_id) > MAX_IDENTITY_CHARS
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[
            _result("tests/test_q.py::test_short"),
            _result(
                long_node_id, outcome="failed", failure=_failure(traceback=_SENTINEL_TRACEBACK)
            ),
        ],
        received_at=now - timedelta(hours=1),
    )

    listed = client.get(f"/api/v1/runs/{run_id}/results").json()["items"]

    assert long_node_id in {item["node_id"] for item in listed}
    for item in listed:
        node_id = item["node_id"]
        detail = client.get(f"/api/v1/runs/{run_id}/result", params={"node_id": node_id})
        history = client.get("/api/v1/tests/history", params={"node_id": node_id})
        assert detail.status_code == 200
        assert detail.json()["node_id"] == node_id
        assert history.status_code == 200
        assert [entry["run_id"] for entry in history.json()["items"]] == [run_id]
    long_detail = client.get(f"/api/v1/runs/{run_id}/result", params={"node_id": long_node_id})
    assert long_detail.json()["traceback"] == _SENTINEL_TRACEBACK


def test_result_item_carries_every_stored_column_by_value(
    client: TestClient, store: ExecutionStore
) -> None:
    """A results item carries all sixteen columns, asserted by value. A dict
    equality over the item is the one assertion shape that cannot be
    satisfied by a builder that drops or transposes a column.

    Every value is distinct from every other, including across the four
    outcome columns and the four duration columns, so a transposition fails
    as loudly as a null. That makes the fixture an odd *derivation* --
    `outcome` is `xpassed` over an `xfailed` call phase -- which is
    deliberate: these are four independently recorded columns and this test
    is about whether each reaches the wire as itself, not about whether the
    plugin's derivation is sound (that is `test_result.py`'s subject)."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(60)
    started_at = datetime(2026, 3, 4, 5, 6, 7, 891011, tzinfo=timezone.utc)
    finished_at = started_at + timedelta(seconds=4.25)
    node_id = "tests/test_field_fidelity.py::FidelityGroup::test_every_column[case-7]"
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[
            Result(
                identity=CaseIdentity(
                    node_id=node_id,
                    file_path="tests/test_field_fidelity.py",
                    class_name="FidelityGroup",
                    function_name="test_every_column",
                    param_id="case-7",
                ),
                outcome="xpassed",
                duration=4.25,
                started_at=started_at,
                finished_at=finished_at,
                setup_outcome="passed",
                call_outcome="xfailed",
                teardown_outcome="skipped",
                setup_duration=0.125,
                call_duration=4.0,
                teardown_duration=0.0625,
                worker_id="gw7",
            )
        ],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(f"/api/v1/runs/{run_id}/results")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert _instant(item["started_at"]) == started_at
    assert _instant(item["finished_at"]) == finished_at
    assert {key: value for key, value in item.items() if not key.endswith("ed_at")} == {
        "node_id": node_id,
        "file_path": "tests/test_field_fidelity.py",
        "class_name": "FidelityGroup",
        "function_name": "test_every_column",
        "param_id": "case-7",
        "outcome": "xpassed",
        "duration": 4.25,
        "setup_outcome": "passed",
        "call_outcome": "xfailed",
        "teardown_outcome": "skipped",
        "setup_duration": 0.125,
        "call_duration": 4.0,
        "teardown_duration": 0.0625,
        "worker_id": "gw7",
        "failure": None,
    }


def test_results_route_unknown_run_id_is_404(client: TestClient) -> None:
    """Consistent with run detail's `404` for an unknown run."""
    response = client.get(f"/api/v1/runs/{_run_id(999)}/results")

    assert response.status_code == 404


def test_history_route_returns_newest_first_with_full_vcs(
    client: TestClient, store: ExecutionStore
) -> None:
    """Test history returns executions newest first, each with its full VCS
    context.

    Commit, branch, commit subject, truncation flag, dirty flag and duration
    are asserted *by value* on both entries -- ordering and an exact key set
    alone leave `_history_entry(duration=None)` and
    `_vcs_response(commit=None)` undetectable. The two entries disagree on
    every field, and the four fixture instants are all distinct, so a swap,
    a constant offset or a null fails as loudly as the others."""
    now = datetime.now(timezone.utc)
    node_id = "tests/test_a.py::test_shared"
    older_run = _run_id(21)
    newer_run = _run_id(22)
    older_started_at = now - timedelta(hours=2)
    older_finished_at = now - timedelta(minutes=100)
    newer_started_at = now - timedelta(hours=1)
    newer_finished_at = now - timedelta(minutes=41)
    store.record_session(
        _execution(
            older_run,
            started_at=older_started_at,
            finished_at=older_finished_at,
            vcs=_vcs(
                commit=_HISTORY_OLDER_COMMIT,
                branch="main",
                commit_subject="the older commit subject",
                commit_subject_truncated=True,
                dirty=False,
            ),
        ),
        results=[_result(node_id, duration=1.5)],
        received_at=older_started_at,
    )
    store.record_session(
        _execution(
            newer_run,
            started_at=newer_started_at,
            finished_at=newer_finished_at,
            vcs=_vcs(
                commit=_HISTORY_NEWER_COMMIT,
                branch="feature",
                commit_subject="the newer commit subject",
                commit_subject_truncated=False,
                dirty=True,
            ),
        ),
        results=[_result(node_id, outcome="failed", duration=0.125)],
        received_at=newer_started_at,
    )

    response = client.get("/api/v1/tests/history", params={"node_id": node_id})

    assert response.status_code == 200
    body = response.json()
    assert body["has_more"] is False
    assert [item["run_id"] for item in body["items"]] == [newer_run, older_run]
    for item in body["items"]:
        assert set(item["vcs"].keys()) == {
            "commit",
            "branch",
            "commit_subject",
            "commit_subject_truncated",
            "dirty",
        }
    newer, older = body["items"]
    assert newer["vcs"] == {
        "commit": _HISTORY_NEWER_COMMIT,
        "branch": "feature",
        "commit_subject": "the newer commit subject",
        "commit_subject_truncated": False,
        "dirty": True,
    }
    assert newer["outcome"] == "failed"
    assert newer["duration"] == 0.125
    assert _instant(newer["started_at"]) == newer_started_at
    assert _instant(newer["finished_at"]) == newer_finished_at
    assert older["vcs"] == {
        "commit": _HISTORY_OLDER_COMMIT,
        "branch": "main",
        "commit_subject": "the older commit subject",
        "commit_subject_truncated": True,
        "dirty": False,
    }
    assert older["outcome"] == "passed"
    assert older["duration"] == 1.5
    assert _instant(older["started_at"]) == older_started_at
    assert _instant(older["finished_at"]) == older_finished_at


def test_history_route_unknown_node_id_is_empty_not_error(client: TestClient) -> None:
    """An unknown test yields empty history, not an error."""
    response = client.get(
        "/api/v1/tests/history", params={"node_id": "tests/test_never_ran.py::test_x"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["has_more"] is False


def test_history_route_response_contains_no_vcs_root(
    client: TestClient, store: ExecutionStore
) -> None:
    """The repository root appears in no history entry. **Structurally
    unfalsifiable today**, like the run list counterpart:
    `HistoryEntry.vcs` is a `VcsProjection`, which has no `root` field at
    all. Kept as a regression guard."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(23)
    node_id = "tests/test_a.py::test_leak_guard"
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now, vcs=_vcs()),
        results=[_result(node_id)],
        received_at=now - timedelta(hours=1),
    )

    response = client.get("/api/v1/tests/history", params={"node_id": node_id})

    assert _KNOWN_ROOT not in response.text


def test_history_entry_for_a_non_repository_run_carries_a_null_vcs_key(
    client: TestClient, store: ExecutionStore
) -> None:
    """A run outside a repository still appears in history, with a null VCS
    context rather than an omitted key.

    `"vcs" in entry` and `entry["vcs"] is None` are two assertions because
    they fail for different reasons: an omitted key and a null value are
    exactly the distinction under test, and `entry.get("vcs") is None`
    cannot tell them apart."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(70)
    node_id = "tests/test_outside_a_repository.py::test_runs_anyway"
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now, vcs=None),
        results=[_result(node_id)],
        received_at=now - timedelta(hours=1),
    )

    response = client.get("/api/v1/tests/history", params={"node_id": node_id})

    assert response.status_code == 200
    entry = response.json()["items"][0]
    assert entry["run_id"] == run_id
    assert "vcs" in entry
    assert entry["vcs"] is None


def test_history_identity_survives_special_characters_intact() -> None:
    """**What this test proves**: a node id containing `/`, `::`, `[`, `]`
    (`tests/test_a.py::TestSuite::test_x[case/1]`), sent percent-encoded as
    a query value by the HTTP client, reaches `store.list_history` as the
    identical, un-mangled string -- the query-parameter transport neither
    corrupts nor re-splits the value before the route hands it to the
    store.

    **What this test does NOT prove**: that a query parameter is the
    *correct* routing choice over a `/{identity:path}` path parameter. Both
    round-trip this value intact over a bare ASGI transport; the real
    disqualifier for `:path` is slash normalization by a proxy in front of
    the application (nginx merges slashes by default; Apache 404s on `%2F`
    unless `AllowEncodedSlashes` is on), which an in-process test cannot
    observe either way.
    """
    store = _SpyExecutionStore()
    client = TestClient(create_app(cast(ExecutionStore, store)))
    node_id = "tests/test_a.py::TestSuite::test_x[case/1]"

    response = client.get("/api/v1/tests/history", params={"node_id": node_id})

    assert response.status_code == 200
    assert store.list_history_calls == [node_id]


def test_history_route_missing_node_id_is_422(client: TestClient) -> None:
    response = client.get("/api/v1/tests/history")

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_identity"


def test_absent_repository_run_appears_in_list_undistinguished(
    client: TestClient, store: ExecutionStore
) -> None:
    """A run recorded outside a repository appears in the run list,
    undistinguished in position or omission.

    Asserts the **ordered list**, not a set: a `set` comparison would stay
    green even if an adapter sorted absent-repository runs to one end
    regardless of recency. `absent_run_id` is the newer of the two, so
    ordinary newest-first ordering puts it first; this test only proves
    that placement is not special-cased (ordering itself is
    `test_list_runs_orders_newest_first_with_total_tiebreak`'s job)."""
    now = datetime.now(timezone.utc)
    repo_run_id = _run_id(90)
    absent_run_id = _run_id(91)
    store.record_session(
        _execution(repo_run_id, started_at=now - timedelta(hours=2), finished_at=now, vcs=_vcs()),
        results=[],
        received_at=now - timedelta(hours=2),
    )
    store.record_session(
        _execution(absent_run_id, started_at=now - timedelta(hours=1), finished_at=now, vcs=None),
        results=[],
        received_at=now - timedelta(hours=1),
    )

    response = client.get("/api/v1/runs")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [absent_run_id, repo_run_id]
    by_id = {item["id"]: item for item in items}
    assert by_id[absent_run_id]["vcs"] is None
    assert by_id[repo_run_id]["vcs"] is not None


def test_list_response_carries_the_truncation_flag_beside_its_subject(
    client: TestClient, store: ExecutionStore
) -> None:
    """The truncation flag always travels beside its subject in a list
    response.

    The flag is a *disjunction*: true when the capture itself was truncated
    OR when display bounding shortened the subject here. Both halves are
    asserted on one real body, because `commit_subject_truncated: false`
    beside a subject cut to 120 characters would misrepresent git.
    """
    now = datetime.now(timezone.utc)
    display_bounded_run = _run_id(30)
    capture_truncated_run = _run_id(31)
    short_subject = "short, but the capture itself was cut"
    store.record_session(
        _execution(
            display_bounded_run,
            started_at=now - timedelta(hours=2),
            finished_at=now,
            vcs=_vcs(
                commit=_TRUNCATION_COMMIT,
                commit_subject="L" * 200,
                commit_subject_truncated=False,
            ),
        ),
        results=[],
        received_at=now - timedelta(hours=2),
    )
    store.record_session(
        _execution(
            capture_truncated_run,
            started_at=now - timedelta(hours=1),
            finished_at=now,
            vcs=_vcs(commit_subject=short_subject, commit_subject_truncated=True),
        ),
        results=[],
        received_at=now - timedelta(hours=1),
    )

    response = client.get("/api/v1/runs")

    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()["items"]}

    # Half one: display bounding. The stored subject was not truncated at
    # capture, so the flag is true only because this response shortened it.
    display_bounded = items[display_bounded_run]["vcs"]
    assert display_bounded["commit_subject"] == "L" * LIST_COMMIT_SUBJECT_CHARS
    assert display_bounded["commit_subject_truncated"] is True

    # Half two: capture truncation. Nothing was shortened here -- the
    # subject is well under the display width -- so the flag must have
    # travelled with the subject from the capture.
    capture_truncated = items[capture_truncated_run]["vcs"]
    assert capture_truncated["commit_subject"] == short_subject
    assert capture_truncated["commit_subject_truncated"] is True


def test_run_list_metadata_filter_returns_only_matching_runs(
    client: TestClient, store: ExecutionStore
) -> None:
    """A `key=value` filter returns only matching runs: a run declaring the
    same key at a different value must not match. (The query plan, seeking
    `idx_run_metadata_key_value` on the full pair, is pinned by
    `test_list_runs_by_metadata_uses_the_key_value_index`.)"""
    now = datetime.now(timezone.utc)
    matching_run = _run_id(100)
    other_value_run = _run_id(101)
    store.record_session(
        _execution(matching_run, started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=1),
        metadata=_captured_metadata("firmware_version", "2.1"),
    )
    store.record_session(
        _execution(other_value_run, started_at=now - timedelta(minutes=30), finished_at=now),
        results=[],
        received_at=now - timedelta(minutes=30),
        metadata=_captured_metadata("firmware_version", "3.0"),
    )

    response = client.get(
        "/api/v1/runs", params={"metadata_key": "firmware_version", "metadata_value": "2.1"}
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [matching_run]


def test_run_list_metadata_filter_requires_both_params_together(client: TestClient) -> None:
    """`metadata_key` and `metadata_value` are both or neither -- one
    without the other is `422 invalid_metadata_filter`, naming the missing
    one."""
    key_only = client.get("/api/v1/runs", params={"metadata_key": "firmware_version"})
    value_only = client.get("/api/v1/runs", params={"metadata_value": "2.1"})

    assert key_only.status_code == 422
    assert key_only.json()["error"] == "invalid_metadata_filter"
    assert key_only.json()["fields"] == ["metadata_value"]
    assert value_only.status_code == 422
    assert value_only.json()["error"] == "invalid_metadata_filter"
    assert value_only.json()["fields"] == ["metadata_key"]


def test_run_list_unknown_metadata_key_yields_empty_match_not_an_error(
    client: TestClient, store: ExecutionStore
) -> None:
    """An unknown key or value yields an empty match, not an error."""
    now = datetime.now(timezone.utc)
    store.record_session(
        _execution(_run_id(102), started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(
        "/api/v1/runs", params={"metadata_key": "never_declared", "metadata_value": "anything"}
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_run_list_metadata_horizon_excludes_and_counts_predating_runs(
    client: TestClient, store: ExecutionStore
) -> None:
    """Runs predating the key are excluded from the match and counted in
    the horizon."""
    now = datetime.now(timezone.utc)
    predating_one = _run_id(110)
    predating_two = _run_id(111)
    declared_run = _run_id(112)
    store.record_session(
        _execution(predating_one, started_at=now - timedelta(hours=3), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=3),
    )
    store.record_session(
        _execution(predating_two, started_at=now - timedelta(hours=2), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=2),
    )
    store.record_session(
        _execution(declared_run, started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=1),
        metadata=_captured_metadata("firmware_version", "2.1"),
    )

    response = client.get(
        "/api/v1/runs", params={"metadata_key": "firmware_version", "metadata_value": "2.1"}
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [declared_run]
    assert body["metadata_horizon"] == {"key": "firmware_version", "predating": 2}


def test_run_list_metadata_horizon_equals_total_when_key_never_declared(
    client: TestClient, store: ExecutionStore
) -> None:
    """When no run has ever carried the key, every run predates it, so
    `predating` is the total run count."""
    now = datetime.now(timezone.utc)
    for seed in range(3):
        store.record_session(
            _execution(
                _run_id(120 + seed), started_at=now - timedelta(hours=seed + 1), finished_at=now
            ),
            results=[],
            received_at=now - timedelta(hours=seed + 1),
        )

    response = client.get(
        "/api/v1/runs", params={"metadata_key": "never_declared", "metadata_value": "x"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["metadata_horizon"] == {"key": "never_declared", "predating": 3}


def test_run_list_metadata_horizon_is_null_without_a_filter(
    client: TestClient, store: ExecutionStore
) -> None:
    """A query with no metadata filter has no horizon to report."""
    now = datetime.now(timezone.utc)
    store.record_session(
        _execution(_run_id(130), started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=1),
        metadata=_captured_metadata("firmware_version", "2.1"),
    )

    response = client.get("/api/v1/runs")

    assert response.status_code == 200
    assert response.json()["metadata_horizon"] is None


def test_run_list_metadata_horizon_counts_a_declared_but_dropped_key(
    client: TestClient, store: ExecutionStore
) -> None:
    """The key's first appearance is the earliest run holding ANY
    `run_metadata` row for it, of ANY status. A run whose value exceeded the
    per-value bound still declared the key, so it must not be counted as
    predating it."""
    now = datetime.now(timezone.utc)
    predating_run = _run_id(140)
    dropped_run = _run_id(141)
    store.record_session(
        _execution(predating_run, started_at=now - timedelta(hours=2), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=2),
    )
    store.record_session(
        _execution(dropped_run, started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=1),
        metadata=RunMetadata(
            files=(
                MetadataFile(
                    source_file="config/firmware.yaml", content_type="yaml", status="captured"
                ),
            ),
            entries=(
                MetadataEntry(
                    key="firmware_version",
                    value=None,
                    source_file="config/firmware.yaml",
                    status="value_too_large",
                ),
            ),
        ),
    )

    response = client.get(
        "/api/v1/runs", params={"metadata_key": "firmware_version", "metadata_value": "2.1"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["metadata_horizon"] == {"key": "firmware_version", "predating": 1}
