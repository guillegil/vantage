"""The typed dependencies through which routes read what `create_app` put
in `app.state`.

`app.state` is untyped, so a route reading it directly would make every
store call unchecked. These are its only readers.

Both are `async def` though neither awaits: FastAPI runs a plain `def`
dependency in its threadpool, and an attribute read is not worth a thread --
nor worth waiting for one while slow store calls hold them all.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import Request

from vantage.core.ports.storage import ExecutionStore
from vantage.service.slots import PasswordSlots


async def get_store(request: Request) -> ExecutionStore:
    """The store `create_app` was given."""
    store: ExecutionStore = request.app.state.store
    return store


async def get_grace_period(request: Request) -> timedelta:
    """How long a run may go without contact before it reads as abandoned."""
    grace_period: timedelta = request.app.state.grace_period
    return grace_period


async def get_password_slots(request: Request) -> PasswordSlots:
    """What every password hash the server computes waits on
    (`routes/login.py`)."""
    slots: PasswordSlots = request.app.state.password_slots
    return slots


__all__ = ["get_grace_period", "get_password_slots", "get_store"]
