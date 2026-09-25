"""The plugin's HTTP client: the session reports, heartbeats and the
capability probe, over ``urllib``.

``urlopen(timeout=t)`` bounds every socket operation of a request, so a
server that accepts the connection and never answers trips at ``t`` rather
than hanging forever.

The response side is bounded and defensive because the server is not trusted
to behave: ``resp.read(MAX_RESPONSE_BYTES)`` never buffers more than 64 KiB,
and a malformed acknowledgement raises like any other bad input. ``send``
and ``send_heartbeat`` are "send, or raise" -- nothing here decides whether a
failure is fatal; ``boundary.py`` turns any exception into a warning.
"""

from __future__ import annotations

import json
from urllib import request as urllib_request

_INGESTION_PATH = "/api/v1/runs"
_HEARTBEAT_PATH_SUFFIX = "/heartbeat"
_CAPABILITIES_PATH = "/api/v1/capabilities"

# The acknowledgement body is a few dozen bytes
# (`{"run_id": ..., "status": ..., "ignored": []}`); 64 KiB is comfortably
# larger than that while still refusing to buffer an unbounded response.
MAX_RESPONSE_BYTES = 64 * 1024


def send(address: str, report: dict[str, object], *, timeout: float) -> None:
    """POST ``report`` as JSON to ``{address}/api/v1/runs``.

    ``address`` has already passed ``config.resolve_and_validate_address``
    by the time this is called (only ``http``/``https`` ever reach here), so
    the scheme audit ``urlopen`` would otherwise flag is a false positive.

    Raises on any transport failure -- connection refused, timeout, DNS
    failure, a non-2xx status via ``urllib.error.HTTPError``, a response
    body that is not valid JSON -- this function catches nothing itself.
    """
    url = address.rstrip("/") + _INGESTION_PATH
    body = json.dumps(report).encode("utf-8")
    # Both `Request(...)` and `urlopen(...)` below are flagged by S310
    # ("audit URL open for permitted schemes"). The scheme has already been
    # validated by `config.resolve_and_validate_address` -- only http/https
    # ever reach here -- so both are false positives at this call site.
    http_request = urllib_request.Request(  # noqa: S310
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(http_request, timeout=timeout) as response:  # noqa: S310
        response_body = response.read(MAX_RESPONSE_BYTES)
    json.loads(response_body)


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
    with urllib_request.urlopen(http_request, timeout=timeout) as response:  # noqa: S310
        response.read(MAX_RESPONSE_BYTES)


def fetch_capabilities(address: str, *, timeout: float) -> bool:
    """GET ``{address}/api/v1/capabilities`` and answer whether the server
    advertises the ``session_lifecycle`` capability.

    Returns ``True`` for exactly one shape: a 2xx response whose JSON body
    is a mapping carrying ``"session_lifecycle": true``. Every other outcome
    is ``False`` -- the `404` from an older server that has no such route, a
    connection failure, a timeout, any other non-2xx status, a body that is
    not valid JSON, or JSON of the wrong shape (not a mapping, or
    ``session_lifecycle`` missing or anything but the JSON boolean ``true``).

    Unlike `send` and `send_heartbeat`, this never raises: it fails closed
    itself, so no overlooked exception path in a caller can accidentally
    turn the lifecycle on.
    """
    url = address.rstrip("/") + _CAPABILITIES_PATH
    try:
        # Flagged by S310 for the same reason `send`'s call site is -- the
        # scheme has already passed `config.resolve_and_validate_address` by
        # the time this is called, so only http/https ever reach here.
        http_request = urllib_request.Request(url, method="GET")  # noqa: S310
        with urllib_request.urlopen(http_request, timeout=timeout) as response:  # noqa: S310
            body = response.read(MAX_RESPONSE_BYTES)
        payload = json.loads(body)
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("session_lifecycle") is True


__all__ = ["MAX_RESPONSE_BYTES", "fetch_capabilities", "send", "send_heartbeat"]
