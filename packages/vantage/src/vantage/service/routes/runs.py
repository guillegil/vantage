"""`POST /api/v1/runs` -- session report ingestion -- and the run heartbeat.

**201 versus 200 comes from the boolean `record_session` returns**, which
the store decides inside its own write transaction. Asking the store whether
the id exists before calling it would reintroduce a check-then-act race at
the HTTP layer.

**The media type and size checks run before the body is buffered.** The
route does not declare `payload: SessionReport` as a parameter, because
FastAPI would then read and parse the whole body before the first line
runs. `service/body.py` checks `Content-Type` from the header, streams the
body under `MAX_REPORT_BYTES`, and only then parses it. Nothing is written
unless all three succeed. Only the streaming is `async`: the parse,
validation, the conversion -- declared YAML metadata included -- and the
store write run in the threadpool, off the event loop every request shares.

**A lone surrogate is replaced, not rejected.** A `\\udXXX` escape with no
partner is valid JSON, and pytest produces such text itself from file names
decoded with `surrogateescape`. It cannot be encoded as UTF-8, so it would
fail the first encode on the way to storage; rejecting the report instead
would lose the whole session over one character. `_without_any_lone_surrogate`
replaces
every one, in keys and values alike, with U+FFFD before validation.

**`results` is optional.** `None` (section absent) and `[]` both mean zero
result rows, not a rejection; the route always passes a list to
`record_session`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, Depends, Path, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from vantage.core.domain.execution import IDENTITY_PATTERN, Execution, Identity, VcsContext
from vantage.core.domain.metadata import (
    FILE_STATUSES,
    MAX_METADATA_ENTRIES,
    MAX_METADATA_KEY_CHARS,
    MAX_METADATA_NAME_CHARS,
    MAX_METADATA_VALUE_BYTES,
    METADATA_CONTENT_TYPES,
    SESSION_KEY_STATUSES,
)
from vantage.core.domain.result import CapturedOutput, CaseIdentity, FailureEvidence, Result
from vantage.core.ports.storage import (
    EMPTY_RUN_METADATA,
    ExecutionStore,
    MetadataEntry,
    MetadataFile,
    RunMetadata,
)
from vantage.service import metadata_parse
from vantage.service.body import decode_json, read_bounded_body, require_json_media_type
from vantage.service.dependencies import get_store
from vantage.service.errors import (
    MAX_REPORT_BYTES,
    InvalidReportError,
    UnknownRunError,
    safe_segment,
)
from vantage.service.schemas import (
    Acknowledgement,
    HeartbeatAcknowledgement,
    MetadataReport,
    MetadataValueReport,
    ResultReport,
    RunReport,
    SessionReport,
    VcsReport,
)
from vantage.service.truncation import truncate

router = APIRouter()

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")

# The three bounds below mirror `pytest_vantage.metadata`. The two
# distributions cannot import each other, so each carries its own copy;
# `packages/pytest-vantage/tests/test_server_contract.py` pins them equal,
# with every other value the two sides share. The plugin never exceeds them,
# so they only ever bind on another HTTP client.

_MAX_DECLARED_PATH_CHARS = 1024
"""Mirrors `MAX_DECLARED_PATH_CHARS`."""

_MAX_DECLARED_FILE_BYTES = 8 * 1024
"""Mirrors `MAX_DECLARED_FILE_BYTES`. Composing YAML costs CPU per byte,
which a worker thread still takes from every other request, so a larger
document is recorded `too_large` without being parsed."""

_MAX_METADATA_SECTION_BYTES = 32 * 1024
"""Mirrors `MAX_METADATA_SECTION_BYTES`, bounding what one report makes the
server parse in total. The plugin spends it on JSON-encoded bytes, never
fewer than the UTF-8 bytes spent here, so a file it captured always fits."""


def _to_vcs_context(vcs: VcsReport | None) -> VcsContext | None:
    """Normalise the `vcs` section: `None` when the section is absent or
    `VcsContext.is_empty`, so a session recorded outside a repository reads
    back as `execution.vcs is None`, never as a `VcsContext` full of nulls.
    `commit_subject` is truncated here, together with its flag.
    """
    if vcs is None:
        return None
    commit_subject, commit_subject_truncated = truncate(vcs.commit_subject)
    context = VcsContext(
        commit=vcs.commit,
        branch=vcs.branch,
        commit_subject=commit_subject,
        commit_subject_truncated=commit_subject_truncated,
        dirty=vcs.dirty,
        root=vcs.root,
    )
    return None if context.is_empty() else context


def _declared_path_shape_is_valid(path: str) -> bool:
    """Re-check a declared path's shape: short, relative, and free of `..`.

    The server cannot see the client's filesystem, so it cannot re-verify
    containment -- only the shape `pytest_vantage.metadata` also enforces.
    Nor does it know the client's platform, so the path must pass both as a
    POSIX and as a Windows path, and a backslash -- a separator to one, a
    name character to the other -- is refused outright. A path failing this
    is dropped, never rejected."""
    if len(path) > _MAX_DECLARED_PATH_CHARS or "\\" in path:
        return False
    for candidate in (PurePosixPath(path), PureWindowsPath(path)):
        if candidate.is_absolute() or candidate.drive or candidate.anchor:
            return False
        if ".." in candidate.parts:
            return False
    return True


def _display_name(metadata: MetadataReport, key: str) -> str | None:
    """The display name `metadata`'s declaration gives `key`, or `None`: a
    name past `MAX_METADATA_NAME_CHARS` is dropped and the key kept."""
    declared = metadata.keys.get(key)
    if declared is None or declared.name is None:
        return None
    return declared.name if len(declared.name) <= MAX_METADATA_NAME_CHARS else None


def _session_value(report: MetadataValueReport) -> tuple[str | None, str]:
    """The `(value, status)` to store for a session value whose status is in
    `SESSION_KEY_STATUSES`. An entry whose value and status contradict each
    other stores no value, as `absent`; a captured value over
    `MAX_METADATA_VALUE_BYTES` is `value_too_large`, as a file's would be."""
    if (report.status == "captured") != (report.value is not None):
        return None, "absent"
    value = report.value
    if value is not None and len(value.encode("utf-8", "surrogatepass")) > MAX_METADATA_VALUE_BYTES:
        return None, "value_too_large"
    return value, report.status


