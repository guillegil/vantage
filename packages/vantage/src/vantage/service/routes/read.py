"""The run list, run detail, results, and history routes (design.md D54,
D57, D59, D61, D62; Phases 4-5).

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

**Phase 5** adds `GET /api/v1/runs/{run_id}/results` and
`GET /api/v1/tests/history`. History resolves a test's identity through a
*named query parameter on an identity-free path* (``?node_id=<value>``), not
a path segment (design.md D54) -- the parameter name is the identity
scheme, so a later `?stable_id=` arrives as an additive sibling. `node_id`
is bounded at `MAX_IDENTITY_CHARS`; a missing or over-long value is shaped
by `service/errors.py`'s `InvalidIdentityError`, never a proxy `414`.
"""

from __future__ import annotations

import importlib.resources
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Path, Query, Request, Response

from vantage.core.domain.execution import VcsContext
from vantage.core.domain.liveness import derive_presentation
from vantage.core.domain.projection import VcsProjection
from vantage.core.domain.result import Result
from vantage.core.ports.storage import (
    MAX_IDENTITY_CHARS,
    MAX_PAGE_ITEMS,
    HistoryEntry,
    RunDetail,
    RunListEntry,
)
from vantage.service.errors import UnknownRunError
from vantage.service.schemas import (
    HistoryEntryResponse,
    HistoryResponse,
    ResultItemResponse,
    ResultsResponse,
    RunDetailResponse,
    RunListItemResponse,
    RunListResponse,
    RunVcsResponse,
)

router = APIRouter()

_IDENTITY_PATTERN = r"^[0-9a-f]{32}$"

# Read once at import time, not per request -- the bytes never change
# while the process runs. Comes from inside the installed distribution,
# never `docs/` (design.md Q5), via the anchor `openapi/__init__.py` exists
# for.
_OPENAPI_DOCUMENT_BYTES = (
    importlib.resources.files("vantage.service.openapi").joinpath("v1.yaml").read_bytes()
)


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


def _result_item(result: Result) -> ResultItemResponse:
    """Field by field -- `Result` has no traceback/captured-output field
    yet, so that exclusion holds by construction (task 7.6)."""
    identity = result.identity
    return ResultItemResponse(
        node_id=identity.node_id,
        file_path=identity.file_path,
        class_name=identity.class_name,
        function_name=identity.function_name,
        param_id=identity.param_id,
        outcome=result.outcome,
        duration=result.duration,
        started_at=result.started_at,
        finished_at=result.finished_at,
        setup_outcome=result.setup_outcome,
        call_outcome=result.call_outcome,
        teardown_outcome=result.teardown_outcome,
        setup_duration=result.setup_duration,
        call_duration=result.call_duration,
        teardown_duration=result.teardown_duration,
        worker_id=result.worker_id,
    )


def _history_entry(entry: HistoryEntry) -> HistoryEntryResponse:
    """Field by field -- `entry.vcs` is a lean `VcsProjection` (D59, D60),
    read through the same `_vcs_response` helper as the other routes."""
    return HistoryEntryResponse(
        run_id=entry.run_id,
        started_at=entry.started_at,
        finished_at=entry.finished_at,
        outcome=entry.outcome,
        duration=entry.duration,
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


@router.get("/runs/{run_id}/results")
async def list_results(
    request: Request,
    run_id: str = Path(pattern=_IDENTITY_PATTERN),
    limit: int = Query(default=MAX_PAGE_ITEMS, gt=0),
    offset: int = Query(default=0, ge=0),
) -> ResultsResponse:
    """`GET /api/v1/runs/{run_id}/results` (design.md D57, D61). An unknown
    `run_id` is `404`, consistent with `get_run_detail` -- checked via the
    cheaper `store.get_execution` rather than building a full detail."""
    store = request.app.state.store
    if store.get_execution(run_id) is None:
        raise UnknownRunError()
    page = store.list_results(run_id, limit=limit, offset=offset)
    items = [_result_item(result) for result in page.items]
    return ResultsResponse(items=items, has_more=page.has_more)


@router.get("/tests/history")
async def list_history(
    request: Request,
    node_id: str = Query(..., max_length=MAX_IDENTITY_CHARS),
    limit: int = Query(default=MAX_PAGE_ITEMS, gt=0),
    offset: int = Query(default=0, ge=0),
) -> HistoryResponse:
    """`GET /api/v1/tests/history?node_id=...` (design.md D54, D57, D61) --
    see the module docstring for why `node_id` is a query value, not a path
    segment. An unknown `node_id` yields an empty page, not an error."""
    store = request.app.state.store
    page = store.list_history(node_id=node_id, limit=limit, offset=offset)
    items = [_history_entry(entry) for entry in page.items]
    return HistoryResponse(items=items, has_more=page.has_more)


@router.get("/openapi.yaml")
async def get_openapi_document() -> Response:
    """Raw bytes, `application/yaml`, never parsed at runtime (design.md
    Q5). Itself a `read`-tagged, documented path (task 6.8)."""
    return Response(content=_OPENAPI_DOCUMENT_BYTES, media_type="application/yaml")


__all__ = ["router"]
