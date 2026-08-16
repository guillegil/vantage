"""Guard: the workspace root ``pyproject.toml`` is the only file that may
declare ``[tool.pytest.ini_options]`` (D9).

pytest resolves exactly one ini file per rootdir. A package ``pyproject.toml``
that declared its own ``[tool.pytest.ini_options]`` would silently become
*that* file when pytest is invoked from inside the package directory, and
every ``@pytest.mark.req`` in that package would then fail collection under
``--strict-markers``.

This is a text scan, not a TOML parse: ``tomllib`` does not exist on the
Python 3.10 floor and ``tomli`` is a third-party backport (RQ-24).
"""

from __future__ import annotations

from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SECTION = "[tool.pytest.ini_options]"
# Two published distributions (ADR-4): pytest-vantage and vantage.
EXPECTED_PACKAGE_COUNT = 2


def _package_pyproject_files() -> list[Path]:
    return sorted(WORKSPACE_ROOT.glob("packages/*/pyproject.toml"))


def test_only_the_workspace_root_declares_pytest_ini_options() -> None:
    package_files = _package_pyproject_files()

    # The guard must not pass having scanned nothing -- the same vacuity
    # failure `test_core_package_is_not_empty` (Phase B) exists to prevent
    # for the architecture test.
    assert len(package_files) == EXPECTED_PACKAGE_COUNT, (
        f"expected {EXPECTED_PACKAGE_COUNT} package pyproject.toml files, "
        f"found {len(package_files)}: {package_files}"
    )

    offenders: list[Path] = []
    scanned = 0
    for path in package_files:
        content = path.read_text(encoding="utf-8")
        scanned += 1
        if FORBIDDEN_SECTION in content:
            offenders.append(path)

    # Ties "found" to "read": the guard must have actually opened every
    # file it claims to have scanned, not merely resolved their paths.
    assert scanned == EXPECTED_PACKAGE_COUNT, (
        f"expected to read {EXPECTED_PACKAGE_COUNT} package pyproject.toml "
        f"files, actually read {scanned}"
    )

    assert not offenders, (
        f"{FORBIDDEN_SECTION} must appear only in the workspace root "
        f"pyproject.toml (D9), but was found in: {offenders}"
    )
