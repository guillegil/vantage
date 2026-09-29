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

from vantage.core.domain.passwords import PASSWORD_MAX_CHARS, PASSWORD_MIN_CHARS
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
    """The client disconnected before sending the whole body -- or, on a
    password route, while it waited for a slot, before any hash was made.

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
    `GET /api/v1/projects/{project}/runs` do not make a filter: they are repeated a different
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
    """The `cursor` of a project's run list or of a test's history in it is
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


class InvalidOutcomeFilterError(InvalidParameterError):
    """An `outcome` of `GET /api/v1/runs/{run_id}/results` is not one of
    the six outcomes a result can have. The sentence names the six, never
    the word sent."""

    def __init__(self) -> None:
        super().__init__(
            "outcome must be one of passed, failed, error, skipped, xfailed and xpassed.",
            ["query.outcome"],
        )


class InvalidChangeFilterError(InvalidParameterError):
    """A `change` of `GET /api/v1/runs/{run_id}/changes` is not one of the
    six changes a comparison records. The sentence names the six, never
    the word sent."""

    def __init__(self) -> None:
        super().__init__(
            "change must be one of new_failure, still_failing, fixed, new_test, removed"
            " and not_reached.",
            ["query.change"],
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


class NoSuchProjectError(RejectionError):
    """No project has that name, or none can. For a report, `fields` names
    its `project`; the name itself is never repeated."""

    status_code = 404
    error = "unknown_project"

    def __init__(self, fields: list[str] | None = None) -> None:
        super().__init__("No project with that name exists.", fields)


class RunOfAnotherProjectError(RejectionError):
    """A report of a run that was created in another project. A run never
    moves, so nothing of it is stored."""

    status_code = 409
    error = "project_mismatch"

    def __init__(self) -> None:
        super().__init__("This run was recorded in another project.", ["project"])


class ProjectNameTakenError(RejectionError):
    """`POST /projects` for a name another project has, `default` included."""

    status_code = 409
    error = "project_exists"

    def __init__(self) -> None:
        super().__init__("A project with that name exists already.", ["name"])


class ProjectNameRefusedError(RejectionError):
    """A name `POST /projects` cannot create. The name is not repeated."""

    status_code = 422
    error = "invalid_project_name"

    def __init__(self) -> None:
        super().__init__(
            "A project name is 1 to 64 characters of a-z, 0-9, '.', '_' and '-', starting with "
            "a letter or a digit.",
            ["name"],
        )


class InvalidProjectRequestError(RejectionError):
    """A `POST /projects` body of the wrong shape."""

    status_code = 422
    error = "invalid_project_request"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidProjectRequestError:
        return cls(
            "The submitted request does not match the expected shape.",
            fields_from_errors(errors),
        )


class ChallengeError(RejectionError):
    """A rejection that says how to authenticate, in a `WWW-Authenticate`
    header as RFC 6750 spells it for a bearer token. The header names the
    scope a request lacked, never the token it sent."""

    def __init__(self, detail: str, challenge: str) -> None:
        super().__init__(detail)
        self.headers = {"WWW-Authenticate": f'Bearer realm="vantage"{challenge}'}


class UnauthenticatedError(ChallengeError):
    """No token on a server that has users, or one that authenticates
    nothing: malformed, unknown, revoked, expired, or its user's disabled.
    All but the first read alike, so a caller cannot tell a revoked token
    from one that never existed."""

    status_code = 401
    error = "unauthenticated"

    @classmethod
    def missing(cls) -> UnauthenticatedError:
        return cls("This server requires a token: send Authorization: Bearer <token>.", "")

    @classmethod
    def invalid(cls) -> UnauthenticatedError:
        return cls(
            "The token is not valid: unknown, revoked, expired, or its user is disabled.",
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


class CrossSiteRequestError(RejectionError):
    """A browser session's cookie on a request the browser did not mark
    `Sec-Fetch-Site: same-origin` -- or, for a read, marked as coming from
    another site or another port of this one. SameSite keeps the cookie
    from other sites, not from other ports of the same host, so without
    this a page served beside vantage could act with its session. A 403
    without a challenge, since no token of the same user would help; the
    header's value is never repeated."""

    status_code = 403
    error = "cross_site_request"

    def __init__(self) -> None:
        super().__init__(
            "A browser session is accepted only from vantage's own pages: the browser must mark "
            "the request Sec-Fetch-Site: same-origin, which browsers do over HTTPS and on this "
            "machine's loopback address."
        )


class OpenServerError(RejectionError):
    """A users, tokens, login, password or members route asked of a
    database with no user, which only a database pytest-vantage's local
    store made can be while served. Nobody can log in or act as an admin
    there, a project has no member to list or change until users exist, and
    the one thing an anonymous caller could do -- make the first user --
    only the `vantage` command does. A 409 without a challenge, since no
    token would help."""

    status_code = 409
    error = "open_server"

    def __init__(self) -> None:
        super().__init__(
            "This server's database was made by pytest-vantage's local store and has no user, "
            "so it serves every request without a token and nobody logs in or manages users "
            "or members here: add the first admin with vantage user add NAME --admin on its "
            "database."
        )


class NotAMemberError(RejectionError):
    """An authenticated caller with no role in the project a path, a run or
    a report names. A 403 without a challenge, since no other token of the
    same user would help; the body names neither the project nor a member."""

    status_code = 403
    error = "not_a_member"

    def __init__(self) -> None:
        super().__init__("You are not a member of this project.")


class InsufficientRoleError(RejectionError):
    """A member whose role in the project is below the one the request
    needs. Like `NotAMemberError`, a 403 without a challenge."""

    status_code = 403
    error = "insufficient_role"

    def __init__(self, role: str) -> None:
        super().__init__(f"This needs the {role} role in this project, and yours is below it.")


class PasswordChecksBusyError(RejectionError):
    """As many requests hold or wait for a password slot as the server lets
    them (`service/slots.py`): refused at once rather than queued, with a
    `Retry-After`, so a flood of logins can queue neither memory nor minutes
    of hashing."""

    status_code = 503
    error = "password_checks_busy"
    headers = {"Retry-After": "1"}

    def __init__(self) -> None:
        super().__init__(
            "The server is checking as many passwords as it will at once; try again shortly."
        )


class InvalidCredentialsError(ChallengeError):
    """A login, or a password change, whose name and password do not match
    an enabled user's: an unknown name, one nobody can have, a user with no
    password or a disabled one, a wrong password, or a password changed
    while it was being checked. Every case reads alike and costs the same
    work, so a caller learns nothing of which names exist."""

    status_code = 401
    error = "invalid_credentials"

    def __init__(self) -> None:
        super().__init__("The name or password is not valid.", "")


class InvalidLoginRequestError(RejectionError):
    """A `POST /login` body that is not exactly a name and a password, both
    strings. The fields are named, their values never repeated."""

    status_code = 422
    error = "invalid_login_request"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidLoginRequestError:
        return cls(
            "The submitted request does not match the expected shape.",
            fields_from_errors(errors),
        )


class InvalidPasswordRequestError(RejectionError):
    """A `POST /password` or `PUT /users/{name}/password` body of the wrong
    shape. The fields are named, their values never repeated."""

    status_code = 422
    error = "invalid_password_request"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidPasswordRequestError:
        return cls(
            "The submitted request does not match the expected shape.",
            fields_from_errors(errors),
        )


class PasswordRefusedError(RejectionError):
    """A password that cannot be set (`check_password`). The password is not
    repeated."""

    status_code = 422
    error = "invalid_password"

    def __init__(self, fields: list[str]) -> None:
        super().__init__(
            f"A password is {PASSWORD_MIN_CHARS} to {PASSWORD_MAX_CHARS} characters, with no "
            "control characters.",
            fields,
        )


class UserNameTakenError(RejectionError):
    """`POST /users` for a name another user has. The name is not repeated."""

    status_code = 409
    error = "user_exists"

    def __init__(self) -> None:
        super().__init__("A user with that name exists already.", ["name"])


class NoSuchUserError(RejectionError):
    """No user has that name, or the name is one nobody can have."""

    status_code = 404
    error = "unknown_user"

    def __init__(self, fields: list[str] | None = None) -> None:
        super().__init__("No user with that name exists.", fields)


class OwnAccountError(RejectionError):
    """An admin demoting or disabling their own user over HTTP. Decided by
    who asks alone, so it races nothing; the command line stays the way to
    do it, and to recover when every admin is gone."""

    status_code = 409
    error = "own_account"

    def __init__(self) -> None:
        super().__init__(
            "An admin cannot demote or disable their own user here: another admin can, or "
            "vantage user update on the server's database."
        )


class NotAnAdminError(RejectionError):
    """The admin scope asked for a token of a user who is not an admin."""

    status_code = 409
    error = "not_an_admin"

    def __init__(self) -> None:
        super().__init__(
            "The user is not an admin, so the admin scope would grant nothing.", ["scopes"]
        )


class DefaultProjectMembersError(RejectionError):
    """A member set or removed in `default`, which takes none: every user
    is an editor of it without a row."""

    status_code = 409
    error = "default_project"

    def __init__(self) -> None:
        super().__init__(
            "Every user is an editor of default, which has no members to set or remove."
        )


class NoSuchMemberError(RejectionError):
    """`DELETE /projects/{project}/members/{user}` for a user who is not a
    member of the project -- whether or not the user exists -- or a name
    nobody can have."""

    status_code = 404
    error = "unknown_member"

    def __init__(self) -> None:
        super().__init__("That user is not a member of this project.")


class RoleRefusedError(RejectionError):
    """A role that is not one (`check_role`). The value is not repeated."""

    status_code = 422
    error = "invalid_role"

    def __init__(self) -> None:
        super().__init__("A role is viewer, editor or owner.", ["role"])


class InvalidMemberRequestError(RejectionError):
    """A `PUT /projects/{project}/members/{user}` body of the wrong shape.
    The fields are named, their values never repeated."""

    status_code = 422
    error = "invalid_member_request"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidMemberRequestError:
        return cls(
            "The submitted request does not match the expected shape.",
            fields_from_errors(errors),
        )


class UnknownTokenError(RejectionError):
    """No token has that id."""

    status_code = 404
    error = "unknown_token"

    def __init__(self) -> None:
        super().__init__("No token with that id exists.")


class InvalidUserRequestError(RejectionError):
    """A `POST /users` or `PATCH /users/{name}` body of the wrong shape, or
    a `PATCH` that changes nothing."""

    status_code = 422
    error = "invalid_user_request"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidUserRequestError:
        return cls(
            "The submitted request does not match the expected shape.",
            fields_from_errors(errors),
        )

    @classmethod
    def nothing_to_change(cls) -> InvalidUserRequestError:
        return cls("Say what to change: admin, disabled, or both.", ["admin", "disabled"])


class UserNameRefusedError(RejectionError):
    """A name `POST /users` cannot create. The name is not repeated."""

    status_code = 422
    error = "invalid_user_name"

    def __init__(self) -> None:
        super().__init__(
            "A user name is 1 to 64 characters of a-z, 0-9, '.', '_' and '-', starting with a "
            "letter or a digit.",
            ["name"],
        )


class InvalidTokenRequestError(RejectionError):
    """A `POST /tokens` body of the wrong shape. Not `invalid_token`, which
    RFC 6750 gives a token that does not authenticate."""

    status_code = 422
    error = "invalid_token_request"

    @classmethod
    def from_errors(cls, errors: Iterable[Mapping[str, Any]]) -> InvalidTokenRequestError:
        return cls(
            "The submitted request does not match the expected shape.",
            fields_from_errors(errors),
        )


class ScopesRefusedError(RejectionError):
    """No scope, or one that is not a scope."""

    status_code = 422
    error = "invalid_scopes"

    def __init__(self) -> None:
        super().__init__("A token holds one or more of admin, manage, read and record.", ["scopes"])


class TokenLabelRefusedError(RejectionError):
    """A label too long, or holding a control character or a lone
    surrogate. The label is not repeated."""

    status_code = 422
    error = "invalid_token_label"

    def __init__(self) -> None:
        super().__init__(
            "A token label is at most 200 characters, with no control characters.", ["label"]
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
    """`DELETE /api/v1/projects/{project}/config/sections` for a name the
    project does not store."""

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
        headers=exc.headers if isinstance(exc, (ChallengeError, PasswordChecksBusyError)) else None,
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
    upsert and the users and tokens routes, which validate the bodies they
    read, and by the other routes.
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
    (`/projects/{project}/tests/history`, `/runs/{run_id}/result`) is shaped as
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
    "CrossSiteRequestError",
    "DefaultProjectMembersError",
    "IncompleteBodyError",
    "InsufficientRoleError",
    "InsufficientScopeError",
    "InvalidChangeFilterError",
    "InvalidCredentialsError",
    "InvalidIdentityError",
    "InvalidJsonError",
    "InvalidLoginRequestError",
    "InvalidMemberRequestError",
    "InvalidMetadataFilterError",
    "InvalidOutcomeFilterError",
    "InvalidParameterError",
    "InvalidPasswordRequestError",
    "InvalidProjectRequestError",
    "InvalidReportError",
    "InvalidSectionError",
    "InvalidSectionNameError",
    "InvalidSectionPrefixError",
    "InvalidTokenRequestError",
    "InvalidUserRequestError",
    "NoSuchMemberError",
    "NoSuchProjectError",
    "NoSuchUserError",
    "NotAMemberError",
    "NotAnAdminError",
    "OpenServerError",
    "OwnAccountError",
    "PasswordChecksBusyError",
    "PasswordRefusedError",
    "PayloadTooLargeError",
    "ProjectNameRefusedError",
    "ProjectNameTakenError",
    "RejectionError",
    "ReservedSectionNameError",
    "RoleRefusedError",
    "RunOfAnotherProjectError",
    "RunOfAnotherUserError",
    "ScopesRefusedError",
    "TokenLabelRefusedError",
    "TooManySectionsError",
    "UnauthenticatedError",
    "UnknownResultError",
    "UnknownRunError",
    "UnknownSectionError",
    "UnknownTokenError",
    "UnreadableSettingError",
    "UnsupportedMediaTypeError",
    "UserNameRefusedError",
    "UserNameTakenError",
    "register_error_handlers",
    "safe_segment",
]
