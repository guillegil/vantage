"""Absence of ``--vantage`` is the plugin's fully inert default state.

**Differential, never absolute**: pytest itself writes ``.pytest_cache`` and
``__pycache__``, so "no file was created" can only be made to pass by lying
about what it checks. Instead the same project runs twice -- once bare, once
with ``-p no:vantage`` (the control: pytest with this plugin definitively
absent) -- and the two resulting project trees must be byte-identical: the
same relative paths, and the same file content.

The stronger half is the socket-level assertion below: with no recording
option present, no connection is even *attempted* -- not "no data sent", no
socket opened at all. That is what proves inertness rather than politeness.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_vantage import vcs
from pytest_vantage.boundary import VantageWarning
from pytest_vantage.plugin import _failure_text_capture_requested, _metadata_capture_requested
from pytest_vantage.recorder import Recorder

_SAMPLE_TEST = "def test_it():\n    assert True\n"
_METADATA_DECLARATION_FILENAME = "vantage-metadata.json"


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

    bare_root = tmp_path_factory.mktemp("vantage-bare")
    control_root = tmp_path_factory.mktemp("vantage-control")
    (bare_root / "test_sample.py").write_text(_SAMPLE_TEST)
    (control_root / "test_sample.py").write_text(_SAMPLE_TEST)

    bare = _run_pytest(bare_root)
    control = _run_pytest(control_root, "-p", "no:vantage")

    assert bare.returncode == 0, bare.stdout + bare.stderr
    assert control.returncode == 0, control.stdout + control.stderr
    assert _tree_snapshot(bare_root) == _tree_snapshot(control_root)


def test_failure_text_opt_in_ini_alone_cannot_enable_capture(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed configuration file cannot enable failure-text capture.

    The same differential as above: with no invocation flag on either run,
    a committed `vantage_failure_text = true` ini value changes nothing --
    the project tree, excluding the ini file itself, must be byte-identical
    with and without it.

    `vantage_failure_text` is not a registered option, so pytest warns
    `Unknown config option` on the run that carries the ini value. That
    warning is asserted *present*: it is honest feedback that the knob does
    not exist, and it proves the ini value is inert rather than silently
    consulted.
    """
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")

    with_ini_root = tmp_path_factory.mktemp("vantage-failtext-with-ini")
    without_ini_root = tmp_path_factory.mktemp("vantage-failtext-without-ini")
    (with_ini_root / "test_sample.py").write_text(_SAMPLE_TEST)
    (without_ini_root / "test_sample.py").write_text(_SAMPLE_TEST)
    (with_ini_root / "pytest.ini").write_text("[pytest]\nvantage_failure_text = true\n")

    with_ini = _run_pytest(with_ini_root)
    without_ini = _run_pytest(without_ini_root)

    assert with_ini.returncode == 0, with_ini.stdout + with_ini.stderr
    assert without_ini.returncode == 0, without_ini.stdout + without_ini.stderr
    assert "Unknown config option: vantage_failure_text" in with_ini.stdout + with_ini.stderr
    with_snapshot = {k: v for k, v in _tree_snapshot(with_ini_root).items() if k != "pytest.ini"}
    assert with_snapshot == _tree_snapshot(without_ini_root)


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
    # count it is not given UNCHECKED, and an inert plugin emits no warning.
    result.assert_outcomes(passed=1, warnings=0)


class _IniOnlyConfig:
    """A config whose invocation activated recording but never asked for
    failure text, while a committed `pytest.ini` sets the opt-in. Reading
    the ini value at all is what this double is built to expose.
    """

    def __init__(self) -> None:
        self.ini_reads: list[str] = []

    def getoption(self, name: str) -> object:
        if name == "vantage":
            return True
        if name == "vantage_failure_text":
            return False
        raise AssertionError(f"unexpected option read: {name}")

    def getini(self, name: str) -> object:
        self.ini_reads.append(name)
        return True


def test_a_committed_ini_cannot_be_the_means_by_which_capture_is_enabled() -> None:
    """The invocation activates recording but never asks for failure text; a
    committed `vantage_failure_text = true` does. Capture must stay absent
    and the ini value must not even be read: a file one person commits must
    never silently turn capture on for everyone who checks the project out,
    and stored failure text is unredacted, so the harm would be disclosure.
    """
    config = _IniOnlyConfig()

    assert _failure_text_capture_requested(config) is False  # type: ignore[arg-type]
    assert config.ini_reads == [], (
        f"the failure-text opt-in must not consult any ini value; read {config.ini_reads}"
    )


