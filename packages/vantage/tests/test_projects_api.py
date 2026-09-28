"""Listing and making projects over HTTP (`service/routes/projects.py`):
anyone who may read lists them -- the ones they have a role in, with that
role, which `test_membership_access.py` covers; only an admin's token makes
one once the database has a user, anyone while it has none; a name is
checked by the rule the command line applies, and a rejection never
repeats it. A project made here takes the reports that name it, as one
made with `vantage project add` does, and has no member, its maker
included.

Run against every adapter (`any_store`): the routes are the service's, but
every answer rests on what the store says of projects.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from vantage.core.domain.access import (
    ADMIN_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    SCOPES,
    new_token,
    token_digest,
)
from vantage.core.domain.projects import DEFAULT_PROJECT, OWNER_ROLE
from vantage.core.ports.storage import ExecutionStore
from vantage.service.app import create_app
from vantage.service.routes.projects import MAX_PROJECTS_BODY_BYTES

# `any_store`, each adapter in turn.
pytest_plugins = ["store_fixtures"]

_NOW = datetime(2026, 9, 28, 9, 0, 0, tzinfo=timezone.utc)
_PROJECTS = "/api/v1/projects"
_JSON = {"Content-Type": "application/json"}


def _token(store: ExecutionStore, user: str, *scopes: str) -> str:
    token = new_token()
    store.create_token(
        user, digest=token_digest(token), label="", scopes=frozenset(scopes), created_at=_NOW
    )
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _instant(text: str) -> datetime:
    """A timestamp as the API writes it; Python 3.10 reads no `Z`."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _names(store: ExecutionStore) -> list[str]:
    return [project.name for project in store.list_projects()]


def _assert_rejected(response: Any, status: int, error: str, fields: list[str]) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert (body["error"], body["fields"]) == (error, fields)


@pytest.fixture
def admin(any_store: ExecutionStore) -> Iterator[TestClient]:
    """A client sending the token of `alice`, an admin, which holds only the
    admin scope, on a store that also has `bob`, who is not an admin."""
    any_store.create_user("alice", admin=True, created_at=_NOW)
    any_store.create_user("bob", admin=False, created_at=_NOW)
    token = _token(any_store, "alice", ADMIN_SCOPE)
    yield TestClient(create_app(any_store), headers=_bearer(token))


# --- Who may list ----------------------------------------------------------------------


def test_a_new_database_lists_default_alone(any_store: ExecutionStore) -> None:
    """Every database has `default` from its creation, for the runs that
    name no project."""
    response = TestClient(create_app(any_store)).get(_PROJECTS)

    assert response.status_code == 200, response.text
    (item,) = response.json()["items"]
    assert item["name"] == DEFAULT_PROJECT
    assert _instant(item["created_at"]).tzinfo is not None
    assert item["role"] is None


def test_listing_needs_the_read_scope_once_the_database_has_a_user(
    any_store: ExecutionStore,
) -> None:
    any_store.create_user("alice", admin=True, created_at=_NOW)
    reader = _token(any_store, "alice", READ_SCOPE)
    recorder = _token(any_store, "alice", RECORD_SCOPE, ADMIN_SCOPE)
    client = TestClient(create_app(any_store))

    anonymous = client.get(_PROJECTS)
    lacking = client.get(_PROJECTS, headers=_bearer(recorder))
    allowed = client.get(_PROJECTS, headers=_bearer(reader))

    _assert_rejected(anonymous, 401, "unauthenticated", [])
    _assert_rejected(lacking, 403, "insufficient_scope", [])
    assert allowed.status_code == 200, allowed.text
    assert [item["name"] for item in allowed.json()["items"]] == [DEFAULT_PROJECT]


def test_projects_list_by_name_with_their_creation_time(
    any_store: ExecutionStore,
) -> None:
    for name in ("zephyr", "boards", "e2e"):
        any_store.create_project(name, created_at=_NOW)

    response = TestClient(create_app(any_store)).get(_PROJECTS)

    items = response.json()["items"]
    assert [item["name"] for item in items] == ["boards", DEFAULT_PROJECT, "e2e", "zephyr"]
    assert _instant(items[0]["created_at"]) == _NOW


# --- Who may make one ------------------------------------------------------------------


def test_only_an_admin_token_of_an_admin_makes_a_project(any_store: ExecutionStore) -> None:
    """A non-admin's token holding every scope is still refused: the admin
    scope grants only what its user may do."""
    any_store.create_user("alice", admin=True, created_at=_NOW)
    any_store.create_user("bob", admin=False, created_at=_NOW)
    without_admin = _token(any_store, "alice", READ_SCOPE, RECORD_SCOPE)
    of_non_admin = _token(any_store, "bob", *SCOPES)
    client = TestClient(create_app(any_store))

    anonymous = client.post(_PROJECTS, json={"name": "firmware"})
    lacking = client.post(_PROJECTS, json={"name": "firmware"}, headers=_bearer(without_admin))
    non_admin = client.post(_PROJECTS, json={"name": "firmware"}, headers=_bearer(of_non_admin))

    _assert_rejected(anonymous, 401, "unauthenticated", [])
    _assert_rejected(lacking, 403, "insufficient_scope", [])
    _assert_rejected(non_admin, 403, "insufficient_scope", [])
    assert _names(any_store) == [DEFAULT_PROJECT]


