"""Each internal package of `vantage` imports only what its layer allows.

`vantage.core` imports the standard library and nothing else -- and not even
the standard library's database, network or process modules, because the
core describes the domain and talking to the outside world is an adapter's
job. `vantage.storage` imports the standard library and the core, so it can
never reach the service or a third-party package -- except
`vantage.storage.postgres`, which alone may import the PostgreSQL driver the
optional `postgres` extra installs, so that an install without it still
imports and serves SQLite.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from importwalk import WalkResult, walk_package

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "packages" / "vantage" / "src"
_CORE_DIR = _SRC_ROOT / "vantage" / "core"
_STORAGE_DIR = _SRC_ROOT / "vantage" / "storage"
_POSTGRES_DIR = _STORAGE_DIR / "postgres"
_STDLIB = frozenset(sys.stdlib_module_names)
_POSTGRES_DRIVER = frozenset({"psycopg", "psycopg_pool"})

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


def _walk_postgres() -> WalkResult:
    return walk_package(
        _POSTGRES_DIR,
        src_root=_SRC_ROOT,
        allowed_top_levels=_STDLIB | _POSTGRES_DRIVER,
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
    """The PostgreSQL subpackage aside, which the next test holds to its own
    rule."""
    result = _walk_storage()

    outside_postgres = [v for v in result.violations if not v.file.is_relative_to(_POSTGRES_DIR)]
    assert outside_postgres == [], [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in outside_postgres
    ]


def test_the_postgres_adapter_imports_nothing_else_but_its_driver() -> None:
    result = _walk_postgres()

    assert result.is_clean, [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in result.violations
    ]


# Run in a fresh interpreter: this one has imported the driver already.
_IMPORT_WITHOUT_DRIVER = """
import sys
sys.modules["psycopg"] = None
sys.modules["psycopg_pool"] = None
import vantage.storage
import vantage.storage.sqlite_store
import vantage.service.cli
try:
    import vantage.storage.postgres
except ImportError as exc:
    print(exc.name)
"""


def test_without_the_driver_everything_but_the_postgres_adapter_imports() -> None:
    """An install without the `postgres` extra serves SQLite; asking for
    PostgreSQL there fails on the missing driver, by name."""
    completed = subprocess.run(  # noqa: S603 -- the interpreter running this test
        [sys.executable, "-c", _IMPORT_WITHOUT_DRIVER],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["psycopg"]


def test_the_core_walk_is_not_vacuous() -> None:
    examined = _examined(_walk_core())

    assert "vantage/core/domain/execution.py" in examined
    assert "vantage/core/domain/metadata.py" in examined
    assert "vantage/core/ports/storage.py" in examined


def test_the_storage_walk_is_not_vacuous() -> None:
    examined = _examined(_walk_storage())

    assert "vantage/storage/connection.py" in examined
    assert "vantage/storage/sqlite_store.py" in examined
    assert "vantage/storage/version.py" in examined


def test_the_postgres_walk_is_not_vacuous() -> None:
    examined = _examined(_walk_postgres())

    assert "vantage/storage/postgres/connection.py" in examined
    assert "vantage/storage/postgres/store.py" in examined


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
