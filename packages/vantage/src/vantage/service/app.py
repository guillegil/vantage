"""The app factory.

**Takes an injected `ExecutionStore`.** The `vantage` command (`cli.py`)
resolves a `SqliteExecutionStore` and passes it in; tests pass whichever
store they inspect. This module never imports `vantage.storage.sqlite_store`.

**Mounts `/api/v1`, and the web client's files at every other `GET` and
`HEAD` path when given one** (`service/web.py`). There is no unversioned
route and no redirect from one, so an unversioned path under `/api` still
answers 404, and so does every other path of an app given no client. The
client is served by middleware, not routes, so the route table the
interface document is checked against holds the API alone. Every answer
carries the security headers `SecurityHeaders` adds, outermost of the
middlewares the app adds.

**No store call runs on the event loop.** A store call blocks -- on the
disk, on the store's own lock, on another process's write -- and one made on
the loop would stall every other request until it returned, heartbeats
included. So every route that reaches the store is a plain `def`, which
FastAPI runs in its threadpool. `POST /runs`, `POST /projects`, a
project's `POST .../config/sections` and `PUT .../members/{user}`,
`POST /users`, `PATCH /users/{name}`, `PUT /users/{name}/password`,
`POST /tokens`, `POST /login` and `POST /password` are `async` only to
stream their bodies under a size cap, and hand the rest to the threadpool
themselves (`service/body.py`). The capabilities and interface-document
routes never block and stay `async`, so they answer even while every
worker thread waits on the store.

**Every route but four needs a token once the database has a user**
(`service/access.py`): the capability advertisement and the interface
document stay open, since a client asks them before it can know it needs
one, and logging in and changing a password take a name and a password
instead. `app.state.access_required` starts false and becomes true, for
good, the first time a request finds a user. The users and tokens routes
need an admin's token, and the members routes a user's, whether or not the
database has a user. Within a project a request also needs a role there,
read from the store on every request and never kept in `app.state`.
`create_app` never makes a user: `cli.py` gives a database its first admin
before serving it.

**At most two password hashes at once, and 32 requests waiting for one**
(`app.state.password_slots`, `service/slots.py`). Each hash takes about
0.2 s of CPU and 32 MiB, in the threadpool; a login waits for a slot on the
event loop, holding no thread, and one past the waiting limit is refused
at once, so a flood of logins can neither grow memory nor keep a report or
a heartbeat from a thread.

**Every rejection is shaped by `service/errors.py`**, registered here once,
so no route can answer a rejection in a different shape -- nor can the
router, for a path nothing serves or a method a path does not take.

**The store can be closed by the app's own shutdown.** uvicorn stops on
SIGTERM -- how a service manager or container runtime stops a server -- by
shutting the app down and then raising the signal again with its default
action, which ends the process before any code after the server returns
(anywhere but as a container's PID 1, which the kernel does not let a
default action end).
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
from pathlib import Path

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool

from vantage.core.config.resolution import DEFAULT_GRACE_PERIOD_SECONDS
from vantage.core.ports.storage import ExecutionStore
from vantage.service.errors import register_error_handlers
from vantage.service.routes.capabilities import router as capabilities_router
from vantage.service.routes.login import router as login_router
from vantage.service.routes.members import router as members_router
from vantage.service.routes.projects import router as projects_router
from vantage.service.routes.read import router as read_router
from vantage.service.routes.runs import router as runs_router
from vantage.service.routes.sections import router as sections_router
from vantage.service.routes.users import router as users_router
from vantage.service.slots import PasswordSlots
from vantage.service.web import SecurityHeaders, WebClient, load_client

# How many password hashes the app computes at once -- two of the
# threadpool's forty threads and 64 MiB at most -- and how many requests may
# wait for one: about three seconds of hashing, and half a MiB of bodies.
_PASSWORD_HASHES_AT_ONCE = 2
_PASSWORD_HASHES_WAITING = 32


def create_app(
    store: ExecutionStore,
    *,
    grace_period_seconds: float = DEFAULT_GRACE_PERIOD_SECONDS,
    close_store_on_shutdown: bool = False,
    client: Path | None = None,
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

    `client` is the directory a build of the web client is in, read once
    here; one that holds no build serves a page saying so. Without it the
    app serves no client at all, as every test that builds an app for the
    API expects, whether or not the client has been built in the checkout.
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
    app.state.access_required = False
    app.state.password_slots = PasswordSlots(
        running=_PASSWORD_HASHES_AT_ONCE, waiting=_PASSWORD_HASHES_WAITING
    )
    app.include_router(runs_router, prefix="/api/v1")
    app.include_router(read_router, prefix="/api/v1")
    app.include_router(capabilities_router, prefix="/api/v1")
    app.include_router(sections_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(members_router, prefix="/api/v1")
    app.include_router(login_router, prefix="/api/v1")
    register_error_handlers(app)
    if client is not None:
        app.add_middleware(WebClient, files=load_client(client))
    # Added last, so it is outermost and sees every answer the others make.
    app.add_middleware(SecurityHeaders)
    return app
