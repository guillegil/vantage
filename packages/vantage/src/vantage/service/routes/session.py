"""A browser's session: `POST`, `GET` and `DELETE /api/v1/session`.

**Signing in trades a name and a password for a login token kept in a
cookie** (`SESSION_COOKIE`), as `/login` trades them for one in its body:
the same checks in the same order (`log_in_with_password`), the same
token, holding read and manage, and admin for an admin, never record, for
`LOGIN_TOKEN_LIFETIME`. The cookie is HttpOnly, so page script -- the
web client's, or anything injected into it -- never holds the token, and
the answer names the user and when the session ends, never the token. It
is Secure and SameSite=Strict, and lives exactly as long as its token. A
flag on `/login` would not do: that route answers the token in its body.

**Signing in or out needs a request from vantage's own pages**
(`requires_same_origin`), whether or not a cookie is sent: otherwise a page
on another port of the same host could sign the browser in as someone else,
so that what it records next is theirs, or sign it out.

**Asking who you are** answers for any credential the other routes take --
the cookie, or a token in the `Authorization` header -- and for nobody on
an open server, which says so. A closed server asked with none answers
`401`, which is how the web client learns to show its sign-in page.

**Signing out revokes the cookie's token** when it still authenticates,
and always answers `204` clearing the cookie: one absent, malformed,
expired or revoked is simply cleared, on any server. The `Authorization`
header is never read here; `POST /tokens/{token_id}/revoke` revokes a
token sent that way. No `Clear-Site-Data`, which would clear the cookies
of every other service on the same host.

**Every password change ends the session**: it revokes every login token
of its user, the cookie's included, and so does disabling the user.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from vantage.core.domain.access import (
    ADMIN_SCOPE,
    LOGIN_TOKEN_LIFETIME,
    token_digest,
    well_formed_token,
)
from vantage.core.ports.storage import ExecutionStore
from vantage.service.access import (
    SESSION_COOKIE,
    Caller,
    requires_closed_server,
    requires_read,
    requires_same_origin,
)
from vantage.service.dependencies import get_password_slots, get_store
from vantage.service.routes.login import log_in_with_password
from vantage.service.schemas import SessionResponse, SessionUserResponse
from vantage.service.slots import PasswordSlots

router = APIRouter()

# The cookie's Max-Age: its token's lifetime, which starts as it is set.
_SESSION_SECONDS = int(LOGIN_TOKEN_LIFETIME.total_seconds())


def _set_session_cookie(response: Response, value: str, max_age: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=max_age,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


@router.post(
    "/session",
    dependencies=[Depends(requires_closed_server), Depends(requires_same_origin)],
)
async def sign_in(
    request: Request,
    store: ExecutionStore = Depends(get_store),
    slots: PasswordSlots = Depends(get_password_slots),
) -> JSONResponse:
    """Sign a browser in for a name and its password."""
    secret, token = await log_in_with_password(request, store, slots)
    session = SessionResponse(
        open=False,
        user=SessionUserResponse(name=token.user, admin=ADMIN_SCOPE in token.scopes),
        expires_at=token.expires_at,
    )
    response = JSONResponse(status_code=201, content=session.model_dump(mode="json"))
    response.headers["Cache-Control"] = "no-store"
    _set_session_cookie(response, secret, _SESSION_SECONDS)
    return response


@router.get("/session")
def get_session(caller: Caller = Depends(requires_read)) -> SessionResponse:
    """Who the caller is, and when their token stops authenticating; or,
    on an open server, that nobody need be anyone."""
    if caller.user is None:
        return SessionResponse(open=True, user=None, expires_at=None)
    return SessionResponse(
        open=False,
        user=SessionUserResponse(name=caller.user, admin=caller.administers),
        expires_at=caller.expires_at,
    )


@router.delete("/session", dependencies=[Depends(requires_same_origin)])
def sign_out(request: Request, store: ExecutionStore = Depends(get_store)) -> Response:
    """Revoke the cookie's token, if it still authenticates, and clear the
    cookie."""
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie is not None and well_formed_token(cookie):
        now = datetime.now(timezone.utc)
        grant = store.authenticate(token_digest(cookie), now=now)
        if grant is not None:
            store.revoke_token(grant.token_id, revoked_at=now, user=grant.user)
    response = Response(status_code=204)
    _set_session_cookie(response, "", 0)
    return response


__all__ = ["router"]
