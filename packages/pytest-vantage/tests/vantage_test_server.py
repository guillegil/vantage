"""`VantageTestServer` -- a real `vantage` server for `pytest-vantage`'s own
end-to-end tests, and the `vantage_server` fixture that wraps it; `ServerGate` and its
`server_gate` fixture, one address for a server that goes away and comes
back; and `git_confined_to_basetemp`, for the tests that need git to find
no repository above their temp directory.

A separate, non-`test_*` module rather than a `conftest.py`: a package-level
`conftest.py` alongside the workspace-root one both resolve to the bare
module name `conftest` under this project's plain (non-package) test
layout, which mypy rejects as a duplicate module. The workspace-root
conftest registers this module as a plugin instead, so any test can request
`vantage_server` without importing it.

Dev-only and never packaged, so it may import the server and `uvicorn`.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from loopback_server import LoopbackServer
from sqlite_rows import read_metadata
from starlette.types import Receive, Scope, Send
from vantage.core.domain.access import READ_SCOPE, RECORD_SCOPE, new_token, token_digest
from vantage.core.domain.execution import Execution
from vantage.core.domain.projects import DEFAULT_PROJECT
from vantage.core.domain.result import CatalogueEntry, Result
from vantage.core.ports.storage import MAX_PAGE_ITEMS, RunMetadata
from vantage.service.app import create_app
from vantage.storage.sqlite_store import SqliteExecutionStore


class VantageTestServer(LoopbackServer):
    """A real `vantage` server (uvicorn + `create_app`) on an ephemeral
    loopback port, backed by a `SqliteExecutionStore` in `directory` -- the
    adapter `vantage` serves -- so the plugin's end-to-end tests exercise
    the store that ships.

    The inspection helpers read through the store's port, and through plain
    SQL for a run's metadata, whose file rows no port method returns.
    """

    def __init__(self, directory: Path) -> None:
        self._database = directory / "vantage.db"
        self.store = SqliteExecutionStore(self._database)
        app = create_app(self.store)
        # Every HTTP request, in arrival order, so a test can assert what the
        # plugin sent even when it left nothing in the store.
        self.requests: list[tuple[str, str]] = []

        async def _log_requests(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                self.requests.append((scope["method"], scope["path"]))
            await app(scope, receive, send)

        super().__init__(_log_requests)

    def close(self) -> None:
        """Stop serving, then close the store. A test may stop the server
        itself and still inspect the store until then."""
        try:
            self.stop()
        finally:
            self.store.close()

    def _run_ids(self) -> list[str]:
        """Every stored run id, in every project, newest first. The plugin
        generates the id, so a test rarely knows it ahead of time; the run
        lists do."""
        runs: list[tuple[datetime, str]] = []
        for project in self.store.list_projects():
            listed = 0
            while True:
                page = self.store.list_runs(
                    project=project.name, limit=MAX_PAGE_ITEMS, offset=listed
                )
                listed += len(page.items)
                runs.extend(
                    (entry.execution.started_at, entry.execution.identity.value)
                    for entry in page.items
                )
                if not page.has_more:
                    break
        return [run_id for _started_at, run_id in sorted(runs, reverse=True)]

    def executions(self) -> list[Execution]:
        """Every execution the server has stored so far, newest first."""
        return [
            execution
            for run_id in self._run_ids()
            if (execution := self.store.get_execution(run_id)) is not None
        ]

    def results(self) -> list[Result]:
        """Every result the server has stored so far, across every
        execution."""
        return [result for run_id in self._run_ids() for result in self.store.get_results(run_id)]

    def metadata(self, run_id: str) -> RunMetadata:
        """The metadata files and entries stored for `run_id`, in no
        particular order."""
        return read_metadata(self._database, run_id)

    def catalogue_entry(
        self, node_id: str, project: str = DEFAULT_PROJECT
    ) -> CatalogueEntry | None:
        """The catalogue entry for one node id in `project`, or `None` if
        the server has never observed it there."""
        return self.store.get_catalogue_entry(node_id, project=project)

    def add_project(self, name: str) -> None:
        """Make the project `name`, as an admin would."""
        self.store.create_project(name, created_at=datetime.now(timezone.utc))

    def project_of(self, run_id: str) -> str:
        """The project the server filed `run_id` in."""
        detail = self.store.get_run_detail(run_id)
        assert detail is not None, f"no run {run_id}"
        return detail.project

    def token(self, user: str, *scopes: str) -> str:
        """A new token of `user` holding `scopes`, read and record when none
        is named. The user is made first if nobody has that name, as an
        admin, which closes the server: from then on it needs a token."""
        now = datetime.now(timezone.utc)
        if self.store.get_user(user) is None:
            self.store.create_user(user, admin=True, created_at=now)
        token = new_token()
        self.store.create_token(
            user,
            digest=token_digest(token),
            label="",
            scopes=frozenset(scopes or (READ_SCOPE, RECORD_SCOPE)),
            created_at=now,
        )
        return token

    def recorded_by(self, run_id: str) -> str | None:
        """Who the server says recorded `run_id`."""
        detail = self.store.get_run_detail(run_id)
        assert detail is not None, f"no run {run_id}"
        return detail.recorded_by


def wait_for_execution(server: VantageTestServer, *, timeout: float = 15.0) -> Execution:
    """Poll `server` until a run entry has landed and return the newest, or
    raise after `timeout` seconds: a bounded wait on an observable condition
    rather than a fixed sleep, which is flaky on a loaded CI runner.
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
def vantage_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[VantageTestServer]:
    # A directory of its own, never one the test itself writes to or
    # inspects, such as `tmp_path` or `pytester.path`.
    server = VantageTestServer(tmp_path_factory.mktemp("vantage-server"))
    try:
        # Inside the `try`: a server that fails to start still closes its
        # store's connection.
        server.start()
        yield server
    finally:
        server.close()


