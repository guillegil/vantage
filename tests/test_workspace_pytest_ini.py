"""Guard: the workspace root ``pyproject.toml`` is the only pytest config file.

pytest uses a single config file: walking up from the directory its path
arguments share, or from the invocation directory when there are none, it
takes the first file that counts. A config file anywhere under ``packages/``
therefore replaces the root's for any run on that directory or below it --
``pytest packages/<pkg>`` from the workspace root included -- and the root's
settings, ``--strict-markers`` and the ``slow`` marker declaration among them,
silently stop applying.

This is a text scan, not a TOML parse: ``tomllib`` does not exist on
Python 3.10 and the project declares no dependency on the ``tomli`` backport.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("pytest-vantage", "vantage")

# pytest takes these as its config even when they are empty.
_ALWAYS_CONFIG = frozenset({"pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini"})
# pytest takes these only when they carry its section. For pyproject.toml that
# is a `[tool.pytest]` or `[tool.pytest.ini_options]` table, with the header
# spaced or quoted as TOML allows, or a top-level `tool.pytest...` dotted key.
_Q = "[\"']?"
_SECTION_CONFIG = {
    "pyproject.toml": re.compile(rf"^\s*\[?\s*{_Q}tool{_Q}\s*\.\s*{_Q}pytest{_Q}\s*[.\]=]", re.M),
    "tox.ini": re.compile(r"^\[pytest\]", re.M),
    "setup.cfg": re.compile(r"^\[tool:pytest\]", re.M),
}
# Environments, caches and build output: never checked in, and an installed
# package's own test data could carry any of the names above.
_SKIPPED_DIRS = frozenset(
    {".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist"}
)


def _pytest_configs_under_packages(root: Path) -> list[Path]:
    """Every file under ``root / "packages"`` that pytest would take as its config."""
    found: list[Path] = []
    for directory, subdirs, files in os.walk(root / "packages"):
        subdirs[:] = sorted(d for d in subdirs if d not in _SKIPPED_DIRS)
        for name in sorted(files):
            path = Path(directory, name)
            section = _SECTION_CONFIG.get(name)
            if name in _ALWAYS_CONFIG or (
                section is not None and section.search(path.read_text(encoding="utf-8"))
            ):
                found.append(path)
    return found


def test_only_the_workspace_root_configures_pytest() -> None:
    # The guard must not pass having scanned nothing.
    for package in PACKAGES:
        assert (WORKSPACE_ROOT / "packages" / package / "pyproject.toml").is_file()

    offenders = _pytest_configs_under_packages(WORKSPACE_ROOT)

    assert not offenders, (
        "only the workspace root pyproject.toml may configure pytest, but these would "
        f"replace it for runs below them: {offenders}"
    )


_PROJECT_TABLE = '[project]\nname = "{name}"\n'


def _workspace_skeleton(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "--strict-markers"\n', encoding="utf-8"
    )
    for package in PACKAGES:
        (root / "packages" / package / "tests").mkdir(parents=True)
        (root / "packages" / package / "pyproject.toml").write_text(
            _PROJECT_TABLE.format(name=package), encoding="utf-8"
        )


_PLUGIN = "packages/pytest-vantage"
_PLUGIN_PROJECT = _PROJECT_TABLE.format(name="pytest-vantage")
# Each file, on its own, replaces the root config for runs under its directory.
_SHADOWING_FILES = {
    "tool.pytest": (
        f"{_PLUGIN}/pyproject.toml",
        f'{_PLUGIN_PROJECT}\n[tool.pytest]\naddopts = ["-q"]\n',
    ),
    "tool.pytest.ini_options": (
        f"{_PLUGIN}/pyproject.toml",
        f'{_PLUGIN_PROJECT}\n[tool.pytest.ini_options]\naddopts = "-q"\n',
    ),
    "spaced-header": (
        f"{_PLUGIN}/pyproject.toml",
        f'{_PLUGIN_PROJECT}\n[ tool . "pytest" . ini_options ]\naddopts = "-q"\n',
    ),
    "dotted-key": (
        f"{_PLUGIN}/pyproject.toml",
        f'tool.pytest.ini_options.addopts = "-q"\n\n{_PLUGIN_PROJECT}',
    ),
    "pytest.toml": (f"{_PLUGIN}/pytest.toml", ""),
    ".pytest.toml": (f"{_PLUGIN}/.pytest.toml", ""),
    "pytest.ini": (f"{_PLUGIN}/pytest.ini", ""),
    ".pytest.ini": (f"{_PLUGIN}/.pytest.ini", ""),
    "tox.ini": (f"{_PLUGIN}/tox.ini", "[tox]\nenvlist = py310\n\n[pytest]\naddopts = -q\n"),
    "setup.cfg": (f"{_PLUGIN}/setup.cfg", "[tool:pytest]\naddopts = -q\n"),
    "below-the-package": (f"{_PLUGIN}/tests/pytest.ini", ""),
}


@pytest.mark.parametrize(
    ("relative_path", "text"), list(_SHADOWING_FILES.values()), ids=list(_SHADOWING_FILES)
)
def test_every_config_file_pytest_honours_is_found(
    tmp_path: Path, relative_path: str, text: str
) -> None:
    _workspace_skeleton(tmp_path)
    shadow = tmp_path / relative_path
    shadow.write_text(text, encoding="utf-8")

    assert _pytest_configs_under_packages(tmp_path) == [shadow]


def test_files_without_a_pytest_section_are_not_reported(tmp_path: Path) -> None:
    _workspace_skeleton(tmp_path)
    plugin = tmp_path / _PLUGIN
    (plugin / "pyproject.toml").write_text(
        f"{_PLUGIN_PROJECT}\n# pytest settings live in [tool.pytest.ini_options] at the root.\n"
        '[tool.pytest_other]\nkey = "value"\n',
        encoding="utf-8",
    )
    (plugin / "tox.ini").write_text("[tox]\nenvlist = py310\n", encoding="utf-8")
    (plugin / "setup.cfg").write_text("[metadata]\nname = pytest-vantage\n", encoding="utf-8")

    assert _pytest_configs_under_packages(tmp_path) == []
