"""What a role in a project adds to a token's scope (`service/access.py`).

Within a project a request needs a role there as well as its scope --
viewer to read, editor to record and to change section definitions, owner
to manage members -- as `effective_role` gives it: owner of every project
for an admin, editor of `default` for every user, otherwise the user's
member row. An authenticated caller with no role in the project is
`403 not_a_member`, one whose role is below the route's
`403 insufficient_role`; neither carries a challenge or names the project.
A server with no user checks no role.

Every test runs against every adapter (`any_store`): the rule is the
service's, but each answer rests on the users, member rows and runs the
adapter holds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from vantage.core.domain.access import (
    ADMIN_SCOPE,
    MANAGE_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    SCOPES,
    new_token,
    token_digest,
)
from vantage.core.domain.projects import (
    DEFAULT_PROJECT,
    EDITOR_ROLE,
    OWNER_ROLE,
    VIEWER_ROLE,
)
from vantage.core.ports.storage import ExecutionStore
from vantage.service.app import create_app
from vantage.service.routes.sections import TEST_SECTIONS_NAMESPACE
from vantage_port_contract import _result, _start_only_execution

# `any_store`, each adapter in turn.
pytest_plugins = ["store_fixtures"]

_NOW = datetime(2026, 9, 28, 9, 0, 0, tzinfo=timezone.utc)
_PROJECT = "firmware"
_RUN = "a" * 32
_UNKNOWN_RUN = "0" * 32
_NODE = "tests/test_a.py::test_one"
_SECTION = "Api"
_EVERY_SCOPE_BUT_ADMIN = tuple(sorted(SCOPES - {ADMIN_SCOPE}))
_NOT_A_MEMBER = "You are not a member of this project."


def _report(
    run_id: str = _RUN, *, project: str | None = _PROJECT, finished: bool = True
) -> dict[str, Any]:
    """A report of `run_id` with one result, `_NODE`, naming `project`, or
    no project with None."""
    report: dict[str, Any] = {
        "run": {
            "id": run_id,
            "started_at": "2026-09-28T09:00:00+00:00",
            "finished_at": "2026-09-28T09:00:05+00:00" if finished else None,
            "exit_status": 0 if finished else None,
            "interrupted": False,
            "interrupt_reason": None,
        },
        "results": [
            {
                "node_id": _NODE,
                "file_path": "tests/test_a.py",
                "class_name": None,
                "function_name": "test_one",
                "param_id": None,
                "outcome": "passed",
                "duration": 0.003,
                "started_at": "2026-09-28T09:00:01+00:00",
                "finished_at": "2026-09-28T09:00:01.003000+00:00",
                "setup_outcome": "passed",
                "call_outcome": "passed",
                "teardown_outcome": "passed",
                "setup_duration": 0.001,
                "call_duration": 0.001,
                "teardown_duration": 0.001,
                "worker_id": None,
            }
        ],
    }
    if project is not None:
        report["project"] = project
    return report


def _user(store: ExecutionStore, name: str, *, admin: bool = False) -> None:
    store.create_user(name, admin=admin, created_at=_NOW)


def _bearer(store: ExecutionStore, user: str, *scopes: str) -> dict[str, str]:
    """A new token of `user` holding `scopes`, as the header that sends it."""
    token = new_token()
    store.create_token(
        user,
        digest=token_digest(token),
        label="",
        scopes=frozenset(scopes),
        created_at=_NOW,
    )
    return {"Authorization": f"Bearer {token}"}


def _seed(store: ExecutionStore, *, recorded_by: str | None) -> None:
    """`_PROJECT`, holding `_RUN`, a run still going that `recorded_by`
    recorded there with one result, `_NODE`, and one section, `_SECTION`."""
    store.create_project(_PROJECT, created_at=_NOW)
    store.record_session(
        _start_only_execution(_RUN),
        results=[_result(_NODE)],
        received_at=_NOW,
        recorded_by=recorded_by,
        project=_PROJECT,
    )
    store.upsert_setting(
        TEST_SECTIONS_NAMESPACE,
        _SECTION,
        value='{"prefix": "tests/api/"}',
        updated_at=_NOW,
        project=_PROJECT,
    )


def _state(store: ExecutionStore) -> tuple[object, ...]:
    """Everything a route here could change: the run, its results and how
    many runs there are, and the project's sections and members."""
    return (
        store.get_run_detail(_RUN),
        store.get_results(_RUN),
        store.count_executions(),
        store.list_settings(TEST_SECTIONS_NAMESPACE, project=_PROJECT),
        store.list_members(project=_PROJECT),
    )


