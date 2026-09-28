"""A browser's session over HTTP (`service/routes/session.py`, and the
cookie's part in `service/access.py`): signing in trades a name and a
password for a login token kept in a Secure, HttpOnly, SameSite=Strict
cookie, and answers who signed in, never the token; any credential can ask
who it is; signing out revokes the cookie's token and always clears it. A
request is authorized by the `Authorization` header alone when it has one;
otherwise, on a server with users, by the cookie, only on a request the
browser marks as coming from vantage's own pages, which is checked before
the cookie is looked up.

The clients speak HTTPS (`base_url`), so the Secure cookie comes back as a
browser would send it; each request says where it comes from in
`Sec-Fetch-Site`, as a browser does. Run against every adapter
(`any_store`) where an answer rests on what the store says of users and
tokens; the spy uses the in-memory one.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from vantage.core.domain.access import (
    ADMIN_SCOPE,
    LOGIN_TOKEN_LABEL,
    LOGIN_TOKEN_LIFETIME,
    MANAGE_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    Grant,
    new_token,
    token_digest,
)
from vantage.core.domain.passwords import hash_password
from vantage.core.ports.storage import ExecutionStore
from vantage.service.access import SESSION_COOKIE
from vantage.service.app import create_app
from vantage.service.slots import PasswordSlots

# `any_store`, each adapter in turn; `cheap_passwords`, for every hash.
pytest_plugins = ["store_fixtures", "password_fixtures"]

pytestmark = pytest.mark.usefixtures("cheap_passwords")

_NOW = datetime(2026, 9, 28, 9, 0, 0, tzinfo=timezone.utc)
_PASSWORD = "correct horse battery staple"
_NEW_PASSWORD = "a new one, long enough to set"
_BASE_URL = "https://testserver"
_MAX_BODY_BYTES = 16 * 1024

_SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
_CLEARED = f'{SESSION_COOKIE}=""; HttpOnly; Max-Age=0; Path=/; SameSite=strict; Secure'
_CROSS_SITE_DETAIL = (
    "A browser session is accepted only from vantage's own pages: the browser must mark the "
    "request Sec-Fetch-Site: same-origin, which browsers do over HTTPS and on this machine's "
    "loopback address."
)


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


def _expired_login(store: ExecutionStore, user: str) -> str:
    """A login token of `user`, which has a password, stored already
    expired."""
    token = new_token()
    stored = store.get_password_hash(user)
    assert stored is not None
    made = store.create_login_token(
        user,
        password_hash=stored,
        digest=token_digest(token),
        created_at=_NOW - LOGIN_TOKEN_LIFETIME,
        expires_at=_NOW,
    )
    assert made is not None
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cookie(token: str, site: str | None = "same-origin") -> dict[str, str]:
    """A session cookie holding `token`, on a request the browser marked as
    coming from `site`, or did not mark."""
    headers = {"Cookie": f"{SESSION_COOKIE}={token}"}
    if site is not None:
        headers["Sec-Fetch-Site"] = site
    return headers


def _instant(text: str) -> datetime:
    """A timestamp as the API writes it; Python 3.10 reads no `Z`."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _report(run_id: str) -> dict[str, Any]:
    return {
        "run": {
            "id": run_id,
            "started_at": "2026-09-28T09:14:02+00:00",
            "finished_at": "2026-09-28T09:14:47+00:00",
            "exit_status": 0,
            "interrupted": False,
            "interrupt_reason": None,
        }
    }


def _sign_in(
    client: TestClient,
    name: str = "bob",
    password: str = _PASSWORD,
    *,
    site: str | None = "same-origin",
) -> Any:
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    return client.post(
        "/api/v1/session", json={"name": name, "password": password}, headers=headers
    )


def _signed_in(client: TestClient, name: str = "bob") -> str:
    """The token of a session `name` signed in to. The client forgets the
    cookie, so every later request says which credential it sends."""
    response = _sign_in(client, name)
    assert response.status_code == 201, response.text
    token: str = response.cookies[SESSION_COOKIE]
    client.cookies.clear()
    return token


