"""The web client's files, served from memory, and the headers every answer
carries.

**Two pure-ASGI middlewares, not routes.** `WebClient` answers `GET` and
`HEAD` for every path but `/api` and those under it, and hands everything
else to the router untouched: a catch-all route would answer an
unversioned `POST /runs` with 405 instead of 404, and would join the route
table the interface document is checked against. `SecurityHeaders` adds
the headers below to the start of every answer and never touches a body,
so `POST /runs` still streams its body under its cap; Starlette's
`BaseHTTPMiddleware` would read the answer through a stream of its own.
Neither calls the store.

**The files are read once.** `load_client` reads every file of a build
when the app is made, never on a request, so a request never touches the
disk and a build replaced under a running server is not half served.

**The page is `index.html`, verbatim.** Every path that is neither the API,
a built file nor under `/assets/` answers it, so the client routes in the
browser and a reload of any of its addresses works. It is never
templated, so nothing a request carries can reach it, and it answers under
`PAGE_POLICY`, which allows the page's own files and nothing else: no
inline script or style, no other origin, and no string turned into HTML.
A hashed file under `/assets/` never changes, so it may be cached for a
year; a name no build made answers 404, never the page, so a stale page
asking for its old files fails visibly. The page and the other built files
are revalidated on every use, by their ETags.

**Without a build, the page says so.** A checkout where the client was
never built serves the API, and every other `GET` answers 404 with a
static page naming the API and how to build the client.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Where a build puts the client, inside the package, so a wheel carries it.
CLIENT_DIRECTORY = Path(__file__).with_name("client")

PAGE_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'; object-src 'none'; "
    "require-trusted-types-for 'script'; trusted-types 'none'"
)

# Added to every answer that does not set its own, so an answer that
# carries a token or a run's failure text is never kept by a browser's
# cache, never framed, and never read by a page of another origin.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)

# The suffixes a client build holds, each with its media type. Fixed here
# rather than asked of `mimetypes`, whose answers vary by platform; any
# other file is bytes, which `nosniff` keeps a browser from running.
# `web/build/check-build.mjs` refuses a build holding any other suffix
# (`.html` is the page's own); keep the two lists equal.
_MEDIA_TYPES: Mapping[str, str] = MappingProxyType(
    {
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".woff2": "font/woff2",
        ".svg": "image/svg+xml",
        ".txt": "text/plain; charset=utf-8",
    }
)
_BYTES = "application/octet-stream"
_HTML = "text/html; charset=utf-8"

_ASSETS = "/assets/"
_API = "/api"
_IMMUTABLE = "public, max-age=31536000, immutable"
_REVALIDATE = "no-cache"

_NOT_FOUND = b"Not found\n"

_MISSING_CLIENT = (
    b"<!doctype html>\n"
    b'<html lang="en">\n'
    b'<head><meta charset="utf-8"><title>vantage</title></head>\n'
    b"<body>\n"
    b"<p>This vantage was installed without its web client. Its HTTP API answers at "
    b'/api/v1, described by <a href="/api/v1/openapi.yaml">/api/v1/openapi.yaml</a>. '
    b"A wheel built by CI carries the client; in a checkout, run "
    b"<code>pnpm --dir web install --frozen-lockfile &amp;&amp; "
    b"pnpm --dir web run build</code> and restart vantage.</p>\n"
    b"</body>\n"
    b"</html>\n"
)


@dataclass(frozen=True)
class ClientFile:
    """One file of a build, as it is answered."""

    body: bytes
    media_type: str
    cache_control: str
    etag: str


@dataclass(frozen=True)
class ClientFiles:
    """A build: its page, and every other file by its exact URL path."""

    page: ClientFile
    files: Mapping[str, ClientFile]


def load_client(directory: Path) -> ClientFiles | None:
    """Read every file of the build in `directory`, or `None` when it holds
    no `index.html`, as a checkout where the client was never built does."""
    index = directory / "index.html"
    if not index.is_file():
        return None
    files: dict[str, ClientFile] = {}
    for path in sorted(directory.rglob("*")):
        if path == index or not path.is_file():
            continue
        url = "/" + path.relative_to(directory).as_posix()
        cache_control = _IMMUTABLE if url.startswith(_ASSETS) else _REVALIDATE
        media_type = _MEDIA_TYPES.get(path.suffix, _BYTES)
        files[url] = _client_file(path.read_bytes(), media_type, cache_control)
    page = _client_file(index.read_bytes(), _HTML, _REVALIDATE)
    return ClientFiles(page=page, files=MappingProxyType(files))


def _client_file(body: bytes, media_type: str, cache_control: str) -> ClientFile:
    etag = f'"{hashlib.sha256(body).hexdigest()}"'
    return ClientFile(body=body, media_type=media_type, cache_control=cache_control, etag=etag)


class WebClient:
    """Answer `GET` and `HEAD` outside `/api` from `files`, or with the page
    saying the client is missing when there is none."""

    def __init__(self, app: ASGIApp, files: ClientFiles | None) -> None:
        self.app = app
        self.files = files

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in ("GET", "HEAD")
            or _is_api(scope["path"])
        ):
            await self.app(scope, receive, send)
            return
        head = scope["method"] == "HEAD"
        if self.files is None:
            await _answer(send, 404, _MISSING_CLIENT, [("content-type", _HTML)], head=head)
            return
        path = scope["path"]
        file = self.files.files.get(path)
        headers: list[tuple[str, str]] = []
        if file is None:
            if path.startswith(_ASSETS):
                plain = [("content-type", "text/plain; charset=utf-8")]
                await _answer(send, 404, _NOT_FOUND, plain, head=head)
                return
            file = self.files.page
            headers.append(("content-security-policy", PAGE_POLICY))
        # What a cache revalidating its copy replaces that copy's headers
        # with, so a 304 carries them as well, the page's policy included.
        headers += [("cache-control", file.cache_control), ("etag", file.etag)]
        if _matches(_request_header(scope, b"if-none-match"), file.etag):
            await _answer(send, 304, b"", headers, head=True)
            return
        headers.append(("content-type", file.media_type))
        await _answer(send, 200, file.body, headers, head=head)


class SecurityHeaders:
    """Add each of `SECURITY_HEADERS` to an answer that has not set it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", ()))
                present = {name.lower() for name, _ in headers}
                headers += [
                    (name, value) for name, value in _ENCODED_HEADERS if name not in present
                ]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


