"""Who may call which route: a server with no user is open, a server with
one needs a token granting each route's scope, a run takes reports and
heartbeats from the user who created it alone, and logging in or changing
a password needs no token but a server that has a user
(`service/access.py`).

Most tests run against every adapter (`any_store`): the rules are the
service's, but the answers they rest on -- whether a user exists, what a
token grants, who recorded a run -- are each adapter's.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from vantage.core.domain.access import (
    ADMIN_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    new_token,
    token_digest,
)
from vantage.core.domain.passwords import hash_password
from vantage.core.domain.projects import DEFAULT_PROJECT
from vantage.core.ports.storage import ExecutionStore
from vantage.service.app import create_app
from vantage.service.errors import MAX_REPORT_BYTES
from vantage.service.routes.sections import TEST_SECTIONS_NAMESPACE

# `any_store`, each adapter in turn; `cheap_passwords`, for the tests that
# give a user a password.
pytest_plugins = ["store_fixtures", "password_fixtures"]

_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
_RUN = "a" * 32
_PASSWORD = "correct horse battery staple"
_JOIN_TIMEOUT_SECONDS = 10

# Logging in and changing a password, each with a body that succeeds once
# `alice` has `_PASSWORD`.
_PASSWORD_ROUTES = [
    ("/api/v1/login", {"name": "alice", "password": _PASSWORD}),
    (
        "/api/v1/password",
        {"name": "alice", "password": _PASSWORD, "new_password": f"{_PASSWORD}!"},
    ),
]


def _report(run_id: str = _RUN, *, finished: bool = True) -> dict[str, Any]:
    return {
        "run": {
            "id": run_id,
            "started_at": "2026-09-27T12:00:00+00:00",
            "finished_at": "2026-09-27T12:00:05+00:00" if finished else None,
            "exit_status": 0 if finished else None,
            "interrupted": False,
            "interrupt_reason": None,
        },
        "results": [
            {
                "node_id": "tests/test_a.py::test_one",
                "file_path": "tests/test_a.py",
                "class_name": None,
                "function_name": "test_one",
                "param_id": None,
                "outcome": "passed",
                "duration": 0.003,
                "started_at": "2026-09-27T12:00:01+00:00",
                "finished_at": "2026-09-27T12:00:01.003000+00:00",
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


def _assert_unauthenticated(response: Any, *, invalid: bool) -> None:
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"
    assert response.json()["fields"] == []
    challenge = 'Bearer realm="vantage"'
    if invalid:
        challenge += ', error="invalid_token"'
    assert response.headers["WWW-Authenticate"] == challenge


def test_a_server_without_users_serves_every_route_without_a_token(
    any_store: ExecutionStore,
) -> None:
    client = TestClient(create_app(any_store))

    created = client.post("/api/v1/runs", json=_report())
    listed = client.get("/api/v1/projects/default/runs")
    section = client.post(
        "/api/v1/projects/default/config/sections", json={"name": "Api", "prefix": "tests/api"}
    )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()["items"][0]["recorded_by"] is None
    assert section.status_code == 201


def test_a_token_sent_to_a_server_without_users_is_refused(any_store: ExecutionStore) -> None:
    """No token authenticates where no user exists, and one that is sent
    anyway is a mistake to report, not a credential to ignore."""
    client = TestClient(create_app(any_store))

    response = client.get(
        "/api/v1/projects/default/runs", headers={"Authorization": f"Bearer {new_token()}"}
    )

    _assert_unauthenticated(response, invalid=True)


def test_once_a_user_exists_a_request_without_a_token_is_refused(
    any_store: ExecutionStore,
) -> None:
    _user(any_store, "alice")
    client = TestClient(create_app(any_store))

    response = client.get("/api/v1/projects/default/runs")

    _assert_unauthenticated(response, invalid=False)
    assert response.json()["detail"] == (
        "This server requires a token: send Authorization: Bearer <token>."
    )


def test_the_first_user_closes_a_server_already_running(any_store: ExecutionStore) -> None:
    """`vantage user add` writes to the database a server is serving; the
    very next request needs a token."""
    client = TestClient(create_app(any_store))
    assert client.get("/api/v1/projects/default/runs").status_code == 200

    _user(any_store, "alice")

    _assert_unauthenticated(client.get("/api/v1/projects/default/runs"), invalid=False)


def test_a_closed_server_stops_asking_whether_a_user_exists() -> None:
    """Users are never deleted, so once one is found the answer is kept:
    the store is asked while the server is open, and never after."""
    store = InMemoryExecutionStore()
    asked: list[bool] = []
    original = store.access_required

    def counting() -> bool:
        asked.append(original())
        return asked[-1]

    store.access_required = counting  # type: ignore[method-assign]
    client = TestClient(create_app(store))

    client.get("/api/v1/projects/default/runs")
    _user(store, "alice")
    for _ in range(3):
        client.get("/api/v1/projects/default/runs")

    assert asked == [False, True]


@pytest.mark.parametrize(
    "authorization",
    [
        "Basic YWxpY2U6c2VjcmV0",
        "Bearer",
        "Bearer ",
        "Bearer two words",
        "Token vantage_x",
        "Bearer vantage_\x7f",
        "Bearer " + "x" * 513,
    ],
)
def test_a_header_that_carries_no_token_is_refused_as_invalid(
    authorization: str,
) -> None:
    store = InMemoryExecutionStore()
    _user(store, "alice")
    client = TestClient(create_app(store))

    response = client.get("/api/v1/projects/default/runs", headers={"Authorization": authorization})

    _assert_unauthenticated(response, invalid=True)


def test_the_bearer_scheme_is_matched_in_any_case(any_store: ExecutionStore) -> None:
    _user(any_store, "alice")
    token = _bearer(any_store, "alice", READ_SCOPE)["Authorization"].removeprefix("Bearer ")
    client = TestClient(create_app(any_store))

    response = client.get(
        "/api/v1/projects/default/runs", headers={"Authorization": f"bEaReR  {token} "}
    )

    assert response.status_code == 200


def test_a_revoked_token_and_a_disabled_users_token_are_refused_alike(
    any_store: ExecutionStore,
) -> None:
    _user(any_store, "alice")
    _user(any_store, "bob")
    revoked = _bearer(any_store, "alice", READ_SCOPE)
    (token,) = any_store.list_tokens(user="alice")
    any_store.revoke_token(token.id, revoked_at=_NOW)
    disabled = _bearer(any_store, "bob", READ_SCOPE)
    any_store.update_user("bob", disabled=True)
    client = TestClient(create_app(any_store))

    by_revoked = client.get("/api/v1/projects/default/runs", headers=revoked)
    by_disabled = client.get("/api/v1/projects/default/runs", headers=disabled)

    _assert_unauthenticated(by_revoked, invalid=True)
    _assert_unauthenticated(by_disabled, invalid=True)
    assert by_revoked.json() == by_disabled.json()


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs", "scope"),
    [
        ("GET", "/api/v1/projects/default/runs", {}, READ_SCOPE),
        ("GET", "/api/v1/projects/default/config/sections", {}, READ_SCOPE),
        ("POST", "/api/v1/runs", {"json": _report()}, RECORD_SCOPE),
        ("POST", f"/api/v1/runs/{_RUN}/heartbeat", {}, RECORD_SCOPE),
        (
            "POST",
            "/api/v1/projects/default/config/sections",
            {"json": {"name": "Api", "prefix": "tests/api"}},
            ADMIN_SCOPE,
        ),
        (
            "DELETE",
            "/api/v1/projects/default/config/sections",
            {"params": {"name": "Api"}},
            ADMIN_SCOPE,
        ),
        ("GET", "/api/v1/projects/default/tests/history", {"params": {"node_id": "n"}}, READ_SCOPE),
        ("GET", "/api/v1/projects", {}, READ_SCOPE),
        ("POST", "/api/v1/projects", {"json": {"name": "web"}}, ADMIN_SCOPE),
    ],
)
def test_a_token_without_the_routes_scope_is_forbidden(
    any_store: ExecutionStore,
    method: str,
    path: str,
    request_kwargs: dict[str, Any],
    scope: str,
) -> None:
    """Every scope but the route's, held by an admin, is not enough; the
    route's own is."""
    _user(any_store, "alice", admin=True)
    others = sorted({READ_SCOPE, RECORD_SCOPE, ADMIN_SCOPE} - {scope})
    lacking = _bearer(any_store, "alice", *others)
    holding = _bearer(any_store, "alice", scope)
    client = TestClient(create_app(any_store))
    if path.endswith("/heartbeat"):
        client.post("/api/v1/runs", json=_report(finished=False), headers=holding)
    any_store.upsert_setting(
        TEST_SECTIONS_NAMESPACE,
        "Api",
        value='{"prefix": "tests/api/"}',
        updated_at=_NOW,
        project=DEFAULT_PROJECT,
    )

    forbidden = client.request(method, path, headers=lacking, **request_kwargs)
    allowed = client.request(method, path, headers=holding, **request_kwargs)

    assert forbidden.status_code == 403
    assert forbidden.json() == {
        "error": "insufficient_scope",
        "detail": f"The token does not grant the {scope} scope.",
        "fields": [],
    }
    assert forbidden.headers["WWW-Authenticate"] == (
        f'Bearer realm="vantage", error="insufficient_scope", scope="{scope}"'
    )
    assert allowed.status_code < 300, allowed.text


