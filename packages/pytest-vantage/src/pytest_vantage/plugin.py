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
from pytest_vantage.config import resolve_report_timeout, resolve_server_address
from pytest_vantage.recorder import Recorder

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


def _preflight_reachable(address: str, timeout: float) -> bool:
    """A bare TCP connect, not an HTTP request (design.md D6) -- proves only
    that something is listening at `address`, never anything about the
    protocol or route. Sends no bytes, so RQ-2 stays untouched: this only
    runs once activation has already been confirmed.

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


def pytest_configure(config: pytest.Config) -> None:
    """The always-imported hook. Four steps, in this order (design.md D2, D6):

    1. Under xdist, every worker re-runs this hook -- unguarded, ``-n 4``
       would leave four workers' recorders plus the controller's, breaking
       RQ-1's "exactly one run entry" (RQ-27's xdist half of the matrix).
       The guard is the FIRST statement: before the activation check and
       before anything could register a recorder, probe a socket, or open
       one.
    2. Only then is activation checked. Absent ``--vantage``, this function
       does nothing further: no recorder is registered, no socket is opened
       (RQ-2).
    3. A bare TCP preflight (``_preflight_reachable``) proves something is
       listening at the resolved address before anything is registered
       (RQ-37 criteria 1 and 2: refused connection, unresolvable host). A
       failed preflight warns once, naming the address, and returns without
       registering a recorder -- the session then runs to completion with
       zero further overhead and nothing recorded.
    4. The recorder is registered only once both activation and the
       preflight succeed.

    A server that answers the preflight and later disappears, or starts
    failing partway through reporting, is not this function's concern --
    ``pytest_vantage.boundary``'s fault-isolation decorator on every
    ``Recorder`` hook is what catches that (RQ-21), and RQ-37 criterion 3
    ("drops out mid-session") is mechanically that same path, not a second
    preflight.
    """
    if hasattr(config, "workerinput"):
        return
    if not _activation_requested(config):
        return
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
    config.pluginmanager.register(Recorder(config, address, timeout))
