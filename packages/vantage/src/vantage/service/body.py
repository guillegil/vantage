"""Reading a JSON request body: media type first, then the bytes under a
size cap, then a strict parse.

Shared by the routes that take a body: `POST /runs`, `POST /projects`, a
project's `POST .../config/sections`, `POST /users`, `PATCH /users/{name}`
and `POST /tokens`. None declares its
body as a parameter, because
FastAPI would then read the whole body, with no bound, and parse it on the
event loop every request shares, before the route's first line runs.
Instead:

1. `require_json_media_type` checks `Content-Type` from the header alone.
2. `read_bounded_body` streams the body and stops the moment the running
   total passes the route's cap; `Content-Length` is never trusted. A
   client disconnect mid-transfer becomes `IncompleteBodyError`.
3. `vantage.ingestion.decode.decode_json` parses the complete, capped body
   and makes its text storable. It blocks for as long as the body is
   large, so the routes call it in the threadpool.
"""

from __future__ import annotations

from fastapi import Request
from starlette.requests import ClientDisconnect

from vantage.service.errors import (
    IncompleteBodyError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)

_JSON_MEDIA_TYPE = "application/json"


def require_json_media_type(request: Request) -> None:
    """Reject on the `Content-Type` header alone, before any body byte is read."""
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != _JSON_MEDIA_TYPE:
        raise UnsupportedMediaTypeError(media_type)


async def read_bounded_body(request: Request, limit: int) -> bytes:
    """Stream the body, aborting as soon as it exceeds `limit` bytes.

    `Content-Length` is not relied on: it can be absent, wrong or a lie.
    The check runs on every chunk received, so the buffer never grows much
    past `limit`, and no chunk after the one that crossed it is asked for.

    A client that disconnects mid-body (a killed process, a dropped
    connection) surfaces as `ClientDisconnect` from `request.stream()`; it
    is converted to `IncompleteBodyError` so it takes the same rejection
    path as everything else instead of escaping the ASGI app unhandled.
    """
    buffer = bytearray()
    try:
        async for chunk in request.stream():
            buffer += chunk
            if len(buffer) > limit:
                raise PayloadTooLargeError(limit)
    except ClientDisconnect as exc:
        raise IncompleteBodyError() from exc
    return bytes(buffer)


__all__ = ["read_bounded_body", "require_json_media_type"]