def test_the_admin_scope_needs_an_admin_user_now(any_store: ExecutionStore) -> None:
    """A token's admin scope grants nothing to a user who is not an admin,
    whether they never were one or stopped being one after it was made."""
    _user(any_store, "alice", admin=True)
    _user(any_store, "bob")
    alices = _bearer(any_store, "alice", ADMIN_SCOPE)
    bobs = _bearer(any_store, "bob", ADMIN_SCOPE)
    client = TestClient(create_app(any_store))
    section = {"name": "Api", "prefix": "tests/api"}

    by_admin = client.post("/api/v1/projects/default/config/sections", json=section, headers=alices)
    by_non_admin = client.post(
        "/api/v1/projects/default/config/sections", json=section, headers=bobs
    )
    any_store.update_user("alice", admin=False)
    by_former_admin = client.post(
        "/api/v1/projects/default/config/sections", json=section, headers=alices
    )

    assert by_admin.status_code == 201
    assert by_non_admin.status_code == 403
    assert by_former_admin.status_code == 403


def test_a_report_records_the_user_whose_token_sent_it(any_store: ExecutionStore) -> None:
    _user(any_store, "alice")
    headers = _bearer(any_store, "alice", READ_SCOPE, RECORD_SCOPE)
    client = TestClient(create_app(any_store))

    created = client.post("/api/v1/runs", json=_report(), headers=headers)
    detail = client.get(f"/api/v1/runs/{_RUN}", headers=headers)
    listed = client.get("/api/v1/projects/default/runs", headers=headers)

    assert created.status_code == 201
    assert detail.json()["recorded_by"] == "alice"
    assert [item["recorded_by"] for item in listed.json()["items"]] == ["alice"]


