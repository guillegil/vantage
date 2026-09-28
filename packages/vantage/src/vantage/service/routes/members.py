"""The members routes: `GET /api/v1/projects/{project}/members` and
`PUT`/`DELETE /api/v1/projects/{project}/members/{user}` -- over HTTP, what
`vantage project member` does on the database.

**Listing needs the read scope and the viewer role in the project, changing
needs the manage scope and the owner role** (`requires_read_members`,
`requires_manage_members`). A viewer may list the members, since every
run's `recorded_by` already shows them user names. An admin acts as an
owner of every project without a row, so an admin can always repair a
project here: there is no guard against an owner demoting or removing
themselves, or the last owner. An owner may make another member an owner.
An admin or a disabled user may be made a member; a disabled one's role
grants nothing while their tokens authenticate nothing.

**`default` has no members**: every user is an editor of it without a row,
so the list is empty and says so in `everyone`, and setting or removing a
member there is `409 default_project` -- to an admin, since to anyone else
the owner role it needs is above their own, `403 insufficient_role`.

**On an open server** -- a database the local store made, with no user --
nobody is a member of anything, and all three answer `409 open_server`,
as the users routes do.

**Every refusal of who asks comes first** (`service/access.py`), then the
project's own: `default`, then the user in the path. A value that cannot
be a user's name is a user nobody has -- `404 unknown_user` on `PUT`,
answered before the body is read, and `404 unknown_member` on `DELETE` --
and is never handed to the store, which keeps U+0000 from every adapter.
A name that can be one is the store's to answer: `unknown_user` tells an
owner which names exist, which adding a colleague by name needs, and a
`DELETE` of a user who is not a member reads as one of a user nobody has,
so a repeated `DELETE` answers `404` as a section's does.

**`PUT` is an idempotent upsert**: `201` when it added the member, `200`
when it set an existing member's role, the same one included -- the
store's own answer, decided inside its write. The body is read as
`POST /projects` reads its own (`service/body.py`): the media type from the
header, at most `MAX_MEMBERS_BODY_BYTES` streamed, then the parse,
validation and store write in the threadpool. The list and `DELETE` are
plain `def`s.

**Several servers on one PostgreSQL database** need nothing more: every
write here is one store call, and the only read before it -- the caller's
role -- is read on every request, so it is stale by one request at most,
as a token's standing is.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from vantage.core.domain.projects import (
    DEFAULT_PROJECT,
    InvalidRoleError,
    Membership,
    Project,
    check_role,
    effective_role,
)
from vantage.core.ports.storage import ExecutionStore, UnknownUserError
from vantage.ingestion.decode import decode_json
from vantage.service.access import requires_manage_members, requires_read_members
from vantage.service.body import read_bounded_body, require_json_media_type
from vantage.service.dependencies import get_store
from vantage.service.errors import (
    DefaultProjectMembersError,
    InvalidMemberRequestError,
    NoSuchMemberError,
    NoSuchUserError,
    RoleRefusedError,
)
from vantage.service.routes.users import can_be_a_name
from vantage.service.schemas import MemberListResponse, MemberResponse, MemberSetRequest

router = APIRouter()

# A body's cap: the longest role spelt with every character as a 6-byte
# escape takes under 50 bytes, and the rest leaves room for whitespace.
MAX_MEMBERS_BODY_BYTES = 4 * 1024


def _member_response(member: Membership) -> MemberResponse:
    return MemberResponse(user=member.user, role=member.role)


@router.get("/projects/{project}/members")
def list_members(
    project: Project = Depends(requires_read_members),
    store: ExecutionStore = Depends(get_store),
) -> MemberListResponse:
    """The project's members, by user, whole, and the role every user holds
    there without a row: `editor` in `default`, none elsewhere."""
    return MemberListResponse(
        items=[_member_response(m) for m in store.list_members(project=project.name)],
        everyone=effective_role(project.name, admin=False, stored=None),
    )


def _set_member(
    store: ExecutionStore, project: str, user: str, body: bytes
) -> tuple[bool, MemberResponse]:
    """Parse and check one complete body and set the member, and return
    whether they were added, with the member as stored. Every step blocks,
    so `set_member` calls this in the threadpool."""
    try:
        payload = MemberSetRequest.model_validate(decode_json(body))
    except ValidationError as exc:
        raise InvalidMemberRequestError.from_errors(exc.errors()) from exc
    try:
        role = check_role(payload.role)
    except InvalidRoleError:
        raise RoleRefusedError() from None
    try:
        created = store.set_member(user, project=project, role=role)
    except UnknownUserError:
        raise NoSuchUserError() from None
    return created, MemberResponse(user=user, role=role)


@router.put("/projects/{project}/members/{user}")
async def set_member(
    request: Request,
    user: str = Path(),
    project: Project = Depends(requires_manage_members),
    store: ExecutionStore = Depends(get_store),
) -> JSONResponse:
    """Make `user` a member of the project with the role the body names, or
    set a member's role. A name nobody can have is answered before the body
    is read."""
    require_json_media_type(request)
    if project.name == DEFAULT_PROJECT:
        raise DefaultProjectMembersError()
    if not can_be_a_name(user):
        raise NoSuchUserError()
    body = await read_bounded_body(request, MAX_MEMBERS_BODY_BYTES)
    created, member = await run_in_threadpool(_set_member, store, project.name, user, body)
    return JSONResponse(status_code=201 if created else 200, content=member.model_dump())


@router.delete("/projects/{project}/members/{user}", status_code=204)
def remove_member(
    user: str = Path(),
    project: Project = Depends(requires_manage_members),
    store: ExecutionStore = Depends(get_store),
) -> Response:
    """Remove `user` from the project. The body is not read."""
    if project.name == DEFAULT_PROJECT:
        raise DefaultProjectMembersError()
    if not can_be_a_name(user) or not store.remove_member(user, project=project.name):
        raise NoSuchMemberError()
    return Response(status_code=204)


__all__ = ["MAX_MEMBERS_BODY_BYTES", "router"]
