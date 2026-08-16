"""Fault isolation for the reporting path (design.md D6, RQ-21, RQ-37).

Two things live here because design.md names one helper for both: `_warn`
emits a session warning that names what went wrong and never lets pytest's
own warning-as-error configuration turn a recording failure into an
unhandled exception. `fault_isolated` is a decorator applied to every
`Recorder` hook so an `Exception` raised while reporting becomes exactly one
`_warn` call instead of propagating into pytest's hook-calling machinery.

`plugin.py`'s preflight (task 6.8) and `Recorder`'s hooks (task 6.10) both
call `_warn` -- the preflight failing before anything is registered, and a
hook failing after registration, are two different moments but the same
kind of event from the user's point of view: recording did not happen, say
so once, and let the suite finish exactly as it would have otherwise.
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


def _warn(config: pytest.Config, message: str) -> None:
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


def fault_isolated(hook: F) -> F:
    """Wrap a `Recorder` hook so any `Exception` it raises becomes exactly
    one warning instead of propagating (design.md D6, RQ-21).

    Catches `Exception`, never `BaseException` -- `KeyboardInterrupt` and
    `SystemExit` must still propagate through pytest's own `wrap_session`
    (RQ-31 depends on a real SIGINT still reaching it). Latches on first
    failure: once one wrapped call has warned, `self._disabled` makes every
    later call on the same `Recorder` instance a silent no-op that does not
    even invoke the hook body again -- this is what keeps a session where
    several hooks fail at exactly one warning (RQ-21's "every hook is
    fault-isolated" scenario), not one per failing hook.

    Never assigns `session.exitstatus`. Catching the exception here and
    simply not re-raising it *is* the entire mechanism -- nothing in this
    module, or the hook it wraps, touches the suite's verdict.
    """

    @functools.wraps(hook)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self._disabled:
            return None
        try:
            return hook(self, *args, **kwargs)
        except Exception as exc:  # deliberately broad, never BaseException -- RQ-21
            self._disabled = True
            _warn(self._config, f"vantage: error while reporting: {exc}")
            return None

    return wrapper  # type: ignore[return-value]


__all__ = ["VantageWarning", "_warn", "fault_isolated"]
