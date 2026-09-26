"""`LoopbackServer` -- a real `uvicorn` server for an ASGI app, run on its own
thread and an ephemeral loopback port, for tests that need a real socket.

The listening socket is bound here, *before* uvicorn sees it, to an
OS-assigned port (`bind(("127.0.0.1", 0))`, then `getsockname()`), so the
port is known ahead of time and the test survives a machine where any fixed
port is already taken.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import TypeVar

import uvicorn
from starlette.types import ASGIApp

# Both bound the wait on the server thread: a thread that dies or hangs must
# fail the test that needed it, never hang the whole suite.
_STARTUP_TIMEOUT_SECONDS = 5.0
_SHUTDOWN_TIMEOUT_SECONDS = 5.0

_Server = TypeVar("_Server", bound="LoopbackServer")


def _cancel_every_task(loop: asyncio.AbstractEventLoop) -> None:
    for task in asyncio.all_tasks(loop):
        task.cancel()


class LoopbackServer:
    """Serves `app` on `address` between `start()` and `stop()`, or for the
    body of a `with` block."""

    def __init__(self, app: ASGIApp) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self.port: int = self._sock.getsockname()[1]
        self.address = f"http://127.0.0.1:{self.port}"

        config = uvicorn.Config(
            app, host="127.0.0.1", log_level="warning", lifespan="off", interface="asgi3"
        )
        self._server = uvicorn.Server(config)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="loopback-test-server", daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._server.serve(sockets=[self._sock]))
        except asyncio.CancelledError:
            pass  # `_release` cancelled a server that did not exit when asked

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                # Released, not stopped: a server that never started must
                # still let go of its socket and loop, and the error must
                # say it did not start whether or not its thread exits.
                self._release()
                raise RuntimeError("the loopback test server did not start")
            time.sleep(0.001)

    def stop(self) -> None:
        """Stop serving; safe to call again once stopped."""
        if not self._release():
            raise RuntimeError("the loopback test server did not stop")

    def _release(self) -> bool:
        """End the server thread and close the socket and the loop; return
        whether the thread exited.

        A failing assertion must not leave a listener behind to poison a
        later test, so every wait is bounded. uvicorn reads `should_exit`
        only between its own steps, so a server stuck before it gets there
        -- still starting, say -- is cancelled instead. The loop is closed
        only once the thread has left it, since closing a running loop
        raises; left open, it warns as unclosed in whichever test happens
        to trigger garbage collection.
        """
        self._server.should_exit = True
        self._thread.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            self._loop.call_soon_threadsafe(_cancel_every_task, self._loop)
            self._thread.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        self._sock.close()
        if self._thread.is_alive():
            return False
        self._loop.close()
        return True

    def __enter__(self: _Server) -> _Server:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
