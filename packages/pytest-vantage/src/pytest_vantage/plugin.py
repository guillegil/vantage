"""The always-loaded half of the plugin, registered via the ``pytest11`` entry point.

Declared inert by design: this module is imported by pytest on **every**
invocation, activated or not, so it must implement no hook capable of any
side effect. ``pytest_addoption`` and ``pytest_configure`` land here; the
recorder that actually reports lives in ``pytest_vantage.recorder`` and is
registered through ``config.pluginmanager.register(...)`` only when a server
address is configured.

That split is what lets CON-05 and RQ-2 hold together: pytest fires any
``pytest_*`` hook it finds on a registered plugin, so a reporting hook on
this always-imported module would run in every session in the world.

Under ADR-9 this plugin never opens a database. It reports finished sessions
over HTTP using ``urllib`` and encodes them with ``json`` -- standard library
only, so installing it can conflict with nothing in the environment it lands
in (RQ-24).
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest

from pytest_vantage.boundary import _warn
from pytest_vantage.config import (
    resolve_failure_text_capture,
    resolve_liveness_timeout,
    resolve_report_timeout,
    resolve_server_address,
)
from pytest_vantage.evidence import EvidenceCollector
from pytest_vantage.recorder import Recorder
from pytest_vantage.transport import fetch_capabilities

# design.md D6: "connect_timeout: min(2.0, report_timeout), applies to the
# preflight probe" -- the preflight must not itself wait as long as a full
# report would be allowed to.
_MAX_CONNECT_TIMEOUT = 2.0
_DEFAULT_HTTP_PORT = 80
_DEFAULT_HTTPS_PORT = 443


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register every CLI/ini surface. Registering an option is not activating
    it -- see ``_activation_requested`` for the one thing that does (RQ-2).
    """
    group = parser.getgroup("vantage")
    group.addoption(
        "--vantage",
        action="store_true",
        default=False,
        help=(
            "Record this session's run to a vantage server. "
            "This is the ONLY thing that activates recording (RQ-2)."
        ),
    )
    group.addoption(
        "--vantage-server",
        default=None,
        metavar="URL",
        help="Where to report the run. Configures WHERE, never activates recording.",
    )
    group.addoption(
        "--vantage-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Bound, in seconds, on the reporting request. Configures WHERE/HOW, never activates.",
    )
    group.addoption(
        "--vantage-no-failure-text",
        action="store_true",
        default=False,
        help=(
            "Disable failure-text capture (traceback, failure fields, captured output) "
            "for this session. Can only narrow an already-activated session -- never "
            "activates recording on its own (design.md D72)."
        ),
    )
    parser.addini(
        "vantage_server",
        help="Same as --vantage-server. Configures WHERE; never activates recording (RQ-2).",
        default=None,
    )
    parser.addini(
        "vantage_timeout",
        help="Same as --vantage-timeout. Configures WHERE/HOW; never activates recording (RQ-2).",
        default=None,
    )
    parser.addini(
        "vantage_no_failure_text",
        help=(
            "Same as --vantage-no-failure-text. Can only narrow an already-activated "
            "session; never activates recording or disables it on its own (design.md D72)."
        ),
        type="bool",
        default=False,
    )


def _preflight_reachable(address: str, timeout: float) -> bool:
    """A bare TCP connect, not an HTTP request (design.md D6) -- proves only
    that something is listening at `address`, never anything about the
    protocol or route. This function itself sends no bytes -- but, since
    `plugin-server-compatibility`, that is no longer true of the preflight
    step as a whole: `pytest_configure` follows a successful call to this
    function with a real HTTP capability request
    (`transport.fetch_capabilities`, design decisions D38-D42). RQ-2 stays
    untouched either way, because none of this runs until activation has
    already been confirmed -- it is "sends no bytes" that stops being an
    accurate claim about the preflight taken together, not RQ-2.

    `ConnectionRefusedError` (nothing listening) and `socket.gaierror` (the
    host does not resolve) are both `OSError` subclasses, so catching
    `OSError` alone covers RQ-37 criteria 1 and 2 without needing to
    distinguish which one fired -- the caller's response is the same
    either way.
    """
    parsed = urlparse(address)
    default_port = _DEFAULT_HTTPS_PORT if parsed.scheme == "https" else _DEFAULT_HTTP_PORT
    port = parsed.port or default_port
    try:
        socket.create_connection((parsed.hostname, port), timeout=timeout).close()
    except OSError:
        return False
    return True


def _activation_requested(config: pytest.Config) -> bool:
    """Whether recording was activated for this session.

    RQ-2: ``--vantage`` is the ACTIVATION switch and the only one. The
    ``--vantage-server``/``--vantage-timeout`` options, the ``vantage_server``/
    ``vantage_timeout`` ini values and the ``VANTAGE_SERVER`` environment
    variable configure WHERE a report would go -- none of them may turn
    recording on by themselves. A config file committed by one person must
    never silently enable recording for everyone who checks the project out.
    """
    return bool(config.getoption("vantage"))


