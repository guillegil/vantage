"""The read routes: run list, run detail, results, result detail and test
history.

The run list and a test's history are a project's, under
`/api/v1/projects/{project}/...`; everything addressed by a run id is not,
and its detail says which project the run belongs to.

Runs the app factory (`vantage.service.app.create_app`) against **both**
`ExecutionStore` implementations: the `store` fixture is parametrised, so
every test below executes twice -- once against `InMemoryExecutionStore` and
once against `SqliteExecutionStore`, the adapter that actually ships. Only
the SQLite run catches a query or a row decoder in `sqlite_store.py` that
drops a value on its way to the wire.

Every fixture constructs `Execution`/`VcsContext` directly and seeds the
store through `record_session`, never through the ingestion route: these
tests are about what the read path returns, not how a session was reported.
Grace-period tests set `last_contact_at` and `create_app`'s
`grace_period_seconds` relative to a `now` the test itself computes, so no
clock control (freezegun, `time.sleep`) is needed.
"""

from __future__ import annotations

import importlib.resources
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
import yaml
from fastapi.testclient import TestClient
from vantage.core.domain.access import READ_SCOPE, new_token, token_digest
from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.projection import LIST_COMMIT_SUBJECT_CHARS, LIST_FAILURE_MESSAGE_CHARS
from vantage.core.domain.projects import DEFAULT_PROJECT, Project
from vantage.core.domain.result import CaseIdentity, Result
from vantage.core.ports.storage import (
    MAX_IDENTITY_CHARS,
    ExecutionStore,
    HistoryEntry,
    MetadataEntry,
    MetadataFile,
    Page,
    RunKey,
    RunListEntry,
    RunMetadata,
)
from vantage.service.app import create_app
from vantage_port_contract import _captured, _failure, _result

# `any_store`, for each adapter in turn.
pytest_plugins = ["store_fixtures"]

_OPENAPI_DOCUMENT_BYTES = (
    importlib.resources.files("vantage.service.openapi").joinpath("v1.yaml").read_bytes()
)

_KNOWN_ROOT = "/home/example/very-unique-repo-root-xyz123"

# The run list and a test's history are a project's; every database has
# `default`, where the runs these tests record go unless they say otherwise.
_RUNS = f"/api/v1/projects/{DEFAULT_PROJECT}/runs"
_HISTORY = f"/api/v1/projects/{DEFAULT_PROJECT}/tests/history"
_OTHER_PROJECT = "firmware"

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


def _reader(store: ExecutionStore, user: str) -> dict[str, str]:
    """Make `user` and a read token of theirs, which closes the server, and
    return the header that sends the token."""
    now = datetime.now(timezone.utc)
    store.create_user(user, admin=False, created_at=now)
    token = new_token()
    store.create_token(
        user,
        digest=token_digest(token),
        label="",
        scopes=frozenset({READ_SCOPE}),
        created_at=now,
    )
    return {"Authorization": f"Bearer {token}"}


class _SpyExecutionStore:
    """A spy exposing only what the history and run-list routes call --
    `get_project`, `list_history`, `list_runs`, and `access_required` for a
    store without users -- `cast` to `ExecutionStore` at its call sites.
    It knows `default` alone, and records every call it answers, so a test
    can assert which the route made and which it did not."""

    def __init__(self) -> None:
        self.get_project_calls: list[str] = []
        self.list_history_calls: list[str] = []
        self.list_runs_calls: list[str] = []

    def access_required(self) -> bool:
        return False

    def get_project(self, name: str) -> Project | None:
        self.get_project_calls.append(name)
        if name != DEFAULT_PROJECT:
            return None
        return Project(name=DEFAULT_PROJECT, created_at=datetime.now(timezone.utc))

    def list_history(
        self,
        *,
        project: str,
        node_id: str,
        limit: int,
        offset: int,
        after: RunKey | None = None,
    ) -> Page[HistoryEntry]:
        self.list_history_calls.append(node_id)
        return Page(items=(), has_more=False)

    def list_runs(
        self, *, project: str, limit: int, offset: int, after: RunKey | None = None
    ) -> Page[RunListEntry]:
        self.list_runs_calls.append(project)
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
    than the `0` a swap would coincidentally match, and the run names the
    user who recorded it. `presentation` and `interrupted` are asserted on
    varied fixtures by
    `test_run_list_presentation_and_interruption_are_per_run`."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(1)
    started_at = now - timedelta(hours=1)
    finished_at = now - timedelta(minutes=17)
    authorization = _reader(store, "alice")
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
        recorded_by="alice",
        project=DEFAULT_PROJECT,
    )

    response = client.get(_RUNS, headers=authorization)

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "has_more", "next_cursor", "metadata_horizon"}
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
        "recorded_by",
    }
    assert item["id"] == run_id
    assert item["recorded_by"] == "alice"
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
    )
    client = _client_with_grace(store, grace_period_seconds=60)

    response = client.get(_RUNS)

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
        project=DEFAULT_PROJECT,
    )

    response = client.get(_RUNS)

    assert _KNOWN_ROOT not in response.text


