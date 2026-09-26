"""The app factory.

**Takes an injected `ExecutionStore`.** The `vantage` command (`cli.py`)
resolves a `SqliteExecutionStore` and passes it in; tests pass whichever
store they inspect. This module never imports `vantage.storage.sqlite_store`.

**Mounts `/api/v1` and nothing else.** There is no unversioned route and no
redirect from one, so an unversioned path answers 404.

**No store call runs on the event loop.** A store call blocks -- on the
disk, on the store's own lock, on another process's write -- and one made on
the loop would stall every other request until it returned, heartbeats
included. So every route that reaches the store is a plain `def`, which
FastAPI runs in its threadpool. `POST /runs` is `async` only to stream its
body under the size cap, and hands the rest to the threadpool itself. The
capabilities and interface-document routes never block and stay `async`, so
they answer even while every worker thread waits on the store.

**Every rejection is shaped by `service/errors.py`**, registered here once,
so no route can answer a rejection in a different shape -- nor can the
router, for a path nothing serves or a method a path does not take.

**The store can be closed by the app's own shutdown.** uvicorn stops on
SIGTERM -- how a service manager or container runtime stops a server -- by
shutting the app down and then raising the signal again with its default
action, which ends the process before any code after the server returns.
So `cli.py` asks for `close_store_on_shutdown`, and the store is closed,
and its write-ahead log folded back into the database file, on every
graceful stop. Tests leave it off and keep inspecting the store they pass.

**FastAPI's generated interface documents are disabled.** A document
generated from this route table could never drift from it, so it could
never catch drift either; `routes/read.py`'s `GET /api/v1/openapi.yaml`
serves a hand-written document instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool

from vantage.core.config.resolution import DEFAULT_GRACE_PERIOD_SECONDS
from vantage.core.ports.storage import ExecutionStore
from vantage.service.errors import register_error_handlers
from vantage.service.routes.capabilities import router as capabilities_router
from vantage.service.routes.read import router as read_router
from vantage.service.routes.runs import router as runs_router
from vantage.service.routes.sections import router as sections_router


def create_app(
    store: ExecutionStore,
    *,
    grace_period_seconds: float = DEFAULT_GRACE_PERIOD_SECONDS,
    close_store_on_shutdown: bool = False,
) -> FastAPI:
    """Build the ASGI app, wired to `store` for every write.

    `grace_period_seconds` becomes `app.state.grace_period`, a `timedelta`
    built once here, which the read routes use to decide when a run without
    contact is abandoned. A value no `timedelta` can hold (nan, inf, one
    too large) raises here rather than on every read, and so does one that
    is not positive, which would present every unfinished run as abandoned
    the moment it is read. The default comes from `resolution.py` so the
    two can never drift apart.

    With `close_store_on_shutdown`, the app's shutdown closes `store`,
    off the event loop like every other store call.
    """
    grace_period = timedelta(seconds=grace_period_seconds)
    if grace_period <= timedelta(0):
        raise ValueError(f"grace_period_seconds must be positive, got {grace_period_seconds!r}")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if close_store_on_shutdown:
            await run_in_threadpool(store.close)

    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.grace_period = grace_period
    app.include_router(runs_router, prefix="/api/v1")
    app.include_router(read_router, prefix="/api/v1")
    app.include_router(capabilities_router, prefix="/api/v1")
    app.include_router(sections_router, prefix="/api/v1")
    register_error_handlers(app)
    return app
