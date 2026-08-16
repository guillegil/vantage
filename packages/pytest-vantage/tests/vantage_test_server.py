"""`VantageTestServer` -- a real `vantage` server for `pytest-vantage`'s own
end-to-end tests (design.md D2a), and the `vantage_server` fixture that
wraps it.

A separate, non-`test_*` module rather than a `conftest.py`: a package-level
`conftest.py` alongside the workspace-root one both resolve to the bare
module name `conftest` under this project's plain (non-package) test
layout, which mypy rejects as a duplicate module -- pytest itself tolerates
it (each is loaded through its own path-keyed machinery), but a second
`conftest.py` here would fail `mypy --strict` outright. Importing the
fixture function directly into the one test module that needs it
(`test_run_report.py`) is the standard pytest idiom for a fixture that does
not need conftest.py's directory-wide auto-application, and it sidesteps
the collision entirely. `vantage_port_contract.py` and `importwalk.py`
(both PR1) already establish the same "shared, non-test module living
directly inside a `tests/` directory" pattern.

Dev-only: never packaged (RQ-24 constrains `src/`, not `tests/`).
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from vantage.core.domain.execution import Execution
from vantage.service.app import create_app
from vantage.storage.memory import InMemoryExecutionStore


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

        config = uvicorn.Config(
            create_app(self.store), host="127.0.0.1", log_level="warning", lifespan="off"
        )
        self._server = uvicorn.Server(config)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="vantage-test-server", daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._server.serve(sockets=[self._sock]))

    def start(self) -> None:
        self._thread.start()
        while not self._server.started:
            time.sleep(0.001)

    def stop(self) -> None:
        self._server.should_exit = True
        # A failing assertion above must not leave a listener behind to
        # poison a later test -- join with a bound, not forever.
        self._thread.join(timeout=5)

    def executions(self) -> list[Execution]:
        """Every execution the server has stored so far, in no particular
        order. `InMemoryExecutionStore.get_execution` needs the id, which
        the caller here rarely knows ahead of time (it is client-generated
        by `Recorder`) -- reaching into the store's own dict once here,
        rather than scattering the same private-attribute access across
        every test that needs to inspect what was recorded.
        """
        return list(self.store._executions.values())  # noqa: SLF001


@pytest.fixture
def vantage_server() -> Iterator[VantageTestServer]:
    server = VantageTestServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()
