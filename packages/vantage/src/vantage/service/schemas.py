"""Pydantic v2 models for the ingestion boundary (design.md D1, D5).

``vantage.service`` is the one package RQ-24 does not constrain, so this is
where Pydantic v2 belongs -- at the system boundary, per CLAUDE.md's
validation rule. The validated model is converted to the core's ``Execution``
dataclass before the storage port is ever touched, so no Pydantic type
crosses into ``vantage.core`` (RQ-26, RQ-30.2).

**`extra=` is asymmetric between `RunReport` and `SessionReport`, and that
asymmetry is deliberate -- it is the whole point of the HTTP boundary.**
``RunReport`` (the ``run`` section) is ``extra="forbid"``. The envelope,
``SessionReport``, is ``extra="ignore"``. That looks inconsistent; it is not.
Across a versioned HTTP boundary the two directions of version skew have
different costs (ADR-4, ADR-9):

- An unknown field **inside** ``run`` means the client and server disagree
  about what a run *is*. That is a client bug -- the plugin is either newer
  than this server understands or malformed -- and rejecting it loudly is a
  service to whoever wrote it. Silently accepting an unrecognised run field
  would let a typo or a schema drift pass unnoticed forever.
- An unknown **section** on the envelope means a newer ``pytest-vantage`` is
  talking to an older ``vantage`` -- Milestone 2 adds ``"results"``,
  Milestone 3 adds ``"environment"`` and ``"vcs"``, as sibling sections. That
  is an ordinary, expected, supported state, not an error: it is what lets
  the two distributions release independently at all (ADR-4). Ignoring it is
  the mechanism, not a shortcut.

If both were strict, every plugin upgrade would be a breaking change for
every server that has not yet been upgraded to match. If both ignored extras,
a typo'd or drifted run field would be silently swallowed instead of
rejected, which is exactly what RQ-42 exists to prevent. The asymmetry is not
an inconsistency to "fix" -- it is two different version-skew directions with
two different acceptable costs.

**A third `extra=` value, `"allow"`, appears on `ResultReport` (design.md
D15) and is a third position, not a compromise between the other two.** An
unknown key on one *result* -- a marker, a parameter -- is neither a schema
disagreement about what `run` is, nor an unnamed sibling section: it is
enrichment on a record whose known fields still fully validate. Rejecting it
would break every plugin upgrade the same way a strict `run` would; ignoring
it silently would hide the drift RQ-42 exists to surface. `"allow"` keeps
both: the report still records, and the tolerated key names surface,
deduplicated, in `Acknowledgement.ignored`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_IDENTITY_PATTERN = r"^[0-9a-f]{32}$"

# Mirrors `vantage.core.domain.result.OUTCOMES` (design.md D15/D17/D21). The
# plugin cannot import `vantage` (RQ-24), so the six-value vocabulary is
# necessarily declared a second time here rather than imported -- the
# consistency between the schema `CHECK`, `OUTCOMES` and this `Literal` is a
# task in its own right (design.md, "Interfaces / Contracts"), not this
# phase's (Phase 5, task 5.8).
_Outcome = Literal["passed", "failed", "error", "skipped", "xfailed", "xpassed"]


class RunReport(BaseModel):
    """The ``run`` section of a session report (design.md D1).

    ``extra="forbid"``: every field is named here, and nothing else is
    accepted. See the module docstring for why this differs from
    `SessionReport`. Every field is required and unaliased with no default --
    even the client's own nulls (`finished_at`, `interrupt_reason`) must be
    sent explicitly, so a field the client forgot to send is a rejection, not
    a silently substituted default.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_IDENTITY_PATTERN)
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    interrupt_reason: str | None


