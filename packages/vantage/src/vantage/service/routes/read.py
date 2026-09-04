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

**Phase 8** adds `GET /api/v1/runs/{run_id}/result?node_id=`, the
single-result complement of `list_results`' now-lean projection (design.md
D76-D78). `list_results` reads a page of `ResultListEntry` -- identity,
outcome, timings and a lean `FailureProjection`, never the full
`FailureEvidence` or captured output. The new route reads the whole stored
`Result` via `store.get_result` and returns every field, unbounded,
matching `node_id`'s existing query-value treatment on `/tests/history`.

**Phase 10** widens `GET /api/v1/runs` with `metadata_key`/`metadata_value`
(design.md D100) -- the product this whole change exists for: "every run
where `firmware_version` is 2.1." Two query parameters, never one
`key=value` string, because a value may itself contain `=` (D54/D87); both
or neither, one without the other is `InvalidMetadataFilterError`, the read
path's only new rejection kind. `store.list_runs` is *extended* with the
filter, not joined by a second method, so it stays the one page over the
one total order D61 already settled. Q2's horizon -- how many runs predate
the filtered key -- is a follow-on slice on top of this one (design.md D100
still governs both).
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
    MAX_IDENTITY_CHARS,
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


def _failure_projection_response(
    failure: FailureProjection | None,
) -> FailureProjectionResponse | None:
    """Field by field, from the lean `FailureProjection` a list entry
    carries -- never the full `FailureEvidence` (design.md D76)."""
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
    """Field by field -- `entry` is the lean `ResultListEntry`
    `list_results` returns (design.md D76, D77), never the full `Result`;
    `failure` is `entry.failure`'s own `FailureProjection`, which has no
    field to carry `traceback`, `failure_repr` or captured output at all
    (task 8.1)."""
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
    """Field by field, the full record (design.md D78) -- every field a
    list response bounds or excludes, unbounded by any display width.
    `result.failure` normalises to `None` when the result carries no
    failure evidence at all (design.md D77); every failure field then
    falls back to its absent shape (`None`/`False`) rather than being
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
    metadata_key: str | None = Query(default=None),
    metadata_value: str | None = Query(default=None),
) -> RunListResponse:
    """`GET /api/v1/runs` (design.md D57, D61, D100). `limit`/`offset` are
    shape-validated here (`limit <= 0` is `422`, "not a page size" -- D61);
    the 200-item cap itself is enforced by `store.list_runs`, not re-clamped
    here, but the default `limit` already keeps the cap holding through the
    HTTP layer even when a caller sends none.

    `metadata_key`/`metadata_value` (design.md D100) are both-or-neither --
    checked here, before either reaches the store, since it is a cross-field
    rule FastAPI's own parameter binding cannot express for two independently
    optional query values. Q2's horizon is a follow-on slice on top of this
    one (module docstring)."""
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
    items = [_result_item(entry) for entry in page.items]
    return ResultsResponse(items=items, has_more=page.has_more)


@router.get("/runs/{run_id}/result")
async def get_result(
    request: Request,
    run_id: str = Path(pattern=_IDENTITY_PATTERN),
    node_id: str = Query(..., max_length=MAX_IDENTITY_CHARS),
) -> ResultDetailResponse:
    """`GET /api/v1/runs/{run_id}/result?node_id=` (design.md D54, D78) --
    `node_id` is again a named query value on an identity-free path segment,
    not a path segment itself, the same reasoning `GET /api/v1/tests/history`
    already applies. An unknown `run_id` is `404` via the existing
    `UnknownRunError`; a known run with no result at that identity is a
    distinct `404`, `UnknownResultError` -- `errors.py`'s one-shape-per-kind
    rule."""
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
