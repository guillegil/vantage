"""The password routes: `POST /api/v1/login`, `POST /api/v1/password` and
`PUT /api/v1/users/{name}/password`.

**Logging in trades a name and a password for a login token**, which holds
the read scope, and the admin scope for an admin, never the record scope:
recording is for a made token, so a leaked login cannot inject runs. It
expires after `LOGIN_TOKEN_LIFETIME`, with no refresh, and every password
set for its user revokes it. The token is in that one answer only, marked
`Cache-Control: no-store`.

**Changing your own password takes the current one**, not a token, so a
stolen login token cannot take the account over, and the password the
server printed at its first start is changed in one request. The change is
a compare-and-set against the hash the current password was checked
against, so of two changes racing, one wins and the other is refused.

**An admin sets anyone's password** (`PUT /users/{name}/password`), as
`vantage user password` does on the database: no current password, even
for their own account, since an admin's token can already make admin
tokens. It enables and promotes nobody.

**Every failure of a name and a password reads alike** (`401
invalid_credentials`) and costs the same work: an unknown name, one nobody
can have, a user with no password or a disabled one are all checked
against a hash that matches nothing (`verify_password`). There is no
lockout, counter or delay, any of which would let anyone lock `admin` out
or grow the database; what bounds guessing is each check's cost and the
password slots (`service/slots.py`): two hashes at once, 32 requests
waiting, and `503 password_checks_busy` for any more, each asked whether
its client is still there before it hashes. uvicorn's access log records
each attempt, with its address.

**Passwords never leave a request body**: never in a URL, a header, a
rejection or a log record. The request models hide their input in
validation errors, and a rejection names fields, never values.

**On an open server** -- a database the local store made, with no user --
nobody has a password, and all three answer `409 open_server`: login and
the password change before the body is read (`requires_closed_server`),
the admin route as every users route does.

**Several servers on one PostgreSQL database** need nothing more: a login
inserts its token only while the hash it checked is still stored, holding
the account row against a concurrent change (`create_login_token`), and a
change revokes every login token in the same transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from vantage.core.domain.access import (
    LOGIN_TOKEN_LIFETIME,
    Token,
    new_token,
    token_digest,
)
from vantage.core.domain.passwords import (
    InvalidPasswordError,
    check_password,
    hash_password,
    verify_password,
)
from vantage.core.ports.storage import ExecutionStore
from vantage.ingestion.decode import decode_json
from vantage.service.access import Caller, requires_admin_token, requires_closed_server
from vantage.service.body import read_bounded_body, require_json_media_type
from vantage.service.dependencies import get_password_slots, get_store
from vantage.service.errors import (
    IncompleteBodyError,
    InvalidCredentialsError,
    InvalidLoginRequestError,
    InvalidPasswordRequestError,
    NoSuchUserError,
    PasswordRefusedError,
)
from vantage.service.routes.users import MAX_USERS_BODY_BYTES, can_be_a_name
from vantage.service.schemas import (
    CreatedTokenResponse,
    LoginRequest,
    PasswordChangeRequest,
    PasswordSetRequest,
)
from vantage.service.slots import PasswordSlots

router = APIRouter()

M = TypeVar("M", bound=BaseModel)
T = TypeVar("T")


def _parse(
    body: bytes,
    model: type[M],
    rejection: type[InvalidLoginRequestError | InvalidPasswordRequestError],
) -> M:
    """`body` as `model`. The validation error is not chained to the
    rejection: it holds the password."""
    try:
        return model.model_validate(decode_json(body))
    except ValidationError as exc:
        raise rejection.from_errors(exc.errors()) from None


async def _hashing(
    request: Request, slots: PasswordSlots, work: Callable[..., T], *args: object
) -> T:
    """`work(*args)` in the threadpool once a password slot is free. The
    slot is awaited on the event loop, so a waiting request holds no
    thread; a client that left while it waited gets no hash."""
    async with slots.slot():
        if await request.is_disconnected():
            raise IncompleteBodyError()
        return await run_in_threadpool(work, *args)


def _checked_hash(store: ExecutionStore, name: str, password: str) -> str | None:
    """The stored hash `password` matches, of the enabled user `name`, or
    None. One scrypt whatever the outcome."""
    stored = store.get_password_hash(name) if can_be_a_name(name) else None
    return stored if verify_password(password, stored) else None


def _log_in(store: ExecutionStore, payload: LoginRequest, secret: str) -> Token | None:
    stored = _checked_hash(store, payload.name, payload.password)
    if stored is None:
        return None
    now = datetime.now(timezone.utc)
    return store.create_login_token(
        payload.name,
        password_hash=stored,
        digest=token_digest(secret),
        created_at=now,
        expires_at=now + LOGIN_TOKEN_LIFETIME,
    )


@router.post("/login", dependencies=[Depends(requires_closed_server)])
async def log_in(
    request: Request,
    store: ExecutionStore = Depends(get_store),
    slots: PasswordSlots = Depends(get_password_slots),
) -> JSONResponse:
    """Answer a login token for a name and its password."""
    require_json_media_type(request)
    body = await read_bounded_body(request, MAX_USERS_BODY_BYTES)
    payload = await run_in_threadpool(_parse, body, LoginRequest, InvalidLoginRequestError)
    secret = new_token()
    token = await _hashing(request, slots, _log_in, store, payload, secret)
    if token is None:
        raise InvalidCredentialsError()
    created = CreatedTokenResponse(
        token=secret,
        id=token.id,
        user=token.user,
        label=token.label,
        scopes=sorted(token.scopes),
        created_at=token.created_at,
        revoked_at=token.revoked_at,
        expires_at=token.expires_at,
    )
    response = JSONResponse(status_code=201, content=created.model_dump(mode="json"))
    response.headers["Cache-Control"] = "no-store"
    return response


def _parse_change(body: bytes) -> PasswordChangeRequest:
    payload = _parse(body, PasswordChangeRequest, InvalidPasswordRequestError)
    try:
        check_password(payload.new_password)
    except InvalidPasswordError:
        raise PasswordRefusedError(["new_password"]) from None
    return payload


def _change_password(store: ExecutionStore, payload: PasswordChangeRequest) -> bool:
    stored = _checked_hash(store, payload.name, payload.password)
    if stored is None:
        return False
    return store.set_password(
        payload.name,
        password_hash=hash_password(payload.new_password),
        changed_at=datetime.now(timezone.utc),
        replacing=stored,
    )


@router.post("/password", dependencies=[Depends(requires_closed_server)])
async def change_password(
    request: Request,
    store: ExecutionStore = Depends(get_store),
    slots: PasswordSlots = Depends(get_password_slots),
) -> Response:
    """Change a user's password, given the current one. The new one is
    checked against the rule before the current one is, so a refused
    request costs no hash."""
    require_json_media_type(request)
    body = await read_bounded_body(request, MAX_USERS_BODY_BYTES)
    payload = await run_in_threadpool(_parse_change, body)
    if not await _hashing(request, slots, _change_password, store, payload):
        raise InvalidCredentialsError()
    return Response(status_code=204)


def _parse_set(body: bytes) -> PasswordSetRequest:
    payload = _parse(body, PasswordSetRequest, InvalidPasswordRequestError)
    try:
        check_password(payload.password)
    except InvalidPasswordError:
        raise PasswordRefusedError(["password"]) from None
    return payload


def _set_password(store: ExecutionStore, name: str, payload: PasswordSetRequest) -> bool:
    return store.set_password(
        name,
        password_hash=hash_password(payload.password),
        changed_at=datetime.now(timezone.utc),
    )


@router.put("/users/{name}/password")
async def set_password(
    request: Request,
    name: str = Path(),
    _caller: Caller = Depends(requires_admin_token),
    store: ExecutionStore = Depends(get_store),
    slots: PasswordSlots = Depends(get_password_slots),
) -> Response:
    """Set a user's password. A name nobody can have is answered before
    the body is read."""
    require_json_media_type(request)
    if not can_be_a_name(name):
        raise NoSuchUserError()
    body = await read_bounded_body(request, MAX_USERS_BODY_BYTES)
    payload = await run_in_threadpool(_parse_set, body)
    if not await _hashing(request, slots, _set_password, store, name, payload):
        raise NoSuchUserError()
    return Response(status_code=204)


__all__ = ["router"]
