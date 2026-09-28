"""Passwords over HTTP (`service/routes/login.py`): logging in trades a name
and a password for a login token that reads and manages, and administers
for an admin, but never records, and expires; changing your own password
takes the current one; an admin sets anyone's; every password set revokes
that user's login tokens and no other; every failed check reads alike and
costs the same; at most two hashes run at once, 32 wait and any more are
refused; and no password ever comes back.

Run against every adapter (`any_store`) where an answer rests on what the
store says of users, passwords and tokens; the spies and the concurrency
check use the in-memory one. Every hash here is made at a tiny cost
(`cheap_passwords`), the routes' own included.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, MutableMapping
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from pydantic import ValidationError
from vantage.core.domain import passwords
from vantage.core.domain.access import (
    ADMIN_SCOPE,
    LOGIN_TOKEN_LIFETIME,
    READ_SCOPE,
    RECORD_SCOPE,
    SCOPES,
    Token,
    new_token,
    token_digest,
)
from vantage.core.domain.passwords import hash_password, verify_password
from vantage.core.ports.storage import ExecutionStore
from vantage.service import access
from vantage.service.app import create_app
from vantage.service.routes import login as login_routes
from vantage.service.schemas import LoginRequest, PasswordChangeRequest, PasswordSetRequest

# `any_store`, each adapter in turn; `cheap_passwords`, for every hash.
pytest_plugins = ["store_fixtures", "password_fixtures"]

pytestmark = pytest.mark.usefixtures("cheap_passwords")

_NOW = datetime(2026, 9, 28, 9, 0, 0, tzinfo=timezone.utc)

# Spaces keep each one from reading as a field name, which a rejection may
# repeat, so finding one in an answer can only mean a value came back.
_PASSWORD = "correct horse battery staple"
_NEW_PASSWORD = "a new one, long enough to set"
_SECRET = "a secret nobody reads back"

_MAX_BODY_BYTES = 16 * 1024
_JOIN_TIMEOUT_SECONDS = 10

_INVALID_CREDENTIALS = {
    "error": "invalid_credentials",
    "detail": "The name or password is not valid.",
    "fields": [],
}


def _user(
    store: ExecutionStore, name: str, *, admin: bool = False, password: str | None = _PASSWORD
) -> None:
    store.create_user(name, admin=admin, created_at=_NOW)
    if password is not None:
        store.set_password(name, password_hash=hash_password(password), changed_at=_NOW)


def _token(store: ExecutionStore, user: str, *scopes: str) -> str:
    """A made token of `user` holding `scopes`."""
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


def _log_in(client: TestClient, name: str = "bob", password: str = _PASSWORD) -> Any:
    return client.post("/api/v1/login", json={"name": name, "password": password})


def _logged_in(client: TestClient, name: str = "bob", password: str = _PASSWORD) -> str:
    """A login token of `name`."""
    response = _log_in(client, name, password)
    assert response.status_code == 201, response.text
    token: str = response.json()["token"]
    return token


def _change(
    client: TestClient, name: str = "bob", password: str = _PASSWORD, new: str = _NEW_PASSWORD
) -> Any:
    return client.post(
        "/api/v1/password", json={"name": name, "password": password, "new_password": new}
    )


def _set(client: TestClient, admin_token: str, name: str = "bob", new: str = _NEW_PASSWORD) -> Any:
    return client.put(
        f"/api/v1/users/{name}/password", json={"password": new}, headers=_bearer(admin_token)
    )


def _reads(client: TestClient, token: str) -> int:
    """The status of reading the run list with `token`."""
    return client.get("/api/v1/projects/default/runs", headers=_bearer(token)).status_code


def _assert_rejected(response: Any, status: int, error: str, fields: list[str]) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert (body["error"], body["fields"]) == (error, fields)


def _assert_invalid_credentials(response: Any) -> None:
    assert response.status_code == 401, response.text
    assert response.json() == _INVALID_CREDENTIALS
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="vantage"'


def _count_checks(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """The stored hash each password check the routes make is made against,
    in order; every check still runs."""
    checked: list[str | None] = []
    verify = verify_password

    def counting(password: str, stored: str | None) -> bool:
        checked.append(stored)
        return verify(password, stored)

    monkeypatch.setattr(login_routes, "verify_password", counting)
    return checked


def _count_hashes(monkeypatch: pytest.MonkeyPatch) -> list[None]:
    """One entry for each scrypt computed, to check a password or to hash one."""
    computed: list[None] = []
    scrypt = passwords._scrypt

    def counting(password: bytes, salt: bytes, cost: Any) -> bytes:
        computed.append(None)
        return scrypt(password, salt, cost)

    monkeypatch.setattr(passwords, "_scrypt", counting)
    return computed


@pytest.fixture
def client(any_store: ExecutionStore) -> TestClient:
    """A client sending no token, to a server whose users are `alice`, an
    admin, and `bob`, who is not, both with `_PASSWORD`."""
    _user(any_store, "alice", admin=True)
    _user(any_store, "bob")
    return TestClient(create_app(any_store))


# --- Logging in -----------------------------------------------------------------------------


def test_a_login_answers_an_uncached_token_expiring_twelve_hours_on(
    client: TestClient, any_store: ExecutionStore
) -> None:
    response = _log_in(client)

    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == {
        "token",
        "id",
        "user",
        "label",
        "scopes",
        "created_at",
        "revoked_at",
        "expires_at",
    }
    assert body["token"].startswith("vantage_")
    assert (body["user"], body["label"], body["revoked_at"]) == ("bob", "login", None)
    assert _instant(body["expires_at"]) - _instant(body["created_at"]) == LOGIN_TOKEN_LIFETIME
    (stored,) = any_store.list_tokens(user="bob")
    assert stored.id == body["id"]
    assert stored.expires_at == _instant(body["expires_at"])
    assert token_digest(body["token"]) not in response.text


@pytest.mark.parametrize(
    ("name", "scopes", "administers"),
    [("alice", ["admin", "manage", "read"], 200), ("bob", ["manage", "read"], 403)],
    ids=["admin", "not-an-admin"],
)
def test_a_login_token_reads_manages_and_administers_as_its_user_stands_but_never_records(
    client: TestClient, name: str, scopes: list[str], administers: int
) -> None:
    """Recording is for a made token, so a login that leaks cannot inject
    runs; what it manages is bounded by its user's role in each project --
    every user edits `default`'s sections -- and the admin scope is only
    ever an admin's."""
    response = _log_in(client, name)
    token = response.json()["token"]

    records = client.post("/api/v1/runs", json={}, headers=_bearer(token))
    manages = client.post(
        "/api/v1/projects/default/config/sections",
        json={"name": "unit", "prefix": "tests/unit"},
        headers=_bearer(token),
    )

    assert response.json()["scopes"] == scopes
    assert _reads(client, token) == 200
    assert manages.status_code == 201, manages.text
    assert client.get("/api/v1/users", headers=_bearer(token)).status_code == administers
    _assert_rejected(records, 403, "insufficient_scope", [])
    assert records.headers["WWW-Authenticate"] == (
        f'Bearer realm="vantage", error="insufficient_scope", scope="{RECORD_SCOPE}"'
    )


