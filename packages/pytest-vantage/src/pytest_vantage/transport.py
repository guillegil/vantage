"""The plugin's HTTP client: the session reports, heartbeats and the
capability probe, over ``urllib``.

Every request goes through `_OPENER`, which carries only the plain HTTP and
HTTPS handlers:

- No redirect handling: any 3xx is a failed request. urllib would re-issue a
  redirected POST as a bodiless GET, which another endpoint may answer 2xx,
  and would follow a ``Location`` to any scheme it knows, ``ftp`` included,
  past the http/https check the address has already passed.
- No proxy handling: the preflight in ``plugin.py`` connects directly, so the
  requests take that same route. Honouring ``http_proxy`` would send every
  report, failure text included, to a host the preflight never checked.

``timeout`` is a deadline on the whole exchange, not on each socket
operation: a peer that trickles its answer a byte at a time would reset a
per-operation timeout on every byte. `run_within` waits at most ``timeout``
for the exchange; the socket timeout still frees its thread once the peer goes
silent.

The response side is bounded and defensive because the server is not trusted
to behave: ``resp.read(MAX_RESPONSE_BYTES)`` never buffers more than 64 KiB,
and anything but an acknowledgement of the report just sent raises like any
other bad input. ``send`` and ``send_heartbeat`` are "send, or raise" --
nothing here decides whether a failure is fatal; ``boundary.py`` turns any
exception into a warning.

A server with users needs a token, which ``send`` and ``send_heartbeat``
carry as ``Authorization: Bearer`` when given one; the capability probe
never does, since the server answers it without one. A token only ever
travels to the address it was configured with, because no redirect is
followed. When a vantage server refuses who sent a request -- no token or
one it does not accept (401), one without the record scope (403), or a run
another user recorded (409) -- the error its body names is turned into an
`AccessRefusedError` saying what to fix, still an `HTTPError` with the same
status. The token never appears in it.

A report names its project, and a server without it answers
`404 unknown_project`: `send` turns that into a `ProjectRefusedError`,
still an `HTTPError` 404, naming the project and how an admin adds it. Any
other 404 -- a wrong base path's `not_found` -- stays a plain one. A server
that has the project but does not let the token's user record there answers
`403 not_a_member` or `403 insufficient_role`: `send` turns that into a
`ProjectRefusedError` too, still a 403, naming the project and how its user
is made an editor of it, and `send_heartbeat`, when it carried a token, into an
`AccessRefusedError`.
Any other 403 -- a proxy's page -- stays a plain one.
"""

from __future__ import annotations

import http.client
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar
from urllib import error as urllib_error
from urllib import request as urllib_request

_INGESTION_PATH = "/api/v1/runs"
_HEARTBEAT_PATH_SUFFIX = "/heartbeat"
_CAPABILITIES_PATH = "/api/v1/capabilities"

# The acknowledgement body is a few dozen bytes
# (`{"run_id": ..., "status": ..., "ignored": []}`); 64 KiB is comfortably
# larger than that while still refusing to buffer an unbounded response.
MAX_RESPONSE_BYTES = 64 * 1024

# The two statuses the ingestion route acknowledges a report with: the first
# report of a run, and any later one. `test_server_contract.py` pins them to
# the statuses the server answers with.
_ACKNOWLEDGED_STATUSES = frozenset({"created", "duplicate"})

_T = TypeVar("_T")

# What a vantage server's rejection body names, for the refusals of who
# sent a request, and what each means to fix from where the plugin stands.
_UNAUTHENTICATED = "unauthenticated"
_INSUFFICIENT_SCOPE = "insufficient_scope"
_FOREIGN_RUN = "foreign_run"
_ACCESS_REFUSALS = {401: _UNAUTHENTICATED, 403: _INSUFFICIENT_SCOPE, 409: _FOREIGN_RUN}

# A server's refusal of a report for a project it does not have.
# `test_server_contract.py` pins it to the server's `NoSuchProjectError`.
_UNKNOWN_PROJECT = (404, "unknown_project")

# What a vantage server's 403 names when it has the project but the token's
# user may not record there: no role in it, or one below editor.
# `test_server_contract.py` pins them to the server's rejections.
_MEMBERSHIP_REFUSAL_STATUS = 403
_NOT_A_MEMBER = "not_a_member"
_INSUFFICIENT_ROLE = "insufficient_role"
_MEMBERSHIP_REFUSALS = frozenset({_NOT_A_MEMBER, _INSUFFICIENT_ROLE})

