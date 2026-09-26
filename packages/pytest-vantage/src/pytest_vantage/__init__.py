"""Vantage's pytest plugin: opt-in run recording, reported over HTTP.

Standard library only. This package holds no database code, no schema
knowledge and no storage port -- it reports sessions to a Vantage server and
that server performs every write. Its entire contract is a versioned HTTP
API.

So installing it adds nothing to a user's environment that could conflict
with anything, and a plugin for another test runner stays possible, since
the boundary is a protocol rather than an import.
"""
