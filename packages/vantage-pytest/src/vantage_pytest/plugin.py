"""The always-loaded half of the plugin, registered via the ``pytest11`` entry point.

Declared inert by design (D4): this module is imported by pytest on every
invocation, activated or not, so it must implement no hook capable of
touching disk. ``pytest_addoption`` and ``pytest_configure`` land here in
Milestone 1 slice C; the recorder that does the actual writing is a
separate object (``vantage_pytest.recorder.Recorder``) registered only on
activation, so this module stays safe to import unconditionally.
"""