# The statuses whose rejection body the plugin reads, for the error it names.
_EXPLAINED_STATUSES = frozenset(
    {*_ACCESS_REFUSALS, _UNKNOWN_PROJECT[0], _MEMBERSHIP_REFUSAL_STATUS}
)

# The shape of a token the server could accept: printable ASCII with no
# space, at most 512 characters. `test_server_contract.py` checks it agrees
# with the server's `well_formed_token`.
_TOKEN_RE = re.compile(r"\A[\x21-\x7e]{1,512}\Z")


def well_formed_token(text: str) -> bool:
    """Whether `text` could be a token: anything else would either never
    authenticate or not fit in a header at all."""
    return _TOKEN_RE.match(text) is not None


class ProjectRefusedError(urllib_error.HTTPError):
    """A vantage server refused a report for its project: it does not have
    it (`unknown_project`), or the token's user may not record there
    (`not_a_member`, `insufficient_role`), as `error` says. Either is a run
    an admin, or an owner of the project, can let in, so it is worth
    keeping. Its status and headers are the refusal's."""

    def __init__(
        self, refused: urllib_error.HTTPError, project: str, reason: str, *, error: str
    ) -> None:
        super().__init__(refused.url, refused.code, refused.msg, refused.headers, None)
        self.project = project
        self.reason_text = reason
        self.error = error

    def __str__(self) -> str:
        return f"HTTP {self.code}: {self.reason_text}"


class AccessRefusedError(urllib_error.HTTPError):
    """A vantage server refused who sent the request, with the reason in
    words: its status and headers are the refusal's, so whatever reads the
    status of an `HTTPError` reads this one the same."""

    def __init__(self, refused: urllib_error.HTTPError, reason: str) -> None:
        super().__init__(refused.url, refused.code, refused.msg, refused.headers, None)
        self.reason_text = reason

    def __str__(self) -> str:
        return f"HTTP {self.code}: {self.reason_text}"


def _refusal_reason(code: int, error: object, *, token: str | None) -> str | None:
    """What a refusal of who sent a request means, or `None` when `error`
    is not the one a vantage server gives with `code`."""
    if code not in _ACCESS_REFUSALS or _ACCESS_REFUSALS[code] != error:
        return None
    if error == _UNAUTHENTICATED:
        if token is None:
            return "the server requires a token: set VANTAGE_TOKEN to one with the record scope"
        return (
            "the server does not accept the token in VANTAGE_TOKEN: it is unknown, revoked or "
            "expired, or its user is disabled"
        )
    if error == _INSUFFICIENT_SCOPE:
        return "the token in VANTAGE_TOKEN does not grant the record scope"
    return "the run was recorded by another user"


def _project_refusal_reason(error: str, project: str, address: str | None) -> str:
    """What a refusal of a report for its project means, and who fixes it
    how. Never the server's own message, which the plugin does not trust."""
    if error == _NOT_A_MEMBER:
        return (
            f"{address} does not let the user of the token in VANTAGE_TOKEN record in project "
            f"{project}, which they are not a member of; an owner of it or an admin adds them "
            f"with: vantage project member set {project} USER editor"
        )
    if error == _INSUFFICIENT_ROLE:
        return (
            f"{address} lets the user of the token in VANTAGE_TOKEN only read project "
            f"{project}; recording needs the editor role, which an owner of it or an admin "
            f"gives with: vantage project member set {project} USER editor"
        )
    return (
        f"{address} has no project {project}; an admin adds it with: vantage project add {project}"
    )


def _project_refusal(code: int, error: object) -> str | None:
    """The error a vantage server's refusal of a report for its project
    names, or `None` when `code` and `error` are not one."""
    if not isinstance(error, str):
        return None
    if code == _MEMBERSHIP_REFUSAL_STATUS and error in _MEMBERSHIP_REFUSALS:
        return error
    if (code, error) == _UNKNOWN_PROJECT:
        return error
    return None


