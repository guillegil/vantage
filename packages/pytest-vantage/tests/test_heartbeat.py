"""`Heartbeat`: the liveness beat's timer thread, on its own -- when it
beats, when it stops, and what it keeps of a failure -- and, against a real
server, what a session says of a beat refused once its user may no longer
record in its run's project. The rest of the session-level behaviour is in
`test_run_report.py`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from pytest_vantage.heartbeat import Heartbeat
from vantage.core.domain.projects import EDITOR_ROLE
from vantage_test_server import VantageTestServer
from warnings_summary import vantage_warnings

# Long enough for any beat these tests wait for to happen many times over.
_PATIENCE_SECONDS = 5.0


def _counting_beat(wanted: int) -> tuple[list[float], threading.Event, Callable[[], None]]:
    """A beat recording when it ran, and setting the event once it has run
    `wanted` times."""
    beats: list[float] = []
    enough = threading.Event()

    def beat() -> None:
        beats.append(time.monotonic())
        if len(beats) >= wanted:
            enough.set()

    return beats, enough, beat


def test_it_beats_once_every_interval_until_stopped() -> None:
    """The first beat comes one interval after `start`, the start report
    having just told the server the session is alive; each later one an
    interval after the last. Once stopped, it sends no more."""
    beats, enough, beat = _counting_beat(3)
    heartbeat = Heartbeat(beat, interval=0.05, join_timeout=_PATIENCE_SECONDS)
    started = time.monotonic()

    heartbeat.start()
    assert enough.wait(_PATIENCE_SECONDS)
    heartbeat.stop()
    stopped_with = len(beats)
    time.sleep(0.2)

    assert beats[0] - started >= 0.05
    assert all(later - earlier >= 0.05 for earlier, later in zip(beats, beats[1:]))
    assert len(beats) == stopped_with
    assert heartbeat.failure is None


def test_the_thread_is_a_daemon_and_ends_when_stopped() -> None:
    """A daemon, so nothing it does can hold the interpreter open at exit;
    joined by `stop`, so it never outlives the session that started it."""
    beats, enough, beat = _counting_beat(1)
    heartbeat = Heartbeat(beat, interval=0.01, join_timeout=_PATIENCE_SECONDS)

    heartbeat.start()
    assert enough.wait(_PATIENCE_SECONDS)
    (thread,) = [t for t in threading.enumerate() if t.name == "vantage-heartbeat"]
    assert thread.daemon
    heartbeat.stop()

    assert not thread.is_alive()


def test_its_first_failure_ends_it_and_is_kept_for_the_main_thread() -> None:
    """The thread never warns: `warn` belongs to pytest's main thread. It
    keeps the exception and stops beating, so a server that fails a beat
    is not asked again."""
    attempts: list[int] = []
    failed = threading.Event()

    def beat() -> None:
        attempts.append(1)
        failed.set()
        raise RuntimeError("boom")

    heartbeat = Heartbeat(beat, interval=0.01, join_timeout=_PATIENCE_SECONDS)

    heartbeat.start()
    assert failed.wait(_PATIENCE_SECONDS)
    time.sleep(0.1)
    heartbeat.stop()

    assert attempts == [1]
    assert isinstance(heartbeat.failure, RuntimeError)
    assert str(heartbeat.failure) == "boom"


def test_stop_waits_for_a_beat_in_flight() -> None:
    """The finish report must not overtake a beat still being sent, so
    `stop` waits for it, as long as its own deadline allows."""
    in_flight = threading.Event()
    finished: list[bool] = []

    def beat() -> None:
        in_flight.set()
        time.sleep(0.2)
        finished.append(True)

    heartbeat = Heartbeat(beat, interval=0.01, join_timeout=_PATIENCE_SECONDS)
    heartbeat.start()
    assert in_flight.wait(_PATIENCE_SECONDS)

    heartbeat.stop()

    assert finished == [True]


def test_stop_gives_up_on_a_beat_past_its_join_timeout() -> None:
    """A beat that overruns every deadline cannot hold the session's finish
    hostage: `stop` returns once `join_timeout` has passed."""
    in_flight = threading.Event()
    release = threading.Event()

    def beat() -> None:
        in_flight.set()
        release.wait(_PATIENCE_SECONDS)

    heartbeat = Heartbeat(beat, interval=0.01, join_timeout=0.1)
    heartbeat.start()
    assert in_flight.wait(_PATIENCE_SECONDS)
    started = time.monotonic()

    heartbeat.stop()

    assert time.monotonic() - started < 1.0
    release.set()


def test_stopping_one_never_started_or_already_stopped_does_nothing() -> None:
    heartbeat = Heartbeat(lambda: None, interval=0.01, join_timeout=_PATIENCE_SECONDS)

    heartbeat.stop()
    heartbeat.start()
    heartbeat.stop()
    heartbeat.stop()


@pytest.mark.parametrize("hook", ["pytest_sessionfinish", "pytest_unconfigure"])
def test_the_session_finishing_stops_it(hook: str) -> None:
    """Registered as a plugin of its own, it is stopped by the session's
    finish -- `tryfirst`, ahead of the finish report -- or, for a session
    that never finished, by `pytest_unconfigure`, whatever the `Recorder`'s
    own latches say."""
    beats, enough, beat = _counting_beat(1)
    heartbeat = Heartbeat(beat, interval=0.01, join_timeout=_PATIENCE_SECONDS)
    heartbeat.start()
    assert enough.wait(_PATIENCE_SECONDS)

    getattr(heartbeat, hook)()
    stopped_with = len(beats)
    time.sleep(0.1)

    assert len(beats) == stopped_with
    assert not any(t.name == "vantage-heartbeat" for t in threading.enumerate())


def test_the_finish_hook_runs_ahead_of_the_recorders() -> None:
    assert getattr(Heartbeat.pytest_sessionfinish, "pytest_impl", {}).get("tryfirst") is True


def test_a_beat_refused_for_the_users_role_warns_once_with_the_reason(
    pytester: pytest.Pytester, vantage_server: VantageTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session's user is removed from its project as soon as its start
    is recorded, so the first beat after is refused for their role, and the
    thread stops there. The session says why once, as a liveness warning
    that names no token; its finish is refused too, with a warning of its
    own, and its verdict is untouched."""
    vantage_server.add_project("firmware")
    token = vantage_server.token("bob", admin=False)
    vantage_server.set_member("bob", "firmware", EDITOR_ROLE)
    real_record_session = vantage_server.store.record_session

    def _remove_when_started(*args: Any, **kwargs: Any) -> bool:
        recorded = real_record_session(*args, **kwargs)
        vantage_server.remove_member("bob", "firmware")
        return recorded

    monkeypatch.setattr(vantage_server.store, "record_session", _remove_when_started)
    monkeypatch.setenv("VANTAGE_TOKEN", token)
    pytester.makeconftest(
        "import pytest_vantage.recorder as recorder\nrecorder._BEAT_INTERVAL_SECONDS = 0.05\n"
    )
    pytester.makepyfile(test_quiet="import time\n\ndef test_it():\n    time.sleep(0.5)\n")

    result = pytester.runpytest_subprocess(
        "--vantage", "--vantage-project=firmware", f"--vantage-server={vantage_server.address}"
    )

    assert result.ret == 0
    liveness, finish = vantage_warnings(result)
    assert liveness == (
        "vantage: error while reporting session liveness: HTTP 403: the user of the token in "
        "VANTAGE_TOKEN may no longer record in this run's project"
    )
    assert finish.startswith("vantage: error while reporting: HTTP 403: ")
    assert "vantage project member set firmware USER editor" in finish
    assert token not in result.stdout.str() + result.stderr.str()
    heartbeats = [path for _method, path in vantage_server.requests if path.endswith("/heartbeat")]
    assert len(heartbeats) == 1
    (execution,) = vantage_server.executions()
    assert execution.finished_at is None