def _assert_refused(response: Any, error: str, detail: str) -> None:
    """A `403` for the caller's role: no challenge, since no other token of
    the same user would help, and nothing naming the project."""
    assert response.status_code == 403, response.text
    assert response.json() == {"error": error, "detail": detail, "fields": []}
    assert "WWW-Authenticate" not in response.headers
    assert _PROJECT not in response.text


def _insufficient(role: str) -> str:
    return f"This needs the {role} role in this project, and yours is below it."


def _listed(response: Any) -> list[tuple[str, str | None]]:
    assert response.status_code == 200, response.text
    return [(item["name"], item["role"]) for item in response.json()["items"]]


# Every route that acts within one project -- the project in its path, the
# run in its path, or, for `create_run`, the project its report names --
# keyed by the operation id the document gives it, with the role it needs
# of a user who is not an admin.
_SECTIONS = f"/api/v1/projects/{_PROJECT}/config/sections"
_MEMBERS = f"/api/v1/projects/{_PROJECT}/members"
_WITHIN_A_PROJECT: dict[str, tuple[str, str, dict[str, Any], str]] = {
    "list_runs": ("GET", f"/api/v1/projects/{_PROJECT}/runs", {}, VIEWER_ROLE),
    "list_history": (
        "GET",
        f"/api/v1/projects/{_PROJECT}/tests/history",
        {"params": {"node_id": _NODE}},
        VIEWER_ROLE,
    ),
    "list_sections": ("GET", _SECTIONS, {}, VIEWER_ROLE),
    "upsert_section": (
        "POST",
        _SECTIONS,
        {"json": {"name": _SECTION, "prefix": "tests/other"}},
        EDITOR_ROLE,
    ),
    "delete_section": ("DELETE", _SECTIONS, {"params": {"name": _SECTION}}, EDITOR_ROLE),
    "list_members": ("GET", _MEMBERS, {}, VIEWER_ROLE),
    "set_member": ("PUT", f"{_MEMBERS}/carol", {"json": {"role": VIEWER_ROLE}}, OWNER_ROLE),
    "remove_member": ("DELETE", f"{_MEMBERS}/carol", {}, OWNER_ROLE),
    "get_run_detail": ("GET", f"/api/v1/runs/{_RUN}", {}, VIEWER_ROLE),
    "get_run_metadata": ("GET", f"/api/v1/runs/{_RUN}/metadata", {}, VIEWER_ROLE),
    "list_results": ("GET", f"/api/v1/runs/{_RUN}/results", {}, VIEWER_ROLE),
    "list_changes": ("GET", f"/api/v1/runs/{_RUN}/changes", {}, VIEWER_ROLE),
    "get_run_outcomes": ("GET", f"/api/v1/runs/{_RUN}/outcomes", {}, VIEWER_ROLE),
    "get_result": (
        "GET",
        f"/api/v1/runs/{_RUN}/result",
        {"params": {"node_id": _NODE}},
        VIEWER_ROLE,
    ),
    "get_run_sections": ("GET", f"/api/v1/runs/{_RUN}/sections", {}, VIEWER_ROLE),
    "heartbeat": ("POST", f"/api/v1/runs/{_RUN}/heartbeat", {}, EDITOR_ROLE),
    "create_run": ("POST", "/api/v1/runs", {"json": _report()}, EDITOR_ROLE),
}

# The role one rank below each a route may need.
_BELOW = {EDITOR_ROLE: VIEWER_ROLE, OWNER_ROLE: EDITOR_ROLE}
_ABOVE_VIEWER = {
    operation: request for operation, request in _WITHIN_A_PROJECT.items() if request[3] in _BELOW
}


