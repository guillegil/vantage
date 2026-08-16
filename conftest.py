"""Workspace-root conftest.

pytest requires enabling its own ``pytester`` plugin from a genuine
top-level conftest.py -- a package-level one is rejected (and, from PR10
onward, needed by ``packages/pytest-vantage/tests/test_opt_in.py`` and
``test_xdist_guard.py`` to run other pytest sessions under test). It has no
effect beyond making the ``pytester`` fixture available; nothing else in
the workspace requests it.
"""

from __future__ import annotations

pytest_plugins = ["pytester"]
