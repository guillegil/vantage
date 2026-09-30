"""The host names a server answers: the rule (`core/config/hosts.py`),
pure, and the middleware that applies it (`service/hosts.py`), which
refuses every other name with `421 misdirected_request` before the web
client or any route runs, and only in an app given a rule.

A browser sends the name in its address bar as `Host`; `TestClient` sends
the one in its `base_url`, so each client here is made with the name the
browser would have used.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from starlette.types import Message
from vantage.core.config.hosts import (
    HostNameError,
    HostRule,
    allowed_host_name,
    host_rule,
    is_loopback,
    request_host,
)
from vantage.service.app import create_app
from vantage.service.web import SECURITY_HEADERS

_REFUSAL = {
    "error": "misdirected_request",
    "detail": (
        "This server does not answer to the host name this request was sent to. Use the "
        "address it serves at, or have its operator allow the name with --allowed-host NAME "
        "or VANTAGE_ALLOWED_HOSTS."
    ),
    "fields": [],
}

_LOOPBACK = HostRule(frozenset())
_PROXIED = HostRule(frozenset({"vantage.example.com"}))

# --- the rule -----------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:8765",
        "LOCALHOST:8765",
        "localhost.:8765",
        "127.0.0.1",
        "127.0.0.1:8765",
        "127.0.0.1.",
        "10.0.0.7:8765",
        "[::1]",
        "[::1]:8765",
        "[::FFFF:127.0.0.1]:8765",
        "localhost:",
    ],
)
def test_localhost_and_every_ip_literal_are_answered_on_any_port(host: str) -> None:
    """No page that rebinds a name has an IP literal as its address, nor
    `localhost`, which a browser resolves to this machine by itself."""
    assert _LOOPBACK.answers(host)


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "rebound.example.net",
        "rebound.example.net:8765",
        "vantage.example.com",
        "localhost.localdomain",
        "sub.localhost",
        "localhost..",
        "127.0.0.1.nip.io",
        "2130706433",
        "0x7f.0.0.1",
        "::1",
        "[::1",
        "[::1]x",
        "[not-an-address]:8765",
        "localhost:http",
        "localhost:8765:1",
        "user@localhost",
    ],
)
def test_every_other_name_and_every_malformed_host_is_refused(host: str | None) -> None:
    assert not _LOOPBACK.answers(host)


@pytest.mark.parametrize(
    "host", ["vantage.example.com", "VANTAGE.example.COM:443", "vantage.example.com."]
)
def test_an_allowed_name_is_answered_in_any_case_on_any_port(host: str) -> None:
    assert _PROXIED.answers(host)
    assert _PROXIED.answers("localhost:8765")
    assert _PROXIED.answers("[::1]:8765")
    assert not _PROXIED.answers("other.example.com")


@pytest.mark.parametrize(
    ("host", "name"),
    [
        ("Example.COM:8765", "example.com"),
        ("example.com.", "example.com"),
        ("[::1]:8765", "[::1]"),
        ("[FE80::1]", "[fe80::1]"),
        ("127.0.0.1:", "127.0.0.1"),
        ("example.com:x", None),
        ("[::1]:x", None),
        (".", None),
        (None, None),
    ],
)
def test_the_host_of_a_header_is_its_name_without_the_port(
    host: str | None, name: str | None
) -> None:
    assert request_host(host) == name


@pytest.mark.parametrize(
    ("value", "name"),
    [
        ("vantage.example.com", "vantage.example.com"),
        ("Vantage.Example.COM", "vantage.example.com"),
        ("vantage.example.com.", "vantage.example.com"),
        ("  vantage  ", "vantage"),
        ("my_service", "my_service"),
        ("a" * 63, "a" * 63),
    ],
)
def test_an_allowed_name_is_kept_lower_cased_without_its_trailing_dot(
    value: str, name: str
) -> None:
    assert allowed_host_name(value) == name


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        ".",
        "https://vantage.example.com",
        "vantage.example.com:8765",
        "vantage.example.com/api",
        "*.example.com",
        "[::1]",
        "-vantage.example.com",
        "vantage-.example.com",
        "vantage..example.com",
        "vantage.example.com..",
        "a" * 64,
        ".".join(["a" * 63] * 4) + "a",
        "vänt.example.com",
        "vantage example",
    ],
)
def test_anything_but_a_host_name_is_refused(value: str) -> None:
    with pytest.raises(HostNameError, match="is not a host name"):
        allowed_host_name(value)


@pytest.mark.parametrize(
    "bind", ["127.0.0.1", "127.0.1.1", "127.8.9.10", "::1", "::ffff:127.0.0.1"]
)
def test_a_loopback_bind_has_a_rule_with_no_name_given(bind: str) -> None:
    assert is_loopback(bind)
    assert host_rule(bind, frozenset()) == HostRule(frozenset())


@pytest.mark.parametrize("bind", ["0.0.0.0", "::", "192.168.1.20", "fe80::1%eth0"])  # noqa: S104
def test_a_wide_bind_checks_names_only_when_some_are_given(bind: str) -> None:
    """Reached by names nobody here can list, it answers them all, unless
    its operator names the ones it is reached by."""
    assert not is_loopback(bind)
    assert host_rule(bind, frozenset()) is None
    assert host_rule(bind, frozenset({"vantage.example.com"})) == _PROXIED


@pytest.mark.parametrize("given", ["localhost", "127.1", "2130706433", "ip6-localhost", "agentbox"])
def test_only_a_bound_address_is_judged_never_the_text_that_named_it(given: str) -> None:
    """Each of these binds wherever the resolver puts it, loopback on most
    machines, so the rule is built from the address the socket then holds
    (`cli.main`); judged as text, a loopback bind would read as wide and
    answer every name."""
    assert not is_loopback(given)


def test_a_loopback_bind_answers_the_names_given_as_well() -> None:
    rule = host_rule("127.0.0.1", frozenset({"vantage.example.com"}))

    assert rule is not None
    assert rule.answers("vantage.example.com:8765")
    assert rule.answers("localhost:8765")


# --- the middleware -------------------------------------------------------------


@pytest.fixture
def build(tmp_path: Path) -> Path:
    directory = tmp_path / "client"
    directory.mkdir()
    (directory / "index.html").write_bytes(b"<!doctype html><title>vantage</title>\n")
    return directory


def _client(rule: HostRule | None, name: str, *, build: Path | None = None) -> TestClient:
    app = create_app(InMemoryExecutionStore(), client=build, hosts=rule)
    # In the header, since `TestClient` takes no IPv6 literal in a URL.
    return TestClient(app, headers={"Host": f"{name}:8765"})


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/"),
        ("HEAD", "/"),
        ("GET", "/p/default/runs"),
        ("GET", "/assets/app.js"),
        ("GET", "/api/v1/capabilities"),
        ("GET", "/api/v1/openapi.yaml"),
        ("GET", "/api/v1/projects/default/runs"),
        ("POST", "/api/v1/runs"),
        ("POST", "/api/v1/session"),
        ("DELETE", "/api/v1/session"),
        ("GET", "/api/v1/nowhere"),
        ("PATCH", "/api/v1/capabilities"),
    ],
)
def test_a_name_not_answered_is_refused_on_every_path_and_method(
    build: Path, method: str, path: str
) -> None:
    """The page, a hashed asset, every kind of API route, and the paths
    nothing serves: each is refused the same way, before anything else
    answers, and the name sent is never repeated."""
    client = _client(_LOOPBACK, "rebound.example.net", build=build)

    response = client.request(method, path, json={} if method == "POST" else None)

    assert response.status_code == 421, response.text
    assert response.headers["content-type"] == "application/json"
    if method != "HEAD":
        assert response.json() == _REFUSAL
    assert "rebound" not in response.text
    for header, value in SECURITY_HEADERS:
        assert response.headers.get_list(header) == [value], header


@pytest.mark.parametrize("name", ["localhost", "127.0.0.1", "[::1]", "vantage.example.com"])
def test_a_name_answered_reaches_the_client_and_the_api(build: Path, name: str) -> None:
    client = _client(_PROXIED, name, build=build)

    assert client.get("/").status_code == 200
    assert client.get("/api/v1/capabilities").status_code == 200
    assert client.get("/api/v1/nowhere").json()["error"] == "not_found"


def test_the_refusal_comes_before_every_refusal_of_who_asks() -> None:
    """A server with users refuses a request with no token with 401, and a
    cookie from another site with 403; a name it does not answer, first."""
    store = InMemoryExecutionStore()
    store.create_user("alice", admin=True, created_at=datetime.now(timezone.utc))
    app = create_app(store, hosts=_LOOPBACK)
    refused = TestClient(app, base_url="http://rebound.example.net")
    answered = TestClient(app, base_url="http://localhost")
    cookie = {"Cookie": "__Host-vantage_session=x", "Sec-Fetch-Site": "cross-site"}

    assert answered.get("/api/v1/projects").status_code == 401
    assert answered.get("/api/v1/projects", headers=cookie).status_code == 403
    assert refused.get("/api/v1/projects").status_code == 421
    assert refused.get("/api/v1/projects", headers=cookie).status_code == 421


def test_an_app_given_no_rule_answers_every_name() -> None:
    """The suite's apps, and a server bound wide with no name given."""
    for name in ("testserver", "rebound.example.net"):
        assert _client(None, name).get("/api/v1/capabilities").status_code == 200


def _asgi_status(rule: HostRule, headers: list[tuple[bytes, bytes]]) -> int:
    """The status an app with `rule` answers a GET carrying exactly
    `headers`, which `TestClient` cannot send without a `Host`."""
    app = create_app(InMemoryExecutionStore(), hosts=rule)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/capabilities",
        "raw_path": b"/api/v1/capabilities",
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8765),
        "state": {},
    }
    asyncio.run(app(scope, receive, send))
    status: int = sent[0]["status"]
    return status


def test_a_request_without_a_host_or_with_two_is_refused() -> None:
    """HTTP/1.0 may omit it; two leave the name the browser meant unknown."""
    assert _asgi_status(_LOOPBACK, [(b"host", b"localhost:8765")]) == 200
    assert _asgi_status(_LOOPBACK, []) == 421
    assert _asgi_status(_LOOPBACK, [(b"host", b"localhost"), (b"host", b"localhost")]) == 421