def test_the_shipped_help_text_advertises_no_ini_equivalent(tmp_path: Path) -> None:
    """`_IniOnlyConfig` above proves the *behaviour*; this proves the
    *promise*. `pytest --help` is what a user reads before deciding how to
    enable capture, so it must actively deny an ini equivalent: offering
    one would invite someone to commit a file that then silently does
    nothing.
    """
    result = _run_pytest(tmp_path, "--help")
    assert result.returncode == 0, result.stderr

    rendered = " ".join(result.stdout.split())
    assert "--vantage-failure-text" in rendered, (
        "the opt-in flag must appear in --help; without it this assertion proves nothing"
    )
    assert "or the ini equivalent is given" not in rendered, (
        "--help must not offer an ini equivalent as a means of enabling capture"
    )
    assert "there is no ini equivalent" in rendered, (
        "--help must actively deny an ini equivalent rather than merely omit it: "
        "silence invites someone to commit a file that would then do nothing"
    )


# --- Metadata capture flag inertness -----------------------------------------
#
# `--vantage-metadata` is its own invocation flag, gated identically to
# `--vantage` and `--vantage-failure-text`: no ini equivalent, the shipped
# `--help` actively denies one, the declaration is opened only after both
# gates pass, and the whole surface stays byte-inert with the flag absent
# even when a `vantage-metadata.json` sits in the project root.


