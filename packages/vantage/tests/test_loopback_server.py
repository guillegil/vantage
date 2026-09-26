"""`LoopbackServer`, the real-socket harness the end-to-end tests serve from.

A harness that leaks its listening socket or event loop when it fails, or
reports the wrong failure, sends whoever debugs the next red test after the
wrong cause.
"""

from __future__ import annotations

import asyncio

import loopback_server
import pytest
from loopback_server import LoopbackServer
from starlette.types import Receive, Scope, Send


async def _app(scope: Scope, receive: Receive, send: Send) -> None:
    raise AssertionError("no request reaches a server that never started")


def test_a_server_that_never_starts_says_so_and_lets_go_of_its_socket_and_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A startup that never finishes never reads `should_exit` either, so
    asking it to exit leaves its thread running. The error still names the
    startup, and the socket and loop are released all the same."""
    monkeypatch.setattr(loopback_server, "_STARTUP_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(loopback_server, "_SHUTDOWN_TIMEOUT_SECONDS", 0.5)
    server = LoopbackServer(_app)

    async def _never_finishes(sockets: object = None) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(server._server, "startup", _never_finishes)

    with pytest.raises(RuntimeError, match="did not start"):
        server.start()

    assert not server._thread.is_alive()
    assert server._loop.is_closed()
    assert server._sock.fileno() == -1


def test_a_server_that_started_serves_until_stopped_and_then_lets_go() -> None:
    with LoopbackServer(_app) as server:
        assert server._server.started

    assert not server._thread.is_alive()
    assert server._loop.is_closed()
    assert server._sock.fileno() == -1
