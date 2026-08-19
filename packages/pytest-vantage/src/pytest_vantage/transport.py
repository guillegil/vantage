"""One ``urllib`` POST per session (design.md D7, RQ-25's shape).

``urlopen(timeout=t)`` bounds every socket operation of the request, so a
server that accepts the connection and never answers trips at ``t`` rather
than hanging forever (design.md D6) -- ``boundary.py`` is what turns that,
and any other exception from this module, into a bounded warning instead of
letting it propagate.

The response side is bounded and defensive on purpose (design.md threat
matrix "Untrusted response"): the server this plugin talks to is not
trusted to behave. ``resp.read(MAX_RESPONSE_BYTES)`` never buffers more than
64 KiB into memory regardless of how much the server actually sends, and the
acknowledgement is parsed as JSON the same way any other malformed input
would be -- by raising, which ``boundary.py``'s fault isolation (RQ-21)
turns into a warning, not by swallowing the error here. This module's
contract stays "send, or raise" -- nothing here decides whether a failure is
fatal.
"""

from __future__ import annotations

import json
from urllib import request as urllib_request

_INGESTION_PATH = "/api/v1/runs"
_HEARTBEAT_PATH_SUFFIX = "/heartbeat"
_CAPABILITIES_PATH = "/api/v1/capabilities"

# design.md threat matrix "Untrusted response": the acknowledgement body is
# never expected to be more than a few dozen bytes
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
    Turning a raised exception here into a warning instead of an unhandled
    error (design.md D6, RQ-21) is ``boundary.py``'s job.
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
    """POST an empty liveness beat to ``{address}/api/v1/runs/{run_id}/heartbeat``
    (design.md D31, D33).

    A distinct function rather than a `path` parameter on `send` above --
    `send`'s contract and docstring stay true to "one session report", and
    the two calls differ in more than their path: this one sends no body of
    meaning (design.md D33: `{}`, read by nothing) and is bounded by the
    liveness timeout, never the report timeout. Declared here in Phase 2 so
    this module's shape settles once; nothing calls it until Phase 4 wires
    the heartbeat hook.

    Raises on any transport failure, exactly like `send` -- turning that
    into a warning instead of an unhandled error is `boundary.py`'s job.
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
    advertises the ``session_lifecycle`` capability (design decisions
    D38-D40, `plugin-server-compatibility`).

    Returns ``True`` for exactly one shape: a `200` response whose JSON body
    is a mapping carrying ``"session_lifecycle": true``. Every other outcome
    degrades to ``False`` -- the `404` an older server (one that predates
    this route entirely) answers (D39), a connection failure, a timeout, any
    other non-2xx status, a body that is not valid JSON, JSON of the wrong
    shape (not a mapping at all, or ``session_lifecycle`` missing, falsy, or
    not literally the JSON boolean ``true``).

    Unlike `send`/`send_heartbeat`, whose contract is "send, or raise" and
    leave turning the exception into a warning to `boundary.py`, this
    function IS the fail-closed boundary (D40): it never propagates. Catching
    broadly here, not in the caller, is deliberate -- a capability check that
    could ever raise out to its caller would let one overlooked exception
    path fail OPEN by accident, exactly the defect this change exists to
    prevent.
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