def _whoami(client: TestClient, headers: dict[str, str]) -> Any:
    return client.get("/api/v1/session", headers=headers)


def _assert_rejected(response: Any, status: int, error: str, fields: list[str]) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert (body["error"], body["fields"]) == (error, fields)


def _assert_cross_site(response: Any) -> None:
    _assert_rejected(response, 403, "cross_site_request", [])
    assert response.json()["detail"] == _CROSS_SITE_DETAIL
    assert "WWW-Authenticate" not in response.headers


@pytest.fixture
def client(any_store: ExecutionStore) -> TestClient:
    """A browser's client, sending no token, to a server whose users are
    `alice`, an admin, and `bob`, who is not, both with `_PASSWORD`."""
    _user(any_store, "alice", admin=True)
    _user(any_store, "bob")
    return TestClient(create_app(any_store), base_url=_BASE_URL)


# --- Signing in -------------------------------------------------------------------------------


def test_signing_in_sets_the_session_cookie_and_answers_no_token(
    client: TestClient, any_store: ExecutionStore
) -> None:
    """HttpOnly, so page script never holds the token; Secure and
    host-only, as its `__Host-` name demands; SameSite=Strict; for exactly
    the token's twelve hours. The body says who signed in and until when,
    and no cache may keep the answer."""
    response = _sign_in(client)

    assert response.status_code == 201, response.text
    token = response.cookies[SESSION_COOKIE]
    assert response.headers.get_list("Set-Cookie") == [
        f"{SESSION_COOKIE}={token}; HttpOnly; Max-Age=43200; Path=/; SameSite=strict; Secure"
    ]
    assert response.headers.get_list("Cache-Control") == ["no-store"]
    (stored,) = any_store.list_tokens(user="bob")
    body = response.json()
    assert set(body) == {"open", "user", "expires_at"}
    assert (body["open"], body["user"]) == (False, {"name": "bob", "admin": False})
    assert _instant(body["expires_at"]) == stored.expires_at
    assert token not in response.text
    assert token_digest(token) not in response.text


@pytest.mark.parametrize(
    ("name", "scopes"),
    [("alice", {READ_SCOPE, MANAGE_SCOPE, ADMIN_SCOPE}), ("bob", {READ_SCOPE, MANAGE_SCOPE})],
    ids=["admin", "not-an-admin"],
)
def test_the_session_cookie_holds_a_login_token(
    client: TestClient, any_store: ExecutionStore, name: str, scopes: set[str]
) -> None:
    """The token `/login` would answer: labelled a login, reading and
    managing, administering for an admin, expiring twelve hours on."""
    response = _sign_in(client, name)
    token = response.cookies[SESSION_COOKIE]
    client.cookies.clear()

    (stored,) = any_store.list_tokens(user=name)
    assert (stored.label, stored.scopes) == (LOGIN_TOKEN_LABEL, frozenset(scopes))
    assert stored.expires_at is not None
    assert stored.expires_at - stored.created_at == LOGIN_TOKEN_LIFETIME
    grant = any_store.authenticate(token_digest(token), now=stored.created_at)
    assert grant is not None
    assert grant.token_id == stored.id
    assert response.json()["user"] == {"name": name, "admin": ADMIN_SCOPE in scopes}
    assert client.get("/api/v1/projects", headers=_cookie(token)).status_code == 200


def _busy(client: TestClient, store: ExecutionStore) -> None:
    """No password slot free, and none to wait for, as when every one is
    taken."""
    del store
    client.app.state.password_slots = PasswordSlots(running=0, waiting=0)  # type: ignore[attr-defined]


_JSON = {"content-type": "application/json"}

