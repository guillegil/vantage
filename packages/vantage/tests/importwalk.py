"""Shared AST import walker for Vantage's static import boundaries.

Used by ``vantage``'s layer guards (``test_architecture.py``: the core and
the storage adapter) and ``pytest-vantage``'s zero-dependency guard (stdlib
or pytest). It lives outside either package's ``src/`` tree because it must
never ship in a wheel. Reached through the root
``pythonpath = ["packages/vantage/tests"]``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ImportViolation:
    """One import statement that fell outside the allow-list."""

    file: Path
    imported: str
    lineno: int


@dataclass(frozen=True, slots=True)
class WalkResult:
    """The outcome of walking every ``.py`` file under a package."""

    modules_examined: tuple[Path, ...]
    violations: tuple[ImportViolation, ...]

    @property
    def is_clean(self) -> bool:
        return not self.violations


def _module_dotted_name(file: Path, src_root: Path) -> str:
    """Return ``file``'s dotted module name relative to ``src_root``.

    ``__init__.py`` names the package it lives in, not a submodule of it.
    """
    relative_parts = list(file.relative_to(src_root).with_suffix("").parts)
    if relative_parts and relative_parts[-1] == "__init__":
        relative_parts = relative_parts[:-1]
    return ".".join(relative_parts)


def _containing_package(dotted_name: str, *, is_init: bool) -> str:
    """Return the package a module belongs to, for resolving relative imports."""
    if is_init or "." not in dotted_name:
        return dotted_name
    return dotted_name.rsplit(".", 1)[0]


def _resolve_relative(containing_package: str, level: int, module: str | None) -> str:
    """Resolve a relative ``ImportFrom`` (``level`` dots) against its own package.

    ``level=1`` (``from . import x``) targets the containing package itself;
    each further dot climbs one package upward. ``level > 0`` alone is NOT
    sufficient permission to allow the import -- the resolved target still
    has to land inside an allowed internal prefix.
    """
    parts = containing_package.split(".") if containing_package else []
    keep = max(len(parts) - (level - 1), 0)
    base = parts[:keep]
    if module:
        base = [*base, *module.split(".")]
    return ".".join(base)


def _is_allowed(
    resolved: str,
    *,
    is_relative: bool,
    allowed_top_levels: frozenset[str],
    allowed_internal_prefixes: tuple[str, ...],
) -> bool:
    if any(
        resolved == prefix or resolved.startswith(prefix + ".")
        for prefix in allowed_internal_prefixes
    ):
        return True
    if is_relative:
        # A relative import that lands outside every internal prefix reaches a
        # sibling (or further) subpackage -- never allowed, regardless of level.
        return False
    top_level = resolved.split(".")[0] if resolved else ""
    return top_level in allowed_top_levels


def walk_package(
    package_dir: Path,
    *,
    src_root: Path,
    allowed_top_levels: frozenset[str],
    allowed_internal_prefixes: tuple[str, ...] = (),
) -> WalkResult:
    """Walk every ``.py`` file under ``package_dir`` and report disallowed imports.

    ``allowed_top_levels`` gates absolute imports (e.g. the standard library,
    optionally plus ``pytest``). ``allowed_internal_prefixes`` gates both
    absolute and relative imports that resolve inside the package's own
    dependency-inward tree (e.g. ``("vantage.core", "vantage.storage")``).
    """
    modules_examined: list[Path] = []
    violations: list[ImportViolation] = []

    for file in sorted(package_dir.rglob("*.py")):
        modules_examined.append(file)
        dotted_name = _module_dotted_name(file, src_root)
        containing_package = _containing_package(dotted_name, is_init=file.name == "__init__.py")
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not _is_allowed(
                        alias.name,
                        is_relative=False,
                        allowed_top_levels=allowed_top_levels,
                        allowed_internal_prefixes=allowed_internal_prefixes,
                    ):
                        violations.append(ImportViolation(file, alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                is_relative = node.level > 0
                resolved = (
                    _resolve_relative(containing_package, node.level, node.module)
                    if is_relative
                    else (node.module or "")
                )
                if not _is_allowed(
                    resolved,
                    is_relative=is_relative,
                    allowed_top_levels=allowed_top_levels,
                    allowed_internal_prefixes=allowed_internal_prefixes,
                ):
                    violations.append(ImportViolation(file, resolved, node.lineno))

    return WalkResult(modules_examined=tuple(modules_examined), violations=tuple(violations))
