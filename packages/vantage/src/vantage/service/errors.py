"""Single place that shapes every rejection body.

**Why one function, not ad hoc responses.** FastAPI's default handler for
`RequestValidationError` mirrors the client's own submitted value back in an
``"input"`` key, includes pydantic's internal error ``"type"`` string, and
can carry a ``"url"`` pointing at versioned pydantic documentation -- three
leaks in one body. A test report can legitimately carry a filesystem path, a
node id, or an environment-derived string; the field that fails validation
is exactly the field whose value would be echoed back to an unauthenticated
caller.

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

# The request body cap, enforced while streaming, before the body is fully
# buffered (`service/routes/runs.py`).
MAX_REPORT_BYTES = 1024 * 1024  # 1 MiB


# A path segment safe to echo: a schema field name, or a list index. The
# allow-list is deliberate -- see `safe_segment`.
_SAFE_SEGMENT = re.compile(r"\A([A-Za-z_][A-Za-z0-9_]{0,63}|[0-9]{1,9})\Z")

_UNNAMEABLE_SEGMENT = "<unnamed>"


def safe_segment(part: object) -> str:
    """Return ``part`` only when it is a name this schema could have declared.

    Almost every ``loc`` segment is a field name from our own models, and
    echoing it is the entire point -- the client needs to know what to fix.
    The exception is ``extra_forbidden``, whose final segment is a key the
    CLIENT chose, and it is not a name at all: it is arbitrary bytes. Echoed
    verbatim it reflects up to ``MAX_REPORT_BYTES`` of attacker-chosen text
    back in the response, and carries CR/LF into any log line that records
    the rejection -- forged log entries from an unauthenticated caller.

    So this is an allow-list too: a deny-list of dangerous characters has
    to guess right every time, and it only has to be wrong once.

    Public because `service/routes/runs.py` reuses it for
    `Acknowledgement.ignored`: an unknown key on a tolerated `ResultReport`
    is the same client-chosen text, echoed on the accept path instead.
    """
    text = str(part)
    return text if _SAFE_SEGMENT.match(text) else _UNNAMEABLE_SEGMENT


def _dotted_path(location: Iterable[object]) -> str:
    """A pydantic ``loc`` tuple, e.g. ``("body", "run", "started_at")``, to
    ``"run.started_at"`` -- dotted paths only, never pydantic's list form,
    and never the ``"body"`` segment FastAPI prepends (it names the
    transport layer, not anything the client wrote in the payload).
    """
    return ".".join(safe_segment(part) for part in location if part != "body")


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
    """Valid JSON, but the report fails schema validation."""

    status_code = 422
    error = "invalid_report"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidReportError:
        return cls(
            "The submitted report does not match the expected shape.",
            _fields_from_errors(errors),
        )


class InvalidJsonError(RejectionError):
    """Complete bytes, but not parseable JSON."""

    status_code = 400
    error = "invalid_json"

    def __init__(self) -> None:
        super().__init__("The request body is not valid JSON.")


class IncompleteBodyError(RejectionError):
    """The client disconnected before sending the whole body.

    Raised by `service/routes/runs.py`'s `_read_bounded_body` when
    `request.stream()` raises `ClientDisconnect`. The client is gone and
    never sees this response; converting the disconnect keeps it on the one
    rejection path instead of surfacing as an unhandled ASGI error.
    """

    status_code = 400
    error = "incomplete_body"

    def __init__(self) -> None:
        super().__init__("The request body was truncated before it was fully received.")


class PayloadTooLargeError(RejectionError):
    """The body exceeds `MAX_REPORT_BYTES`."""

    status_code = 413
    error = "payload_too_large"

    def __init__(self) -> None:
        super().__init__(f"The request body exceeds the {MAX_REPORT_BYTES}-byte limit.")


class UnsupportedMediaTypeError(RejectionError):
    """Wrong or absent `Content-Type`, checked before the body is read."""

    status_code = 415
    error = "unsupported_media_type"

    def __init__(self, media_type: str) -> None:
        super().__init__(f"Content-Type must be application/json, got {media_type!r}.")


class InvalidIdentityError(RejectionError):
    """A `node_id` query value is missing or exceeds `MAX_IDENTITY_CHARS`.

    Chosen by `_handle_request_validation_error` only when every failing
    field is `node_id`, so an unrelated failure (e.g. a malformed `limit`)
    still gets the generic shape. The identity value itself is never
    echoed."""

    status_code = 422
    error = "invalid_identity"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidIdentityError:
        return cls(
            "The node_id query parameter is missing or exceeds the maximum identity length.",
            _fields_from_errors(errors),
        )


class InvalidMetadataFilterError(RejectionError):
    """`metadata_key` and `metadata_value` were not both supplied on
    `GET /api/v1/runs`.

    They are two parameters rather than one `key=value` string because a
    value may itself contain `=`. `fields` names whichever of the pair the
    caller omitted."""

    status_code = 422
    error = "invalid_metadata_filter"

    def __init__(self, missing_field: str) -> None:
        super().__init__(
            "metadata_key and metadata_value must be supplied together, or neither.",
            [missing_field],
        )


class UnknownRunError(RejectionError):
    """No run matches the id used in a heartbeat.

    Accepting a heartbeat for an id the server never saw would either
    manufacture liveness for a run that does not exist or require inventing
    a row with no `started_at` -- neither is acceptable, so this is a
    rejection, not a silent accept.
    """

    status_code = 404
    error = "unknown_run"

    def __init__(self) -> None:
        super().__init__("No run with that identifier has been recorded.")


class UnknownResultError(RejectionError):
    """A run is known, but no result matches the `node_id` used against the
    single-result endpoint.

    Distinct from `UnknownRunError`: "no such run" and "the run exists but
    has no result at that identity" are different facts the client needs to
    tell apart.
    """

    status_code = 404
    error = "unknown_result"

    def __init__(self) -> None:
        super().__init__("No result with that identifier has been recorded for this run.")


class InvalidSectionNameError(RejectionError):
    """A section name is empty after `strip()`, or exceeds
    `SECTION_NAME_MAX_CHARS`.

    The message and `fields` are fixed strings; the submitted name is never
    interpolated, so a hostile name (a CR/LF, a `</script>`) cannot ride
    along in the rejection body."""

    status_code = 422
    error = "invalid_section_name"

    def __init__(self) -> None:
        super().__init__(
            "The section name is empty after stripping whitespace, or exceeds the maximum length.",
            ["name"],
        )


class ReservedSectionNameError(RejectionError):
    """A section name equals `unassigned`, matched case-insensitively -- a
    distinct kind from `InvalidSectionNameError` because the client fixes
    them differently."""

    status_code = 422
    error = "reserved_section_name"

    def __init__(self) -> None:
        super().__init__("The name 'unassigned' is reserved and cannot be used.", ["name"])


class InvalidSectionPrefixError(RejectionError):
    """A section prefix is empty after `strip()`, or exceeds
    `SECTION_PREFIX_MAX_CHARS`. Same no-echo discipline as
    `InvalidSectionNameError`."""

    status_code = 422
    error = "invalid_section_prefix"

    def __init__(self) -> None:
        super().__init__(
            "The section prefix is empty after stripping whitespace, "
            "or exceeds the maximum length.",
            ["prefix"],
        )


class UnknownSectionError(RejectionError):
    """`DELETE /api/v1/config/sections` for a name that is not stored."""

    status_code = 404
    error = "unknown_section"

    def __init__(self) -> None:
        super().__init__("No section with that name has been stored.")


class TooManySectionsError(RejectionError):
    """A *new* section name would exceed `MAX_SECTIONS` -- renaming or
    updating an existing name is never refused by this check."""

    status_code = 422
    error = "too_many_sections"

    def __init__(self) -> None:
        super().__init__("The maximum number of stored sections has already been reached.")


class UnreadableSettingError(RejectionError):
    """A stored `value` fails its namespace's own Pydantic model -- a named
    `500`, not a traceback. `key` is routed through
    `safe_segment` before it ever reaches the body: a hand-edited database
    row is not guaranteed to hold a name this API would have accepted."""

    status_code = 500
    error = "unreadable_setting"

    def __init__(self, namespace: str, key: str) -> None:
        safe_key = safe_segment(key)
        super().__init__(
            f"The stored value for {safe_key!r} in namespace {namespace!r} could not be parsed.",
            [safe_key],
        )


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

    A `RequestValidationError` confined to the `node_id` query parameter
    (`GET /api/v1/tests/history`) is shaped as `InvalidIdentityError`; every
    other automatic-binding failure gets the `InvalidReportError` shape.
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
        errors = exc.errors()
        if errors and all(
            len(error["loc"]) >= 2 and error["loc"][0] == "query" and error["loc"][1] == "node_id"
            for error in errors
        ):
            return _rejection_response(InvalidIdentityError.from_errors(errors))
        return _rejection_response(InvalidReportError.from_errors(errors))


__all__ = [
    "MAX_REPORT_BYTES",
    "IncompleteBodyError",
    "InvalidIdentityError",
    "InvalidJsonError",
    "InvalidMetadataFilterError",
    "InvalidReportError",
    "InvalidSectionNameError",
    "InvalidSectionPrefixError",
    "PayloadTooLargeError",
    "RejectionError",
    "ReservedSectionNameError",
    "TooManySectionsError",
    "UnknownResultError",
    "UnknownRunError",
    "UnknownSectionError",
    "UnreadableSettingError",
    "UnsupportedMediaTypeError",
    "register_error_handlers",
    "safe_segment",
]
