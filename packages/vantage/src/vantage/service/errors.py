"""Single place that shapes every rejection body.

**Why one function, not ad hoc responses.** FastAPI's default handler for
`RequestValidationError` mirrors the client's own submitted value back in an
``"input"`` key, includes pydantic's internal error ``"type"`` string, and
can carry a ``"url"`` pointing at versioned pydantic documentation -- three
leaks in one body. Every rejection response in this module is instead built
from scratch, from a fixed set of fields: an error code, one human sentence,
and dotted field paths (`vantage.ingestion.errors` explains the allow-list
they go through). Nothing pydantic hands back is ever passed through.

The rejections a report itself can earn -- `RejectionError`,
`InvalidJsonError`, `InvalidReportError` -- are raised by
`vantage.ingestion`, which knows nothing of HTTP; the rest belong to the
routes and are declared here, on the same base.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from vantage.ingestion.errors import (
    InvalidJsonError,
    InvalidReportError,
    RejectionError,
    fields_from_errors,
    safe_segment,
)
from vantage.service.schemas import RejectionResponse

# The session report's body cap, enforced while streaming, before the body
# is fully buffered (`service/body.py`).
MAX_REPORT_BYTES = 1024 * 1024  # 1 MiB


def _rejection_body(error: str, detail: str, fields: list[str] | None = None) -> dict[str, object]:
    return RejectionResponse(error=error, detail=detail, fields=fields or []).model_dump()


class IncompleteBodyError(RejectionError):
    """The client disconnected before sending the whole body.

    Raised by `service/body.py`'s `read_bounded_body` when
    `request.stream()` raises `ClientDisconnect`. The client is gone and
    never sees this response; converting the disconnect keeps it on the one
    rejection path instead of surfacing as an unhandled ASGI error.
    """

    status_code = 400
    error = "incomplete_body"

    def __init__(self) -> None:
        super().__init__("The request body was truncated before it was fully received.")


class PayloadTooLargeError(RejectionError):
    """The body exceeds its route's cap: `MAX_REPORT_BYTES` for a session
    report."""

    status_code = 413
    error = "payload_too_large"

    def __init__(self, limit: int) -> None:
        super().__init__(f"The request body exceeds the {limit}-byte limit.")


class UnsupportedMediaTypeError(RejectionError):
    """Wrong or absent `Content-Type`, checked before the body is read.

    The header is client text, so it is named back only through
    `safe_segment`, one half of `type/subtype` at a time: `text/plain`
    survives for diagnosis, markup or padding does not."""

    status_code = 415
    error = "unsupported_media_type"

    def __init__(self, media_type: str) -> None:
        kind, slash, subtype = media_type.partition("/")
        if not media_type:
            # Not a name `safe_segment` passes, so no header value reads as it.
            shown = "<absent>"
        elif slash:
            shown = f"{safe_segment(kind)}/{safe_segment(subtype)}"
        else:
            shown = safe_segment(kind)
        super().__init__(f"Content-Type must be application/json, got {shown!r}.")


class InvalidSectionError(RejectionError):
    """Valid JSON, but not a `SectionUpsertRequest`: not an object, or a
    `name` or `prefix` missing or not a string."""

    status_code = 422
    error = "invalid_section"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidSectionError:
        return cls(
            "The submitted section does not match the expected shape.",
            fields_from_errors(errors),
        )


class InvalidParameterError(RejectionError):
    """A path or query parameter fails its declared type or bound, such as
    a `limit` below 1 or a `run_id` that is not 32 lowercase hex
    characters. `fields` names each as `query.<name>` or `path.<name>`."""

    status_code = 422
    error = "invalid_parameter"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidParameterError:
        return cls("A path or query parameter is not valid.", fields_from_errors(errors))


class InvalidIdentityError(RejectionError):
    """A required `node_id` query value is missing. There is no length
    bound: any node id already stored must stay readable by its exact value.

    Chosen by `_handle_request_validation_error` only when every failing
    field is `node_id`, so an unrelated failure (e.g. a malformed `limit`)
    is still `InvalidParameterError`."""

    status_code = 422
    error = "invalid_identity"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidIdentityError:
        return cls("The node_id query parameter is missing.", fields_from_errors(errors))


class InvalidMetadataFilterError(InvalidParameterError):
    """The `metadata_key` and `metadata_value` parameters of
    `GET /api/v1/runs` do not make a filter: they are repeated a different
    number of times, or give more pairs than the route takes.

    A pair is two parameters rather than one `key=value` string because a
    value may itself contain `=`; each value pairs with the key in the same
    position. It is an `invalid_parameter` like any other malformed query
    parameter, and `fields` names the parameters at fault."""

    @classmethod
    def unpaired(cls, short_field: str) -> InvalidMetadataFilterError:
        """`short_field`, one of the two, was given fewer times."""
        return cls(
            "metadata_key and metadata_value must be repeated the same number of times; "
            "each value pairs with the key in the same position.",
            [f"query.{short_field}"],
        )

    @classmethod
    def too_many(cls, limit: int) -> InvalidMetadataFilterError:
        return cls(
            f"At most {limit} metadata_key and metadata_value pairs may be given.",
            ["query.metadata_key", "query.metadata_value"],
        )


class InvalidCursorError(InvalidParameterError):
    """The `cursor` of `GET /api/v1/runs` or `GET /api/v1/tests/history` is
    not one the server handed out as `next_cursor`, or comes with an offset.
    A cursor already says where the page starts, so an offset past it would
    be a second answer to the same question. `fields` names the parameters
    at fault."""

    @classmethod
    def malformed(cls) -> InvalidCursorError:
        return cls(
            "cursor must be a next_cursor value a previous page returned.",
            ["query.cursor"],
        )

    @classmethod
    def with_offset(cls) -> InvalidCursorError:
        return cls(
            "cursor and a non-zero offset cannot be given together.",
            ["query.cursor", "query.offset"],
        )


class UnknownRunError(RejectionError):
    """No run matches the `run_id` in a heartbeat or read path.

    Accepting a heartbeat for an id the server never saw would either
    manufacture liveness for a run that does not exist or require inventing
    a row with no `started_at` -- neither is acceptable, so this is a
    rejection, not a silent accept.
    """

    status_code = 404
    error = "unknown_run"

    def __init__(self) -> None:
        super().__init__("No run with that identifier has been recorded.")


class RunOfAnotherUserError(RejectionError):
    """A report or heartbeat for a run another user's token created -- or
    one created without a token, sent with one, or the other way round.
    Nothing of it is stored. Answered whether or not the run is finished,
    so nobody learns more of another user's run than that it exists."""

    status_code = 409
    error = "foreign_run"

    def __init__(self) -> None:
        super().__init__("This run was recorded by another user.")