def test_project_tree_is_byte_identical_with_a_metadata_declaration_present_but_the_flag_absent(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same differential as `test_project_tree_is_byte_identical_with_plugin_absent`,
    with a `vantage-metadata.json` in both project roots. Its mere presence
    must not change a single byte the bare run produces relative to the
    `-p no:vantage` control -- the flag, not the file, enables metadata
    capture.
    """
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")

    bare_root = tmp_path_factory.mktemp("vantage-metadata-bare")
    control_root = tmp_path_factory.mktemp("vantage-metadata-control")
    declaration = '{"version": 1, "files": []}\n'
    (bare_root / "test_sample.py").write_text(_SAMPLE_TEST)
    (control_root / "test_sample.py").write_text(_SAMPLE_TEST)
    (bare_root / _METADATA_DECLARATION_FILENAME).write_text(declaration)
    (control_root / _METADATA_DECLARATION_FILENAME).write_text(declaration)

    bare = _run_pytest(bare_root)
    control = _run_pytest(control_root, "-p", "no:vantage")

    assert bare.returncode == 0, bare.stdout + bare.stderr
    assert control.returncode == 0, control.stdout + control.stderr
    assert _tree_snapshot(bare_root) == _tree_snapshot(control_root)


def test_no_connection_is_attempted_with_a_metadata_declaration_present_but_no_flags(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The socket-level half: a `vantage-metadata.json` present in the
    project root, with no `--vantage` or `--vantage-metadata` given, must
    not cause even a single connection attempt.
    """
    pytester.makepyfile(test_sample=_SAMPLE_TEST)
    (pytester.path / _METADATA_DECLARATION_FILENAME).write_text('{"version": 1, "files": []}\n')
    monkeypatch.setattr(socket, "create_connection", _forbidden_create_connection)

    result = pytester.runpytest()

    result.assert_outcomes(passed=1, warnings=0)


def test_the_shipped_help_text_advertises_no_ini_equivalent_for_metadata(tmp_path: Path) -> None:
    """The shipped `--help` denies an ini equivalent for `--vantage-metadata`,
    exactly as it does for `--vantage-failure-text`.
    """
    result = _run_pytest(tmp_path, "--help")
    assert result.returncode == 0, result.stderr

    rendered = " ".join(result.stdout.split())
    assert "--vantage-metadata" in rendered, (
        "the metadata flag must appear in --help; without it this assertion proves nothing"
    )
    assert "or the ini equivalent is given" not in rendered, (
        "--help must not offer an ini equivalent as a means of enabling metadata capture"
    )
    assert "there is no ini equivalent" in rendered, (
        "--help must actively deny an ini equivalent rather than merely omit it: "
        "silence invites someone to commit a file that would then do nothing"
    )


class _UnactivatedConfig:
    """A config whose invocation never activated recording at all. Reading
    ``vantage_metadata`` here is what this double is built to catch --
    `_metadata_capture_requested` must short-circuit on
    `_activation_requested` before touching the opt-in surface.
    """

    def getoption(self, name: str) -> object:
        if name == "vantage":
            return False
        raise AssertionError(f"unexpected option read: {name}")


def test_metadata_capture_requested_short_circuits_when_not_activated() -> None:
    """An unactivated session reads `"vantage"` alone, exactly as
    `_failure_text_capture_requested` does -- the gate is structural, not
    merely a happy-path default."""
    assert _metadata_capture_requested(_UnactivatedConfig()) is False  # type: ignore[arg-type]


def _patch_path_open_recorder(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Wraps the real `Path.open` to record every path it is called on
    while still letting it execute for real -- the same non-fabricating
    shape `test_vcs.py`'s `_CallRecorder` uses for `subprocess.run`.

    Deliberately a plain function, not a callable class instance: `Path.open`
    is looked up through the descriptor protocol (`instance.open(...)`
    implicitly passes `instance` as the first argument only because a
    *function* object implements `__get__`), and a callable instance does
    not implement that protocol -- patching with one would silently drop
    the `Path` instance the call was actually made on.
    """
    paths_opened: list[Path] = []
    real_open = Path.open

    def _record_open(path_self: Path, *args: object, **kwargs: object) -> object:
        paths_opened.append(path_self)
        return real_open(path_self, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", _record_open)
    return paths_opened


def test_declaration_is_not_opened_when_metadata_capture_was_not_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The declaration is read only after both gates pass. A `Recorder`
    constructed with `metadata_requested=False` -- what either closed gate
    collapses to -- must never open the declaration file.
    """
    (tmp_path / _METADATA_DECLARATION_FILENAME).write_text('{"version": 1, "files": []}\n')
    paths_opened = _patch_path_open_recorder(monkeypatch)
    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", lambda rootpath: vcs.VcsSnapshot())

    Recorder(
        config=SimpleNamespace(rootpath=str(tmp_path)),  # type: ignore[arg-type]
        address="http://example.invalid",
        timeout=1.0,
        lifecycle_available=True,
        metadata_requested=False,
    )

    assert not any(path.name == _METADATA_DECLARATION_FILENAME for path in paths_opened)


def test_declaration_is_opened_when_metadata_capture_was_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once both gates pass, the declaration IS consulted --
    `metadata_requested=True` must reach the filesystem, proving the
    previous test's zero-calls result is not an implementation that never
    opens anything at all."""
    (tmp_path / _METADATA_DECLARATION_FILENAME).write_text('{"version": 1, "files": []}\n')
    paths_opened = _patch_path_open_recorder(monkeypatch)
    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", lambda rootpath: vcs.VcsSnapshot())

    Recorder(
        config=SimpleNamespace(rootpath=str(tmp_path)),  # type: ignore[arg-type]
        address="http://example.invalid",
        timeout=1.0,
        lifecycle_available=True,
        metadata_requested=True,
    )

    assert any(path.name == _METADATA_DECLARATION_FILENAME for path in paths_opened)


def test_recorder_warns_exactly_once_when_metadata_requested_and_declaration_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recwarn: pytest.WarningsRecorder
) -> None:
    """Setting `--vantage-metadata` is a deliberate act, so a missing
    declaration warns once instead of silently capturing nothing. The
    warning is bounded to the declaration itself: a malformed *declared
    document* never warns and never fails ingestion.
    """
    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", lambda rootpath: vcs.VcsSnapshot())

    Recorder(
        config=SimpleNamespace(rootpath=str(tmp_path)),  # type: ignore[arg-type]
        address="http://example.invalid",
        timeout=1.0,
        lifecycle_available=True,
        metadata_requested=True,
    )

    metadata_warnings = [w for w in recwarn.list if issubclass(w.category, VantageWarning)]
    assert len(metadata_warnings) == 1
    assert _METADATA_DECLARATION_FILENAME in str(metadata_warnings[0].message)


def test_recorder_emits_no_warning_when_metadata_requested_and_declaration_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recwarn: pytest.WarningsRecorder
) -> None:
    """A declaration that IS present emits no warning at all -- the warning
    is reserved for the declaration's absence."""
    (tmp_path / _METADATA_DECLARATION_FILENAME).write_text('{"version": 1, "files": []}\n')
    monkeypatch.setattr("pytest_vantage.recorder.vcs.capture", lambda rootpath: vcs.VcsSnapshot())

    Recorder(
        config=SimpleNamespace(rootpath=str(tmp_path)),  # type: ignore[arg-type]
        address="http://example.invalid",
        timeout=1.0,
        lifecycle_available=True,
        metadata_requested=True,
    )

    assert not any(issubclass(w.category, VantageWarning) for w in recwarn.list)