def _build_opener() -> urllib_request.OpenerDirector:
    opener = urllib_request.OpenerDirector()
    handlers: list[urllib_request.BaseHandler] = [
        urllib_request.HTTPHandler(),
        urllib_request.HTTPDefaultErrorHandler(),
        urllib_request.HTTPErrorProcessor(),
        urllib_request.UnknownHandler(),
    ]
    # Absent when Python was built without `ssl`; an https address then
    # fails as an unknown URL type instead of breaking this import.
    if hasattr(urllib_request, "HTTPSHandler"):
        handlers.append(urllib_request.HTTPSHandler())
    for handler in handlers:
        opener.add_handler(handler)
    return opener


_OPENER = _build_opener()


def run_within(timeout: float, work: Callable[[], _T]) -> _T:
    """Run ``work`` on a daemon thread and wait at most ``timeout`` seconds
    for it, returning its result or re-raising its exception.

    A thread still running at the deadline is abandoned, not joined: it is a
    daemon, so it never delays the interpreter's exit, and its own socket
    timeout ends it once the peer stops sending.
    """
    outcome: list[_T] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            outcome.append(work())
        except BaseException as exc:  # handed to the caller, which re-raises it
            failure.append(exc)

    worker = threading.Thread(target=run, name="vantage-request", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"no complete answer within {timeout:g}s")
    if failure:
        raise failure[0]
    return outcome[0]


def _exchange(
    http_request: urllib_request.Request,
    timeout: float,
    *,
    token: str | None = None,
    project: str | None = None,
    address: str | None = None,
) -> bytes:
    """Open ``http_request`` and read at most `MAX_RESPONSE_BYTES` of the
    answer, all within ``timeout`` seconds. A non-2xx status, 3xx included,
    raises `urllib.error.HTTPError`, as an `AccessRefusedError` when a
    vantage server refused who sent it; ``token`` is what was sent, if
    anything, which the reason depends on. With ``project``, the report's,
    a server's `404 unknown_project`, `403 not_a_member` or
    `403 insufficient_role` raises `ProjectRefusedError` naming it and
    ``address``; without one, as for a heartbeat, either 403 raises
    `AccessRefusedError`.
    """

    def attempt() -> bytes:
        try:
            response = _OPENER.open(http_request, timeout=timeout)
        except urllib_error.HTTPError as exc:
            # The error carries the still-open response. Only a refusal a
            # vantage server explains needs its body, for the error it
            # names; the connection is released before the error is handed on.
            try:
                error = _rejection_error(exc) if exc.code in _EXPLAINED_STATUSES else None
            finally:
                exc.close()
            reason = _refusal_reason(exc.code, error, token=token)
            if reason is not None:
                raise AccessRefusedError(exc, reason) from None
            refused = _project_refusal(exc.code, error)
            if refused is not None and project is not None:
                raise ProjectRefusedError(
                    exc, project, _project_refusal_reason(refused, project, address), error=refused
                ) from None
            # Without a project the request is a heartbeat, for a run the
            # server already files in one. Only a caller with a token is
            # refused for its role, so the capability probe, which carries
            # none, never reads as one.
            if refused in _MEMBERSHIP_REFUSALS and token is not None:
                raise AccessRefusedError(
                    exc,
                    "the user of the token in VANTAGE_TOKEN may no longer record in this "
                    "run's project",
                ) from None
            raise
        with response:
            body: bytes = response.read(MAX_RESPONSE_BYTES)
        return body

    return run_within(timeout, attempt)


def _rejection_error(refused: urllib_error.HTTPError) -> object:
    """The `error` a rejection body names, or `None` for a body that is not
    a JSON object: a proxy's page, or nothing at all."""
    try:
        body = json.loads(refused.read(MAX_RESPONSE_BYTES))
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return body.get("error") if isinstance(body, dict) else None