def _meanwhile(change: Callable[[ExecutionStore], object]) -> Callable[[ExecutionStore], None]:
    """Make `change` happen to the store after a login has checked the
    password and before it stores its token, as a request running alongside
    would."""

    def arrange(store: ExecutionStore) -> None:
        create_login_token = store.create_login_token

        def changed_first(name: str, **kwargs: Any) -> Token | None:
            change(store)
            return create_login_token(name, **kwargs)

        store.create_login_token = changed_first  # type: ignore[method-assign]

    return arrange


# Every way a login fails, as `(name, password, what to do to the store
# first)`, against the users of `client`.
_FAILED_LOGINS: dict[str, tuple[str, str, Callable[[ExecutionStore], object] | None]] = {
    "unknown-name": ("ghost", _PASSWORD, None),
    "a-name-nobody-can-have": ("NOT-A-NAME", _PASSWORD, None),
    "no-password": ("carol", _PASSWORD, lambda store: _user(store, "carol", password=None)),
    "disabled": ("bob", _PASSWORD, lambda store: store.update_user("bob", disabled=True)),
    "wrong-password": ("bob", _NEW_PASSWORD, None),
    "password-changed-meanwhile": (
        "bob",
        _PASSWORD,
        _meanwhile(
            lambda store: store.set_password(
                "bob", password_hash=hash_password(_NEW_PASSWORD), changed_at=_NOW
            )
        ),
    ),
    "disabled-meanwhile": (
        "bob",
        _PASSWORD,
        _meanwhile(lambda store: store.update_user("bob", disabled=True)),
    ),
}


