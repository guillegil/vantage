"""One ``urllib`` POST per session (design.md D7, RQ-25's shape).

``urlopen(timeout=t)`` bounds every socket operation of the request, so a
server that accepts the connection and never answers trips at ``t`` rather
than hanging forever (design.md D6) -- once ``boundary.py`` (PR12) wraps the
call in a bounded warning path instead of letting it propagate.

Response handling stays minimal here on purpose: bounding *how much* of the
response is read and defensively parsing the acknowledgement are threat-matrix
concerns ("Untrusted response") that land with the failure-path work in PR12
(task 6.12, ``MAX_RESPONSE_BYTES``). This module's job for PR11 is only the
request half -- send the report, or raise.
"""

from __future__ import annotations

import json
from urllib import request as urllib_request

_INGESTION_PATH = "/api/v1/runs"


def send(address: str, report: dict[str, object], *, timeout: float) -> None:
    """POST ``report`` as JSON to ``{address}/api/v1/runs``.

    ``address`` has already passed ``config.resolve_and_validate_address``
    by the time this is called (only ``http``/``https`` ever reach here), so
    the scheme audit ``urlopen`` would otherwise flag is a false positive.

    Raises on any transport failure -- connection refused, timeout, DNS
    failure, a non-2xx status via ``urllib.error.HTTPError`` -- this
    function catches nothing. Turning a raised exception here into a
    warning instead of an unhandled error (design.md D6, RQ-21) is PR12's
    ``boundary.py`` decorator; until it lands, this module's contract is
    "send, or raise" and nothing more.
    """
    url = address.rstrip("/") + _INGESTION_PATH
    body = json.dumps(report).encode("utf-8")
    http_request = urllib_request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(http_request, timeout=timeout) as response:  # noqa: S310
        response.read()


__all__ = ["send"]
