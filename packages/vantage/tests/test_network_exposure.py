"""Threat matrix "Network exposure": binding wider than loopback with no
authentication in front of it (Phase 4) must warn, naming exactly what is
missing. The default binds nothing wider and warns about nothing -- a
warning on every normal start trains people to ignore the one that matters.
"""

from __future__ import annotations

import logging

import pytest
from vantage.service.cli import warn_if_bound_wide


def test_default_host_emits_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_bound_wide("127.0.0.1")

    assert caplog.records == []


def test_non_loopback_host_warns_naming_missing_authentication(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_bound_wide("0.0.0.0")  # noqa: S104

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("authentication" in message for message in messages)
    assert any("0.0.0.0" in message for message in messages)  # noqa: S104
