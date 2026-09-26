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
per-operation timeout on every byte. `_within` waits at most ``timeout`` for
the exchange; the socket timeout still frees its thread once the peer goes
silent.

The response side is bounded and defensive because the server is not trusted
to behave: ``resp.read(MAX_RESPONSE_BYTES)`` never buffers more than 64 KiB,
and anything but an acknowledgement of the report just sent raises like any
other bad input. ``send`` and ``send_heartbeat`` are "send, or raise" --
nothing here decides whether a failure is fatal; ``boundary.py`` turns any
exception into a warning.
"""

from __future__ import annotations

import json
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


def _within(timeout: float, work: Callable[[], _T]) -> _T:
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


def _exchange(http_request: urllib_request.Request, timeout: float) -> bytes:
    """Open ``http_request`` and read at most `MAX_RESPONSE_BYTES` of the
    answer, all within ``timeout`` seconds. A non-2xx status, 3xx included,
    raises `urllib.error.HTTPError`.
    """

    def attempt() -> bytes:
        try:
            response = _OPENER.open(http_request, timeout=timeout)
        except urllib_error.HTTPError as exc:
            # The error carries the still-open response; only its status is
            # needed, so release the connection before handing it on.
            exc.close()
            raise
        with response:
            body: bytes = response.read(MAX_RESPONSE_BYTES)
        return body

    return _within(timeout, attempt)


def send(address: str, report: dict[str, object], *, timeout: float) -> None:
    """POST ``report`` as JSON to ``{address}/api/v1/runs``.

    ``address`` has already passed ``config.resolve_and_validate_address``
    by the time this is called (only ``http``/``https`` ever reach here), so
    the scheme audit ``Request`` would otherwise flag is a false positive.

    Raises on any transport failure -- connection refused, timeout, DNS
    failure, a non-2xx status via ``urllib.error.HTTPError`` -- and on any
    answer that does not acknowledge this report's run: a body that is not
    JSON, or JSON that is not ``{"run_id": <this run>, "status": ...}``.
    This function catches nothing itself.
    """
    url = address.rstrip("/") + _INGESTION_PATH
    body = json.dumps(report).encode("utf-8")
    http_request = urllib_request.Request(  # noqa: S310
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    acknowledgement = json.loads(_exchange(http_request, timeout))
    run = report.get("run")
    run_id = run.get("id") if isinstance(run, dict) else None
    if not (
        isinstance(acknowledgement, dict)
        and acknowledgement.get("run_id") == run_id
        and acknowledgement.get("status") in _ACKNOWLEDGED_STATUSES
    ):
        raise ValueError(f"{url} answered without acknowledging run {run_id}")


def send_heartbeat(address: str, run_id: str, *, timeout: float) -> None:
    """POST an empty liveness beat to ``{address}/api/v1/runs/{run_id}/heartbeat``.

    Separate from `send` because the two differ in more than their path: the
    body is a meaningless `{}`, the response is not parsed, and the caller
    bounds it by the liveness timeout rather than the report timeout.

    Raises on any transport failure, exactly like `send`.
    """
    url = address.rstrip("/") + _INGESTION_PATH + f"/{run_id}" + _HEARTBEAT_PATH_SUFFIX
    http_request = urllib_request.Request(  # noqa: S310
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    _exchange(http_request, timeout)


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
    "Capabilities",
    "fetch_capabilities",
    "send",
    "send_heartbeat",
]
