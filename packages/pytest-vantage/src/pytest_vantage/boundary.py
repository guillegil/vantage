"""Fault isolation for the reporting and liveness paths.

A recording failure is reported once, as a warning, and the suite finishes
exactly as it would have otherwise. `warn` emits that warning;
`fault_isolated`, `liveness_isolated` and `accumulation_isolated` wrap
`Recorder` hooks, each on its own flag, so neither a failed start-write or
heartbeat nor an unexpected test report disables the finish report.

pytest records warnings, for its warnings summary, only inside its
collection, test-protocol, session-finish and terminal-summary hooks. A
warning raised anywhere else -- the plugin's `pytest_configure`,
`pytest_sessionstart`, the xdist controller's loop -- would print to stderr
and never reach the summary, so `warn` issues it there the way pytest issues
its own configuration warnings. `configuring` and `WarningPhase` tell it
when.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import sys
import warnings
from collections.abc import Callable, Generator, Iterator
from types import FrameType
from typing import Any, TypeVar

import pytest

_F = TypeVar("_F", bound=Callable[..., Any])


class VantageWarning(UserWarning):
    """A recording failure that must not disrupt the host pytest session.

    Deliberately not `pytest.PytestWarning` -- a project's own
    `-W error::pytest.PytestWarning` must not turn a Vantage reporting
    failure into a raised exception just because it happens to share
    pytest's own warning family.
    """


_PACKAGE = __name__.partition(".")[0]

# Whether pytest is recording no warning right now while its warnings
# summary is still to come, for `warn`. A context variable, not the stash:
# the plugin's own `pytest_configure` has no plugin registered to own state
# (a session without `--vantage` must register none), and each value is set
# only for the length of one `with`, so nothing outlives the hook that set
# it -- a pytest session run inside another keeps its own.
_UNRECORDED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "vantage_unrecorded", default=False
)


@contextlib.contextmanager
def _unrecorded_while(value: bool) -> Iterator[None]:
    token = _UNRECORDED.set(value)
    try:
        yield
    finally:
        _UNRECORDED.reset(token)


def configuring() -> contextlib.AbstractContextManager[None]:
    """Marks the plugin's own `pytest_configure`, for `warn`: pytest records
    no warning raised there."""
    return _unrecorded_while(True)


class WarningPhase:
    """Tells `warn` where pytest records no warning for the rest of a
    recorded session: registered with the `Recorder`, on the controller.

    `pytest_sessionstart` and the test loop are unrecorded -- the loop is
    where the xdist controller does all its work -- but each test inside it
    is recorded, by pytest's own `pytest_runtest_protocol` wrapper, which
    this one's `trylast` puts it inside. Collection, the session's finish and
    its summary are recorded too, and nothing is marked there.
    """

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionstart(self) -> Generator[None, object, object]:
        with _unrecorded_while(True):
            return (yield)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtestloop(self) -> Generator[None, object, object]:
        with _unrecorded_while(True):
            return (yield)

    @pytest.hookimpl(wrapper=True, trylast=True)
    def pytest_runtest_protocol(self) -> Generator[None, object, object]:
        with _unrecorded_while(False):
            return (yield)


def _in_this_package(frame: FrameType) -> bool:
    name = frame.f_globals.get("__name__")
    return isinstance(name, str) and name.partition(".")[0] == _PACKAGE


def _project_stacklevel() -> int:
    """The `stacklevel`, as `warn` passes it, of the nearest frame outside
    this package: the project's own line that called into the plugin. 2,
    `warn`'s caller, when every frame is the plugin's."""
    # Counted from this function's own frame, 0, so that `warn`'s is 1, as
    # `stacklevel` counts from the function calling `warnings.warn`.
    frame: FrameType | None = sys._getframe()
    level = 0
    while frame is not None and _in_this_package(frame):
        frame = frame.f_back
        level += 1
    return 2 if frame is None else level


def warn(config: pytest.Config, message: str, *, at_project_line: bool = False) -> None:
    """Emit `message` as a `VantageWarning`, located at the caller, or with
    `at_project_line` at the project's own line that called into the
    plugin, for a warning about something that line did.

    Where pytest records nothing (`_UNRECORDED`), it goes through
    `config.issue_config_time_warning`, which applies the project's warning
    filters and puts it in the warnings summary. Without pytest's warnings
    plugin (`-p no:warnings`) there is no summary, and a plain warning
    prints to stderr.

    `warnings.warn` raises the warning instance itself when the active
    filters turn it into an error (`filterwarnings = ["error"]`, `-W
    error`) -- a project that made that choice for its own warnings must not
    have a Vantage reporting failure turn into an unhandled exception as a
    side effect. The fallback is the terminal reporter, then `sys.stderr` if
    no terminal reporter is registered (e.g. `-q -q` or a very early
    failure): either way the message is not lost.
    """
    stacklevel = _project_stacklevel() if at_project_line else 2
    try:
        if _UNRECORDED.get() and not config.pluginmanager.is_blocked("warnings"):
            # One frame deeper: `issue_config_time_warning` calls `warnings.warn`.
            config.issue_config_time_warning(VantageWarning(message), stacklevel=stacklevel + 1)
        else:
            warnings.warn(VantageWarning(message), stacklevel=stacklevel)
        return
    except VantageWarning:
        pass
    # Duck-typed, not `isinstance(..., TerminalReporter)`: importing that
    # class means reaching into pytest's private `_pytest.terminal` module
    # for a fallback path that only matters when no reporter is present at
    # all. Anything registered under this name that can `write_line` is
    # good enough to receive the message.
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    write_line = getattr(reporter, "write_line", None)
    if callable(write_line):
        write_line(message)
        return
    print(message, file=sys.stderr)


def _isolated(flag: str, description: str, *, latch: bool = True) -> Callable[[_F], _F]:
    """Build a fault-isolation decorator keyed off its own instance flag.

    Catches `Exception`, never `BaseException` -- `KeyboardInterrupt` and
    `SystemExit` must still propagate through pytest's own `wrap_session`,
    so a real Ctrl-C still interrupts the session. Warns on the first
    failure only: `flag` records that the warning was given. With `latch`,
    `flag` also makes every later call through a decorator built from the
    *same* `flag` a silent no-op that does not even invoke the hook body
    again; without it, the body keeps running and later failures stay
    silent. A decorator built with a *different* `flag` neither reads nor
    sets this one -- two decorators from two calls to this factory are
    independent in both directions, which is the whole reason
    `liveness_isolated` and `accumulation_isolated` exist rather than
    reusing `fault_isolated`'s `_disabled`.

    The wrapper carries `isolation_flag`, naming `flag`, so a test can check
    which isolation a hook is under.

    Never assigns `session.exitstatus`. Catching the exception here and
    simply not re-raising it *is* the entire mechanism -- nothing in this
    module, or the hook it wraps, touches the suite's verdict.
    """

    def decorator(hook: _F) -> _F:
        @functools.wraps(hook)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            if latch and getattr(self, flag):
                return None
            try:
                return hook(self, *args, **kwargs)
            except Exception as exc:  # deliberately broad, never BaseException
                if not getattr(self, flag):
                    setattr(self, flag, True)
                    warn(self._config, f"vantage: {description}: {exc}")
                return None

        setattr(wrapper, "isolation_flag", flag)  # a function attribute mypy cannot type
        return wrapper  # type: ignore[return-value]

    return decorator


fault_isolated = _isolated("_disabled", "error while reporting")
"""Wraps the `Recorder` hooks that report to the server. Latching on
`_disabled` keeps a session where several hooks fail at exactly one
warning, not one per failing hook.
"""

liveness_isolated = _isolated("_liveness_disabled", "error while reporting session liveness")
"""Wraps the start-write and heartbeat hooks. Latches on its own
`_liveness_disabled` flag, deliberately -- a server that failed one beat
has almost certainly failed the next, and repeated attempts across a long
suite would each cost a bounded stall for no new information -- but never
reads or sets `_disabled`, so a liveness failure can never disable result
accumulation, `pytest_sessionfinish`, or the finish-write.
"""

accumulation_isolated = _isolated(
    "_accumulation_warned", "error while recording a test report", latch=False
)
"""Wraps the hooks that only record, in memory, what pytest reports. Warns
once, on its own `_accumulation_warned` flag, but never stops recording:
one report of a shape the plugin did not expect costs that report alone,
never the reports after it and never the finish-write, whose `_disabled`
it neither reads nor sets.
"""


__all__ = [
    "VantageWarning",
    "WarningPhase",
    "accumulation_isolated",
    "configuring",
    "fault_isolated",
    "liveness_isolated",
    "warn",
]
