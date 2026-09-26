"""Each internal package of `vantage` imports only what its layer allows.

`vantage.core` imports the standard library and nothing else -- and not even
the standard library's database, network or process modules, because the
core describes the domain and talking to the outside world is an adapter's
job. `vantage.storage` imports the standard library and the core, so it can
never reach the service or a third-party package.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from importwalk import WalkResult, walk_package

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "packages" / "vantage" / "src"
_CORE_DIR = _SRC_ROOT / "vantage" / "core"
_STORAGE_DIR = _SRC_ROOT / "vantage" / "storage"
_STDLIB = frozenset(sys.stdlib_module_names)

# Standard-library modules that open a database, a socket or a process.
_IO_MODULES = frozenset(
    {
        "asyncio",
        "dbm",
        "ftplib",
        "http",
        "imaplib",
        "multiprocessing",
        "poplib",
        "select",
        "selectors",
        "shelve",
        "smtplib",
        "socket",
        "socketserver",
        "sqlite3",
        "ssl",
        "subprocess",
        "urllib",
        "wsgiref",
        "xmlrpc",
    }
)
_CORE_ALLOWED = _STDLIB - _IO_MODULES


def _walk_core() -> WalkResult:
    return walk_package(
        _CORE_DIR,
        src_root=_SRC_ROOT,
        allowed_top_levels=_CORE_ALLOWED,
        allowed_internal_prefixes=("vantage.core",),
    )


def _walk_storage() -> WalkResult:
    return walk_package(
        _STORAGE_DIR,
        src_root=_SRC_ROOT,
        allowed_top_levels=_STDLIB,
        allowed_internal_prefixes=("vantage.core", "vantage.storage"),
    )


def _examined(result: WalkResult) -> set[str]:
    return {p.relative_to(_SRC_ROOT).as_posix() for p in result.modules_examined}


def test_every_core_import_resolves_to_the_standard_library_without_io() -> None:
    result = _walk_core()

    assert result.is_clean, [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in result.violations
    ]


def test_every_storage_import_resolves_to_the_standard_library_or_the_core() -> None:
    result = _walk_storage()

    assert result.is_clean, [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in result.violations
    ]


def test_the_core_walk_is_not_vacuous() -> None:
    examined = _examined(_walk_core())

    assert "vantage/core/domain/execution.py" in examined
    assert "vantage/core/domain/metadata.py" in examined
    assert "vantage/core/ports/storage.py" in examined


def test_the_storage_walk_is_not_vacuous() -> None:
    examined = _examined(_walk_storage())

    assert "vantage/storage/connection.py" in examined
    assert "vantage/storage/sqlite_store.py" in examined


def test_every_io_module_kept_out_of_the_core_is_a_real_stdlib_module() -> None:
    """A misspelt name would silently let the real module through."""
    assert _IO_MODULES <= _STDLIB, sorted(_IO_MODULES - _STDLIB)


def test_the_walk_rejects_a_relative_import_into_a_sibling_subpackage(tmp_path: Path) -> None:
    """A relative import (`level > 0`) is not allowed merely for being
    relative.

    A synthetic package stands in for `vantage.core`/`vantage.storage`:
    `fakepkg/core/leaf.py` reaches for `fakepkg/storage.py` with
    `from ..storage import X`, the sibling-subpackage coupling the core must
    never have.
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
        allowed_internal_prefixes=("fakepkg.core",),
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
        allowed_internal_prefixes=("fakepkg.core",),
    )

    assert result.is_clean, [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in result.violations
    ]


def test_the_walk_allows_each_listed_prefix_and_nothing_beside_them(tmp_path: Path) -> None:
    """The storage shape: a second internal prefix admits the core, but not
    the service next to it, whether the import is relative or absolute.
    """
    src_root = tmp_path / "src"
    storage_dir = src_root / "fakepkg" / "storage"
    storage_dir.mkdir(parents=True)
    (src_root / "fakepkg" / "__init__.py").write_text("")
    (storage_dir / "__init__.py").write_text("")
    (storage_dir / "leaf.py").write_text(
        textwrap.dedent(
            """
            from ..core import Allowed
            from fakepkg.core.port import AlsoAllowed
            from ..service import Sneaky
            from fakepkg.service.app import AlsoSneaky
            """
        )
    )

    result = walk_package(
        storage_dir,
        src_root=src_root,
        allowed_top_levels=_STDLIB,
        allowed_internal_prefixes=("fakepkg.core", "fakepkg.storage"),
    )

    assert [v.imported for v in result.violations] == ["fakepkg.service", "fakepkg.service.app"]
