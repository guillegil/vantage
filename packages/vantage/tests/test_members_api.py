"""A project's members over HTTP (`service/routes/members.py`): listing
needs the read scope and the viewer role, changing needs the manage scope
and the owner role, and an admin holds that role in every project without
a row; `default` has none, every user being an editor of it; every refusal
of who asks comes first, in `service/access.py`'s order, then the route's
own, and a name no project or user can have is answered without asking the
store; setting is an idempotent upsert, removing is not; roles are read on
every request; nothing a caller sends comes back in a rejection.

Run against every adapter (`any_store`): the routes are the service's, but
every answer rests on what the store says of users, projects and members.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from vantage.core.domain.access import (
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
    Project,
)
from vantage.core.ports.storage import ExecutionStore
from vantage.service.app import create_app

# `any_store`, each adapter in turn.
pytest_plugins = ["store_fixtures"]

_NOW = datetime(2026, 9, 28, 9, 0, 0, tzinfo=timezone.utc)
_PROJECT = "firmware"
_MAX_BODY_BYTES = 4 * 1024
_TOO_LARGE = b" " * (_MAX_BODY_BYTES + 1)
_JSON = {"content-type": "application/json"}

# What a login token of a user who is not an admin holds.
_LOGIN_SCOPES = (READ_SCOPE, MANAGE_SCOPE)

# The members of `_PROJECT` the `owner` fixture makes, and their roles.
_BEFORE = {"bob": OWNER_ROLE, "carol": EDITOR_ROLE, "dave": VIEWER_ROLE}

# Every operation, as `(method, path within a project, the scope it needs)`.
# The user in each path is one nobody can have, and `_ask` sends a body of
# a media type no route reads, so a request that gets past who asks is
# refused for what it asks.
_OPERATIONS: list[tuple[str, str, str]] = [
    ("GET", "/members", READ_SCOPE),
    ("PUT", "/members/NOT-A-NAME", MANAGE_SCOPE),
    ("DELETE", "/members/NOT-A-NAME", MANAGE_SCOPE),
]
_CHANGES = [(method, tail) for method, tail, scope in _OPERATIONS if scope == MANAGE_SCOPE]


def _token(store: ExecutionStore, user: str, *scopes: str) -> str:
    token = new_token()
    store.create_token(
        user, digest=token_digest(token), label="", scopes=frozenset(scopes), created_at=_NOW
    )
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _as(store: ExecutionStore, user: str, *scopes: str) -> TestClient:
    """A client sending a made token of `user` holding `scopes`."""
    return TestClient(create_app(store), headers=_bearer(_token(store, user, *scopes)))


def _members(project: str = _PROJECT) -> str:
    return f"/api/v1/projects/{project}/members"


def _member(user: str, project: str = _PROJECT) -> str:
    return f"/api/v1/projects/{project}/members/{user}"


def _ask(client: TestClient, method: str, tail: str, project: str = _PROJECT) -> Any:
    """`method` on `tail` within `project`, with a body that is not JSON,
    of a media type no route reads."""
    return client.request(
        method,
        f"/api/v1/projects/{project}{tail}",
        content=b"{",
        headers={"content-type": "text/plain"},
    )


def _roles(store: ExecutionStore, project: str = _PROJECT) -> dict[str, str]:
    """Each member of `project` with their role, as the store holds them."""
    return {member.user: member.role for member in store.list_members(project=project)}


def _assert_rejected(response: Any, status: int, error: str, fields: list[str]) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert (body["error"], body["fields"]) == (error, fields)


@pytest.fixture
def owner(any_store: ExecutionStore) -> TestClient:
    """A client sending a token of `bob` holding read and manage, on a store
    whose users are `alice`, an admin, and `bob`, `carol`, `dave` and
    `erin`, who are not; of its projects besides `default`, bob owns
    `firmware`, carol edits it and dave views it, and `boards` has no
    member. Erin is a member of nothing."""
    any_store.create_user("alice", admin=True, created_at=_NOW)
    for name in ("bob", "carol", "dave", "erin"):
        any_store.create_user(name, admin=False, created_at=_NOW)
    for name in (_PROJECT, "boards"):
        any_store.create_project(name, created_at=_NOW)
    # Added out of name order, so a list in it is sorted, not as added.
    for user, role in reversed(_BEFORE.items()):
        any_store.set_member(user, project=_PROJECT, role=role)
    return _as(any_store, "bob", *_LOGIN_SCOPES)


# --- Who may ask, in order --------------------------------------------------------------


@pytest.mark.parametrize(("method", "tail", "scope"), _OPERATIONS)
def test_an_open_server_answers_open_server_before_the_project_is_looked_at(
    any_store: ExecutionStore, method: str, tail: str, scope: str
) -> None:
    """Nobody is a member of anything until users exist, so the anonymous
    caller an open server lets through everywhere else is refused here; a
    token sent along is refused as it is everywhere."""
    app = create_app(any_store)

    anonymous = _ask(TestClient(app), method, tail, "NOT-A-PROJECT")
    in_default = _ask(TestClient(app), method, tail, DEFAULT_PROJECT)
    with_token = _ask(TestClient(app, headers=_bearer(new_token())), method, tail)

    _assert_rejected(anonymous, 409, "open_server", [])
    _assert_rejected(in_default, 409, "open_server", [])
    assert "WWW-Authenticate" not in anonymous.headers
    _assert_rejected(with_token, 401, "unauthenticated", [])
    assert any_store.access_required() is False


@pytest.mark.parametrize(("method", "tail", "scope"), _OPERATIONS)
def test_only_a_token_holding_the_scope_learns_whether_the_project_exists(
    owner: TestClient, any_store: ExecutionStore, method: str, tail: str, scope: str
) -> None:
    """An admin's token holding every other scope is refused too; and the
    answers are the same for a project that exists, one that does not and
    one that cannot."""
    anonymous = TestClient(owner.app)
    lacking = _as(any_store, "alice", *(SCOPES - {scope}))

    for project in (_PROJECT, "ghost", "NOT-A-PROJECT"):
        refused = _ask(lacking, method, tail, project)

        _assert_rejected(_ask(anonymous, method, tail, project), 401, "unauthenticated", [])
        _assert_rejected(refused, 403, "insufficient_scope", [])
        assert refused.headers["WWW-Authenticate"] == (
            f'Bearer realm="vantage", error="insufficient_scope", scope="{scope}"'
        )


@pytest.mark.parametrize(("method", "tail", "scope"), _OPERATIONS)
def test_a_project_nobody_has_is_unknown_project_to_an_admin_and_a_member_of_nothing(
    owner: TestClient, any_store: ExecutionStore, method: str, tail: str, scope: str
) -> None:
    """Answered before whether the caller is a member is asked."""
    for user in ("alice", "erin"):
        response = _ask(_as(any_store, user, *_LOGIN_SCOPES), method, tail, "ghost")

        _assert_rejected(response, 404, "unknown_project", [])


@pytest.mark.parametrize("project", ["Firmware", "f" * 65, "fir%00mware"])
def test_a_project_name_nobody_can_have_is_unknown_project_without_asking_the_store(
    project: str,
) -> None:
    store = InMemoryExecutionStore()
    store.create_user("alice", admin=True, created_at=_NOW)
    asked: list[str] = []
    get_project = store.get_project

    def spy(name: str) -> Project | None:
        asked.append(name)
        return get_project(name)

    store.get_project = spy  # type: ignore[method-assign]
    client = _as(store, "alice", *_LOGIN_SCOPES)

    responses = [_ask(client, method, tail, project) for method, tail, _scope in _OPERATIONS]

    for response in responses:
        _assert_rejected(response, 404, "unknown_project", [])
    assert asked == []


@pytest.mark.parametrize(("method", "tail", "scope"), _OPERATIONS)
def test_a_user_with_no_role_in_the_project_is_not_a_member_before_anything_is_asked(
    owner: TestClient, any_store: ExecutionStore, method: str, tail: str, scope: str
) -> None:
    """Erin is a member of nothing, and bob, who owns `firmware`, of
    nothing else. No challenge, since no other token of theirs would help,
    and the answer names neither the project nor a member."""
    for user, project in (("erin", _PROJECT), ("bob", "boards")):
        response = _ask(_as(any_store, user, *_LOGIN_SCOPES), method, tail, project)

        _assert_rejected(response, 403, "not_a_member", [])
        assert response.json()["detail"] == "You are not a member of this project."
        assert "WWW-Authenticate" not in response.headers
    assert _roles(any_store) == _BEFORE


@pytest.mark.parametrize(("method", "tail"), _CHANGES)
def test_a_role_below_owner_is_refused_before_anything_is_asked(
    owner: TestClient, any_store: ExecutionStore, method: str, tail: str
) -> None:
    for user in ("carol", "dave"):
        response = _ask(_as(any_store, user, *_LOGIN_SCOPES), method, tail)

        _assert_rejected(response, 403, "insufficient_role", [])
        assert response.json()["detail"] == (
            "This needs the owner role in this project, and yours is below it."
        )
        assert "WWW-Authenticate" not in response.headers


def test_a_viewer_and_an_editor_list_the_members_but_change_none(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    """A viewer already sees user names in every run's `recorded_by`."""
    for user in ("carol", "dave"):
        client = _as(any_store, user, *_LOGIN_SCOPES)

        listed = client.get(_members())
        added = client.put(_member("erin"), json={"role": VIEWER_ROLE})
        removed = client.delete(_member("dave"))

        assert listed.status_code == 200, listed.text
        assert [item["user"] for item in listed.json()["items"]] == ["bob", "carol", "dave"]
        _assert_rejected(added, 403, "insufficient_role", [])
        _assert_rejected(removed, 403, "insufficient_role", [])
    assert _roles(any_store) == _BEFORE


