"""Pydantic v2 models of the HTTP responses and of the section settings.

A session report's own models are `vantage.ingestion.schemas`: the local
store validates a report exactly as the server does, without the server.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from vantage.core.domain.access import DEFAULT_SCOPES


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
    """One entry of `RunListResponse`. `recorded_by` is the user whose token
    created the run, `None` for one recorded without a token."""

    id: str
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    presentation: str
    vcs: RunVcsResponse | None
    recorded_by: str | None


class MetadataHorizonResponse(BaseModel):
    """One entry of `RunListResponse.metadata_horizon`, for one filtered
    key. `predating` is the count of runs recorded before any run carried
    `key`, which equals the total run count when none ever has."""

    key: str
    predating: int


class RunListResponse(BaseModel):
    """The response body for `GET /api/v1/projects/{project}/runs`. No `total`, which would cost
    a `COUNT(*)` on every page. `next_cursor` is the `cursor` for the page
    after this one, and `None` on the last page. `metadata_horizon` holds one
    entry per distinct filtered key, in the order first given, and is `None`
    when no metadata filter was given: there is no horizon to report."""

    items: list[RunListItemResponse]
    has_more: bool
    next_cursor: str | None
    metadata_horizon: list[MetadataHorizonResponse] | None


class RunDetailResponse(BaseModel):
    """The response body for `GET /api/v1/runs/{run_id}`. Carries
    `interrupt_reason`, which the lean list entry omits -- the detail path
    keeps the full record reachable. `recorded_by` is as on
    `RunListItemResponse`; `project` is the one the run was created in,
    whose history and sections the run is read against."""

    id: str
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    interrupt_reason: str | None
    presentation: str
    vcs: RunVcsResponse | None
    recorded_by: str | None
    project: str


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


class MetadataFileResponse(BaseModel):
    """One entry of `RunMetadataResponse.files`: one file the run's
    declaration named. `status` is `captured` when it was read, and
    otherwise says why its keys have no value. Built field by field in
    `routes/read.py`."""

    source_file: str
    content_type: str
    status: str


class RunMetadataResponse(BaseModel):
    """The response body for `GET /api/v1/runs/{run_id}/metadata`: every
    key the run reported, ordered by key, and every file it declared,
    ordered by path. Not paged -- a run holds at most `MAX_METADATA_ENTRIES`
    keys."""

    items: list[MetadataItemResponse]
    files: list[MetadataFileResponse]


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
    """The response body for `GET /api/v1/projects/{project}/tests/history`. `next_cursor`, as
    on `RunListResponse`, is the `cursor` for the page after this one, and
    `None` on the last page."""

    items: list[HistoryEntryResponse]
    has_more: bool
    next_cursor: str | None


class SectionValue(BaseModel):
    """The stored `value` for one `test_sections` row.

    `model_dump_json()` on write, `model_validate_json()` on read -- Pydantic
    begins and ends at this model in both directions. The section's name is
    not repeated here: it is already the row's `key`, and two encodings of
    one fact would drift."""

    prefix: str


class SectionUpsertRequest(BaseModel):
    """The request body for `POST /api/v1/projects/{project}/config/sections`."""

    name: str
    prefix: str


class SectionResponse(BaseModel):
    """One section, name and its normalized prefix -- the response body for
    the upsert route, and one entry of `SectionListResponse`."""

    name: str
    prefix: str


class SectionListResponse(BaseModel):
    """The response body for `GET /api/v1/projects/{project}/config/sections`."""

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


# --- Users and tokens -------------------------------------------------------
#
# The request models forbid unknown fields, so a misspelt `disable` is a 422
# rather than a change half made, and are strict, so `"true"` or `1` is not
# a boolean. Names, scopes and labels are plain strings here and checked by
# the domain's own rules (`core/domain/access.py`), which the command line
# applies too, so each failure keeps its own error code.


class UserCreateRequest(BaseModel):
    """The request body for `POST /api/v1/users`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    admin: bool = False


class UserUpdateRequest(BaseModel):
    """The request body for `PATCH /api/v1/users/{name}`: each field given
    and not null is set, and at least one must be."""

    model_config = ConfigDict(extra="forbid", strict=True)

    admin: bool | None = None
    disabled: bool | None = None


