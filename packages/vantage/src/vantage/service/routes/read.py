"""The run list and run detail routes (design.md D57, D59, D61, D62; Phase 4).

**Every response model is built field by field.** `RunVcsResponse`,
`RunListItemResponse` and `RunDetailResponse` (`service/schemas.py`) are
never constructed with `model_validate(execution, from_attributes=True)` or
any other whole-object mapping -- that is exactly how `VcsContext.root`
would reach the wire, silently, the first time someone adds a field to
`Execution` or `VcsContext` upstream. On the list path `VcsProjection` has no
`root` field at all, so the exclusion is structural (design.md D59); on the
detail path `VcsContext` *does* carry `root`, and this module's explicit
`_vcs_response` helper -- reading `commit`/`branch`/`commit_subject`/
`commit_subject_truncated`/`dirty` and nothing else -- is the only thing
standing between it and the response body.

**`derive_presentation` gets its first caller here (design.md D62).** This
module calls it; it does not reimplement any part of the precedence it
encodes. `app.state.grace_period` -- a named seam since D34, wired by
`create_app` -- finally has a reader.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Path, Query, Request

from vantage.core.domain.execution import VcsContext
from vantage.core.domain.liveness import derive_presentation
from vantage.core.domain.projection import VcsProjection
from vantage.core.ports.storage import MAX_PAGE_ITEMS, RunDetail, RunListEntry
from vantage.service.errors import UnknownRunError
from vantage.service.schemas import (
    RunDetailResponse,
    RunListItemResponse,
    RunListResponse,
    RunVcsResponse,
)

router = APIRouter()

_IDENTITY_PATTERN = r"^[0-9a-f]{32}$"


def _vcs_response(vcs: VcsProjection | VcsContext | None) -> RunVcsResponse | None:
    """Field by field, from either read type -- both carry the same five
    names; neither is read here through `root` (design.md D59)."""
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


def _run_detail_response(
    detail: RunDetail, *, now: datetime, grace: timedelta
) -> RunDetailResponse:
    execution = detail.execution
    return RunDetailResponse(
        id=execution.identity.value,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        exit_status=execution.exit_status,
        interrupted=execution.interrupted,
        interrupt_reason=execution.interrupt_reason,
        presentation=derive_presentation(
            execution, last_contact_at=detail.last_contact_at, now=now, grace=grace
        ),
        vcs=_vcs_response(execution.vcs),
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


@router.get("/runs/{run_id}")
async def get_run_detail(
    request: Request, run_id: str = Path(pattern=_IDENTITY_PATTERN)
) -> RunDetailResponse:
    """`GET /api/v1/runs/{run_id}` (design.md D57, D59, D62). Reuses
    `UnknownRunError` (`service/errors.py`) rather than a fresh rejection
    type -- "no run with that identifier has been recorded" is exactly the
    heartbeat route's existing 404 case, and `errors.py`'s docstring already
    asks for one shape per rejection kind, not one per route."""
    store = request.app.state.store
    detail = store.get_run_detail(run_id)
    if detail is None:
        raise UnknownRunError()

    now = datetime.now(timezone.utc)
    grace = timedelta(seconds=request.app.state.grace_period)
    return _run_detail_response(detail, now=now, grace=grace)


__all__ = ["router"]