class ResultReport(BaseModel):
    """One test's resolved outcome inside the `results` section (design.md
    D15, D17, D18).

    ``extra="allow"``: unlike `RunReport`, an unknown key on a *result* is
    tolerated rather than rejected. See the module docstring for the
    `RunReport`/`SessionReport` asymmetry -- this is a third, distinct
    position for a third, distinct kind of drift: a newer plugin's enriched
    result (an added marker, an added parameter) must still record on an
    older server, and the tolerance must be visible rather than silent.
    `service/routes/runs.py` collects the tolerated key **names**,
    deduplicated, into `Acknowledgement.ignored` as `results[].<name>` --
    never a per-index path, so one unknown key on 500 results is one entry,
    not 500.

    Every known field is required with no default, matching `RunReport`'s
    rule: even a field whose type allows `None` must be sent explicitly, so
    a field the client forgot to send is a rejection (422), not a silently
    substituted null. Nullability itself mirrors
    `vantage.core.domain.result.CaseIdentity`/`Result`: `node_id`,
    `file_path`, `function_name` and `outcome` are never null there, so they
    are not optional here either -- a client that sends `null` for one of
    them fails validation exactly like a client that omits it, because the
    core dataclasses that receive the converted value do not accept `None`
    for those fields. Every other field can be null because the
    corresponding phase may never have run (RQ-5.2) or the identity
    component may genuinely be absent (RQ-9.2/9.3).

    **The failure-evidence fields below are the one exception to "every
    known field is required with no default."** An older plugin's report
    predates every one of them, so each defaults to the absent shape
    (`None`/`False`) rather than rejecting (design.md D75).
    `routes/runs.py`'s `_to_failure_evidence`/`_to_captured_output` OR each
    `*_truncated` flag with the server's own bound, never assign it.

    `class_name` and `param_id` are plain `str | None`, never a
    length-constrained string: `param_id=""` (RQ-9's extension scenario)
    must arrive intact. **No `min_length=1`, no validator that coerces a
    falsy value to `None`** -- either would silently turn the empty-string
    case into the null case, which is exactly the distinction RQ-9 exists to
    preserve (design.md D18, the ``""`` is not `None` hop). The same rule
    applies to `duration` and the three phase durations: a genuine `0.0`
    must survive as `0.0`, never `x or None`.
    """

    model_config = ConfigDict(extra="allow")

    node_id: str
    file_path: str
    class_name: str | None
    function_name: str
    param_id: str | None
    outcome: _Outcome
    duration: float | None
    started_at: datetime | None
    finished_at: datetime | None
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
    failure_lineno: int | None = None
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
    """The ``vcs`` section of a session report (design.md D47).

    ``extra="forbid"``, matching `RunReport` rather than `ResultReport`: an
    unknown field *inside* `vcs` means the two sides disagree about what a
    VCS snapshot is, not `ResultReport`'s enrichment case. ``commit`` is
    bounded with ``max_length=64``, never a 40-hex pattern -- a SHA-256
    repository produces 64 hex characters. Every field is required with no
    default, matching `RunReport`'s rule: all five nulls must be sent
    explicitly. No ``commit_subject_truncated`` field: the server owns the
    bound and the flag (design.md D49).
    """

    model_config = ConfigDict(extra="forbid")

    commit: str | None = Field(max_length=64)
    branch: str | None
    commit_subject: str | None
    dirty: bool | None
    root: str | None


