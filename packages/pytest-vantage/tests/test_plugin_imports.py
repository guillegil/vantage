"""`pytest_vantage` imports only the standard library or `pytest`, so
installing it adds no third-party dependency. The plugin's own code never
opens the vantage database: it reports over HTTP with `urllib`, and the
local modes hand a run to `vantage.local` -- imported from one module, inside
a function, and only when such a mode is configured.

Reuses `packages/vantage/tests/importwalk.py`, parameterised by
`allowed_top_levels`, rather than duplicating the walker. The walker visits
imports inside functions too, so the recording modules the plugin imports
lazily are held to the same rule.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from importwalk import walk_package
from vantage_test_server import VantageTestServer

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "packages" / "pytest-vantage" / "src"
_PLUGIN_DIR = _SRC_ROOT / "pytest_vantage"
_ALLOWED = frozenset(sys.stdlib_module_names) | {"pytest"}

# The plugin's one import from outside the standard library and pytest.
_LOCAL_STORAGE_MODULE = "pytest_vantage/local.py"
_LOCAL_STORAGE_IMPORT = "vantage.local"


def test_every_plugin_import_resolves_to_stdlib_or_pytest_but_the_local_storage_one() -> None:
    result = walk_package(
        _PLUGIN_DIR,
        src_root=_SRC_ROOT,
        allowed_top_levels=_ALLOWED,
        allowed_internal_prefixes=("pytest_vantage",),
    )

    outside = {(v.file.relative_to(_SRC_ROOT).as_posix(), v.imported) for v in result.violations}
    assert outside == {(_LOCAL_STORAGE_MODULE, _LOCAL_STORAGE_IMPORT)}


def test_the_local_storage_import_is_made_inside_a_function_only() -> None:
    """At module level, importing the plugin's local-storage door would
    import `vantage`, and fail where it is not installed."""
    tree = ast.parse((_PLUGIN_DIR / "local.py").read_text(encoding="utf-8"))
    in_functions = {
        id(node)
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
    }
    imports_of_vantage = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        and any(alias.name.partition(".")[0] == "vantage" for alias in node.names)
        or isinstance(node, ast.ImportFrom)
        and (node.module or "").partition(".")[0] == "vantage"
    ]

    assert imports_of_vantage
    assert all(id(node) in in_functions for node in imports_of_vantage)


def test_a_session_recording_to_a_server_never_loads_local_storage(
    pytester: pytest.Pytester, vantage_server: VantageTestServer
) -> None:
    """Nothing but a local mode imports `vantage.local`, the outbox or the
    module between them. A fresh subprocess, because this process has
    imported all of them."""
    pytester.makeconftest(
        """
        import json
        import sys


        def pytest_unconfigure(config):
            loaded = sorted(
                name for name in sys.modules
                if name.startswith(("pytest_vantage", "vantage.local"))
            )
            with open("loaded_modules.json", "w") as handle:
                json.dump(loaded, handle)
        """
    )
    pytester.makepyfile(test_sample="def test_it():\n    assert True\n")

    result = pytester.runpytest_subprocess(
        "--vantage", f"--vantage-server={vantage_server.address}"
    )

    result.assert_outcomes(passed=1)
    assert len(vantage_server.executions()) == 1
    loaded = set(json.loads((pytester.path / "loaded_modules.json").read_text()))
    assert "pytest_vantage.recorder" in loaded
    assert not loaded & {"pytest_vantage.local", "pytest_vantage.outbox", "vantage.local"}


def test_the_walk_is_not_vacuous() -> None:
    result = walk_package(
        _PLUGIN_DIR,
        src_root=_SRC_ROOT,
        allowed_top_levels=_ALLOWED,
        allowed_internal_prefixes=("pytest_vantage",),
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
