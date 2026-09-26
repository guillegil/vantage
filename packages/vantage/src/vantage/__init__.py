"""Vantage: stores the runs pytest-vantage records and serves their history.

Internal packages, with the dependency arrow pointing inwards:

``vantage.core``
    Domain model, storage port, option resolution. Standard library only,
    enforced by a static import walk rather than by convention.
``vantage.storage``
    Adapters implementing the core's storage port. Depends on the core alone.
``vantage.ingestion``
    A session report validated, converted and recorded in a store it is
    handed. The core, Pydantic and PyYAML; never the web framework.
``vantage.service``
    HTTP surface: the ingestion endpoint the plugin reports to, the read API
    the interface consumes, and the `vantage` command. The only package that
    imports the web framework, which the `server` extra installs.

The pytest plugin is *not* here. It ships as its own distribution
(``pytest-vantage``) and imports nothing from this one, so installing it
never brings a server along.
"""
