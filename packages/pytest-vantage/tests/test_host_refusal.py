"""A server that does not answer to the host name in the address: every
request is refused with `421 misdirected_request`, which the plugin reads
as what to fix -- the address it was given, or the names the server allows
-- and which leaves queued runs queued, for once either is fixed.

Against a real server that answers only the names a loopback bind does,
reached by a name it was never told (`misdirected_server`), and a stand-in
for anything else that answers 421.
"""

from __future__ import annotations

import math
import urllib.error
from pathlib import Path

import pytest
from loopback_server import LoopbackServer
from pytest_vantage.outbox import Outbox, outbox_path, send_queued, worth_retrying
from pytest_vantage.transport import (
    MisdirectedRequestError,
    fetch_capabilities,
    send,
    send_heartbeat,
)
from starlette.types import Receive, Scope, Send
from vantage_test_server import VantageTestServer

_RUN = "c" * 32

_REASON = (
    "HTTP 421: the server does not answer to the host name in the address: use the address it "
    "serves at, such as http://127.0.0.1:PORT, or have its operator allow the name with "
    "--allowed-host NAME"
)


def _report(run_id: str = _RUN) -> dict[str, object]:
    return {
        "run": {
            "id": run_id,
            "started_at": "2026-09-30T12:00:00+00:00",
            "finished_at": "2026-09-30T12:00:05+00:00",
            "exit_status": 0,
            "interrupted": False,
            "interrupt_reason": None,
        },
    }


def test_every_request_a_server_refuses_by_host_name_says_what_to_fix(
    misdirected_server: VantageTestServer,
) -> None:
    """A report, a heartbeat, with a token or without, and the capability
    probe: each names the fix, never the server's own words."""
    address = misdirected_server.address
    with pytest.raises(MisdirectedRequestError) as refused:
        send(address, _report(), timeout=5.0)
    with pytest.raises(MisdirectedRequestError) as with_token:
        send(address, _report(), timeout=5.0, token="vantage_s3cret")
    with pytest.raises(MisdirectedRequestError) as beat:
        send_heartbeat(address, _RUN, timeout=5.0, token="vantage_s3cret")
    probed = fetch_capabilities(address, timeout=5.0)

    for error in (refused.value, with_token.value, beat.value):
        assert error.code == 421
        assert str(error) == _REASON
        assert worth_retrying(error) is True
    assert probed.problem is not None
    assert _REASON in probed.problem
    assert misdirected_server.executions() == []


class _Answering(LoopbackServer):
    """Answers every request with `status` and `body`."""

    def __init__(self, status: int, body: bytes) -> None:
        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                return
            while (await receive()).get("more_body", False):
                pass
            headers = [(b"content-type", b"application/json")]
            await send({"type": "http.response.start", "status": status, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        super().__init__(app)


@pytest.mark.parametrize("body", [b"", b"<html>Misdirected</html>", b'{"error": "other"}'])
def test_a_421_no_vantage_server_gave_stays_plain_but_is_still_kept(body: bytes) -> None:
    """A proxy's 421 says nothing about `--allowed-host`, but the request
    went to the wrong place all the same: the run is worth sending again
    once the address is fixed."""
    with _Answering(421, body) as server, pytest.raises(urllib.error.HTTPError) as refused:
        send(server.address, _report(), timeout=5.0)

    assert type(refused.value) is urllib.error.HTTPError
    assert refused.value.code == 421
    assert worth_retrying(refused.value) is True


def test_queued_runs_a_server_refuses_by_host_name_stay_and_sending_stops(
    tmp_path: Path, misdirected_server: VantageTestServer
) -> None:
    """Every run would be refused the same way, so the first is tried and
    kept, the rest are not tried, and the reason says what to fix."""
    address = misdirected_server.address
    with Outbox(outbox_path(tmp_path / "vantage.db")) as box:
        box.enqueue(address, _RUN, [_report()])
        box.enqueue(address, "d" * 32, [_report("d" * 32)])

        summary = send_queued(box, address, timeout=5.0, budget=math.inf)

    assert (summary.sent, summary.dropped, summary.waiting) == (0, (), 2)
    assert summary.stopped == f"{address} did not take run {_RUN} ({_REASON})"
    assert misdirected_server.requests == [("POST", "/api/v1/runs")]
