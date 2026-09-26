"""Each internal package of `vantage` imports only what its layer allows.

`vantage.core` imports the standard library and nothing else -- and not even
the standard library's database, network or process modules, because the
core describes the domain and talking to the outside world is an adapter's
job. `vantage.storage` imports the standard library and the core, so it can
never reach the service or a third-party package -- except
`vantage.storage.postgres`, which alone may import the PostgreSQL driver the
optional `postgres` extra installs, so that an install without it still
imports and serves SQLite.

`vantage.ingestion` turns a report into rows for the server and for the
local store alike, so it takes Pydantic and PyYAML, which every install
has, and never the web framework, which only the `server` extra brings, nor
a storage adapter: the store is handed in. `vantage.service` is the only
package that imports the web framework.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

from importwalk import WalkResult, walk_package

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "packages" / "vantage" / "src"
_CORE_DIR = _SRC_ROOT / "vantage" / "core"
_STORAGE_DIR = _SRC_ROOT / "vantage" / "storage"
_INGESTION_DIR = _SRC_ROOT / "vantage" / "ingestion"
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

_POSTGRES_ADAPTER = "vantage.storage.postgres"
_POSTGRES_DRIVER = frozenset({"psycopg", "psycopg_pool"})

# What the `server` extra brings: FastAPI, the Starlette it is built on, and
# the ASGI server.
_WEB_FRAMEWORK = frozenset({"fastapi", "starlette", "uvicorn"})


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
        allowed_top_levels_within={_POSTGRES_ADAPTER: _POSTGRES_DRIVER},
    )


def _walk_ingestion() -> WalkResult:
    return walk_package(
        _INGESTION_DIR,
        src_root=_SRC_ROOT,
        allowed_top_levels=_STDLIB | {"pydantic", "yaml"},
        allowed_internal_prefixes=("vantage.core", "vantage.ingestion"),
    )


def _examined(result: WalkResult) -> set[str]:
    return {p.relative_to(_SRC_ROOT).as_posix() for p in result.modules_examined}


def test_every_core_import_resolves_to_the_standard_library_without_io() -> None:
    result = _walk_core()

    assert result.is_clean, [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in result.violations
    ]


def test_every_storage_import_resolves_to_the_standard_library_or_the_core() -> None:
    """The PostgreSQL adapter may import its driver as well, and nothing
    else in storage may."""
    result = _walk_storage()

    assert result.is_clean, [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in result.violations
    ]


def test_every_ingestion_import_resolves_to_the_standard_library_the_core_or_validation() -> None:
    """Pydantic and PyYAML, and nothing that serves HTTP or opens a store."""
    result = _walk_ingestion()

    assert result.is_clean, [
        f"{v.file}:{v.lineno} imports {v.imported!r}" for v in result.violations
    ]


def _web_framework_importers() -> set[str]:
    """Every module under `vantage` with an import of the web framework,
    those inside functions included."""
    importers: set[str] = set()
    for file in sorted((_SRC_ROOT / "vantage").rglob("*.py")):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"), filename=str(file))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            if any(name.partition(".")[0] in _WEB_FRAMEWORK for name in names):
                importers.add(file.relative_to(_SRC_ROOT).as_posix())
    return importers


def test_the_service_is_the_only_package_that_imports_the_web_framework() -> None:
    importers = _web_framework_importers()

    assert "vantage/service/app.py" in importers
    assert [path for path in importers if not path.startswith("vantage/service/")] == []


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
    assert "vantage/storage/postgres/connection.py" in examined
    assert "vantage/storage/postgres/store.py" in examined


def test_the_ingestion_walk_is_not_vacuous() -> None:
    examined = _examined(_walk_ingestion())

    assert "vantage/ingestion/__init__.py" in examined
    assert "vantage/ingestion/conversion.py" in examined
    assert "vantage/ingestion/metadata_parse.py" in examined
    assert "vantage/ingestion/schemas.py" in examined


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


def test_the_driver_is_allowed_to_the_adapter_given_it_and_nothing_beside_it(
    tmp_path: Path,
) -> None:
    """The storage shape with a PostgreSQL adapter: its package and modules
    may import the driver; a sibling module, one whose name merely starts
    the same, and the storage package itself may not."""
    src_root = tmp_path / "src"
    storage_dir = src_root / "fakepkg" / "storage"
    adapter_dir = storage_dir / "postgres"
    adapter_dir.mkdir(parents=True)
    driver_imports = "import psycopg\nfrom psycopg_pool import ConnectionPool\n"
    (src_root / "fakepkg" / "__init__.py").write_text("")
    (storage_dir / "__init__.py").write_text("import psycopg\n")
    (storage_dir / "sqlite_store.py").write_text(driver_imports)
    (storage_dir / "postgres_notes.py").write_text("import psycopg\n")
    (adapter_dir / "__init__.py").write_text(driver_imports)
    (adapter_dir / "store.py").write_text(driver_imports)

    result = walk_package(
        storage_dir,
        src_root=src_root,
        allowed_top_levels=_STDLIB,
        allowed_internal_prefixes=("fakepkg.core", "fakepkg.storage"),
        allowed_top_levels_within={"fakepkg.storage.postgres": _POSTGRES_DRIVER},
    )

    assert sorted((v.file.name, v.imported) for v in result.violations) == [
        ("__init__.py", "psycopg"),
        ("postgres_notes.py", "psycopg"),
        ("sqlite_store.py", "psycopg"),
        ("sqlite_store.py", "psycopg_pool"),
    ]


_WITHOUT_THE_DRIVER = """
import sys

for name in ("psycopg", "psycopg_pool"):
    sys.modules[name] = None

import vantage.service.app
import vantage.service.cli
import vantage.storage
import vantage.storage.sqlite_store

assert "vantage.storage.postgres" not in sys.modules, "the PostgreSQL adapter was loaded"

try:
    import vantage.storage.postgres
except ImportError as exc:
    print(exc.name)
"""


def test_storage_and_the_command_import_without_the_postgresql_driver() -> None:
    """A SQLite-only installation has no psycopg: nothing a SQLite server
    loads may import the adapter, which is where the driver is imported, and
    asking for the adapter there fails on the missing driver, by name."""
    completed = subprocess.run(  # noqa: S603 -- the interpreter running this test
        [sys.executable, "-c", _WITHOUT_THE_DRIVER],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["psycopg"]


_WITHOUT_THE_WEB_FRAMEWORK = """
import sys

for name in ("fastapi", "starlette", "uvicorn"):
    sys.modules[name] = None

import vantage.ingestion
import vantage.ingestion.decode

try:
    import vantage.service.app
except ImportError as exc:
    print(exc.name)
"""


def test_ingestion_imports_without_the_web_framework() -> None:
    """An install without the `server` extra has no FastAPI: ingestion must
    import there, and the app, asked for, fails on the missing framework."""
    completed = subprocess.run(  # noqa: S603 -- the interpreter running this test
        [sys.executable, "-c", _WITHOUT_THE_WEB_FRAMEWORK],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["fastapi"]