# Every refusal a sign-in shares with `/login`, as `(the request, what to do
# to the server first, the status, the error and its fields)`.
_REFUSED_SIGN_INS: dict[
    str,
    tuple[
        dict[str, Any],
        Callable[[TestClient, ExecutionStore], object] | None,
        int,
        str,
        list[str],
    ],
] = {
    "wrong-password": (
        {"json": {"name": "bob", "password": _NEW_PASSWORD}},
        None,
        401,
        "invalid_credentials",
        [],
    ),
    "unknown-user": (
        {"json": {"name": "ghost", "password": _PASSWORD}},
        None,
        401,
        "invalid_credentials",
        [],
    ),
    "disabled-user": (
        {"json": {"name": "bob", "password": _PASSWORD}},
        lambda _client, store: store.update_user("bob", disabled=True),
        401,
        "invalid_credentials",
        [],
    ),
    "no-password": (
        {"json": {"name": "carol", "password": _PASSWORD}},
        lambda _client, store: _user(store, "carol", password=None),
        401,
        "invalid_credentials",
        [],
    ),
    "wrong-media-type": (
        {"content": b"{}", "headers": {"content-type": "text/plain"}},
        None,
        415,
        "unsupported_media_type",
        [],
    ),
    "too-large": (
        {"content": b" " * (_MAX_BODY_BYTES + 1), "headers": _JSON},
        None,
        413,
        "payload_too_large",
        [],
    ),
    "not-json": ({"content": b"{", "headers": _JSON}, None, 400, "invalid_json", []),
    "wrong-shape": ({"json": {"name": "bob"}}, None, 422, "invalid_login_request", ["password"]),
    "busy": (
        {"json": {"name": "bob", "password": _PASSWORD}},
        _busy,
        503,
        "password_checks_busy",
        [],
    ),
}


@pytest.mark.parametrize("case", list(_REFUSED_SIGN_INS))
def test_signing_in_refuses_what_logging_in_refuses_and_sets_no_cookie(
    client: TestClient, any_store: ExecutionStore, case: str
) -> None:
    """In `/login`'s words, and nothing is stored: a name and password of
    no enabled user with that password, a body of another kind or shape,
    and a server checking as many passwords as it will."""
    request, arrange, status, error, fields = _REFUSED_SIGN_INS[case]
    if arrange is not None:
        arrange(client, any_store)
    headers = {**request.get("headers", {}), **_SAME_ORIGIN}
    body = {key: value for key, value in request.items() if key != "headers"}

    response = client.post("/api/v1/session", headers=headers, **body)

    _assert_rejected(response, status, error, fields)
    assert "set-cookie" not in response.headers
    assert [token for token in any_store.list_tokens() if token.label == LOGIN_TOKEN_LABEL] == []


@pytest.mark.parametrize("site", ["same-origin", "cross-site", None], ids=str)
def test_a_server_with_no_user_refuses_a_sign_in_before_anything_else(site: str | None) -> None:
    """Nobody has a password there, which is said first: before where the
    request comes from, the media type or the size is looked at."""
    client = TestClient(create_app(InMemoryExecutionStore()), base_url=_BASE_URL)
    headers = {"content-type": "text/plain"}
    if site is not None:
        headers["Sec-Fetch-Site"] = site

    response = client.post("/api/v1/session", content=b" " * (_MAX_BODY_BYTES + 1), headers=headers)

    _assert_rejected(response, 409, "open_server", [])
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("site", [None, "none", "same-site", "cross-site", "</script>"], ids=str)
def test_signing_in_needs_a_request_from_vantages_own_pages(
    client: TestClient, any_store: ExecutionStore, site: str | None
) -> None:
    """Otherwise a page on another port of the host could sign the browser
    in as someone else, so that what it does next is theirs. Refused
    before the password is checked, whatever the header says, which is
    never repeated."""
    response = _sign_in(client, site=site)

    _assert_cross_site(response)
    assert "set-cookie" not in response.headers
    assert any_store.list_tokens() == ()


# --- Asking who you are -----------------------------------------------------------------------


