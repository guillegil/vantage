"""`VantageTestServer` -- a real `vantage` server for `pytest-vantage`'s own
end-to-end tests, and the `vantage_server` fixture that wraps it. Also
`git_confined_to_basetemp`, for the tests that need git to find no
repository above their temp directory.

A separate, non-`test_*` module rather than a `conftest.py`: a package-level
`conftest.py` alongside the workspace-root one both resolve to the bare
module name `conftest` under this project's plain (non-package) test
layout, which mypy rejects as a duplicate module. The workspace-root
conftest registers this module as a plugin instead, so any test can request
`vantage_server` without importing it.

Dev-only and never packaged, so it may import the server and `uvicorn`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from loopback_server import LoopbackServer
from sqlite_rows import read_metadata
from starlette.types import Receive, Scope, Send
from vantage.core.domain.execution import Execution
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
        """Every stored run id, newest first. The plugin generates the id,
        so a test rarely knows it ahead of time; the run list does."""
        run_ids: list[str] = []
        while True:
            page = self.store.list_runs(limit=MAX_PAGE_ITEMS, offset=len(run_ids))
            run_ids.extend(entry.execution.identity.value for entry in page.items)
            if not page.has_more:
                return run_ids

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

    def catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        """The catalogue entry for one node id, or `None` if the server has
        never observed it."""
        return self.store.get_catalogue_entry(node_id)


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
