"""The xdist guard is the FIRST statement in ``pytest_configure``.

Under xdist every worker re-runs ``pytest_configure`` as a full pytest
session of its own -- unguarded, ``-n 4`` would leave four workers' recorders
plus the controller's, recording one session as several runs. The
discriminator is whether the config object carries a ``workerinput``
attribute.

A worker is the only process with `item`/`excinfo`, so `EvidenceCollector`
runs there, and the only one that knows whether Ctrl-C or ``pytest.exit()``
interrupted it, so `WorkerInterruptRelay` does too. Session fixtures run
there as well, so `WorkerMetadataRelay` holds what the ``vantage_metadata``
fixture hands them, once the controller has said in ``workerinput`` that
it records. The worker branch reads
exactly three options: ``vantage`` and ``vantage_failure_text`` to decide
what to register, and ``capture``, read once by
`EvidenceCollector.__init__`. xdist hands each
worker the controller's typed arguments, so the worker applies the same
"typed on the command line" rule and agrees with its controller. Beyond that
a worker never resolves a server address, reads a timeout, preflights a
socket, probes the server's capabilities or constructs a `Recorder`.
`_WorkerConfigDouble` answers exactly those reads and raises for anything
else.

Pure unit tests: config doubles stand in for a real ``pytest.Config``, no
subprocess or real xdist session needed.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_vantage.boundary import VantageWarning
from pytest_vantage.evidence import EvidenceCollector
from pytest_vantage.plugin import pytest_configure
from pytest_vantage.recorder import (
    _CONTROLLER_RECORDS_KEY,
    WorkerInterruptRelay,
    WorkerMetadataRelay,
)
from pytest_vantage.session_metadata import SESSION_METADATA


class _RegisterCallDouble:
    """A ``pluginmanager.register`` stand-in that records every call. If a
    worker ever constructed a `Recorder`, it would be registered here --
    the most direct place to assert it never is.
    """

    def __init__(self) -> None:
        self.registered: list[object] = []

    def register(self, plugin: object) -> None:
        self.registered.append(plugin)

    def is_blocked(self, name: str) -> bool:
        return False


class _WorkerConfigDouble:
    """A ``pytest.Config`` stand-in carrying xdist's ``workerinput`` marker.

    ``getoption`` answers only the options a worker may read --
    `"vantage"`, `"vantage_failure_text"` and `"capture"` -- and raises for
    anything else: if ``pytest_configure`` or `EvidenceCollector` ever reach
    for a server address, a timeout, or anything else on a worker, this is
    where that would be caught. ``getini`` raises unconditionally -- no ini
    value is read on a worker. ``pluginmanager`` is a
    ``_RegisterCallDouble``, so a worker that ever constructed a `Recorder`
    is caught there too. Both flags parse as set; ``typed`` is what the
    controller's command line actually carried, and ``recorded`` whether
    the controller said it records.
    """

    _ALLOWED_OPTIONS = frozenset({"vantage", "capture", "vantage_failure_text"})

    def __init__(self, typed: tuple[str, ...], *, recorded: bool = True) -> None:
        self.workerinput: dict[str, Any] = {"workerid": "gw0"}
        if recorded:
            self.workerinput[_CONTROLLER_RECORDS_KEY] = True
        self.pluginmanager = _RegisterCallDouble()
        self.invocation_params = SimpleNamespace(args=typed)
        self.stash = pytest.Stash()

    def getoption(self, name: str, default: object = None) -> object:
        if name == "vantage":
            return True
        if name == "capture":
            return "fd"
        if name == "vantage_failure_text":
            return True
        raise AssertionError(
            f"pytest_configure must not read option {name!r} on an xdist worker "
            f"(only {sorted(self._ALLOWED_OPTIONS)!r} may be read there)"
        )

    def getini(self, name: str) -> object:
        raise AssertionError(
            f"pytest_configure must not read ini value {name!r} on an xdist worker "
            "(no ini value is ever read there)"
        )


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        (("--vantage", "-n", "2"), [WorkerInterruptRelay, WorkerMetadataRelay]),
        (
            ("--vantage", "--vantage-failure-text", "-n", "2"),
            [WorkerInterruptRelay, WorkerMetadataRelay, EvidenceCollector],
        ),
    ],
    ids=["recording", "recording-with-failure-text"],
)
def test_a_recording_worker_registers_its_relays_and_collector_and_no_recorder(
    typed: tuple[str, ...], expected: list[type]
) -> None:
    """A worker's `pytest_configure` registers both relays when recording,
    `EvidenceCollector` when failure text was asked for too, and nothing
    else -- in particular no `Recorder`. The metadata relay's mapping is the
    one the fixture finds."""
    config = _WorkerConfigDouble(typed=typed)
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert [type(plugin) for plugin in config.pluginmanager.registered] == expected
    (relay,) = [p for p in config.pluginmanager.registered if isinstance(p, WorkerMetadataRelay)]
    assert config.stash[SESSION_METADATA] is relay.session_metadata


def test_a_worker_whose_controller_does_not_record_hands_the_fixture_nothing() -> None:
    """``--vantage`` was typed, so it reaches the worker, but the controller
    found no server: the worker still relays an interrupt, which nobody
    reads, and leaves the ``vantage_metadata`` fixture to hand out a mapping
    that neither warns nor sends."""
    config = _WorkerConfigDouble(typed=("--vantage", "-n", "2"), recorded=False)
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert [type(plugin) for plugin in config.pluginmanager.registered] == [WorkerInterruptRelay]
    assert SESSION_METADATA not in config.stash


def test_worker_registers_nothing_when_the_flags_came_from_addopts() -> None:
    """The flags parse as set -- a committed ``addopts`` reaches every
    worker too -- but the controller's command line only carried ``-n``."""
    config = _WorkerConfigDouble(typed=("-n", "2"))
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert config.pluginmanager.registered == []