def test_run_list_rejects_non_positive_limit(client: TestClient) -> None:
    """Zero and negative limits are not page sizes."""
    zero = client.get(_RUNS, params={"limit": 0})
    negative = client.get(_RUNS, params={"limit": -1})

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
            project=DEFAULT_PROJECT,
        )

    response = client.get(_RUNS)

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
            project=DEFAULT_PROJECT,
        )

    response = client.get(_RUNS, params={"limit": 500})

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 200
    assert body["has_more"] is True


@pytest.mark.parametrize(
    ("path", "params"),
    [
        (_RUNS, {}),
        (_RUNS, {"metadata_key": "k", "metadata_value": "v"}),
        ("/api/v1/runs/{run_id}/results", {}),
        (_HISTORY, {"node_id": "t.py::test_x"}),
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
        project=DEFAULT_PROJECT,
    )
    url = path.format(run_id=run_id)

    beyond = client.get(url, params={**params, "offset": 2**63})
    at_max = client.get(url, params={**params, "offset": 2**63 - 1})

    assert beyond.status_code == 422
    assert beyond.json()["fields"] == ["query.offset"]
    assert at_max.status_code == 200
    assert at_max.json()["items"] == []
    assert at_max.json()["has_more"] is False


_CURSOR_PATHS = pytest.mark.parametrize(
    ("path", "params", "id_field"),
    [
        (_RUNS, {}, "id"),
        (_RUNS, {"metadata_key": "k", "metadata_value": "v"}, "id"),
        (_HISTORY, {"node_id": "t.py::test_x"}, "run_id"),
    ],
    ids=["runs", "runs-filtered", "history"],
)


def _record_listed_run(store: ExecutionStore, seed: int, started_at: datetime) -> None:
    """A finished run every list in `_CURSOR_PATHS` shows: it holds the
    filtered pair and a result for the history's node id."""
    store.record_session(
        _execution(_run_id(seed), started_at=started_at, finished_at=started_at),
        results=[_result("t.py::test_x")],
        received_at=started_at,
        metadata=_captured_metadata("k", "v"),
        project=DEFAULT_PROJECT,
    )


@_CURSOR_PATHS
def test_following_next_cursor_lists_every_run_once_while_runs_keep_arriving(
    client: TestClient, store: ExecutionStore, path: str, params: dict[str, str], id_field: str
) -> None:
    """Walking a list through `next_cursor` lists every run recorded before
    the walk began exactly once, even when a run is recorded between two
    pages -- where an offset lists a run twice. The last page's
    `next_cursor` is null."""
    base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
    for seed in range(5):
        _record_listed_run(store, seed, base + timedelta(seconds=seed))

    listed: list[str] = []
    body = client.get(path, params={**params, "limit": 2}).json()
    _record_listed_run(store, 9, base + timedelta(minutes=5))
    while True:
        listed += [item[id_field] for item in body["items"]]
        if body["next_cursor"] is None:
            break
        assert body["has_more"] is True
        body = client.get(path, params={**params, "limit": 2, "cursor": body["next_cursor"]}).json()

    assert listed == [_run_id(seed) for seed in (4, 3, 2, 1, 0)]
    assert body["has_more"] is False


@_CURSOR_PATHS
def test_a_page_with_nothing_after_it_has_no_next_cursor(
    client: TestClient, store: ExecutionStore, path: str, params: dict[str, str], id_field: str
) -> None:
    now = datetime.now(timezone.utc)
    _record_listed_run(store, 1, now)

    body = client.get(path, params=params).json()

    assert [item[id_field] for item in body["items"]] == [_run_id(1)]
    assert body["has_more"] is False
    assert body["next_cursor"] is None


@_CURSOR_PATHS
@pytest.mark.parametrize(
    "cursor",
    ["", "not a cursor", "bm90IGEgY3Vyc29y", "x" * 1000],
    ids=["empty", "not-base64", "not-a-key", "oversized"],
)
def test_a_cursor_the_server_did_not_issue_is_422(
    client: TestClient,
    path: str,
    params: dict[str, str],
    id_field: str,
    cursor: str,
) -> None:
    response = client.get(path, params={**params, "cursor": cursor})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_parameter"
    assert response.json()["fields"] == ["query.cursor"]


@_CURSOR_PATHS
def test_a_cursor_with_an_offset_is_422_but_with_offset_zero_is_a_page(
    client: TestClient, store: ExecutionStore, path: str, params: dict[str, str], id_field: str
) -> None:
    """A cursor already says where the page starts, so an offset past it
    would be a second answer to the same question."""
    base = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
    for seed in range(3):
        _record_listed_run(store, seed, base + timedelta(seconds=seed))
    cursor = client.get(path, params={**params, "limit": 1}).json()["next_cursor"]

    both = client.get(path, params={**params, "cursor": cursor, "offset": 1})
    zero = client.get(path, params={**params, "cursor": cursor, "offset": 0})

    assert both.status_code == 422
    assert both.json()["error"] == "invalid_parameter"
    assert both.json()["fields"] == ["query.cursor", "query.offset"]
    assert zero.status_code == 200
    assert [item[id_field] for item in zero.json()["items"]] == [_run_id(1), _run_id(0)]


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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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

    The two runs also disagree on `id`, `started_at`, `exit_status` and who
    recorded them, so `id=<constant>` fails on whichever run it is not, and
    a `started_at` shifted by a fixed offset fails on both."""
    now = datetime.now(timezone.utc)
    authorization = _reader(store, "alice")
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
        recorded_by="alice",
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
    )

    orderly = client.get(f"/api/v1/runs/{orderly_run}", headers=authorization).json()
    ctrl_c = client.get(f"/api/v1/runs/{ctrl_c_run}", headers=authorization).json()

    assert set(orderly.keys()) == {
        "id",
        "started_at",
        "finished_at",
        "exit_status",
        "interrupted",
        "interrupt_reason",
        "presentation",
        "vcs",
        "recorded_by",
        "project",
    }
    assert orderly["recorded_by"] == "alice"
    assert ctrl_c["recorded_by"] is None
    assert orderly["project"] == ctrl_c["project"] == DEFAULT_PROJECT
    assert orderly["id"] == orderly_run
    assert _instant(orderly["started_at"]) == orderly_started_at
    assert _instant(orderly["finished_at"]) == orderly_finished_at
    assert orderly["exit_status"] == 3
    assert orderly["interrupted"] is False
    assert orderly["interrupt_reason"] is None
    assert orderly["presentation"] == "finished"
    # The detail path's five VCS fields, by value: detail is the only route
    # that reads a full `VcsContext` rather than a `VcsProjection`, so
    # nothing else on the wire covers how a stored run's full VCS columns
    # are decoded.
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


def test_run_metadata_returns_every_key_and_file_in_order_with_every_field(
    client: TestClient, store: ExecutionStore
) -> None:
    """File and session keys alike, each field carried by value, and the
    keys of another run left out."""
    now = datetime.now(timezone.utc)
    run_id = _run_id(150)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=1),
        metadata=RunMetadata(
            files=(
                MetadataFile(source_file="fw.yaml", content_type="yaml", status="captured"),
                MetadataFile(
                    source_file="build/manifest.json", content_type="json", status="not_found"
                ),
            ),
            entries=(
                MetadataEntry(
                    key="toolchain",
                    value=None,
                    source_file="build/manifest.json",
                    status="source_unavailable",
                ),
                MetadataEntry(
                    key="fpga.firmware",
                    value="1.1.0",
                    source_file=None,
                    status="captured",
                    source="session",
                    name="FPGA firmware version",
                    declared=True,
                ),
                MetadataEntry(
                    key="firmware_version",
                    value="2.1",
                    source_file="fw.yaml",
                    status="captured",
                    name="Firmware version",
                ),
                MetadataEntry(
                    key="bench",
                    value=None,
                    source_file=None,
                    status="value_too_large",
                    source="session",
                    declared=False,
                ),
            ),
        ),
        project=DEFAULT_PROJECT,
    )
    store.record_session(
        _execution(_run_id(151), started_at=now - timedelta(hours=2), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=2),
        metadata=_captured_metadata("other_run_key", "x"),
        project=DEFAULT_PROJECT,
    )

    response = client.get(f"/api/v1/runs/{run_id}/metadata")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "key": "bench",
                "name": None,
                "value": None,
                "status": "value_too_large",
                "source": "session",
                "source_file": None,
                "declared": False,
            },
            {
                "key": "firmware_version",
                "name": "Firmware version",
                "value": "2.1",
                "status": "captured",
                "source": "file",
                "source_file": "fw.yaml",
                "declared": True,
            },
            {
                "key": "fpga.firmware",
                "name": "FPGA firmware version",
                "value": "1.1.0",
                "status": "captured",
                "source": "session",
                "source_file": None,
                "declared": True,
            },
            {
                "key": "toolchain",
                "name": None,
                "value": None,
                "status": "source_unavailable",
                "source": "file",
                "source_file": "build/manifest.json",
                "declared": True,
            },
        ],
        "files": [
            {"source_file": "build/manifest.json", "content_type": "json", "status": "not_found"},
            {"source_file": "fw.yaml", "content_type": "yaml", "status": "captured"},
        ],
    }


def test_run_metadata_of_a_run_that_reported_none_has_no_items_and_no_files(
    client: TestClient, store: ExecutionStore
) -> None:
    now = datetime.now(timezone.utc)
    run_id = _run_id(152)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=1),
        project=DEFAULT_PROJECT,
    )

    response = client.get(f"/api/v1/runs/{run_id}/metadata")

    assert response.status_code == 200
    assert response.json() == {"items": [], "files": []}


def test_run_metadata_of_an_unknown_run_is_404_unknown_run(client: TestClient) -> None:
    response = client.get(f"/api/v1/runs/{_run_id(999)}/metadata")

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_run"


def test_run_metadata_of_a_malformed_run_id_is_422(client: TestClient) -> None:
    response = client.get("/api/v1/runs/NOT-AN-ID/metadata")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_parameter"
    assert response.json()["fields"] == ["path.run_id"]


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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
    )

    listed = client.get(f"/api/v1/runs/{run_id}/results").json()["items"]

    assert long_node_id in {item["node_id"] for item in listed}
    for item in listed:
        node_id = item["node_id"]
        detail = client.get(f"/api/v1/runs/{run_id}/result", params={"node_id": node_id})
        history = client.get(_HISTORY, params={"node_id": node_id})
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
    )

    response = client.get(_HISTORY, params={"node_id": node_id})

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
    response = client.get(_HISTORY, params={"node_id": "tests/test_never_ran.py::test_x"})

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
        project=DEFAULT_PROJECT,
    )

    response = client.get(_HISTORY, params={"node_id": node_id})

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
        project=DEFAULT_PROJECT,
    )

    response = client.get(_HISTORY, params={"node_id": node_id})

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

    response = client.get(_HISTORY, params={"node_id": node_id})

    assert response.status_code == 200
    assert store.list_history_calls == [node_id]


def test_history_route_missing_node_id_is_422(client: TestClient) -> None:
    response = client.get(_HISTORY)

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
        project=DEFAULT_PROJECT,
    )
    store.record_session(
        _execution(absent_run_id, started_at=now - timedelta(hours=1), finished_at=now, vcs=None),
        results=[],
        received_at=now - timedelta(hours=1),
        project=DEFAULT_PROJECT,
    )

    response = client.get(_RUNS)

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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
    )

    response = client.get(_RUNS)

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
        project=DEFAULT_PROJECT,
    )
    store.record_session(
        _execution(other_value_run, started_at=now - timedelta(minutes=30), finished_at=now),
        results=[],
        received_at=now - timedelta(minutes=30),
        metadata=_captured_metadata("firmware_version", "3.0"),
        project=DEFAULT_PROJECT,
    )

    response = client.get(
        _RUNS, params={"metadata_key": "firmware_version", "metadata_value": "2.1"}
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [matching_run]


def test_run_list_unknown_metadata_key_yields_empty_match_not_an_error(
    client: TestClient, store: ExecutionStore
) -> None:
    """An unknown key or value yields an empty match, not an error."""
    now = datetime.now(timezone.utc)
    store.record_session(
        _execution(_run_id(102), started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=1),
        project=DEFAULT_PROJECT,
    )

    response = client.get(
        _RUNS, params={"metadata_key": "never_declared", "metadata_value": "anything"}
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
        project=DEFAULT_PROJECT,
    )
    store.record_session(
        _execution(predating_two, started_at=now - timedelta(hours=2), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=2),
        project=DEFAULT_PROJECT,
    )
    store.record_session(
        _execution(declared_run, started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now - timedelta(hours=1),
        metadata=_captured_metadata("firmware_version", "2.1"),
        project=DEFAULT_PROJECT,
    )

    response = client.get(
        _RUNS, params={"metadata_key": "firmware_version", "metadata_value": "2.1"}
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [declared_run]
    assert body["metadata_horizon"] == [{"key": "firmware_version", "predating": 2}]


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
            project=DEFAULT_PROJECT,
        )

    response = client.get(_RUNS, params={"metadata_key": "never_declared", "metadata_value": "x"})

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["metadata_horizon"] == [{"key": "never_declared", "predating": 3}]


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
        project=DEFAULT_PROJECT,
    )

    response = client.get(_RUNS)

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
        project=DEFAULT_PROJECT,
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
        project=DEFAULT_PROJECT,
    )

    response = client.get(
        _RUNS, params={"metadata_key": "firmware_version", "metadata_value": "2.1"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["metadata_horizon"] == [{"key": "firmware_version", "predating": 1}]


def _session_metadata(**values: str) -> RunMetadata:
    """Values the session reported itself, with no file behind them."""
    return RunMetadata(
        entries=tuple(
            MetadataEntry(
                key=key, value=value, source_file=None, status="captured", source="session"
            )
            for key, value in values.items()
        )
    )


def test_run_list_metadata_pairs_match_only_runs_holding_every_pair(
    client: TestClient, store: ExecutionStore
) -> None:
    """Each value pairs with the key in the same position, a run must hold
    every pair to match, and a value the session reported matches like one
    read from a file. The horizon has one entry per distinct key, in the
    order first given."""
    now = datetime.now(timezone.utc)
    runs = {
        _run_id(160): RunMetadata(),
        _run_id(161): _session_metadata(fmc="5.2.0"),
        _run_id(162): _session_metadata(fw="1.1.0", fmc="5.2.0"),
        _run_id(163): _session_metadata(fw="1.1.0", fmc="5.3.0"),
        _run_id(164): _session_metadata(fw="5.2.0", fmc="1.1.0"),
    }
    for age, (run_id, metadata) in enumerate(reversed(runs.items())):
        store.record_session(
            _execution(run_id, started_at=now - timedelta(hours=age + 1), finished_at=now),
            results=[],
            received_at=now - timedelta(hours=age + 1),
            metadata=metadata,
            project=DEFAULT_PROJECT,
        )

    response = client.get(
        _RUNS,
        params=[
            ("metadata_key", "fw"),
            ("metadata_key", "fmc"),
            ("metadata_value", "1.1.0"),
            ("metadata_value", "5.2.0"),
            ("metadata_key", "fw"),
            ("metadata_value", "1.1.0"),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [_run_id(162)]
    assert body["metadata_horizon"] == [
        {"key": "fw", "predating": 2},
        {"key": "fmc", "predating": 1},
    ]


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"metadata_key": "fw"}, "query.metadata_value"),
        ({"metadata_value": "1.1.0"}, "query.metadata_key"),
        ({"metadata_key": ["fw", "fmc"], "metadata_value": "1.1.0"}, "query.metadata_value"),
        ({"metadata_key": "fw", "metadata_value": ["1.1.0", "5.2.0"]}, "query.metadata_key"),
    ],
    ids=["key-only", "value-only", "fewer-values", "fewer-keys"],
)
def test_run_list_metadata_keys_and_values_repeated_unequally_are_422(
    client: TestClient, params: dict[str, str | list[str]], field: str
) -> None:
    """A value without its key, or a key without its value, is not a pair.
    `fields` names the parameter given fewer times."""
    response = client.get(_RUNS, params=params)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_parameter"
    assert response.json()["fields"] == [field]


def test_run_list_takes_as_many_metadata_pairs_as_the_document_states_and_no_more(
    client: TestClient,
) -> None:
    """The bound is read from the interface document, so the two cannot
    drift apart."""
    document = yaml.safe_load(_OPENAPI_DOCUMENT_BYTES)
    parameters = {
        parameter["name"]: parameter["schema"]
        for parameter in document["paths"]["/projects/{project}/runs"]["get"]["parameters"]
        if "name" in parameter
    }
    bound = parameters["metadata_key"]["maxItems"]
    assert parameters["metadata_value"]["maxItems"] == bound

    def _pairs(count: int) -> dict[str, list[str]]:
        return {
            "metadata_key": [f"key_{index}" for index in range(count)],
            "metadata_value": ["v"] * count,
        }

    at_bound = client.get(_RUNS, params=_pairs(bound))
    past_bound = client.get(_RUNS, params=_pairs(bound + 1))

    assert at_bound.status_code == 200
    assert len(at_bound.json()["metadata_horizon"]) == bound
    assert past_bound.status_code == 422
    assert past_bound.json()["error"] == "invalid_parameter"
    assert past_bound.json()["fields"] == ["query.metadata_key", "query.metadata_value"]


# --- U+0000 in a lookup ---------------------------------------------------------
#
# Nothing a report stores holds U+0000, and PostgreSQL cannot even be asked
# about one, so a lookup value holding it matches nothing -- never a 500 --
# even in a database an earlier server wrote, where SQLite kept the text as
# it came. Those rows are written straight to the store here; PostgreSQL
# stores U+FFFD in place of U+0000.

_NUL_NODE_ID = "tests/test_nul.py::test_\x00one"


def _record_nul_node_id(store: ExecutionStore, run_id: str) -> None:
    now = datetime.now(timezone.utc)
    store.record_session(
        _execution(run_id, started_at=now - timedelta(minutes=1), finished_at=now),
        results=[_result(_NUL_NODE_ID)],
        received_at=now,
        project=DEFAULT_PROJECT,
    )


def test_a_node_id_holding_nul_names_no_result(client: TestClient, store: ExecutionStore) -> None:
    run_id = _run_id(170)
    _record_nul_node_id(store, run_id)

    response = client.get(f"/api/v1/runs/{run_id}/result", params={"node_id": _NUL_NODE_ID})

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_result"


def test_a_node_id_holding_nul_has_no_history(client: TestClient, store: ExecutionStore) -> None:
    _record_nul_node_id(store, _run_id(171))

    response = client.get(_HISTORY, params={"node_id": _NUL_NODE_ID})

    assert response.status_code == 200
    assert response.json() == {"items": [], "has_more": False, "next_cursor": None}


def test_a_metadata_value_holding_nul_matches_no_run(
    client: TestClient, store: ExecutionStore
) -> None:
    now = datetime.now(timezone.utc)
    store.record_session(
        _execution(_run_id(172), started_at=now - timedelta(hours=1), finished_at=now),
        results=[],
        received_at=now,
        metadata=_session_metadata(fw="1.1\x00"),
        project=DEFAULT_PROJECT,
    )

    response = client.get(_RUNS, params={"metadata_key": "fw", "metadata_value": "1.1\x00"})

    assert response.status_code == 200
    body = response.json()
    assert (body["items"], body["has_more"]) == ([], False)
    assert body["metadata_horizon"] == [{"key": "fw", "predating": 0}]


def test_a_metadata_key_holding_nul_matches_no_run_and_counts_as_stored(
    client: TestClient, store: ExecutionStore
) -> None:
    """The horizon of a key holding U+0000 is that of the text a report
    carrying the key stored, U+0000 replaced by U+FFFD: the runs before that
    one predate it. A key given both ways is one key to the store and two
    in the answer."""
    now = datetime.now(timezone.utc)
    runs = {
        _run_id(173): RunMetadata(),
        _run_id(174): _session_metadata(**{"fw�": "1.1�"}),
    }
    for age, (run_id, metadata) in enumerate(reversed(runs.items())):
        store.record_session(
            _execution(run_id, started_at=now - timedelta(hours=age + 1), finished_at=now),
            results=[],
            received_at=now,
            metadata=metadata,
            project=DEFAULT_PROJECT,
        )

    response = client.get(
        _RUNS,
        params=[
            ("metadata_key", "fw\x00"),
            ("metadata_value", "1.1\x00"),
            ("metadata_key", "fw�"),
            ("metadata_value", "1.1�"),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["items"], body["has_more"]) == ([], False)
    assert body["metadata_horizon"] == [
        {"key": "fw\x00", "predating": 1},
        {"key": "fw�", "predating": 1},
    ]


# --- Projects -------------------------------------------------------------------
#
# The run list and a test's history are read within the project the path
# names. Two projects may hold tests with the same node id, and a list or a
# history that leaked across them would mix two suites' results.


def _project_path(project: str, route: str) -> str:
    """`route` (`runs` or `tests/history`) within `project`."""
    return f"/api/v1/projects/{project}/{route}"


def _add_project(store: ExecutionStore, name: str = _OTHER_PROJECT) -> None:
    store.create_project(name, created_at=datetime.now(timezone.utc))


def _record_in(
    store: ExecutionStore,
    seed: int,
    *,
    project: str,
    started_at: datetime,
    metadata: RunMetadata | None = None,
) -> None:
    """A finished run of `project` with one result, `t.py::test_x`: the same
    node id in every project, so only the project can keep them apart."""
    store.record_session(
        _execution(_run_id(seed), started_at=started_at, finished_at=started_at),
        results=[_result("t.py::test_x")],
        received_at=started_at,
        metadata=metadata if metadata is not None else RunMetadata(),
        project=project,
    )


def test_run_list_and_history_see_only_their_project(
    client: TestClient, store: ExecutionStore
) -> None:
    """Each project's run list and each project's history of one node id
    hold that project's runs and no other's, whichever is newer."""
    now = datetime.now(timezone.utc)
    _add_project(store)
    _record_in(store, 200, project=DEFAULT_PROJECT, started_at=now - timedelta(hours=3))
    _record_in(store, 201, project=_OTHER_PROJECT, started_at=now - timedelta(hours=2))
    _record_in(store, 202, project=DEFAULT_PROJECT, started_at=now - timedelta(hours=1))
    node = {"node_id": "t.py::test_x"}

    default_runs = client.get(_project_path(DEFAULT_PROJECT, "runs")).json()["items"]
    other_runs = client.get(_project_path(_OTHER_PROJECT, "runs")).json()["items"]
    default_history = client.get(_project_path(DEFAULT_PROJECT, "tests/history"), params=node)
    other_history = client.get(_project_path(_OTHER_PROJECT, "tests/history"), params=node)

    assert [item["id"] for item in default_runs] == [_run_id(202), _run_id(200)]
    assert [item["id"] for item in other_runs] == [_run_id(201)]
    assert [item["run_id"] for item in default_history.json()["items"]] == [
        _run_id(202),
        _run_id(200),
    ]
    assert [item["run_id"] for item in other_history.json()["items"]] == [_run_id(201)]


def test_metadata_filter_and_horizon_are_per_project(
    client: TestClient, store: ExecutionStore
) -> None:
    """A filter matches the project's runs alone, and a key's horizon counts
    the project's runs before the project's first run holding it. The runs
    are interleaved so that counting across projects, from either project's
    first appearance of the key, gives another number."""
    now = datetime.now(timezone.utc)
    _add_project(store)
    pair = _captured_metadata("firmware_version", "2.1")
    _record_in(store, 210, project=_OTHER_PROJECT, started_at=now - timedelta(hours=6))
    _record_in(store, 211, project=_OTHER_PROJECT, started_at=now - timedelta(hours=5))
    _record_in(store, 212, project=DEFAULT_PROJECT, started_at=now - timedelta(hours=4))
    _record_in(
        store, 213, project=DEFAULT_PROJECT, started_at=now - timedelta(hours=3), metadata=pair
    )
    _record_in(
        store, 214, project=_OTHER_PROJECT, started_at=now - timedelta(hours=2), metadata=pair
    )
    params = {"metadata_key": "firmware_version", "metadata_value": "2.1"}

    default = client.get(_project_path(DEFAULT_PROJECT, "runs"), params=params).json()
    other = client.get(_project_path(_OTHER_PROJECT, "runs"), params=params).json()

    assert [item["id"] for item in default["items"]] == [_run_id(213)]
    assert default["metadata_horizon"] == [{"key": "firmware_version", "predating": 1}]
    assert [item["id"] for item in other["items"]] == [_run_id(214)]
    assert other["metadata_horizon"] == [{"key": "firmware_version", "predating": 2}]


@pytest.mark.parametrize(
    ("route", "params", "id_field"),
    [
        ("runs", {}, "id"),
        ("runs", {"metadata_key": "k", "metadata_value": "v"}, "id"),
        ("tests/history", {"node_id": "t.py::test_x"}, "run_id"),
    ],
    ids=["runs", "runs-filtered", "history"],
)
def test_a_cursor_from_another_project_pages_harmlessly(
    client: TestClient, store: ExecutionStore, route: str, params: dict[str, str], id_field: str
) -> None:
    """A cursor names a place in the order every list shares, not a run of
    one project, so one another project's list handed out starts this
    project's page just past that place and lists this project's runs
    alone."""
    now = datetime.now(timezone.utc)
    _add_project(store)
    pair = _captured_metadata("k", "v")
    for seed, project, hours in (
        (220, DEFAULT_PROJECT, 0.5),
        (221, _OTHER_PROJECT, 1),
        (222, DEFAULT_PROJECT, 2),
        (223, _OTHER_PROJECT, 3),
        (224, DEFAULT_PROJECT, 4),
    ):
        _record_in(
            store, seed, project=project, started_at=now - timedelta(hours=hours), metadata=pair
        )
    other_page = client.get(
        _project_path(_OTHER_PROJECT, route), params={**params, "limit": 1}
    ).json()
    assert [item[id_field] for item in other_page["items"]] == [_run_id(221)]

    response = client.get(
        _project_path(DEFAULT_PROJECT, route),
        params={**params, "cursor": other_page["next_cursor"]},
    )

    assert response.status_code == 200
    assert [item[id_field] for item in response.json()["items"]] == [_run_id(222), _run_id(224)]
    assert response.json()["next_cursor"] is None


_PROJECT_ROUTES = pytest.mark.parametrize(
    ("route", "params"),
    [("runs", {}), ("tests/history", {"node_id": "t.py::test_x"})],
    ids=["runs", "history"],
)


@_PROJECT_ROUTES
def test_a_project_nobody_made_is_404_unknown_project(
    client: TestClient, store: ExecutionStore, route: str, params: dict[str, str]
) -> None:
    """A name a project could have but none does is `404 unknown_project`,
    not an empty page: an empty page would read as a project that has not
    recorded yet. The name is not repeated back."""
    _add_project(store)

    response = client.get(_project_path("never-made", route), params=params)

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_project"
    assert "never-made" not in response.text


@_PROJECT_ROUTES
@pytest.mark.parametrize(
    "name",
    ["Firmware", "-firmware", ".firmware", "x" * 65, "fw%20bench", "fw%00"],
    ids=["upper-case", "leading-dash", "leading-dot", "too-long", "space", "nul"],
)
def test_a_name_no_project_can_have_is_404_unknown_project_without_asking_the_store(
    route: str, params: dict[str, str], name: str
) -> None:
    """A name the project rule refuses cannot name a project, so it is
    answered as an unknown one without a store call -- which also keeps
    U+0000 from every adapter. A name the rule accepts is looked up, so the
    spy tells the two apart."""
    store = _SpyExecutionStore()
    client = TestClient(create_app(cast(ExecutionStore, store)))

    impossible = client.get(_project_path(name, route), params=params)
    unknown = client.get(_project_path("firmware", route), params=params)

    assert impossible.status_code == 404
    assert impossible.json()["error"] == "unknown_project"
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "unknown_project"
    assert store.get_project_calls == ["firmware"]
    assert store.list_runs_calls == []
    assert store.list_history_calls == []


@_PROJECT_ROUTES
@pytest.mark.parametrize("name", ["never-made", "Not-A-Name"], ids=["unknown", "impossible"])
def test_on_a_closed_server_a_caller_without_a_token_learns_nothing_of_projects(
    store: ExecutionStore, route: str, params: dict[str, str], name: str
) -> None:
    """The project is resolved after the caller is authorized, so a caller
    who may not read gets `401` whether or not the project exists or could,
    and only a reader learns that it does not."""
    client = TestClient(create_app(store))
    authorization = _reader(store, "alice")

    anonymous = client.get(_project_path(name, route), params=params)
    reader = client.get(_project_path(name, route), params=params, headers=authorization)

    assert anonymous.status_code == 401
    assert anonymous.json()["error"] == "unauthenticated"
    assert reader.status_code == 404
    assert reader.json()["error"] == "unknown_project"


def test_the_run_list_and_history_are_no_longer_served_outside_a_project(
    client: TestClient,
) -> None:
    """`/runs` still takes a report, so reading it is a method the path
    does not take -- `405`, naming the one it does -- while
    `/tests/history` is no path at all. Neither answers with some
    project's runs, which a client written for the old paths would read as
    the whole database."""
    runs = client.get("/api/v1/runs")
    history = client.get("/api/v1/tests/history", params={"node_id": "t.py::test_x"})

    assert runs.status_code == 405
    assert runs.headers["allow"] == "POST"
    assert runs.json()["error"] == "method_not_allowed"
    assert history.status_code == 404
    assert history.json()["error"] == "not_found"


def test_run_detail_names_the_project_the_run_was_recorded_in(
    client: TestClient, store: ExecutionStore
) -> None:
    """A run id names one run across projects, so its detail says which
    project to ask for the history of its tests. Two runs in two projects,
    so a constant fails on one of them."""
    now = datetime.now(timezone.utc)
    _add_project(store)
    _record_in(store, 230, project=DEFAULT_PROJECT, started_at=now - timedelta(hours=2))
    _record_in(store, 231, project=_OTHER_PROJECT, started_at=now - timedelta(hours=1))

    default = client.get(f"/api/v1/runs/{_run_id(230)}")
    other = client.get(f"/api/v1/runs/{_run_id(231)}")

    assert default.status_code == other.status_code == 200
    assert default.json()["project"] == DEFAULT_PROJECT
    assert other.json()["project"] == _OTHER_PROJECT
