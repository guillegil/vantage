"""`pytest_vantage` imports only the standard library or `pytest`, so
installing it adds no third-party dependency. The plugin never opens a
database and never imports the server packages -- it reports over HTTP with
`urllib`.

Reuses `packages/vantage/tests/importwalk.py`, parameterised by
`allowed_top_levels`, rather than duplicating the walker. The walker visits
imports inside functions too, so the recording modules the plugin imports
lazily are held to the same rule.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from importwalk import walk_package

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "packages" / "pytest-vantage" / "src"
_PLUGIN_DIR = _SRC_ROOT / "pytest_vantage"
_ALLOWED = frozenset(sys.stdlib_module_names) | {"pytest"}


def test_every_plugin_import_resolves_to_stdlib_or_pytest() -> None:
    result = walk_package(
        _PLUGIN_DIR,
        src_root=_SRC_ROOT,
        allowed_top_levels=_ALLOWED,
        allowed_internal_prefix="pytest_vantage",
    )

    assert result.is_clean, [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in result.violations
    ]


def test_the_walk_is_not_vacuous() -> None:
    result = walk_package(
        _PLUGIN_DIR,
        src_root=_SRC_ROOT,
        allowed_top_levels=_ALLOWED,
        allowed_internal_prefix="pytest_vantage",
    )

    examined_relative = {p.relative_to(_SRC_ROOT).as_posix() for p in result.modules_examined}

    assert len(result.modules_examined) >= 3
    assert "pytest_vantage/plugin.py" in examined_relative
    assert "pytest_vantage/config.py" in examined_relative


def test_an_unactivated_session_imports_only_the_lightweight_modules(
    pytester: pytest.Pytester,
) -> None:
    """pytest imports the plugin on every invocation, so everything the
    recorder needs -- HTTP transport, git and metadata capture, report
    assembly -- is imported only once ``--vantage`` asks for it. A fresh
    subprocess, because this process has long since imported all of it.
    """
    pytester.makeconftest(
        """
        import json
        import sys


        def pytest_sessionfinish(session):
            loaded = sorted(name for name in sys.modules if name.startswith("pytest_vantage"))
            with open("loaded_modules.json", "w") as handle:
                json.dump(loaded, handle)
        """
    )
    pytester.makepyfile(test_sample="def test_it():\n    assert True\n")

    result = pytester.runpytest_subprocess()

    result.assert_outcomes(passed=1)
    loaded = set(json.loads((pytester.path / "loaded_modules.json").read_text()))
    assert "pytest_vantage.plugin" in loaded
    assert loaded <= {
        "pytest_vantage",
        "pytest_vantage.boundary",
        "pytest_vantage.config",
        "pytest_vantage.plugin",
    }
