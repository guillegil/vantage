"""Storage adapters implementing the core's storage port.

SQLite via the standard library first (ADR-6, hand-written SQL, no ORM);
PostgreSQL through the ``vantage[postgres]`` extra later. Which adapter runs
is chosen by a connection URL in configuration, never by an import.

Depends on ``vantage.core`` and nothing else. A second real adapter is what
proves the port was ever honest -- an in-memory one shares a process and has
no SQL dialect, so it proves the port is *clean*, not that swapping engines
is cheap.
"""