class ChallengeError(RejectionError):
    """A rejection that says how to authenticate, in a `WWW-Authenticate`
    header as RFC 6750 spells it for a bearer token. The header names the
    scope a request lacked, never the token it sent."""

    def __init__(self, detail: str, challenge: str) -> None:
        super().__init__(detail)
        self.headers = {"WWW-Authenticate": f'Bearer realm="vantage"{challenge}'}


class UnauthenticatedError(ChallengeError):
    """No token on a server that has users, or one that authenticates
    nothing: malformed, unknown, revoked, or its user's disabled. The last
    four read alike, so a caller cannot tell a revoked token from one that
    never existed."""

    status_code = 401
    error = "unauthenticated"

    @classmethod
    def missing(cls) -> UnauthenticatedError:
        return cls("This server requires a token: send Authorization: Bearer <token>.", "")

    @classmethod
    def invalid(cls) -> UnauthenticatedError:
        return cls(
            "The token is not valid: unknown, revoked, or its user is disabled.",
            ', error="invalid_token"',
        )


class InsufficientScopeError(ChallengeError):
    """A token that authenticates, but whose grant does not cover the
    route's scope: it does not hold it, or it is the admin scope and its
    user is no longer an admin."""

    status_code = 403
    error = "insufficient_scope"

    def __init__(self, scope: str) -> None:
        super().__init__(
            f"The token does not grant the {scope} scope.",
            f', error="insufficient_scope", scope="{scope}"',
        )


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
    """A section name is empty after `strip()`, exceeds
    `SECTION_NAME_MAX_CHARS`, or holds text that is not valid Unicode -- a
    lone surrogate, which JSON can escape but UTF-8 cannot encode.

    The message and `fields` are fixed strings; the submitted name is never
    interpolated, so a hostile name (a CR/LF, a `</script>`) cannot ride
    along in the rejection body."""

    status_code = 422
    error = "invalid_section_name"

    def __init__(self) -> None:
        super().__init__(
            "The section name is empty after stripping whitespace, exceeds the maximum length, "
            "or is not valid Unicode.",
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
    """A section prefix is empty after `strip()`, exceeds
    `SECTION_PREFIX_MAX_CHARS` once normalized, or holds text that is not
    valid Unicode. Same no-echo discipline as `InvalidSectionNameError`."""

    status_code = 422
    error = "invalid_section_prefix"

    def __init__(self) -> None:
        super().__init__(
            "The section prefix is empty after stripping whitespace, exceeds the maximum length, "
            "or is not valid Unicode.",
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
        headers=exc.headers if isinstance(exc, ChallengeError) else None,
    )


_ROUTING_REJECTIONS: dict[int, tuple[str, str]] = {
    404: ("not_found", "No route matches that path."),
    405: ("method_not_allowed", "That method is not allowed on this path."),
}
_OTHER_HTTP_REJECTION = ("http_error", "The request could not be served.")


def register_error_handlers(app: FastAPI) -> None:
    """Wire every rejection this service can raise through the one shape.

    Three sources, one output. `RejectionError` is raised by the manual body
    handling in `service/body.py` (media type, size cap), by
    `vantage.ingestion` (JSON parse, report validation), by the section
    upsert, which validates the body it reads, and by the other routes.
    `RequestValidationError` is FastAPI's own exception, raised for a path
    or query parameter that fails automatic binding -- no route binds its
    body that way. Neither handler ever forwards a pydantic error dict
    as-is. Starlette's
    `HTTPException` is what the router raises itself for a path nothing
    serves (404) or a method the path does not take (405); its `detail` is
    replaced from a fixed table, and its headers, which carry a 405's
    `Allow`, are kept. `RejectionError` is not a subclass of it, so a
    route's own 404, such as `unknown_run`, keeps its code.

    A `RequestValidationError` confined to the `node_id` query parameter
    (`/tests/history`, `/runs/{run_id}/result`) is shaped as
    `InvalidIdentityError`; every other one as `InvalidParameterError`, so
    a client that sent no report is never told its report is malformed.
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
        return _rejection_response(InvalidParameterError.from_errors(errors))

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
        del request
        error, detail = _ROUTING_REJECTIONS.get(exc.status_code, _OTHER_HTTP_REJECTION)
        return JSONResponse(
            status_code=exc.status_code,
            content=_rejection_body(error, detail),
            headers=exc.headers,
        )


__all__ = [
    "MAX_REPORT_BYTES",
    "ChallengeError",
    "IncompleteBodyError",
    "InsufficientScopeError",
    "InvalidIdentityError",
    "InvalidJsonError",
    "InvalidMetadataFilterError",
    "InvalidParameterError",
    "InvalidReportError",
    "InvalidSectionError",
    "InvalidSectionNameError",
    "InvalidSectionPrefixError",
    "PayloadTooLargeError",
    "RejectionError",
    "ReservedSectionNameError",
    "RunOfAnotherUserError",
    "TooManySectionsError",
    "UnauthenticatedError",
    "UnknownResultError",
    "UnknownRunError",
    "UnknownSectionError",
    "UnreadableSettingError",
    "UnsupportedMediaTypeError",
    "register_error_handlers",
    "safe_segment",
]
