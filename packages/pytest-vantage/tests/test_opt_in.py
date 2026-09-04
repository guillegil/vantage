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
def test_failure_text_opt_in_ini_alone_cannot_enable_capture(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """failure-evidence -> Capture is opt-in, absent by default -> A
    committed configuration file cannot enable capture on its own.

    RQ-2's own established differential (above), applied to the
    failure-text opt-in specifically (design.md D72, revised after Phase
    9's RQ-25 measurement, and further corrected to remove the ini surface
    entirely): with no invocation flag on either run -- neither `--vantage`
    nor `--vantage-failure-text` -- a committed `vantage_failure_text =
    true` ini value changes nothing. The scenario under test is that
    failure-text capture behaves *identically* whether the configuration
    file is present or absent, and the established differential form for
    that is tree-identity: the project tree -- excluding the ini file
    itself, which is the one deliberate difference between the two runs --
    must still be byte-identical.

    `vantage_failure_text` is not a registered option any more (the whole
    point of this correction), so pytest now warns `Unknown config option`
    on the run that carries the ini value. That warning is asserted
    *present*, not absent: it is honest feedback that the knob does not
    exist, and it is exactly what proves the ini value is inert rather than
    silently consulted.
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
    """failure-evidence -> Capture is opt-in, absent by default: "no
    committed configuration file MAY be the means by which capture is
    enabled".

    The invocation activates recording but never asks for failure text; a
    committed `vantage_failure_text = true` does. Capture must stay absent,
    for the same reason `_activation_requested` reads only `--vantage`:
    a file one person commits must never silently turn capture on for
    everyone who checks the project out -- and stored failure text is
    unredacted (ADR-0016), so the harm here is disclosure, not only the
    RQ-25 overhead this polarity exists to avoid.
    """
    config = _IniOnlyConfig()

    assert _failure_text_capture_requested(config) is False  # type: ignore[arg-type]
    assert config.ini_reads == [], (
        f"the failure-text opt-in must not consult any ini value; read {config.ini_reads}"
    )


@pytest.mark.req(id="RQ-2")
def test_the_shipped_help_text_advertises_no_ini_equivalent(tmp_path: Path) -> None:
    """failure-evidence -> Capture is opt-in, absent by default: "no
    committed configuration file MAY be the means by which capture is
    enabled".

    `_IniOnlyConfig` above proves the *behaviour*; this proves the
    *promise*. `pytest --help` is the surface a user reads before deciding
    how to enable capture, and for a while it read "capture never happens
    unless this or the ini equivalent is given" -- advertising exactly the
    means the requirement forbids, and inviting someone to commit a file
    that would then silently do nothing. Removing a configuration surface
    is not finished until the help text stops offering it.
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


# --- Metadata capture flag inertness (opt-in-activation, RQ-2 extended, ------
# --- design.md D99, tasks 5.3/5.5/5.6) ---------------------------------------
#
# `--vantage-metadata` is its own invocation flag, gated identically to
# `--vantage` and `--vantage-failure-text`: no ini equivalent, the shipped
# `--help` actively denies one (C3), the declaration is opened only after
# both gates pass (C2), and the whole surface stays byte-inert with the flag
# absent even when a `vantage-metadata.json` sits in the project root (C1).


@pytest.mark.req(id="RQ-2")
def test_project_tree_is_byte_identical_with_a_metadata_declaration_present_but_the_flag_absent(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """opt-in-activation: "No read or connection without the metadata flag"
    (C1). The same differential `test_project_tree_is_byte_identical_with_
    plugin_absent` uses above, with one deliberate addition: a
    `vantage-metadata.json` sits in both project roots. Its mere presence
    must not change a single byte the bare run produces relative to the
    `-p no:vantage` control -- the flag, not the file, is what the
    capability spec's inertness requirement gates on.
    """
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")

    bare_root = tmp_path_factory.mktemp("vantage-metadata-rq2-bare")
    control_root = tmp_path_factory.mktemp("vantage-metadata-rq2-control")
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


@pytest.mark.req(id="RQ-2")
def test_no_connection_is_attempted_with_a_metadata_declaration_present_but_no_flags(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """opt-in-activation (C1), the socket-level half: a `vantage-metadata.json`
    present in the project root, with no `--vantage` or `--vantage-metadata`
    given, must not cause even a single connection attempt. Same shape as
    `test_no_connection_is_attempted_with_no_recording_option` above.
    """
    pytester.makepyfile(test_sample=_SAMPLE_TEST)
    (pytester.path / _METADATA_DECLARATION_FILENAME).write_text('{"version": 1, "files": []}\n')
    monkeypatch.setattr(socket, "create_connection", _forbidden_create_connection)

    result = pytester.runpytest()

    result.assert_outcomes(passed=1, warnings=0)


@pytest.mark.req(id="RQ-2")
def test_the_shipped_help_text_advertises_no_ini_equivalent_for_metadata(tmp_path: Path) -> None:
    """opt-in-activation: "The shipped `--help` denies an ini equivalent for
    the metadata flag" (C3). The identical assertion shape
    `test_the_shipped_help_text_advertises_no_ini_equivalent` above proves
    for `--vantage-failure-text`, inherited verbatim (design.md D99) for
    `--vantage-metadata`.
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
    ``vantage_metadata`` here at all is what this double is built to catch
    -- `_metadata_capture_requested` must short-circuit on
    `_activation_requested` before touching the opt-in surface (design.md
    D99, mirroring `plugin.py:157-158`), the same guarantee that keeps
    `test_xdist_guard.py`'s `_WorkerConfigDouble` (whose allow-list does not
    include ``vantage_metadata``) from ever seeing that option read.
    """

    def getoption(self, name: str) -> object:
        if name == "vantage":
            return False
        raise AssertionError(f"unexpected option read: {name}")


def test_metadata_capture_requested_short_circuits_when_not_activated() -> None:
    """design.md D99: an unactivated session reads `"vantage"` alone,
    exactly as `_failure_text_capture_requested` already does -- proves the
    gate is structural, not merely a happy-path default."""
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
    """C2 (design.md D99): "The declaration is read only after both gates
    pass." A `Recorder` constructed with `metadata_requested=False` --
    which is what either gate closed collapses to, since
    `_metadata_capture_requested` has already combined both before this
    keyword is ever set -- must never open the declaration file.
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
    """The other half of C2: once both gates pass, the declaration IS
    consulted -- `metadata_requested=True` must reach the filesystem,
    proving the earlier test's zero-calls result is not an implementation
    that never opens anything at all."""
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
    """Q3 (design.md D92): setting `--vantage-metadata` is a deliberate
    act, so a missing declaration warns once instead of silently capturing
    nothing -- the one place this design departs from
    `recording-fault-tolerance`'s silent posture, and bounded to the
    declaration itself: a malformed *declared document* never warns and
    never fails ingestion (D97), a different thing entirely.
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
    """Q3's other half: a declaration that IS present emits no warning at
    all -- the departure from silence is bounded to the declaration's own
    absence, never triggered by its mere presence."""
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