class _UnactivatedControllerDouble:
    """The non-worker counterpart: no ``workerinput``, so the activation
    check must run -- triangulates that the guard is scoped to xdist workers
    only, not swallowing every invocation. No ``getini`` and no
    ``pluginmanager``: an unactivated session touches neither.
    """

    invocation_params = SimpleNamespace(args=())

    def __init__(self) -> None:
        self.options_read: list[str] = []

    def getoption(self, name: str, default: object = None) -> object:
        self.options_read.append(name)
        return False


def test_no_worker_input_still_runs_the_activation_check() -> None:
    config = _UnactivatedControllerDouble()
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert "vantage" in config.options_read


class _ActivatedControllerDouble:
    """A controller whose typed arguments ask for recording and failure
    text, pointed at a port where nothing listens: `EvidenceCollector`
    registers before the preflight, so its outcome does not matter here.
    A warning raised in `pytest_configure` is issued the way pytest issues
    its own configuration warnings, and kept in `issued`."""

    def __init__(self, address: str) -> None:
        self.pluginmanager = _RegisterCallDouble()
        self.issued: list[Warning] = []
        self.invocation_params = SimpleNamespace(args=("--vantage", "--vantage-failure-text"))
        self._options: dict[str, Any] = {
            "vantage": True,
            "vantage_server": address,
            "vantage_timeout": 0.5,
            "vantage_failure_text": True,
            "capture": "fd",
        }

    def getoption(self, name: str, default: object = None) -> object:
        return self._options.get(name, default)

    def getini(self, name: str) -> object:
        return None

    def issue_config_time_warning(self, warning: Warning, stacklevel: int) -> None:
        self.issued.append(warning)


def test_controller_registers_an_evidencecollector_when_activated() -> None:
    """A session with no xdist workers at all still needs failure evidence
    collected somewhere, so the controller registers `EvidenceCollector`
    too."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()
    config = _ActivatedControllerDouble(f"http://127.0.0.1:{closed_port}")

    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert [type(plugin) for plugin in config.pluginmanager.registered] == [EvidenceCollector]
    (warned,) = config.issued
    assert isinstance(warned, VantageWarning)
    assert "cannot reach" in str(warned)
