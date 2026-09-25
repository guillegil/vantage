"""Fault isolation for the reporting and liveness paths.

A recording failure is reported once, as a warning, and the suite finishes
exactly as it would have otherwise. `warn` emits that warning;
`fault_isolated`, `liveness_isolated` and `accumulation_isolated` wrap
`Recorder` hooks, each on its own flag, so neither a failed start-write or
heartbeat nor an unexpected test report disables the finish report.
"""

from __future__ import annotations

import functools
import sys
import warnings
from collections.abc import Callable
from typing import Any, TypeVar

import pytest

F = TypeVar("F", bound=Callable[..., Any])


class VantageWarning(UserWarning):
    """A recording failure that must not disrupt the host pytest session.

    Deliberately not `pytest.PytestWarning` -- a project's own
    `-W error::pytest.PytestWarning` must not turn a Vantage reporting
    failure into a raised exception just because it happens to share
    pytest's own warning family.
    """


def warn(config: pytest.Config, message: str) -> None:
    """Emit `message` as a `VantageWarning`.

    `warnings.warn` raises the warning instance itself when the active
    filters turn it into an error (`filterwarnings = ["error"]`, `-W
    error`) -- a project that made that choice for its own warnings must not
    have a Vantage reporting failure turn into an unhandled exception as a
    side effect. The fallback is the terminal reporter, then `sys.stderr` if
    no terminal reporter is registered (e.g. `-q -q` or a very early
    failure): either way the message is not lost.
    """
    try:
        warnings.warn(VantageWarning(message), stacklevel=2)
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


def _isolated(flag: str, description: str, *, latch: bool = True) -> Callable[[F], F]:
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

    def decorator(hook: F) -> F:
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
    "accumulation_isolated",
    "fault_isolated",
    "liveness_isolated",
    "warn",
]