def test_an_owners_token_does_only_what_its_scopes_allow(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    """Scopes and roles only narrow each other: an owner's token holding
    read and record, as a made token does unless told otherwise, changes
    nothing, and one holding manage alone lists nothing."""
    reads = _as(any_store, "bob", READ_SCOPE, RECORD_SCOPE)
    manages = _as(any_store, "bob", MANAGE_SCOPE)

    listed = reads.get(_members())
    not_added = reads.put(_member("erin"), json={"role": VIEWER_ROLE})
    not_removed = reads.delete(_member("dave"))
    not_listed = manages.get(_members())
    added = manages.put(_member("erin"), json={"role": VIEWER_ROLE})

    assert listed.status_code == 200, listed.text
    _assert_rejected(not_added, 403, "insufficient_scope", [])
    _assert_rejected(not_removed, 403, "insufficient_scope", [])
    _assert_rejected(not_listed, 403, "insufficient_scope", [])
    assert added.status_code == 201, added.text


# --- default ----------------------------------------------------------------------------


def test_every_user_is_an_editor_of_default_where_no_member_is_listed(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    """A user who is a member of nothing lists it, and is told everyone is
    an editor there; changing a member needs the owner role, above theirs."""
    client = _as(any_store, "erin", *_LOGIN_SCOPES)

    listed = client.get(_members(DEFAULT_PROJECT))
    added = client.put(_member("carol", DEFAULT_PROJECT), json={"role": OWNER_ROLE})
    removed = client.delete(_member("carol", DEFAULT_PROJECT))

    assert listed.status_code == 200, listed.text
    assert listed.json() == {"items": [], "everyone": EDITOR_ROLE}
    _assert_rejected(added, 403, "insufficient_role", [])
    _assert_rejected(removed, 403, "insufficient_role", [])


@pytest.mark.parametrize("user", ["bob", "NOT-A-NAME"])
def test_an_admin_is_told_default_has_no_member_to_set_or_remove(
    owner: TestClient, any_store: ExecutionStore, user: str
) -> None:
    """Before the user in the path is looked at, and before a body is read."""
    admin = _as(any_store, "alice", *_LOGIN_SCOPES)

    listed = admin.get(_members(DEFAULT_PROJECT))
    added = admin.put(_member(user, DEFAULT_PROJECT), content=_TOO_LARGE, headers=_JSON)
    removed = admin.delete(_member(user, DEFAULT_PROJECT))

    assert listed.json() == {"items": [], "everyone": EDITOR_ROLE}
    _assert_rejected(added, 409, "default_project", [])
    _assert_rejected(removed, 409, "default_project", [])
    assert added.json()["detail"] == (
        "Every user is an editor of default, which has no members to set or remove."
    )
    assert list(any_store.list_members(project=DEFAULT_PROJECT)) == []


# --- Setting a member -------------------------------------------------------------------


def test_adding_a_member_answers_201_and_setting_a_role_200_the_same_one_included(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    added = owner.put(_member("erin"), json={"role": VIEWER_ROLE})
    changed = owner.put(_member("erin"), json={"role": EDITOR_ROLE})
    again = owner.put(_member("erin"), json={"role": EDITOR_ROLE})

    assert (added.status_code, changed.status_code, again.status_code) == (201, 200, 200)
    assert added.json() == {"user": "erin", "role": VIEWER_ROLE}
    assert changed.json() == again.json() == {"user": "erin", "role": EDITOR_ROLE}
    assert _roles(any_store) == {**_BEFORE, "erin": EDITOR_ROLE}


def test_an_owner_makes_another_an_owner_who_manages_from_their_next_request(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    """Roles are read on every request, never kept by the server: carol's
    client is the same before and after."""
    carols = _as(any_store, "carol", *_LOGIN_SCOPES)

    before = carols.put(_member("erin"), json={"role": VIEWER_ROLE})
    promoted = owner.put(_member("carol"), json={"role": OWNER_ROLE})
    added = carols.put(_member("erin"), json={"role": VIEWER_ROLE})
    removed = carols.delete(_member("bob"))

    _assert_rejected(before, 403, "insufficient_role", [])
    assert (promoted.status_code, added.status_code, removed.status_code) == (200, 201, 204)
    assert _roles(any_store) == {"carol": OWNER_ROLE, "dave": VIEWER_ROLE, "erin": VIEWER_ROLE}


def test_an_owner_who_demotes_themselves_reads_on_but_manages_no_more(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    """Nothing guards an owner's own row, or the last owner's: an admin can
    always repair the project."""
    demoted = owner.put(_member("bob"), json={"role": VIEWER_ROLE})
    listed = owner.get(_members())
    refused = owner.put(_member("bob"), json={"role": OWNER_ROLE})
    repaired = _as(any_store, "alice", *_LOGIN_SCOPES).put(
        _member("bob"), json={"role": OWNER_ROLE}
    )

    assert (demoted.status_code, demoted.json()) == (200, {"user": "bob", "role": VIEWER_ROLE})
    assert listed.status_code == 200, listed.text
    _assert_rejected(refused, 403, "insufficient_role", [])
    assert repaired.status_code == 200, repaired.text
    assert _roles(any_store) == _BEFORE


def test_an_owner_who_removes_themselves_is_a_member_no_more(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    removed = owner.delete(_member("bob"))
    listed = owner.get(_members())

    assert (removed.status_code, removed.content) == (204, b"")
    _assert_rejected(listed, 403, "not_a_member", [])
    assert _roles(any_store) == {"carol": EDITOR_ROLE, "dave": VIEWER_ROLE}


def test_an_admin_manages_the_members_of_a_project_without_being_one(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    admin = _as(any_store, "alice", *_LOGIN_SCOPES)

    listed = admin.get(_members("boards"))
    added = admin.put(_member("erin", "boards"), json={"role": OWNER_ROLE})
    removed = admin.delete(_member("erin", "boards"))

    assert listed.json() == {"items": [], "everyone": None}
    assert (added.status_code, removed.status_code) == (201, 204)
    assert _roles(any_store, "boards") == {}


def test_an_admin_who_is_demoted_manages_nothing_from_their_next_request(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    """Admin standing is read as the token is, on every request."""
    admin = _as(any_store, "alice", *_LOGIN_SCOPES)

    before = admin.get(_members())
    any_store.update_user("alice", admin=False)
    after = admin.get(_members())

    assert before.status_code == 200, before.text
    _assert_rejected(after, 403, "not_a_member", [])


def test_an_admin_or_a_disabled_user_may_be_made_a_member(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    """An admin acts as an owner whatever their row says; a disabled user's
    role grants nothing while their tokens authenticate nothing, and counts
    once they are enabled."""
    any_store.update_user("erin", disabled=True)
    erins = _as(any_store, "erin", READ_SCOPE)

    admin_added = owner.put(_member("alice"), json={"role": VIEWER_ROLE})
    disabled_added = owner.put(_member("erin"), json={"role": VIEWER_ROLE})
    while_disabled = erins.get(_members())
    any_store.update_user("erin", disabled=False)
    once_enabled = erins.get(_members())
    by_admin = _as(any_store, "alice", *_LOGIN_SCOPES).delete(_member("dave"))

    assert (admin_added.status_code, disabled_added.status_code) == (201, 201)
    _assert_rejected(while_disabled, 401, "unauthenticated", [])
    assert once_enabled.status_code == 200, once_enabled.text
    assert by_admin.status_code == 204, by_admin.text
    assert _roles(any_store) == {
        "alice": VIEWER_ROLE,
        "bob": OWNER_ROLE,
        "carol": EDITOR_ROLE,
        "erin": VIEWER_ROLE,
    }


def test_members_list_by_user_and_no_role_is_everyones_outside_default(
    owner: TestClient,
) -> None:
    """In code point order, whatever order they were added in."""
    owner.put(_member("alice"), json={"role": EDITOR_ROLE})

    response = owner.get(_members())

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {"user": "alice", "role": EDITOR_ROLE},
            {"user": "bob", "role": OWNER_ROLE},
            {"user": "carol", "role": EDITOR_ROLE},
            {"user": "dave", "role": VIEWER_ROLE},
        ],
        "everyone": None,
    }


@pytest.mark.parametrize(
    ("project", "user", "content_type", "content", "status", "error"),
    [
        (DEFAULT_PROJECT, "NOT-A-NAME", "text/plain", b"{", 415, "unsupported_media_type"),
        (DEFAULT_PROJECT, "NOT-A-NAME", "application/json", b"{", 409, "default_project"),
        (_PROJECT, "NOT-A-NAME", "application/json", _TOO_LARGE, 404, "unknown_user"),
        (_PROJECT, "ghost", "application/json", _TOO_LARGE, 413, "payload_too_large"),
        (_PROJECT, "ghost", "application/json", b"{", 400, "invalid_json"),
        (_PROJECT, "ghost", "application/json", b'{"role": 1}', 422, "invalid_member_request"),
        (_PROJECT, "ghost", "application/json", b'{"role": "admin"}', 422, "invalid_role"),
        (_PROJECT, "ghost", "application/json", b'{"role": "viewer"}', 404, "unknown_user"),
    ],
    ids=[
        "media-type",
        "default",
        "a-name-nobody-can-have",
        "size",
        "json",
        "shape",
        "role",
        "user",
    ],
)
def test_a_set_with_several_faults_names_the_first_its_checks_find(
    owner: TestClient,
    any_store: ExecutionStore,
    project: str,
    user: str,
    content_type: str,
    content: bytes,
    status: int,
    error: str,
) -> None:
    """Asked by an admin, whom no refusal of who asks stops: the media
    type, then `default`, then the user in the path, then the body -- its
    size, its JSON, its shape, its role -- and last whether the user
    exists."""
    admin = _as(any_store, "alice", *_LOGIN_SCOPES)

    response = admin.put(
        _member(user, project), content=content, headers={"content-type": content_type}
    )

    assert (response.status_code, response.json()["error"]) == (status, error)
    assert _roles(any_store) == _BEFORE


@pytest.mark.parametrize("user", ["Bob", "b" * 65, "bob%00"])
def test_a_name_no_user_can_have_is_nobody_without_asking_the_store(user: str) -> None:
    """Setting one is `unknown_user` before the body is read, removing one
    `unknown_member`; a U+0000 never reaches an adapter."""
    store = InMemoryExecutionStore()
    store.create_user("alice", admin=True, created_at=_NOW)
    store.create_project(_PROJECT, created_at=_NOW)
    asked: list[str] = []
    set_member, remove_member = store.set_member, store.remove_member

    def set_spy(user: str, *, project: str, role: str) -> bool:
        asked.append(user)
        return set_member(user, project=project, role=role)

    def remove_spy(user: str, *, project: str) -> bool:
        asked.append(user)
        return remove_member(user, project=project)

    store.set_member = set_spy  # type: ignore[method-assign]
    store.remove_member = remove_spy  # type: ignore[method-assign]
    client = _as(store, "alice", *_LOGIN_SCOPES)

    added = client.put(_member(user), content=_TOO_LARGE, headers=_JSON)
    removed = client.delete(_member(user))

    _assert_rejected(added, 404, "unknown_user", [])
    _assert_rejected(removed, 404, "unknown_member", [])
    assert asked == []


def test_setting_a_user_nobody_has_is_unknown_user_and_nothing_is_stored(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    """Which names exist is what an owner adding a colleague by name needs
    to know."""
    response = owner.put(_member("ghost"), json={"role": VIEWER_ROLE})

    _assert_rejected(response, 404, "unknown_user", [])
    assert _roles(any_store) == _BEFORE


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        ({}, ["role"]),
        ({"role": 1}, ["role"]),
        ({"role": None}, ["role"]),
        ({"role": [EDITOR_ROLE]}, ["role"]),
        ({"role": EDITOR_ROLE, "admin": True}, ["admin"]),
        ([EDITOR_ROLE], []),
        (EDITOR_ROLE, []),
    ],
)
def test_a_body_of_another_shape_sets_nobody(
    owner: TestClient, any_store: ExecutionStore, body: Any, fields: list[str]
) -> None:
    response = owner.put(_member("erin"), json=body)

    _assert_rejected(response, 422, "invalid_member_request", fields)
    assert _roles(any_store) == _BEFORE


@pytest.mark.parametrize("role", ["admin", "Editor", "", " owner", "editor\x00"])
def test_a_role_that_is_not_one_is_invalid_role_and_sets_nobody(
    owner: TestClient, any_store: ExecutionStore, role: str
) -> None:
    """A U+0000 arrives as U+FFFD, which no role holds either."""
    response = owner.put(_member("erin"), json={"role": role})

    _assert_rejected(response, 422, "invalid_role", ["role"])
    assert response.json()["detail"] == "A role is viewer, editor or owner."
    assert _roles(any_store) == _BEFORE


def test_a_body_is_json_of_at_most_4_kib(owner: TestClient) -> None:
    body = b'{"role": "viewer"}'

    def send(content: bytes, content_type: str = "application/json") -> Any:
        return owner.put(_member("erin"), content=content, headers={"content-type": content_type})

    wrong_type = send(body, "text/plain")
    too_large = send(body.ljust(_MAX_BODY_BYTES + 1))
    not_json = send(b"{")
    at_the_cap = send(body.ljust(_MAX_BODY_BYTES))

    _assert_rejected(wrong_type, 415, "unsupported_media_type", [])
    _assert_rejected(too_large, 413, "payload_too_large", [])
    _assert_rejected(not_json, 400, "invalid_json", [])
    assert at_the_cap.status_code == 201, at_the_cap.text


# --- Removing a member ------------------------------------------------------------------


def test_removing_a_member_answers_no_content_and_again_unknown_member(
    owner: TestClient, any_store: ExecutionStore
) -> None:
    carols = _as(any_store, "carol", READ_SCOPE)

    removed = owner.delete(_member("carol"))
    again = owner.delete(_member("carol"))

    assert (removed.status_code, removed.content) == (204, b"")
    _assert_rejected(again, 404, "unknown_member", [])
    _assert_rejected(carols.get(_members()), 403, "not_a_member", [])
    assert _roles(any_store) == {"bob": OWNER_ROLE, "dave": VIEWER_ROLE}


@pytest.mark.parametrize(
    "user",
    ["ghost", "erin", "NOT-A-NAME"],
    ids=["unknown-user", "member-elsewhere", "a-name-nobody-can-have"],
)
def test_removing_a_user_who_is_not_a_member_is_unknown_member(
    owner: TestClient, any_store: ExecutionStore, user: str
) -> None:
    """A user nobody has reads as one who is not a member; a member of
    another project stays one."""
    any_store.set_member("erin", project="boards", role=VIEWER_ROLE)

    response = owner.delete(_member(user))

    _assert_rejected(response, 404, "unknown_member", [])
    assert response.json()["detail"] == "That user is not a member of this project."
    assert _roles(any_store) == _BEFORE
    assert _roles(any_store, "boards") == {"erin": VIEWER_ROLE}


def test_removing_reads_no_body(owner: TestClient) -> None:
    response = owner.request(
        "DELETE", _member("carol"), content=b"not json", headers={"content-type": "text/plain"}
    )

    assert response.status_code == 204, response.text


# --- What comes back --------------------------------------------------------------------


def test_nothing_a_caller_sends_comes_back_in_a_rejection(owner: TestClient) -> None:
    hostile = "<script>\r\nX-Injected: 1"
    in_path = quote(hostile, safe="")

    answers = [
        (owner.put(_member("erin"), json={"role": hostile}), "invalid_role"),
        (
            owner.put(_member("erin"), json={"role": VIEWER_ROLE, hostile: True}),
            "invalid_member_request",
        ),
        (owner.put(_member("erin"), json={"role": {hostile: hostile}}), "invalid_member_request"),
        (owner.put(_member(in_path), json={"role": VIEWER_ROLE}), "unknown_user"),
        (owner.delete(_member(in_path)), "unknown_member"),
        (owner.get(_members(in_path)), "unknown_project"),
    ]

    for response, error in answers:
        assert response.json()["error"] == error
        assert "script" not in response.text
        assert "Injected" not in str(response.headers)
