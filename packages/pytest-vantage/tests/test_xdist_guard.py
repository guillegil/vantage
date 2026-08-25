"""design.md D2: the xdist guard is the FIRST statement in ``pytest_configure``.

Under xdist every worker re-runs ``pytest_configure`` as a full pytest
session of its own -- unguarded, ``-n 4`` would leave four workers' recorders
plus the controller's, breaking RQ-1's "exactly one run entry" (RQ-27's
xdist half of the process-integration threat matrix). The discriminator is
whether the config object carries a ``workerinput`` attribute.

**The invariant this file proves changed with `failure-capture` (design.md
D68), and the change is deliberate, not a relaxation.** Before D68, nothing
at all ran on a worker: `pytest_configure` returned before reading a single
option. `EvidenceCollector` needs to run `pytest_runtest_makereport` on the
worker -- that is the only process with `item`/`excinfo` -- so the worker
branch now reads exactly three things: `getoption("vantage")` to decide
whether to register at all; and `getoption("vantage_failure_text")`, gated
through the same `resolve_failure_text_capture` the controller uses
(design.md D72, revised after Phase 9's RQ-25 measurement and further
corrected to remove the ini surface entirely -- capture is opt-in, absent
by default, the opt-in is session-wide, not controller-only, and
`--vantage-failure-text` is the only means by which it is granted); and
`EvidenceCollector.__init__` itself reads `getoption("capture")` once
(design.md D71, the empty-vs-absent rule for captured output). **What
survives unchanged is narrower and still absolute**: a worker never
resolves a server address, never reads a timeout, never preflights a
socket, never asks the server's capability endpoint anything, and never
constructs a `Recorder`. `_WorkerConfigDouble` therefore answers exactly
those option/ini reads and raises for anything else -- the strongest
available proof that the worker path reads exactly those things and
touches nothing past them.

Pure unit test: a config double stands in for a real ``pytest.Config``, no
subprocess or real xdist session needed.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_vantage.plugin import pytest_configure


class _RegisterCallDouble:
    """A ``pluginmanager.register`` stand-in that records every call (task
    4.21, design.md D36). If a worker ever constructed a `Recorder`,
    `pytest_configure` would end its guarded path here -- this is the most
    direct place to assert it never does, one step past the existing
    `getoption`-raises proof that the guard runs first.
    """

    def __init__(self) -> None:
        self.registered: list[object] = []

    def register(self, plugin: object) -> None:
        self.registered.append(plugin)


class _WorkerConfigDouble:
    """A ``pytest.Config`` stand-in carrying xdist's ``workerinput`` marker.

    ``getoption`` answers only the option names D68/D71/D72 require a
    worker to read -- `"vantage"`, `"vantage_failure_text"` (CLI only, no
    ini form exists any more) and `"capture"` -- and raises for anything
    else: if ``pytest_configure`` or `EvidenceCollector` ever reach for a
    server address, a timeout, or anything else on a worker, this is where
    that would be caught. ``getini`` raises unconditionally -- no ini value
    is ever permitted on the opt-in path, on a worker or the controller,
    now that the capability spec's "no committed configuration file MAY be
    the means by which capture is enabled" requirement has removed that
    surface entirely. ``pluginmanager`` is a ``_RegisterCallDouble``, so a
    worker that ever constructed a `Recorder` (never permitted, D68) is
    caught there too. Opted in (`True`) so this double still exercises the
    registration mechanism D68 proves -- under D72's revised default-absent
    polarity, an opted-out worker would register nothing at all, which is
    a different (and separately covered) test.
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
            f"(design.md D68/D71/D72 -- only {sorted(self._ALLOWED_OPTIONS)!r} may be read there)"
        )

    def getini(self, name: str) -> object:
        raise AssertionError(
            f"pytest_configure must not read ini value {name!r} on an xdist worker "
            "(design.md D72, further corrected -- no ini value is ever read there)"
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
    """design.md D68 -- the highest-value unit-level RED test for this
    decision (task 2.1). Confirmed failing on `ImportError` before
    `pytest_vantage.evidence` existed: a worker's `pytest_configure` must
    register exactly one `EvidenceCollector`, and nothing else, when
    activated."""
    from pytest_vantage.evidence import EvidenceCollector

    config = _WorkerConfigDouble()
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert len(config.pluginmanager.registered) == 1
    (registered,) = config.pluginmanager.registered
    assert isinstance(registered, EvidenceCollector)


@pytest.mark.req(id="RQ-1")
@pytest.mark.req(id="RQ-27")
def test_worker_never_registers_a_recorder_even_when_activated() -> None:
    """design.md D68: the narrowed invariant. A worker MAY now register an
    `EvidenceCollector`, but it must never construct a `Recorder` -- no
    worker opens a socket, not even indirectly through the reporting path
    RQ-1/RQ-27 exist to protect."""
    from pytest_vantage.recorder import Recorder

    config = _WorkerConfigDouble()
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert not any(isinstance(plugin, Recorder) for plugin in config.pluginmanager.registered)


@pytest.mark.req(id="RQ-1")
@pytest.mark.req(id="RQ-27")
def test_no_worker_input_still_runs_the_activation_check() -> None:
    config = _ControllerConfigDouble()
    pytest_configure(config)  # type: ignore[arg-type]  # deliberately not a real Config

    assert config.options_read == ["vantage"]
