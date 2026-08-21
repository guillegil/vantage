"""Run list route tests (design.md D57, D59, D61, D62; Phase 4a of PR4).

Runs the app factory (`vantage.service.app.create_app`) against an injected
`InMemoryExecutionStore`, matching `test_ingestion.py`'s pattern -- the port
(ADR-3) is a real seam, and these tests never wire `SqliteExecutionStore`.

Every fixture in this file constructs `Execution`/`VcsContext` directly and
seeds the store through `record_session`, never through the ingestion route:
`history-read-api` describes what the read path returns, not how a session
was reported.
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
