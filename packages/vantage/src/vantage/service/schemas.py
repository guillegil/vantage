"""Pydantic v2 models of the HTTP responses and of the section settings.

A session report's own models are `vantage.ingestion.schemas`: the local
store validates a report exactly as the server does, without the server.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

# Re-exported for the plugin's contract tests, which validate what the
# plugin sends against the model the server applies.
from vantage.ingestion.schemas import MetadataReport as MetadataReport


class RejectionResponse(BaseModel):
    """The one body every rejection has, built only by `service/errors.py`:
    an error code, a fixed sentence and dotted field paths, never a value
    the client submitted."""

    error: str
    detail: str
    fields: list[str]


class Acknowledgement(BaseModel):
    """The response body of `POST /api/v1/runs`, for both `201` and `200`."""

    run_id: str
    status: str
    ignored: list[str] = []


class HeartbeatAcknowledgement(BaseModel):
    """The response body for `POST /runs/{run_id}/heartbeat`.

    Separate from `Acknowledgement` because the heartbeat's request body is
    never read, so nothing could ever populate `ignored`.
    """

    run_id: str
    status: str


class RunVcsResponse(BaseModel):
    """The VCS section of a run response, list or detail alike.

    **No `root` field, on purpose.** `routes/read.py` builds this model field
    by field from either a lean `VcsProjection` (list path) or a full
    `VcsContext` (detail path). `VcsContext` carries `root`, the reporter's
    absolute checkout path, and this model having no field for it is the only
    thing that keeps it off the wire on the detail path.
    """

    commit: str | None
    branch: str | None
    commit_subject: str | None
    commit_subject_truncated: bool
    dirty: bool | None


class RunListItemResponse(BaseModel):
    """One entry of `RunListResponse`."""

    id: str
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    presentation: str
    vcs: RunVcsResponse | None


class MetadataHorizonResponse(BaseModel):
    """One entry of `RunListResponse.metadata_horizon`, for one filtered
    key. `predating` is the count of runs recorded before any run carried
    `key`, which equals the total run count when none ever has."""

    key: str
    predating: int


class RunListResponse(BaseModel):
    """The response body for `GET /api/v1/runs`. No `total`, which would cost
    a `COUNT(*)` on every page. `metadata_horizon` holds one entry per
    distinct filtered key, in the order first given, and is `None` when no
    metadata filter was given: there is no horizon to report."""

    items: list[RunListItemResponse]
    has_more: bool
    metadata_horizon: list[MetadataHorizonResponse] | None


class RunDetailResponse(BaseModel):
    """The response body for `GET /api/v1/runs/{run_id}`. Carries
    `interrupt_reason`, which the lean list entry omits -- the detail path
    keeps the full record reachable."""

    id: str
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    interrupt_reason: str | None
    presentation: str
    vcs: RunVcsResponse | None


class MetadataItemResponse(BaseModel):
    """One entry of `RunMetadataResponse`: one key a run reported. `source`
    is `file` or `session`, and `source_file` names the declared file of a
    `file` key and is `None` for a `session` one. `value` is `None` unless
    `status` is `captured`. Built field by field in `routes/read.py`."""

    key: str
    name: str | None
    value: str | None
    status: str
    source: str
    source_file: str | None
    declared: bool


class RunMetadataResponse(BaseModel):
    """The response body for `GET /api/v1/runs/{run_id}/metadata`: every
    key the run reported, ordered by key. Not paged -- a run holds at most
    `MAX_METADATA_ENTRIES` keys."""

    items: list[MetadataItemResponse]


class FailureProjectionResponse(BaseModel):
    """The lean failure projection nested on `ResultListItemResponse`. No
    `traceback`, `failure_repr` or captured-output field at all, so a
    results list cannot carry them -- the same structural exclusion
    `RunVcsResponse` applies to `root`."""

    failure_type: str | None
    failure_message: str | None
    failure_message_truncated: bool
    failure_path: str | None
    failure_lineno: int | None
    skip_reason: str | None
    xfail_reason: str | None


class ResultListItemResponse(BaseModel):
    """One entry of `ResultsResponse`. `failure` is a lean
    `FailureProjectionResponse`, never the full failure evidence -- the full
    record is reachable via `ResultDetailResponse`. Built field by field in
    `routes/read.py`, never `model_validate(..., from_attributes=True)`."""

    node_id: str
    file_path: str
    class_name: str | None
    function_name: str
    param_id: str | None
    outcome: str
    duration: float | None
    started_at: datetime | None
    finished_at: datetime | None
    setup_outcome: str | None
    call_outcome: str | None
    teardown_outcome: str | None
    setup_duration: float | None
    call_duration: float | None
    teardown_duration: float | None
    worker_id: str | None
    failure: FailureProjectionResponse | None


class ResultsResponse(BaseModel):
    """The response body for `GET /api/v1/runs/{run_id}/results`."""

    items: list[ResultListItemResponse]
    has_more: bool


class ResultDetailResponse(BaseModel):
    """The response body for `GET /api/v1/runs/{run_id}/result` -- the full
    record, every field a list response bounds or excludes, unbounded by any
    display width. Flat, matching `ResultReport`'s own wire shape for the
    same fields, rather than nesting `failure`/`captured` sub-objects. Built
    field by field in `routes/read.py`, never
    `model_validate(..., from_attributes=True)`."""

    node_id: str
    file_path: str
    class_name: str | None
    function_name: str
    param_id: str | None
    outcome: str
    duration: float | None
    started_at: datetime | None
    finished_at: datetime | None
    setup_outcome: str | None
    call_outcome: str | None
    teardown_outcome: str | None
    setup_duration: float | None
    call_duration: float | None
    teardown_duration: float | None
    worker_id: str | None
    failure_type: str | None
    failure_message: str | None
    failure_message_truncated: bool
    failure_path: str | None
    failure_lineno: int | None
    failure_repr: str | None
    failure_repr_truncated: bool
    traceback: str | None
    traceback_truncated: bool
    skip_reason: str | None
    skip_reason_truncated: bool
    xfail_reason: str | None
    xfail_reason_truncated: bool
    captured_stdout: str | None
    captured_stdout_truncated: bool
    captured_stderr: str | None
    captured_stderr_truncated: bool


class HistoryEntryResponse(BaseModel):
    """One entry of `HistoryResponse`. `vcs` is a lean `RunVcsResponse`
    built from `HistoryEntry.vcs`, a `VcsProjection` with no `root` field --
    the same exclusion as the run list."""

    run_id: str
    started_at: datetime
    finished_at: datetime | None
    outcome: str
    duration: float | None
    vcs: RunVcsResponse | None


class HistoryResponse(BaseModel):
    """The response body for `GET /api/v1/tests/history`."""

    items: list[HistoryEntryResponse]
    has_more: bool


class SectionValue(BaseModel):
    """The stored `value` for one `test_sections` row.

    `model_dump_json()` on write, `model_validate_json()` on read -- Pydantic
    begins and ends at this model in both directions. The section's name is
    not repeated here: it is already the row's `key`, and two encodings of
    one fact would drift."""

    prefix: str


class SectionUpsertRequest(BaseModel):
    """The request body for `POST /api/v1/config/sections`."""

    name: str
    prefix: str


class SectionResponse(BaseModel):
    """One section, name and its normalized prefix -- the response body for
    the upsert route, and one entry of `SectionListResponse`."""

    name: str
    prefix: str


class SectionListResponse(BaseModel):
    """The response body for `GET /api/v1/config/sections`."""

    items: list[SectionResponse]


class SectionSummaryResponse(BaseModel):
    """One bucket's four published numbers -- the wire shape of
    `vantage.core.domain.sections.SectionSummary`. Built field by field in
    `routes/sections.py`, never `model_validate(..., from_attributes=True)`;
    `pass_percentage` is never recomputed here, only carried through from the
    pure core, which rounds it exactly once."""

    name: str
    total: int
    measured: int
    passing: int
    pass_percentage: float | None


class RunSectionSummaryResponse(BaseModel):
    """The response body for `GET /api/v1/runs/{run_id}/sections`.
    `unassigned` is its own field, never an entry of `items` -- always
    present, even when empty, so the sum of every item's `total` plus
    `unassigned.total` reconciles against the run's result count."""

    items: list[SectionSummaryResponse]
    unassigned: SectionSummaryResponse
