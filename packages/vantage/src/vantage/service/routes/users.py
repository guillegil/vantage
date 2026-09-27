"""The users and tokens routes: `GET`/`POST /api/v1/users`,
`PATCH /api/v1/users/{name}`, `GET`/`POST /api/v1/tokens` and
`POST /api/v1/tokens/{token_id}/revoke` -- over HTTP, what `vantage user`
and `vantage token` do on the database.

**Every one needs an admin's token, on every server** (`requires_admin_token`):
an open server answers `409 open_server` to an anonymous caller here, so the
first user is always made by the `vantage` command. The dependency runs
before the path is validated or the body read, so a caller who may not
manage users learns nothing of which names or ids exist.

**The command line's checks, in its order**, through the same domain rules
(`core/domain/access.py`): a scope list, then a label, then the user, then
whether the admin scope is one its user may hold. A value that cannot be a
name is a user nobody has -- `404`, or an empty list -- and is never handed
to the store, which keeps U+0000 from every adapter; only `POST /users`,
which makes a name, calls it invalid.

**Two differences from the command line, both deliberate.** An admin cannot
demote or disable their own user here (`409 own_account`), which is decided
by who asks and so races nothing; the command line stays the way to recover
when no admin is left. And revoking is idempotent: a token revoked already
answers `200` with its first revocation time, so a client that retries after
a lost answer reads the same thing twice.

**The token is in one answer only**, the `201` of `POST /tokens`, marked
`Cache-Control: no-store`: never in a URL, a list or a rejection, and never
stored itself.

**Bodies** are read as `POST /config/sections` reads its own
(`service/body.py`): the media type from the header, at most
`MAX_USERS_BODY_BYTES` streamed, then the parse, validation and store write
in the threadpool. The routes without a body are plain `def`s.

**Several servers on one PostgreSQL database** need nothing more: every
write here is one statement or one transaction in the store, and the only
read before a write -- whether a token's user is an admin -- grants nothing
if it goes stale, since `authenticate` reads the user's standing each time
the token is used.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from vantage.core.domain.access import (
    ADMIN_SCOPE,
    InvalidScopesError,
    InvalidTokenLabelError,
    InvalidUserNameError,
    Token,
    User,
    check_scopes,
    check_token_label,
    check_user_name,
    new_token,
    token_digest,
)
from vantage.core.ports.storage import ExecutionStore, UnknownUserError, UserExistsError
from vantage.ingestion.decode import decode_json
from vantage.service.access import Caller, requires_admin_token
from vantage.service.body import read_bounded_body, require_json_media_type
from vantage.service.dependencies import get_store
from vantage.service.errors import (
    InvalidTokenRequestError,
    InvalidUserRequestError,
    NoSuchUserError,
    NotAnAdminError,
    OwnAccountError,
    ScopesRefusedError,
    TokenLabelRefusedError,
    UnknownTokenError,
    UserNameRefusedError,
    UserNameTakenError,
)
from vantage.service.schemas import (
    CreatedTokenResponse,
    TokenCreateRequest,
    TokenListResponse,
    TokenResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)

router = APIRouter()

# A body's cap. The largest a valid body can need is a 64-character name
# with every character spelt as a 6-byte escape, and a 200-character label
# with every character spelt as a 12-byte escaped surrogate pair: under
# 3 KiB. The rest leaves room for whitespace and a repeated scope list.
MAX_USERS_BODY_BYTES = 16 * 1024

# The ids a signed 64-bit key holds; SQLite cannot bind a larger one.
_MAX_TOKEN_ID = 2**63 - 1


def _can_be_a_name(name: str) -> bool:
    try:
        check_user_name(name)
    except InvalidUserNameError:
        return False
    return True


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        name=user.name, admin=user.admin, disabled=user.disabled, created_at=user.created_at
    )


def _token_response(token: Token) -> TokenResponse:
    return TokenResponse(
        id=token.id,
        user=token.user,
        label=token.label,
        scopes=sorted(token.scopes),
        created_at=token.created_at,
        revoked_at=token.revoked_at,
    )


def _json(model: BaseModel, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=model.model_dump(mode="json"))


@router.get("/users")
def list_users(
    _caller: Caller = Depends(requires_admin_token),
    store: ExecutionStore = Depends(get_store),
) -> UserListResponse:
    """Every user, disabled ones included, by name, whole -- as `vantage
    user list` prints them."""
    return UserListResponse(items=[_user_response(user) for user in store.list_users()])


def _create_user(store: ExecutionStore, body: bytes) -> UserResponse:
    try:
        payload = UserCreateRequest.model_validate(decode_json(body))
    except ValidationError as exc:
        raise InvalidUserRequestError.from_errors(exc.errors()) from exc
    if not _can_be_a_name(payload.name):
        raise UserNameRefusedError()
    try:
        user = store.create_user(
            payload.name, admin=payload.admin, created_at=datetime.now(timezone.utc)
        )
    except UserExistsError:
        raise UserNameTakenError() from None
    return _user_response(user)


@router.post("/users")
async def create_user(
    request: Request,
    _caller: Caller = Depends(requires_admin_token),
    store: ExecutionStore = Depends(get_store),
) -> JSONResponse:
    require_json_media_type(request)
    body = await read_bounded_body(request, MAX_USERS_BODY_BYTES)
    return _json(await run_in_threadpool(_create_user, store, body), status_code=201)


def _update_user(store: ExecutionStore, name: str, caller: Caller, body: bytes) -> UserResponse:
    try:
        payload = UserUpdateRequest.model_validate(decode_json(body))
    except ValidationError as exc:
        raise InvalidUserRequestError.from_errors(exc.errors()) from exc
    if payload.admin is None and payload.disabled is None:
        raise InvalidUserRequestError.nothing_to_change()
    if name == caller.user and (payload.admin is False or payload.disabled is True):
        raise OwnAccountError()
    user = store.update_user(name, admin=payload.admin, disabled=payload.disabled)
    if user is None:
        raise NoSuchUserError()
    return _user_response(user)


@router.patch("/users/{name}")
async def update_user(
    request: Request,
    name: str = Path(),
    caller: Caller = Depends(requires_admin_token),
    store: ExecutionStore = Depends(get_store),
) -> JSONResponse:
    """Set `admin`, `disabled` or both, and answer the user as it now
    stands. A name nobody can have is answered before the body is read."""
    require_json_media_type(request)
    if not _can_be_a_name(name):
        raise NoSuchUserError()
    body = await read_bounded_body(request, MAX_USERS_BODY_BYTES)
    return _json(await run_in_threadpool(_update_user, store, name, caller, body))


@router.get("/tokens")
def list_tokens(
    user: str | None = Query(default=None),
    _caller: Caller = Depends(requires_admin_token),
    store: ExecutionStore = Depends(get_store),
) -> TokenListResponse:
    """Every token, or `user`'s, revoked ones included, oldest first, whole
    -- as `vantage token list [USER]` prints them. A user nobody can be has
    none, and the store is not asked."""
    if user is not None and not _can_be_a_name(user):
        return TokenListResponse(items=[])
    return TokenListResponse(
        items=[_token_response(token) for token in store.list_tokens(user=user)]
    )


def _create_token(store: ExecutionStore, body: bytes) -> CreatedTokenResponse:
    try:
        payload = TokenCreateRequest.model_validate(decode_json(body))
    except ValidationError as exc:
        raise InvalidTokenRequestError.from_errors(exc.errors()) from exc
    try:
        scopes = check_scopes(payload.scopes)
    except InvalidScopesError:
        raise ScopesRefusedError() from None
    try:
        label = check_token_label(payload.label)
    except InvalidTokenLabelError:
        raise TokenLabelRefusedError() from None
    owner = store.get_user(payload.user) if _can_be_a_name(payload.user) else None
    if owner is None:
        raise NoSuchUserError(["user"])
    if ADMIN_SCOPE in scopes and not owner.admin:
        raise NotAnAdminError()
    secret = new_token()
    try:
        token = store.create_token(
            owner.name,
            digest=token_digest(secret),
            label=label,
            scopes=scopes,
            created_at=datetime.now(timezone.utc),
        )
    except UnknownUserError:
        raise NoSuchUserError(["user"]) from None
    return CreatedTokenResponse(
        token=secret,
        id=token.id,
        user=token.user,
        label=token.label,
        scopes=sorted(token.scopes),
        created_at=token.created_at,
        revoked_at=token.revoked_at,
    )


@router.post("/tokens")
async def create_token(
    request: Request,
    _caller: Caller = Depends(requires_admin_token),
    store: ExecutionStore = Depends(get_store),
) -> JSONResponse:
    """Make a token for a user and answer it, this once. A disabled user's
    token is made, as the command line makes it, and authenticates nothing
    until the user is enabled."""
    require_json_media_type(request)
    body = await read_bounded_body(request, MAX_USERS_BODY_BYTES)
    created = await run_in_threadpool(_create_token, store, body)
    response = _json(created, status_code=201)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/tokens/{token_id}/revoke")
def revoke_token(
    token_id: int = Path(ge=1, le=_MAX_TOKEN_ID),
    _caller: Caller = Depends(requires_admin_token),
    store: ExecutionStore = Depends(get_store),
) -> TokenResponse:
    """Revoke the token, and answer it with the time it was revoked: the
    first time, however often it is asked. Revocation only moves forward --
    one conditional write that keeps the first time, on a row never deleted
    -- so reading the row afterwards races nothing. The body is not read."""
    store.revoke_token(token_id, revoked_at=datetime.now(timezone.utc))
    token = store.get_token(token_id)
    if token is None:
        raise UnknownTokenError()
    return _token_response(token)


__all__ = ["MAX_USERS_BODY_BYTES", "router"]
