"""Pydantic v2 models for the HTTP boundary.

Pydantic is confined to ``vantage.service``; ``vantage.core`` and
``vantage.storage`` take no third-party dependency. A validated report is
converted to the core's dataclasses before the storage port is touched, so
no Pydantic type crosses into the core.

**The ``extra=`` setting differs per model on purpose**, because an unknown
key means something different in each place:

- ``RunReport``, ``VcsReport`` and the two metadata models are
  ``extra="forbid"``. An unknown field *inside* one of these sections means
  client and server disagree about what the section is -- a bug or a typo,
  which is rejected loudly rather than swallowed.
- ``SessionReport``, the envelope, is ``extra="ignore"``. An unknown sibling
  section means a newer ``pytest-vantage`` is talking to an older
  ``vantage``. That is a supported state, and ignoring the section is what
  lets the two distributions release independently.
- ``ResultReport`` is ``extra="allow"``. An unknown key on one result (a
  marker, a parameter) is enrichment on a record whose known fields still
  validate: the report is recorded, and the tolerated key names are reported
  back in ``Acknowledgement.ignored`` so the drift stays visible.

**The metadata models carry no length or pattern constraint on any field.**
A failed Pydantic constraint rejects the whole session report with a
``422``, and a declared configuration value is a string this project does
not control, so a constraint here would turn a typo in someone's config
file into a lost test session. Any bound on metadata belongs in
``routes/runs.py``'s ``_to_run_metadata``, which drops the offending value
instead of rejecting the report.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

_IDENTITY_PATTERN = r"^[0-9a-f]{32}$"

# SQLite stores an integer as signed 64 bits and refuses a larger one at
# parameter binding, after validation, as a 500 that loses the whole report.
# Bounding here makes it a 422 naming the field, whichever store is behind.
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def _to_utc(value: datetime) -> datetime:
    """Normalise a reported timestamp to UTC.

    Stored timestamps are TEXT, and `test_case.last_seen_at` only moves
    forward via `MAX(...)` -- a lexicographic comparison, correct only when
    every string has the same offset. The plugin already sends UTC, but any
    HTTP client can report here, so the server does not trust the wire.

    A naive value is stamped as UTC with `replace()`: `astimezone()` on a
    naive value would assume the server's local zone. An aware value whose
    UTC form leaves years 1-9999 cannot be stored, and converting it here
    makes that a validation error on its own field.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    try:
        return value.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("timestamp is out of range once converted to UTC") from None


_UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]

# Mirrors `vantage.core.domain.result.OUTCOMES`. A `Literal` must spell out
# its values, so the vocabulary is declared again here;
# `test_outcome_vocabulary_matches_across_schema_sql_core_and_service` keeps
# it in step with `OUTCOMES` and the schema's `CHECK`.
_Outcome = Literal["passed", "failed", "error", "skipped", "xfailed", "xpassed"]


