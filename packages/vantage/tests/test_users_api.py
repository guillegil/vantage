"""Managing users and tokens over HTTP (`service/routes/users.py`): only an
admin's token may, on every server; the command line's checks apply, in its
order; an admin cannot demote or disable themselves; a token is shown once
and never cached; revoking is idempotent; nothing a caller sends comes back
in a rejection.

Run against every adapter (`any_store`): the routes are the service's, but
every answer rests on what the store says of users and tokens.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from vantage.core.domain.access import (
    ADMIN_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    SCOPES,
    User,
    new_token,
    token_digest,
)
from vantage.core.ports.storage import ExecutionStore
from vantage.service.app import create_app
from vantage.service.schemas import CreatedTokenResponse

# `any_store`, each adapter in turn.
pytest_plugins = ["store_fixtures"]

_NOW = datetime(2026, 9, 28, 9, 0, 0, tzinfo=timezone.utc)

# Every operation, as `(method, path, body)`, for the tests that ask all of
# them the same question.
_OPERATIONS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/users", None),
    ("POST", "/api/v1/users", {"name": "carol"}),
    ("PATCH", "/api/v1/users/bob", {"admin": True}),
    ("GET", "/api/v1/tokens", None),
    ("POST", "/api/v1/tokens", {"user": "bob"}),
    ("POST", "/api/v1/tokens/1/revoke", None),
]


def _token(store: ExecutionStore, user: str, *scopes: str) -> str:
    token = new_token()
    store.create_token(
        user, digest=token_digest(token), label="", scopes=frozenset(scopes), created_at=_NOW
    )
    return token


def _instant(text: str) -> datetime:
    """A timestamp as the API writes it; Python 3.10 reads no `Z`."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _call(client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> Any:
    return client.request(method, path, json=body)


@pytest.fixture
def admin(any_store: ExecutionStore) -> Iterator[TestClient]:
    """A client sending the token of `alice`, an admin, which holds only the
    admin scope, on a store that also has `bob`, who is not an admin."""
    any_store.create_user("alice", admin=True, created_at=_NOW)
    any_store.create_user("bob", admin=False, created_at=_NOW)
    token = _token(any_store, "alice", ADMIN_SCOPE)
    yield TestClient(create_app(any_store), headers=_bearer(token))


def _assert_rejected(response: Any, status: int, error: str, fields: list[str]) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert (body["error"], body["fields"]) == (error, fields)


# --- Bootstrap: the first user is the command line's ---------------------------------


@pytest.mark.parametrize(("method", "path", "body"), _OPERATIONS)
def test_an_open_server_answers_open_server_to_anyone_and_makes_no_user(
    any_store: ExecutionStore, method: str, path: str, body: dict[str, Any] | None
) -> None:
    client = TestClient(create_app(any_store))

    response = _call(client, method, path, body)

    _assert_rejected(response, 409, "open_server", [])
    assert "vantage user add NAME --admin" in response.json()["detail"]
    assert "WWW-Authenticate" not in response.headers
    assert any_store.access_required() is False


def test_a_token_sent_to_an_open_server_is_refused_as_ever(any_store: ExecutionStore) -> None:
    client = TestClient(create_app(any_store), headers=_bearer(new_token()))

    response = client.post("/api/v1/users", json={"name": "alice", "admin": True})

    _assert_rejected(response, 401, "unauthenticated", [])
    assert any_store.list_users() == ()