def test_any_credential_can_ask_who_it_is(client: TestClient, any_store: ExecutionStore) -> None:
    """The cookie, and a token in the header, a login's or a made one's --
    which never expires. A server with no user says it is open; one with
    users, asked with no credential, answers 401, which is how the web
    client learns to sign in; and a token that may not read, 403."""
    session = _signed_in(client)
    logged_in = client.post("/api/v1/login", json={"name": "alice", "password": _PASSWORD})
    made = _token(any_store, "bob", READ_SCOPE)
    recorder = _token(any_store, "bob", RECORD_SCOPE)
    expiry = {token.user: token.expires_at for token in any_store.list_tokens() if token.expires_at}

    by_cookie = _whoami(client, _cookie(session)).json()
    by_login = _whoami(client, _bearer(logged_in.json()["token"])).json()
    by_made = _whoami(client, _bearer(made)).json()
    nobody = _whoami(client, {})
    by_recorder = _whoami(client, _bearer(recorder))
    on_open_server = TestClient(create_app(InMemoryExecutionStore()), base_url=_BASE_URL)

    assert (by_cookie["open"], by_cookie["user"]) == (False, {"name": "bob", "admin": False})
    assert _instant(by_cookie["expires_at"]) == expiry["bob"]
    assert (by_login["open"], by_login["user"]) == (False, {"name": "alice", "admin": True})
    assert _instant(by_login["expires_at"]) == expiry["alice"]
    assert by_made == {"open": False, "user": {"name": "bob", "admin": False}, "expires_at": None}
    assert _whoami(on_open_server, {}).json() == {"open": True, "user": None, "expires_at": None}
    _assert_rejected(nobody, 401, "unauthenticated", [])
    _assert_rejected(by_recorder, 403, "insufficient_scope", [])


# --- Signing out ------------------------------------------------------------------------------


def test_signing_out_revokes_the_cookies_token_and_clears_it(
    client: TestClient, any_store: ExecutionStore
) -> None:
    session = _signed_in(client)

    response = client.delete("/api/v1/session", headers=_cookie(session))
    replayed = client.get("/api/v1/projects", headers=_cookie(session))

    assert response.status_code == 204, response.text
    assert response.headers.get_list("Set-Cookie") == [_CLEARED]
    (stored,) = any_store.list_tokens(user="bob")
    assert stored.revoked_at is not None
    _assert_rejected(replayed, 401, "unauthenticated", [])


@pytest.mark.parametrize("cookie", [None, "garbage", "not a token", "expired", "open-server"])
def test_signing_out_never_fails_and_never_touches_the_authorization_header(
    client: TestClient, any_store: ExecutionStore, cookie: str | None
) -> None:
    """A cookie that is absent, malformed, unknown or expired is cleared,
    on any server, an open one included. A token sent in the header is not
    the session's: `POST /tokens/{token_id}/revoke` revokes it, and this
    leaves it live."""
    made = _token(any_store, "bob", READ_SCOPE)
    headers = dict(_SAME_ORIGIN)
    server = client
    if cookie == "expired":
        headers.update(_cookie(_expired_login(any_store, "bob")))
    elif cookie == "open-server":
        server = TestClient(create_app(InMemoryExecutionStore()), base_url=_BASE_URL)
        headers.update(_cookie(new_token()))
    elif cookie is not None:
        headers["Cookie"] = f"{SESSION_COOKIE}={cookie}"

    signed_out = server.delete("/api/v1/session", headers=headers)
    with_a_token = client.delete("/api/v1/session", headers={**_bearer(made), **_SAME_ORIGIN})

    for response in (signed_out, with_a_token):
        assert response.status_code == 204, response.text
        assert response.headers.get_list("Set-Cookie") == [_CLEARED]
    assert _whoami(client, _bearer(made)).status_code == 200
    assert all(token.revoked_at is None for token in any_store.list_tokens())


@pytest.mark.parametrize("site", [None, "same-site", "cross-site"], ids=str)
def test_signing_out_needs_a_request_from_vantages_own_pages(
    client: TestClient, any_store: ExecutionStore, site: str | None
) -> None:
    """Otherwise a page on another port of the host could sign the browser
    out. Nothing is revoked or cleared."""
    session = _signed_in(client)

    response = client.delete("/api/v1/session", headers=_cookie(session, site))

    _assert_cross_site(response)
    assert "set-cookie" not in response.headers
    assert _whoami(client, _cookie(session)).status_code == 200
    assert all(token.revoked_at is None for token in any_store.list_tokens())


