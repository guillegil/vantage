"""The schema version every adapter stamps and checks, and the refusal they
raise when a database carries another.

One version for every adapter: each adapter's schema describes the same
tables, so a change to one is a change to all of them, and both adapters
refuse any stamp but this one. Kept apart from either adapter so the SQLite
one never imports a database driver and the PostgreSQL one never imports
`sqlite3`.
"""

from __future__ import annotations

# Bumped whenever a schema changes shape. The only statement of the version:
# every adapter stamps it on a database it creates and compares against it
# on one it opens.
_SCHEMA_VERSION = 11


class SchemaVersionError(RuntimeError):
    """The database's schema version does not match what this build
    requires, or the database holds another schema and no stamp at all.

    Raised before anything in the refused database is changed.
    """
