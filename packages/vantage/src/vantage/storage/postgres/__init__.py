"""The PostgreSQL adapter: `PostgresExecutionStore`, and `PostgresOpenError`
for a database it cannot open.

The only part of `vantage` that imports `psycopg` or `psycopg_pool`, which
the `postgres` extra installs. Nothing imports this package until a
PostgreSQL database is asked for, so an install without the extra serves
SQLite as before; importing it without the driver raises `ImportError`.
"""

from vantage.storage.postgres.connection import PostgresOpenError
from vantage.storage.postgres.store import PostgresExecutionStore

__all__ = ["PostgresExecutionStore", "PostgresOpenError"]
