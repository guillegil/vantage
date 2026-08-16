"""Fault isolation for the reporting path (design.md D6, RQ-21, RQ-37).

`_warn` emits a session warning that names what went wrong and never lets
pytest's own warning-as-error configuration turn a recording failure into an
unhandled exception. `plugin.py`'s preflight (task 6.8) calls it when the
server cannot be reached at all, before anything is registered.

A second piece lands here in task 6.10: a decorator applied to every
`Recorder` hook so an `Exception` raised while reporting -- after
registration, not before -- becomes exactly one more `_warn` call instead of
propagating into pytest's hook-calling machinery (RQ-21). Both moments call
the same helper because, from the user's point of view, they are the same
kind of event: recording did not happen, say so once, and let the suite
finish exactly as it would have otherwise.
"""

from __future__ import annotations

import sys
import warnings

import pytest


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


__all__ = ["VantageWarning", "_warn"]
