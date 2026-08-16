"""RQ-26: `vantage.core` imports only the standard library.

Also covers RQ-30.2 as a corollary -- an import walk that rejects every
non-stdlib import necessarily rejects an import of either storage
implementation, since neither is stdlib (design.md, D10).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from importwalk import walk_package

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "packages" / "vantage" / "src"
_CORE_DIR = _SRC_ROOT / "vantage" / "core"
_STDLIB = frozenset(sys.stdlib_module_names)


@pytest.mark.req("RQ-26")
@pytest.mark.req("RQ-30")
def test_every_core_import_resolves_to_the_standard_library() -> None:
    result = walk_package(
        _CORE_DIR,
        src_root=_SRC_ROOT,
        allowed_top_levels=_STDLIB,
        allowed_internal_prefix="vantage.core",
    )

    assert result.is_clean, [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in result.violations
    ]


@pytest.mark.req("RQ-26")
def test_the_walk_is_not_vacuous() -> None:
    result = walk_package(
        _CORE_DIR,
        src_root=_SRC_ROOT,
        allowed_top_levels=_STDLIB,
        allowed_internal_prefix="vantage.core",
    )

    examined_relative = {p.relative_to(_SRC_ROOT).as_posix() for p in result.modules_examined}

    assert len(result.modules_examined) >= 3
    assert "vantage/core/domain/execution.py" in examined_relative
    assert "vantage/core/ports/storage.py" in examined_relative


def test_the_walk_rejects_a_relative_import_into_a_sibling_subpackage(tmp_path: Path) -> None:
    """`level > 0` alone is not sufficient permission (design.md, D10).

    A synthetic package stands in for `vantage.core`/`vantage.storage`:
    `fakepkg/core/leaf.py` reaches for `fakepkg/storage.py` with
    `from ..storage import X`, which is exactly the sibling-subpackage
    coupling RQ-26/RQ-30.2 exist to forbid.
    """
    src_root = tmp_path / "src"
    core_dir = src_root / "fakepkg" / "core"
    core_dir.mkdir(parents=True)
    (src_root / "fakepkg" / "__init__.py").write_text("")
    (core_dir / "__init__.py").write_text("")
    (core_dir / "leaf.py").write_text(
        textwrap.dedent(
            """
            from ..storage import Sneaky
            """
        )
    )
    (src_root / "fakepkg" / "storage.py").write_text("class Sneaky: ...\n")

    result = walk_package(
        core_dir,
        src_root=src_root,
        allowed_top_levels=_STDLIB,
        allowed_internal_prefix="fakepkg.core",
    )

    assert not result.is_clean
    assert result.violations[0].imported == "fakepkg.storage"


def test_the_walk_allows_a_relative_import_within_the_same_package(tmp_path: Path) -> None:
    """Triangulates the previous case: a legitimate intra-package import is not flagged."""
    src_root = tmp_path / "src"
    core_dir = src_root / "fakepkg" / "core"
    core_dir.mkdir(parents=True)
    (src_root / "fakepkg" / "__init__.py").write_text("")
    (core_dir / "__init__.py").write_text("")
    (core_dir / "sibling.py").write_text("VALUE = 1\n")
    (core_dir / "leaf.py").write_text(
        textwrap.dedent(
            """
            from .sibling import VALUE
            """
        )
    )

    result = walk_package(
        core_dir,
        src_root=src_root,
        allowed_top_levels=_STDLIB,
        allowed_internal_prefix="fakepkg.core",
    )

    assert result.is_clean, [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in result.violations
    ]