def _to_run_metadata(metadata: MetadataReport | None) -> RunMetadata:
    """Normalise the `metadata` section; `EMPTY_RUN_METADATA` when absent.

    Every declared file becomes one `MetadataFile` row and each of its
    declared keys one `MetadataEntry` row, captured or not. Then each value
    the session reported becomes a session row, with no file. Every row
    carries the display name the declaration gives its key, and whether the
    declaration names the key at all, in `keys` or in any file's `keys`.
    Values are never truncated, and this function never raises, so bad
    metadata cannot block the run from being stored.

    A file whose path fails the shape re-check, repeats an earlier file's
    path, or has a `status` or `format` this server cannot store is dropped
    with all its keys; a well-behaved plugin never sends one. A repeated
    path is dropped rather than merged because the store keeps one file row
    per path, and the later file's keys would be stored under a status that
    describes a different document.

    The plugin's bounds are applied again, by dropping rather than
    rejecting: a key over `MAX_METADATA_KEY_CHARS`, a key already produced
    by an earlier file or value -- so a file's value beats the session's --
    and every key past `MAX_METADATA_ENTRIES`, files first, are left out; a
    session value with a status outside `SESSION_KEY_STATUSES` is left out
    too. A captured document over the per-file bound or past the section
    budget is recorded `too_large` or `over_budget` without being parsed.
    """
    if metadata is None:
        return EMPTY_RUN_METADATA

    declared_keys = set(metadata.keys).union(*(report.keys for report in metadata.files))
    files: list[MetadataFile] = []
    entries: list[MetadataEntry] = []
    accepted_paths: set[str] = set()
    accepted_keys: set[str] = set()
    remaining_budget = _MAX_METADATA_SECTION_BYTES
    budget_exhausted = False

    for file_report in metadata.files:
        if (
            file_report.path in accepted_paths
            or not _declared_path_shape_is_valid(file_report.path)
            or file_report.format not in METADATA_CONTENT_TYPES
            or file_report.status not in FILE_STATUSES
        ):
            continue
        accepted_paths.add(file_report.path)

        keys: list[str] = []
        for key in file_report.keys:
            if (
                len(key) <= MAX_METADATA_KEY_CHARS
                and key not in accepted_keys
                and len(accepted_keys) < MAX_METADATA_ENTRIES
            ):
                accepted_keys.add(key)
                keys.append(key)

        # A status other than `captured` is the plugin's own and is trusted
        # verbatim -- the server has no way to verify it.
        status = file_report.status
        parsed: dict[str, metadata_parse.KeyResult] | None = None
        if status == "captured":
            content = file_report.content
            size = 0 if content is None else len(content.encode("utf-8", "surrogatepass"))
            if content is None:
                # Nothing to parse; `captured` would contradict every key.
                status = "malformed"
            elif size > _MAX_DECLARED_FILE_BYTES:
                status = "too_large"
            elif budget_exhausted or size > remaining_budget:
                # Spent in declaration order, as the plugin spends it.
                status = "over_budget"
                budget_exhausted = True
            else:
                remaining_budget -= size
                parsed = metadata_parse.parse(content, file_report.format, keys)
                if parsed is None:
                    status = "malformed"

        files.append(
            MetadataFile(
                source_file=file_report.path, content_type=file_report.format, status=status
            )
        )
        outcomes = (
            {key: metadata_parse.KeyResult(status="source_unavailable", value=None) for key in keys}
            if parsed is None
            else parsed
        )
        entries.extend(
            MetadataEntry(
                key=key,
                value=outcome.value,
                source_file=file_report.path,
                status=outcome.status,
                name=_display_name(metadata, key),
            )
            for key, outcome in outcomes.items()
        )

    for value_report in metadata.values:
        key = value_report.key
        if (
            len(key) > MAX_METADATA_KEY_CHARS
            or value_report.status not in SESSION_KEY_STATUSES
            or key in accepted_keys
            or len(accepted_keys) >= MAX_METADATA_ENTRIES
        ):
            continue
        accepted_keys.add(key)
        value, status = _session_value(value_report)
        entries.append(
            MetadataEntry(
                key=key,
                value=value,
                source_file=None,
                status=status,
                source="session",
                name=_display_name(metadata, key),
                declared=key in declared_keys,
            )
        )

    return RunMetadata(files=tuple(files), entries=tuple(entries))


