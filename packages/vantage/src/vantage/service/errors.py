"""Single place that shapes every rejection body (design.md D5).

**Why one function, not four ad hoc responses.** FastAPI's default handler
for `RequestValidationError` mirrors the client's own submitted value back
in an ``"input"`` key, includes pydantic's internal error ``"type"`` string,
and can carry a ``"url"`` pointing at versioned pydantic documentation --
three leaks in one body (RQ-42.4). A test report can legitimately carry a
filesystem path, a node id, or an environment-derived string; the field that
fails validation is exactly the field whose value would be echoed back to an
unauthenticated caller.

**An allow-list beats a deny-list here.** Stripping known-dangerous keys out
of pydantic's error dicts requires naming every dangerous key correctly, and
a pydantic upgrade can add one a deny-list has never heard of. Every
rejection response in this module is instead built from scratch, from a
fixed set of fields this file names: an error code, one human sentence, and
dotted field paths. Nothing pydantic hands back is ever passed through.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Threat matrix "Unbounded request body": the cap the read path enforces
# before the body is fully buffered (service/runs.py).
MAX_REPORT_BYTES = 1024 * 1024  # 1 MiB


# A path segment safe to echo: a schema field name, or a list index. The
# allow-list is deliberate -- see `_safe_segment`.
_SAFE_SEGMENT = re.compile(r"\A([A-Za-z_][A-Za-z0-9_]{0,63}|[0-9]{1,9})\Z")

_UNNAMEABLE_SEGMENT = "<unnamed>"


def _safe_segment(part: object) -> str:
    """Return ``part`` only when it is a name this schema could have declared.

    Almost every ``loc`` segment is a field name from our own models, and
    echoing it is the entire point -- the client needs to know what to fix.
    The exception is ``extra_forbidden``, whose final segment is a key the
    CLIENT chose, and it is not a name at all: it is arbitrary bytes. Echoed
    verbatim it reflects up to ``MAX_REPORT_BYTES`` of attacker-chosen text
    back in the response, and carries CR/LF into any log line that records
    the rejection -- forged log entries from an unauthenticated caller.

    So this is an allow-list too, for the same reason the module docstring
    gives: a deny-list of dangerous characters has to guess right every
    time, and it only has to be wrong once.
    """
    text = str(part)
    return text if _SAFE_SEGMENT.match(text) else _UNNAMEABLE_SEGMENT


def _dotted_path(location: Iterable[object]) -> str:
    """A pydantic ``loc`` tuple, e.g. ``("body", "run", "started_at")``, to
    ``"run.started_at"`` -- dotted paths only, never pydantic's list form,
    and never the ``"body"`` segment FastAPI prepends (it names the
    transport layer, not anything the client wrote in the payload).
    """
    return ".".join(_safe_segment(part) for part in location if part != "body")


def _fields_from_errors(errors: Iterable[Mapping[str, Any]]) -> list[str]:
    return [_dotted_path(error["loc"]) for error in errors]


def _rejection_body(error: str, detail: str, fields: list[str] | None = None) -> dict[str, object]:
    return {"error": error, "detail": detail, "fields": fields or []}


class RejectionError(Exception):
    """Base for every rejection this service can raise.

    One shape, one place: every subclass carries only ``status_code``,
    ``error``, ``detail`` and ``fields`` -- exactly what `_rejection_body`
    is allowed to emit, and nothing pydantic-specific.
    """

    status_code: int
    error: str

    def __init__(self, detail: str, fields: list[str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.fields = fields or []


class InvalidReportError(RejectionError):
    """Valid JSON, but the `run` section fails validation (RQ-42.1)."""

    status_code = 422
    error = "invalid_report"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidReportError:
        return cls(
            "The submitted report does not match the expected shape.",
            _fields_from_errors(errors),
        )


class InvalidJsonError(RejectionError):
    """Complete bytes, but not parseable JSON (RQ-42.2)."""

    status_code = 400
    error = "invalid_json"

    def __init__(self) -> None:
        super().__init__("The request body is not valid JSON.")


class IncompleteBodyError(RejectionError):
    """The client disconnected before sending the whole body (RQ-3.2,
    RQ-42's "Body truncated midway" scenario).

    Raised in `service/routes/runs.py`'s `_read_bounded_body` when
    `request.stream()` raises `starlette.requests.ClientDisconnect` --
    itself a real signal from a real disconnected socket, not something
    this service invents. A client that has already gone away can never
    observe this response (design.md D12's own note on the point); the
    value of turning the disconnect into a `RejectionError` instead of
    letting it propagate unhandled is that it completes cleanly through
    this module's one exception-handling path, rather than reaching
    uvicorn's own ASGI exception wrapper as an unhandled error.
    """

    status_code = 400
    error = "incomplete_body"

    def __init__(self) -> None:
        super().__init__("The request body was truncated before it was fully received.")


class PayloadTooLargeError(RejectionError):
    """The body exceeds `MAX_REPORT_BYTES` (threat matrix, unbounded body)."""

    status_code = 413
    error = "payload_too_large"

    def __init__(self) -> None:
        super().__init__(f"The request body exceeds the {MAX_REPORT_BYTES}-byte limit.")


class UnsupportedMediaTypeError(RejectionError):
    """Wrong or absent `Content-Type` (RQ-42, checked before the body is read)."""

    status_code = 415
    error = "unsupported_media_type"

    def __init__(self, media_type: str) -> None:
        super().__init__(f"Content-Type must be application/json, got {media_type!r}.")


def _rejection_response(exc: RejectionError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_rejection_body(exc.error, exc.detail, exc.fields),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Wire every rejection this service can raise through the one shape.

    Two sources, one output. `RejectionError` is raised by the manual body
    handling in `service/routes/runs.py` (media type, size cap, JSON parse,
    schema validation). `RequestValidationError` is FastAPI's own exception
    and is handled here too, as a safety net for anything still validated
    through automatic parameter binding -- neither handler ever forwards a
    pydantic error dict as-is.
    """

    @app.exception_handler(RejectionError)
    async def _handle_rejection(request: Request, exc: RejectionError) -> JSONResponse:
        del request
        return _rejection_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        return _rejection_response(InvalidReportError.from_errors(exc.errors()))


__all__ = [
    "MAX_REPORT_BYTES",
    "IncompleteBodyError",
    "InvalidJsonError",
    "InvalidReportError",
    "PayloadTooLargeError",
    "RejectionError",
    "UnsupportedMediaTypeError",
    "register_error_handlers",
]
