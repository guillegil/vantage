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
from typing import cast

import pytest
from fastapi.testclient import TestClient
from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.projection import LIST_COMMIT_SUBJECT_CHARS
from vantage.core.domain.result import CaseIdentity, Result
from vantage.core.ports.storage import ExecutionStore, HistoryEntry, Page
from vantage.service.app import create_app
from vantage.storage.memory import InMemoryExecutionStore
from vantage_port_contract import _result

_KNOWN_ROOT = "/home/example/very-unique-repo-root-xyz123"

# Distinct, recognisable 40-hex commits, one per fixture that asserts VCS
# values on the wire. Distinct so a swapped or nulled field fails loudly
# instead of coincidentally matching a neighbour's value or a shared default.
_LIST_COMMIT = "c0ffee01" * 5
_HISTORY_NEWER_COMMIT = "beef0002" * 5
_HISTORY_OLDER_COMMIT = "dead0003" * 5
_TRUNCATION_COMMIT = "face0004" * 5


def _run_id(seed: int) -> str:
    """A well-formed 32-lowercase-hex identity, unique per `seed`."""
    return f"{seed:032x}"


def _instant(wire_value: str) -> datetime:
    """The instant an ISO-8601 timestamp on the wire denotes.

    Compares timestamps by *instant* rather than by string, so these tests
    assert the value the response carries and not Pydantic's chosen spelling
    of it -- a serializer that switched between `+00:00` and `Z` would
    otherwise fail every timestamp assertion for no behavioural reason. The
    `Z` substitution is for Python 3.10, whose `fromisoformat` does not
    accept the military suffix (this project's floor, CLAUDE.md)."""
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


@pytest.fixture
def store() -> InMemoryExecutionStore:
    return InMemoryExecutionStore()


@pytest.fixture
def client(store: InMemoryExecutionStore) -> TestClient:
    return TestClient(create_app(store))


def _client_with_grace(store: InMemoryExecutionStore, grace_period_seconds: float) -> TestClient:
    return TestClient(create_app(store, grace_period_seconds=grace_period_seconds))


class _SpyExecutionStore:
    """A spy exposing only `list_history`, `cast` to `ExecutionStore` at its
    one call site -- task 5.6's route touches no other method, and this
    test asserts nothing about the rest of the port."""

    def __init__(self) -> None:
        self.list_history_calls: list[str] = []

    def list_history(self, *, node_id: str, limit: int, offset: int) -> Page[HistoryEntry]:
        self.list_history_calls.append(node_id)
        return Page(items=(), has_more=False)


# --- 4.1 --------------------------------------------------------------