def test_an_open_server_lets_anyone_make_a_project(any_store: ExecutionStore) -> None:
    """A server with no user already lets anyone record runs and change the
    sections; making a project is no more than that, and unlike making a
    user it opens nothing. A server with no user checks no role, so the
    answer names none."""
    response = TestClient(create_app(any_store)).post(_PROJECTS, json={"name": "firmware"})

    assert response.status_code == 201, response.text
    assert response.json()["role"] is None
    assert _names(any_store) == [DEFAULT_PROJECT, "firmware"]
    assert any_store.access_required() is False


def test_a_made_project_is_answered_and_stored(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    """The answer names the role its maker acts with there: `owner`, as an
    admin."""
    before = datetime.now(timezone.utc)

    response = admin.post(_PROJECTS, json={"name": "fw-2.x_nightly"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"name", "created_at", "role"}
    assert body["name"] == "fw-2.x_nightly"
    assert before <= _instant(body["created_at"]) <= datetime.now(timezone.utc)
    assert body["role"] == OWNER_ROLE
    stored = any_store.get_project("fw-2.x_nightly")
    assert stored is not None and stored.created_at == _instant(body["created_at"])
    assert _names(any_store) == [DEFAULT_PROJECT, "fw-2.x_nightly"]


def test_a_made_project_has_no_member_its_maker_included(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    """Its maker gets no row: an admin acts as an owner of every project
    without one, so once they stop being an admin they are no member of
    the project they made."""
    reader = _bearer(_token(any_store, "alice", READ_SCOPE))
    made = admin.post(_PROJECTS, json={"name": "firmware"})

    as_admin = admin.get("/api/v1/projects/firmware/runs", headers=reader)
    any_store.update_user("alice", admin=False)
    demoted = admin.get("/api/v1/projects/firmware/runs", headers=reader)

    assert made.status_code == 201, made.text
    assert any_store.list_members(project="firmware") == ()
    assert any_store.list_memberships("alice") == ()
    assert as_admin.status_code == 200, as_admin.text
    _assert_rejected(demoted, 403, "not_a_member", [])


@pytest.mark.parametrize("name", [DEFAULT_PROJECT, "firmware"])
def test_a_taken_name_is_refused_and_the_project_left_as_it_was(
    admin: TestClient, any_store: ExecutionStore, name: str
) -> None:
    any_store.create_project("firmware", created_at=_NOW)
    before = any_store.list_projects()

    response = admin.post(_PROJECTS, json={"name": name})

    _assert_rejected(response, 409, "project_exists", ["name"])
    assert name not in response.json()["detail"]
    assert any_store.list_projects() == before


@pytest.mark.parametrize(
    "name",
    ["", "Firmware", "-firmware", ".firmware", "f" * 65, "fw 2", "../fw", "fw/2", "fw\x00", "é"],
    ids=[
        "empty",
        "upper-case",
        "leading-dash",
        "leading-dot",
        "too-long",
        "space",
        "dot-dot",
        "slash",
        "nul",
        "non-ascii",
    ],
)
def test_a_name_no_project_can_have_is_refused_without_being_repeated(
    admin: TestClient, any_store: ExecutionStore, name: str
) -> None:
    response = admin.post(_PROJECTS, json={"name": name})

    _assert_rejected(response, 422, "invalid_project_name", ["name"])
    # The whole detail is the fixed sentence, so no spelling of the name --
    # raw, escaped, or with U+0000 already replaced -- can ride along.
    assert response.json()["detail"] == (
        "A project name is 1 to 64 characters of a-z, 0-9, '.', '_' and '-', "
        "starting with a letter or a digit."
    )
    if name:
        for shown in {name, name.replace("\x00", "\ufffd"), json.dumps(name)[1:-1]}:
            assert shown not in response.text
    assert _names(any_store) == [DEFAULT_PROJECT]


def test_the_longest_name_is_made(admin: TestClient, any_store: ExecutionStore) -> None:
    name = "f" * 64

    response = admin.post(_PROJECTS, json={"name": name})

    assert response.status_code == 201, response.text
    assert _names(any_store) == [DEFAULT_PROJECT, name]


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        ({}, ["name"]),
        ({"name": 5}, ["name"]),
        ({"name": None}, ["name"]),
        ({"name": ["firmware"]}, ["name"]),
        ({"name": "firmware", "description": "nightly"}, ["description"]),
        (["firmware"], []),
        ("firmware", []),
    ],
    ids=[
        "empty-object",
        "number",
        "null",
        "list",
        "extra-field",
        "not-an-object",
        "bare-string",
    ],
)
def test_a_body_of_another_shape_makes_no_project(
    admin: TestClient, any_store: ExecutionStore, body: Any, fields: list[str]
) -> None:
    """Strict: a name is a JSON string, never something that converts to
    one, and a field the route does not know is refused, not ignored."""
    response = admin.post(_PROJECTS, json=body)

    assert response.status_code == 422, response.text
    assert response.json() == {
        "error": "invalid_project_request",
        "detail": "The submitted request does not match the expected shape.",
        "fields": fields,
    }
    assert "firmware" not in response.text
    assert "nightly" not in response.text
    assert _names(any_store) == [DEFAULT_PROJECT]


@pytest.mark.parametrize(
    "body",
    [b"{bad", b'{"name": "firmware"', b'{"name": "\xff"}', b'{"name": NaN}'],
    ids=["malformed", "cut-short", "not-utf8", "nan"],
)
def test_a_body_that_is_not_json_is_400_invalid_json(
    admin: TestClient, any_store: ExecutionStore, body: bytes
) -> None:
    response = admin.post(_PROJECTS, content=body, headers=_JSON)

    _assert_rejected(response, 400, "invalid_json", [])
    assert "firmware" not in response.text
    assert _names(any_store) == [DEFAULT_PROJECT]


def test_a_body_over_the_cap_is_413_and_makes_nothing(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    body = b'{"name": "firmware"}' + b" " * MAX_PROJECTS_BODY_BYTES

    response = admin.post(_PROJECTS, content=body, headers=_JSON)

    _assert_rejected(response, 413, "payload_too_large", [])
    assert _names(any_store) == [DEFAULT_PROJECT]


def test_the_most_a_valid_body_can_take_fits_under_the_cap(admin: TestClient) -> None:
    """The longest name with every character spelt as a six-byte escape."""
    name = "f" * 64
    body = ('{"name": "' + "".join(f"\\u{ord(c):04x}" for c in name) + '"}').encode()
    assert len(body) <= MAX_PROJECTS_BODY_BYTES

    response = admin.post(_PROJECTS, content=body, headers=_JSON)

    assert response.status_code == 201, response.text
    assert response.json()["name"] == name


@pytest.mark.parametrize("content_type", ["text/plain", None])
def test_a_body_not_declared_json_is_415_and_makes_nothing(
    admin: TestClient, any_store: ExecutionStore, content_type: str | None
) -> None:
    headers = {} if content_type is None else {"Content-Type": content_type}

    response = admin.post(_PROJECTS, content=b'{"name": "firmware"}', headers=headers)

    _assert_rejected(response, 415, "unsupported_media_type", [])
    assert _names(any_store) == [DEFAULT_PROJECT]


def test_a_caller_who_may_not_make_one_is_refused_before_the_body_is_looked_at(
    any_store: ExecutionStore,
) -> None:
    """Authorization comes first, so a refused caller learns nothing of
    whether its name would be taken or refused."""
    any_store.create_user("alice", admin=True, created_at=_NOW)
    reader = _token(any_store, "alice", READ_SCOPE)
    client = TestClient(create_app(any_store), headers=_bearer(reader))

    taken = client.post(_PROJECTS, json={"name": DEFAULT_PROJECT})
    refused = client.post(_PROJECTS, json={"name": "Bad"})
    unreadable = client.post(_PROJECTS, content=b"{bad", headers={"Content-Type": "text/plain"})

    for response in (taken, refused, unreadable):
        _assert_rejected(response, 403, "insufficient_scope", [])


# --- What a made project is for --------------------------------------------------------


def _report(run_id: str, project: str) -> dict[str, Any]:
    return {
        "project": project,
        "run": {
            "id": run_id,
            "started_at": "2026-09-28T09:14:02.481930+00:00",
            "finished_at": "2026-09-28T09:14:47.002118+00:00",
            "exit_status": 0,
            "interrupted": False,
            "interrupt_reason": None,
        },
    }


def test_a_made_project_takes_the_reports_that_name_it(any_store: ExecutionStore) -> None:
    """A server never makes a project from a report: the one naming it is
    refused until the project exists, then lands in it."""
    client = TestClient(create_app(any_store))
    run_id = "a" * 32

    before = client.post("/api/v1/runs", json=_report(run_id, "firmware"))
    made = client.post(_PROJECTS, json={"name": "firmware"})
    after = client.post("/api/v1/runs", json=_report(run_id, "firmware"))

    _assert_rejected(before, 404, "unknown_project", ["project"])
    assert made.status_code == 201, made.text
    assert after.status_code == 201, after.text
    runs = client.get("/api/v1/projects/firmware/runs")
    assert [item["id"] for item in runs.json()["items"]] == [run_id]
    assert client.get("/api/v1/projects/default/runs").json()["items"] == []
    assert client.get(f"/api/v1/runs/{run_id}").json()["project"] == "firmware"
