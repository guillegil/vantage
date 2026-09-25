"""SQLite adapter for `ExecutionStore`.

A start-write and a finish-write share one run `id`, so the run row is written
by `INSERT ... ON CONFLICT(id) DO UPDATE ... WHERE run.exit_status IS NULL AND
excluded.exit_status IS NOT NULL`: a finish applies over a start-only row,
never the reverse. `exit_status` rather than `finished_at` is the
discriminator because a Ctrl-C session reports an exit status with a null
finish time. `received_at` and `started_at` never change on the conflict path.

Under `DO UPDATE`, `cursor.rowcount` cannot tell an insert from an applied
update, so `created` comes from a `SELECT 1 FROM run WHERE id = ?` probe run
right after `BEGIN IMMEDIATE`. The transaction already holds the `RESERVED`
lock and `self._lock`, so no other write can land between probe and upsert.

Concurrency needs two layers. `self._lock` is held across every statement
and transaction, reads included: every thread shares one connection, and a
read issued while another thread is inside a write transaction would run
inside it and see rows that are not committed yet, or never will be. WAL mode
and the connection's busy timeout cover a second process on the same file,
which no in-process lock can reach. Multi-statement writes start with `BEGIN
IMMEDIATE` because a deferred transaction that upgrades to a write
mid-statement is the classic two-writer deadlock.

A session's run, catalogue, result and metadata rows are written in one
transaction, without `RETURNING`: it needs SQLite >= 3.35, newer than some
Python 3.10 builds link against. The catalogue
advances `last_seen_at` with `MAX`, so a late report with an older
`started_at` cannot move it backwards, and the result insert is `ON
CONFLICT(run_id, node_id) DO NOTHING`, so a replayed report is a silent
no-op.

`last_contact_at` is set by the creating report only -- a finished or
interrupted run is done, not stale -- and advanced by `touch_last_contact`'s
monotonic `last_contact_at < ?` update. That comparison is on TEXT, so every
value written to the column goes through `_fixed_width_isoformat`.
`test_case.last_seen_at`'s `MAX` and the `started_at` ordering compare plain
`.isoformat()` text and rely on the service normalising timestamps to UTC.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar, cast

from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.projection import (
    LIST_COMMIT_SUBJECT_CHARS,
    LIST_FAILURE_MESSAGE_CHARS,
)
from vantage.core.domain.result import (
    CapturedOutput,
    CaseIdentity,
    CatalogueEntry,
    FailureEvidence,
    Result,
)
from vantage.core.ports.storage import (
    EMPTY_RUN_METADATA,
    MAX_PAGE_ITEMS,
    HistoryEntry,
    Page,
    ResultListEntry,
    RunDetail,
    RunListEntry,
    RunMetadata,
    UserSetting,
)
from vantage.storage.connection import open_database

T = TypeVar("T")

# SQLITE_MAX_VARIABLE_NUMBER is 999 on older SQLite builds; 500 leaves
# headroom without needing to introspect the running library's compile-time
# limit.
_MAX_PLACEHOLDERS = 500

# `last_contact_at` is set on the insert branch only; the `DO UPDATE SET`
# list never names it. The `vcs_*` columns update under the same
# `exit_status` guard, each through `COALESCE(excluded, run)`, so a report
# without VCS data never nulls a value an earlier report recorded.
# `vcs_commit_subject_truncated` follows whichever subject is kept, so the
# flag always describes the stored subject.
_UPSERT_RUN = """
    INSERT INTO run (
        id, received_at, last_contact_at, started_at, finished_at,
        exit_status, interrupted, interrupt_reason,
        vcs_commit, vcs_branch, vcs_commit_subject, vcs_commit_subject_truncated,
        vcs_dirty, vcs_root
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        finished_at      = excluded.finished_at,
        exit_status      = excluded.exit_status,
        interrupted      = excluded.interrupted,
        interrupt_reason = excluded.interrupt_reason,
        vcs_commit         = COALESCE(excluded.vcs_commit,         run.vcs_commit),
        vcs_branch         = COALESCE(excluded.vcs_branch,         run.vcs_branch),
        vcs_commit_subject = COALESCE(excluded.vcs_commit_subject, run.vcs_commit_subject),
        vcs_dirty          = COALESCE(excluded.vcs_dirty,          run.vcs_dirty),
        vcs_root           = COALESCE(excluded.vcs_root,           run.vcs_root),
        vcs_commit_subject_truncated =
            CASE WHEN excluded.vcs_commit_subject IS NOT NULL
                 THEN excluded.vcs_commit_subject_truncated
                 ELSE run.vcs_commit_subject_truncated END
     WHERE run.exit_status IS NULL AND excluded.exit_status IS NOT NULL
"""

_PROBE_RUN_EXISTS = "SELECT 1 FROM run WHERE id = ?"

# Monotonic: a `contacted_at` earlier than or equal to the stored one changes
# zero rows.
_TOUCH_LAST_CONTACT = """
    UPDATE run
       SET last_contact_at = ?
     WHERE id = ?
       AND (last_contact_at IS NULL OR last_contact_at < ?)
"""

# The statements below are composed from these column lists so that a query
# and its twin cannot drift apart. Only the module's own constants are ever
# interpolated -- never a caller's value -- so the S608 findings on the
# composed `SELECT` bases are not the injection pattern the rule looks for.

# `_decode_execution`'s twelve columns, in its order.
_EXECUTION_COLUMNS = """
    run.id, run.started_at, run.finished_at, run.exit_status, run.interrupted,
    run.interrupt_reason,
    run.vcs_commit, run.vcs_branch, run.vcs_commit_subject,
    run.vcs_commit_subject_truncated, run.vcs_dirty, run.vcs_root
"""

# `_EXECUTION_COLUMNS` for a list: only a prefix of the commit subject is
# loaded, so a 64 KiB subject is never read only to be sliced. The row still
# decodes as an `Execution`, and `project_vcs` applies the list rule to it.
# Binds `_LIST_SUBJECT_PREFIX_BYTES`.
_LIST_EXECUTION_COLUMNS = """
    run.id, run.started_at, run.finished_at, run.exit_status, run.interrupted,
    run.interrupt_reason,
    run.vcs_commit, run.vcs_branch, substr(CAST(run.vcs_commit_subject AS BLOB), 1, ?),
    run.vcs_commit_subject_truncated, run.vcs_dirty, run.vcs_root
"""

# How much of a bounded list column to load. The prefix is taken in bytes,
# from the value cast to a BLOB, because SQLite's text `substr` stops at the
# first NUL and would hand back a short prefix that looks complete. UTF-8
# spends at most four bytes on a character, so this many bytes always hold
# the first width + 1 characters whole -- one past the display width, which
# is what `project_vcs` and `project_failure` need to tell a longer value
# from one that fits.
_LIST_SUBJECT_PREFIX_BYTES = 4 * (LIST_COMMIT_SUBJECT_CHARS + 1)
_LIST_MESSAGE_PREFIX_BYTES = 4 * (LIST_FAILURE_MESSAGE_CHARS + 1)

# `get_execution` and `get_run_detail` share one statement; `last_contact_at`
# comes last so the first twelve values decode as an `Execution`.
_SELECT_RUN = f"""
    SELECT {_EXECUTION_COLUMNS}, run.last_contact_at FROM run WHERE run.id = ?
"""  # noqa: S608

_SELECT_RUN_LIST = f"""
    SELECT {_LIST_EXECUTION_COLUMNS}, run.last_contact_at FROM run
"""  # noqa: S608

_LIST_RUNS = f"""
    {_SELECT_RUN_LIST}
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT ? OFFSET ?
"""

# `_LIST_RUNS` filtered to runs holding one `(key, value)` metadata pair.
#
# `id IN (subquery)`, not a correlated `EXISTS`: with `EXISTS`, SQLite anchors
# on `rm.run_id = run.id` and probes the primary-key autoindex once per `run`
# row, so cost grows with the total run count. The uncorrelated `IN` seeks
# `idx_run_metadata_key_value` once, then looks each `run` up by primary key.
# `value` is NULL for any non-captured row, and NULL never equals a bound
# string, so a declared-but-dropped entry never matches.
# `test_list_runs_by_metadata_uses_the_key_value_index` pins the plan.
_LIST_RUNS_BY_METADATA = f"""
    {_SELECT_RUN_LIST}
    WHERE run.id IN (
        SELECT rm.run_id FROM run_metadata rm WHERE rm.key = ? AND rm.value = ?
    )
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT ? OFFSET ?
"""  # noqa: S608

# How many runs started before `key` was first declared, as one statement so
# it reads one state of the database. `first_seen` is the earliest
# `started_at` among runs holding any `run_metadata` row for `key`, whatever
# its status, found through `idx_run_metadata_key_value`; the count is served
# by `idx_run_started_at`. A key never declared has no `first_seen`, and
# every run predates it.
_COUNT_RUNS_PREDATING_KEY = """
    SELECT CASE WHEN first_seen.started_at IS NULL
                THEN (SELECT COUNT(*) FROM run)
                ELSE (SELECT COUNT(*) FROM run WHERE run.started_at < first_seen.started_at)
           END
    FROM (
        SELECT MIN(run.started_at) AS started_at
        FROM run_metadata rm
        JOIN run ON run.id = rm.run_id
        WHERE rm.key = ?
    ) AS first_seen
"""

# Conflict target is `node_id`, the catalogue's identity key.
_UPSERT_TEST_CASE = """
    INSERT INTO test_case (
        node_id, file_path, class_name, function_name,
        param_id, first_seen_at, last_seen_at, last_seen_run_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(node_id) DO UPDATE SET
        file_path        = excluded.file_path,
        class_name       = excluded.class_name,
        function_name    = excluded.function_name,
        param_id         = excluded.param_id,
        last_seen_run_id = CASE WHEN excluded.last_seen_at > test_case.last_seen_at
                                THEN excluded.last_seen_run_id ELSE test_case.last_seen_run_id END,
        last_seen_at     = MAX(test_case.last_seen_at, excluded.last_seen_at)
"""

# The thirteen `FailureEvidence` columns and the four `CapturedOutput`
# columns follow `worker_id`, in the order `_result_rows` builds them.
_INSERT_RESULT = """
    INSERT INTO result (
        run_id, test_case_id, node_id, outcome, duration, started_at, finished_at,
        setup_outcome, call_outcome, teardown_outcome,
        setup_duration, call_duration, teardown_duration, worker_id,
        failure_type, failure_message, failure_message_truncated,
        failure_path, failure_lineno,
        failure_repr, failure_repr_truncated,
        traceback, traceback_truncated,
        skip_reason, skip_reason_truncated,
        xfail_reason, xfail_reason_truncated,
        captured_stdout, captured_stdout_truncated,
        captured_stderr, captured_stderr_truncated
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
    ON CONFLICT(run_id, node_id) DO NOTHING
"""

# `INSERT OR IGNORE` on the primary key makes metadata write-once: a replay,
# even one carrying different values, never changes a stored row.
_INSERT_METADATA_FILE = """
    INSERT OR IGNORE INTO run_metadata_file (run_id, source_file, content_type, status)
    VALUES (?, ?, ?, ?)
"""

_INSERT_METADATA_ENTRY = """
    INSERT OR IGNORE INTO run_metadata (run_id, key, value, source_file, status)
    VALUES (?, ?, ?, ?, ?)
"""

# `_decode_identity`'s five columns and the eleven outcome and timing
# columns every result read starts with.
_RESULT_COLUMNS = """
    r.node_id, tc.file_path, tc.class_name, tc.function_name, tc.param_id,
    r.outcome, r.duration, r.started_at, r.finished_at,
    r.setup_outcome, r.call_outcome, r.teardown_outcome,
    r.setup_duration, r.call_duration, r.teardown_duration, r.worker_id
"""

# `_decode_failure`'s thirteen columns, in `FailureEvidence`'s field order.
_FAILURE_COLUMNS = """
    r.failure_type, r.failure_message, r.failure_message_truncated,
    r.failure_path, r.failure_lineno,
    r.failure_repr, r.failure_repr_truncated,
    r.traceback, r.traceback_truncated,
    r.skip_reason, r.skip_reason_truncated,
    r.xfail_reason, r.xfail_reason_truncated
"""

# `_decode_captured`'s four columns.
_CAPTURED_COLUMNS = """
    r.captured_stdout, r.captured_stdout_truncated,
    r.captured_stderr, r.captured_stderr_truncated
"""

# The full, unbounded record `get_results` and `get_result` return: every
# failure and captured-output column, unlike the lean `_LIST_RESULTS` below.
_SELECT_FULL_RESULT = f"""
    SELECT {_RESULT_COLUMNS}, {_FAILURE_COLUMNS}, {_CAPTURED_COLUMNS}
    FROM result r
    JOIN test_case tc ON tc.id = r.test_case_id
"""  # noqa: S608

_SELECT_RESULTS_FOR_RUN = f"{_SELECT_FULL_RESULT} WHERE r.run_id = ? ORDER BY r.id"

_SELECT_RESULT = f"{_SELECT_FULL_RESULT} WHERE r.run_id = ? AND r.node_id = ?"

# `list_results`' SELECT -- the paginated, lean sibling of
# `_SELECT_RESULTS_FOR_RUN`, in `_FAILURE_COLUMNS`' shape so the row decodes
# as a `Result` for `project_failure`. `failure_message` is a byte prefix,
# as in `_LIST_EXECUTION_COLUMNS`. `failure_repr` and `traceback` are never
# loaded: `substr(x, 1, 0)` is '' for a stored value and NULL for none, which
# is all the emptiness rule needs of them. No captured-output column is
# selected. Binds `_LIST_MESSAGE_PREFIX_BYTES`.
_LIST_RESULTS = f"""
    SELECT {_RESULT_COLUMNS},
           r.failure_type, substr(CAST(r.failure_message AS BLOB), 1, ?),
           r.failure_message_truncated,
           r.failure_path, r.failure_lineno,
           substr(r.failure_repr, 1, 0), r.failure_repr_truncated,
           substr(r.traceback, 1, 0), r.traceback_truncated,
           r.skip_reason, r.skip_reason_truncated,
           r.xfail_reason, r.xfail_reason_truncated
    FROM result r
    JOIN test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = ?
    ORDER BY r.id
    LIMIT ? OFFSET ?
"""  # noqa: S608

# `list_history`' SELECT: `node_id` resolves through the unique
# `idx_test_case_node_id` to one `test_case.id`, then
# `idx_result_test_case_id` finds that test's results, then `run` is read by
# primary key. Same execution columns and total order as `_LIST_RUNS`.
_LIST_HISTORY = f"""
    SELECT {_LIST_EXECUTION_COLUMNS}, run.last_contact_at, r.outcome, r.duration
    FROM test_case tc
    JOIN result r ON r.test_case_id = tc.id
    JOIN run ON run.id = r.run_id
    WHERE tc.node_id = ?
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT ? OFFSET ?
"""  # noqa: S608

_SELECT_TEST_CASE = """
    SELECT node_id, file_path, class_name, function_name, param_id,
           first_seen_at, last_seen_at, last_seen_run_id
    FROM test_case WHERE node_id = ?
"""

# Ordered by `key`, i.e. alphabetically by section name.
_LIST_SETTINGS = """
    SELECT namespace, key, value, updated_at
    FROM user_setting WHERE namespace = ? ORDER BY key
"""

_PROBE_SETTING_EXISTS = "SELECT 1 FROM user_setting WHERE namespace = ? AND key = ?"

_UPSERT_SETTING = """
    INSERT INTO user_setting (namespace, key, value, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT (namespace, key) DO UPDATE SET
        value = excluded.value,
        updated_at = excluded.updated_at
"""

# `_DELETE_SETTING`: `rowcount == 1` is the "it existed" answer.
_DELETE_SETTING = "DELETE FROM user_setting WHERE namespace = ? AND key = ?"

# The per-run aggregate read. It filters on `result.run_id` and joins
# `test_case` by primary key; `file_path` is only read, so it needs no index.
_SELECT_RUN_CASE_OUTCOMES = """
    SELECT tc.file_path, r.outcome
    FROM result r
    JOIN test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = ?
"""


def _fixed_width_isoformat(moment: datetime) -> str:
    """Fixed-width ISO-8601 UTC text: `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`.

    The same format as `pytest_vantage.recorder.isoformat_utc`. Converts to
    UTC first rather than trusting the caller: `strftime` alone would stamp a
    `+02:00` value with `+00:00`, storing it two hours off and breaking the
    text comparison against UTC rows (the in-memory adapter compares real
    `datetime` objects and would disagree).

    Used for `last_contact_at`, the column compared as text (`< ?`), and for
    `user_setting.updated_at`; other timestamp columns use `.isoformat()`.
    """
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _vcs_columns(vcs: VcsContext | None) -> tuple[object, ...]:
    """The six `vcs_*` `_UPSERT_RUN` parameters.

    `vcs_dirty` is written as `1`, `0` or `NULL`, never `0` for "unknown",
    which would have a run recorded outside a repository claim a clean
    working tree.
    """
    if vcs is None:
        return (None, None, None, 0, None, None)
    return (
        vcs.commit,
        vcs.branch,
        vcs.commit_subject,
        1 if vcs.commit_subject_truncated else 0,
        None if vcs.dirty is None else (1 if vcs.dirty else 0),
        vcs.root,
    )


def _datetime(value: object) -> datetime:
    """A `NOT NULL` timestamp column. `schema.sql` guarantees the value, so a
    `cast` documents it without an assert statement (S101)."""
    return datetime.fromisoformat(cast(str, value))


def _opt_datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _text(value: object) -> str | None:
    """A nullable text column, or the UTF-8 byte prefix a list query loads
    in its place. Decoding the prefix drops only the one character it may
    have cut in half; stored text is always valid UTF-8."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return cast("str | None", value)


def _decode_identity(row: Sequence[object]) -> CaseIdentity:
    node_id, file_path, class_name, function_name, param_id = row
    return CaseIdentity(
        node_id=cast(str, node_id),
        file_path=cast(str, file_path),
        class_name=cast("str | None", class_name),
        function_name=cast(str, function_name),
        param_id=cast("str | None", param_id),
    )


def _decode_vcs(row: Sequence[object]) -> VcsContext | None:
    """The six `vcs_*` columns; `None` for a run recorded outside a
    repository, by `VcsContext.is_empty`."""
    commit, branch, commit_subject, commit_subject_truncated, dirty, root = row
    vcs = VcsContext(
        commit=cast("str | None", commit),
        branch=cast("str | None", branch),
        commit_subject=_text(commit_subject),
        commit_subject_truncated=bool(commit_subject_truncated),
        dirty=None if dirty is None else bool(dirty),
        root=cast("str | None", root),
    )
    return None if vcs.is_empty() else vcs


def _decode_execution(row: Sequence[object]) -> Execution:
    """The twelve `_EXECUTION_COLUMNS` or `_LIST_EXECUTION_COLUMNS`."""
    identity_value, started_at, finished_at, exit_status, interrupted, interrupt_reason = row[:6]
    return Execution(
        identity=Identity(cast(str, identity_value)),
        started_at=_datetime(started_at),
        finished_at=_opt_datetime(finished_at),
        exit_status=exit_status if isinstance(exit_status, int) else None,
        interrupted=bool(interrupted),
        interrupt_reason=interrupt_reason if isinstance(interrupt_reason, str) else None,
        vcs=_decode_vcs(row[6:12]),
    )


def _row_to_run_list_entry(row: tuple[object, ...]) -> RunListEntry:
    return RunListEntry.from_execution(
        _decode_execution(row[:12]), last_contact_at=_opt_datetime(row[12])
    )


def _row_to_history_entry(row: tuple[object, ...]) -> HistoryEntry:
    last_contact_at, outcome, duration = row[12:]
    return HistoryEntry.from_execution(
        _decode_execution(row[:12]),
        last_contact_at=_opt_datetime(last_contact_at),
        outcome=cast(str, outcome),
        duration=cast("float | None", duration),
    )


def _decode_failure(row: Sequence[object]) -> FailureEvidence | None:
    """The thirteen `_FAILURE_COLUMNS`; `None` for a result without
    evidence, by `FailureEvidence.is_empty`."""
    (
        failure_type,
        failure_message,
        failure_message_truncated,
        failure_path,
        failure_lineno,
        failure_repr,
        failure_repr_truncated,
        traceback,
        traceback_truncated,
        skip_reason,
        skip_reason_truncated,
        xfail_reason,
        xfail_reason_truncated,
    ) = row
    failure = FailureEvidence(
        failure_type=cast("str | None", failure_type),
        failure_message=_text(failure_message),
        failure_message_truncated=bool(failure_message_truncated),
        failure_path=cast("str | None", failure_path),
        failure_lineno=cast("int | None", failure_lineno),
        failure_repr=cast("str | None", failure_repr),
        failure_repr_truncated=bool(failure_repr_truncated),
        traceback=cast("str | None", traceback),
        traceback_truncated=bool(traceback_truncated),
        skip_reason=cast("str | None", skip_reason),
        skip_reason_truncated=bool(skip_reason_truncated),
        xfail_reason=cast("str | None", xfail_reason),
        xfail_reason_truncated=bool(xfail_reason_truncated),
    )
    return None if failure.is_empty() else failure


def _decode_captured(row: Sequence[object]) -> CapturedOutput:
    """Never `None`. Reads the four columns straight through, so a stored
    `''` reads back as `''`, never coerced to `None`."""
    stdout, stdout_truncated, stderr, stderr_truncated = row
    return CapturedOutput(
        stdout=cast("str | None", stdout),
        stdout_truncated=bool(stdout_truncated),
        stderr=cast("str | None", stderr),
        stderr_truncated=bool(stderr_truncated),
    )


def _decode_result(row: Sequence[object], failure: FailureEvidence | None) -> Result:
    """The sixteen `_RESULT_COLUMNS`, with the failure evidence decoded by
    the caller."""
    (
        outcome,
        duration,
        started_at,
        finished_at,
        setup_outcome,
        call_outcome,
        teardown_outcome,
        setup_duration,
        call_duration,
        teardown_duration,
        worker_id,
    ) = row[5:16]
    return Result(
        identity=_decode_identity(row[:5]),
        outcome=cast(str, outcome),
        duration=cast("float | None", duration),
        started_at=_opt_datetime(started_at),
        finished_at=_opt_datetime(finished_at),
        setup_outcome=cast("str | None", setup_outcome),
        call_outcome=cast("str | None", call_outcome),
        teardown_outcome=cast("str | None", teardown_outcome),
        setup_duration=cast("float | None", setup_duration),
        call_duration=cast("float | None", call_duration),
        teardown_duration=cast("float | None", teardown_duration),
        worker_id=cast("str | None", worker_id),
        failure=failure,
    )


def _row_to_result(row: tuple[object, ...]) -> Result:
    """A `_SELECT_FULL_RESULT` row."""
    return replace(
        _decode_result(row[:16], _decode_failure(row[16:29])),
        captured=_decode_captured(row[29:33]),
    )


def _row_to_result_list_entry(row: tuple[object, ...]) -> ResultListEntry:
    """A `_LIST_RESULTS` row."""
    return ResultListEntry.from_result(_decode_result(row[:16], _decode_failure(row[16:29])))


def _row_to_catalogue_entry(row: tuple[object, ...]) -> CatalogueEntry:
    first_seen_at, last_seen_at, last_seen_run_id = row[5:]
    return CatalogueEntry(
        identity=_decode_identity(row[:5]),
        first_seen_at=_datetime(first_seen_at),
        last_seen_at=_datetime(last_seen_at),
        last_seen_run_id=cast("str | None", last_seen_run_id),
    )


def _catalogue_rows(
    execution: Execution, results: Sequence[Result]
) -> list[tuple[str, str, str | None, str, str | None, str, str, str]]:
    started_at = execution.started_at.isoformat()
    run_id = execution.identity.value
    # Keyed by node_id so a report carrying the same node id twice (the
    # service rejects that) still yields one upsert row rather than a batch
    # containing a duplicate key.
    by_node_id: dict[str, CaseIdentity] = {
        result.identity.node_id: result.identity for result in results
    }
    return [
        (
            identity.node_id,
            identity.file_path,
            identity.class_name,
            identity.function_name,
            identity.param_id,
            started_at,  # first_seen_at -- only used on INSERT, ignored on conflict
            started_at,  # last_seen_at -- the MAX/CASE clause decides on conflict
            run_id,  # last_seen_run_id
        )
        for identity in by_node_id.values()
    ]


def _metadata_file_rows(run_id: str, metadata: RunMetadata) -> list[tuple[str, str, str, str]]:
    return [(run_id, file.source_file, file.content_type, file.status) for file in metadata.files]


def _metadata_entry_rows(
    run_id: str, metadata: RunMetadata
) -> list[tuple[str, str, str | None, str, str]]:
    return [
        (run_id, entry.key, entry.value, entry.source_file, entry.status)
        for entry in metadata.entries
    ]


def _failure_columns(failure: FailureEvidence | None) -> tuple[object, ...]:
    """The thirteen `FailureEvidence` `_INSERT_RESULT` parameters. `None`
    writes every column NULL/0 -- no failure evidence at all, not a record
    whose fields all happen to be null."""
    if failure is None:
        return (None, None, 0, None, None, None, 0, None, 0, None, 0, None, 0)
    return (
        failure.failure_type,
        failure.failure_message,
        1 if failure.failure_message_truncated else 0,
        failure.failure_path,
        failure.failure_lineno,
        failure.failure_repr,
        1 if failure.failure_repr_truncated else 0,
        failure.traceback,
        1 if failure.traceback_truncated else 0,
        failure.skip_reason,
        1 if failure.skip_reason_truncated else 0,
        failure.xfail_reason,
        1 if failure.xfail_reason_truncated else 0,
    )


def _captured_columns(captured: CapturedOutput) -> tuple[object, ...]:
    """The four `CapturedOutput` `_INSERT_RESULT` parameters.
    `captured.stdout`/`.stderr` are written through unchanged -- never
    `value or None` or any other truthy check, which would collapse `""`
    (captured, empty) into SQL `NULL` (never captured)."""
    return (
        captured.stdout,
        1 if captured.stdout_truncated else 0,
        captured.stderr,
        1 if captured.stderr_truncated else 0,
    )


def _result_rows(
    execution: Execution, results: Sequence[Result], test_case_ids: dict[str, int]
) -> list[tuple[object, ...]]:
    run_id = execution.identity.value
    return [
        (
            run_id,
            test_case_ids[result.identity.node_id],
            result.identity.node_id,
            result.outcome,
            result.duration,
            result.started_at.isoformat() if result.started_at is not None else None,
            result.finished_at.isoformat() if result.finished_at is not None else None,
            result.setup_outcome,
            result.call_outcome,
            result.teardown_outcome,
            result.setup_duration,
            result.call_duration,
            result.teardown_duration,
            result.worker_id,
            *_failure_columns(result.failure),
            *_captured_columns(result.captured),
        )
        for result in results
    ]


def _resolve_test_case_ids(conn: sqlite3.Connection, node_ids: Sequence[str]) -> dict[str, int]:
    resolved: dict[str, int] = {}
    for start in range(0, len(node_ids), _MAX_PLACEHOLDERS):
        batch = node_ids[start : start + _MAX_PLACEHOLDERS]
        # `placeholders` is built only from the literal `?` marker, repeated
        # once per batch item -- no value is ever interpolated into the SQL
        # text itself, so this is not the injection pattern S608 flags.
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT id, node_id FROM test_case WHERE node_id IN ({placeholders})",  # noqa: S608
            batch,
        ).fetchall()
        for row_id, row_node_id in rows:
            resolved[cast(str, row_node_id)] = cast(int, row_id)
    return resolved


def _row_to_user_setting(row: tuple[object, ...]) -> UserSetting:
    namespace, key, value, updated_at = row
    return UserSetting(
        namespace=cast(str, namespace),
        key=cast(str, key),
        value=cast(str, value),
        updated_at=_datetime(updated_at),
    )


def _page(
    rows: Sequence[tuple[object, ...]], page_limit: int, decode: Callable[[tuple[object, ...]], T]
) -> Page[T]:
    """A page from a query that fetched one row past `page_limit`: that
    row's presence distinguishes a truncated page from an exhausted one
    without a second `COUNT` query that could race the first."""
    return Page(
        items=tuple(decode(row) for row in rows[:page_limit]), has_more=len(rows) > page_limit
    )


class SqliteExecutionStore:
    """Implements `vantage.core.ports.storage.ExecutionStore` against SQLite.

    One connection per process, shared across threads
    (`check_same_thread=False`) and opened via `open_database`, which sets
    file permissions and WAL. Every read and every write transaction holds
    `self._lock` to serialise this process's threads; `BEGIN IMMEDIATE` and
    the busy timeout handle a second process sharing the file. Neither
    substitutes for the other.
    """

    def __init__(self, path: Path) -> None:
        self._conn = open_database(path)
        self._lock = threading.Lock()

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Hold `self._lock` across one `BEGIN IMMEDIATE` transaction,
        rolled back if the body or the `COMMIT` itself raises.

        A `COMMIT` refused with SQLITE_BUSY leaves the transaction open, and
        this connection is shared by every caller, so it must be rolled back
        here or every later write fails. A `COMMIT` that fails any other way
        has already been rolled back by SQLite, and a second `ROLLBACK` would
        replace the real error with "no transaction is active"."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    @contextmanager
    def _read_snapshot(self) -> Iterator[sqlite3.Connection]:
        """Hold `self._lock` across one deferred read transaction. Under WAL
        its first read pins a snapshot, so every statement inside it sees
        the same committed state, whatever another process commits
        meanwhile."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            finally:
                # Nothing was written, so ending it either way is the same.
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")

    def _fetchone(self, sql: str, params: Sequence[object] = ()) -> tuple[object, ...] | None:
        with self._lock:
            return cast("tuple[object, ...] | None", self._conn.execute(sql, params).fetchone())

    def _fetchall(self, sql: str, params: Sequence[object]) -> list[tuple[object, ...]]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _count(self, sql: str, params: Sequence[object] = ()) -> int:
        row = self._fetchone(sql, params)
        return int(cast(int, row[0])) if row is not None else 0

    def record_session(
        self,
        execution: Execution,
        *,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
    ) -> bool:
        # One transaction, fixed order: existence probe, run upsert,
        # catalogue upsert, surrogate-key resolve, result insert, metadata
        # inserts. The order is required -- `PRAGMA foreign_keys=ON` is set on
        # every connection, so each row's `run_id`/`test_case_id` referent
        # must exist first.
        run_id = execution.identity.value
        with self._write_transaction() as conn:
            created = conn.execute(_PROBE_RUN_EXISTS, (run_id,)).fetchone() is None

            conn.execute(
                _UPSERT_RUN,
                (
                    run_id,
                    received_at.isoformat(),
                    _fixed_width_isoformat(received_at),
                    execution.started_at.isoformat(),
                    execution.finished_at.isoformat() if execution.finished_at else None,
                    execution.exit_status,
                    1 if execution.interrupted else 0,
                    execution.interrupt_reason,
                    *_vcs_columns(execution.vcs),
                ),
            )

            if results:
                catalogue_rows = _catalogue_rows(execution, results)
                conn.executemany(_UPSERT_TEST_CASE, catalogue_rows)
                test_case_ids = _resolve_test_case_ids(conn, [row[0] for row in catalogue_rows])
                conn.executemany(_INSERT_RESULT, _result_rows(execution, results, test_case_ids))

            if metadata.files:
                conn.executemany(_INSERT_METADATA_FILE, _metadata_file_rows(run_id, metadata))
            if metadata.entries:
                conn.executemany(_INSERT_METADATA_ENTRY, _metadata_entry_rows(run_id, metadata))
        return created

    def get_execution(self, execution_id: str) -> Execution | None:
        row = self._fetchone(_SELECT_RUN, (execution_id,))
        return None if row is None else _decode_execution(row[:12])

    def touch_last_contact(self, execution_id: str, contacted_at: datetime) -> bool:
        with self._lock:
            formatted = _fixed_width_isoformat(contacted_at)
            cursor = self._conn.execute(_TOUCH_LAST_CONTACT, (formatted, execution_id, formatted))
            return cursor.rowcount == 1

    def count_executions(self) -> int:
        return self._count("SELECT COUNT(*) FROM run")

    def get_results(self, execution_id: str) -> Sequence[Result]:
        rows = self._fetchall(_SELECT_RESULTS_FOR_RUN, (execution_id,))
        return [_row_to_result(row) for row in rows]

    def count_results(self) -> int:
        return self._count("SELECT COUNT(*) FROM result")

    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        row = self._fetchone(_SELECT_TEST_CASE, (node_id,))
        return None if row is None else _row_to_catalogue_entry(row)

    def list_runs(
        self,
        *,
        limit: int,
        offset: int,
        metadata_key: str | None = None,
        metadata_value: str | None = None,
    ) -> Page[RunListEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        if metadata_key is not None and metadata_value is not None:
            rows = self._fetchall(
                _LIST_RUNS_BY_METADATA,
                (_LIST_SUBJECT_PREFIX_BYTES, metadata_key, metadata_value, page_limit + 1, offset),
            )
        else:
            rows = self._fetchall(_LIST_RUNS, (_LIST_SUBJECT_PREFIX_BYTES, page_limit + 1, offset))
        return _page(rows, page_limit, _row_to_run_list_entry)

    def count_runs_predating_metadata_key(self, key: str) -> int:
        return self._count(_COUNT_RUNS_PREDATING_KEY, (key,))

    def list_runs_with_metadata_horizon(
        self, *, key: str, value: str, limit: int, offset: int
    ) -> tuple[Page[RunListEntry], int]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        with self._read_snapshot() as conn:
            rows = conn.execute(
                _LIST_RUNS_BY_METADATA,
                (_LIST_SUBJECT_PREFIX_BYTES, key, value, page_limit + 1, offset),
            ).fetchall()
            (predating,) = conn.execute(_COUNT_RUNS_PREDATING_KEY, (key,)).fetchone()
        return _page(rows, page_limit, _row_to_run_list_entry), int(predating)

    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        row = self._fetchone(_SELECT_RUN, (execution_id,))
        if row is None:
            return None
        return RunDetail(
            execution=_decode_execution(row[:12]), last_contact_at=_opt_datetime(row[12])
        )

    def list_results(self, execution_id: str, *, limit: int, offset: int) -> Page[ResultListEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        rows = self._fetchall(
            _LIST_RESULTS,
            (_LIST_MESSAGE_PREFIX_BYTES, execution_id, page_limit + 1, offset),
        )
        return _page(rows, page_limit, _row_to_result_list_entry)

    def get_result(self, execution_id: str, *, node_id: str) -> Result | None:
        row = self._fetchone(_SELECT_RESULT, (execution_id, node_id))
        return None if row is None else _row_to_result(row)

    def list_history(self, *, node_id: str, limit: int, offset: int) -> Page[HistoryEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        rows = self._fetchall(
            _LIST_HISTORY,
            (_LIST_SUBJECT_PREFIX_BYTES, node_id, page_limit + 1, offset),
        )
        return _page(rows, page_limit, _row_to_history_entry)

    def list_settings(self, namespace: str) -> Sequence[UserSetting]:
        rows = self._fetchall(_LIST_SETTINGS, (namespace,))
        return tuple(_row_to_user_setting(row) for row in rows)

    def upsert_setting(self, namespace: str, key: str, *, value: str, updated_at: datetime) -> bool:
        # Probe first, as `record_session` does: `rowcount` cannot
        # distinguish insert from update under `DO UPDATE`.
        formatted = _fixed_width_isoformat(updated_at)
        with self._write_transaction() as conn:
            created = conn.execute(_PROBE_SETTING_EXISTS, (namespace, key)).fetchone() is None
            conn.execute(_UPSERT_SETTING, (namespace, key, value, formatted))
        return created

    def delete_setting(self, namespace: str, key: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(_DELETE_SETTING, (namespace, key))
            return cursor.rowcount == 1

    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]:
        rows = self._fetchall(_SELECT_RUN_CASE_OUTCOMES, (execution_id,))
        return tuple((cast(str, file_path), cast(str, outcome)) for file_path, outcome in rows)

    def close(self) -> None:
        self._conn.close()
