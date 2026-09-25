"""The app factory.

**Takes an injected `ExecutionStore`.** Tests pass `InMemoryExecutionStore`;
the `vantage` command (`cli.py`) resolves a `SqliteExecutionStore` and
passes it in. This module never imports `vantage.storage.sqlite_store`.

**Mounts `/api/v1` and nothing else.** There is no unversioned route and no
redirect from one, so an unversioned path answers 404.

**Every rejection is shaped by `service/errors.py`**, registered here once,
so no route can answer a rejection in a different shape -- nor can the
router, for a path nothing serves or a method a path does not take.

**FastAPI's generated interface documents are disabled.** A document
generated from this route table could never drift from it, so it could
never catch drift either; `routes/read.py`'s `GET /api/v1/openapi.yaml`
serves a hand-written document instead.
"""

from __future__ import annotations

from fastapi import FastAPI

from vantage.core.config.resolution import DEFAULT_GRACE_PERIOD_SECONDS
from vantage.core.ports.storage import ExecutionStore
from vantage.service.errors import register_error_handlers
from vantage.service.routes.capabilities import router as capabilities_router
from vantage.service.routes.read import router as read_router
from vantage.service.routes.runs import router as runs_router
from vantage.service.routes.sections import router as sections_router


def create_app(
    store: ExecutionStore, *, grace_period_seconds: float = DEFAULT_GRACE_PERIOD_SECONDS
) -> FastAPI:
    """Build the ASGI app, wired to `store` for every write.

    `grace_period_seconds` is stored as `app.state.grace_period`, which the
    read routes use to decide when a run without contact is abandoned. The
    default comes from `resolution.py` so the two can never drift apart.
    """
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.grace_period = grace_period_seconds
    app.include_router(runs_router, prefix="/api/v1")
    app.include_router(read_router, prefix="/api/v1")
    app.include_router(capabilities_router, prefix="/api/v1")
    app.include_router(sections_router, prefix="/api/v1")
    register_error_handlers(app)
    return app
