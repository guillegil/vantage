"""`VantageTestServer` -- a real `vantage` server for `pytest-vantage`'s own
end-to-end tests, and the `vantage_server` fixture that wraps it.

A separate, non-`test_*` module rather than a `conftest.py`: a package-level
`conftest.py` alongside the workspace-root one both resolve to the bare
module name `conftest` under this project's plain (non-package) test
layout, which mypy rejects as a duplicate module. The workspace-root
conftest registers this module as a plugin instead, so any test can request
`vantage_server` without importing it.

Dev-only and never packaged, so it may import the server and `uvicorn`.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from memory_store import InMemoryExecutionStore
from starlette.types import Receive, Scope, Send
from vantage.core.domain.execution import Execution
from vantage.core.domain.result import CatalogueEntry, Result
from vantage.core.ports.storage import RunMetadata
from vantage.service.app import create_app

# Both bound the wait on the server thread: a thread that dies or hangs must
# fail the test that needed it, never hang the whole suite.
_STARTUP_TIMEOUT_SECONDS = 5.0
_SHUTDOWN_TIMEOUT_SECONDS = 5.0


class VantageTestServer:
    """A real `vantage` server (uvicorn + `create_app`), bound to an
    ephemeral loopback port, backed by an in-memory store the test can
    inspect directly.

    Binds its own listening socket first (`("127.0.0.1", 0)`, then
    `getsockname()` for the OS-assigned port), the same ordering
    `test_rejection.py::_RawSocketServer` uses and for the same reason: the
    real port must be known ahead of time without guessing or hardcoding
    one that might already be taken.
    """

    def __init__(self) -> None:
        self.store = InMemoryExecutionStore()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self.port = self._sock.getsockname()[1]
        self.address = f"http://127.0.0.1:{self.port}"

        app = create_app(self.store)
        # Every HTTP request, in arrival order, so a test can assert what the
        # plugin sent even when it left nothing in the store.
        self.requests: list[tuple[str, str]] = []

        async def _log_requests(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                self.requests.append((scope["method"], scope["path"]))
            await app(scope, receive, send)

        config = uvicorn.Config(
            _log_requests,
            host="127.0.0.1",
            log_level="warning",
            lifespan="off",
            interface="asgi3",
        )
        self._server = uvicorn.Server(config)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="vantage-test-server", daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._server.serve(sockets=[self._sock]))

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                self.stop()
                raise RuntimeError("the vantage test server did not start")
            time.sleep(0.001)

    def stop(self) -> None:
        self._server.should_exit = True
        # A failing assertion above must not leave a listener behind to
        # poison a later test -- join with a bound, not forever.
        self._thread.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            raise RuntimeError("the vantage test server did not stop")
        # Only once the thread has exited: closing a loop that is still
        # running raises. Left open, it warns as unclosed in whichever test
        # happens to trigger garbage collection.
        self._loop.close()
        self._sock.close()

    def executions(self) -> list[Execution]:
        """Every execution the server has stored so far, in no particular
        order. `InMemoryExecutionStore.get_execution` needs the id, which
        the caller here rarely knows ahead of time (it is client-generated
        by `Recorder`) -- reaching into the store's own dict once here,
        rather than scattering the same private-attribute access across
        every test that needs to inspect what was recorded.
        """
        return list(self.store._executions.values())  # noqa: SLF001

    def results(self) -> list[Result]:
        """Every result the server has stored so far, across every
        execution, in no particular order. `get_results` on the port needs
        an execution id the caller rarely knows ahead of time -- same
        reasoning as `executions()` above, and the same private-attribute
        reach-in rather than scattering it across every test that needs it.
        """
        return list(self.store._results.values())  # noqa: SLF001

    def metadata(self, run_id: str) -> RunMetadata:
        """The metadata files and entries stored for `run_id`. No port
        method returns them -- the run list filters by them without showing
        them -- so this reaches into the store the way `executions()` does.
        """
        files = tuple(
            stored
            for (stored_run, _path), stored in self.store._metadata_files.items()  # noqa: SLF001
            if stored_run == run_id
        )
        entries = tuple(
            stored
            for (stored_run, _key), stored in self.store._metadata_entries.items()  # noqa: SLF001
            if stored_run == run_id
        )
        return RunMetadata(files=files, entries=entries)

    def catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        """The catalogue entry for one node id, or `None` if the server has
        never observed it. A thin pass-through -- `get_catalogue_entry` is
        already public on the port and takes exactly this argument, so no
        private reach-in is needed here (unlike `executions()`/`results()`).
        """
        return self.store.get_catalogue_entry(node_id)


def wait_for_execution(server: VantageTestServer, *, timeout: float = 15.0) -> Execution:
    """Poll `server` until its first run entry has landed, or raise after
    `timeout` seconds: a bounded wait on an observable condition rather
    than a fixed sleep, which is flaky on a loaded CI runner.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        executions = server.executions()
        if executions:
            return executions[0]
        time.sleep(0.02)
    raise TimeoutError(f"no run entry appeared within {timeout}s")


def wait_for_file(path: Path, *, timeout: float = 15.0) -> None:
    """Poll until `path` exists, or raise after `timeout` seconds: the same
    bounded wait, on a marker a child process writes once it has reached a
    point the test must not act before.
    """
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"{path} did not appear within {timeout}s")
        time.sleep(0.01)


@pytest.fixture
def vantage_server() -> Iterator[VantageTestServer]:
    server = VantageTestServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()
