"""The run list, run detail, results, result detail and test history routes.

**Every response model is built field by field**, never with
`model_validate(..., from_attributes=True)` or any other whole-object
mapping, which would silently put `VcsContext.root` (the repository's local
path) on the wire as soon as a field is added upstream. On the list and
history paths the source is a `VcsProjection`, which has no `root` at all; on
the detail path `VcsContext` does carry it, and `_vcs_response` naming its
five fields is the only thing keeping it out of the body.

A run's presentation comes from `derive_presentation` and
`app.state.grace_period`; nothing here reimplements its precedence.

A test's identity travels as a named query parameter (`?node_id=`), never a
path segment: a node id contains `/`, an encoded slash in a path is decoded
before routing and may be merged or rejected by a proxy, while a query value
arrives intact. The parameter name also leaves room for another identity
scheme as an additive sibling. `node_id` has no length bound here: any
node id already stored -- pytest never shortens a parametrize id -- must
stay readable by the exact value `/results` lists. A missing value is
shaped by `InvalidIdentityError`.

`list_results` returns a lean `ResultListEntry` per result, never the full
failure evidence or captured output; `get_result` returns every field of one
stored `Result`, unbounded.

`GET /runs` filters by `metadata_key` and `metadata_value`: two parameters
rather than one `key=value` string because a value may itself contain `=`,
and both or neither. A run recorded before the key was ever declared has no
value for it and is excluded, so `metadata_horizon` reports how many runs
predate the key -- otherwise "not asked yet" would read as "did not match".
It is `None` when no filter was given.
"""

from __future__ import annotations

import importlib.resources
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Path, Query, Request, Response

from vantage.core.domain.execution import VcsContext
from vantage.core.domain.liveness import derive_presentation
from vantage.core.domain.projection import FailureProjection, VcsProjection
from vantage.core.domain.result import Result
from vantage.core.ports.storage import (
    MAX_PAGE_ITEMS,
    HistoryEntry,
    ResultListEntry,
    RunDetail,
    RunListEntry,
)
from vantage.service.errors import InvalidMetadataFilterError, UnknownResultError, UnknownRunError
from vantage.service.schemas import (
    FailureProjectionResponse,
    HistoryEntryResponse,
    HistoryResponse,
    MetadataHorizonResponse,
    ResultDetailResponse,
    ResultListItemResponse,
    ResultsResponse,
    RunDetailResponse,
    RunListItemResponse,
    RunListResponse,
    RunVcsResponse,
)

router = APIRouter()

_IDENTITY_PATTERN = r"^[0-9a-f]{32}$"

# Read once at import time -- the bytes never change while the process runs.
# Loaded from inside the installed distribution through the
# `openapi/__init__.py` anchor, never from `docs/`, so it works from a wheel.
_OPENAPI_DOCUMENT_BYTES = (
    importlib.resources.files("vantage.service.openapi").joinpath("v1.yaml").read_bytes()
)


def _vcs_response(vcs: VcsProjection | VcsContext | None) -> RunVcsResponse | None:
    """Field by field, from either read type -- both carry these five
    fields, and `root` is never read."""
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


def _failure_projection_response(
    failure: FailureProjection | None,
) -> FailureProjectionResponse | None:
    """Field by field, from the lean `FailureProjection` a list entry
    carries -- never the full `FailureEvidence`."""
    if failure is None:
        return None
    return FailureProjectionResponse(
        failure_type=failure.failure_type,
        failure_message=failure.failure_message,
        failure_message_truncated=failure.failure_message_truncated,
        failure_path=failure.failure_path,
        failure_lineno=failure.failure_lineno,
        skip_reason=failure.skip_reason,
        xfail_reason=failure.xfail_reason,
    )


def _result_item(entry: ResultListEntry) -> ResultListItemResponse:
    """Field by field from the lean `ResultListEntry`, never the full
    `Result`; its `FailureProjection` has no field to carry `traceback`,
    `failure_repr` or captured output at all."""
    identity = entry.identity
    return ResultListItemResponse(
        node_id=identity.node_id,
        file_path=identity.file_path,
        class_name=identity.class_name,
        function_name=identity.function_name,
        param_id=identity.param_id,
        outcome=entry.outcome,
        duration=entry.duration,
        started_at=entry.started_at,
        finished_at=entry.finished_at,
        setup_outcome=entry.setup_outcome,
        call_outcome=entry.call_outcome,
        teardown_outcome=entry.teardown_outcome,
        setup_duration=entry.setup_duration,
        call_duration=entry.call_duration,
        teardown_duration=entry.teardown_duration,
        worker_id=entry.worker_id,
        failure=_failure_projection_response(entry.failure),
    )