class SessionReport(BaseModel):
    """The envelope submitted to `POST /api/v1/runs` (design.md D1).

    ``extra="ignore"``: see the module docstring. Milestone 2 and 3 add
    sibling sections (``results``, ``environment``, ``vcs``) that an older
    server must tolerate rather than reject.

    ``results`` is `None` by default -- design.md D15's "optional sibling
    section". `None` (the section is absent) and `[]` (the session collected
    nothing) are both legal and both mean zero result rows: a reverted
    plugin against an un-reverted server still must have its run stored.

    ``vcs`` is `None` by default for the same reason -- an older plugin's
    report carries no such key, and `extra="ignore"` on this envelope drops
    an unrecognised key rather than rejecting the whole report (design.md
    D47). No capability gate exists for it: a flag nothing branches on would
    advertise a gate that does not exist.
    """

    model_config = ConfigDict(extra="ignore")

    run: RunReport
    results: list[ResultReport] | None = None
    vcs: VcsReport | None = None

    @field_validator("results")
    @classmethod
    def _reject_duplicate_node_ids(
        cls, value: list[ResultReport] | None
    ) -> list[ResultReport] | None:
        """D19 layer 2: a duplicate `node_id` inside one report is rejected
        loudly and wholesale, before it ever reaches the silent replay
        backstop (D19 layer 3). The `ValueError` message never repeats a
        `node_id` -- not because it would reach the client (`errors.py`
        builds every rejection body from `loc` segments only, never a
        validator's message text), but for the same discipline `errors.py`'s
        own docstring names: nothing client-chosen is passed through, even
        where it currently happens not to be echoed.
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
    """The response body for both `201` and `200` (design.md D3)."""

    run_id: str
    status: str
    ignored: list[str] = []


class HeartbeatAcknowledgement(BaseModel):
    """The response body for `POST /runs/{run_id}/heartbeat` (design.md D33).

    A distinct model from `Acknowledgement` rather than a reuse with
    `ignored` defaulted -- the heartbeat request carries no `results`
    section, so there is nothing that could ever populate `ignored`, and a
    field that can never be non-empty does not belong on the response shape.
    """

    run_id: str
    status: str


class RunVcsResponse(BaseModel):
    """The VCS section of a run response, list or detail alike (design.md
    D59).

    **No `root` field, on purpose.** `routes/read.py` builds this model
    field by field from either a lean `VcsProjection` (list path) or a full
    `VcsContext` (detail path) -- both carry `commit`, `branch`,
    `commit_subject`, `commit_subject_truncated` and `dirty` under the same
    names, so one response model serves both callers. Neither caller ever
    reads `root` off its source object to populate this model; the field
    simply does not exist here to be populated. That is what keeps
    `VcsContext.root` off the wire on the detail path, where nothing else
    would stop it (design.md D59's own point -- the exclusion is a response-
    model choice, not a structural one, precisely because `VcsContext` is
    the type in hand there).
    """

    commit: str | None
    branch: str | None
    commit_subject: str | None
    commit_subject_truncated: bool
    dirty: bool | None


class RunListItemResponse(BaseModel):
    """One entry of `RunListResponse` (design.md D57, D59, D62). Matches the
    design's own wire-shape example field for field."""

    id: str
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    presentation: str
    vcs: RunVcsResponse | None


class RunListResponse(BaseModel):
    """The response body for `GET /api/v1/runs` (design.md D58 -- no
    `total`)."""

    items: list[RunListItemResponse]
    has_more: bool


class RunDetailResponse(BaseModel):
    """The response body for `GET /api/v1/runs/{run_id}` (design.md D57,
    D59, D62). Carries `interrupt_reason`, which the lean list entry omits
    -- the detail path keeps the full record reachable, matching
    `RunDetail`'s own reason for existing."""

    id: str
    started_at: datetime
    finished_at: datetime | None
    exit_status: int | None
    interrupted: bool
    interrupt_reason: str | None
    presentation: str
    vcs: RunVcsResponse | None


class FailureProjectionResponse(BaseModel):
    """The lean failure projection nested on `ResultListItemResponse`
    (design.md D76). No `traceback`, `failure_repr` or captured-output
    field at all -- the exclusion is structural, the same defence
    `RunVcsResponse` gives `vcs_root` (D59): a results list has nothing to
    leak because this model never carries those fields in the first
    place."""

    failure_type: str | None
    failure_message: str | None
    failure_message_truncated: bool
    failure_path: str | None
    failure_lineno: int | None
    skip_reason: str | None
    xfail_reason: str | None


class ResultListItemResponse(BaseModel):
    """One entry of `ResultsResponse` (design.md D57, D76). `failure` is a
    lean `FailureProjectionResponse`, never the full failure evidence --
    the full record is reachable via `ResultDetailResponse`, the
    single-result endpoint's response model. Built field by field in
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
    """The response body for `GET /api/v1/runs/{run_id}/results` (design.md
    D57, D61)."""

    items: list[ResultListItemResponse]
    has_more: bool


class ResultDetailResponse(BaseModel):
    """The response body for `GET /api/v1/runs/{run_id}/result` (design.md
    D77, D78) -- the full record, every field a list response bounds or
    excludes, unbounded by any display width. Flat, matching
    `ResultReport`'s own wire shape for the same fields, rather than
    nesting `failure`/`captured` sub-objects. Built field by field in
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
    """One entry of `HistoryResponse` (design.md D54, D57, D59, D60). `vcs`
    is a lean `RunVcsResponse` built from `HistoryEntry.vcs` (a
    `VcsProjection`, no `root` field -- same structural exclusion as the
    run list, D59)."""

    run_id: str
    started_at: datetime
    finished_at: datetime | None
    outcome: str
    duration: float | None
    vcs: RunVcsResponse | None


class HistoryResponse(BaseModel):
    """The response body for `GET /api/v1/tests/history` (design.md D54,
    D57, D61)."""

    items: list[HistoryEntryResponse]
    has_more: bool
