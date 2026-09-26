"""Pydantic v2 models of a session report, the request side of ingestion.

A validated report is converted to the core's dataclasses
(`ingestion/conversion.py`) before the storage port is touched, so no
Pydantic type crosses into the core or the storage adapters.

**The ``extra=`` setting differs per model on purpose**, because an unknown
key means something different in each place:

- ``RunReport``, ``VcsReport`` and the metadata models are
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
  back in ``Ingested.ignored`` so the drift stays visible.

**The metadata models carry no length or pattern constraint on any field.**
A failed Pydantic constraint rejects the whole session report, and a
declared configuration value is a string this project does not control, so
a constraint here would turn a typo in someone's config file into a lost
test session. Any bound on metadata belongs in
`conversion.py`'s `to_run_metadata`, which drops the offending value
instead of rejecting the report.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from vantage.core.domain.execution import IDENTITY_PATTERN

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
# `test_outcome_vocabulary_matches_across_schema_sql_core_and_ingestion` keeps
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

    id: str = Field(pattern=IDENTITY_PATTERN)
    started_at: _UtcDatetime
    finished_at: _UtcDatetime | None
    exit_status: int | None = Field(ge=INT64_MIN, le=INT64_MAX)
    interrupted: bool
    interrupt_reason: str | None


class ResultReport(BaseModel):
    """One test's resolved outcome inside the ``results`` section.

    ``extra="allow"`` (see the module docstring). `ingestion/conversion.py`
    collects the tolerated key **names**, deduplicated, into
    `Ingested.ignored` as `results[].<name>` -- never a per-index
    path, so one unknown key on 500 results is one entry, not 500.

    The fields up to `worker_id` are required with no default, as on
    `RunReport`. Nullability mirrors the core's `CaseIdentity`/`Result`:
    `node_id`, `file_path`, `function_name` and `outcome` are never null
    there, so `null` for one of them fails validation. The rest may be null:
    a test phase may never have run, an identity component may be absent,
    and `worker_id` is null outside xdist.

    **The failure and captured-output fields are the exception**: an older
    plugin sends none of them, so each defaults to its absent shape
    (`None`/`False`). `conversion.py`'s `_to_failure_evidence` and
    `_to_captured_output` OR each `*_truncated` flag with the server's own
    bound, never assign it.

    `class_name` and `param_id` are plain `str | None`: `param_id=""` (a
    parametrised test whose id is the empty string) must arrive intact and
    distinct from `None`. **No `min_length=1`, no validator that coerces a
    falsy value to `None`.** The same goes for the durations: a genuine
    `0.0` must survive as `0.0`, never `x or None`. A duration must be
    finite, though (`allow_inf_nan=False`): `1e400` is valid JSON that
    parses as infinity, which no JSON response can carry, so it would be
    stored and then read back as `null`.
    """

    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

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


class MetadataKeyReport(BaseModel):
    """One entry of `MetadataReport.keys`: what the declaration says about
    one key it names.

    ``extra="forbid"``, and no constraint on `name` (see the module
    docstring). `name` is optional because a declaration need not give a key
    a display name.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None


class MetadataValueReport(BaseModel):
    """One entry of `MetadataReport.values`: a key the test session set
    itself, or a declared key that nothing gave a value.

    ``extra="forbid"``, and no constraint on any field (see the module
    docstring). `value` is `None` unless `status` is `"captured"`; the
    plugin decides every status, and `_to_run_metadata` stores an entry that
    contradicts itself as `absent` rather than refusing the report.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    value: str | None
    status: str


class MetadataReport(BaseModel):
    """The ``metadata`` section of a session report: the declaration's own
    name (`None` when the session had no declaration file), the display
    names it gives keys, one `MetadataFileReport` per file it named, and the
    values the session reported itself.

    ``extra="forbid"``, and **no constraint on `declaration` either** (see
    the module docstring). `keys`, `files` and `values` may each be left
    out: a session reporting values needs no declaration file, and a report
    other than the last of a session carries no values.
    """

    model_config = ConfigDict(extra="forbid")

    declaration: str | None
    keys: dict[str, MetadataKeyReport] = {}
    files: list[MetadataFileReport] = []
    values: list[MetadataValueReport] = []


class SessionReport(BaseModel):
    """The envelope of a session report, as submitted to `POST /api/v1/runs`.

    ``extra="ignore"`` (see the module docstring): an older server tolerates
    sections a newer plugin adds rather than rejecting the report.

    ``results``, ``vcs`` and ``metadata`` default to `None`, because an older
    plugin -- or, for ``metadata``, a session with no metadata to report --
    sends no such key. For ``results``, `None` (the
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
