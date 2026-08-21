"""The run list route (design.md D57, D59, D61, D62; Phase 4a of PR4).

**Every response model is built field by field.** `RunVcsResponse` and
`RunListItemResponse` (`service/schemas.py`) are never constructed with
`model_validate(execution, from_attributes=True)` or any other whole-object
mapping -- that is exactly how an unrelated field would reach the wire,
silently, the first time someone adds a field to `Execution` upstream. On
the list path `VcsProjection` has no `root` field at all, so the exclusion
is structural (design.md D59).

**`derive_presentation` gets its first caller here (design.md D62).** This
module calls it; it does not reimplement any part of the precedence it
encodes. `app.state.grace_period` -- a named seam since D34, wired by
`create_app` -- finally has a reader.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request

from vantage.core.domain.liveness import derive_presentation
from vantage.core.domain.projection import VcsProjection
from vantage.core.ports.storage import MAX_PAGE_ITEMS, RunListEntry
from vantage.service.schemas import RunListItemResponse, RunListResponse, RunVcsResponse

router = APIRouter()


def _vcs_response(vcs: VcsProjection | None) -> RunVcsResponse | None:
    """Field by field, from the list path's lean projection -- never read
    through `root` (design.md D59)."""
    if vcs is None:
        return None
    return RunVcsResponse(
        commit=vcs.commit,
        branch=vcs.branch,
        commit_subject=vcs.commit_subject,
        commit_subject_truncated=vcs.commit_subject_truncated,
        dirty=vcs.dirty,
    )


def _run_list_item(entry: RunListEntry, *, now: datetime, grace: timedelta) -> RunListItemResponse:
    execution = entry.execution
    return RunListItemResponse(
        id=execution.identity.value,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        exit_status=execution.exit_status,
        interrupted=execution.interrupted,
        presentation=derive_presentation(
            execution, last_contact_at=entry.last_contact_at, now=now, grace=grace
        ),
        vcs=_vcs_response(entry.vcs),
    )


@router.get("/runs")
async def list_runs(
    request: Request,
    limit: int = Query(default=MAX_PAGE_ITEMS, gt=0),
    offset: int = Query(default=0, ge=0),
) -> RunListResponse:
    """`GET /api/v1/runs` (design.md D57, D61). `limit`/`offset` are shape-
    validated here (`limit <= 0` is `422`, "not a page size" -- D61); the
    200-item cap itself is enforced by `store.list_runs`, not re-clamped
    here, but the default `limit` already keeps the cap holding through the
    HTTP layer even when a caller sends none."""
    store = request.app.state.store
    page = store.list_runs(limit=limit, offset=offset)
    now = datetime.now(timezone.utc)
    grace = timedelta(seconds=request.app.state.grace_period)
    items = [_run_list_item(entry, now=now, grace=grace) for entry in page.items]
    return RunListResponse(items=items, has_more=page.has_more)


__all__ = ["router"]
