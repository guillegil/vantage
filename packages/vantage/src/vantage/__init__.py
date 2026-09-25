"""Vantage server: records runs reported over HTTP and serves their history.

Three internal packages, with the dependency arrow pointing inwards:

``vantage.core``
    Domain model, storage port, option resolution. Standard library only,
    enforced by a static import walk rather than by convention.
``vantage.storage``
    Adapters implementing the core's storage port. Depends on the core alone.
``vantage.service``
    HTTP surface: the ingestion endpoint the plugin reports to, and the
    read API the interface consumes. The only package permitted a
    third-party dependency.

The pytest plugin is *not* here. It reports over HTTP and shares no code with
this package, so it ships as its own distribution (``pytest-vantage``) with no
dependency on this one.
"""
