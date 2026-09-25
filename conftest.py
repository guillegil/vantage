"""Workspace-root conftest.

Registers the plugins the tests share: pytest's ``pytester``, which the
plugin tests use to run inner pytest sessions, and ``vantage_test_server``,
whose ``vantage_server`` fixture serves a real ``vantage`` server. They have
to be declared here: ``pytest_plugins`` in a package-level conftest.py fails
collection of the whole suite, because pytest accepts it only in a conftest
loaded before collection starts.
"""

from __future__ import annotations

pytest_plugins = ["pytester", "vantage_test_server"]
