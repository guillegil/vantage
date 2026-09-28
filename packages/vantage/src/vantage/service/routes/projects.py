"""`GET /api/v1/projects` and `POST /api/v1/projects`: the projects a
database has, and making one, as `vantage project add` does.

**The list is of the projects the caller has a role in**, each with that
role (`effective_role`): every project, as `owner`, for an admin; `default`,
as `editor`, and each project their member rows name, for anyone else. The
rows are read first and the projects after, and projects are never
deleted, so every row's project is in the list read after it; a row
changed between the two reads gives the answer a request a moment earlier
or later would. On a server with no user, which checks no role, the list
is every project, with no role.

**Only an admin may make one, once the database has a user**; anyone may
while it has none -- an open server already lets anyone record runs and
change what they share. Only a database pytest-vantage's local store made
is ever served with no user, since `vantage` gives any other one an admin
before serving it, so anyone may make a project only there. A server
never makes a project from a report, so a report naming one that does not
exist yet waits for an admin, or for this route. A new project has no
members, its maker included: an admin acts as its owner without one.

**Nothing about a project changes afterwards**: no rename, no delete. Every
row that names a project keeps naming one, and a check that one exists stays
true, which is what lets every other route look a project up and then act
without a lock.

The body is read as `POST /config/sections` reads its own
(`service/body.py`): the media type from the header, at most
`MAX_PROJECTS_BODY_BYTES` streamed, then the parse, validation and store
write in the threadpool. The list is a plain `def`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from vantage.core.domain.projects import Project, can_name_a_project, effective_role
from vantage.core.ports.storage import ExecutionStore, ProjectExistsError
from vantage.ingestion.decode import decode_json
from vantage.service.access import Caller, requires_admin, requires_read
from vantage.service.body import read_bounded_body, require_json_media_type
from vantage.service.dependencies import get_store
from vantage.service.errors import (
    InvalidProjectRequestError,
    ProjectNameRefusedError,
    ProjectNameTakenError,
)
from vantage.service.schemas import ProjectCreateRequest, ProjectListResponse, ProjectResponse

router = APIRouter()

# A body's cap: a 64-character name with every character spelt as a 6-byte
# escape takes under 400 bytes, and the rest leaves room for whitespace.
MAX_PROJECTS_BODY_BYTES = 4 * 1024


def _project_response(project: Project, role: str | None) -> ProjectResponse:
    return ProjectResponse(name=project.name, created_at=project.created_at, role=role)


def _role(caller: Caller, project: str, stored: str | None = None) -> str | None:
    """The role `caller` acts with in `project`, given their member row's;
    None on a server with no user, which checks no role."""
    if caller.user is None:
        return None
    return effective_role(project, admin=caller.admin, stored=stored)


@router.get("/projects")
def list_projects(
    caller: Caller = Depends(requires_read),
    store: ExecutionStore = Depends(get_store),
) -> ProjectListResponse:
    """The projects the caller has a role in, `default` included, by name,
    whole."""
    stored: dict[str, str] = {}
    if caller.user is not None and not caller.admin:
        stored = {m.project: m.role for m in store.list_memberships(caller.user)}
    items = []
    for project in store.list_projects():
        role = _role(caller, project.name, stored.get(project.name))
        if caller.user is None or role is not None:
            items.append(_project_response(project, role))
    return ProjectListResponse(items=items)


def _create_project(store: ExecutionStore, body: bytes, caller: Caller) -> ProjectResponse:
    try:
        payload = ProjectCreateRequest.model_validate(decode_json(body))
    except ValidationError as exc:
        raise InvalidProjectRequestError.from_errors(exc.errors()) from exc
    if not can_name_a_project(payload.name):
        raise ProjectNameRefusedError()
    try:
        project = store.create_project(payload.name, created_at=datetime.now(timezone.utc))
    except ProjectExistsError:
        raise ProjectNameTakenError() from None
    return _project_response(project, _role(caller, project.name))


@router.post("/projects")
async def create_project(
    request: Request,
    caller: Caller = Depends(requires_admin),
    store: ExecutionStore = Depends(get_store),
) -> JSONResponse:
    require_json_media_type(request)
    body = await read_bounded_body(request, MAX_PROJECTS_BODY_BYTES)
    created = await run_in_threadpool(_create_project, store, body, caller)
    return JSONResponse(status_code=201, content=created.model_dump(mode="json"))


__all__ = ["MAX_PROJECTS_BODY_BYTES", "router"]
