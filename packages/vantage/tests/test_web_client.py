"""The web client's files (`service/web.py`): an app given a build answers
every `GET` and `HEAD` outside `/api` from it -- the page under its own
policy, hashed assets cached for good, the rest revalidated -- and never
shadows the API; an app given no build answers a page saying so; an app
given nothing serves no client at all; and every answer, the API's
included, carries the security headers.

Each app here is given a fixture build in `tmp_path`, never the package's
own `client/`, which a checkout may or may not hold.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from memory_store import InMemoryExecutionStore
from starlette.types import Message
from vantage.service.app import create_app
from vantage.service.web import PAGE_POLICY, SECURITY_HEADERS

_INDEX = (
    b"<!doctype html>\n"
    b'<html lang="en"><head><meta charset="utf-8"><title>vantage</title>'
    b'<script type="module" src="/assets/app-3f9a.js"></script>'
    b'<link rel="stylesheet" href="/assets/app-3f9a.css"></head>'
    b'<body><div id="root"></div></body></html>\n'
)

# By URL path: the body, and the media type and caching it answers with.
_BUILT = {
    "/assets/app-3f9a.js": (
        b"export const answer = 42;\n",
        "text/javascript; charset=utf-8",
        "public, max-age=31536000, immutable",
    ),
    "/assets/app-3f9a.css": (
        b":root { color-scheme: light; }\n",
        "text/css; charset=utf-8",
        "public, max-age=31536000, immutable",
    ),
    "/assets/Chivo-1a2b.woff2": (
        b"wOF2\x00\x01\x00\x00fixture",
        "font/woff2",
        "public, max-age=31536000, immutable",
    ),
    "/fonts/OFL.txt": (
        b"SIL OPEN FONT LICENSE Version 1.1\n",
        "text/plain; charset=utf-8",
        "no-cache",
    ),
    "/notes.bin": (b"\x00\x01\x02 not a known suffix", "application/octet-stream", "no-cache"),
}

_API_DEFAULTS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}

_NOT_FOUND = {"error": "not_found", "detail": "No route matches that path.", "fields": []}
_METHOD_NOT_ALLOWED = {
    "error": "method_not_allowed",
    "detail": "That method is not allowed on this path.",
    "fields": [],
}

_MISSING_CLIENT = "This vantage was installed without its web client."


@pytest.fixture
def build(tmp_path: Path) -> Path:
    """A client build: its page, hashed assets, a licence and a file of a
    suffix no build is expected to hold."""
    directory = tmp_path / "client"
    (directory / "assets").mkdir(parents=True)
    (directory / "fonts").mkdir()
    (directory / "index.html").write_bytes(_INDEX)
    for path, (body, _media_type, _cache_control) in _BUILT.items():
        (directory / path.lstrip("/")).write_bytes(body)
    return directory


@pytest.fixture
def client(build: Path) -> TestClient:
    return TestClient(create_app(InMemoryExecutionStore(), client=build))


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/sign-in",
        "/p/default/runs",
        f"/runs/{'0123456789abcdef' * 2}",
        "/apiary",
        "/docs",
        "/capabilities",
        "/index.html",
    ],
)
def test_every_other_path_answers_the_page_under_its_policy(client: TestClient, path: str) -> None:
    """The client routes in the browser, so any address of it, reloaded,
    gets the same page: verbatim, revalidated, and under `PAGE_POLICY`
    alone. A path that only begins like `/api` is not the API."""
    response = client.get(path)

    assert response.status_code == 200
    assert response.content == _INDEX
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert re.fullmatch(r'"[0-9a-f]{64}"', response.headers["etag"])
    assert response.headers.get_list("content-security-policy") == [PAGE_POLICY]


def test_head_answers_the_pages_headers_without_its_body(build: Path) -> None:
    """Asked of the app itself, since an HTTP client drops whatever body a
    `HEAD` answer carries: a server would send it."""
    app = create_app(InMemoryExecutionStore(), client=build)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "HEAD",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))

    start, *bodies = sent
    headers = {name.decode(): value.decode() for name, value in start["headers"]}
    assert start["status"] == 200
    assert headers["content-length"] == str(len(_INDEX))
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert headers["content-security-policy"] == PAGE_POLICY
    assert [body.get("body", b"") for body in bodies] == [b""]


def test_a_page_the_browser_holds_is_not_sent_again(client: TestClient) -> None:
    """A 304 replaces the headers of the copy a cache keeps, so it carries
    the page's own policy and caching, not the defaults every other answer
    gets, which would leave the kept page unable to run."""
    etag = client.get("/runs").headers["etag"]

    response = client.get("/sign-in", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["content-security-policy"] == PAGE_POLICY
    assert client.get("/", headers={"If-None-Match": '"stale"'}).status_code == 200


@pytest.mark.parametrize(("path", "expected"), list(_BUILT.items()), ids=list(_BUILT))
def test_each_built_file_answers_its_bytes_type_and_caching(
    client: TestClient, path: str, expected: tuple[bytes, str, str]
) -> None:
    """A hashed name under `/assets/` never changes, so it is kept for a
    year; the others are revalidated. A suffix no build is expected to hold
    is served as bytes the browser may not sniff into something it runs."""
    body, media_type, cache_control = expected

    response = client.get(path)

    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"] == media_type
    assert response.headers["cache-control"] == cache_control
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == _API_DEFAULTS["Content-Security-Policy"]


def test_an_asset_no_build_made_is_not_found_rather_than_the_page(client: TestClient) -> None:
    """A page kept from an older build asks for names this one lacks, and
    must fail on them visibly, not load the page as a script."""
    for path in ("/assets/missing.js", "/assets/"):
        response = client.get(path)

        assert response.status_code == 404, path
        assert response.content != _INDEX, path
        assert response.headers["content-type"] == "text/plain; charset=utf-8", path


@pytest.mark.parametrize(
    ("method", "path", "status", "body"),
    [
        ("GET", "/api", 404, _NOT_FOUND),
        ("GET", "/api/", 404, _NOT_FOUND),
        ("GET", "/api/v1/nope", 404, _NOT_FOUND),
        ("GET", "/api/runs", 404, _NOT_FOUND),
        ("HEAD", "/api/v1/nope", 404, None),
        ("POST", "/api", 404, _NOT_FOUND),
        ("POST", "/api/", 404, _NOT_FOUND),
        ("POST", "/api/v1/nope", 404, _NOT_FOUND),
        ("POST", "/api/runs", 404, _NOT_FOUND),
        ("GET", "/api/v1/runs", 405, _METHOD_NOT_ALLOWED),
        ("POST", "/runs", 404, _NOT_FOUND),
        ("PUT", "/p/x", 404, _NOT_FOUND),
    ],
)
def test_the_api_is_never_shadowed(
    client: TestClient, method: str, path: str, status: int, body: dict[str, object] | None
) -> None:
    """Every path under `/api`, and every method but `GET` and `HEAD`
    anywhere, is the router's, whose refusals keep the rejection shape."""
    response = client.request(method, path)

    assert response.status_code == status
    assert response.content != _INDEX
    if body is not None:
        assert response.json() == body
    if status == 405:
        assert "POST" in response.headers["allow"]


