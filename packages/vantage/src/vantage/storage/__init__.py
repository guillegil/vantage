"""Storage adapters implementing the core's storage port.

`SqliteExecutionStore` uses the standard library's `sqlite3` with
hand-written SQL and no ORM, and depends on ``vantage.core`` and nothing
else. `vantage.storage.postgres` holds `PostgresExecutionStore`, the one
module that imports a third-party driver; nothing here imports it, so a
SQLite-only install never needs the driver.
"""
