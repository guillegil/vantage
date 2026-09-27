"""`GET /api/v1/projects` and `POST /api/v1/projects`: the projects a
database has, and making one, as `vantage project add` does.

**Who may make one is who may change sections**: an admin's token once the
database has a user, anyone while it has none -- an open server already
lets anyone record runs and change what they share. A server never makes a
project from a report, so a report naming one that does not exist yet waits
for an admin, or for this route.

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

from vantage.core.domain.projects import Project, can_name_a_project
from vantage.core.ports.storage import ExecutionStore, ProjectExistsError
from vantage.ingestion.decode import decode_json
from vantage.service.access import requires_admin, requires_read
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


def _project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(name=project.name, created_at=project.created_at)


@router.get("/projects", dependencies=[Depends(requires_read)])
def list_projects(store: ExecutionStore = Depends(get_store)) -> ProjectListResponse:
    """Every project, `default` included, by name, whole."""
    return ProjectListResponse(items=[_project_response(p) for p in store.list_projects()])


def _create_project(store: ExecutionStore, body: bytes) -> ProjectResponse:
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
    return _project_response(project)


@router.post("/projects", dependencies=[Depends(requires_admin)])
async def create_project(
    request: Request, store: ExecutionStore = Depends(get_store)
) -> JSONResponse:
    require_json_media_type(request)
    body = await read_bounded_body(request, MAX_PROJECTS_BODY_BYTES)
    created = await run_in_threadpool(_create_project, store, body)
    return JSONResponse(status_code=201, content=created.model_dump(mode="json"))


__all__ = ["MAX_PROJECTS_BODY_BYTES", "router"]