def test_the_client_adds_nothing_to_the_route_table(build: Path) -> None:
    """The client is served by middleware, so the routes the interface
    document is checked against are the API's alone."""
    with_client = create_app(InMemoryExecutionStore(), client=build)
    without = create_app(InMemoryExecutionStore())

    assert with_client.openapi()["paths"] == without.openapi()["paths"]


@pytest.mark.parametrize("state", ["missing", "without-index"])
def test_without_a_build_every_page_says_how_to_build_one(tmp_path: Path, state: str) -> None:
    directory = tmp_path / "client"
    if state == "without-index":
        (directory / "assets").mkdir(parents=True)
        (directory / "assets" / "app-3f9a.js").write_bytes(b"stale\n")
    client = TestClient(create_app(InMemoryExecutionStore(), client=directory))

    for path in ("/", "/p/default/runs", "/assets/app-3f9a.js"):
        response = client.get(path)

        assert response.status_code == 404, path
        assert response.headers["content-type"] == "text/html; charset=utf-8", path
        assert _MISSING_CLIENT in response.text, path
        assert "/api/v1/openapi.yaml" in response.text, path
        assert "pnpm --dir web run build" in response.text, path
        assert "<script" not in response.text.lower(), path
        assert "<style" not in response.text.lower(), path
        assert "style=" not in response.text.lower(), path
    assert client.get("/api/v1/capabilities").status_code == 200