class RunReport(BaseModel):
    """The ``run`` section of a session report.

    ``extra="forbid"`` (see the module docstring). Every field is required
    with no default -- even the nullable ones (`finished_at`,
    `interrupt_reason`) must be sent explicitly, so a field the client forgot
    is a rejection, not a silently substituted default.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_IDENTITY_PATTERN)
    started_at: _UtcDatetime
    finished_at: _UtcDatetime | None
    exit_status: int | None = Field(ge=INT64_MIN, le=INT64_MAX)
    interrupted: bool
    interrupt_reason: str | None


class ResultReport(BaseModel):
    """One test's resolved outcome inside the ``results`` section.

    ``extra="allow"`` (see the module docstring). `service/routes/runs.py`
    collects the tolerated key **names**, deduplicated, into
    `Acknowledgement.ignored` as `results[].<name>` -- never a per-index
    path, so one unknown key on 500 results is one entry, not 500.

    The fields up to `worker_id` are required with no default, as on
    `RunReport`. Nullability mirrors the core's `CaseIdentity`/`Result`:
    `node_id`, `file_path`, `function_name` and `outcome` are never null
    there, so `null` for one of them fails validation. The rest may be null:
    a test phase may never have run, an identity component may be absent,
    and `worker_id` is null outside xdist.

    **The failure and captured-output fields are the exception**: an older
    plugin sends none of them, so each defaults to its absent shape
    (`None`/`False`). `routes/runs.py`'s `_to_failure_evidence` and
    `_to_captured_output` OR each `*_truncated` flag with the server's own
    bound, never assign it.

    `class_name` and `param_id` are plain `str | None`: `param_id=""` (a
    parametrised test whose id is the empty string) must arrive intact and
    distinct from `None`. **No `min_length=1`, no validator that coerces a
    falsy value to `None`.** The same goes for the durations: a genuine
    `0.0` must survive as `0.0`, never `x or None`.
    """

    model_config = ConfigDict(extra="allow")

    node_id: str
    file_path: str
    class_name: str | None
    function_name: str
    param_id: str | None
    outcome: _Outcome
    duration: float | None
    started_at: _UtcDatetime | None
    finished_at: _UtcDatetime | None
    setup_outcome: _Outcome | None
    call_outcome: _Outcome | None
    teardown_outcome: _Outcome | None
    setup_duration: float | None
    call_duration: float | None
    teardown_duration: float | None
    worker_id: str | None
    failure_type: str | None = None
    failure_message: str | None = None
    failure_message_truncated: bool = False
    failure_path: str | None = None
    failure_lineno: int | None = Field(default=None, ge=INT64_MIN, le=INT64_MAX)
    failure_repr: str | None = None
    failure_repr_truncated: bool = False
    traceback: str | None = None
    traceback_truncated: bool = False
    skip_reason: str | None = None
    skip_reason_truncated: bool = False
    xfail_reason: str | None = None
    xfail_reason_truncated: bool = False
    captured_stdout: str | None = None
    captured_stdout_truncated: bool = False
    captured_stderr: str | None = None
    captured_stderr_truncated: bool = False


class VcsReport(BaseModel):
    """The ``vcs`` section of a session report.

    ``extra="forbid"`` (see the module docstring). ``commit`` is bounded with
    ``max_length=64``, never a 40-hex pattern -- a SHA-256 repository
    produces 64 hex characters. Every field is required with no default, so
    all five nulls must be sent explicitly. There is no
    ``commit_subject_truncated`` field: the server applies the subject bound
    and sets the flag itself.
    """

    model_config = ConfigDict(extra="forbid")

    commit: str | None = Field(max_length=64)
    branch: str | None
    commit_subject: str | None
    dirty: bool | None
    root: str | None


class MetadataFileReport(BaseModel):
    """One entry of `MetadataReport.files`: the outcome recorded for one
    declared file, whether or not it was captured.

    ``extra="forbid"``, and **no constraint of any kind on `path`, `keys` or
    `content`** (see the module docstring). `content` is `None` whenever
    `status` is not `"captured"`: a declared file that was dropped is still
    reported, not omitted.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    format: str
    status: str
    keys: list[str]
    content: str | None


class MetadataReport(BaseModel):
    """The ``metadata`` section of a session report: the declaration's own
    name, and one `MetadataFileReport` per file it named.

    ``extra="forbid"``, and **no constraint on `declaration` either** (see
    the module docstring).
    """

    model_config = ConfigDict(extra="forbid")

    declaration: str
    files: list[MetadataFileReport]


class SessionReport(BaseModel):
    """The envelope submitted to `POST /api/v1/runs`.

    ``extra="ignore"`` (see the module docstring): an older server tolerates
    sections a newer plugin adds rather than rejecting the report.

    ``results``, ``vcs`` and ``metadata`` default to `None`, because an older
    plugin -- or, for ``metadata``, a session run without
    `--vantage-metadata` -- sends no such key. For ``results``, `None` (the
    section is absent) and `[]` (the session collected nothing) both mean
    zero result rows; the run is still stored.
    """

    model_config = ConfigDict(extra="ignore")

    run: RunReport
    results: list[ResultReport] | None = None
    vcs: VcsReport | None = None
    metadata: MetadataReport | None = None

    @field_validator("results")
    @classmethod
    def _reject_duplicate_node_ids(
        cls, value: list[ResultReport] | None
    ) -> list[ResultReport] | None:
        """Reject a duplicate `node_id` inside one report, loudly and
        wholesale. Otherwise storage's `ON CONFLICT ... DO NOTHING` result
        insert would silently keep the first entry and drop the rest.

        The message never repeats the `node_id`: nothing client-chosen is
        passed through, even though `errors.py` builds rejection bodies from
        `loc` segments only and would not echo it anyway.
        """
        if value is None:
            return value
        seen: set[str] = set()
        for item in value:
            if item.node_id in seen:
                raise ValueError("results contains a duplicate node_id")
            seen.add(item.node_id)
        return value


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
    """`RunListResponse.metadata_horizon`'s populated shape -- present only
    when a metadata filter was supplied. `predating` is the count of runs
    recorded before `key` was ever declared, which equals the total run
    count when `key` was never declared at all."""

    key: str
    predating: int


class RunListResponse(BaseModel):
    """The response body for `GET /api/v1/runs`. No `total`, which would cost
    a `COUNT(*)` on every page. `metadata_horizon` is `None` when no metadata
    filter was given: there is no horizon to report."""

    items: list[RunListItemResponse]
    has_more: bool
    metadata_horizon: MetadataHorizonResponse | None


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
