"""The always-loaded half of the plugin, registered via the ``pytest11`` entry point.

pytest imports this module on every invocation, activated or not, so it
implements no hook capable of a side effect: only ``pytest_addoption`` and
``pytest_configure``. The recorder that reports lives in
``pytest_vantage.recorder`` and is registered through
``config.pluginmanager.register(...)`` only once ``--vantage`` is given --
pytest fires every ``pytest_*`` hook it finds on a registered plugin, so a
reporting hook here would run in every session.

The plugin never opens a database. It reports sessions over HTTP with
``urllib`` and ``json`` -- standard library only, so installing it can
conflict with nothing in the environment it lands in.
"""

from __future__ import annotations

import socket
from typing import Literal
from urllib.parse import urlparse

import pytest

from pytest_vantage.boundary import _warn
from pytest_vantage.config import (
    VantageConfigError,
    resolve_failure_text_capture,
    resolve_liveness_timeout,
    resolve_metadata_capture,
    resolve_settings,
)
from pytest_vantage.evidence import EvidenceCollector
from pytest_vantage.recorder import Recorder
from pytest_vantage.transport import fetch_capabilities

# The preflight probe waits at most min(2.0, report timeout): it must not
# itself wait as long as a full report is allowed to.
_MAX_CONNECT_TIMEOUT = 2.0
_DEFAULT_HTTP_PORT = 80
_DEFAULT_HTTPS_PORT = 443

