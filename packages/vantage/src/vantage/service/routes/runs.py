"""`POST /api/v1/runs` -- session report ingestion (RQ-41, RQ-42, design.md D3, D5).

**The 201-vs-200 decision comes from the boolean `record_session` already
returns.** `record_session` is `INSERT ... ON CONFLICT(id) DO NOTHING`,
deciding its own return value from the INSERT's own row count -- no
preceding `SELECT`. This route does not ask the store whether the id exists
and then decide: that would reintroduce, at the HTTP layer, precisely the
check-then-act race the storage adapter's `ON CONFLICT` avoids at the SQL
layer. One call, one boolean, one branch.

**The media type and size checks both run before the body is touched.**
This route does *not* declare `payload: SessionReport` as a parameter --
that would make FastAPI parse and fully buffer the body itself before this
function's first line ever runs, which is exactly the ordering hazard RQ-42
warns against: a check performed after the bytes are already buffered has
protected nothing. Instead the body is read by hand, in the order the
threat matrix requires:

1. `Content-Type` is read off the header alone (`_require_json_media_type`)
   -- zero body bytes have been touched yet.
2. `_read_bounded_body` streams the body through `request.stream()` and
   raises the instant the running total exceeds `MAX_REPORT_BYTES`, without
   asking the stream for another chunk. That is the one line doing the
   actual protecting -- it does not trust `Content-Length`, which can be
   absent, wrong, or simply a lie. A client that disconnects mid-transfer
   (RQ-3) surfaces here too, as `starlette.requests.ClientDisconnect`,
   converted to `IncompleteBodyError` rather than left to propagate.
3. Only once a complete, capped byte string exists is it parsed as JSON,
   then validated against `SessionReport`.

Nothing is written before all three steps succeed, so a rejection at any
step leaves `count_executions() == 0`.

**`results` is optional and its absence is not an error (design.md D15).**
`payload.results` is `None` (section absent) or `[]` (nothing collected) as
often as it is a populated list -- both mean zero result rows, not a
rejection. `record_session`'s `results` parameter is keyword-only and
required (design.md D21), so this route always passes a list, even an empty
one, rather than special-casing the absent-section case.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import PurePath
from typing import overload

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.metadata import FILE_STATUSES
from vantage.core.domain.result import CapturedOutput, CaseIdentity, FailureEvidence, Result
from vantage.core.ports.storage import EMPTY_RUN_METADATA, MetadataEntry, MetadataFile, RunMetadata
from vantage.service import metadata_parse
from vantage.service.errors import (
    MAX_REPORT_BYTES,
    IncompleteBodyError,
    InvalidJsonError,
    InvalidReportError,
    PayloadTooLargeError,
    UnknownRunError,
    UnsupportedMediaTypeError,
    safe_segment,
)
from vantage.service.schemas import (
    Acknowledgement,
    HeartbeatAcknowledgement,
    MetadataReport,
    ResultReport,
    RunReport,
    SessionReport,
    VcsReport,
)
from vantage.service.truncation import truncate

router = APIRouter()

_JSON_MEDIA_TYPE = "application/json"
_IDENTITY_PATTERN = r"^[0-9a-f]{32}$"

_KNOWN_METADATA_CONTENT_TYPES = frozenset({"json", "yaml", "toml"})
"""Mirrors `run_metadata_file.content_type`'s SQL `CHECK` exactly
(schema.sql). `MetadataFileReport.format` carries no Pydantic constraint
(design.md D96's trap), so a value outside this set can never be written --
rather than let it reach `record_session` and raise a
`sqlite3.IntegrityError` mid-transaction (rolling back the run row with it,
the exact outcome D97's governing rule forbids), the whole file entry is
dropped: the same "well-behaved plugin cannot produce this" bucket D97
class 11 already uses for a rejected `source_file`."""

_MAX_DECLARED_PATH_CHARS = 1024
"""Mirrors `pytest_vantage.metadata.MAX_DECLARED_PATH_CHARS` (design.md D94)
across the RQ-24/ADR-4 package boundary -- `vantage` cannot import
`pytest-vantage`, so the two distributions each carry their own copy of this
bound, the same second-mechanism discipline the two storage adapters already
use for each other (design.md D98)."""


@overload
def _normalize_to_utc(value: datetime) -> datetime: ...
@overload
def _normalize_to_utc(value: None) -> None: ...
def _normalize_to_utc(value: datetime | None) -> datetime | None:
    """The one helper every timestamp that reaches the store goes through
    (Phase 5 UTC-normalization addendum, Engram observation 62's resolution).

    `test_case.last_seen_at` is TEXT and D20's monotonicity guard advances it
    with `MAX(...)` -- a **lexicographic** comparison, correct only when
    every stored string carries the same offset and the same width. The
    plugin already normalizes (`pytest_vantage.recorder.isoformat_utc`), but
    ADR-9 exists precisely so a client in another language can talk to this
    server without normalizing, so the boundary must not trust the wire.

    An **aware** value converts with `astimezone(timezone.utc)`. A **naive**
    value is stamped `replace(tzinfo=timezone.utc)` -- never `astimezone()`
    on a naive value, which silently assumes the SERVER's local zone and
    would make the stored instant depend on where the server happens to run.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_vcs_context(vcs: VcsReport | None) -> VcsContext | None:
    """Normalises the `vcs` section (design.md D48): `None` when the
    section is absent OR every field in it is null -- a session recorded
    outside a repository reads back as `execution.vcs is None`, never as a
    `VcsContext` full of nulls. `truncate()` is applied to `commit_subject`
    here, and only here, so `commit_subject_truncated` is always set
    consistently with the value it describes (design.md D49).
    """
    if vcs is None:
        return None
    commit_subject, commit_subject_truncated = truncate(vcs.commit_subject)
    if (
        vcs.commit is None
        and vcs.branch is None
        and commit_subject is None
        and vcs.dirty is None
        and vcs.root is None
    ):
        return None
    return VcsContext(
        commit=vcs.commit,
        branch=vcs.branch,
        commit_subject=commit_subject,
        commit_subject_truncated=commit_subject_truncated,
        dirty=vcs.dirty,
        root=vcs.root,
    )


def _declared_path_shape_is_valid(path: str) -> bool:
    """D93's server-side re-check: the server has no access to the client's
    filesystem, so containment itself cannot be re-verified here -- only
    shape. `source_file` must be short, relative, and free of `..`, the
    three properties `pytest_vantage.metadata.resolve_declared_path` also
    checks before ever touching a disk. A path failing this is dropped
    whole (D97 class 11), never rejected."""
    if len(path) > _MAX_DECLARED_PATH_CHARS:
        return False
    candidate = PurePath(path)
    if candidate.is_absolute() or candidate.drive or candidate.anchor:
        return False
    return ".." not in candidate.parts


def _to_run_metadata(metadata: MetadataReport | None) -> RunMetadata:
    """Normalises the `metadata` section (design.md D96, D97, D98),
    following `_to_vcs_context`'s shape: `None` when the section is absent.

    Every declared file becomes exactly one `MetadataFile` row and every one
    of its declared keys becomes exactly one `MetadataEntry` row, whether or
    not either was captured (D95) -- drop-whole everywhere, `truncate()` is
    never called, and this function never raises. A file whose `source_file`
    fails the server's own shape re-check, or whose unconstrained `status`/
    `format` fields (D96's trap) are not values this server can ever write,
    is dropped in its entirety: neither it nor its keys produce a row (D97
    class 11 and its two wire-shape siblings) -- a well-behaved plugin
    cannot produce any of these.
    """
    if metadata is None:
        return EMPTY_RUN_METADATA

    files: list[MetadataFile] = []
    entries: list[MetadataEntry] = []

    for file_report in metadata.files:
        if (
            not _declared_path_shape_is_valid(file_report.path)
            or file_report.format not in _KNOWN_METADATA_CONTENT_TYPES
            or file_report.status not in FILE_STATUSES
        ):
            continue

        if file_report.status != "captured" or file_report.content is None:
            # D97 classes 1-6: the plugin's own status is trusted verbatim
            # -- the server has no way to verify it independently.
            files.append(
                MetadataFile(
                    source_file=file_report.path,
                    content_type=file_report.format,
                    status=file_report.status,
                )
            )
            entries.extend(
                MetadataEntry(
                    key=key,
                    value=None,
                    source_file=file_report.path,
                    status="source_unavailable",
                )
                for key in file_report.keys
            )
            continue

        parsed = metadata_parse.parse(file_report.content, file_report.format, file_report.keys)
        if parsed is None:
            # D97 class 7: server-detected parse failure.
            files.append(
                MetadataFile(
                    source_file=file_report.path,
                    content_type=file_report.format,
                    status="malformed",
                )
            )
            entries.extend(
                MetadataEntry(
                    key=key,
                    value=None,
                    source_file=file_report.path,
                    status="source_unavailable",
                )
                for key in file_report.keys
            )
            continue

        # D97 classes 8-10, plus the `captured` case: `metadata_parse.parse`
        # already classified every declared key against the document.
        files.append(
            MetadataFile(
                source_file=file_report.path,
                content_type=file_report.format,
                status="captured",
            )
        )
        entries.extend(
            MetadataEntry(
                key=key, value=result.value, source_file=file_report.path, status=result.status
            )
            for key, result in parsed.items()
        )

    return RunMetadata(files=tuple(files), entries=tuple(entries))


def _to_execution(run: RunReport, vcs: VcsReport | None) -> Execution:
    return Execution(
        identity=Identity(run.id),
        started_at=_normalize_to_utc(run.started_at),
        finished_at=_normalize_to_utc(run.finished_at),
        exit_status=run.exit_status,
        interrupted=run.interrupted,
        interrupt_reason=run.interrupt_reason,
        vcs=_to_vcs_context(vcs),
    )


def _to_failure_evidence(item: ResultReport) -> FailureEvidence | None:
    """Normalises the failure-evidence fields (design.md D75, D77) --
    `None` when every field is null-or-false, mirroring `_to_vcs_context`'s
    D48 rule: a failure either happened or it did not.

    `truncate()` applies the server's own 64 KiB bound to every string
    field, exactly as `_to_vcs_context` applies it to `commit_subject`
    (D49). The truncation flag is a **disjunction** of the client's report
    and the server's own result, never a plain assignment: `truncate(None)`
    returns `(None, False)`, so a server that assigns `stored_flag =
    server_flag` would silently clear a budget drop the client already
    reported (design.md D75) -- the client is the only side that knows a
    field was dropped for budget rather than never captured.
    """
    failure_message, message_cut = truncate(item.failure_message)
    failure_message_truncated = bool(item.failure_message_truncated) or message_cut
    failure_repr, repr_cut = truncate(item.failure_repr)
    failure_repr_truncated = bool(item.failure_repr_truncated) or repr_cut
    traceback, traceback_cut = truncate(item.traceback)
    traceback_truncated = bool(item.traceback_truncated) or traceback_cut
    skip_reason, skip_cut = truncate(item.skip_reason)
    skip_reason_truncated = bool(item.skip_reason_truncated) or skip_cut
    xfail_reason, xfail_cut = truncate(item.xfail_reason)
    xfail_reason_truncated = bool(item.xfail_reason_truncated) or xfail_cut

    if (
        item.failure_type is None
        and failure_message is None
        and not failure_message_truncated
        and item.failure_path is None
        and item.failure_lineno is None
        and failure_repr is None
        and not failure_repr_truncated
        and traceback is None
        and not traceback_truncated
        and skip_reason is None
        and not skip_reason_truncated
        and xfail_reason is None
        and not xfail_reason_truncated
    ):
        return None

    return FailureEvidence(
        failure_type=item.failure_type,
        failure_message=failure_message,
        failure_message_truncated=failure_message_truncated,
        failure_path=item.failure_path,
        failure_lineno=item.failure_lineno,
        failure_repr=failure_repr,
        failure_repr_truncated=failure_repr_truncated,
        traceback=traceback,
        traceback_truncated=traceback_truncated,
        skip_reason=skip_reason,
        skip_reason_truncated=skip_reason_truncated,
        xfail_reason=xfail_reason,
        xfail_reason_truncated=xfail_reason_truncated,
    )


def _to_captured_output(item: ResultReport) -> CapturedOutput:
    """Normalises captured stdout/stderr (design.md D71, D77). Never
    `None` -- the empty-versus-absent distinction lives INSIDE this type,
    in the `str | None` fields, so `Result.captured` is always a
    `CapturedOutput` instance. `truncate()`'s `(None, False)` result for a
    `None` input keeps `None` (never captured) distinct from `""`
    (captured, empty) all the way through the bound.
    """
    stdout, stdout_cut = truncate(item.captured_stdout)
    stderr, stderr_cut = truncate(item.captured_stderr)
    return CapturedOutput(
        stdout=stdout,
        stdout_truncated=bool(item.captured_stdout_truncated) or stdout_cut,
        stderr=stderr,
        stderr_truncated=bool(item.captured_stderr_truncated) or stderr_cut,
    )


def _to_result(item: ResultReport) -> Result:
    # `started_at`/`finished_at` go through `_normalize_to_utc`, same as
    # `_to_execution` above -- one path, not two (Phase 5 UTC-normalization
    # addendum). This used to be deliberately absent (see the Phase 3-era
    # Engram observation 62 finding); it stops being true here.
    return Result(
        identity=CaseIdentity(
            node_id=item.node_id,
            file_path=item.file_path,
            class_name=item.class_name,
            function_name=item.function_name,
            param_id=item.param_id,
        ),
        outcome=item.outcome,
        duration=item.duration,
        started_at=_normalize_to_utc(item.started_at),
        finished_at=_normalize_to_utc(item.finished_at),
        setup_outcome=item.setup_outcome,
        call_outcome=item.call_outcome,
        teardown_outcome=item.teardown_outcome,
        setup_duration=item.setup_duration,
        call_duration=item.call_duration,
        teardown_duration=item.teardown_duration,
        worker_id=item.worker_id,
        failure=_to_failure_evidence(item),
        captured=_to_captured_output(item),
    )


def _ignored_result_keys(results: Sequence[ResultReport]) -> list[str]:
    """Deduplicated `results[].<name>` for every unknown key tolerated by
    `ResultReport`'s `extra="allow"` (design.md D15).

    Insertion order, one entry per key name regardless of how many results
    carried it -- 500 results sharing one unknown key produce one entry, not
    500 (D15's own example). Each name is routed through the same
    `safe_segment` allow-list `errors.py` uses for rejection bodies: an
    unknown result key is client-chosen text, exactly the kind of value that
    allow-list exists to make safe to echo (design.md, Threat Matrix).
    """
    seen: dict[str, None] = {}
    for item in results:
        for key in item.model_extra or {}:
            seen.setdefault(f"results[].{safe_segment(key)}", None)
    return list(seen)


def _require_json_media_type(request: Request) -> None:
    """Reject on the `Content-Type` header alone, before any body byte is read."""
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != _JSON_MEDIA_TYPE:
        raise UnsupportedMediaTypeError(media_type or "<absent>")


async def _read_bounded_body(request: Request) -> bytes:
    """Stream the body, aborting before the buffer can exceed the cap.

    Deliberately does not rely on `Content-Length`: it can be absent, wrong,
    or an outright lie, and none of those excuse buffering an unbounded
    body. The check below runs on every chunk actually received, so the
    buffer is structurally incapable of growing past `MAX_REPORT_BYTES`.

    A client that disconnects before sending the whole body -- a process
    killed mid-write (RQ-3.1), a network partition mid-transfer (RQ-3.2) --
    surfaces here as `starlette.requests.ClientDisconnect`, raised from
    inside `request.stream()` itself. Catching it and converting it to
    `IncompleteBodyError` is what keeps this the one exception-handling
    path every rejection goes through (design.md D5); the alternative is
    letting it propagate unhandled out of the ASGI application, which is
    exactly what task 3.7's raw-socket test caught before this existed.
    Either way `count_executions()` stays at zero: no streaming parse, no
    partial-parse path, nothing is written before this function returns a
    complete body.
    """
    buffer = bytearray()
    try:
        async for chunk in request.stream():
            buffer += chunk
            if len(buffer) > MAX_REPORT_BYTES:
                # THE LINE THAT PROTECTS: raised the moment the running
                # total crosses the cap, before the loop asks
                # `request.stream()` for another chunk -- the read stops
                # here, it does not continue and check afterwards.
                raise PayloadTooLargeError()
    except ClientDisconnect as exc:
        raise IncompleteBodyError() from exc
    return bytes(buffer)


@router.post("/runs")
async def create_run(request: Request) -> JSONResponse:
    _require_json_media_type(request)
    body = await _read_bounded_body(request)

    try:
        payload_dict = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidJsonError() from exc

    try:
        payload = SessionReport.model_validate(payload_dict)
    except ValidationError as exc:
        raise InvalidReportError.from_errors(exc.errors()) from exc

    store = request.app.state.store
    execution = _to_execution(payload.run, payload.vcs)
    reported_results = payload.results or []
    results = [_to_result(item) for item in reported_results]
    metadata = _to_run_metadata(payload.metadata)

    created = store.record_session(
        execution, results=results, received_at=datetime.now(timezone.utc), metadata=metadata
    )

    acknowledgement = Acknowledgement(
        run_id=payload.run.id,
        status="created" if created else "duplicate",
        ignored=_ignored_result_keys(reported_results),
    )
    return JSONResponse(
        status_code=201 if created else 200,
        content=acknowledgement.model_dump(),
    )


@router.post("/runs/{run_id}/heartbeat")
async def heartbeat(
    request: Request, run_id: str = Path(pattern=_IDENTITY_PATTERN)
) -> HeartbeatAcknowledgement:
    """Advance `run_id`'s last contact (design.md D33).

    The request body is `{}`, read by nothing -- the strongest form of "MUST
    NOT accept or apply `finished_at`, `exit_status`, `interrupted` or
    `interrupt_reason`" is that there is no field to send. `get_execution`,
    not `touch_last_contact`'s own boolean, decides the 404: a zero-rowcount
    update is ambiguous between "unknown run" and "a newer contact is
    already recorded", and only the former is a rejection.
    """
    store = request.app.state.store
    if store.get_execution(run_id) is None:
        raise UnknownRunError()

    store.touch_last_contact(run_id, datetime.now(timezone.utc))
    return HeartbeatAcknowledgement(run_id=run_id, status="acknowledged")
