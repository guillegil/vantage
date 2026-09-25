"""Workspace-root conftest.

Enables pytest's ``pytester`` plugin, which the plugin tests use to run
inner pytest sessions. It has to be declared here: ``pytest_plugins`` in a
package-level conftest.py fails collection of the whole suite, because
pytest accepts it only in a conftest loaded before collection starts.
"""

from __future__ import annotations

pytest_plugins = ["pytester"]