# --- The cookie beside the header -------------------------------------------------------------


def test_the_authorization_header_decides_alone(
    client: TestClient, any_store: ExecutionStore
) -> None:
    """A header that authenticates nobody is refused whatever cookie comes
    with it; one that does acts as its own user, whoever's the cookie is;
    and it is taken from anywhere, as the plugin and CI send it, since no
    browser sends it unasked."""
    session = _signed_in(client)
    alices = _token(any_store, "alice", READ_SCOPE)
    recorder = _token(any_store, "bob", RECORD_SCOPE)

    forged = _whoami(client, {**_bearer(new_token()), **_cookie(session)})
    malformed = _whoami(client, {"Authorization": "Basic Ym9iOnNlY3JldA==", **_cookie(session)})
    as_alice = _whoami(client, {**_bearer(alices), **_cookie(session)})
    recorded = client.post(
        "/api/v1/runs",
        json=_report("c" * 32),
        headers={**_bearer(recorder), "Sec-Fetch-Site": "cross-site"},
    )

    _assert_rejected(forged, 401, "unauthenticated", [])
    _assert_rejected(malformed, 401, "unauthenticated", [])
    # Alice's own read token, which cannot administer, whoever's the cookie is.
    assert as_alice.json()["user"] == {"name": "alice", "admin": False}
    assert recorded.status_code == 201, recorded.text


_READING_SITES = {"same-origin": 200, "none": 200, None: 200, "same-site": 403, "cross-site": 403}


@pytest.mark.parametrize(
    ("method", "site"),
    [
        *(("GET", site) for site in _READING_SITES),
        ("PUT", "same-origin"),
        ("PUT", "same-site"),
        ("PUT", None),
        ("POST", "same-site"),
    ],
    ids=str,
)
def test_a_session_cookie_is_taken_only_from_vantages_own_pages(
    client: TestClient, any_store: ExecutionStore, method: str, site: str | None
) -> None:
    """SameSite keeps the cookie from other sites, not from another port of
    the same host, so a page served there must not act with it. A read the
    browser marks as typed in, or does not mark at all, is taken, since a
    page elsewhere cannot read what it answers; a change must be marked
    same-origin. An admin's session on another port cannot revoke a
    token."""
    any_store.create_project("firmware", created_at=_NOW)
    session = _signed_in(client, "alice")
    victim = any_store.list_tokens(user="alice")[0]
    headers = _cookie(session, site)

    if method == "GET":
        response = client.get("/api/v1/projects", headers=headers)
        expected = _READING_SITES[site]
    elif method == "PUT":
        response = client.put(
            "/api/v1/projects/firmware/members/bob", json={"role": "viewer"}, headers=headers
        )
        expected = 201 if site == "same-origin" else 403
    else:
        response = client.post(f"/api/v1/tokens/{victim.id}/revoke", headers=headers)
        expected = 403

    assert response.status_code == expected, response.text
    if expected == 403:
        _assert_cross_site(response)
        assert any_store.get_member_role("bob", project="firmware") is None
        assert any_store.get_token(victim.id) == victim


def test_a_cookie_from_elsewhere_is_refused_before_it_is_looked_up() -> None:
    """So a page elsewhere learns nothing of whether a cookie authenticates,
    and costs the store nothing."""
    store = InMemoryExecutionStore()
    _user(store, "bob")
    asked: list[str] = []
    authenticate = store.authenticate

    def spy(digest: str, *, now: datetime) -> Grant | None:
        asked.append(digest)
        return authenticate(digest, now=now)

    store.authenticate = spy  # type: ignore[method-assign]
    client = TestClient(create_app(store), base_url=_BASE_URL)

    response = client.get("/api/v1/projects", headers=_cookie("garbage", "same-site"))

    _assert_cross_site(response)
    assert asked == []


