"""Fixtures for `pytest-vantage`'s own test suite.

Dev-only: this file is never packaged (RQ-24 constrains `src/`, not `tests/`,
matching the precedent set by `test_plugin_imports.py` importing the shared
`importwalk` module from `packages/vantage/tests`). The plugin's happy-path
tests are end-to-end (design.md D2a), which means a real `vantage` server is
needed here, not a stub -- `VantageTestServer` (`vantage_test_server.py`)
does the actual work; this file only wraps it as a fixture.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from vantage_test_server import VantageTestServer


@pytest.fixture
def vantage_server() -> Iterator[VantageTestServer]:
    server = VantageTestServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()