def _nowhere(path: str, request_kwargs: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """The same request of a project, or a run, nobody has, with the error
    that answers it."""
    if path == "/api/v1/runs":
        return path, {"json": _report(project="ghost")}, "unknown_project"
    if f"/runs/{_RUN}" in path:
        return path.replace(_RUN, _UNKNOWN_RUN), request_kwargs, "unknown_run"
    return path.replace(_PROJECT, "ghost"), request_kwargs, "unknown_project"


# --- Listing projects ------------------------------------------------------------------


def test_the_project_list_holds_the_projects_the_caller_has_a_role_in(
    any_store: ExecutionStore,
) -> None:
    """An admin sees every project, as its owner, whatever rows they have;
    anyone else sees `default`, as its editor, and each project a row of
    theirs names, with the row's role; a user with no row, `default`
    alone."""
    for name in ("zephyr", "firmware", "boards"):
        any_store.create_project(name, created_at=_NOW)
    _user(any_store, "alice", admin=True)
    for name in ("bob", "carol"):
        _user(any_store, name)
    any_store.set_member("alice", project="boards", role=VIEWER_ROLE)
    any_store.set_member("bob", project="firmware", role=VIEWER_ROLE)
    any_store.set_member("bob", project="zephyr", role=OWNER_ROLE)
    client = TestClient(create_app(any_store))

    admin, member, stranger = (
        client.get("/api/v1/projects", headers=_bearer(any_store, name, READ_SCOPE))
        for name in ("alice", "bob", "carol")
    )

    assert _listed(admin) == [
        ("boards", OWNER_ROLE),
        (DEFAULT_PROJECT, OWNER_ROLE),
        ("firmware", OWNER_ROLE),
        ("zephyr", OWNER_ROLE),
    ]
    assert _listed(member) == [
        (DEFAULT_PROJECT, EDITOR_ROLE),
        ("firmware", VIEWER_ROLE),
        ("zephyr", OWNER_ROLE),
    ]
    assert _listed(stranger) == [(DEFAULT_PROJECT, EDITOR_ROLE)]


def test_a_server_with_no_user_lists_every_project_with_no_role(
    any_store: ExecutionStore,
) -> None:
    any_store.create_project("boards", created_at=_NOW)

    response = TestClient(create_app(any_store)).get("/api/v1/projects")

    assert _listed(response) == [("boards", None), (DEFAULT_PROJECT, None)]


def test_an_admin_who_stops_being_one_lists_what_their_rows_give(
    any_store: ExecutionStore,
) -> None:
    """Whether the caller is an admin is read with their token on every
    request, so the list shrinks at once to what a user of their rows sees."""
    for name in ("boards", "firmware"):
        any_store.create_project(name, created_at=_NOW)
    _user(any_store, "alice", admin=True)
    any_store.set_member("alice", project="boards", role=VIEWER_ROLE)
    headers = _bearer(any_store, "alice", READ_SCOPE)
    client = TestClient(create_app(any_store))

    as_admin = client.get("/api/v1/projects", headers=headers)
    any_store.update_user("alice", admin=False)
    demoted = client.get("/api/v1/projects", headers=headers)

    assert [role for _, role in _listed(as_admin)] == [OWNER_ROLE] * 3
    assert _listed(demoted) == [("boards", VIEWER_ROLE), (DEFAULT_PROJECT, EDITOR_ROLE)]


# --- Who may act within a project ------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs", "role"),
    _WITHIN_A_PROJECT.values(),
    ids=_WITHIN_A_PROJECT,
)
def test_a_user_with_no_role_in_the_project_is_403_not_a_member(
    any_store: ExecutionStore,
    method: str,
    path: str,
    request_kwargs: dict[str, Any],
    role: str,
) -> None:
    """Whatever the route, with every scope it could need, and whatever it
    would have answered next -- a run of another user, a section or a
    member to change -- the answer is the same, names nothing of the
    project, and changes nothing."""
    for name in ("carol", "bob"):
        _user(any_store, name)
    _seed(any_store, recorded_by="carol")
    any_store.set_member("carol", project=_PROJECT, role=OWNER_ROLE)
    headers = _bearer(any_store, "bob", *_EVERY_SCOPE_BUT_ADMIN)
    before = _state(any_store)

    response = TestClient(create_app(any_store)).request(
        method, path, headers=headers, **request_kwargs
    )

    _assert_refused(response, "not_a_member", _NOT_A_MEMBER)
    assert _state(any_store) == before


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs", "role"),
    _ABOVE_VIEWER.values(),
    ids=_ABOVE_VIEWER,
)
def test_a_role_below_the_routes_is_403_insufficient_role(
    any_store: ExecutionStore,
    method: str,
    path: str,
    request_kwargs: dict[str, Any],
    role: str,
) -> None:
    """One rank below: a viewer where recording or changing sections needs
    an editor, an editor where managing members needs an owner. It is
    refused before the store is asked about another user's run, so a
    report or a heartbeat of one is `403`, not `409 foreign_run`."""
    for name in ("carol", "bob"):
        _user(any_store, name)
    _seed(any_store, recorded_by="carol")
    any_store.set_member("carol", project=_PROJECT, role=OWNER_ROLE)
    any_store.set_member("bob", project=_PROJECT, role=_BELOW[role])
    headers = _bearer(any_store, "bob", *_EVERY_SCOPE_BUT_ADMIN)
    before = _state(any_store)

    response = TestClient(create_app(any_store)).request(
        method, path, headers=headers, **request_kwargs
    )

    _assert_refused(response, "insufficient_role", _insufficient(role))
    assert _state(any_store) == before


