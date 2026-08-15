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

import pytest


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
    """The always-imported hook. Two gates, in this order, before anything
    else may run (design.md D2):

    1. Under xdist, every worker re-runs this hook -- unguarded, ``-n 4``
       would register four recorders plus the controller's, breaking RQ-1's
       "exactly one run entry". The guard is the FIRST statement, before the
       activation check and before anything could register or open a
       socket.
    2. Only then is activation checked. Absent ``--vantage``, this function
       does nothing further: no recorder is registered, no socket is opened
       (RQ-2).
    """
    if hasattr(config, "workerinput"):
        return
    if not _activation_requested(config):
        return
    # The recorder is registered here from PR11 onward, once it exists.
