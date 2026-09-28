"""The run list, run detail, run metadata, results, result detail and test
history routes.

**The run list and a test's history are a project's**, under
`/projects/{project}/...`: the project in the path is resolved once the
caller is authorized (`requires_read_project`), and one no project has is
`404 unknown_project`. Everything addressed by a run id stays where it was:
a run id names one run across projects, and its detail says which project
it belongs to, whose history a client then asks for. Either way the
caller needs the viewer role in the project, checked for a run id once the
run is found (`requires_read_run`), before the query is validated.

**Every response model is built field by field**, never with
`model_validate(..., from_attributes=True)` or any other whole-object
mapping, which would silently put `VcsContext.root` (the repository's local
path) on the wire as soon as a field is added upstream. On the list and
history paths the source is a `VcsProjection`, which has no `root` at all; on
the detail path `VcsContext` does carry it, and `_vcs_response` naming its
five fields is the only thing keeping it out of the body.

A run's presentation comes from `derive_presentation` and the app's grace
period; nothing here reimplements its precedence.

Every route here but the interface document reads the store, so each is a
plain `def` that FastAPI runs in its threadpool (see `app.py`), and needs
the read scope and the viewer role once the server has a user
(`service/access.py`). The interface document needs nothing: a client
reads it to learn how to ask.

A test's identity travels as a named query parameter (`?node_id=`), never a
path segment: a node id contains `/`, an encoded slash in a path is decoded
before routing and may be merged or rejected by a proxy, while a query value
arrives intact. The parameter name also leaves room for another identity
scheme as an additive sibling. `node_id` has no length bound here: any
node id already stored -- pytest never shortens a parametrize id -- must
stay readable by the exact value `/results` lists, and the `vantage`
command sizes the HTTP parser's request-line bound for the longest one a
report can carry (`cli.py`). A missing value is shaped by
`InvalidIdentityError`.

`list_results` returns a lean `ResultListEntry` per result, never the full
failure evidence or captured output; `get_result` returns every field of one
stored `Result`, unbounded. `list_results` narrows to the outcomes named by
a repeated `outcome` parameter, so a client pages through what did not pass
without reading what did.

A run in the list and on its own carries `counts`, how many of its results
hold each outcome, read in one store call for the whole page once the page
is read. Results only grow, and a finished run takes no more, so a run
finished when the page was read has its final counts; a running one's may
include results stored since. `/runs/{run_id}/outcomes` gives the run's
outcomes in stored order, one of pytest's characters each, the input a
client draws the whole run from.

A project's run list filters by pairs of `metadata_key` and `metadata_value`: two
parameters rather than one `key=value` string because a value may itself
contain `=`, repeated once per pair, and a run must hold every pair to
match. A run recorded before a key ever appeared has no value for it and is
excluded, so `metadata_horizon` reports, per filtered key, how many runs
predate it -- otherwise "not asked yet" would read as "did not match". The
page and the counts come from one store call, so they describe the same set
of runs. `metadata_horizon` is `None` when no filter was given.

A node id or metadata filter holding U+0000 matches nothing: nothing stored
holds one (`ingestion/text.py`), and PostgreSQL cannot even be asked about
one, so these routes answer it without passing it to the store.

The run list and a test's history page by `offset` or by `cursor`. An offset
counts runs from the newest, so a run recorded while a client pages through
pushes a run it already listed onto the next page. A cursor names the last
run a page listed (`service/cursor.py`), and the next page starts just past
it in the same order, whatever was recorded since. Each page hands out the
cursor for the next one as `next_cursor`.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response

from vantage.core.domain.execution import VcsContext
from vantage.core.domain.liveness import derive_presentation
from vantage.core.domain.projection import FailureProjection, VcsProjection
from vantage.core.domain.projects import Project
from vantage.core.domain.result import OUTCOMES, Result
from vantage.core.ports.storage import (
    MAX_PAGE_ITEMS,
    ExecutionStore,
    HistoryEntry,
    MetadataEntry,
    MetadataFile,
    Page,
    ResultListEntry,
    RunDetail,
    RunKey,
    RunListEntry,
)
from vantage.ingestion.text import NUL, without_nul
from vantage.service.access import requires_read_project, requires_read_run
from vantage.service.cursor import MAX_CURSOR_CHARS, decode_cursor, encode_cursor
from vantage.service.dependencies import get_grace_period, get_store
from vantage.service.errors import (
    InvalidCursorError,
    InvalidMetadataFilterError,
    InvalidOutcomeFilterError,
    UnknownResultError,
    UnknownRunError,
)
from vantage.service.schemas import (
    FailureProjectionResponse,
    HistoryEntryResponse,
    HistoryResponse,
    MetadataFileResponse,
    MetadataHorizonResponse,
    MetadataItemResponse,
    OutcomeCountsResponse,
    ResultDetailResponse,
    ResultListItemResponse,
    ResultsResponse,
    RunDetailResponse,
    RunListItemResponse,
    RunListResponse,
    RunMetadataResponse,
    RunOutcomesResponse,
    RunVcsResponse,
)

router = APIRouter()

# SQLite binds an integer as signed 64-bit and raises past it, so a larger
# offset is refused here as a shaped 422 rather than failing in the query.
_MAX_OFFSET = 2**63 - 1

# Each metadata pair is one more index seek in the store's query, so the
# number a caller may ask for is bounded like any other parameter.
MAX_METADATA_FILTERS = 16

# pytest's own character for each outcome, as its progress line prints it.
_OUTCOME_CHARACTERS = {
    "passed": ".",
    "failed": "F",
    "error": "E",
    "skipped": "s",
    "xfailed": "x",
    "xpassed": "X",
}

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


def _outcome_counts(counts: Mapping[str, int]) -> OutcomeCountsResponse:
    """Field by field, every outcome present: one a run has none of is
    left out of what the store returns, and is zero here."""
    return OutcomeCountsResponse(
        passed=counts.get("passed", 0),
        failed=counts.get("failed", 0),
        error=counts.get("error", 0),
        skipped=counts.get("skipped", 0),
        xfailed=counts.get("xfailed", 0),
        xpassed=counts.get("xpassed", 0),
    )


def _run_list_item(
    entry: RunListEntry, *, counts: Mapping[str, int], now: datetime, grace: timedelta
) -> RunListItemResponse:
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
        recorded_by=entry.recorded_by,
        counts=_outcome_counts(counts),
    )


def _run_detail_response(
    detail: RunDetail, *, counts: Mapping[str, int], now: datetime, grace: timedelta
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
        recorded_by=detail.recorded_by,
        project=detail.project,
        counts=_outcome_counts(counts),
    )


def _metadata_item(entry: MetadataEntry) -> MetadataItemResponse:
    return MetadataItemResponse(
        key=entry.key,
        name=entry.name,
        value=entry.value,
        status=entry.status,
        source=entry.source,
        source_file=entry.source_file,
        declared=entry.declared,
    )


def _metadata_file(metadata_file: MetadataFile) -> MetadataFileResponse:
    return MetadataFileResponse(
        source_file=metadata_file.source_file,
        content_type=metadata_file.content_type,
        status=metadata_file.status,
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


def _run_key(cursor: str | None, offset: int) -> RunKey | None:
    """The key a page starts after, from the `cursor` a previous page
    handed out; `None` without one."""
    if cursor is None:
        return None
    key = decode_cursor(cursor)
    if key is None:
        raise InvalidCursorError.malformed()
    if offset:
        raise InvalidCursorError.with_offset()
    return key


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


@router.get("/projects/{project}/runs")
def list_runs(
    project: Project = Depends(requires_read_project),
    limit: int = Query(default=MAX_PAGE_ITEMS, gt=0),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
    cursor: str | None = Query(default=None, max_length=MAX_CURSOR_CHARS),
    metadata_key: list[str] | None = Query(default=None),
    metadata_value: list[str] | None = Query(default=None),
    store: ExecutionStore = Depends(get_store),
    grace: timedelta = Depends(get_grace_period),
) -> RunListResponse:
    """`GET /api/v1/projects/{project}/runs`: the project's runs, and only
    them, the metadata filter and its horizon included. `limit <= 0` is a
    `422` -- not a page size. The
    200-item cap is enforced by the store, not re-clamped here; the default
    `limit` keeps it holding when a caller sends none.

    The n-th `metadata_value` pairs with the n-th `metadata_key`, so the two
    must be repeated the same number of times, checked here because
    FastAPI's parameter binding cannot express a cross-field rule.
    `metadata_horizon` has one entry per distinct filtered key, in the order
    first given, and is `None` when no filter was given.

    A pair holding U+0000 matches no run. Its key's horizon is that of the
    key with U+0000 replaced by U+FFFD, the text a report carrying the key
    stores, so the store is still asked once, for one snapshot, and never
    with U+0000. Each run's `counts` are read after the page, in one more
    call for all of them."""
    after = _run_key(cursor, offset)
    keys = metadata_key or []
    values = metadata_value or []
    if len(keys) != len(values):
        raise InvalidMetadataFilterError.unpaired(
            "metadata_value" if len(keys) > len(values) else "metadata_key"
        )
    if len(keys) > MAX_METADATA_FILTERS:
        raise InvalidMetadataFilterError.too_many(MAX_METADATA_FILTERS)
    horizon: list[MetadataHorizonResponse] | None = None
    if keys:
        stored_keys = [without_nul(key) for key in keys]
        page, predating = store.list_runs_with_metadata_horizon(
            project=project.name,
            filters=list(zip(stored_keys, map(without_nul, values))),
            limit=limit,
            offset=offset,
            after=after,
        )
        # Two keys differing only in U+0000 and U+FFFD are one key to the store.
        counts = dict(zip(dict.fromkeys(stored_keys), predating, strict=True))
        horizon = [
            MetadataHorizonResponse(key=key, predating=counts[without_nul(key)])
            for key in dict.fromkeys(keys)
        ]
        if any(NUL in text for text in (*keys, *values)):
            page = Page(items=(), has_more=False)
    else:
        page = store.list_runs(project=project.name, limit=limit, offset=offset, after=after)
    outcome_counts = store.count_outcomes([entry.execution.identity.value for entry in page.items])
    now = datetime.now(timezone.utc)
    items = [
        _run_list_item(
            entry,
            counts=outcome_counts.get(entry.execution.identity.value, {}),
            now=now,
            grace=grace,
        )
        for entry in page.items
    ]
    next_cursor = None
    if page.has_more:
        last = page.items[-1].execution
        next_cursor = encode_cursor(RunKey(started_at=last.started_at, run_id=last.identity.value))
    return RunListResponse(
        items=items, has_more=page.has_more, next_cursor=next_cursor, metadata_horizon=horizon
    )


@router.get("/runs/{run_id}")
def get_run_detail(
    detail: RunDetail = Depends(requires_read_run),
    store: ExecutionStore = Depends(get_store),
    grace: timedelta = Depends(get_grace_period),
) -> RunDetailResponse:
    """`GET /api/v1/runs/{run_id}`. An unknown run is the same
    `UnknownRunError` the heartbeat route raises: one rejection shape per
    kind, not one per route. `counts` is read after the run, as the list
    reads it after its page."""
    run_id = detail.execution.identity.value
    counts = store.count_outcomes([run_id]).get(run_id, {})
    return _run_detail_response(detail, counts=counts, now=datetime.now(timezone.utc), grace=grace)


@router.get("/runs/{run_id}/outcomes")
def get_run_outcomes(
    detail: RunDetail = Depends(requires_read_run),
    store: ExecutionStore = Depends(get_store),
) -> RunOutcomesResponse:
    """`GET /api/v1/runs/{run_id}/outcomes`: one character per result, in
    stored order. Not paged, like the section summary, which reads the same
    store call: at a byte a result, a run of a hundred thousand tests is
    100 KB. An unknown run is `UnknownRunError`, from
    `requires_read_run`."""
    case_outcomes = store.get_run_case_outcomes(detail.execution.identity.value)
    return RunOutcomesResponse(
        outcomes="".join(_OUTCOME_CHARACTERS[outcome] for _file_path, outcome in case_outcomes)
    )


@router.get("/runs/{run_id}/metadata")
def get_run_metadata(
    detail: RunDetail = Depends(requires_read_run),
    store: ExecutionStore = Depends(get_store),
) -> RunMetadataResponse:
    """`GET /api/v1/runs/{run_id}/metadata` -- every key the run reported,
    ordered by key, from whichever source, and every file it declared,
    ordered by path, with the status saying why a file's keys have no
    value. Not paged, since a run holds at most `MAX_METADATA_ENTRIES` keys.
    An unknown run is `UnknownRunError`, as on `get_run_detail`; one that
    reported no metadata has no items and no files."""
    metadata = store.get_run_metadata(detail.execution.identity.value)
    if metadata is None:
        raise UnknownRunError()
    return RunMetadataResponse(
        items=[_metadata_item(entry) for entry in metadata.entries],
        files=[_metadata_file(metadata_file) for metadata_file in metadata.files],
    )


@router.get("/runs/{run_id}/results")
def list_results(
    detail: RunDetail = Depends(requires_read_run),
    limit: int = Query(default=MAX_PAGE_ITEMS, gt=0),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
    outcome: list[str] | None = Query(default=None),
    store: ExecutionStore = Depends(get_store),
) -> ResultsResponse:
    """`GET /api/v1/runs/{run_id}/results`. An unknown `run_id` is `404`,
    consistent with `get_run_detail`, decided by `requires_read_run`
    before the page is asked for.

    Each `outcome` given keeps the results holding it, and `limit`,
    `offset` and `has_more` then page over those alone. A word that is not
    an outcome is refused rather than matching nothing, so a misspelt
    filter never reads as a run where everything passed. It is checked
    here rather than by the parameter's type, so `fields` names
    `query.outcome` whichever repetition is at fault, and the sentence
    never repeats the word."""
    wanted = None if outcome is None else frozenset(outcome)
    if wanted is not None and not wanted <= OUTCOMES:
        raise InvalidOutcomeFilterError()
    page = store.list_results(
        detail.execution.identity.value, limit=limit, offset=offset, outcomes=wanted
    )
    items = [_result_item(entry) for entry in page.items]
    return ResultsResponse(items=items, has_more=page.has_more)


@router.get("/runs/{run_id}/result")
def get_result(
    detail: RunDetail = Depends(requires_read_run),
    node_id: str = Query(...),
    store: ExecutionStore = Depends(get_store),
) -> ResultDetailResponse:
    """`GET /api/v1/runs/{run_id}/result?node_id=` -- `node_id` is a query
    value for the same reason as on `/projects/{project}/tests/history`. An unknown `run_id` is
    `UnknownRunError`; a known run with no result at that identity is a
    distinct `404`, `UnknownResultError`."""
    run_id = detail.execution.identity.value
    result = None if NUL in node_id else store.get_result(run_id, node_id=node_id)
    if result is None:
        raise UnknownResultError()
    return _result_detail_response(result)


@router.get("/projects/{project}/tests/history")
def list_history(
    project: Project = Depends(requires_read_project),
    node_id: str = Query(...),
    limit: int = Query(default=MAX_PAGE_ITEMS, gt=0),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
    cursor: str | None = Query(default=None, max_length=MAX_CURSOR_CHARS),
    store: ExecutionStore = Depends(get_store),
) -> HistoryResponse:
    """`GET /api/v1/projects/{project}/tests/history?node_id=...` -- one
    test's history in the project, whose catalogue alone says what the node
    id is. See the module docstring for why `node_id` is a query value, not
    a path segment. An unknown `node_id` yields an empty page, not an
    error."""
    after = _run_key(cursor, offset)
    if NUL in node_id:
        return HistoryResponse(items=[], has_more=False, next_cursor=None)
    page = store.list_history(
        project=project.name, node_id=node_id, limit=limit, offset=offset, after=after
    )
    items = [_history_entry(entry) for entry in page.items]
    next_cursor = None
    if page.has_more:
        last = page.items[-1]
        next_cursor = encode_cursor(RunKey(started_at=last.started_at, run_id=last.run_id))
    return HistoryResponse(items=items, has_more=page.has_more, next_cursor=next_cursor)


@router.get("/openapi.yaml")
async def get_openapi_document() -> Response:
    """Raw bytes, `application/yaml`, never parsed at runtime. Itself a
    `read`-tagged, documented path."""
    return Response(content=_OPENAPI_DOCUMENT_BYTES, media_type="application/yaml")


__all__ = ["router"]
