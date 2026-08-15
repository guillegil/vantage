"""Vantage server: records runs reported over HTTP and serves their history.

Three internal packages, with the dependency arrow pointing inwards:

``vantage.core``
    Domain model, storage port, option resolution. Standard library only
    (RQ-26), enforced by a static import walk rather than by convention.
``vantage.storage``
    Adapters implementing the core's storage port -- SQLite first (ADR-6),
    others later. Depends on the core alone.
``vantage.service``
    HTTP surface: the ingestion endpoint the plugin reports to, and the
    read API the interface consumes. The only package permitted a
    third-party dependency.

The pytest plugin is *not* here. Under ADR-9 it reports over HTTP and shares
no code with this package, which is why it ships as its own distribution
(``pytest-vantage``) with no dependency on this one.
"""