def test_an_app_given_no_client_serves_none() -> None:
    """The app every other test builds for the API routes as it always has,
    whether or not the client has been built in the checkout."""
    client = TestClient(create_app(InMemoryExecutionStore()))

    response = client.get("/")

    assert response.status_code == 404
    assert response.json() == _NOT_FOUND


def test_the_build_is_read_once_when_the_app_is_made(build: Path) -> None:
    """A request never reads the disk, so a build removed or replaced under
    a running server changes nothing it answers."""
    client = TestClient(create_app(InMemoryExecutionStore(), client=build))
    shutil.rmtree(build)

    assert client.get("/").content == _INDEX
    assert client.get("/assets/app-3f9a.js").content == _BUILT["/assets/app-3f9a.js"][0]


def test_every_answer_carries_the_security_headers(client: TestClient, build: Path) -> None:
    """The API's own answers, a refusal of who asks, the router's 404, the
    page and an asset: each carries every header, the API's the restrictive
    defaults, and the page keeps its own policy and caching."""
    closed_store = InMemoryExecutionStore()
    closed_store.create_user("alice", admin=True, created_at=datetime.now(timezone.utc))
    closed = TestClient(create_app(closed_store, client=build))

    api = {
        "200": client.get("/api/v1/capabilities"),
        "401": closed.get("/api/v1/projects"),
        "404": client.get("/api/v1/nope"),
    }
    page = client.get("/")
    asset = client.get("/assets/app-3f9a.js")

    assert [response.status_code for response in api.values()] == [200, 401, 404]
    for response in (*api.values(), page, asset):
        for name, value in SECURITY_HEADERS:
            assert len(response.headers.get_list(name)) == 1, (response.url, name)
            if name not in ("Content-Security-Policy", "Cache-Control"):
                assert response.headers[name] == value, (response.url, name)
    for status, response in api.items():
        for name, value in _API_DEFAULTS.items():
            assert response.headers[name] == value, (status, name)
    assert page.headers["Content-Security-Policy"] == PAGE_POLICY
    assert page.headers["Cache-Control"] == "no-cache"
    assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_the_page_policy_allows_the_pages_own_files_and_nothing_else() -> None:
    """No inline script or style, no string evaluated as code, no data URL
    and no other origin; strings never reach a script sink; no page may
    frame this one, and no form posts natively."""
    directives = {
        name: value
        for name, _, value in (directive.partition(" ") for directive in PAGE_POLICY.split("; "))
    }

    assert PAGE_POLICY == (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'; object-src 'none'; "
        "require-trusted-types-for 'script'; trusted-types 'none'"
    )
    for forbidden in ("unsafe-inline", "unsafe-eval", "unsafe-hashes", "data:", "http", "*"):
        assert forbidden not in PAGE_POLICY, forbidden
    assert ":" not in PAGE_POLICY
    assert directives["default-src"] == "'none'"
    assert directives["require-trusted-types-for"] == "'script'"
    assert directives["trusted-types"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["base-uri"] == "'none'"
    assert directives["form-action"] == "'none'"
    assert set(directives.values()) <= {"'self'", "'none'", "'script'"}