def test_a_server_with_no_user_ignores_the_cookie() -> None:
    """It authenticates nobody there, and must refuse nobody: the browser
    sends it unasked, to every port of the host, so it may be another
    vantage's. A request carrying one, from anywhere, is served as one
    carrying none."""
    store = InMemoryExecutionStore()
    client = TestClient(create_app(store), base_url=_BASE_URL)

    listed = client.get("/api/v1/projects", headers=_cookie("garbage", "cross-site"))
    asked = _whoami(client, _cookie(new_token(), "same-site"))
    recorded = client.post(
        "/api/v1/runs", json=_report("d" * 32), headers=_cookie(new_token(), "cross-site")
    )

    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["role"] is None
    assert asked.json() == {"open": True, "user": None, "expires_at": None}
    assert recorded.status_code == 201, recorded.text
    assert store.access_required() is False


# --- How a session ends -----------------------------------------------------------------------


@pytest.mark.parametrize("end", ["own-password", "admin-sets-password", "disabled", "expired"])
def test_the_session_ends_with_its_token(
    client: TestClient, any_store: ExecutionStore, end: str
) -> None:
    """Any password set for its user revokes every login token of theirs,
    the cookie's included; a disabled user's tokens authenticate nothing;
    and the token's twelve hours end it."""
    session = _signed_in(client)
    if end == "own-password":
        changed = client.post(
            "/api/v1/password",
            json={"name": "bob", "password": _PASSWORD, "new_password": _NEW_PASSWORD},
        )
        assert changed.status_code == 204, changed.text
    elif end == "admin-sets-password":
        admins = _token(any_store, "alice", ADMIN_SCOPE)
        set_ = client.put(
            "/api/v1/users/bob/password", json={"password": _NEW_PASSWORD}, headers=_bearer(admins)
        )
        assert set_.status_code == 204, set_.text
    elif end == "disabled":
        any_store.update_user("bob", disabled=True)
    else:
        session = _expired_login(any_store, "bob")

    _assert_rejected(_whoami(client, _cookie(session)), 401, "unauthenticated", [])


@pytest.mark.parametrize("credential", ["made-without-admin", "promoted-after-sign-in"])
def test_a_credential_that_cannot_administer_says_so_whatever_its_user_is(
    client: TestClient, any_store: ExecutionStore, credential: str
) -> None:
    """`admin` answers whether this credential may use the admin routes,
    not whether its user is an admin: an admin's token made without the
    admin scope, and a login from before its user was made an admin, both
    say false, as the admin routes then answer."""
    if credential == "made-without-admin":
        headers = _bearer(_token(any_store, "alice", READ_SCOPE))
        name = "alice"
    else:
        headers = _cookie(_signed_in(client, "bob"))
        any_store.update_user("bob", admin=True)
        name = "bob"

    asked = _whoami(client, headers)
    administering = client.get("/api/v1/users", headers=headers)

    assert asked.json()["user"] == {"name": name, "admin": False}
    assert administering.status_code == 403


def test_a_session_of_a_user_no_longer_an_admin_administers_nothing(
    client: TestClient, any_store: ExecutionStore
) -> None:
    """Its token holds the admin scope, which grants nothing once its user
    stops being an admin, and the session says so."""
    session = _signed_in(client, "alice")
    any_store.update_user("alice", admin=False)

    asked = _whoami(client, _cookie(session))
    users = client.get("/api/v1/users", headers=_cookie(session))

    assert asked.json()["user"] == {"name": "alice", "admin": False}
    _assert_rejected(users, 403, "insufficient_scope", [])


def test_a_browser_session_cannot_record(client: TestClient, any_store: ExecutionStore) -> None:
    """Its token is a login's, which never holds the record scope, so a
    page that runs script in the session still cannot inject runs."""
    session = _signed_in(client, "alice")

    response = client.post("/api/v1/runs", json=_report("e" * 32), headers=_cookie(session))

    _assert_rejected(response, 403, "insufficient_scope", [])
    assert any_store.get_run_detail("e" * 32) is None