# pytest's native TOML table hands `getini` a number for `vantage_timeout =
# 5` and refuses one registered as a string. The float type exists from
# pytest 8.4; older versions read every ini value as text, which
# `resolve_report_timeout` accepts too.
_TIMEOUT_INI_TYPE: Literal["float"] | None = "float" if pytest.version_tuple[:2] >= (8, 4) else None


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register every CLI/ini surface. Registering an option is not activating
    it -- see ``_activation_requested`` for the one thing that does.
    """
    group = parser.getgroup("vantage")
    group.addoption(
        "--vantage",
        action="store_true",
        default=False,
        help=(
            "Record this session's run to a vantage server. "
            "This is the ONLY thing that activates recording."
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
        "--vantage-failure-text",
        action="store_true",
        default=False,
        help=(
            "Enable failure-text capture (traceback, failure fields, captured output) "
            "for this session. Absent by default -- capture never happens "
            "unless this flag is given, there is no ini equivalent, and the flag "
            "cannot activate recording on its own. Stored failure "
            "text is unredacted and may contain any value a test printed or "
            "asserted, including credentials."
        ),
    )
    group.addoption(
        "--vantage-metadata",
        action="store_true",
        default=False,
        help=(
            "Read the files named by vantage-metadata.json in the project root and "
            "record the declared keys with this session. Absent by default -- capture "
            "never happens unless this flag is given, there is no ini equivalent, and "
            "the flag cannot activate recording on its own. Declared "
            "files are read from disk and their declared values are stored; a "
            "configuration file is where credentials live by convention."
        ),
    )
    parser.addini(
        "vantage_server",
        help="Same as --vantage-server. Configures WHERE; never activates recording.",
        default=None,
    )
    parser.addini(
        "vantage_timeout",
        help="Same as --vantage-timeout. Configures WHERE/HOW; never activates recording.",
        type=_TIMEOUT_INI_TYPE,
        default=None,
    )


def _preflight_reachable(address: str, timeout: float) -> bool:
    """A bare TCP connect, not an HTTP request: it sends no bytes and proves
    only that something is listening at `address`, never anything about the
    protocol or route.

    `ConnectionRefusedError` (nothing listening) and `socket.gaierror` (the
    host does not resolve) are both `OSError` subclasses, and the caller's
    response is the same either way. `ValueError` covers what validation
    already refuses -- a bad port, a host the socket layer cannot encode --
    should anything slip past it: the answer is still "cannot reach", never
    an exception out of `pytest_configure`.
    """
    try:
        parsed = urlparse(address)
        default_port = _DEFAULT_HTTPS_PORT if parsed.scheme == "https" else _DEFAULT_HTTP_PORT
        port = parsed.port or default_port
        socket.create_connection((parsed.hostname, port), timeout=timeout).close()
    except (OSError, ValueError):
        return False
    return True


def _activation_requested(config: pytest.Config) -> bool:
    """Whether recording was activated for this session.

    ``--vantage`` is the ACTIVATION switch and the only one. The
    ``--vantage-server``/``--vantage-timeout`` options, the ``vantage_server``/
    ``vantage_timeout`` ini values and the ``VANTAGE_SERVER`` environment
    variable configure WHERE a report would go -- none of them may turn
    recording on by themselves. A config file committed by one person must
    never silently enable recording for everyone who checks the project out.
    """
    return bool(config.getoption("vantage"))


def _failure_text_capture_requested(config: pytest.Config) -> bool:
    """Whether `EvidenceCollector` should be registered for this session:
    only when both `--vantage` and `--vantage-failure-text` are given, with
    no ini or environment equivalent, so a committed configuration file can
    never enable capture.

    Called on both the worker and controller branches of `pytest_configure`,
    since the opt-in is session-wide. An unactivated session short-circuits
    and reads `"vantage"` alone.
    """
    if not _activation_requested(config):
        return False
    return resolve_failure_text_capture(
        activated=True,
        cli_opt_in=bool(config.getoption("vantage_failure_text")),
    )


def _metadata_capture_requested(config: pytest.Config) -> bool:
    """Whether `Recorder` should attempt to read the metadata declaration
    for this session: the same gate as `_failure_text_capture_requested`,
    for the `--vantage-metadata` flag, so a committed configuration file can
    never enable a filesystem read.

    Called only on the controller: no `Recorder` is ever constructed on a
    worker, so the declaration is read once per session regardless of
    worker count.
    """
    if not _activation_requested(config):
        return False
    return resolve_metadata_capture(
        activated=True,
        cli_opt_in=bool(config.getoption("vantage_metadata")),
    )


def pytest_configure(config: pytest.Config) -> None:
    """The always-imported hook: decides what, if anything, to register.

    1. Under xdist every worker re-runs this hook as a full pytest session
       of its own, so the FIRST statement branches on ``workerinput``.
    2. **Worker branch.** ``pytest_runtest_makereport`` fires only in the
       process that ran the test, so a worker registers `EvidenceCollector`
       if failure text was requested, and returns. It never resolves an
       address, reads a timeout, probes the server or constructs a
       `Recorder`: a `Recorder` per worker would record one session as
       several runs.
    3. **Controller branch.** Absent ``--vantage``, nothing further happens:
       no plugin is registered, no socket is opened.
    4. The address and timeout are resolved and validated. An invalid value
       stops the session with a usage error naming the option -- reporting
       somewhere other than intended is worse than not starting.
    5. If failure text was requested, the controller registers
       `EvidenceCollector` too -- a session with no xdist workers still
       needs evidence collected -- before anything that could fail or block.
    6. A bare TCP preflight (``_preflight_reachable``) checks that something
       is listening at the resolved address. If not (connection refused,
       host unresolvable), it warns once, naming the address, and returns
       without a `Recorder`; the suite still runs to completion.
    7. A capability probe (``transport.fetch_capabilities``) asks whether
       the server advertises ``session_lifecycle``, bounded by the liveness
       timeout so a hanging server cannot put the full report timeout in
       front of every session. Anything but an explicit yes, including the
       ``404`` of a server without that route, makes the `Recorder` send
       only the finish report -- no start-write or heartbeats -- rather
       than half-record against a server that cannot finish the job.

    A server that passes the preflight and later disappears or fails is
    handled by ``pytest_vantage.boundary``'s fault-isolation decorator on
    every ``Recorder`` hook, not by this function.
    """
    if hasattr(config, "workerinput"):
        if _failure_text_capture_requested(config):
            config.pluginmanager.register(EvidenceCollector(config))
        return
    if not _activation_requested(config):
        return
    try:
        settings = resolve_settings(config)
    except VantageConfigError as exc:
        raise pytest.UsageError(str(exc)) from None
    if _failure_text_capture_requested(config):
        config.pluginmanager.register(EvidenceCollector(config))
    connect_timeout = min(_MAX_CONNECT_TIMEOUT, settings.timeout)
    if not _preflight_reachable(settings.address, connect_timeout):
        _warn(
            config, f"vantage: cannot reach {settings.address}, this session will not be recorded"
        )
        return
    liveness_timeout = resolve_liveness_timeout(settings.timeout)
    lifecycle_available = fetch_capabilities(settings.address, timeout=liveness_timeout)
    config.pluginmanager.register(
        Recorder(
            config,
            settings.address,
            settings.timeout,
            lifecycle_available=lifecycle_available,
            metadata_requested=_metadata_capture_requested(config),
        )
    )
