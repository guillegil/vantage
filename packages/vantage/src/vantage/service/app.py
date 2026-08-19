"""The app factory (design.md D3, D12).

**Takes an injected `ExecutionStore`.** This proves the port (ADR-3) is a
real seam: PR6 is reviewable without PR2-PR5 (the schema and SQLite adapter)
in scope, because the tests inject `InMemoryExecutionStore` instead of
wiring `SqliteExecutionStore`. `vantage serve` (PR9) is what resolves a real
`SqliteExecutionStore` and passes it in here -- this module never imports
`vantage.storage.sqlite_store`.

**Mounts `/api/v1` and nothing else.** RQ-41's third criterion is served by
absence: there is no unversioned route and no redirect from one. A router
included with any other prefix, or none, would answer where it must 404.

**Every rejection is shaped by `service/errors.py`**, registered here once,
so no route can answer a rejection in a different shape (design.md D5,
RQ-42).
"""

from __future__ import annotations

from fastapi import FastAPI

from vantage.core.config.resolution import DEFAULT_GRACE_PERIOD_SECONDS
from vantage.core.ports.storage import ExecutionStore
from vantage.service.errors import register_error_handlers
from vantage.service.routes.runs import router as runs_router


def create_app(
    store: ExecutionStore, *, grace_period_seconds: float = DEFAULT_GRACE_PERIOD_SECONDS
) -> FastAPI:
    """Build the ASGI app, wired to `store` for every write.

    `grace_period_seconds` reaches `app.state.grace_period` (design.md D34)
    -- a named seam with no reader yet, kept here rather than left as a bare
    literal at every call site so `vantage serve` (`cli.py`) can wire its own
    resolved `ServerConfig.grace_period_seconds` through without this
    module's default ever drifting out of sync with `resolution.py`'s.
    """
    app = FastAPI()
    app.state.store = store
    app.state.grace_period = grace_period_seconds
    app.include_router(runs_router, prefix="/api/v1")
    register_error_handlers(app)
    return app
