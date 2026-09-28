"""`PasswordSlots`: how many password hashes the server computes at once,
and how many requests may wait for one.

A hash takes about 0.2 s of CPU and 32 MiB, and anyone may ask for one by
logging in, so both are bounded. `running` requests hash at once, each in
the threadpool; up to `waiting` more wait on the event loop, holding no
thread; any beyond those are refused at once (`PasswordChecksBusyError`),
so a flood of logins -- even from clients that send a body and leave --
can neither grow memory without bound nor queue minutes of hashing ahead
of the next real login. The count is only ever changed on the event loop,
so it needs no lock.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from vantage.service.errors import PasswordChecksBusyError


class PasswordSlots:
    """At most `running` password hashes at once, and `waiting` more
    requests waiting for one."""

    def __init__(self, *, running: int, waiting: int) -> None:
        self._running = asyncio.Semaphore(running)
        self._limit = running + waiting
        self._admitted = 0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold a slot for the body of the `async with`, once one is free;
        or refuse with `PasswordChecksBusyError` when as many requests hold
        or wait for one as the limit allows. The semaphore binds to the loop
        the first request waits on."""
        if self._admitted >= self._limit:
            raise PasswordChecksBusyError()
        self._admitted += 1
        try:
            async with self._running:
                yield
        finally:
            self._admitted -= 1


__all__ = ["PasswordSlots"]
