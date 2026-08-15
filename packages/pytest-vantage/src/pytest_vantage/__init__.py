"""Vantage's pytest plugin: opt-in run recording, reported over HTTP.

Standard library only. Under ADR-9 this package holds no database code, no
schema knowledge and no storage port -- it reports finished sessions to a
Vantage server and that server performs every write. Its entire contract is
a versioned HTTP API.

That is what makes RQ-24 hold by construction: installing this adds nothing
to a user's environment that could conflict with anything, because there is
nothing to add. It is also what makes a plugin for another test runner
possible later, since the boundary is a protocol rather than an import.
"""