@pytest.mark.parametrize("case", list(_FAILED_LOGINS))
def test_every_failed_login_reads_alike_costs_one_check_and_stores_nothing(
    client: TestClient, any_store: ExecutionStore, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """The same body, the same challenge and one password check whatever
    went wrong, so neither the answer nor its timing tells a caller which
    names exist or have a password."""
    name, password, arrange = _FAILED_LOGINS[case]
    if arrange is not None:
        arrange(any_store)
    checks = _count_checks(monkeypatch)

    response = _log_in(client, name, password)

    _assert_invalid_credentials(response)
    assert len(checks) == 1
    assert any_store.list_tokens() == ()


@pytest.mark.parametrize("name", ["NOT-A-NAME", "a\x00b", "b" * 65])
def test_a_name_nobody_can_have_is_checked_without_asking_the_store(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """A U+0000 arrives as U+FFFD, which no name holds either."""
    store = InMemoryExecutionStore()
    _user(store, "alice")
    asked: list[str] = []
    get_password_hash = store.get_password_hash

    def spy(name: str) -> str | None:
        asked.append(name)
        return get_password_hash(name)

    store.get_password_hash = spy  # type: ignore[method-assign]
    checks = _count_checks(monkeypatch)

    response = _log_in(TestClient(create_app(store)), name)

    _assert_invalid_credentials(response)
    assert asked == []
    assert checks == [None]


@pytest.mark.parametrize("path", ["/api/v1/login", "/api/v1/password"])
def test_an_open_server_refuses_before_reading_the_body_whatever_the_header(
    any_store: ExecutionStore, path: str
) -> None:
    """Nobody has a password on a server with no user. The refusal comes
    before the media type or the size is looked at, and a token sent along
    is not looked at either."""
    client = TestClient(create_app(any_store))

    response = client.post(
        path,
        content=b" " * (_MAX_BODY_BYTES + 1),
        headers={"content-type": "text/plain", **_bearer(new_token())},
    )

    _assert_rejected(response, 409, "open_server", [])
    assert "vantage user add NAME --admin" in response.json()["detail"]
    assert "WWW-Authenticate" not in response.headers
    assert any_store.access_required() is False


@pytest.mark.parametrize(
    "authorization",
    [f"Bearer {new_token()}", "Basic YWxpY2U6c2VjcmV0", "Bearer"],
    ids=["unknown-token", "basic", "no-token"],
)
def test_the_authorization_header_is_ignored_when_logging_in_or_changing_a_password(
    client: TestClient, authorization: str
) -> None:
    """The body carries the credentials; a header any other route would
    refuse changes nothing here."""
    headers = {"Authorization": authorization}

    logged_in = client.post(
        "/api/v1/login", json={"name": "bob", "password": _PASSWORD}, headers=headers
    )
    changed = client.post(
        "/api/v1/password",
        json={"name": "bob", "password": _PASSWORD, "new_password": _NEW_PASSWORD},
        headers=headers,
    )

    assert (logged_in.status_code, changed.status_code) == (201, 204)


def test_a_login_token_is_refused_once_it_expires_and_a_made_token_is_not(
    client: TestClient, any_store: ExecutionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged_in = _logged_in(client)
    made = _token(any_store, "bob", READ_SCOPE)

    def clock(later: timedelta) -> None:
        """Authenticate as if `later` had passed."""
        monkeypatch.setattr(
            access, "datetime", SimpleNamespace(now=lambda tz=None: datetime.now(tz) + later)
        )

    clock(LOGIN_TOKEN_LIFETIME - timedelta(minutes=1))
    before = _reads(client, logged_in)
    clock(LOGIN_TOKEN_LIFETIME)
    expired = client.get("/api/v1/projects/default/runs", headers=_bearer(logged_in))
    clock(100 * LOGIN_TOKEN_LIFETIME)
    by_made = _reads(client, made)

    assert before == 200
    _assert_rejected(expired, 401, "unauthenticated", [])
    assert "expired" in expired.json()["detail"]
    assert expired.headers["WWW-Authenticate"] == 'Bearer realm="vantage", error="invalid_token"'
    assert by_made == 200


# --- Bodies ---------------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["login", "change", "set"])
def test_a_password_route_checks_its_media_type_size_and_json(
    client: TestClient, any_store: ExecutionStore, route: str
) -> None:
    headers: dict[str, str] = {}
    method, path = "POST", f"/api/v1/{'login' if route == 'login' else 'password'}"
    if route == "set":
        method, path = "PUT", "/api/v1/users/bob/password"
        headers = _bearer(_token(any_store, "alice", ADMIN_SCOPE))

    def send(content: bytes, content_type: str = "application/json") -> Any:
        return client.request(
            method, path, content=content, headers={**headers, "content-type": content_type}
        )

    wrong_type = send(b"{}", "text/plain")
    too_large = send(b" " * (_MAX_BODY_BYTES + 1))
    at_the_cap = send(b" " * _MAX_BODY_BYTES)
    not_json = send(b"{")

    _assert_rejected(wrong_type, 415, "unsupported_media_type", [])
    _assert_rejected(too_large, 413, "payload_too_large", [])
    _assert_rejected(at_the_cap, 400, "invalid_json", [])
    _assert_rejected(not_json, 400, "invalid_json", [])


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        ({}, ["name", "password"]),
        ({"name": "bob"}, ["password"]),
        ({"password": _SECRET}, ["name"]),
        ({"name": 1, "password": _SECRET}, ["name"]),
        ({"name": ["bob"], "password": _SECRET}, ["name"]),
        ({"name": "bob", "password": None}, ["password"]),
        ({"name": "bob", "password": [_SECRET]}, ["password"]),
        ({"name": "bob", "password": _SECRET, "remember": _SECRET}, ["remember"]),
        (["bob", _SECRET], []),
        (_SECRET, []),
    ],
)
def test_a_login_body_of_another_shape_is_refused_naming_its_fields_alone(
    client: TestClient, any_store: ExecutionStore, body: Any, fields: list[str]
) -> None:
    response = client.post("/api/v1/login", json=body)

    _assert_rejected(response, 422, "invalid_login_request", fields)
    assert _SECRET not in response.text
    assert any_store.list_tokens() == ()


def test_no_answer_ever_repeats_a_password(client: TestClient, any_store: ExecutionStore) -> None:
    """Whatever shape a body carrying one has, and whichever check refuses
    it: the rejections name fields, never values."""
    admin = _bearer(_token(any_store, "alice", ADMIN_SCOPE))

    def raw(path: str, text: str, method: str = "POST") -> Any:
        return client.request(
            method,
            path,
            content=text.encode(),
            headers={**admin, "content-type": "application/json"},
        )

    responses = [
        client.post("/api/v1/login", json={"password": _SECRET}),
        client.post("/api/v1/login", json={"name": 1, "password": _SECRET}),
        client.post("/api/v1/login", json={"name": "bob", "password": _SECRET, "again": _SECRET}),
        client.post("/api/v1/login", json={"name": _SECRET, "password": _SECRET}),
        client.post("/api/v1/login", json={"name": "bob", "password": _SECRET}),
        raw("/api/v1/login", f'{{"name": "bob", "password": "{_SECRET}"'),
        client.post("/api/v1/password", json={"name": "bob", "new_password": _SECRET}),
        client.post(
            "/api/v1/password",
            json={"name": "bob", "password": _SECRET, "new_password": _NEW_PASSWORD},
        ),
        client.post(
            "/api/v1/password",
            json={"name": "bob", "password": _PASSWORD, "new_password": f"{_SECRET}\n"},
        ),
        client.post(
            "/api/v1/password",
            json={"name": 1, "password": _SECRET, "new_password": _SECRET},
        ),
        client.put("/api/v1/users/bob/password", json={"pasword": _SECRET}, headers=admin),
        client.put(
            "/api/v1/users/bob/password",
            json={"password": _SECRET, "confirm": _SECRET},
            headers=admin,
        ),
        client.put(
            "/api/v1/users/bob/password", json={"password": f"{_SECRET}\x01"}, headers=admin
        ),
        raw("/api/v1/users/bob/password", f'{{"password": "{_SECRET} \\ud800"}}', "PUT"),
        client.put("/api/v1/users/ghost/password", json={"password": _SECRET}, headers=admin),
    ]

    for response in responses:
        assert response.status_code >= 400, response.text
        assert _SECRET not in response.text
        assert _SECRET not in str(response.headers)


def test_a_password_never_shows_in_a_request_models_repr_or_its_validation_error() -> None:
    """A body missing a field would otherwise repeat the whole input in the
    error's text."""
    models = [
        LoginRequest(name="bob", password=_SECRET),
        PasswordChangeRequest(name="bob", password=_SECRET, new_password=_SECRET),
        PasswordSetRequest(password=_SECRET),
    ]
    with pytest.raises(ValidationError) as missing:
        LoginRequest.model_validate({"password": _SECRET})

    for model in models:
        assert _SECRET not in repr(model)
        assert _SECRET not in str(model)
    assert _SECRET not in str(missing.value)


# --- Changing one's own password ------------------------------------------------------------


def test_changing_a_password_answers_no_content_and_only_the_new_one_logs_in(
    client: TestClient,
) -> None:
    response = _change(client)

    assert (response.status_code, response.content) == (204, b"")
    _assert_invalid_credentials(_log_in(client, "bob", _PASSWORD))
    assert _log_in(client, "bob", _NEW_PASSWORD).status_code == 201


@pytest.mark.parametrize(
    "case", [case for case in _FAILED_LOGINS if not case.endswith("-meanwhile")]
)
def test_a_change_needs_the_current_password_of_an_enabled_user(
    client: TestClient, any_store: ExecutionStore, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Refused as a failed login is, with one check, and nothing changes."""
    name, password, arrange = _FAILED_LOGINS[case]
    before = {user: any_store.get_password_hash(user) for user in ("alice", "bob")}
    if arrange is not None:
        arrange(any_store)
    checks = _count_checks(monkeypatch)

    response = _change(client, name, password)

    _assert_invalid_credentials(response)
    assert len(checks) == 1
    any_store.update_user("bob", disabled=False)
    assert {user: any_store.get_password_hash(user) for user in ("alice", "bob")} == before
    carol = any_store.get_user("carol")
    assert carol is None or carol.has_password is False


_BREAKING_THE_RULE = {
    "too-short": '"fourteen chars"',
    "too-long": '"' + "x" * 257 + '"',
    "tab": '"fifteen\\tchars!!"',
    "delete": '"fifteen chars!\\u007f"',
    "c1-control": '"fifteen chars!\\u0085"',
    "lone-surrogate": '"fifteen chars!\\ud800"',
}
"""New passwords the rule refuses, as JSON text: a lone surrogate can only
be sent escaped."""


@pytest.mark.parametrize("new", list(_BREAKING_THE_RULE.values()), ids=list(_BREAKING_THE_RULE))
def test_a_new_password_that_breaks_the_rule_is_refused_before_any_hash(
    client: TestClient, any_store: ExecutionStore, monkeypatch: pytest.MonkeyPatch, new: str
) -> None:
    """Checked before the current password is, so it is refused even with
    a wrong one, and a refused request costs no scrypt."""
    admin = _bearer(_token(any_store, "alice", ADMIN_SCOPE))
    before = any_store.get_password_hash("bob")
    json_headers = {"content-type": "application/json"}
    computed = _count_hashes(monkeypatch)

    changed = client.post(
        "/api/v1/password",
        content=f'{{"name": "bob", "password": "{_SECRET}", "new_password": {new}}}'.encode(),
        headers=json_headers,
    )
    by_admin = client.put(
        "/api/v1/users/bob/password",
        content=f'{{"password": {new}}}'.encode(),
        headers={**admin, **json_headers},
    )

    _assert_rejected(changed, 422, "invalid_password", ["new_password"])
    _assert_rejected(by_admin, 422, "invalid_password", ["password"])
    assert changed.json()["detail"] == (
        "A password is 15 to 256 characters, with no control characters."
    )
    assert computed == []
    assert any_store.get_password_hash("bob") == before


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        ({}, ["name", "password", "new_password"]),
        ({"name": "bob", "password": _PASSWORD}, ["new_password"]),
        ({"name": "bob", "password": _PASSWORD, "new_password": 1}, ["new_password"]),
        (
            {"name": "bob", "password": _PASSWORD, "new_password": _NEW_PASSWORD, "admin": True},
            ["admin"],
        ),
        (["bob", _PASSWORD, _NEW_PASSWORD], []),
    ],
)
def test_a_change_body_of_another_shape_is_refused_naming_its_fields_alone(
    client: TestClient, any_store: ExecutionStore, body: Any, fields: list[str]
) -> None:
    before = any_store.get_password_hash("bob")

    response = client.post("/api/v1/password", json=body)

    _assert_rejected(response, 422, "invalid_password_request", fields)
    assert any_store.get_password_hash("bob") == before


def test_a_change_revokes_that_users_login_tokens_and_no_other_token(
    client: TestClient, any_store: ExecutionStore
) -> None:
    """Whoever held a login of the old password logs in again; a made
    token, which no password opened, is left alone."""
    first, second = _logged_in(client), _logged_in(client)
    made = _token(any_store, "bob", READ_SCOPE)
    alices = _logged_in(client, "alice")

    assert _change(client).status_code == 204

    assert (_reads(client, first), _reads(client, second)) == (401, 401)
    assert (_reads(client, made), _reads(client, alices)) == (200, 200)
    revoked = {token.label: token.revoked_at for token in any_store.list_tokens(user="bob")}
    assert revoked[""] is None
    assert revoked["login"] is not None


def test_a_change_that_loses_to_one_made_meanwhile_is_refused(
    client: TestClient, any_store: ExecutionStore
) -> None:
    """Two changes checked against the same current password: the second
    to write finds the hash it checked replaced, and changes nothing."""
    set_password = any_store.set_password

    def other_first(name: str, **kwargs: Any) -> bool:
        if kwargs.get("replacing") is not None:
            set_password(name, password_hash=hash_password(_SECRET), changed_at=_NOW)
        return set_password(name, **kwargs)

    any_store.set_password = other_first  # type: ignore[method-assign]

    response = _change(client)

    _assert_invalid_credentials(response)
    _assert_invalid_credentials(_log_in(client, "bob", _NEW_PASSWORD))
    assert _log_in(client, "bob", _SECRET).status_code == 201


# --- An admin sets a password ---------------------------------------------------------------


def test_an_admin_sets_a_password_with_which_the_user_then_logs_in(
    client: TestClient, any_store: ExecutionStore
) -> None:
    admin = _token(any_store, "alice", ADMIN_SCOPE)

    response = _set(client, admin)

    assert (response.status_code, response.content) == (204, b"")
    _assert_invalid_credentials(_log_in(client, "bob", _PASSWORD))
    assert _log_in(client, "bob", _NEW_PASSWORD).status_code == 201


def test_setting_a_password_revokes_that_users_login_tokens_alone(
    client: TestClient, any_store: ExecutionStore
) -> None:
    admin = _token(any_store, "alice", ADMIN_SCOPE)
    bobs, made = _logged_in(client), _token(any_store, "bob", READ_SCOPE)
    alices = _logged_in(client, "alice")

    assert _set(client, admin).status_code == 204

    assert _reads(client, bobs) == 401
    assert (_reads(client, made), _reads(client, alices)) == (200, 200)


def test_an_admin_sets_their_own_password_without_the_current_one(
    client: TestClient, any_store: ExecutionStore
) -> None:
    """Asking for it would protect nothing: an admin's token can already
    make admin tokens. Their own login tokens are revoked like anyone's,
    the made one they used is not."""
    admin = _token(any_store, "alice", ADMIN_SCOPE)
    logged_in = _logged_in(client, "alice")

    response = _set(client, admin, "alice")

    assert response.status_code == 204
    assert _reads(client, logged_in) == 401
    assert client.get("/api/v1/users", headers=_bearer(admin)).status_code == 200
    assert _log_in(client, "alice", _NEW_PASSWORD).status_code == 201


def test_a_disabled_user_given_a_password_still_cannot_log_in(
    client: TestClient, any_store: ExecutionStore
) -> None:
    """Setting a password enables and promotes nobody."""
    admin = _token(any_store, "alice", ADMIN_SCOPE)
    any_store.update_user("bob", disabled=True)

    response = _set(client, admin)
    while_disabled = _log_in(client, "bob", _NEW_PASSWORD)
    bob = any_store.get_user("bob")
    any_store.update_user("bob", disabled=False)

    assert response.status_code == 204
    _assert_invalid_credentials(while_disabled)
    assert bob is not None and (bob.disabled, bob.admin, bob.has_password) == (True, False, True)
    assert _log_in(client, "bob", _NEW_PASSWORD).status_code == 201


def test_only_an_admin_token_of_an_admin_may_set_a_password(
    client: TestClient, any_store: ExecutionStore
) -> None:
    """A login token of an admin holds the admin scope, and may."""
    without_admin = _token(any_store, "alice", READ_SCOPE, RECORD_SCOPE)
    of_non_admin = _token(any_store, "bob", *SCOPES)
    non_admins_login = _logged_in(client)
    admins_login = _logged_in(client, "alice")
    body = {"password": _NEW_PASSWORD}

    anonymous = client.put("/api/v1/users/bob/password", json=body)
    lacking = _set(client, without_admin)
    non_admin = _set(client, of_non_admin)
    by_login = _set(client, non_admins_login)
    unchanged = _log_in(client, "bob", _PASSWORD)
    by_admins_login = _set(client, admins_login)

    _assert_rejected(anonymous, 401, "unauthenticated", [])
    _assert_rejected(lacking, 403, "insufficient_scope", [])
    _assert_rejected(non_admin, 403, "insufficient_scope", [])
    _assert_rejected(by_login, 403, "insufficient_scope", [])
    assert unchanged.status_code == 201
    assert by_admins_login.status_code == 204


def test_an_open_server_sets_no_password(any_store: ExecutionStore) -> None:
    """Refused for who asks, as every users route is on a server with no
    user, before the name or the body is looked at."""
    client = TestClient(create_app(any_store))

    response = client.put(
        "/api/v1/users/NOT-A-NAME/password", content=b"{", headers={"content-type": "text/plain"}
    )

    _assert_rejected(response, 409, "open_server", [])
    assert any_store.access_required() is False


@pytest.mark.parametrize(
    ("content", "content_type"),
    [(b" " * (_MAX_BODY_BYTES + 1), "application/json"), (b"{", "application/json")],
    ids=["too-large", "not-json"],
)
def test_a_name_nobody_can_have_is_unknown_user_before_the_body_is_read(
    client: TestClient, any_store: ExecutionStore, content: bytes, content_type: str
) -> None:
    admin = _token(any_store, "alice", ADMIN_SCOPE)

    response = client.put(
        "/api/v1/users/NOT-A-NAME/password",
        content=content,
        headers={**_bearer(admin), "content-type": content_type},
    )

    _assert_rejected(response, 404, "unknown_user", [])


@pytest.mark.parametrize("name", ["ghost", "Ghost", "g" * 65])
def test_setting_the_password_of_nobody_is_unknown_user(
    client: TestClient, any_store: ExecutionStore, name: str
) -> None:
    admin = _token(any_store, "alice", ADMIN_SCOPE)

    response = _set(client, admin, name)

    _assert_rejected(response, 404, "unknown_user", [])
    assert [user.name for user in any_store.list_users()] == ["alice", "bob"]


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        ({}, ["password"]),
        ({"password": 1}, ["password"]),
        ({"password": None}, ["password"]),
        ({"password": _NEW_PASSWORD, "admin": True}, ["admin"]),
        ({"password": _NEW_PASSWORD, "disabled": False}, ["disabled"]),
        ([_NEW_PASSWORD], []),
    ],
)
def test_a_set_body_of_another_shape_is_refused_naming_its_fields_alone(
    client: TestClient, any_store: ExecutionStore, body: Any, fields: list[str]
) -> None:
    admin = _token(any_store, "alice", ADMIN_SCOPE)
    before = any_store.get_password_hash("bob")

    response = client.put("/api/v1/users/bob/password", json=body, headers=_bearer(admin))

    _assert_rejected(response, 422, "invalid_password_request", fields)
    assert any_store.get_password_hash("bob") == before


def test_a_u0000_in_a_password_is_taken_as_u_fffd_wherever_it_is_sent(
    client: TestClient, any_store: ExecutionStore
) -> None:
    """No U+0000 reaches a store: the body decoder takes it as U+FFFD, the
    same when a password is set, changed and checked, so either spelling
    logs in, and neither is refused as a control character."""
    admin = _token(any_store, "alice", ADMIN_SCOPE)
    with_nul = "correct horse\u0000battery staple"
    with_fffd = "correct horse\ufffdbattery staple"

    assert _set(client, admin, "bob", with_nul).status_code == 204
    assert verify_password(with_fffd, any_store.get_password_hash("bob"))
    assert _log_in(client, "bob", with_nul).status_code == 201
    assert _log_in(client, "bob", with_fffd).status_code == 201

    assert _change(client, "bob", with_fffd, f"{_NEW_PASSWORD}\u0000").status_code == 204
    assert _log_in(client, "bob", f"{_NEW_PASSWORD}\ufffd").status_code == 201


# --- Password slots -------------------------------------------------------------------------


def test_at_most_two_password_checks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each check takes about 0.2 s of CPU and 32 MiB, so a flood of logins
    runs two at a time, the rest waiting for a slot. The logins share one
    event loop, as they do under uvicorn: the slots are awaited on it."""
    store = InMemoryExecutionStore()
    _user(store, "bob")
    verify = verify_password
    parse = login_routes._parse
    logins = 5
    lock = threading.Lock()
    counts = {"parsed": 0, "inside": 0, "most": 0}
    all_parsed, two_inside, three_inside = threading.Event(), threading.Event(), threading.Event()
    release = threading.Event()

    def counted_parse(*args: Any) -> Any:
        payload = parse(*args)
        with lock:
            counts["parsed"] += 1
            if counts["parsed"] == logins:
                all_parsed.set()
        return payload

    def held_verify(password: str, stored: str | None) -> bool:
        with lock:
            counts["inside"] += 1
            counts["most"] = max(counts["most"], counts["inside"])
            if counts["inside"] == 2:
                two_inside.set()
            elif counts["inside"] > 2:
                three_inside.set()
        release.wait(_JOIN_TIMEOUT_SECONDS)
        with lock:
            counts["inside"] -= 1
        return verify(password, stored)

    monkeypatch.setattr(login_routes, "_parse", counted_parse)
    monkeypatch.setattr(login_routes, "verify_password", held_verify)
    answered: list[int] = []

    with TestClient(create_app(store)) as client:

        def _logging_in() -> None:
            answered.append(_log_in(client).status_code)

        threads = [threading.Thread(target=_logging_in, daemon=True) for _ in range(logins)]
        for thread in threads:
            thread.start()
        try:
            assert all_parsed.wait(_JOIN_TIMEOUT_SECONDS), "not every login reached its check"
            assert two_inside.wait(_JOIN_TIMEOUT_SECONDS), "two checks never ran at once"
            assert not three_inside.wait(0.5), "a third check ran alongside two"
        finally:
            release.set()
            for thread in threads:
                thread.join(_JOIN_TIMEOUT_SECONDS)

    assert not any(thread.is_alive() for thread in threads)
    assert counts["most"] == 2
    assert answered == [201] * logins


def _held_scrypt(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, int], threading.Event, threading.Event, threading.Event]:
    """Make every scrypt the routes run -- every check and every hash -- wait
    for `release`, counting how many run at once and how many ran; `two`
    is set once two run together, `three` if a third ever joins them."""
    scrypt = passwords._scrypt
    lock = threading.Lock()
    counts = {"inside": 0, "most": 0, "ran": 0}
    two, three, release = threading.Event(), threading.Event(), threading.Event()

    def held(*args: Any) -> bytes:
        with lock:
            counts["inside"] += 1
            counts["ran"] += 1
            counts["most"] = max(counts["most"], counts["inside"])
            if counts["inside"] == 2:
                two.set()
            elif counts["inside"] > 2:
                three.set()
        release.wait(_JOIN_TIMEOUT_SECONDS)
        with lock:
            counts["inside"] -= 1
        return scrypt(*args)

    monkeypatch.setattr(passwords, "_scrypt", held)
    return counts, two, three, release


_HASHING_ROUTES: dict[str, Callable[[TestClient, int, str], Any]] = {
    "log_in": lambda client, i, admin: _log_in(client, f"user{i}"),
    "change_password": lambda client, i, admin: _change(client, f"user{i}"),
    "set_password": lambda client, i, admin: _set(client, admin, f"user{i}"),
}


@pytest.mark.parametrize("route", list(_HASHING_ROUTES))
def test_every_password_route_hashes_two_at_a_time_and_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """A check, a change's check and hash, and an admin's hash all take a
    slot, so no more than two run at once whichever route asks; and each
    runs in the threadpool, so while two are held the capability check,
    which never leaves the event loop, still answers."""
    store = InMemoryExecutionStore()
    _user(store, "alice", admin=True)
    admin = _token(store, "alice", ADMIN_SCOPE)
    requests = 5
    for i in range(requests):
        _user(store, f"user{i}")
    counts, two, three, release = _held_scrypt(monkeypatch)
    send = _HASHING_ROUTES[route]
    answered: list[int] = []

    with TestClient(create_app(store)) as client:
        threads = [
            threading.Thread(
                target=lambda i=i: answered.append(send(client, i, admin).status_code), daemon=True
            )
            for i in range(requests)
        ]
        for thread in threads:
            thread.start()
        try:
            assert two.wait(_JOIN_TIMEOUT_SECONDS), "two hashes never ran at once"
            assert client.get("/api/v1/capabilities").status_code == 200
            assert not three.wait(0.5), "a third hash ran alongside two"
        finally:
            release.set()
            for thread in threads:
                thread.join(_JOIN_TIMEOUT_SECONDS)

    assert not any(thread.is_alive() for thread in threads)
    assert counts["most"] == 2
    assert sorted(answered) == [201 if route == "log_in" else 204] * requests


def test_past_thirty_two_waiting_a_password_request_is_refused_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client can send a body and leave, holding no connection while its
    request waits: without a bound, a flood would queue memory and minutes
    of hashing ahead of every real login. Two hash, 32 wait, and the next
    is refused before anything is asked of the store or hashed; once the
    queue drains, logins are served again."""
    store = InMemoryExecutionStore()
    _user(store, "bob")
    counts, two, _three, release = _held_scrypt(monkeypatch)
    admitted = 2 + 32
    answered: list[int] = []

    with TestClient(create_app(store)) as client:
        slots = client.app.state.password_slots  # type: ignore[attr-defined]
        threads = [
            threading.Thread(
                target=lambda: answered.append(_log_in(client).status_code), daemon=True
            )
            for _ in range(admitted)
        ]
        for thread in threads:
            thread.start()
        try:
            assert two.wait(_JOIN_TIMEOUT_SECONDS), "two hashes never ran at once"
            deadline = threading.Event()
            for _ in range(int(_JOIN_TIMEOUT_SECONDS / 0.01)):
                if slots._admitted == admitted:
                    break
                deadline.wait(0.01)
            assert slots._admitted == admitted, "not every request reached the slots"
            calls = _count_checks(monkeypatch)

            refused = _log_in(client)

            _assert_rejected(refused, 503, "password_checks_busy", [])
            assert refused.headers["Retry-After"] == "1"
            assert calls == []
        finally:
            release.set()
            for thread in threads:
                thread.join(_JOIN_TIMEOUT_SECONDS)

        assert _log_in(client).status_code == 201

    assert not any(thread.is_alive() for thread in threads)
    assert answered == [201] * admitted
    assert counts["ran"] == admitted + 1
    assert slots._admitted == 0


@pytest.mark.parametrize("gone", [True, False], ids=["gone", "still-there"])
def test_a_login_whose_client_left_while_it_waited_hashes_nothing(
    monkeypatch: pytest.MonkeyPatch, gone: bool
) -> None:
    """Asked once it holds a slot, before any hash: a client that sent its
    body and left gets no hash, and one still there is served. Driven
    through the ASGI interface itself, which says `http.disconnect` when
    the client has gone and otherwise has nothing to say."""
    store = InMemoryExecutionStore()
    _user(store, "bob")
    app = create_app(store)
    counts, _two, _three, release = _held_scrypt(monkeypatch)
    release.set()
    body = LoginRequest(name="bob", password=_PASSWORD).model_dump_json().encode()
    sent: list[MutableMapping[str, Any]] = []

    async def exchange() -> None:
        messages = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive() -> dict[str, Any]:
            if messages:
                return messages.pop()
            if gone:
                return {"type": "http.disconnect"}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/login",
            "raw_path": b"/api/v1/login",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"host", b"testserver")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        await app(scope, receive, send)

    asyncio.run(exchange())

    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    if gone:
        assert (status, counts["ran"]) == (400, 0)
    else:
        assert (status, counts["ran"]) == (201, 1)
