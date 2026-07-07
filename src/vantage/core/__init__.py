"""Vantage core -- producer- and storage-agnostic domain logic.

Zero I/O: this package and its sub-packages never import sqlalchemy,
sqlite3, fastapi, uvicorn, apscheduler, or pytest. Concrete implementations
live in `vantage.adapters`, `vantage.server`, and `vantage.collector` (design
§0 -- the decoupling litmus).
"""
