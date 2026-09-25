"""`POST /api/v1/runs` -- session report ingestion -- and the run heartbeat.

**201 versus 200 comes from the boolean `record_session` returns**, which
the store decides inside its own write transaction. Asking the store whether
the id exists before calling it would reintroduce a check-then-act race at
the HTTP layer.

**The media type and size checks run before the body is buffered.** The
route does not declare `payload: SessionReport` as a parameter, because
FastAPI would then read and parse the whole body before the first line
runs. Instead:

1. `Content-Type` is checked from the header alone.
2. `_read_bounded_body` streams the body and stops the moment the running
   total exceeds `MAX_REPORT_BYTES`; `Content-Length` is never trusted. A
   client disconnect mid-transfer becomes `IncompleteBodyError`.
3. Only a complete, capped body is parsed as JSON and validated.

Nothing is written unless all three succeed.

**`results` is optional.** `None` (section absent) and `[]` both mean zero
result rows, not a rejection; the route always passes a list to
`record_session`.
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
"""Mirrors the SQL `CHECK` on `run_metadata_file.content_type` (schema.sql).
`MetadataFileReport.format` is unconstrained on the wire, so a file entry
with any other format -- which the schema cannot store -- is dropped here,
with its keys, before it reaches the store. A well-behaved plugin never
sends one."""

_MAX_DECLARED_PATH_CHARS = 1024
"""Mirrors `pytest_vantage.metadata.MAX_DECLARED_PATH_CHARS`. The two
distributions cannot import each other, so each carries its own copy."""


@overload
def _normalize_to_utc(value: datetime) -> datetime: ...
@overload
def _normalize_to_utc(value: None) -> None: ...
def _normalize_to_utc(value: datetime | None) -> datetime | None:
    """Normalize every timestamp that reaches the store to UTC.

    Stored timestamps are TEXT, and `test_case.last_seen_at` only moves
    forward via `MAX(...)` -- a lexicographic comparison, correct only when
    every string has the same offset. The plugin already sends UTC, but any
    HTTP client can report here, so the server does not trust the wire.

    An aware value converts with `astimezone(timezone.utc)`. A naive value is
    stamped as UTC with `replace()` -- `astimezone()` on a naive value would
    assume the server's local zone.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_vcs_context(vcs: VcsReport | None) -> VcsContext | None:
    """Normalise the `vcs` section: `None` when the section is absent or
    every field in it is null, so a session recorded outside a repository
    reads back as `execution.vcs is None`, never as a `VcsContext` full of
    nulls. `commit_subject` is truncated here, together with its flag.
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
    """Re-check a declared path's shape: short, relative, and free of `..`.

    The server cannot see the client's filesystem, so it cannot re-verify
    containment -- only the shape `pytest_vantage.metadata` also enforces.
    A path failing this is dropped, never rejected."""
    if len(path) > _MAX_DECLARED_PATH_CHARS:
        return False
    candidate = PurePath(path)
    if candidate.is_absolute() or candidate.drive or candidate.anchor:
        return False
    return ".." not in candidate.parts


def _to_run_metadata(metadata: MetadataReport | None) -> RunMetadata:
    """Normalise the `metadata` section; `EMPTY_RUN_METADATA` when absent.

    Every declared file becomes one `MetadataFile` row and each of its
    declared keys one `MetadataEntry` row, captured or not. Values are never
    truncated, and this function never raises, so bad metadata cannot block
    the run from being stored. A file whose path fails the shape re-check,
    or whose `status` or `format` this server cannot store, is dropped with
    all its keys; a well-behaved plugin never sends one.
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
            # The plugin's own status is trusted verbatim -- the server has
            # no way to verify it.
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
            # The server could not parse the document.
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

        # `metadata_parse.parse` already classified every declared key.
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
    """Normalise the failure evidence fields: `None` when every field is
    null or false.

    Every string field is truncated to the server's 64 KiB bound. Each
    stored flag is the client's flag OR the server's own: the client is the
    only side that knows a field was dropped for its report budget, and
    `truncate(None)` returns `(None, False)`, so assigning the server's flag
    alone would erase that.
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
    """Normalise captured stdout/stderr. Never `None`: `None` (never
    captured) versus `""` (captured, empty) lives in the fields, and
    `truncate()` preserves that distinction.
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
    `ResultReport`'s `extra="allow"`.

    One entry per key name in first-seen order, however many results carry
    it. Each name goes through `safe_segment`, because an unknown key is
    client-chosen text echoed back in the response.
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
    """Stream the body, aborting as soon as it exceeds the cap.

    `Content-Length` is not relied on: it can be absent, wrong or a lie.
    The check runs on every chunk received, so the buffer never grows much
    past `MAX_REPORT_BYTES`.

    A client that disconnects mid-body (a killed process, a dropped
    connection) surfaces as `ClientDisconnect` from `request.stream()`; it
    is converted to `IncompleteBodyError` so it takes the same rejection
    path as everything else instead of escaping the ASGI app unhandled.
    """
    buffer = bytearray()
    try:
        async for chunk in request.stream():
            buffer += chunk
            if len(buffer) > MAX_REPORT_BYTES:
                # Stop reading now; never ask the stream for another chunk.
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
    """Advance `run_id`'s last contact.

    The request body is never read, so a heartbeat cannot change a run's
    finish fields. `get_execution`, not `touch_last_contact`'s boolean,
    decides the 404: a no-op update means either "unknown run" or "a newer
    contact is already recorded", and only the former is a rejection.
    """
    store = request.app.state.store
    if store.get_execution(run_id) is None:
        raise UnknownRunError()

    store.touch_last_contact(run_id, datetime.now(timezone.utc))
    return HeartbeatAcknowledgement(run_id=run_id, status="acknowledged")