def test_another_users_report_of_a_run_is_refused_and_stores_nothing(
    any_store: ExecutionStore,
) -> None:
    _user(any_store, "alice")
    _user(any_store, "bob")
    alices = _bearer(any_store, "alice", RECORD_SCOPE)
    bobs = _bearer(any_store, "bob", RECORD_SCOPE)
    client = TestClient(create_app(any_store))
    client.post("/api/v1/runs", json={**_report(finished=False), "results": []}, headers=alices)

    refused = client.post("/api/v1/runs", json=_report(), headers=bobs)

    assert refused.status_code == 409
    assert refused.json() == {
        "error": "foreign_run",
        "detail": "This run was recorded by another user.",
        "fields": [],
    }
    execution = any_store.get_execution(_RUN)
    assert execution is not None and execution.exit_status is None
    assert any_store.get_results(_RUN) == []


def test_a_heartbeat_is_taken_from_the_runs_creator_alone(any_store: ExecutionStore) -> None:
    _user(any_store, "alice")
    _user(any_store, "bob")
    alices = _bearer(any_store, "alice", RECORD_SCOPE)
    bobs = _bearer(any_store, "bob", RECORD_SCOPE)
    client = TestClient(create_app(any_store))
    client.post("/api/v1/runs", json=_report(finished=False), headers=alices)
    detail = any_store.get_run_detail(_RUN)
    assert detail is not None
    contact = detail.last_contact_at

    by_bob = client.post(f"/api/v1/runs/{_RUN}/heartbeat", headers=bobs)
    after_bob = any_store.get_run_detail(_RUN)
    by_alice = client.post(f"/api/v1/runs/{_RUN}/heartbeat", headers=alices)

    assert by_bob.status_code == 409
    assert by_bob.json()["error"] == "foreign_run"
    assert after_bob is not None and after_bob.last_contact_at == contact
    assert by_alice.status_code == 200


def test_a_run_recorded_before_the_first_user_takes_nothing_from_a_token(
    any_store: ExecutionStore,
) -> None:
    """A session that started while the server was open cannot be carried
    on by whoever holds a token once it is closed."""
    client = TestClient(create_app(any_store))
    client.post("/api/v1/runs", json=_report(finished=False))
    _user(any_store, "alice")
    alices = _bearer(any_store, "alice", RECORD_SCOPE)

    heartbeat = client.post(f"/api/v1/runs/{_RUN}/heartbeat", headers=alices)
    finish = client.post("/api/v1/runs", json=_report(), headers=alices)

    assert (heartbeat.status_code, finish.status_code) == (409, 409)


def test_a_report_without_a_token_is_refused_before_its_body_is_read() -> None:
    """A body over the cap would be `413` once read; refused on its
    headers, it is never read at all."""
    store = InMemoryExecutionStore()
    _user(store, "alice")
    client = TestClient(create_app(store))

    response = client.post(
        "/api/v1/runs",
        content=b" " * (MAX_REPORT_BYTES + 1),
        headers={"content-type": "text/plain"},
    )

    _assert_unauthenticated(response, invalid=False)