class TokenCreateRequest(BaseModel):
    """The request body for `POST /api/v1/tokens`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    user: str
    scopes: list[str] = Field(default_factory=lambda: sorted(DEFAULT_SCOPES))
    label: str = ""


class UserResponse(BaseModel):
    """One user. `has_password` says whether they can log in."""

    name: str
    admin: bool
    disabled: bool
    created_at: datetime
    has_password: bool


class UserListResponse(BaseModel):
    """The response body for `GET /api/v1/users`: every user, by name."""

    items: list[UserResponse]


class TokenResponse(BaseModel):
    """One token, without the token itself, which is never stored.
    `scopes` is sorted; `expires_at` is set for a login token alone."""

    id: int
    user: str
    label: str
    scopes: list[str]
    created_at: datetime
    revoked_at: datetime | None
    expires_at: datetime | None


class TokenListResponse(BaseModel):
    """The response body for `GET /api/v1/tokens`: every token, or one
    user's, revoked ones included, oldest first."""

    items: list[TokenResponse]


class CreatedTokenResponse(BaseModel):
    """The response body for `POST /api/v1/tokens` and `POST /api/v1/login`:
    the token, shown this once and kept out of the `repr`, and what
    `TokenResponse` says of it."""

    token: str = Field(repr=False)
    id: int
    user: str
    label: str
    scopes: list[str]
    created_at: datetime
    revoked_at: datetime | None
    expires_at: datetime | None


# The password request bodies. `hide_input_in_errors` keeps a password out
# of a validation error's text, which otherwise repeats the whole input of
# a body missing a field, and `repr=False` keeps it out of the model's own.


class LoginRequest(BaseModel):
    """The request body for `POST /api/v1/login`."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    name: str
    password: str = Field(repr=False)


class PasswordChangeRequest(BaseModel):
    """The request body for `POST /api/v1/password`: whose password, the
    current one, and the new one."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    name: str
    password: str = Field(repr=False)
    new_password: str = Field(repr=False)


class PasswordSetRequest(BaseModel):
    """The request body for `PUT /api/v1/users/{name}/password`."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    password: str = Field(repr=False)


# --- Browser sessions -------------------------------------------------------


class SessionUserResponse(BaseModel):
    """Who a session acts as: the user, and whether its token may
    administer now."""

    name: str
    admin: bool


class SessionResponse(BaseModel):
    """The response body for `POST /api/v1/session` and `GET
    /api/v1/session`, never holding the token itself. On an open server
    `open` is true and there is no user; otherwise `user` is the caller,
    and `expires_at` is when its token stops authenticating -- set for a
    login token alone."""

    open: bool
    user: SessionUserResponse | None
    expires_at: datetime | None


# --- Projects ---------------------------------------------------------------


class ProjectCreateRequest(BaseModel):
    """The request body for `POST /api/v1/projects`. The name is checked by
    the domain's own rule, which the command line applies too."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str


class ProjectResponse(BaseModel):
    """One project. `role` is the one the caller acts with in it: `owner`
    for an admin, `editor` in `default`, otherwise their member row's; `None`
    on a server with no user, which checks no role."""

    name: str
    created_at: datetime
    role: str | None


class ProjectListResponse(BaseModel):
    """The response body for `GET /api/v1/projects`: every project the
    caller has a role in -- every project, for an admin or on a server with
    no user -- by name."""

    items: list[ProjectResponse]


# --- Members ----------------------------------------------------------------


class MemberSetRequest(BaseModel):
    """The request body for `PUT /api/v1/projects/{project}/members/{user}`.
    The role is checked by the domain's own rule, which the command line
    applies too."""

    model_config = ConfigDict(extra="forbid", strict=True)

    role: str


class MemberResponse(BaseModel):
    """One member of a project, and their role in it."""

    user: str
    role: str


class MemberListResponse(BaseModel):
    """The response body for `GET /api/v1/projects/{project}/members`: the
    project's members, by user. `everyone` is the role every user holds
    there without a row -- `editor` in `default`, which has no members --
    and `None` elsewhere."""

    items: list[MemberResponse]
    everyone: str | None