def _failure_text_capture_requested(config: pytest.Config) -> bool:
    """Whether `EvidenceCollector` should be registered for this session
    (design.md D72). Composes `_activation_requested` with both opt-out
    surfaces through `resolve_failure_text_capture` -- the single monotone
    conjunction that can only narrow an activated session, never enable one.
    Called identically on both the worker and controller branches of
    `pytest_configure`: the opt-out is session-wide, not controller-only.

    Short-circuits before reading either opt-out surface when the session
    was never activated at all (RQ-2): an unactivated worker or controller
    reads `"vantage"` alone, exactly as it did before this decision existed.
    """
    if not _activation_requested(config):
        return False
    return resolve_failure_text_capture(
        activated=True,
        cli_opt_out=bool(config.getoption("vantage_no_failure_text")),
        ini_opt_out=bool(config.getini("vantage_no_failure_text")),
    )


def pytest_configure(config: pytest.Config) -> None:
    """The always-imported hook (design.md D2, D6, D68; design decisions
    D38-D42 for the capability probe).

    1. Under xdist, every worker re-runs this hook as a full pytest session
       of its own. The guard is the FIRST statement: before anything else
       runs, branch on ``workerinput``.
    2. **Worker branch** (design.md D68): a worker is where
       ``pytest_runtest_makereport`` actually fires -- ``item`` and
       ``excinfo`` exist only in the process that ran the test -- so, when
       activated, a worker registers `EvidenceCollector` and returns
       immediately. Nothing past that: no server address is resolved, no
       timeout is read, no preflight runs, no capability probe runs, and no
       `Recorder` is EVER constructed on a worker -- that would break RQ-1's
       "exactly one run entry" (RQ-27's xdist half of the matrix), and
       `EvidenceCollector` alone cannot cause it because it never opens a
       socket.
    3. **Controller branch.** Absent ``--vantage``, this function does
       nothing further on the controller either: no plugin is registered, no
       socket is opened (RQ-2).
    4. Once activated, the controller registers `EvidenceCollector` too --
       a session with no xdist workers at all still needs failure evidence
       collected somewhere -- before anything that could fail or block.
    5. A bare TCP preflight (``_preflight_reachable``) proves something is
       listening at the resolved address before a `Recorder` is registered
       (RQ-37 criteria 1 and 2: refused connection, unresolvable host). A
       failed preflight warns once, naming the address, and returns without
       registering a `Recorder` -- the session then runs to completion with
       `EvidenceCollector` still active but nothing reported.
    6. Once the preflight succeeds, a capability probe
       (``transport.fetch_capabilities``) asks whether the server
       advertises ``session_lifecycle`` -- bounded by
       ``resolve_liveness_timeout``, never the report timeout (D42), so a
       slow or hanging server cannot put the full report timeout in front of
       every session. Anything other than an explicit positive answer,
       including the ``404`` an older server answers (D39), degrades: the
       `Recorder` still gets registered below, but records exactly as the
       previous release did (D41) rather than half-recording against a
       server that cannot finish the job. The `Recorder` is registered once
       activation and the preflight succeed, carrying whatever the
       capability probe found.

    A server that answers the preflight and later disappears, or starts
    failing partway through reporting, is not this function's concern --
    ``pytest_vantage.boundary``'s fault-isolation decorator on every
    ``Recorder`` hook is what catches that (RQ-21), and RQ-37 criterion 3
    ("drops out mid-session") is mechanically that same path, not a second
    preflight.
    """
    if hasattr(config, "workerinput"):
        if _failure_text_capture_requested(config):
            config.pluginmanager.register(EvidenceCollector(config))
        return
    if not _activation_requested(config):
        return
    if _failure_text_capture_requested(config):
        config.pluginmanager.register(EvidenceCollector(config))
    address = resolve_server_address(
        cli_url=config.getoption("vantage_server"),
        env_url=os.environ.get("VANTAGE_SERVER"),
        ini_url=config.getini("vantage_server"),
    )
    timeout = resolve_report_timeout(
        cli_timeout=config.getoption("vantage_timeout"),
        ini_timeout=config.getini("vantage_timeout"),
    )
    connect_timeout = min(_MAX_CONNECT_TIMEOUT, timeout)
    if not _preflight_reachable(address, connect_timeout):
        _warn(config, f"vantage: cannot reach {address}, this session will not be recorded")
        return
    liveness_timeout = resolve_liveness_timeout(timeout)
    lifecycle_available = fetch_capabilities(address, timeout=liveness_timeout)
    config.pluginmanager.register(
        Recorder(config, address, timeout, lifecycle_available=lifecycle_available)
    )
