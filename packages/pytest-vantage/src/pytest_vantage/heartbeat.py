"""`Heartbeat`: the session's liveness beat, sent from a timer thread.

A beat says the session's process is alive, not that its tests are moving:
one test, fixture or collection quieter than the server's grace period keeps
its run `running`, where beats sent only as test reports arrive would let it
read as `abandoned` until the next one. A test that hangs therefore reads as
`running` too; ending it is a timeout's job, not the recorder's.

Standard library and `pytest` only.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest


class Heartbeat:
    """Calls `beat` every `interval` seconds on a daemon thread, from `start`
    until `stop` or the first beat that raises.

    The thread never warns: `warn` and the isolation latches belong to
    pytest's main thread, where a warning reaches the summary and the
    project's filters. It keeps the exception in `failure` instead, for the
    `Recorder` to report there, and stops, so a server that failed a beat is
    not asked again.

    Registered as a plugin of its own, so the session's end stops it
    whatever state the `Recorder`'s own latches are in: `pytest_sessionfinish`
    is `tryfirst`, ahead of the `Recorder`'s finish report, and
    `pytest_unconfigure` stops one whose session never finished. `stop` waits
    for a beat in flight, which `beat`'s own deadline bounds, but never
    longer than `join_timeout`.
    """

    def __init__(self, beat: Callable[[], None], *, interval: float, join_timeout: float) -> None:
        self._beat = beat
        self._interval = interval
        self._join_timeout = join_timeout
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self.failure: Exception | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="vantage-heartbeat", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # `wait` returns True only once `stop` is called: the first beat is
        # an interval after `start`, when the start report has just said
        # the session is alive.
        while not self._stopped.wait(self._interval):
            try:
                self._beat()
            except Exception as exc:  # never BaseException, which is not the server's doing
                self.failure = exc
                return

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(self._join_timeout)

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self) -> None:
        self.stop()

    def pytest_unconfigure(self) -> None:
        self.stop()


__all__ = ["Heartbeat"]