def _result_detail_response(result: Result) -> ResultDetailResponse:
    """Field by field, the full record -- every field a list response
    bounds or excludes, unbounded by any display width. `result.failure` is
    `None` when the result carries no failure evidence; every failure field
    then falls back to its absent shape (`None`/`False`) rather than being
    omitted -- `ResultDetailResponse` always carries every field."""
    identity = result.identity
    failure = result.failure
    captured = result.captured
    return ResultDetailResponse(
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
        failure_type=failure.failure_type if failure else None,
        failure_message=failure.failure_message if failure else None,
        failure_message_truncated=failure.failure_message_truncated if failure else False,
        failure_path=failure.failure_path if failure else None,
        failure_lineno=failure.failure_lineno if failure else None,
        failure_repr=failure.failure_repr if failure else None,
        failure_repr_truncated=failure.failure_repr_truncated if failure else False,
        traceback=failure.traceback if failure else None,
        traceback_truncated=failure.traceback_truncated if failure else False,
        skip_reason=failure.skip_reason if failure else None,
        skip_reason_truncated=failure.skip_reason_truncated if failure else False,
        xfail_reason=failure.xfail_reason if failure else None,
        xfail_reason_truncated=failure.xfail_reason_truncated if failure else False,
        captured_stdout=captured.stdout,
        captured_stdout_truncated=captured.stdout_truncated,
        captured_stderr=captured.stderr,
        captured_stderr_truncated=captured.stderr_truncated,
    )


def _history_entry(entry: HistoryEntry) -> HistoryEntryResponse:
    """Field by field -- `entry.vcs` is a lean `VcsProjection`, read
    through the same `_vcs_response` helper as the other routes."""
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
    metadata_key: str | None = Query(default=None),
    metadata_value: str | None = Query(default=None),
) -> RunListResponse:
    """`GET /api/v1/runs`. `limit <= 0` is a `422` -- not a page size. The
    200-item cap is enforced by `store.list_runs`, not re-clamped here; the
    default `limit` keeps it holding when a caller sends none.

    `metadata_key`/`metadata_value` are both-or-neither, checked here
    because FastAPI's parameter binding cannot express a cross-field rule.
    `metadata_horizon` is populated only when a filter was given."""
    if (metadata_key is None) != (metadata_value is None):
        raise InvalidMetadataFilterError(
            "metadata_value" if metadata_key is not None else "metadata_key"
        )
    store = request.app.state.store
    page = store.list_runs(
        limit=limit, offset=offset, metadata_key=metadata_key, metadata_value=metadata_value
    )
    now = datetime.now(timezone.utc)
    grace = timedelta(seconds=request.app.state.grace_period)
    items = [_run_list_item(entry, now=now, grace=grace) for entry in page.items]
    horizon = (
        MetadataHorizonResponse(
            key=metadata_key,
            predating=store.count_runs_predating_metadata_key(metadata_key),
        )
        if metadata_key is not None
        else None
    )
    return RunListResponse(items=items, has_more=page.has_more, metadata_horizon=horizon)


@router.get("/runs/{run_id}")
async def get_run_detail(
    request: Request, run_id: str = Path(pattern=_IDENTITY_PATTERN)
) -> RunDetailResponse:
    """`GET /api/v1/runs/{run_id}`. An unknown run is the same
    `UnknownRunError` the heartbeat route raises: one rejection shape per
    kind, not one per route."""
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
    """`GET /api/v1/runs/{run_id}/results`. An unknown
    `run_id` is `404`, consistent with `get_run_detail` -- checked via the
    cheaper `store.get_execution` rather than building a full detail."""
    store = request.app.state.store
    if store.get_execution(run_id) is None:
        raise UnknownRunError()
    page = store.list_results(run_id, limit=limit, offset=offset)
    items = [_result_item(entry) for entry in page.items]
    return ResultsResponse(items=items, has_more=page.has_more)


@router.get("/runs/{run_id}/result")
async def get_result(
    request: Request,
    run_id: str = Path(pattern=_IDENTITY_PATTERN),
    node_id: str = Query(...),
) -> ResultDetailResponse:
    """`GET /api/v1/runs/{run_id}/result?node_id=` -- `node_id` is a query
    value for the same reason as on `/tests/history`. An unknown `run_id` is
    `UnknownRunError`; a known run with no result at that identity is a
    distinct `404`, `UnknownResultError`."""
    store = request.app.state.store
    if store.get_execution(run_id) is None:
        raise UnknownRunError()
    result = store.get_result(run_id, node_id=node_id)
    if result is None:
        raise UnknownResultError()
    return _result_detail_response(result)


@router.get("/tests/history")
async def list_history(
    request: Request,
    node_id: str = Query(...),
    limit: int = Query(default=MAX_PAGE_ITEMS, gt=0),
    offset: int = Query(default=0, ge=0),
) -> HistoryResponse:
    """`GET /api/v1/tests/history?node_id=...` -- see the module docstring
    for why `node_id` is a query value, not a path segment. An unknown
    `node_id` yields an empty page, not an error."""
    store = request.app.state.store
    page = store.list_history(node_id=node_id, limit=limit, offset=offset)
    items = [_history_entry(entry) for entry in page.items]
    return HistoryResponse(items=items, has_more=page.has_more)


@router.get("/openapi.yaml")
async def get_openapi_document() -> Response:
    """Raw bytes, `application/yaml`, never parsed at runtime. Itself a
    `read`-tagged, documented path."""
    return Response(content=_OPENAPI_DOCUMENT_BYTES, media_type="application/yaml")


__all__ = ["router"]