class _JustClosed(InMemoryExecutionStore):
    """A store the first `vantage user add` reaches between two looks: it
    still says no user exists, while `alice`, an admin, already does."""

    def access_required(self) -> bool:
        return False

    def create_token(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("an anonymous caller reached create_token")


def test_an_anonymous_caller_racing_the_first_user_is_refused_for_who_it_is() -> None:
    """The refusal is decided by who calls, never by asking the store again,
    so a request passing the open check just before the first admin commits
    still cannot mint that admin a token."""
    store = _JustClosed()
    store.create_user("alice", admin=True, created_at=_NOW)
    client = TestClient(create_app(store))

    response = client.post("/api/v1/tokens", json={"user": "alice", "scopes": ["admin"]})

    _assert_rejected(response, 409, "open_server", [])


# --- Who may call ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), _OPERATIONS)
def test_only_an_admin_token_of_an_admin_gets_through(
    any_store: ExecutionStore, method: str, path: str, body: dict[str, Any] | None
) -> None:
    any_store.create_user("alice", admin=True, created_at=_NOW)
    any_store.create_user("bob", admin=False, created_at=_NOW)
    without_admin = _token(any_store, "alice", READ_SCOPE, RECORD_SCOPE)
    of_non_admin = _token(any_store, "bob", *SCOPES)
    client = TestClient(create_app(any_store))

    anonymous = _call(client, method, path, body)
    client.headers.update(_bearer(without_admin))
    lacking = _call(client, method, path, body)
    client.headers.update(_bearer(of_non_admin))
    non_admin = _call(client, method, path, body)

    _assert_rejected(anonymous, 401, "unauthenticated", [])
    _assert_rejected(lacking, 403, "insufficient_scope", [])
    _assert_rejected(non_admin, 403, "insufficient_scope", [])
    assert [user.name for user in any_store.list_users()] == ["alice", "bob"]


def test_a_demoted_admins_token_stops_working_and_a_promoted_ones_starts(
    any_store: ExecutionStore,
) -> None:
    any_store.create_user("alice", admin=True, created_at=_NOW)
    token = _token(any_store, "alice", ADMIN_SCOPE)
    client = TestClient(create_app(any_store), headers=_bearer(token))

    any_store.update_user("alice", admin=False)
    demoted = client.get("/api/v1/users")
    any_store.update_user("alice", admin=True)
    promoted = client.get("/api/v1/users")

    assert (demoted.status_code, promoted.status_code) == (403, 200)


def test_a_disabled_admins_token_is_not_valid(any_store: ExecutionStore) -> None:
    any_store.create_user("alice", admin=True, created_at=_NOW)
    token = _token(any_store, "alice", ADMIN_SCOPE)
    any_store.update_user("alice", disabled=True)

    response = TestClient(create_app(any_store), headers=_bearer(token)).get("/api/v1/users")

    _assert_rejected(response, 401, "unauthenticated", [])


@pytest.mark.parametrize(
    ("method", "path"),
    [("PATCH", "/api/v1/users/ghost"), ("POST", "/api/v1/tokens/999/revoke")],
)
def test_nobody_but_an_admin_learns_which_names_and_ids_exist(
    any_store: ExecutionStore, method: str, path: str
) -> None:
    """Authorization comes before the path is looked at, so the answer is
    the same for a name or an id that exists and one that does not."""
    any_store.create_user("alice", admin=True, created_at=_NOW)
    reader = _token(any_store, "alice", READ_SCOPE, RECORD_SCOPE)
    client = TestClient(create_app(any_store))
    body = {"admin": True} if method == "PATCH" else None

    anonymous = client.request(method, path, json=body)
    for_malformed = client.post("/api/v1/tokens/NOT-AN-ID/revoke")
    by_reader = client.request(method, path, json=body, headers=_bearer(reader))

    assert (anonymous.status_code, for_malformed.status_code, by_reader.status_code) == (
        401,
        401,
        403,
    )


# --- Users ----------------------------------------------------------------------------------


def test_a_created_user_is_enabled_and_listed(admin: TestClient, any_store: ExecutionStore) -> None:
    created = admin.post("/api/v1/users", json={"name": "carol", "admin": True})
    plain = admin.post("/api/v1/users", json={"name": "dave"})
    listed = admin.get("/api/v1/users")

    assert created.status_code == 201
    body = created.json()
    assert {key: body[key] for key in ("name", "admin", "disabled")} == {
        "name": "carol",
        "admin": True,
        "disabled": False,
    }
    stored = any_store.get_user("carol")
    assert stored is not None
    assert _instant(body["created_at"]) == stored.created_at
    assert plain.json()["admin"] is False
    assert [user["name"] for user in listed.json()["items"]] == ["alice", "bob", "carol", "dave"]


def test_users_list_with_their_standing_disabled_ones_included(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    any_store.update_user("bob", disabled=True)

    items = admin.get("/api/v1/users").json()["items"]

    assert [(item["name"], item["admin"], item["disabled"]) for item in items] == [
        ("alice", True, False),
        ("bob", False, True),
    ]
    assert set(items[0]) == {"name", "admin", "disabled", "created_at"}


def test_a_taken_name_is_refused_and_the_user_left_as_it_was(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    response = admin.post("/api/v1/users", json={"name": "bob", "admin": True})

    _assert_rejected(response, 409, "user_exists", ["name"])
    bob = any_store.get_user("bob")
    assert bob is not None and bob.admin is False


@pytest.mark.parametrize("name", ["Bob", "-bob", "", "b" * 65, "bo b", "bob\x00"])
def test_a_name_no_user_can_have_is_refused(
    admin: TestClient, any_store: ExecutionStore, name: str
) -> None:
    response = admin.post("/api/v1/users", json={"name": name})

    _assert_rejected(response, 422, "invalid_user_name", ["name"])
    assert len(any_store.list_users()) == 2


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": 1},
        {"name": "carol", "admin": "true"},
        {"name": "carol", "admin": 1},
        {"name": "carol", "role": "admin"},
        ["carol"],
    ],
)
def test_a_body_of_another_shape_creates_nobody(
    admin: TestClient, any_store: ExecutionStore, body: Any
) -> None:
    response = admin.post("/api/v1/users", json=body)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_user_request"
    assert any_store.get_user("carol") is None


@pytest.mark.parametrize(
    ("change", "admin_after", "disabled_after"),
    [
        ({"admin": True}, True, False),
        ({"disabled": True}, False, True),
        ({"admin": True, "disabled": True}, True, True),
        ({"admin": False, "disabled": None}, False, False),
    ],
)
def test_an_update_sets_what_it_is_given_and_answers_the_user(
    admin: TestClient,
    any_store: ExecutionStore,
    change: dict[str, Any],
    admin_after: bool,
    disabled_after: bool,
) -> None:
    response = admin.patch("/api/v1/users/bob", json=change)

    assert response.status_code == 200, response.text
    assert (response.json()["admin"], response.json()["disabled"]) == (admin_after, disabled_after)
    bob = any_store.get_user("bob")
    assert bob is not None and (bob.admin, bob.disabled) == (admin_after, disabled_after)


@pytest.mark.parametrize(
    ("before", "change", "after"),
    [
        ({"admin": True}, {"disabled": True}, (True, True)),
        ({"admin": True}, {"admin": None, "disabled": True}, (True, True)),
        ({"disabled": True}, {"admin": True}, (True, True)),
        ({"disabled": True}, {"admin": True, "disabled": None}, (True, True)),
    ],
)
def test_a_field_left_out_or_null_is_left_as_it_is(
    admin: TestClient,
    any_store: ExecutionStore,
    before: dict[str, bool],
    change: dict[str, Any],
    after: tuple[bool, bool],
) -> None:
    """Set away from its default first, so a field reset instead of left
    alone shows."""
    any_store.update_user("bob", **before)

    response = admin.patch("/api/v1/users/bob", json=change)

    assert (response.json()["admin"], response.json()["disabled"]) == after
    bob = any_store.get_user("bob")
    assert bob is not None and (bob.admin, bob.disabled) == after


def test_enabling_a_user_again_lets_its_tokens_back_in(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    bobs = _token(any_store, "bob", READ_SCOPE)
    reader = TestClient(admin.app, headers=_bearer(bobs))

    admin.patch("/api/v1/users/bob", json={"disabled": True})
    while_disabled = reader.get("/api/v1/projects/default/runs")
    admin.patch("/api/v1/users/bob", json={"disabled": False})

    assert while_disabled.status_code == 401
    assert reader.get("/api/v1/projects/default/runs").status_code == 200


@pytest.mark.parametrize(
    "body", [{}, {"admin": None}, {"admin": None, "disabled": None}], ids=["empty", "null", "nulls"]
)
def test_an_update_that_changes_nothing_is_refused(admin: TestClient, body: Any) -> None:
    response = admin.patch("/api/v1/users/bob", json=body)

    _assert_rejected(response, 422, "invalid_user_request", ["admin", "disabled"])
    assert response.json()["detail"] == "Say what to change: admin, disabled, or both."


def test_a_misspelt_field_changes_nothing_at_all(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    """`disable` for `disabled`: refused whole rather than half applied."""
    response = admin.patch("/api/v1/users/bob", json={"admin": True, "disable": True})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_user_request"
    assert any_store.get_user("bob") == User(
        name="bob", admin=False, disabled=False, created_at=_NOW
    )


@pytest.mark.parametrize(
    ("content", "content_type"),
    [(b" " * (16 * 1024 + 1), "application/json"), (b"{", "application/json")],
    ids=["too-large", "not-json"],
)
def test_a_name_no_user_can_have_is_answered_before_the_body_is_read(
    admin: TestClient, content: bytes, content_type: str
) -> None:
    """A body that would be refused once read is never read."""
    response = admin.patch(
        "/api/v1/users/NOT-A-NAME", content=content, headers={"content-type": content_type}
    )

    _assert_rejected(response, 404, "unknown_user", [])


def test_updating_nobody_is_unknown_user(admin: TestClient) -> None:
    response = admin.patch("/api/v1/users/ghost", json={"admin": True})

    _assert_rejected(response, 404, "unknown_user", [])


@pytest.mark.parametrize("name", ["Bob", "b" * 65, "bob%00"])
def test_a_name_no_user_can_have_is_nobody_without_asking_the_store(name: str) -> None:
    store = InMemoryExecutionStore()
    store.create_user("alice", admin=True, created_at=_NOW)
    asked: list[str] = []
    update_user = store.update_user

    def spy(name: str, *, admin: bool | None = None, disabled: bool | None = None) -> User | None:
        asked.append(name)
        return update_user(name, admin=admin, disabled=disabled)

    store.update_user = spy  # type: ignore[method-assign]
    client = TestClient(create_app(store), headers=_bearer(_token(store, "alice", ADMIN_SCOPE)))

    response = client.patch(f"/api/v1/users/{name}", json={"admin": True})

    _assert_rejected(response, 404, "unknown_user", [])
    assert asked == []


@pytest.mark.parametrize("change", [{"admin": False}, {"disabled": True}])
def test_an_admin_cannot_demote_or_disable_their_own_user(
    admin: TestClient, any_store: ExecutionStore, change: dict[str, Any]
) -> None:
    response = admin.patch("/api/v1/users/alice", json=change)

    _assert_rejected(response, 409, "own_account", [])
    alice = any_store.get_user("alice")
    assert alice is not None and (alice.admin, alice.disabled) == (True, False)
    assert admin.get("/api/v1/users").status_code == 200


def test_an_admin_may_confirm_their_own_standing(admin: TestClient) -> None:
    response = admin.patch("/api/v1/users/alice", json={"admin": True, "disabled": False})

    assert response.status_code == 200


def test_another_admin_can_demote_an_admin_whose_token_then_stops_working(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    any_store.create_user("carol", admin=True, created_at=_NOW)
    carols = TestClient(admin.app, headers=_bearer(_token(any_store, "carol", ADMIN_SCOPE)))

    demoted = admin.patch("/api/v1/users/carol", json={"admin": False})

    assert demoted.status_code == 200
    assert carols.get("/api/v1/users").status_code == 403


# --- Tokens ---------------------------------------------------------------------------------


def test_a_created_token_is_answered_once_uncached_and_grants_its_scopes(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    response = admin.post(
        "/api/v1/tokens", json={"user": "bob", "scopes": ["record"], "label": "ci nightly"}
    )

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    secret = body["token"]
    assert secret.startswith("vantage_")
    (stored,) = any_store.list_tokens(user="bob")
    assert {key: body[key] for key in ("id", "user", "label", "scopes", "revoked_at")} == {
        "id": stored.id,
        "user": "bob",
        "label": "ci nightly",
        "scopes": ["record"],
        "revoked_at": None,
    }
    grant = any_store.authenticate(token_digest(secret))
    assert grant is not None and grant.scopes == frozenset({RECORD_SCOPE})
    listed = admin.get("/api/v1/tokens")
    assert secret not in listed.text
    assert token_digest(secret) not in listed.text


def test_a_token_holds_read_and_record_unless_told_otherwise(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    body = admin.post("/api/v1/tokens", json={"user": "bob"}).json()

    assert body["scopes"] == ["read", "record"]
    assert body["label"] == ""
    grant = any_store.authenticate(token_digest(body["token"]))
    assert grant is not None and grant.scopes == frozenset({READ_SCOPE, RECORD_SCOPE})


def test_repeated_scopes_are_one(admin: TestClient) -> None:
    body = admin.post(
        "/api/v1/tokens", json={"user": "alice", "scopes": ["admin", "read", "admin"]}
    ).json()

    assert body["scopes"] == ["admin", "read"]


def test_the_admin_scope_is_for_an_admin_only(admin: TestClient, any_store: ExecutionStore) -> None:
    refused = admin.post("/api/v1/tokens", json={"user": "bob", "scopes": ["admin"]})
    granted = admin.post("/api/v1/tokens", json={"user": "alice", "scopes": ["admin"]})

    _assert_rejected(refused, 409, "not_an_admin", ["scopes"])
    assert any_store.list_tokens(user="bob") == ()
    assert granted.status_code == 201


@pytest.mark.parametrize("user", ["ghost", "Ghost", "g" * 65])
def test_a_token_for_nobody_is_unknown_user_and_nothing_is_stored(
    admin: TestClient, any_store: ExecutionStore, user: str
) -> None:
    response = admin.post("/api/v1/tokens", json={"user": user})

    _assert_rejected(response, 404, "unknown_user", ["user"])
    assert len(any_store.list_tokens()) == 1


@pytest.mark.parametrize("scopes", [[], ["write"], ["read", "owner"]])
def test_scopes_that_are_not_scopes_are_refused(admin: TestClient, scopes: list[str]) -> None:
    response = admin.post("/api/v1/tokens", json={"user": "bob", "scopes": scopes})

    _assert_rejected(response, 422, "invalid_scopes", ["scopes"])


@pytest.mark.parametrize(
    "label",
    ['"' + "x" * 201 + '"', '"two\\nlines"', '"tab\\there"', '"\\ud800"'],
    ids=["too-long", "newline", "tab", "lone-surrogate"],
)
def test_a_label_that_cannot_label_a_token_is_refused(
    admin: TestClient, any_store: ExecutionStore, label: str
) -> None:
    """`label` is JSON text: a lone surrogate can only be sent escaped."""
    response = admin.post(
        "/api/v1/tokens",
        content=f'{{"user": "bob", "label": {label}}}'.encode(),
        headers={"content-type": "application/json"},
    )

    _assert_rejected(response, 422, "invalid_token_label", ["label"])
    assert any_store.list_tokens(user="bob") == ()


def test_a_label_of_200_characters_is_kept_whole(admin: TestClient) -> None:
    body = admin.post("/api/v1/tokens", json={"user": "bob", "label": "é" * 200}).json()

    assert body["label"] == "é" * 200


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"user": 1},
        {"user": "bob", "scopes": "read"},
        {"user": "bob", "label": None},
        {"user": "bob", "expires": "never"},
    ],
)
def test_a_token_body_of_another_shape_makes_no_token(
    admin: TestClient, any_store: ExecutionStore, body: Any
) -> None:
    response = admin.post("/api/v1/tokens", json=body)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_token_request"
    assert any_store.list_tokens(user="bob") == ()


def test_a_token_for_a_disabled_user_is_made_and_works_once_they_are_enabled(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    any_store.update_user("bob", disabled=True)

    body = admin.post("/api/v1/tokens", json={"user": "bob", "scopes": ["read"]}).json()
    reader = TestClient(admin.app, headers=_bearer(body["token"]))
    while_disabled = reader.get("/api/v1/projects/default/runs")
    any_store.update_user("bob", disabled=False)

    assert while_disabled.status_code == 401
    assert reader.get("/api/v1/projects/default/runs").status_code == 200


def test_tokens_list_oldest_first_for_everyone_or_one_user(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    for user in ("bob", "alice", "bob"):
        admin.post("/api/v1/tokens", json={"user": user, "label": f"for {user}"})
    first_bobs = any_store.list_tokens(user="bob")[0]
    admin.post(f"/api/v1/tokens/{first_bobs.id}/revoke")

    everyone = admin.get("/api/v1/tokens").json()["items"]
    bobs = admin.get("/api/v1/tokens", params={"user": "bob"}).json()["items"]

    assert [item["user"] for item in everyone] == ["alice", "bob", "alice", "bob"]
    assert [item["id"] for item in everyone] == sorted(item["id"] for item in everyone)
    assert [item["id"] for item in bobs] == [
        token.id for token in any_store.list_tokens(user="bob")
    ]
    assert bobs[0]["revoked_at"] is not None and bobs[1]["revoked_at"] is None
    assert set(bobs[0]) == {"id", "user", "label", "scopes", "created_at", "revoked_at"}


@pytest.mark.parametrize("user", ["ghost", "NOT-A-NAME", "a\x00b"])
def test_tokens_of_nobody_are_none(admin: TestClient, user: str) -> None:
    response = admin.get("/api/v1/tokens", params={"user": user})

    assert (response.status_code, response.json()) == (200, {"items": []})


@pytest.mark.parametrize("user", ["NOT-A-NAME", "a\x00b", "g" * 65])
def test_tokens_of_a_user_nobody_can_be_are_none_without_asking_the_store(user: str) -> None:
    store = InMemoryExecutionStore()
    store.create_user("alice", admin=True, created_at=_NOW)
    client = TestClient(create_app(store), headers=_bearer(_token(store, "alice", ADMIN_SCOPE)))
    asked: list[str | None] = []
    list_tokens = store.list_tokens

    def spy(*, user: str | None = None) -> Any:
        asked.append(user)
        return list_tokens(user=user)

    store.list_tokens = spy  # type: ignore[method-assign]

    response = client.get("/api/v1/tokens", params={"user": user})

    assert (response.status_code, response.json()) == (200, {"items": []})
    assert asked == []


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"user": "ghost", "scopes": [], "label": "a\nb"}, "invalid_scopes"),
        ({"user": "ghost", "label": "a\nb"}, "invalid_token_label"),
        ({"user": "ghost", "scopes": ["admin"]}, "unknown_user"),
        ({"user": "bob", "scopes": ["admin"], "label": "a\nb"}, "invalid_token_label"),
    ],
)
def test_a_token_request_with_several_faults_names_the_first_the_commands_check(
    admin: TestClient, body: dict[str, Any], error: str
) -> None:
    """Scopes, then the label, then the user, then whether the admin scope
    is the user's to hold -- the order `vantage token create` checks in."""
    response = admin.post("/api/v1/tokens", json=body)

    assert response.json()["error"] == error


def test_the_token_never_shows_in_the_models_repr() -> None:
    created = CreatedTokenResponse(
        token="vantage_secret",
        id=1,
        user="bob",
        label="",
        scopes=["read"],
        created_at=_NOW,
        revoked_at=None,
    )

    assert "vantage_secret" not in repr(created)
    assert "vantage_secret" not in str(created)


# --- Revoking -------------------------------------------------------------------------------


def test_revoking_answers_the_token_and_again_the_same_first_time(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    secret = _token(any_store, "bob", READ_SCOPE)
    (token,) = any_store.list_tokens(user="bob")

    first = admin.post(f"/api/v1/tokens/{token.id}/revoke")
    again = admin.post(f"/api/v1/tokens/{token.id}/revoke")

    assert (first.status_code, again.status_code) == (200, 200)
    assert first.json() == again.json()
    assert first.json()["revoked_at"] is not None
    assert "token" not in first.json()
    assert any_store.authenticate(token_digest(secret)) is None
    reader = TestClient(admin.app, headers=_bearer(secret))
    assert reader.get("/api/v1/projects/default/runs").status_code == 401


def test_revoking_a_token_nobody_has_is_unknown_token(admin: TestClient) -> None:
    response = admin.post("/api/v1/tokens/999/revoke")

    _assert_rejected(response, 404, "unknown_token", [])


@pytest.mark.parametrize("token_id", ["0", "-1", str(2**63), "x"])
def test_an_id_no_token_can_have_is_an_invalid_parameter(admin: TestClient, token_id: str) -> None:
    response = admin.post(f"/api/v1/tokens/{token_id}/revoke")

    _assert_rejected(response, 422, "invalid_parameter", ["path.token_id"])


def test_an_admin_may_revoke_the_token_they_are_using(admin: TestClient) -> None:
    listed = admin.get("/api/v1/tokens", params={"user": "alice"}).json()["items"]

    response = admin.post(f"/api/v1/tokens/{listed[0]['id']}/revoke")

    assert response.status_code == 200
    assert admin.get("/api/v1/users").status_code == 401


def test_revoking_reads_no_body(admin: TestClient, any_store: ExecutionStore) -> None:
    _token(any_store, "bob", READ_SCOPE)
    (token,) = any_store.list_tokens(user="bob")

    response = admin.post(
        f"/api/v1/tokens/{token.id}/revoke",
        content=b"not json",
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 200


# --- Bodies and what comes back -------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/api/v1/users"), ("PATCH", "/api/v1/users/bob"), ("POST", "/api/v1/tokens")],
)
def test_a_body_route_checks_its_media_type_and_size(
    admin: TestClient, method: str, path: str
) -> None:
    wrong_type = admin.request(method, path, content=b"{}", headers={"content-type": "text/plain"})
    too_large = admin.request(
        method, path, content=b" " * (16 * 1024 + 1), headers={"content-type": "application/json"}
    )
    not_json = admin.request(
        method, path, content=b"{", headers={"content-type": "application/json"}
    )

    assert (wrong_type.status_code, too_large.status_code, not_json.status_code) == (415, 413, 400)


def test_nothing_a_caller_sends_comes_back_in_a_rejection(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    hostile = "</script>\r\nX-Injected: 1"
    secret = _token(any_store, "bob", READ_SCOPE)

    responses = [
        admin.post("/api/v1/users", json={"name": hostile}),
        admin.post("/api/v1/users", json={"name": "carol", hostile: True}),
        admin.post("/api/v1/tokens", json={"user": hostile}),
        admin.post("/api/v1/tokens", json={"user": "bob", "label": hostile * 20}),
        admin.post("/api/v1/tokens", json={"user": "bob", "scopes": [hostile]}),
        admin.patch("/api/v1/users/bob", json={hostile: True}),
        TestClient(admin.app).get("/api/v1/users", headers=_bearer(secret)),
    ]

    for response in responses:
        assert response.status_code >= 400
        assert "script" not in response.text
        assert secret not in response.text
        assert secret not in str(response.headers)


def test_a_label_holding_u0000_is_stored_with_ufffd(
    admin: TestClient, any_store: ExecutionStore
) -> None:
    response = admin.post("/api/v1/tokens", json={"user": "bob", "label": "a\x00b"})

    assert response.status_code == 201
    (token,) = any_store.list_tokens(user="bob")
    assert token.label == "a�b"
