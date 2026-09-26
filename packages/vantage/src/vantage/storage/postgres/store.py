"""PostgreSQL adapter for `ExecutionStore`.

Several server processes may share one database, so nothing here relies on
a lock inside the process. Every call borrows a pooled connection and runs
in a transaction of its own, and the database serialises what must be
serialised:

- A report is one transaction. The run upsert's `ON CONFLICT (id) DO UPDATE
  ... WHERE run.exit_status IS NULL AND excluded.exit_status IS NOT NULL`
  applies a finish over a start, never the reverse, as the SQLite adapter
  does. Whether it created the run is read from that statement itself --
  `RETURNING xmax = 0` is true only for a row it inserted, and it returns
  no row when the conflict's `WHERE` declined -- never from a probe another
  process could overtake. The run row is then locked, so concurrent reports
  of one run write their results and count their metadata one at a time.
- The catalogue is upserted in node-id order, so two reports sharing tests
  lock those rows in one order and never deadlock. `first_seen_at` takes
  the earlier time, `last_seen_at` the later, and the identity follows the
  newest run, as in the SQLite adapter.
- `upsert_setting` counts a namespace's keys and writes under an advisory
  lock on the namespace, held to the end of its transaction.
- `touch_last_contact` is one conditional `UPDATE`, which never moves the
  contact backwards.
- A transaction ended by a serialization failure or a deadlock is run again,
  a bounded number of times; any other error propagates.
- A read that must see one state -- a run page with its metadata horizons, a
  run's metadata -- is one `REPEATABLE READ` transaction; every other read is
  a single statement.

Timestamps are `timestamptz`, compared as instants and read back in UTC
whatever the session's time zone. PostgreSQL text cannot hold U+0000, so it
becomes U+FFFD in everything written, and a lookup by a value holding one
matches nothing, as it would in a store that never held one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import TypeVar, cast

from psycopg import errors

from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES
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
    MetadataEntry,
    NamespaceFullError,
    Page,
    ResultListEntry,
    RunDetail,
    RunListEntry,
    RunMetadata,
    UserSetting,
)
from vantage.storage.postgres.connection import PgConnection, open_pool, prepare_database

T = TypeVar("T")
Row = tuple[object, ...]

# How many times a transaction is run before its serialization failure or
# deadlock propagates.
_MAX_ATTEMPTS = 5

# The first key of `upsert_setting`'s advisory lock ("vset"), vantage's own
# so it never meets another application's lock on the same database.
_SETTINGS_LOCK_CLASS = 0x76736574

_SNAPSHOT = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"

# `last_contact_at` is set on the insert branch only. The `vcs_*` columns
# update under the `exit_status` guard, each through `COALESCE`, so a report
# without VCS data never nulls one an earlier report recorded, and the
# truncation flag follows whichever subject is kept.
_UPSERT_RUN = """
    INSERT INTO vantage.run AS run (
        id, received_at, last_contact_at, started_at, finished_at,
        exit_status, interrupted, interrupt_reason,
        vcs_commit, vcs_branch, vcs_commit_subject, vcs_commit_subject_truncated,
        vcs_dirty, vcs_root
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
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
    RETURNING xmax = 0
"""

# The conflict path of `_UPSERT_RUN` already holds this lock; taking it
# explicitly holds it on every path.
_LOCK_RUN = "SELECT 1 FROM vantage.run WHERE id = %s FOR UPDATE"

_PROBE_RUN = "SELECT 1 FROM vantage.run WHERE id = %s"

_TOUCH_LAST_CONTACT = """
    UPDATE vantage.run
       SET last_contact_at = %s
     WHERE id = %s
       AND (last_contact_at IS NULL OR last_contact_at < %s)
"""

# The statements below are composed from these column lists so that a query
# and its twin cannot drift apart. Only the module's own constants are ever
# interpolated, never a caller's value, so the S608 findings on them are not
# the injection pattern the rule looks for.

# `_decode_execution`'s twelve columns, in its order.
_EXECUTION_COLUMNS = """
    run.id, run.started_at, run.finished_at, run.exit_status, run.interrupted,
    run.interrupt_reason,
    run.vcs_commit, run.vcs_branch, run.vcs_commit_subject,
    run.vcs_commit_subject_truncated, run.vcs_dirty, run.vcs_root
"""

# `_EXECUTION_COLUMNS` for a list: one character past the display width of
# the subject, which is what `project_vcs` needs to tell a longer subject
# from one that fits, so a 64 KiB subject is never read only to be cut.
_LIST_EXECUTION_COLUMNS = f"""
    run.id, run.started_at, run.finished_at, run.exit_status, run.interrupted,
    run.interrupt_reason,
    run.vcs_commit, run.vcs_branch,
    left(run.vcs_commit_subject, {LIST_COMMIT_SUBJECT_CHARS + 1}),
    run.vcs_commit_subject_truncated, run.vcs_dirty, run.vcs_root
"""

# `last_contact_at` comes last, so the first twelve values decode as an
# `Execution`.
_SELECT_RUN = f"""
    SELECT {_EXECUTION_COLUMNS}, run.last_contact_at FROM vantage.run AS run WHERE run.id = %s
"""  # noqa: S608

_SELECT_RUN_LIST = f"""
    SELECT {_LIST_EXECUTION_COLUMNS}, run.last_contact_at FROM vantage.run AS run
"""  # noqa: S608

_LIST_RUNS = f"""
    {_SELECT_RUN_LIST}
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT %s OFFSET %s
"""

# The runs holding one `(key, value)` pair, bound as key, key, value, value.
# The digests find the rows through `run_metadata_key_value`; comparing the
# text as well keeps the match exact. `value` is NULL for a row without a
# captured value, and NULL equals nothing, so such a key never matches.
_RUNS_HOLDING_PAIR = """
    SELECT rm.run_id FROM vantage.run_metadata rm
    WHERE vantage.text_key(rm.key) = vantage.text_key(%s) AND rm.key = %s
      AND vantage.text_key(rm.value) = vantage.text_key(%s) AND rm.value = %s
"""


def _list_runs_by_metadata(pair_count: int) -> str:
    """`_LIST_RUNS` narrowed to the runs holding each of `pair_count` pairs,
    bound before the limit and offset. Only the module's own constants are
    interpolated."""
    holding_every_pair = " INTERSECT ".join([_RUNS_HOLDING_PAIR] * pair_count)
    return f"""
        {_SELECT_RUN_LIST}
        WHERE run.id IN ({holding_every_pair})
        ORDER BY run.started_at DESC, run.id DESC
        LIMIT %s OFFSET %s
    """  # noqa: S608


# How many runs started before `key` first appeared, bound as key, key.
# `first_seen` is the earliest start among runs holding any row for the key,
# whatever its status or source; a key no run ever carried has none, and
# every run predates it.
_COUNT_RUNS_PREDATING_KEY = """
    SELECT CASE WHEN first_seen.started_at IS NULL
                THEN (SELECT count(*) FROM vantage.run)
                ELSE (SELECT count(*) FROM vantage.run
                      WHERE run.started_at < first_seen.started_at)
           END
    FROM (
        SELECT min(run.started_at) AS started_at
        FROM vantage.run_metadata rm
        JOIN vantage.run AS run ON run.id = rm.run_id
        WHERE vantage.text_key(rm.key) = vantage.text_key(%s) AND rm.key = %s
    ) AS first_seen
"""

_COUNT_RUNS = "SELECT count(*) FROM vantage.run"

# Every right-hand side reads the row as it was before the update. The
# identity columns and `last_seen_run_id` follow the newest run, so a late
# report of an older one never changes what newer runs read. `RETURNING id`
# answers with the row's id whichever branch ran.
_UPSERT_TEST_CASE = """
    INSERT INTO vantage.test_case AS tc (
        node_id, file_path, class_name, function_name,
        param_id, first_seen_at, last_seen_at, last_seen_run_id
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (vantage.text_key(node_id)) DO UPDATE SET
        file_path        = CASE WHEN excluded.last_seen_at > tc.last_seen_at
                                THEN excluded.file_path ELSE tc.file_path END,
        class_name       = CASE WHEN excluded.last_seen_at > tc.last_seen_at
                                THEN excluded.class_name ELSE tc.class_name END,
        function_name    = CASE WHEN excluded.last_seen_at > tc.last_seen_at
                                THEN excluded.function_name ELSE tc.function_name END,
        param_id         = CASE WHEN excluded.last_seen_at > tc.last_seen_at
                                THEN excluded.param_id ELSE tc.param_id END,
        last_seen_run_id = CASE WHEN excluded.last_seen_at > tc.last_seen_at
                                THEN excluded.last_seen_run_id ELSE tc.last_seen_run_id END,
        first_seen_at    = LEAST(tc.first_seen_at, excluded.first_seen_at),
        last_seen_at     = GREATEST(tc.last_seen_at, excluded.last_seen_at)
    RETURNING id
"""

# The thirteen `FailureEvidence` columns and the four `CapturedOutput`
# columns follow `worker_id`, in the order `_result_rows` builds them.
_INSERT_RESULT = """
    INSERT INTO vantage.result (
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
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
    ON CONFLICT (run_id, vantage.text_key(node_id)) DO NOTHING
"""

# Metadata is write-once: the first report to carry a file or a key keeps
# it, whatever a later one says.
_INSERT_METADATA_FILE = """
    INSERT INTO vantage.run_metadata_file (run_id, source_file, content_type, status)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (run_id, vantage.text_key(source_file)) DO NOTHING
"""

# A new key is dropped once its run holds `MAX_METADATA_ENTRIES` rows. Each
# execution is its own statement, so its count sees the rows the earlier
# ones inserted, and the run row lock keeps another report's from landing
# in between.
_INSERT_METADATA_ENTRY = """
    INSERT INTO vantage.run_metadata (
        run_id, key, name, value, status, source, source_file, declared
    )
    SELECT %s, %s, %s, %s, %s, %s, %s, %s
    WHERE (SELECT count(*) FROM vantage.run_metadata WHERE run_id = %s) < %s
    ON CONFLICT (run_id, vantage.text_key(key)) DO NOTHING
"""

# `key` is `COLLATE "C"`: code point order, as the other adapters sort.
_SELECT_RUN_METADATA = """
    SELECT key, name, value, status, source, source_file, declared
    FROM vantage.run_metadata WHERE run_id = %s
    ORDER BY key
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

_SELECT_FULL_RESULT = f"""
    SELECT {_RESULT_COLUMNS}, {_FAILURE_COLUMNS}, {_CAPTURED_COLUMNS}
    FROM vantage.result r
    JOIN vantage.test_case tc ON tc.id = r.test_case_id
"""  # noqa: S608

_SELECT_RESULTS_FOR_RUN = f"{_SELECT_FULL_RESULT} WHERE r.run_id = %s ORDER BY r.id"

_SELECT_RESULT = f"""
    {_SELECT_FULL_RESULT}
    WHERE r.run_id = %s
      AND vantage.text_key(r.node_id) = vantage.text_key(%s) AND r.node_id = %s
"""

# The paginated, lean sibling of `_SELECT_RESULTS_FOR_RUN`, in
# `_FAILURE_COLUMNS`' shape so the row decodes as a `Result` for
# `project_failure`. The message is read one character past its display
# width; `failure_repr` and `traceback` never: `left(x, 0)` is '' for a
# stored value and NULL for none, all the emptiness rule needs of them.
_LIST_RESULTS = f"""
    SELECT {_RESULT_COLUMNS},
           r.failure_type,
           left(r.failure_message, {LIST_FAILURE_MESSAGE_CHARS + 1}),
           r.failure_message_truncated,
           r.failure_path, r.failure_lineno,
           left(r.failure_repr, 0), r.failure_repr_truncated,
           left(r.traceback, 0), r.traceback_truncated,
           r.skip_reason, r.skip_reason_truncated,
           r.xfail_reason, r.xfail_reason_truncated
    FROM vantage.result r
    JOIN vantage.test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = %s
    ORDER BY r.id
    LIMIT %s OFFSET %s
"""  # noqa: S608

# Same execution columns and total order as `_LIST_RUNS`.
_LIST_HISTORY = f"""
    SELECT {_LIST_EXECUTION_COLUMNS}, run.last_contact_at, r.outcome, r.duration
    FROM vantage.test_case tc
    JOIN vantage.result r ON r.test_case_id = tc.id
    JOIN vantage.run AS run ON run.id = r.run_id
    WHERE vantage.text_key(tc.node_id) = vantage.text_key(%s) AND tc.node_id = %s
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT %s OFFSET %s
"""  # noqa: S608

_SELECT_TEST_CASE = """
    SELECT node_id, file_path, class_name, function_name, param_id,
           first_seen_at, last_seen_at, last_seen_run_id
    FROM vantage.test_case
    WHERE vantage.text_key(node_id) = vantage.text_key(%s) AND node_id = %s
"""

_LIST_SETTINGS = """
    SELECT namespace, key, value, updated_at
    FROM vantage.user_setting WHERE namespace = %s ORDER BY key
"""

_LOCK_NAMESPACE = "SELECT pg_advisory_xact_lock(%s, hashtext(%s))"

_PROBE_SETTING = "SELECT 1 FROM vantage.user_setting WHERE namespace = %s AND key = %s"

_COUNT_SETTINGS = "SELECT count(*) FROM vantage.user_setting WHERE namespace = %s"

_UPSERT_SETTING = """
    INSERT INTO vantage.user_setting (namespace, key, value, updated_at)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (namespace, key) DO UPDATE SET
        value = excluded.value,
        updated_at = excluded.updated_at
    RETURNING xmax = 0
"""

_DELETE_SETTING = "DELETE FROM vantage.user_setting WHERE namespace = %s AND key = %s"

_SELECT_RUN_CASE_OUTCOMES = """
    SELECT tc.file_path, r.outcome
    FROM vantage.result r
    JOIN vantage.test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = %s
    ORDER BY r.id
"""


def _stored(value: T) -> T:
    """`value` as it is written: a string with U+0000, which PostgreSQL
    text cannot hold, replaced by U+FFFD; anything else unchanged."""
    if isinstance(value, str):
        return cast(T, value.replace("\x00", "\ufffd"))
    return value


def _params(*values: object) -> Row:
    """Statement parameters for a write, each string as `_stored` writes it."""
    return tuple(_stored(value) for value in values)


def _unmatchable(*values: str) -> bool:
    """Whether a lookup by `values` can match nothing: every string written
    has had U+0000 replaced, so none stored holds one."""
    return any("\x00" in value for value in values)


def _utc(value: object) -> datetime:
    """A `NOT NULL` timestamp, in UTC. psycopg hands it back in the
    session's time zone, whatever the server was told."""
    return cast(datetime, value).astimezone(timezone.utc)


def _opt_utc(value: object) -> datetime | None:
    return None if value is None else _utc(value)


def _vcs_columns(vcs: VcsContext | None) -> Row:
    """The six `vcs_*` `_UPSERT_RUN` parameters. `vcs_dirty` stays NULL when
    unknown, never false, which would have a run recorded outside a
    repository claim a clean working tree."""
    if vcs is None:
        return (None, None, None, False, None, None)
    return (
        vcs.commit,
        vcs.branch,
        vcs.commit_subject,
        vcs.commit_subject_truncated,
        vcs.dirty,
        vcs.root,
    )


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
        commit_subject=cast("str | None", commit_subject),
        commit_subject_truncated=bool(commit_subject_truncated),
        dirty=cast("bool | None", dirty),
        root=cast("str | None", root),
    )
    return None if vcs.is_empty() else vcs


def _decode_execution(row: Sequence[object]) -> Execution:
    """The twelve `_EXECUTION_COLUMNS` or `_LIST_EXECUTION_COLUMNS`."""
    identity_value, started_at, finished_at, exit_status, interrupted, interrupt_reason = row[:6]
    return Execution(
        identity=Identity(cast(str, identity_value)),
        started_at=_utc(started_at),
        finished_at=_opt_utc(finished_at),
        exit_status=cast("int | None", exit_status),
        interrupted=bool(interrupted),
        interrupt_reason=cast("str | None", interrupt_reason),
        vcs=_decode_vcs(row[6:12]),
    )


def _row_to_run_list_entry(row: Row) -> RunListEntry:
    return RunListEntry.from_execution(
        _decode_execution(row[:12]), last_contact_at=_opt_utc(row[12])
    )


def _row_to_history_entry(row: Row) -> HistoryEntry:
    last_contact_at, outcome, duration = row[12:]
    return HistoryEntry.from_execution(
        _decode_execution(row[:12]),
        last_contact_at=_opt_utc(last_contact_at),
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
        failure_message=cast("str | None", failure_message),
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
    """Never `None`; a stored `''` reads back as `''`."""
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
        started_at=_opt_utc(started_at),
        finished_at=_opt_utc(finished_at),
        setup_outcome=cast("str | None", setup_outcome),
        call_outcome=cast("str | None", call_outcome),
        teardown_outcome=cast("str | None", teardown_outcome),
        setup_duration=cast("float | None", setup_duration),
        call_duration=cast("float | None", call_duration),
        teardown_duration=cast("float | None", teardown_duration),
        worker_id=cast("str | None", worker_id),
        failure=failure,
    )


def _row_to_result(row: Row) -> Result:
    """A `_SELECT_FULL_RESULT` row."""
    return replace(
        _decode_result(row[:16], _decode_failure(row[16:29])),
        captured=_decode_captured(row[29:33]),
    )


def _row_to_result_list_entry(row: Row) -> ResultListEntry:
    """A `_LIST_RESULTS` row."""
    return ResultListEntry.from_result(_decode_result(row[:16], _decode_failure(row[16:29])))


def _row_to_metadata_entry(row: Row) -> MetadataEntry:
    """A `_SELECT_RUN_METADATA` row."""
    key, name, value, status, source, source_file, declared = row
    return MetadataEntry(
        key=cast(str, key),
        name=cast("str | None", name),
        value=cast("str | None", value),
        status=cast(str, status),
        source=cast(str, source),
        source_file=cast("str | None", source_file),
        declared=bool(declared),
    )


def _row_to_catalogue_entry(row: Row) -> CatalogueEntry:
    first_seen_at, last_seen_at, last_seen_run_id = row[5:]
    return CatalogueEntry(
        identity=_decode_identity(row[:5]),
        first_seen_at=_utc(first_seen_at),
        last_seen_at=_utc(last_seen_at),
        last_seen_run_id=cast("str | None", last_seen_run_id),
    )


def _row_to_user_setting(row: Row) -> UserSetting:
    namespace, key, value, updated_at = row
    return UserSetting(
        namespace=cast(str, namespace),
        key=cast(str, key),
        value=cast(str, value),
        updated_at=_utc(updated_at),
    )


def _catalogue_rows(execution: Execution, results: Sequence[Result]) -> list[Row]:
    """One `_UPSERT_TEST_CASE` row per node id, in node-id order: every
    report locks the rows it shares with another in the same order."""
    run_id = execution.identity.value
    by_node_id: dict[str, CaseIdentity] = {
        _stored(result.identity.node_id): result.identity for result in results
    }
    return [
        _params(
            node_id,
            identity.file_path,
            identity.class_name,
            identity.function_name,
            identity.param_id,
            execution.started_at,  # first_seen_at -- LEAST decides on conflict
            execution.started_at,  # last_seen_at -- GREATEST and CASE decide
            run_id,
        )
        for node_id, identity in sorted(by_node_id.items())
    ]


def _failure_columns(failure: FailureEvidence | None) -> Row:
    """The thirteen `FailureEvidence` `_INSERT_RESULT` parameters, all
    NULL or false for no evidence at all."""
    if failure is None:
        return (None, None, False, None, None, None, False, None, False, None, False, None, False)
    return (
        failure.failure_type,
        failure.failure_message,
        failure.failure_message_truncated,
        failure.failure_path,
        failure.failure_lineno,
        failure.failure_repr,
        failure.failure_repr_truncated,
        failure.traceback,
        failure.traceback_truncated,
        failure.skip_reason,
        failure.skip_reason_truncated,
        failure.xfail_reason,
        failure.xfail_reason_truncated,
    )


def _result_rows(
    execution: Execution, results: Sequence[Result], test_case_ids: dict[str, object]
) -> list[Row]:
    run_id = execution.identity.value
    return [
        _params(
            run_id,
            test_case_ids[_stored(result.identity.node_id)],
            result.identity.node_id,
            result.outcome,
            result.duration,
            result.started_at,
            result.finished_at,
            result.setup_outcome,
            result.call_outcome,
            result.teardown_outcome,
            result.setup_duration,
            result.call_duration,
            result.teardown_duration,
            result.worker_id,
            *_failure_columns(result.failure),
            # Written through unchanged: `''` (captured, empty) must stay
            # apart from NULL (never captured).
            result.captured.stdout,
            result.captured.stdout_truncated,
            result.captured.stderr,
            result.captured.stderr_truncated,
        )
        for result in results
    ]


def _metadata_entry_rows(run_id: str, metadata: RunMetadata) -> list[Row]:
    return [
        _params(
            run_id,
            entry.key,
            entry.name,
            entry.value,
            entry.status,
            entry.source,
            entry.source_file,
            entry.declared,
            run_id,
            MAX_METADATA_ENTRIES,
        )
        for entry in metadata.entries
    ]


def _page(rows: Sequence[Row], page_limit: int, decode: Callable[[Row], T]) -> Page[T]:
    """A page from a query that fetched one row past `page_limit`: that
    row's presence tells a truncated page from an exhausted one without a
    second query that could see another state."""
    return Page(
        items=tuple(decode(row) for row in rows[:page_limit]), has_more=len(rows) > page_limit
    )


def _upsert_catalogue(conn: PgConnection, rows: list[Row]) -> dict[str, object]:
    """Upsert every catalogue row and return each node id's `test_case.id`.
    `executemany` returns one result per row, in the rows' order."""
    ids: list[object] = []
    with conn.cursor() as cursor:
        cursor.executemany(_UPSERT_TEST_CASE, rows, returning=True)
        while True:
            row = cursor.fetchone()
            ids.append(None if row is None else row[0])
            if not cursor.nextset():
                break
    return {cast(str, row[0]): test_case_id for row, test_case_id in zip(rows, ids)}


def _count_runs_predating(conn: PgConnection, key: str) -> int:
    if _unmatchable(key):
        row = conn.execute(_COUNT_RUNS).fetchone()
    else:
        row = conn.execute(_COUNT_RUNS_PREDATING_KEY, (key, key)).fetchone()
    return 0 if row is None else int(cast(int, row[0]))


class PostgresExecutionStore:
    """Implements `vantage.core.ports.storage.ExecutionStore` against
    PostgreSQL, in the `vantage` schema of the database `url` names.

    `url` is a libpq connection string, usually a `postgresql://` URL;
    credentials may also come from libpq's own `PGPASSWORD`, `~/.pgpass` and
    other `PG*` variables. The constructor creates the schema, or refuses the
    database with `SchemaVersionError` or `PostgresOpenError`, then opens a
    pool of at most `max_connections` connections, which `close` closes.
    """

    def __init__(self, url: str, *, max_connections: int = 10) -> None:
        prepare_database(url)
        self._pool = open_pool(url, max_connections=max_connections)

    def _transaction(self, work: Callable[[PgConnection], T], *, snapshot: bool = False) -> T:
        """`work` in one transaction on a pooled connection, rolled back if
        it raises, and run again from the start after a serialization
        failure or a deadlock. `snapshot` makes it a read-only `REPEATABLE
        READ` transaction, whose every statement sees one state."""
        attempt = 1
        while True:
            try:
                with self._pool.connection() as conn, conn.transaction():
                    if snapshot:
                        conn.execute(_SNAPSHOT)
                    return work(conn)
            except (errors.SerializationFailure, errors.DeadlockDetected):
                if attempt >= _MAX_ATTEMPTS:
                    raise
                attempt += 1

    def _fetchone(self, sql: str, params: Sequence[object] = ()) -> Row | None:
        """One statement, its own transaction on an autocommit connection."""
        with self._pool.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: Sequence[object]) -> list[Row]:
        with self._pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def _count(self, sql: str) -> int:
        row = self._fetchone(sql)
        return 0 if row is None else int(cast(int, row[0]))

    def record_session(
        self,
        execution: Execution,
        *,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
    ) -> bool:
        # One transaction, in the order every foreign key needs: run,
        # catalogue, results, metadata.
        run_id = execution.identity.value
        run_row = _params(
            run_id,
            received_at,
            received_at,
            execution.started_at,
            execution.finished_at,
            execution.exit_status,
            execution.interrupted,
            execution.interrupt_reason,
            *_vcs_columns(execution.vcs),
        )
        catalogue_rows = _catalogue_rows(execution, results)
        file_rows = [
            _params(run_id, file.source_file, file.content_type, file.status)
            for file in metadata.files
        ]
        entry_rows = _metadata_entry_rows(run_id, metadata)

        def write(conn: PgConnection) -> bool:
            upserted = conn.execute(_UPSERT_RUN, run_row).fetchone()
            conn.execute(_LOCK_RUN, (run_id,))
            with conn.cursor() as cursor:
                if catalogue_rows:
                    test_case_ids = _upsert_catalogue(conn, catalogue_rows)
                    cursor.executemany(
                        _INSERT_RESULT, _result_rows(execution, results, test_case_ids)
                    )
                if file_rows:
                    cursor.executemany(_INSERT_METADATA_FILE, file_rows)
                if entry_rows:
                    cursor.executemany(_INSERT_METADATA_ENTRY, entry_rows)
            return upserted is not None and bool(upserted[0])

        return self._transaction(write)

    def get_execution(self, execution_id: str) -> Execution | None:
        if _unmatchable(execution_id):
            return None
        row = self._fetchone(_SELECT_RUN, (execution_id,))
        return None if row is None else _decode_execution(row[:12])

    def touch_last_contact(self, execution_id: str, contacted_at: datetime) -> bool:
        if _unmatchable(execution_id):
            return False
        with self._pool.connection() as conn:
            cursor = conn.execute(_TOUCH_LAST_CONTACT, (contacted_at, execution_id, contacted_at))
            return cursor.rowcount == 1

    def count_executions(self) -> int:
        return self._count(_COUNT_RUNS)

    def get_results(self, execution_id: str) -> Sequence[Result]:
        if _unmatchable(execution_id):
            return []
        rows = self._fetchall(_SELECT_RESULTS_FOR_RUN, (execution_id,))
        return [_row_to_result(row) for row in rows]

    def count_results(self) -> int:
        return self._count("SELECT count(*) FROM vantage.result")

    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        if _unmatchable(node_id):
            return None
        row = self._fetchone(_SELECT_TEST_CASE, (node_id, node_id))
        return None if row is None else _row_to_catalogue_entry(row)

    def list_runs(self, *, limit: int, offset: int) -> Page[RunListEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        rows = self._fetchall(_LIST_RUNS, (page_limit + 1, offset))
        return _page(rows, page_limit, _row_to_run_list_entry)

    def list_runs_with_metadata_horizon(
        self, *, filters: Sequence[tuple[str, str]], limit: int, offset: int
    ) -> tuple[Page[RunListEntry], tuple[int, ...]]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        # A repeated pair narrows nothing further.
        pairs = list(dict.fromkeys(filters))
        keys = list(dict.fromkeys(key for key, _value in pairs))

        def read(conn: PgConnection) -> tuple[list[Row], tuple[int, ...]]:
            if any(_unmatchable(key, value) for key, value in pairs):
                rows: list[Row] = []
            else:
                sql = _list_runs_by_metadata(len(pairs)) if pairs else _LIST_RUNS
                params = [
                    *(part for key, value in pairs for part in (key, key, value, value)),
                    page_limit + 1,
                    offset,
                ]
                rows = conn.execute(sql, params).fetchall()
            return rows, tuple(_count_runs_predating(conn, key) for key in keys)

        rows, predating = self._transaction(read, snapshot=True)
        return _page(rows, page_limit, _row_to_run_list_entry), predating

    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        if _unmatchable(execution_id):
            return None
        row = self._fetchone(_SELECT_RUN, (execution_id,))
        if row is None:
            return None
        return RunDetail(execution=_decode_execution(row[:12]), last_contact_at=_opt_utc(row[12]))

    def get_run_metadata(self, execution_id: str) -> Sequence[MetadataEntry] | None:
        if _unmatchable(execution_id):
            return None

        def read(conn: PgConnection) -> list[Row] | None:
            if conn.execute(_PROBE_RUN, (execution_id,)).fetchone() is None:
                return None
            return conn.execute(_SELECT_RUN_METADATA, (execution_id,)).fetchall()

        rows = self._transaction(read, snapshot=True)
        return None if rows is None else tuple(_row_to_metadata_entry(row) for row in rows)

    def list_results(self, execution_id: str, *, limit: int, offset: int) -> Page[ResultListEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        if _unmatchable(execution_id):
            return Page(items=(), has_more=False)
        rows = self._fetchall(_LIST_RESULTS, (execution_id, page_limit + 1, offset))
        return _page(rows, page_limit, _row_to_result_list_entry)

    def get_result(self, execution_id: str, *, node_id: str) -> Result | None:
        if _unmatchable(execution_id, node_id):
            return None
        row = self._fetchone(_SELECT_RESULT, (execution_id, node_id, node_id))
        return None if row is None else _row_to_result(row)

    def list_history(self, *, node_id: str, limit: int, offset: int) -> Page[HistoryEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        if _unmatchable(node_id):
            return Page(items=(), has_more=False)
        rows = self._fetchall(_LIST_HISTORY, (node_id, node_id, page_limit + 1, offset))
        return _page(rows, page_limit, _row_to_history_entry)

    def list_settings(self, namespace: str) -> Sequence[UserSetting]:
        if _unmatchable(namespace):
            return ()
        rows = self._fetchall(_LIST_SETTINGS, (namespace,))
        return tuple(_row_to_user_setting(row) for row in rows)

    def upsert_setting(
        self,
        namespace: str,
        key: str,
        *,
        value: str,
        updated_at: datetime,
        max_keys: int | None = None,
    ) -> bool:
        namespace, key, value = _stored(namespace), _stored(key), _stored(value)

        def write(conn: PgConnection) -> bool:
            # Every writer of the namespace takes this lock first, so the
            # count below cannot be passed by a key another process adds
            # before this transaction commits.
            conn.execute(_LOCK_NAMESPACE, (_SETTINGS_LOCK_CLASS, namespace))
            if (
                max_keys is not None
                and conn.execute(_PROBE_SETTING, (namespace, key)).fetchone() is None
            ):
                counted = conn.execute(_COUNT_SETTINGS, (namespace,)).fetchone()
                held = 0 if counted is None else int(cast(int, counted[0]))
                if held >= max_keys:
                    raise NamespaceFullError(f"{namespace!r} already holds {held} keys")
            upserted = conn.execute(_UPSERT_SETTING, (namespace, key, value, updated_at)).fetchone()
            return upserted is not None and bool(upserted[0])

        return self._transaction(write)

    def delete_setting(self, namespace: str, key: str) -> bool:
        if _unmatchable(namespace, key):
            return False
        with self._pool.connection() as conn:
            return conn.execute(_DELETE_SETTING, (namespace, key)).rowcount == 1

    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]:
        if _unmatchable(execution_id):
            return ()
        rows = self._fetchall(_SELECT_RUN_CASE_OUTCOMES, (execution_id,))
        return tuple((cast(str, file_path), cast(str, outcome)) for file_path, outcome in rows)

    def close(self) -> None:
        self._pool.close()