def _headers(token: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def send(
    address: str, report: dict[str, object], *, timeout: float, token: str | None = None
) -> None:
    """POST ``report`` as JSON to ``{address}/api/v1/runs``.

    ``address`` has already passed ``config.resolve_and_validate_address``
    by the time this is called (only ``http``/``https`` ever reach here), so
    the scheme audit ``Request`` would otherwise flag is a false positive.

    Raises on any transport failure -- connection refused, timeout, DNS
    failure, a non-2xx status via ``urllib.error.HTTPError`` -- and on any
    answer that does not acknowledge this report's run: a body that is not
    JSON, or JSON that is not ``{"run_id": <this run>, "status": ...}``.
    This function catches nothing itself. ``token``, when given, goes in
    the ``Authorization`` header.
    """
    url = address.rstrip("/") + _INGESTION_PATH
    body = json.dumps(report).encode("utf-8")
    http_request = urllib_request.Request(  # noqa: S310
        url,
        data=body,
        headers=_headers(token),
        method="POST",
    )
    project = report.get("project")
    # A server answers `unknown_project` only for a name its own rule
    # accepts -- anything else is a 422 -- so the name is safe to show.
    named = project if isinstance(project, str) else "default"
    acknowledgement = json.loads(
        _exchange(http_request, timeout, token=token, project=named, address=address)
    )
    run = report.get("run")
    run_id = run.get("id") if isinstance(run, dict) else None
    if not (
        isinstance(acknowledgement, dict)
        and acknowledgement.get("run_id") == run_id
        and acknowledgement.get("status") in _ACKNOWLEDGED_STATUSES
    ):
        raise ValueError(f"{url} answered without acknowledging run {run_id}")


def send_heartbeat(address: str, run_id: str, *, timeout: float, token: str | None = None) -> None:
    """POST an empty liveness beat to ``{address}/api/v1/runs/{run_id}/heartbeat``.

    Separate from `send` because the two differ in more than their path: the
    body is a meaningless `{}`, the response is not parsed, and the caller
    bounds it by the liveness timeout rather than the report timeout.

    Raises on any transport failure, exactly like `send`, and carries
    ``token`` as `send` does.
    """
    url = address.rstrip("/") + _INGESTION_PATH + f"/{run_id}" + _HEARTBEAT_PATH_SUFFIX
    http_request = urllib_request.Request(  # noqa: S310
        url,
        data=b"{}",
        headers=_headers(token),
        method="POST",
    )
    _exchange(http_request, timeout, token=token)


@dataclass(frozen=True)
class Capabilities:
    """What the capability probe learned.

    Truthy exactly when the server advertises the session lifecycle.
    Otherwise ``problem`` says why not, because the user's next step
    differs: a server that answered no needs upgrading, while a probe that
    got no usable answer points at the address, the network or the server's
    health.
    """

    session_lifecycle: bool
    problem: str | None = None

    def __bool__(self) -> bool:
        return self.session_lifecycle


def fetch_capabilities(address: str, *, timeout: float) -> Capabilities:
    """GET ``{address}/api/v1/capabilities`` and report whether the server
    advertises the ``session_lifecycle`` capability.

    The lifecycle is on for exactly one shape: a 2xx response whose JSON
    body is a mapping carrying ``"session_lifecycle": true``. Everything
    else turns it off, with a ``problem`` that tells two cases apart:

    - a negative answer: the ``404`` of a server without the route, or a
      mapping whose ``session_lifecycle`` is missing or anything but the
      JSON boolean ``true``;
    - a failed probe: no connection, a timeout, any other non-2xx status
      (a 3xx included), a body that is not JSON, or JSON that is not a
      mapping.

    Both name the probed URL, so an address carrying a path, which doubles
    into ``.../api/v1/api/v1/capabilities``, is visible in the warning.

    Unlike `send` and `send_heartbeat`, this never raises: it fails closed
    itself, so no overlooked exception path in a caller can accidentally
    turn the lifecycle on.
    """
    url = address.rstrip("/") + _CAPABILITIES_PATH
    not_advertised = f"{address} does not advertise the session lifecycle (GET {url}"
    try:
        http_request = urllib_request.Request(url, method="GET")  # noqa: S310
        payload = json.loads(_exchange(http_request, timeout))
    except urllib_error.HTTPError as exc:
        if exc.code == 404:
            return Capabilities(False, f"{not_advertised} answered 404)")
        return Capabilities(False, f"the capability probe to {url} failed ({exc})")
    except Exception as exc:
        return Capabilities(False, f"the capability probe to {url} failed ({exc})")
    if not isinstance(payload, dict):
        return Capabilities(
            False, f"the capability probe to {url} failed (the answer is not a JSON object)"
        )
    if payload.get("session_lifecycle") is not True:
        return Capabilities(False, f"{not_advertised} answered without it)")
    return Capabilities(True)


__all__ = [
    "MAX_RESPONSE_BYTES",
    "AccessRefusedError",
    "Capabilities",
    "ProjectRefusedError",
    "fetch_capabilities",
    "run_within",
    "send",
    "send_heartbeat",
    "well_formed_token",
]