class ServerGate:
    """A loopback address that refuses every connection until `open`, then
    forwards each to a real server: one address for a server that is down
    and then back. Bound but not listening is what makes a connect fail
    with "connection refused".

    Let through a number of `connections`, it resets every connection after
    those: a server that went away in the middle of a session.
    `let_through(None)` brings it back for good."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self.address = f"http://127.0.0.1:{self._sock.getsockname()[1]}"
        self._stopped = threading.Event()
        self._target: tuple[str, int] | None = None
        self._forwarding: int | None = None

    def open(self, target: VantageTestServer, *, connections: int | None = None) -> None:
        self._target = ("127.0.0.1", target.port)
        self._forwarding = connections
        self._sock.listen(16)
        self._sock.settimeout(0.1)
        threading.Thread(target=self._accept, daemon=True).start()

    def let_through(self, connections: int | None) -> None:
        """Forward the next `connections` and reset the rest; `None` for
        every one."""
        self._forwarding = connections

    def _accept(self) -> None:
        while not self._stopped.is_set():
            try:
                client, _ = self._sock.accept()
            except OSError:
                continue
            if self._forwarding is not None:
                if self._forwarding == 0:
                    # A zero linger makes close send a reset, not a clean end.
                    client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    client.close()
                    continue
                self._forwarding -= 1
            threading.Thread(target=self._forward, args=(client,), daemon=True).start()

    def _forward(self, client: socket.socket) -> None:
        assert self._target is not None
        try:
            upstream = socket.create_connection(self._target, timeout=10)
        except OSError:
            client.close()
            return
        pumps = [
            threading.Thread(target=_pump, args=(client, upstream), daemon=True),
            threading.Thread(target=_pump, args=(upstream, client), daemon=True),
        ]
        for pump in pumps:
            pump.start()
        for pump in pumps:
            pump.join(timeout=30)
        client.close()
        upstream.close()

    def close(self) -> None:
        self._stopped.set()
        self._sock.close()


def _pump(source: socket.socket, sink: socket.socket) -> None:
    try:
        while data := source.recv(65536):
            sink.sendall(data)
    except OSError:
        pass
    try:
        sink.shutdown(socket.SHUT_WR)
    except OSError:
        pass


@pytest.fixture
def server_gate() -> Iterator[ServerGate]:
    gate = ServerGate()
    try:
        yield gate
    finally:
        gate.close()


@pytest.fixture
def git_confined_to_basetemp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stops git's upward search at the session's base temp directory.

    A test that builds a directory with no repository, or a broken one,
    relies on git finding nothing above it. A base temp directory inside
    some git work tree (a `--basetemp` in a checkout, a TMPDIR under a
    git-managed home) would let git find that repository instead. git and
    `vcs.capture` both honour the ceiling, and a subprocess session
    inherits it.
    """
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path_factory.getbasetemp()))
