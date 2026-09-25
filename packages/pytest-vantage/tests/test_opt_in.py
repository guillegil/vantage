"""What turns recording on, and what never does.

Only flags typed on the command line count. ``--vantage`` activates
recording; ``--vantage-failure-text`` and ``--vantage-metadata`` widen an
activated session's capture. pytest folds a committed ``addopts`` and the
``PYTEST_ADDOPTS`` environment variable into the parsed options, so the
plugin checks the typed arguments themselves: a flag that arrives any other
way is ignored with one warning. An ini value or an environment variable
may say where to report, never whether.

Absence of ``--vantage`` is the plugin's fully inert default state.
**Differential, never absolute**: pytest itself writes ``.pytest_cache`` and
``__pycache__``, so "no file was created" can only be made to pass by lying
about what it checks. Instead the same project runs twice -- once bare, once
with ``-p no:vantage`` (the control: pytest with this plugin definitively
absent) -- and the two resulting project trees must be byte-identical: the
same relative paths, and the same file content.

The stronger half is the socket-level assertion: with no recording option
present, no connection is even *attempted* -- not "no data sent", no socket
opened at all. That is what proves inertness rather than politeness.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_vantage import vcs
from pytest_vantage.boundary import VantageWarning
from pytest_vantage.plugin import (
    _activation_requested,
    _failure_text_capture_requested,
    _metadata_capture_requested,
)
from pytest_vantage.recorder import Recorder
from vantage_test_server import VantageTestServer, vantage_server  # noqa: F401 -- fixture

_SAMPLE_TEST = "def test_it():\n    assert True\n"
_METADATA_DECLARATION_FILENAME = "vantage-metadata.json"
_ALL_FLAGS = "--vantage --vantage-failure-text --vantage-metadata"


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


def _vantage_warnings(recwarn: pytest.WarningsRecorder) -> list[str]:
    return [str(w.message) for w in recwarn.list if issubclass(w.category, VantageWarning)]


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


# --- Only typed flags count ----------------------------------------------------

_ADDOPTS_SOURCES = ["pyproject.toml addopts", "pytest.ini addopts", "PYTEST_ADDOPTS"]


def _put_in_addopts(
    source: str, flags: str, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    if source == "pyproject.toml addopts":
        pytester.makepyprojecttoml(f'[tool.pytest.ini_options]\naddopts = "{flags}"\n')
    elif source == "pytest.ini addopts":
        pytester.makeini(f"[pytest]\naddopts = {flags}\n")
    else:
        monkeypatch.setenv("PYTEST_ADDOPTS", flags)


@pytest.mark.parametrize("source", _ADDOPTS_SOURCES)
@pytest.mark.parametrize(
    ("typed", "activated"),
    [((), False), (("--vantage",), True), (("--", "--vantage"), False)],
    ids=["nothing typed", "--vantage typed", "--vantage after a bare --"],
)
def test_flags_from_addopts_enable_nothing(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    typed: tuple[str, ...],
    activated: bool,
) -> None:
    """A committed ``addopts`` is exactly how one person's choice would
    silently become everyone's: recording, unredacted failure text and
    configuration-file reads for every clone. pytest does fold the flags
    into the parsed options -- asserted first, so the gates below are
    tested against the real hazard -- and none of them may count. Only a
    ``--vantage`` typed before any bare ``--`` activates, and it does not
    carry the capture flags from ``addopts`` with it.
    """
    _put_in_addopts(source, _ALL_FLAGS, pytester, monkeypatch)

    config = pytester.parseconfig(*typed)

    assert config.getoption("vantage_failure_text") is True
    assert _activation_requested(config) is activated
    assert _failure_text_capture_requested(config) is False
    assert _metadata_capture_requested(config) is False


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ((), (False, False, False)),
        (("--vantage-failure-text", "--vantage-metadata"), (False, False, False)),
        (("--vantage",), (True, False, False)),
        (("--vantage", "--vantage-failure-text"), (True, True, False)),
        (("--vantage", "--vantage-metadata"), (True, False, True)),
    ],
)
def test_typed_flags_decide_every_gate(
    pytester: pytest.Pytester, typed: tuple[str, ...], expected: tuple[bool, bool, bool]
) -> None:
    """Capture is absent unless asked for, and a capture flag never
    activates recording on its own."""
    config = pytester.parseconfig(*typed)

    gates = (
        _activation_requested(config),
        _failure_text_capture_requested(config),
        _metadata_capture_requested(config),
    )
    assert gates == expected


# --- Nothing but a typed --vantage records, even with a server listening -------

_LEAKY_FAILING_TEST = """
def test_login():
    password = "hunter2"
    print("logging in with", password)
    assert password == "not-the-password"
