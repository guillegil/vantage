"""Who is asking, and whether they may: the dependency every route declares
but the capability advertisement and the interface document.

**A server with no user is open.** A database nobody has made a user in
serves anyone, as the server did before it had users, so `vantage` serving
a local database on a test machine needs no token. Once a user exists every
route here needs `Authorization: Bearer <token>`, of a live token of an
enabled user whose grant covers the route's scope (`Grant.allows`).

**The first user closes a running server.** A user is made with `vantage
user add`, often against a database a server is already serving, so while
the server is open each request asks the store whether a user exists yet.
Users are never deleted, so once one does, `app.state.access_required`
remembers it and the store is not asked again.

**Managing users and tokens needs a user, always** (`requires_admin_token`).
On an open server the anonymous caller may do anything else, but not this:
the one thing it could do here is make the first user, which only the
`vantage` command does. It is refused for who it is, never for what the
store says, so a request racing the first `vantage user add` cannot slip
through between the two, reads included.

**A token is checked wherever it is sent.** On an open server no token
authenticates, since tokens belong to users; a request carrying one is
refused rather than served as if it carried none, so a client that means
to act as a user finds out it is not one.

**A plain `def`.** Authenticating reads the store, which never happens on
the event loop, so FastAPI runs the dependency in its threadpool, as it runs
the routes that read the store. On `POST /runs` it runs before the body is
read, so a request that may not record is refused unread.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI, Request

from vantage.core.domain.access import (
    ADMIN_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    token_digest,
    well_formed_token,
)
from vantage.core.ports.storage import ExecutionStore
from vantage.service.errors import (
    InsufficientScopeError,
    OpenServerError,
    UnauthenticatedError,
)


@dataclass(frozen=True, slots=True)
class Caller:
    """Who a request acts as: the user its token belongs to, or `None` on
    a server that has no user."""

    user: str | None


def _server_open(app: FastAPI, store: ExecutionStore) -> bool:
    if app.state.access_required:
        return False
    if store.access_required():
        app.state.access_required = True
        return False
    return True


def authorize(request: Request, scope: str) -> Caller:
    """The caller of `request`, if it may act within `scope`; otherwise
    `UnauthenticatedError` or `InsufficientScopeError`."""
    store: ExecutionStore = request.app.state.store
    header = request.headers.get("authorization")
    if header is None:
        if _server_open(request.app, store):
            return Caller(user=None)
        raise UnauthenticatedError.missing()
    scheme, _, token = header.strip().partition(" ")
    token = token.strip()
    # A token that could never be one is refused without asking the store.
    if scheme.lower() != "bearer" or not well_formed_token(token):
        raise UnauthenticatedError.invalid()
    grant = store.authenticate(token_digest(token))
    if grant is None:
        raise UnauthenticatedError.invalid()
    if not grant.allows(scope):
        raise InsufficientScopeError(scope)
    return Caller(user=grant.user)


def _requires(scope: str) -> Callable[[Request], Caller]:
    def dependency(request: Request) -> Caller:
        return authorize(request, scope)

    dependency.__name__ = f"requires_{scope}"
    return dependency


requires_read = _requires(READ_SCOPE)
"""Reading runs, results, history and sections."""

requires_record = _requires(RECORD_SCOPE)
"""Sending a report or a heartbeat."""

requires_admin = _requires(ADMIN_SCOPE)
"""Changing what every user shares: the section definitions."""


def requires_admin_token(request: Request) -> Caller:
    """Managing users and tokens: a live admin-scope token of an enabled
    admin user, on every server. The anonymous caller an open server lets
    through is refused with `OpenServerError`."""
    caller = authorize(request, ADMIN_SCOPE)
    if caller.user is None:
        raise OpenServerError()
    return caller


__all__ = [
    "Caller",
    "authorize",
    "requires_admin",
    "requires_admin_token",
    "requires_read",
    "requires_record",
]
