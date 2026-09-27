"""Reads the `VantageWarning`s out of a pytest run's warnings summary.

A warning the plugin raises where pytest records nothing -- its
`pytest_configure`, `pytest_sessionstart` -- is issued into the session's
own warnings summary, so a test running a session reads it there, from the
session's output, whether it ran in this process or another.
"""

from __future__ import annotations

import re

import pytest

_VANTAGE_WARNING = re.compile(r"VantageWarning: (.*)$")


def warnings_summary(result: pytest.RunResult) -> str:
    """The warnings summary section of `result`'s output, or `""`."""
    output = result.stdout.str()
    start = output.find("warnings summary")
    if start == -1:
        return ""
    end = output.find("-- Docs:", start)
    return output[start:] if end == -1 else output[start:end]


def vantage_warnings(result: pytest.RunResult) -> list[str]:
    """The message of every `VantageWarning` in `result`'s warnings summary,
    in the order listed."""
    return [
        match.group(1)
        for line in warnings_summary(result).splitlines()
        if (match := _VANTAGE_WARNING.search(line))
    ]