# The routes of `_WITHIN_A_PROJECT` that check a query, a body or a path
# value of their own, each with one their own check would refuse: the
# caller's role is refused first, so a caller who may not act there learns
# nothing of what the route takes. A report is the exception, refused for
# its shape before its project is read from it.
_MALFORMED: dict[str, tuple[str, str, dict[str, Any], str]] = {
    "list_runs": (
        "GET",
        f"/api/v1/projects/{_PROJECT}/runs",
        {"params": {"limit": "x"}},
        VIEWER_ROLE,
    ),
    "list_history": ("GET", f"/api/v1/projects/{_PROJECT}/tests/history", {}, VIEWER_ROLE),
    "upsert_section": (
        "POST",
        _SECTIONS,
        {"content": b"name=Api", "headers": {"Content-Type": "text/plain"}},
        EDITOR_ROLE,
    ),
    "upsert_section-shape": ("POST", _SECTIONS, {"json": {"name": 1}}, EDITOR_ROLE),
    "delete_section": ("DELETE", _SECTIONS, {}, EDITOR_ROLE),
    "set_member": ("PUT", f"{_MEMBERS}/carol", {"json": {"role": "king"}}, OWNER_ROLE),
    "remove_member": ("DELETE", f"{_MEMBERS}/NOT-A-NAME", {}, OWNER_ROLE),
    "list_results": (
        "GET",
        f"/api/v1/runs/{_RUN}/results",
        {"params": {"offset": "-1"}},
        VIEWER_ROLE,
    ),
    "list_changes": (
        "GET",
        f"/api/v1/runs/{_RUN}/changes",
        {"params": {"change": "flaky"}},
        VIEWER_ROLE,
    ),
    "get_result": ("GET", f"/api/v1/runs/{_RUN}/result", {}, VIEWER_ROLE),
}
_MALFORMED_ABOVE_VIEWER = {
    operation: request for operation, request in _MALFORMED.items() if request[3] in _BELOW
}


