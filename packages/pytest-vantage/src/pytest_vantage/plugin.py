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