_ENCODED_HEADERS = tuple(
    (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in SECURITY_HEADERS
)


def _is_api(path: str) -> bool:
    return path == _API or path.startswith(_API + "/")


def _request_header(scope: Scope, name: bytes) -> str | None:
    values = [value.decode("latin-1") for key, value in scope["headers"] if key.lower() == name]
    return ", ".join(values) if values else None


def _matches(if_none_match: str | None, etag: str) -> bool:
    """Whether `If-None-Match` names `etag`, compared weakly as the header
    asks, or is `*`."""
    if if_none_match is None:
        return False
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate.removeprefix("W/") == etag:
            return True
    return False


async def _answer(
    send: Send,
    status: int,
    body: bytes,
    headers: Iterable[tuple[str, str]],
    *,
    head: bool,
) -> None:
    """Send `body` with `headers` and its length, or only the headers for a
    `HEAD`; a 304 has no body and names no length."""
    raw = [(name.encode("latin-1"), value.encode("latin-1")) for name, value in headers]
    if status != 304:
        raw.append((b"content-length", str(len(body)).encode("latin-1")))
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": b"" if head else body})


__all__ = [
    "CLIENT_DIRECTORY",
    "PAGE_POLICY",
    "SECURITY_HEADERS",
    "ClientFile",
    "ClientFiles",
    "SecurityHeaders",
    "WebClient",
    "load_client",
]