def _to_execution(run: RunReport, vcs: VcsReport | None) -> Execution:
    # The plugin sends at most a short line here, so the bound only binds on
    # another client, and no column records the cut.
    interrupt_reason, _ = truncate(run.interrupt_reason)
    return Execution(
        identity=Identity(run.id),
        started_at=run.started_at,
        finished_at=run.finished_at,
        exit_status=run.exit_status,
        interrupted=run.interrupted,
        interrupt_reason=interrupt_reason,
        vcs=_to_vcs_context(vcs),
    )


def _to_failure_evidence(item: ResultReport) -> FailureEvidence | None:
    """Normalise the failure evidence fields: `None` when
    `FailureEvidence.is_empty`.

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

    failure = FailureEvidence(
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
    return None if failure.is_empty() else failure


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
        started_at=item.started_at,
        finished_at=item.finished_at,
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


def _without_lone_surrogates(text: str) -> str:
    return text if text.isascii() else _LONE_SURROGATE.sub("\ufffd", text)


def _without_any_lone_surrogate(payload: Any) -> Any:
    """`payload`, parsed JSON, with every lone surrogate in its keys and
    string values replaced by U+FFFD.

    The walk is iterative because `json.loads` accepts nesting deeper than
    Python's recursion limit. Valid surrogate pairs were already combined by
    `json.loads`, so any surrogate left is a lone one.
    """
    if isinstance(payload, str):
        return _without_lone_surrogates(payload)
    pending: list[Any] = [payload]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if not all(key.isascii() for key in node):
                cleaned = {_without_lone_surrogates(key): value for key, value in node.items()}
                node.clear()
                node.update(cleaned)
            for key, value in node.items():
                if isinstance(value, str):
                    node[key] = _without_lone_surrogates(value)
                elif isinstance(value, (dict, list)):
                    pending.append(value)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                if isinstance(value, str):
                    node[index] = _without_lone_surrogates(value)
                elif isinstance(value, (dict, list)):
                    pending.append(value)
    return payload


def _record(store: ExecutionStore, body: bytes) -> tuple[bool, Acknowledgement]:
    """Parse, validate, convert and store one complete report body, and
    return whether the run was new with the acknowledgement to send. Every
    step blocks, so `create_run` calls this in the threadpool."""
    payload_dict = _without_any_lone_surrogate(decode_json(body))

    try:
        payload = SessionReport.model_validate(payload_dict)
    except ValidationError as exc:
        raise InvalidReportError.from_errors(exc.errors()) from exc

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
    return created, acknowledgement


@router.post("/runs")
async def create_run(request: Request, store: ExecutionStore = Depends(get_store)) -> JSONResponse:
    require_json_media_type(request)
    body = await read_bounded_body(request, MAX_REPORT_BYTES)
    created, acknowledgement = await run_in_threadpool(_record, store, body)
    return JSONResponse(
        status_code=201 if created else 200,
        content=acknowledgement.model_dump(),
    )


@router.post("/runs/{run_id}/heartbeat")
def heartbeat(
    run_id: str = Path(pattern=IDENTITY_PATTERN), store: ExecutionStore = Depends(get_store)
) -> HeartbeatAcknowledgement:
    """Advance `run_id`'s last contact.

    The request body is never read, so a heartbeat cannot change a run's
    finish fields. `get_execution`, not `touch_last_contact`'s boolean,
    decides the 404: a no-op update means either "unknown run" or "a newer
    contact is already recorded", and only the former is a rejection.
    """
    if store.get_execution(run_id) is None:
        raise UnknownRunError()

    store.touch_last_contact(run_id, datetime.now(timezone.utc))
    return HeartbeatAcknowledgement(run_id=run_id, status="acknowledged")
