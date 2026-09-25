"""Workspace-root conftest.

Enables pytest's ``pytester`` plugin, which the plugin tests use to run
inner pytest sessions. pytest only honours ``pytest_plugins`` in a
top-level conftest.py, so it cannot live in a package-level one.
"""

from __future__ import annotations

pytest_plugins = ["pytester"]