def test_run_list_returns_items_and_has_more_envelope(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(history-read-api -> Bounded pagination, list envelope shape; and
    Test history -> full VCS context at the list layer)*.

    **Shape and value are different properties and this test holds both.**
    The exact key sets assert the shape a field-by-field response model
    produces, never `from_attributes`'s incidental extras. The value
    assertions then assert that every scalar reaches the wire intact: a
    key-set check alone stays green while `_run_list_item` hardcodes
    `finished_at=None` or `_vcs_response` hardcodes `dirty=None`, which is
    exactly how a field could be silently destroyed on the wire.

    Every fixture value here is deliberately off the default: `started_at`
    and `finished_at` are three distinct instants apart, and `exit_status` is
    `7` rather than the `0` a swap would coincidentally match (verify round
    2, WARNING-1 -- five of this builder's seven fields were mutable with the
    suite green). `presentation` and `interrupted` are asserted on varied
    fixtures by `test_run_list_presentation_and_interruption_are_per_run`,
    which needs runs this one is not."""
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
    store: InMemoryExecutionStore,
) -> None:
    """*(session-liveness -> Abandoned run is observable, at the list layer;
    design.md D62 -- `derive_presentation` gets its first caller.)*

    **The list path calls `derive_presentation` and until verify round 2
    nothing observed that it did.** Every liveness scenario was demonstrated
    through run *detail*, so `_run_list_item` could hardcode
    `presentation="finished"` -- never calling the function whose first
    caller is the entire point of D62 -- and the whole suite stayed green.
    One run per branch of the derivation, in one list response, is what makes
    that mutation fail: a constant cannot be right for four runs at once.

    `interrupted` is asserted here rather than in 4.1 for the same reason a
    constant needs contradicting fixtures: 4.1's run is not interrupted, so
    only a run that *is* can catch the flag being hardcoded false. The
    interrupted run additionally separates the two: it presents as
    `interrupted` while carrying `interrupted: true`, so neither field can be
    derived from the other by accident."""
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


# --- 4.2 --------------------------------------------------------------


def test_run_list_response_contains_no_vcs_root_anywhere(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(Lean list projections -> `vcs_root` appears in no run list or run
    detail response)*. A substring assertion on the raw serialized body.

    **Structurally unfalsifiable, like 5.5.** On the list path the source
    object is a `VcsProjection`, which has no `root` field at all (D59), and
    both adapters additionally strip the context off the entry itself
    (`replace(execution, vcs=None)`) -- `_KNOWN_ROOT` has no code path to
    this body, so this assertion cannot currently fail. Reintroducing `root`
    on `RunVcsResponse` leaves it green. Kept as a regression guard against
    a future list entry that carries a full `VcsContext`, not because it
    proves anything today. **4.6, on the detail path, is the test that
    carries this scenario.**"""
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


def test_run_list_clamps_an_over_cap_limit_rather_than_rejecting_it(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(Bounded pagination -> A list response never exceeds 200 items)*.
    The existing cap test supplies no `limit` at all, so it exercises the
    default and never the branch a caller reaches by asking for more.

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
    """*(Lean list projections -> `vcs_root` appears in no run list or run
    detail response -- the falsifiable half.)*

    **This is the test that carries the scenario.** Same substring shape as
    4.2 and 5.5, but unlike either of them it can actually fail: the detail
    path's source object is the full `VcsContext`, which *does* carry
    `root`, so nothing structural excludes it. The only thing keeping it off
    the wire is `RunVcsResponse` having no `root` field and `_vcs_response`
    naming its five fields explicitly, never
    `model_validate(..., from_attributes=True)`. Add `root` back to
    `RunVcsResponse` and populate it, and this test goes red while 4.2 and
    5.5 stay green."""
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
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(history-read-api -> Test history -> the full record stays reachable
    via run detail; design.md D57.)*

    **Every one of the eight detail fields, asserted by value** (verify round
    2, WARNING-1 -- six of them could be nulled, shifted or replaced with a
    constant while the suite stayed green). Two runs rather than one, because
    a single fixture cannot populate both halves of the record: an orderly
    run carries `finished_at` and no `interrupt_reason`, a Ctrl-C run carries
    the reason and no `finished_at`, and a builder that hardcoded either to
    `None` would still satisfy whichever run happens to be null there.

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
            vcs=_vcs(),
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
    assert orderly["vcs"] is not None

    assert ctrl_c["id"] == ctrl_c_run
    assert _instant(ctrl_c["started_at"]) == ctrl_c_started_at
    assert ctrl_c["finished_at"] is None
    assert ctrl_c["exit_status"] == 2
    assert ctrl_c["interrupted"] is True
    assert ctrl_c["interrupt_reason"] == ctrl_c_reason
    assert ctrl_c["presentation"] == "interrupted"
    assert ctrl_c["vcs"] is None


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


# --- 5.1 --------------------------------------------------------------


def test_results_route_returns_paginated_envelope(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(history-read-api -> Bounded pagination, applied to
    `GET /api/v1/runs/{run_id}/results`)*."""
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
    # No `assert "traceback" not in item` here (task 7.6, history-read-api ->
    # Lean list projections -> Inspection). `Result` has no traceback or
    # captured-output field yet -- nothing this route could serve even if it
    # tried -- so an exclusion assertion on this body would pass whether the
    # exclusion is real or the field simply does not exist, which is not a
    # test, only the appearance of one. This stays Inspection, honestly,
    # until failure capture lands a `traceback` field on `Result`; only then
    # does `ResultItemResponse` omitting it become a claim this test can
    # falsify.


def test_result_item_carries_every_stored_column_by_value(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(history-read-api -> Bounded pagination -> the results envelope's
    item shape; design.md D57 -- `_result_item` is built field by field.)*

    **All sixteen columns, asserted by value.** 5.1 checks `node_id` and
    `outcome`; verify round 2 mutated the other fourteen -- every phase
    outcome, every phase duration, the whole decomposed identity -- and the
    suite stayed green for all of them, including non-null swaps of
    `file_path` and `function_name`. A dict equality over the item is what
    closes that: it is the one assertion shape that cannot be satisfied by a
    builder that drops or transposes a column.

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
    }


# --- 5.2 --------------------------------------------------------------


def test_results_route_unknown_run_id_is_404(client: TestClient) -> None:
    """*(consistent with run detail's 404 behavior, task 4.7)*."""
    response = client.get(f"/api/v1/runs/{_run_id(999)}/results")

    assert response.status_code == 404


# --- 5.3 --------------------------------------------------------------


def test_history_route_returns_newest_first_with_full_vcs(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(history-read-api -> Test history -> Executions return newest
    first, with full VCS context -- route level)*.

    The scenario enumerates six things every entry carries: its commit,
    branch, commit subject, truncation flag, dirty flag, and duration. All
    six are asserted here *by value*, on both entries, with deliberately
    distinct fixtures -- ordering and an exact key set alone leave
    `_history_entry(duration=None)` and `_vcs_response(commit=None)`
    undetectable. The two entries disagree on every field, so a swap between
    them fails as loudly as a null.

    The entry's two timestamps are asserted the same way (verify round 2,
    WARNING-1): the four fixture instants are all distinct, so neither
    `started_at` shifted by a constant offset nor `finished_at` nulled nor
    the pair transposed survives."""
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


# --- 5.4 --------------------------------------------------------------


def test_history_route_unknown_node_id_is_empty_not_error(client: TestClient) -> None:
    """*(Test history -> An unknown test yields empty history, not an
    error -- route level)*."""
    response = client.get(
        "/api/v1/tests/history", params={"node_id": "tests/test_never_ran.py::test_x"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["has_more"] is False


# --- 5.5 --------------------------------------------------------------


def test_history_route_response_contains_no_vcs_root(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(Test history -> `vcs_root` appears in no history entry)*. Same
    substring-on-raw-body shape as 4.2/4.6. **Structurally unfalsifiable,
    like 4.2**: `HistoryEntry.vcs` is a `VcsProjection`, which has no
    `root` field at all (D59) -- no code path could leak `_KNOWN_ROOT`
    here. Kept as a regression guard, not because this can currently fail."""
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
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(version-control-context -> A non-repository execution has a null VCS
    context, not an omitted entry -- route level.)*

    The scenario is written about what a caller reads back, and until verify
    round 2 it was covered only by `test_list_history_null_vcs_entry_present_not_omitted`
    at the port. This closes it through the surface it describes, the same
    way `test_absent_repository_run_appears_in_list_undistinguished` already
    does for the run list. `"vcs" in entry` and `entry["vcs"] is None` are
    two assertions rather than one because they fail for different reasons:
    an omitted key and a null value are exactly the distinction the scenario
    names, and `entry.get("vcs") is None` cannot tell them apart."""
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


# --- 5.6 --------------------------------------------------------------


def test_history_identity_survives_special_characters_intact() -> None:
    """*(history-read-api -> Test history, design.md D54 -- the
    load-bearing wire-encoding test for this change.)*

    **What this test proves**: a node id containing `/`, `::`, `[`, `]`
    (`tests/test_a.py::TestSuite::test_x[case/1]`), sent percent-encoded as
    a query value by the HTTP client, reaches `store.list_history` as the
    identical, un-mangled string -- the query-parameter transport neither
    corrupts nor re-splits the value before the route hands it to the
    store.

    **What this test does NOT prove**: that a query parameter is the
    *correct* routing choice over a `/{identity:path}` path parameter.
    Measured 2026-08-21 against a live uvicorn server (not this
    `TestClient`, which runs over httpx's in-process ASGI transport, not
    uvicorn), both a query parameter and a `/{identity:path}` path
    parameter round-trip this exact value byte-identical under a bare ASGI
    transport -- only a plain `/{identity}` path parameter fails, with
    404. The real disqualifier for `:path` is proxy-dependent slash
    normalization in front of the application (nginx merges/normalises
    slashes by default; Apache 404s on `%2F` unless `AllowEncodedSlashes`
    is on), which an in-process test -- this one included -- structurally
    cannot observe either way. A `TestSuite` name inside this literal node
    id is a string value, not a class definition; it does not trigger
    pytest's `Test*` collection warning (CLAUDE.md).
    """
    store = _SpyExecutionStore()
    client = TestClient(create_app(cast(ExecutionStore, store)))
    node_id = "tests/test_a.py::TestSuite::test_x[case/1]"

    response = client.get("/api/v1/tests/history", params={"node_id": node_id})

    assert response.status_code == 200
    assert store.list_history_calls == [node_id]


# --- 5.7 --------------------------------------------------------------


def test_history_route_missing_node_id_is_422(client: TestClient) -> None:
    response = client.get("/api/v1/tests/history")

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_identity"


# --- 5.8 --------------------------------------------------------------


def test_history_route_overlong_identity_is_422_not_414(client: TestClient) -> None:
    """*(D54's 1,024-character bound -- a shaped 422, never a
    proxy-generated 414)*."""
    overlong = "a" * 1025

    response = client.get("/api/v1/tests/history", params={"node_id": overlong})

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_identity"
    assert overlong not in response.text


# --- 7.9 --------------------------------------------------------------


def test_absent_repository_run_appears_in_list_undistinguished(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(version-control-context -> Absent repository -> Absent repository's
    run appears in the run list)*. Promotes that scenario from Inspection to
    Test, through the live `GET /api/v1/runs` endpoint this change adds --
    the criterion `history-read-api` was deferred to supply.

    Asserts the **ordered list**, not a set (verify round 1, SUGGESTION-2):
    the scenario says the absent-repository run is "in no way distinguished
    in position or omission," and a `set` comparison cannot observe a
    positional difference by construction -- it would stay green even if an
    adapter sorted absent-repository runs to one end regardless of recency.
    `absent_run_id` is the newer of the two, so ordinary newest-first
    ordering (design.md D61) already puts it first; this test's only job is
    to prove that placement is not special-cased for the absent-repository
    case, not to prove ordering exists (`test_list_runs_orders_newest_first_with_total_tiebreak`
    already does that)."""
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


# --- verify round 1 -----------------------------------------------------


def test_list_response_carries_the_truncation_flag_beside_its_subject(
    client: TestClient, store: InMemoryExecutionStore
) -> None:
    """*(history-read-api -> Lean list projections -> The truncation flag
    never surfaces independently of its subject -- response level.)*

    The scenario is written about responses -- "in any response, list or
    detail ... in that same response" -- and until this test the only checks
    were at the port. The flag is a *disjunction* (design.md D60): true when
    the capture itself was truncated OR when display bounding shortened the
    subject here. Both halves are asserted on one real body, because a wire
    that always answers `commit_subject_truncated: false` beside a subject
    cut to 120 characters misrepresents git -- the exact dishonesty the flag
    exists to prevent.
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
