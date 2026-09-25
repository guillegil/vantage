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
from typing import TypeVar
from urllib import request as urllib_request

_INGESTION_PATH = "/api/v1/runs"
_HEARTBEAT_PATH_SUFFIX = "/heartbeat"
_CAPABILITIES_PATH = "/api/v1/capabilities"

# The acknowledgement body is a few dozen bytes
# (`{"run_id": ..., "status": ..., "ignored": []}`); 64 KiB is comfortably
# larger than that while still refusing to buffer an unbounded response.
MAX_RESPONSE_BYTES = 64 * 1024

# The two statuses the ingestion route acknowledges a report with: the first
# report of a run, and any later one.
_ACKNOWLEDGED_STATUSES = frozenset({"created", "duplicate"})

_T = TypeVar("_T")


def _build_opener() -> urllib_request.OpenerDirector:
    opener = urllib_request.OpenerDirector()
    handlers: list[urllib_request.BaseHandler] = [
        urllib_request.HTTPHandler(),
        urllib_request.HTTPDefaultErrorHandler(),
        urllib_request.HTTPErrorProcessor(),
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
        with _OPENER.open(http_request, timeout=timeout) as response:
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


def fetch_capabilities(address: str, *, timeout: float) -> bool:
    """GET ``{address}/api/v1/capabilities`` and answer whether the server
    advertises the ``session_lifecycle`` capability.

    Returns ``True`` for exactly one shape: a 2xx response whose JSON body
    is a mapping carrying ``"session_lifecycle": true``. Every other outcome
    is ``False`` -- the `404` from an older server that has no such route, a
    connection failure, a timeout, any other non-2xx status (a 3xx
    included), a body that is not valid JSON, or JSON of the wrong shape
    (not a mapping, or ``session_lifecycle`` missing or anything but the
    JSON boolean ``true``).

    Unlike `send` and `send_heartbeat`, this never raises: it fails closed
    itself, so no overlooked exception path in a caller can accidentally
    turn the lifecycle on.
    """
    url = address.rstrip("/") + _CAPABILITIES_PATH
    try:
        http_request = urllib_request.Request(url, method="GET")  # noqa: S310
        payload = json.loads(_exchange(http_request, timeout))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("session_lifecycle") is True


__all__ = ["MAX_RESPONSE_BYTES", "fetch_capabilities", "send", "send_heartbeat"]
