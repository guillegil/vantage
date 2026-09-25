"""The xdist guard is the FIRST statement in ``pytest_configure``.

Under xdist every worker re-runs ``pytest_configure`` as a full pytest
session of its own -- unguarded, ``-n 4`` would leave four workers' recorders
plus the controller's, recording one session as several runs. The
discriminator is whether the config object carries a ``workerinput``
attribute.

A worker is the only process with `item`/`excinfo`, so `EvidenceCollector`
runs there, and the worker branch reads exactly three options:
``vantage`` and ``vantage_failure_text`` to decide whether to register it,
and ``capture``, read once by `EvidenceCollector.__init__`. Beyond that a
worker never resolves a server address, reads a timeout, preflights a
socket, probes the server's capabilities or constructs a `Recorder`.
`_WorkerConfigDouble` answers exactly those reads and raises for anything
else.

Pure unit test: a config double stands in for a real ``pytest.Config``, no
subprocess or real xdist session needed.
"""

from __future__ import annotations

from typing import Any

from pytest_vantage.plugin import pytest_configure


class _RegisterCallDouble:
    """A ``pluginmanager.register`` stand-in that records every call. If a
    worker ever constructed a `Recorder`, it would be registered here --
    the most direct place to assert it never is.
    """

    def __init__(self) -> None:
        self.registered: list[object] = []

    def register(self, plugin: object) -> None:
        self.registered.append(plugin)


class _WorkerConfigDouble:
    """A ``pytest.Config`` stand-in carrying xdist's ``workerinput`` marker.

    ``getoption`` answers only the options a worker may read --
    `"vantage"`, `"vantage_failure_text"` and `"capture"` -- and raises for
    anything else: if ``pytest_configure`` or `EvidenceCollector` ever reach
    for a server address, a timeout, or anything else on a worker, this is
    where that would be caught. ``getini`` raises unconditionally -- no ini
    value is read on a worker. ``pluginmanager`` is a
    ``_RegisterCallDouble``, so a worker that ever constructed a `Recorder`
    is caught there too. Opted in (`True`) so the registration path is
    exercised; an opted-out worker would register nothing at all.
    """

    workerinput: dict[str, Any] = {}
    _ALLOWED_OPTIONS = frozenset({"vantage", "capture", "vantage_failure_text"})

    def __init__(self) -> None:
        self.pluginmanager = _RegisterCallDouble()

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


class _ControllerConfigDouble:
    """The non-worker counterpart: no ``workerinput``, so the activation
    check must run -- triangulates that the guard is scoped to xdist workers
    only, not swallowing every invocation.
    """

    def __init__(self) -> None:
        self.options_read: list[str] = []

    def getoption(self, name: str, default: object = None) -> object:
        self.options_read.append(name)
        return False


def test_worker_registers_exactly_one_evidencecollector_when_activated() -> None:
    """A worker's `pytest_configure` must register exactly one
    `EvidenceCollector`, and nothing else -- in particular no `Recorder` --
    when activated."""
    from pytest_vantage.evidence import EvidenceCollector

    config = _WorkerConfigDouble()
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert len(config.pluginmanager.registered) == 1
    (registered,) = config.pluginmanager.registered
    assert isinstance(registered, EvidenceCollector)


def test_no_worker_input_still_runs_the_activation_check() -> None:
    config = _ControllerConfigDouble()
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert config.options_read == ["vantage"]
