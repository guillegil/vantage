"""Run list + run detail routes (design.md D57, D59, D61, D62; Phase 4).

Runs the app factory (`vantage.service.app.create_app`) against an injected
`InMemoryExecutionStore`, matching `test_ingestion.py`'s pattern -- the port
(ADR-3) is a real seam, and these tests never wire `SqliteExecutionStore`.

Every fixture in this file constructs `Execution`/`VcsContext` directly and
seeds the store through `record_session`, never through the ingestion route:
`history-read-api` and `session-liveness` describe what the read path
returns, not how a session was reported. Grace-period fixtures (4.8-4.11) set
`last_contact_at` and `create_app`'s `grace_period_seconds` relative to a
`now` the test itself computes -- no clock control (freezegun, `time.sleep`),
matching design.md D62's own claim that the demonstration needs none.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.service.app import create_app
from vantage.storage.memory import InMemoryExecutionStore

_KNOWN_ROOT = "/home/example/very-unique-repo-root-xyz123"


def _run_id(seed: int) -> str:
    """A well-formed 32-lowercase-hex identity, unique per `seed`."""
    return f"{seed:032x}"


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


@pytest.fixture
def store() -> InMemoryExecutionStore:
    return InMemoryExecutionStore()


@pytest.fixture
def client(store: InMemoryExecutionStore) -> TestClient:
    return TestClient(create_app(store))


def _client_with_grace(store: InMemoryExecutionStore, grace_period_seconds: float) -> TestClient:
    return TestClient(create_app(store, grace_period_seconds=grace_period_seconds))


# --- 4.1 --------------------------------------------------------------


def test_run_list_returns_items_and_has_more_envelope(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(history-read-api -> Bounded pagination, list envelope shape)*.
    Each response field is asserted by exact key set -- the shape a
    field-by-field response model produces, never `from_attributes`'s
    incidental extras (inspected in `schemas.py`/`routes/read.py` at review
    time, per task 4.1's own note)."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(1)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now, vcs=_vcs()),
        results=[],
        received_at=now - timedelta(hours=1),
    )

    response = client.get("/api/v1/runs")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "has_more"}
    assert isinstance(body["has_more"], bool)
    assert body["has_more"] is False
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
    assert item["presentation"] == "finished"
    assert set(item["vcs"].keys()) == {
        "commit",
        "branch",
        "commit_subject",
        "commit_subject_truncated",
        "dirty",
    }


# --- 4.2 --------------------------------------------------------------


def test_run_list_response_contains_no_vcs_root_anywhere(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(Lean list projections -> `vcs_root` appears in no run list or run
    detail response)*. A substring assertion on the raw serialized body --
    the only test shape that catches an accidental `from_attributes`
    passthrough of `VcsContext.root`."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(2)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now, vcs=_vcs()),
        results=[],
        received_at=now - timedelta(hours=1),
    )

    response = client.get("/api/v1/runs")

    assert _KNOWN_ROOT not in response.text


# --- 4.3 --------------------------------------------------------------


def test_run_list_rejects_non_positive_limit(client: TestClient) -> None:
    """*(D61 -- "not a page size")*."""
    zero = client.get("/api/v1/runs", params={"limit": 0})
    negative = client.get("/api/v1/runs", params={"limit": -1})

    assert zero.status_code == 422
    assert negative.status_code == 422


# --- 4.4 --------------------------------------------------------------


def test_run_list_caps_at_200_at_the_route(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(Bounded pagination, route level)*. 201 stored runs, no `limit`
    supplied -- the cap must hold through the HTTP layer, not only the
    port (task 4.4's own note)."""
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


# --- 4.5 --------------------------------------------------------------


def test_run_detail_returns_full_untruncated_subject(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(D58, D59 -- the full record stays reachable via run detail)*. A
    200-character stored subject, well past the 120-character list display
    width, must come back whole and untruncated on the detail path."""
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


# --- 4.6 --------------------------------------------------------------


def test_run_detail_response_contains_no_vcs_root(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """Same substring assertion as 4.2, on the detail response body --
    detail carries the full `VcsContext`, which DOES carry `root`; only the
    response model's explicit field-by-field construction keeps it off the
    wire (Phase 4 prompt's own warning)."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(6)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now, vcs=_vcs()),
        results=[],
        received_at=now - timedelta(hours=1),
    )

    response = client.get(f"/api/v1/runs/{run_id}")

    assert _KNOWN_ROOT not in response.text


# --- 4.7 --------------------------------------------------------------


def test_run_detail_unknown_id_is_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/runs/{_run_id(999)}")

    assert response.status_code == 404


# --- 4.8 --------------------------------------------------------------


def test_abandoned_run_reads_back_as_abandoned(store: InMemoryExecutionStore) -> None:
    """*(session-liveness -> Abandoned run is observable -> A run past its
    grace period reads back as abandoned, Demonstration)*. No clock
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


# --- 4.9 --------------------------------------------------------------


def test_running_run_reads_back_as_running(store: InMemoryExecutionStore) -> None:
    """*(session-liveness -> A run inside its grace period reads back as
    running)*."""
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


# --- 4.10 -------------------------------------------------------------


def test_interrupted_run_reads_back_as_interrupted(store: InMemoryExecutionStore) -> None:
    """*(session-liveness -> A Ctrl-C interrupted run reads back as
    interrupted, not abandoned)*. `last_contact_at` is stamped just as
    stale as the abandoned fixture, and the grace period just as short --
    the only difference is `interrupted=True`, which must win regardless
    of staleness."""
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


# --- 4.11 -------------------------------------------------------------


def test_abandonment_invents_no_stored_field(store: InMemoryExecutionStore) -> None:
    """*(session-liveness -> Abandonment invents no stored field)*. Reads
    the row back directly via `store.get_execution`, not through the
    response body -- a derived presentation must not mutate what was
    recorded."""
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
