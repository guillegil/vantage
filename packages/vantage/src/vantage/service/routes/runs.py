"""`POST /api/v1/runs` -- session report ingestion (RQ-41, RQ-42, design.md D3, D5).

**The 201-vs-200 decision comes from the boolean `record_execution` already
returns.** `record_execution` is `INSERT ... ON CONFLICT(id) DO NOTHING`,
deciding its own return value from the INSERT's own row count -- no
preceding `SELECT`. This route does not ask the store whether the id exists
and then decide: that would reintroduce, at the HTTP layer, precisely the
check-then-act race the storage adapter's `ON CONFLICT` avoids at the SQL
layer. One call, one boolean, one branch.

**The media type and size checks both run before the body is touched.**
This route does *not* declare `payload: SessionReport` as a parameter --
that would make FastAPI parse and fully buffer the body itself before this
function's first line ever runs, which is exactly the ordering hazard RQ-42
warns against: a check performed after the bytes are already buffered has
protected nothing. Instead the body is read by hand, in the order the
threat matrix requires:

1. `Content-Type` is read off the header alone (`_require_json_media_type`)
   -- zero body bytes have been touched yet.
2. `_read_bounded_body` streams the body through `request.stream()` and
   raises the instant the running total exceeds `MAX_REPORT_BYTES`, without
   asking the stream for another chunk. That is the one line doing the
   actual protecting -- it does not trust `Content-Length`, which can be
   absent, wrong, or simply a lie. A client that disconnects mid-transfer
   (RQ-3) surfaces here too, as `starlette.requests.ClientDisconnect`,
   converted to `IncompleteBodyError` rather than left to propagate.
3. Only once a complete, capped byte string exists is it parsed as JSON,
   then validated against `SessionReport`.

Nothing is written before all three steps succeed, so a rejection at any
step leaves `count_executions() == 0`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from vantage.core.domain.execution import Execution, Identity
from vantage.service.errors import (
    MAX_REPORT_BYTES,
    IncompleteBodyError,
    InvalidJsonError,
    InvalidReportError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from vantage.service.schemas import Acknowledgement, RunReport, SessionReport

router = APIRouter()

_JSON_MEDIA_TYPE = "application/json"


def _to_execution(run: RunReport) -> Execution:
    return Execution(
        identity=Identity(run.id),
        started_at=run.started_at,
        finished_at=run.finished_at,
        exit_status=run.exit_status,
        interrupted=run.interrupted,
        interrupt_reason=run.interrupt_reason,
    )


def _require_json_media_type(request: Request) -> None:
    """Reject on the `Content-Type` header alone, before any body byte is read."""
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != _JSON_MEDIA_TYPE:
        raise UnsupportedMediaTypeError(media_type or "<absent>")


async def _read_bounded_body(request: Request) -> bytes:
    """Stream the body, aborting before the buffer can exceed the cap.

    Deliberately does not rely on `Content-Length`: it can be absent, wrong,
    or an outright lie, and none of those excuse buffering an unbounded
    body. The check below runs on every chunk actually received, so the
    buffer is structurally incapable of growing past `MAX_REPORT_BYTES`.

    A client that disconnects before sending the whole body -- a process
    killed mid-write (RQ-3.1), a network partition mid-transfer (RQ-3.2) --
    surfaces here as `starlette.requests.ClientDisconnect`, raised from
    inside `request.stream()` itself. Catching it and converting it to
    `IncompleteBodyError` is what keeps this the one exception-handling
    path every rejection goes through (design.md D5); the alternative is
    letting it propagate unhandled out of the ASGI application, which is
    exactly what task 3.7's raw-socket test caught before this existed.
    Either way `count_executions()` stays at zero: no streaming parse, no
    partial-parse path, nothing is written before this function returns a
    complete body.
    """
    buffer = bytearray()
    try:
        async for chunk in request.stream():
            buffer += chunk
            if len(buffer) > MAX_REPORT_BYTES:
                # THE LINE THAT PROTECTS: raised the moment the running
                # total crosses the cap, before the loop asks
                # `request.stream()` for another chunk -- the read stops
                # here, it does not continue and check afterwards.
                raise PayloadTooLargeError()
    except ClientDisconnect as exc:
        raise IncompleteBodyError() from exc
    return bytes(buffer)


@router.post("/runs")
async def create_run(request: Request) -> JSONResponse:
    _require_json_media_type(request)
    body = await _read_bounded_body(request)

    try:
        payload_dict = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidJsonError() from exc

    try:
        payload = SessionReport.model_validate(payload_dict)
    except ValidationError as exc:
        raise InvalidReportError.from_errors(exc.errors()) from exc

    store = request.app.state.store
    execution = _to_execution(payload.run)

    created = store.record_execution(execution, received_at=datetime.now(timezone.utc))

    acknowledgement = Acknowledgement(
        run_id=payload.run.id,
        status="created" if created else "duplicate",
        ignored=[],
    )
    return JSONResponse(
        status_code=201 if created else 200,
        content=acknowledgement.model_dump(),
    )