def test_the_capabilities_and_the_document_need_no_token(any_store: ExecutionStore) -> None:
    """A client asks both before it can know it needs a token."""
    _user(any_store, "alice")
    client = TestClient(create_app(any_store))

    assert client.get("/api/v1/capabilities").status_code == 200
    assert client.get("/api/v1/openapi.yaml").status_code == 200


def test_a_token_never_comes_back_in_a_rejection(any_store: ExecutionStore) -> None:
    """Neither the body nor the challenge names the token a request sent."""
    _user(any_store, "alice")
    headers = _bearer(any_store, "alice", RECORD_SCOPE)
    token = headers["Authorization"].removeprefix("Bearer ")
    client = TestClient(create_app(any_store))
    unknown = f"{token[:-4]}abcd"

    for response in (
        client.get("/api/v1/projects/default/runs", headers=headers),
        client.get("/api/v1/projects/default/runs", headers={"Authorization": f"Bearer {unknown}"}),
    ):
        assert response.status_code in {401, 403}
        assert token not in response.text and unknown not in response.text
        assert token not in str(response.headers) and unknown not in str(response.headers)


def test_a_token_revoked_after_a_request_is_refused_on_the_next(
    any_store: ExecutionStore,
) -> None:
    """Nothing about a token is remembered between requests."""
    _user(any_store, "alice")
    headers = _bearer(any_store, "alice", READ_SCOPE)
    client = TestClient(create_app(any_store))
    assert client.get("/api/v1/projects/default/runs", headers=headers).status_code == 200

    (token,) = any_store.list_tokens()
    any_store.revoke_token(token.id, revoked_at=_NOW + timedelta(minutes=1))

    _assert_unauthenticated(
        client.get("/api/v1/projects/default/runs", headers=headers), invalid=True
    )


# --- Logging in and changing a password -----------------------------------------------------


@pytest.mark.parametrize(("path", "body"), _PASSWORD_ROUTES, ids=["login", "password"])
def test_logging_in_is_refused_while_nobody_has_a_password_and_answered_once_one_does(
    any_store: ExecutionStore, cheap_passwords: None, path: str, body: dict[str, str]
) -> None:
    """Neither route needs a token, so neither is opened by the server
    being open: both answer `409 open_server` there, as the users routes
    do, until the first user closes it, with no restart."""
    client = TestClient(create_app(any_store))

    while_open = client.post(path, json=body)
    _user(any_store, "alice")
    any_store.set_password("alice", password_hash=hash_password(_PASSWORD), changed_at=_NOW)
    once_closed = client.post(path, json=body)

    assert while_open.status_code == 409
    assert while_open.json()["error"] == "open_server"
    assert once_closed.status_code in {201, 204}, once_closed.text


@pytest.mark.parametrize(("path", "body"), _PASSWORD_ROUTES, ids=["login", "password"])
def test_asking_whether_a_user_exists_before_a_login_holds_up_no_other_request(
    path: str, body: dict[str, str]
) -> None:
    """Whether the server is open is asked of the store before the body is
    read, in a plain `def` dependency FastAPI runs in its threadpool. Asked
    on the event loop, it would stall every other request until the store
    answered, heartbeats included."""
    store = InMemoryExecutionStore()
    entered, release = threading.Event(), threading.Event()
    access_required = store.access_required

    def held() -> bool:
        entered.set()
        release.wait(2 * _JOIN_TIMEOUT_SECONDS)
        return access_required()

    store.access_required = held  # type: ignore[method-assign]
    answered: dict[str, int] = {}

    with TestClient(create_app(store)) as client:

        def _held_request() -> None:
            answered["held"] = client.post(path, json=body).status_code

        def _capabilities() -> None:
            answered["capabilities"] = client.get("/api/v1/capabilities").status_code

        holder = threading.Thread(target=_held_request, daemon=True)
        other = threading.Thread(target=_capabilities, daemon=True)
        holder.start()
        try:
            assert entered.wait(_JOIN_TIMEOUT_SECONDS), f"{path} never asked whether a user exists"
            other.start()
            other.join(timeout=_JOIN_TIMEOUT_SECONDS)
            assert not other.is_alive(), f"a request waited for {path}'s question"
            assert "held" not in answered
        finally:
            release.set()
            holder.join(timeout=_JOIN_TIMEOUT_SECONDS)
            other.join(timeout=_JOIN_TIMEOUT_SECONDS)

    assert answered == {"capabilities": 200, "held": 409}
