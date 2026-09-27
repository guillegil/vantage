"""Workspace-root conftest.

Registers the plugins the tests share: pytest's ``pytester``, which the
plugin tests use to run inner pytest sessions, and ``vantage_test_server``,
whose ``vantage_server`` fixture serves a real ``vantage`` server. They have
to be declared here: ``pytest_plugins`` in a package-level conftest.py fails
collection of the whole suite, because pytest accepts it only in a conftest
loaded before collection starts.

Every test also starts without ``VANTAGE_TOKEN`` and ``VANTAGE_PROJECT``:
an inner session or a `vantage push` inherits the environment, and a token
a developer exported for their own server would be refused by every test
server that has no user, as a project would by every test server that has
only `default`. A test that wants one sets it.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester", "vantage_test_server"]


@pytest.fixture(autouse=True)
def _no_ambient_vantage_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VANTAGE_TOKEN", raising=False)
    monkeypatch.delenv("VANTAGE_PROJECT", raising=False)
