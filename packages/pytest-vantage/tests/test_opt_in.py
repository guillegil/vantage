"""RQ-2: absence of ``--vantage`` is the plugin's fully inert default state.

**Differential, never absolute** (CLAUDE.md's RQ-2 trap): pytest itself writes
``.pytest_cache`` and ``__pycache__``, so "no file was created" can only be made
to pass by lying about what it checks. The test of record instead runs the same
project twice -- once bare, once with ``-p no:vantage`` (the control: pytest
with this plugin definitively absent) -- and asserts the two resulting project
trees are byte-identical: the same relative paths, and the same file content.

The stronger half is the socket-level assertion below: with no recording
option present, no connection is even *attempted* -- not "no data sent", no
socket opened at all. That is what proves inertness rather than politeness.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

_SAMPLE_TEST = "def test_it():\n    assert True\n"


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    """Map every file under ``root`` to its raw bytes, keyed by relative path."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run_pytest(cwd: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", *extra_args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _forbidden_create_connection(*args: object, **kwargs: object) -> tuple[object, ...]:
    raise AssertionError("socket.create_connection must not be called when --vantage is absent")


@pytest.mark.req(id="RQ-2")
def test_project_tree_is_byte_identical_with_plugin_absent(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No CLI option, ini value or env var present anywhere: a bare run and a
    ``-p no:vantage`` run of the identical project must leave byte-identical
    trees. ``-p no:vantage`` is the control -- it is pytest with the plugin
    definitively absent, so any difference the bare run introduces is
    something the plugin did.

    Two independent, freshly-written directories -- never a copy of one
    run's output directory into the other -- so neither run's own debug
    instrumentation (subprocess helpers routinely stash a captured
    ``stdout``/``stderr`` alongside the project) becomes a spurious
    asymmetry. Bytecode caching is disabled for both: a ``.pyc``'s header
    embeds the source's mtime, so two independently-written copies of the
    same source produce different ``.pyc`` bytes even though the plugin did
    nothing -- noise this test must not mistake for a plugin-caused
    difference.
    """
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")

    bare_root = tmp_path_factory.mktemp("vantage-rq2-bare")
    control_root = tmp_path_factory.mktemp("vantage-rq2-control")
    (bare_root / "test_sample.py").write_text(_SAMPLE_TEST)
    (control_root / "test_sample.py").write_text(_SAMPLE_TEST)

    bare = _run_pytest(bare_root)
    control = _run_pytest(control_root, "-p", "no:vantage")

    assert bare.returncode == 0, bare.stdout + bare.stderr
    assert control.returncode == 0, control.stdout + control.stderr
    assert _tree_snapshot(bare_root) == _tree_snapshot(control_root)


@pytest.mark.req(id="RQ-2")
def test_failure_text_opt_out_ini_alone_cannot_enable_capture(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """failure-evidence -> Capture opt-out under the opt-in rule -> A
    committed configuration file cannot enable capture on its own.

    RQ-2's own established differential (above), applied to the
    failure-text opt-out ini value specifically: with no invocation flag on
    either run -- neither `--vantage` nor `--vantage-no-failure-text` -- a
    committed `vantage_no_failure_text = true` ini value changes nothing.
    `Unknown config option` is asserted absent from the run that carries it,
    which is currently a genuine RED: the ini value is not yet a registered
    option (task 2.12 registers it), so pytest currently warns about it.
    Once registered, the project tree -- excluding the ini file itself,
    which is the one deliberate difference between the two runs -- must
    still be byte-identical.
    """
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")

    with_ini_root = tmp_path_factory.mktemp("vantage-failtext-with-ini")
    without_ini_root = tmp_path_factory.mktemp("vantage-failtext-without-ini")
    (with_ini_root / "test_sample.py").write_text(_SAMPLE_TEST)
    (without_ini_root / "test_sample.py").write_text(_SAMPLE_TEST)
    (with_ini_root / "pytest.ini").write_text("[pytest]\nvantage_no_failure_text = true\n")

    with_ini = _run_pytest(with_ini_root)
    without_ini = _run_pytest(without_ini_root)

    assert with_ini.returncode == 0, with_ini.stdout + with_ini.stderr
    assert without_ini.returncode == 0, without_ini.stdout + without_ini.stderr
    assert "Unknown config option" not in with_ini.stdout + with_ini.stderr
    with_snapshot = {k: v for k, v in _tree_snapshot(with_ini_root).items() if k != "pytest.ini"}
    assert with_snapshot == _tree_snapshot(without_ini_root)


@pytest.mark.req(id="RQ-2")
def test_no_connection_is_attempted_with_no_recording_option(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not "no data was sent" -- no socket opened at all.

    Patches ``socket.create_connection`` in-process, which is why this run is
    ``runpytest`` (in-process) rather than ``runpytest_subprocess``: a
    monkeypatch made in this process has no effect on a child process.
    """
    pytester.makepyfile(test_sample=_SAMPLE_TEST)
    monkeypatch.setattr(socket, "create_connection", _forbidden_create_connection)

    result = pytester.runpytest()

    # `warnings=0` explicitly, not omitted: `assert_outcomes` leaves any
    # count it is not given UNCHECKED, so the spec's "and emits no warning"
    # half was silently unverified while this line read `passed=1` alone.
    result.assert_outcomes(passed=1, warnings=0)