def _merged_headers(headers: dict[str, str], request_kwargs: dict[str, Any]) -> dict[str, Any]:
    """`request_kwargs` with `headers` added to any headers it sends."""
    return {**request_kwargs, "headers": {**headers, **request_kwargs.get("headers", {})}}


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs", "role"), _MALFORMED.values(), ids=_MALFORMED
)
def test_a_non_member_is_403_before_the_route_looks_at_what_was_sent(
    any_store: ExecutionStore,
    method: str,
    path: str,
    request_kwargs: dict[str, Any],
    role: str,
) -> None:
    for name in ("carol", "bob"):
        _user(any_store, name)
    _seed(any_store, recorded_by="carol")
    headers = _bearer(any_store, "bob", *_EVERY_SCOPE_BUT_ADMIN)
    client = TestClient(create_app(any_store))
    before = _state(any_store)

    refused = client.request(method, path, **_merged_headers(headers, request_kwargs))

    _assert_refused(refused, "not_a_member", _NOT_A_MEMBER)
    assert _state(any_store) == before
    # The same request from a member is refused for what it sent.
    any_store.set_member("bob", project=_PROJECT, role=OWNER_ROLE)
    malformed = client.request(method, path, **_merged_headers(headers, request_kwargs))
    assert 400 <= malformed.status_code < 500 and malformed.status_code != 403, malformed.text


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs", "role"),
    _MALFORMED_ABOVE_VIEWER.values(),
    ids=_MALFORMED_ABOVE_VIEWER,
)
def test_a_role_below_the_routes_is_403_before_the_route_looks_at_what_was_sent(
    any_store: ExecutionStore,
    method: str,
    path: str,
    request_kwargs: dict[str, Any],
    role: str,
) -> None:
    for name in ("carol", "bob"):
        _user(any_store, name)
    _seed(any_store, recorded_by="carol")
    any_store.set_member("bob", project=_PROJECT, role=_BELOW[role])
    headers = _bearer(any_store, "bob", *_EVERY_SCOPE_BUT_ADMIN)
    before = _state(any_store)

    refused = TestClient(create_app(any_store)).request(
        method, path, **_merged_headers(headers, request_kwargs)
    )

    _assert_refused(refused, "insufficient_role", _insufficient(role))
    assert _state(any_store) == before


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs", "role"),
    _WITHIN_A_PROJECT.values(),
    ids=_WITHIN_A_PROJECT,
)
def test_a_project_or_run_nobody_has_is_404_to_a_non_member_too(
    any_store: ExecutionStore,
    method: str,
    path: str,
    request_kwargs: dict[str, Any],
    role: str,
) -> None:
    """The project, or the run whose project it is, is found before the
    caller's role in it is asked, so a missing one is a `404` whoever
    asks. A run id is a random uuid4, so answering `unknown_run` before a
    `403` reveals nothing guessable."""
    _user(any_store, "bob")
    _seed(any_store, recorded_by=None)
    headers = _bearer(any_store, "bob", *_EVERY_SCOPE_BUT_ADMIN)
    path, request_kwargs, error = _nowhere(path, request_kwargs)

    response = TestClient(create_app(any_store)).request(
        method, path, headers=headers, **request_kwargs
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"] == error


def test_a_result_a_run_lacks_is_403_to_a_non_member_not_unknown_result(
    any_store: ExecutionStore,
) -> None:
    """Whether a run holds a result is the run's to tell, so only a caller
    who may read the run learns it."""
    for name in ("carol", "bob"):
        _user(any_store, name)
    _seed(any_store, recorded_by="carol")
    headers = _bearer(any_store, "bob", READ_SCOPE)
    client = TestClient(create_app(any_store))
    params = {"node_id": "tests/test_z.py::test_never_ran"}

    stranger = client.get(f"/api/v1/runs/{_RUN}/result", params=params, headers=headers)
    any_store.set_member("bob", project=_PROJECT, role=VIEWER_ROLE)
    viewer = client.get(f"/api/v1/runs/{_RUN}/result", params=params, headers=headers)

    _assert_refused(stranger, "not_a_member", _NOT_A_MEMBER)
    assert viewer.status_code == 404
    assert viewer.json()["error"] == "unknown_result"


# --- Recording -------------------------------------------------------------------------

_BOBS_RUN = "b" * 32
_NEW_RUN = "c" * 32


def _recording_setup(store: ExecutionStore) -> dict[str, str]:
    """`_PROJECT` holding `carol`'s `_RUN`, and `boards` holding `bob`'s
    `_BOBS_RUN`: a report of either naming `_PROJECT` is one the store
    refuses from `bob`, for its user or for its project. Returns the header
    sending a read and record token of `bob`'s."""
    for name in ("carol", "bob"):
        _user(store, name)
    _seed(store, recorded_by="carol")
    store.set_member("carol", project=_PROJECT, role=EDITOR_ROLE)
    store.create_project("boards", created_at=_NOW)
    store.set_member("bob", project="boards", role=EDITOR_ROLE)
    store.record_session(
        _start_only_execution(_BOBS_RUN),
        results=[],
        received_at=_NOW,
        recorded_by="bob",
        project="boards",
    )
    return _bearer(store, "bob", READ_SCOPE, RECORD_SCOPE)


def test_a_report_is_refused_for_its_shape_then_its_project_then_its_senders_role(
    any_store: ExecutionStore,
) -> None:
    """A report is read and checked before the sender's role in the
    project it names is asked, and that project is found first: `422`,
    then `404 unknown_project`, then `403`."""
    headers = _recording_setup(any_store)
    client = TestClient(create_app(any_store))
    invalid = _report(_NEW_RUN)
    del invalid["run"]["started_at"]

    malformed = client.post("/api/v1/runs", json=invalid, headers=headers)
    unknown = client.post("/api/v1/runs", json=_report(_NEW_RUN, project="ghost"), headers=headers)
    refused = client.post("/api/v1/runs", json=_report(_NEW_RUN), headers=headers)

    assert (malformed.status_code, malformed.json()["error"]) == (422, "invalid_report")
    assert unknown.status_code == 404
    assert unknown.json() == {
        "error": "unknown_project",
        "detail": "No project with that name exists.",
        "fields": ["project"],
    }
    _assert_refused(refused, "not_a_member", _NOT_A_MEMBER)
    assert any_store.get_execution(_NEW_RUN) is None


@pytest.mark.parametrize(
    ("role", "error"),
    [(None, "not_a_member"), (VIEWER_ROLE, "insufficient_role")],
    ids=["no-role", "viewer"],
)
def test_a_sender_who_may_not_record_learns_nothing_of_the_projects_runs(
    any_store: ExecutionStore, role: str | None, error: str
) -> None:
    """A report of another user's run, of the sender's own run filed in
    another project, and of a new run are refused alike, before the store
    is asked: a `409` would tell the sender which runs the project holds.
    Nothing is written."""
    headers = _recording_setup(any_store)
    if role is not None:
        any_store.set_member("bob", project=_PROJECT, role=role)
    client = TestClient(create_app(any_store))
    before = [any_store.get_run_detail(run_id) for run_id in (_RUN, _BOBS_RUN)]

    responses = [
        client.post("/api/v1/runs", json=_report(run_id), headers=headers)
        for run_id in (_RUN, _BOBS_RUN, _NEW_RUN)
    ]

    for response in responses:
        assert (response.status_code, response.json()["error"]) == (403, error)
        assert _PROJECT not in response.text
    assert [any_store.get_run_detail(run_id) for run_id in (_RUN, _BOBS_RUN)] == before
    assert any_store.get_execution(_NEW_RUN) is None
    assert any_store.count_executions() == 2
    assert [result.identity.node_id for result in any_store.get_results(_RUN)] == [_NODE]
    assert any_store.get_results(_BOBS_RUN) == []


def test_an_editor_gets_what_the_store_answers(any_store: ExecutionStore) -> None:
    """Once the sender may record in the project, the run is the store's to
    answer for: `409 foreign_run` for another user's, `409
    project_mismatch` for one filed in another project, `201` for a new
    one."""
    headers = _recording_setup(any_store)
    any_store.set_member("bob", project=_PROJECT, role=EDITOR_ROLE)
    client = TestClient(create_app(any_store))

    foreign, mismatch, created = (
        client.post("/api/v1/runs", json=_report(run_id), headers=headers)
        for run_id in (_RUN, _BOBS_RUN, _NEW_RUN)
    )

    assert (foreign.status_code, foreign.json()["error"]) == (409, "foreign_run")
    assert (mismatch.status_code, mismatch.json()["error"]) == (409, "project_mismatch")
    assert created.status_code == 201, created.text
    detail = any_store.get_run_detail(_NEW_RUN)
    assert detail is not None
    assert (detail.project, detail.recorded_by) == (_PROJECT, "bob")


@pytest.mark.parametrize(
    ("role", "error"),
    [(None, "not_a_member"), (VIEWER_ROLE, "insufficient_role")],
    ids=["removed", "made-a-viewer"],
)
def test_a_recorder_who_stops_being_an_editor_leaves_the_run_abandoned_until_made_one_again(
    any_store: ExecutionStore, role: str | None, error: str
) -> None:
    """Roles are read on every request: once the recorder is no editor of
    the project, their heartbeat and their finish report are refused and
    store nothing, so the run reads as abandoned past the grace period.
    Made an editor again, the same finish report finishes it."""
    _user(any_store, "alice", admin=True)
    _user(any_store, "bob")
    any_store.create_project(_PROJECT, created_at=_NOW)
    any_store.set_member("bob", project=_PROJECT, role=EDITOR_ROLE)
    any_store.record_session(
        _start_only_execution(_RUN),
        results=[],
        received_at=datetime.now(timezone.utc) - timedelta(hours=2),
        recorded_by="bob",
        project=_PROJECT,
    )
    bobs = _bearer(any_store, "bob", READ_SCOPE, RECORD_SCOPE)
    alices = _bearer(any_store, "alice", READ_SCOPE)
    client = TestClient(create_app(any_store, grace_period_seconds=3600))
    if role is None:
        any_store.remove_member("bob", project=_PROJECT)
    else:
        any_store.set_member("bob", project=_PROJECT, role=role)

    heartbeat = client.post(f"/api/v1/runs/{_RUN}/heartbeat", headers=bobs)
    refused = client.post("/api/v1/runs", json=_report(), headers=bobs)
    abandoned = client.get(f"/api/v1/runs/{_RUN}", headers=alices)
    any_store.set_member("bob", project=_PROJECT, role=EDITOR_ROLE)
    finished = client.post("/api/v1/runs", json=_report(), headers=bobs)
    detail = client.get(f"/api/v1/runs/{_RUN}", headers=alices)

    assert (heartbeat.status_code, heartbeat.json()["error"]) == (403, error)
    assert (refused.status_code, refused.json()["error"]) == (403, error)
    assert abandoned.json()["presentation"] == "abandoned"
    assert finished.status_code == 200, finished.text
    assert detail.json()["presentation"] == "finished"
    assert [result.identity.node_id for result in any_store.get_results(_RUN)] == [_NODE]


# --- `default` -------------------------------------------------------------------------


def test_every_user_reads_and_records_in_default_without_a_row(
    any_store: ExecutionStore,
) -> None:
    """Every user is an editor of `default`, which takes no row: a user made
    after the server started reads, records and keeps a run alive there as
    one made before it, and changes its sections with a manage token."""
    _user(any_store, "alice", admin=True)
    _user(any_store, "bob")
    client = TestClient(create_app(any_store))
    _user(any_store, "carol")

    for seed, name in enumerate(("bob", "carol"), start=1):
        run_id = f"{seed:032x}"
        recorder = _bearer(any_store, name, READ_SCOPE, RECORD_SCOPE)
        manager = _bearer(any_store, name, MANAGE_SCOPE)
        answered = [
            client.post(
                "/api/v1/runs", json=_report(run_id, project=None, finished=False), headers=recorder
            ),
            client.post(f"/api/v1/runs/{run_id}/heartbeat", headers=recorder),
            client.post("/api/v1/runs", json=_report(run_id, project=None), headers=recorder),
            client.get("/api/v1/projects/default/runs", headers=recorder),
            client.get(f"/api/v1/runs/{run_id}/results", headers=recorder),
            client.get("/api/v1/projects/default/members", headers=recorder),
            client.post(
                "/api/v1/projects/default/config/sections",
                json={"name": name, "prefix": f"tests/{name}"},
                headers=manager,
            ),
        ]
        statuses = [response.status_code for response in answered]
        assert statuses == [201, 200, 200, 200, 200, 200, 201], [r.text for r in answered]
        assert answered[5].json() == {"items": [], "everyone": EDITOR_ROLE}
        assert any_store.list_memberships(name) == ()
    assert any_store.list_members(project=DEFAULT_PROJECT) == ()


# --- Sections --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "request_kwargs"),
    [
        ("POST", {"json": {"name": _SECTION, "prefix": "tests/other"}}),
        ("DELETE", {"params": {"name": _SECTION}}),
    ],
    ids=["upsert", "delete"],
)
@pytest.mark.parametrize(
    ("who", "scopes", "error"),
    [
        (EDITOR_ROLE, (MANAGE_SCOPE,), None),
        (OWNER_ROLE, (MANAGE_SCOPE,), None),
        ("admin", (MANAGE_SCOPE,), None),
        (EDITOR_ROLE, (READ_SCOPE, RECORD_SCOPE), "insufficient_scope"),
        (VIEWER_ROLE, (READ_SCOPE, MANAGE_SCOPE), "insufficient_role"),
        ("admin", (ADMIN_SCOPE,), "insufficient_scope"),
    ],
    ids=[
        "editor-manage",
        "owner-manage",
        "admin-manage",
        "editor-read-record",
        "viewer-manage",
        "admin-admin",
    ],
)
def test_sections_are_changed_with_the_manage_scope_by_an_editor(
    any_store: ExecutionStore,
    method: str,
    request_kwargs: dict[str, Any],
    who: str,
    scopes: tuple[str, ...],
    error: str | None,
) -> None:
    """The manage scope, which a token made for the plugin does not hold,
    and the editor role, which an admin holds everywhere without a row.
    Neither is enough alone, and the admin scope is not the manage scope."""
    _user(any_store, "bob", admin=who == "admin")
    _seed(any_store, recorded_by=None)
    if who != "admin":
        any_store.set_member("bob", project=_PROJECT, role=who)
    headers = _bearer(any_store, "bob", *scopes)
    before = any_store.list_settings(TEST_SECTIONS_NAMESPACE, project=_PROJECT)

    response = TestClient(create_app(any_store)).request(
        method, _SECTIONS, headers=headers, **request_kwargs
    )

    after = any_store.list_settings(TEST_SECTIONS_NAMESPACE, project=_PROJECT)
    if error is None:
        assert response.status_code in {200, 204}, response.text
        assert after != before
    else:
        assert response.status_code == 403, response.text
        assert response.json()["error"] == error
        assert after == before


