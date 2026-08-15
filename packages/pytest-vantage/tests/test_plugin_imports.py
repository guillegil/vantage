"""RQ-24 criterion 2: `pytest_vantage` imports only the standard library or
`pytest`. Under ADR-9 this plugin never opens a database and never imports
the server packages -- it reports over HTTP with `urllib`.

Reuses `packages/vantage/tests/importwalk.py` (design.md, D10) rather than
duplicating the walker -- PR1 built it parameterised by `allowed_top_levels`
for exactly this second use.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from importwalk import walk_package

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "packages" / "pytest-vantage" / "src"
_PLUGIN_DIR = _SRC_ROOT / "pytest_vantage"
_ALLOWED = frozenset(sys.stdlib_module_names) | {"pytest"}


@pytest.mark.req("RQ-24")
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


@pytest.mark.req("RQ-24")
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