"""


def _make_capturable_project(pytester: pytest.Pytester) -> None:
    """A failing test whose failure text carries a secret, and a metadata
    declaration naming a file that carries another: everything the capture
    flags would ship if they were ever enabled by accident."""
    pytester.makepyfile(test_login=_LEAKY_FAILING_TEST)
    (pytester.path / "settings.json").write_text(json.dumps({"db_password": "s3cr3t-db"}))
    (pytester.path / _METADATA_DECLARATION_FILENAME).write_text(
        json.dumps(
            {
                "version": 1,
                "files": [{"path": "settings.json", "format": "json", "keys": ["db_password"]}],
            }
        )
    )


def _spy_on_connections(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Record every ``socket.create_connection`` while still connecting for
    real -- the preflight is the plugin's first network action, so an empty
    list means no socket was ever opened."""
    attempts: list[object] = []
    real_create_connection = socket.create_connection

    def _spy(address: tuple[str, int], *args: object, **kwargs: object) -> socket.socket:
        attempts.append(address)
        return real_create_connection(address, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "create_connection", _spy)
    return attempts


_UNTYPED_SOURCES = [
    "vantage_server ini value",
    "VANTAGE_SERVER",
    "--vantage-server alone",
    *_ADDOPTS_SOURCES,
]


@pytest.mark.parametrize("source", _UNTYPED_SOURCES)
def test_nothing_but_a_typed_vantage_records(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    recwarn: pytest.WarningsRecorder,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    source: str,
) -> None:
    """Every way of configuring the plugin short of typing ``--vantage``,
    pointed at a server that is up and would accept the run: no socket is
    opened and nothing is stored. The ``addopts`` sources carry every flag,
    and the plugin says once that it ignored them."""
    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    _make_capturable_project(pytester)
    address = vantage_server.address
    args: list[str] = []
    if source == "vantage_server ini value":
        pytester.makeini(f"[pytest]\nvantage_server = {address}\n")
    elif source == "VANTAGE_SERVER":
        monkeypatch.setenv("VANTAGE_SERVER", address)
    elif source == "--vantage-server alone":
        args = [f"--vantage-server={address}"]
    else:
        _put_in_addopts(source, f"{_ALL_FLAGS} --vantage-server={address}", pytester, monkeypatch)
    attempts = _spy_on_connections(monkeypatch)

    result = pytester.runpytest(*args)

    result.assert_outcomes(failed=1)
    assert attempts == []
    assert vantage_server.executions() == []
    warned = _vantage_warnings(recwarn)
    if source in _ADDOPTS_SOURCES:
        assert len(warned) == 1, warned
        assert "ignoring --vantage, --vantage-failure-text, --vantage-metadata" in warned[0]
    else:
        assert warned == []


def test_a_typed_vantage_records_to_the_ini_address(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
) -> None:
    """The positive control for the test above: the same project and the
    same committed address record once ``--vantage`` is typed, so the empty
    server there means "not activated", not "not reachable"."""
    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    _make_capturable_project(pytester)
    pytester.makeini(f"[pytest]\nvantage_server = {vantage_server.address}\n")
    attempts = _spy_on_connections(monkeypatch)

    result = pytester.runpytest("--vantage")

    result.assert_outcomes(failed=1)
    assert attempts != []
    assert len(vantage_server.executions()) == 1


# --- The capture opt-ins have no ini or environment equivalent -----------------

_CAPTURE_SOURCES = {
    "vantage_failure_text ini value": ("ini", "vantage_failure_text"),
    "vantage_metadata ini value": ("ini", "vantage_metadata"),
    "VANTAGE_FAILURE_TEXT": ("env", "VANTAGE_FAILURE_TEXT"),
    "VANTAGE_METADATA": ("env", "VANTAGE_METADATA"),
    "typed flags (control)": ("typed", ""),
}


@pytest.mark.parametrize(
    ("kind", "name"), list(_CAPTURE_SOURCES.values()), ids=list(_CAPTURE_SOURCES)
)
def test_capture_is_enabled_only_by_its_typed_flag(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    vantage_server: VantageTestServer,  # noqa: F811 -- fixture param shadows the import by name, on purpose
    kind: str,
    name: str,
) -> None:
    """An activated session with a committed ini value or an exported
    environment variable asking for capture stores the outcome and nothing
    else: no failure text, no captured output, no metadata. The typed-flag
    row is the control proving the same project would capture all three.

    Neither ini key is registered, so pytest warns ``Unknown config
    option``: honest feedback that the knob does not exist.
    """
    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    _make_capturable_project(pytester)
    args = ["--vantage", f"--vantage-server={vantage_server.address}"]
    if kind == "ini":
        pytester.makeini(f"[pytest]\n{name} = true\n")
    elif kind == "env":
        monkeypatch.setenv(name, "1")
    else:
        args += ["--vantage-failure-text", "--vantage-metadata"]

    result = pytester.runpytest(*args)

    result.assert_outcomes(failed=1)
    (stored,) = vantage_server.results()
    metadata_rows = vantage_server.store._metadata_entries
    if kind == "typed":
        assert stored.failure is not None
        assert "hunter2" in (stored.failure.traceback or "")
        assert "hunter2" in (stored.captured.stdout or "")
        assert metadata_rows
        return
    assert stored.failure is None
    assert stored.captured.stdout is None
    assert not metadata_rows
    if kind == "ini":
        assert f"Unknown config option: {name}" in result.stdout.str() + result.stderr.str()


class _IniOnlyConfig:
    """A config whose invocation activated recording but never asked for
    failure text, while a committed `pytest.ini` sets the opt-in. Reading
    the ini value at all is what this double is built to expose.
    """

    invocation_params = SimpleNamespace(args=("--vantage",))

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