# --- Who the caller is now -------------------------------------------------------------


def test_a_disabled_members_rows_count_again_once_they_are_enabled(
    any_store: ExecutionStore,
) -> None:
    """A disabled user's token authenticates nothing -- `401`, before any
    role is asked -- and their rows stay, so enabling them again gives
    back what they may do."""
    _user(any_store, "bob")
    any_store.create_project(_PROJECT, created_at=_NOW)
    any_store.set_member("bob", project=_PROJECT, role=VIEWER_ROLE)
    headers = _bearer(any_store, "bob", READ_SCOPE)
    client = TestClient(create_app(any_store))
    runs = f"/api/v1/projects/{_PROJECT}/runs"

    enabled = client.get(runs, headers=headers)
    any_store.update_user("bob", disabled=True)
    disabled = client.get(runs, headers=headers)
    any_store.update_user("bob", disabled=False)
    enabled_again = client.get(runs, headers=headers)

    assert enabled.status_code == 200, enabled.text
    assert (disabled.status_code, disabled.json()["error"]) == (401, "unauthenticated")
    assert enabled_again.status_code == 200, enabled_again.text
    assert any_store.get_member_role("bob", project=_PROJECT) == VIEWER_ROLE


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs", "role"),
    _WITHIN_A_PROJECT.values(),
    ids=_WITHIN_A_PROJECT,
)
def test_a_server_with_no_user_checks_no_role(
    any_store: ExecutionStore,
    method: str,
    path: str,
    request_kwargs: dict[str, Any],
    role: str,
) -> None:
    """Its anonymous caller does whatever any role would allow, in a project
    with no member, but for the members routes, which answer `409
    open_server`: nobody is a member of anything until users exist."""
    _seed(any_store, recorded_by=None)

    response = TestClient(create_app(any_store)).request(method, path, **request_kwargs)

    if path.startswith(_MEMBERS):
        assert response.status_code == 409, response.text
        assert response.json()["error"] == "open_server"
    else:
        assert response.status_code < 300, response.text
