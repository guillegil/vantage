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
`upsert_setting` and `set_member` tell an added row from a changed one the
same way.

Concurrency needs two layers. `self._lock` is held across every statement
and transaction, reads included, and across `close`: every thread shares one
connection, and a read issued while another thread is inside a write
transaction would run inside it and see rows that are not committed yet, or
never will be. WAL mode and the connection's busy timeout cover another
connection on the same file -- a second server process -- which no
in-process lock can reach. Multi-statement writes start with `BEGIN
IMMEDIATE` because a deferred transaction that upgrades to a write
mid-statement is the classic two-writer deadlock; it also makes a
check-then-write such as `upsert_setting`'s key bound atomic against both.

A session's run, catalogue, result and metadata rows are written in one
transaction, without `RETURNING`: it needs SQLite >= 3.35, newer than some
Python 3.10 builds link against. The catalogue takes `first_seen_at` with
`MIN` and `last_seen_at` with `MAX`, so a report arriving out of start order
moves neither the wrong way, and the result insert is `ON CONFLICT(run_id,
node_id) DO NOTHING`, so a replayed report is a silent no-op. The report
that gives a run its exit status then compares it, last in the same
transaction (`_fix_comparison`): a finished run returns before anything is
written, so a run is compared once, and the lock and `BEGIN IMMEDIATE`
order its choice of baseline against every other writer's commit.

`last_contact_at` is set by the creating report only -- a finished or
interrupted run is done, not stale -- and advanced by `touch_last_contact`'s
monotonic `last_contact_at < ?` update.

Timestamps are compared as TEXT -- that update, the catalogue's `MIN` and
`MAX`, the metadata horizon's `<`, every `ORDER BY started_at` -- so every
one is written through `isoformat_utc`: fixed-width UTC, whose text order
is chronological order whatever offset the caller's `datetime` carried.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TypeVar, cast

from vantage.core.domain.access import (
    ADMIN_SCOPE,
    LOGIN_TOKEN_LABEL,
    MANAGE_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    Grant,
    Token,
    User,
)
from vantage.core.domain.changes import (
    BASELINE_EXIT_STATUSES,
    CHANGE_ORDER,
    RESULT_CHANGES,
    ChangeRow,
    Streak,
    compare,
    ran_to_its_end,
    wanted_changes,
)
from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES
from vantage.core.domain.projection import (
    LIST_COMMIT_SUBJECT_CHARS,
    LIST_FAILURE_MESSAGE_CHARS,
)
from vantage.core.domain.projects import DEFAULT_PROJECT, Membership, Project, check_role
from vantage.core.domain.result import (
    OUTCOMES,
    CapturedOutput,
    CaseIdentity,
    CatalogueEntry,
    FailureEvidence,
    Result,
)
from vantage.core.ports.storage import (
    EMPTY_RUN_METADATA,
    MAX_PAGE_ITEMS,
    BaselineRef,
    ChangeEntry,
    ForeignRunError,
    HistoryEntry,
    MetadataEntry,
    MetadataFile,
    NamespaceFullError,
    Page,
    ProjectExistsError,
    ProjectMismatchError,
    ProjectSetting,
    ResultChange,
    ResultListEntry,
    RunDetail,
    RunKey,
    RunListEntry,
    RunMetadata,
    UnknownProjectError,
    UnknownUserError,
    UserExistsError,
    comparison_of,
)
from vantage.storage.connection import isoformat_utc, open_database

T = TypeVar("T")

# SQLITE_MAX_VARIABLE_NUMBER is 999 on older SQLite builds; 500 leaves
# headroom without needing to introspect the running library's compile-time
# limit.
_MAX_PLACEHOLDERS = 500

# `project`, `last_contact_at` and `recorded_by` are set on the insert branch
# only; the `DO UPDATE SET` list never names them, so no statement moves a
# run to another project. The `vcs_*` columns update
# under the same `exit_status` guard, each through `COALESCE(excluded,
# run)`, so a report without VCS data never nulls a value an earlier report
# recorded. `vcs_commit_subject_truncated` follows whichever subject is
# kept, so the flag always describes the stored subject. The guard also
# names the recorder and the project, though `record_session` refuses
# another user's or another project's report before it gets here: a
# statement that could finish someone else's run should not rely on its
# caller to stop it.
_UPSERT_RUN = """
    INSERT INTO run (
        id, project, received_at, last_contact_at, started_at, finished_at,
        exit_status, interrupted, interrupt_reason,
        vcs_commit, vcs_branch, vcs_commit_subject, vcs_commit_subject_truncated,
        vcs_dirty, vcs_root, recorded_by
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
       AND run.recorded_by IS excluded.recorded_by
       AND run.project = excluded.project
"""

_PROBE_RUN_EXISTS = "SELECT 1 FROM run WHERE id = ?"

_SELECT_RUN_STATE = "SELECT exit_status, recorded_by, project FROM run WHERE id = ?"

_PROBE_PROJECT = "SELECT 1 FROM project WHERE name = ?"

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
    run.vcs_commit, run.vcs_branch,
    CASE WHEN run.vcs_commit_subject = '' THEN ''
         ELSE substr(CAST(run.vcs_commit_subject AS BLOB), 1, ?) END,
    run.vcs_commit_subject_truncated, run.vcs_dirty, run.vcs_root
"""

# How much of a bounded list column to load. The prefix is taken in bytes,
# from the value cast to a BLOB, because SQLite's text `substr` stops at the
# first NUL and would hand back a short prefix that looks complete. An empty
# value is read as itself: a zero-length BLOB has no data, and `substr` of it
# is NULL, which would list a stored `''` as no value at all. UTF-8
# spends at most four bytes on a character, so this many bytes always hold
# the first width + 1 characters whole -- one past the display width, which
# is what `project_vcs` and `project_failure` need to tell a longer value
# from one that fits.
_LIST_SUBJECT_PREFIX_BYTES = 4 * (LIST_COMMIT_SUBJECT_CHARS + 1)
_LIST_MESSAGE_PREFIX_BYTES = 4 * (LIST_FAILURE_MESSAGE_CHARS + 1)

# A run's baseline, read with the run: its id, start and branch, found by
# primary key through `run.baseline_id`, so a page and its comparisons are
# one read. `_decode_baseline`'s three columns.
_BASELINE_COLUMNS = "run.baseline_id, base.started_at, base.vcs_branch"

_WITH_BASELINE = "LEFT JOIN run AS base ON base.id = run.baseline_id"

# `get_execution`, `get_run_detail` and the finishing step share one
# statement; `last_contact_at`, `recorded_by`, `project` and the baseline
# come last so the first twelve values decode as an `Execution`.
_SELECT_RUN = f"""
    SELECT {_EXECUTION_COLUMNS}, run.last_contact_at, run.recorded_by, run.project,
           {_BASELINE_COLUMNS}
    FROM run {_WITH_BASELINE} WHERE run.id = ?
"""  # noqa: S608

_SELECT_RUN_LIST = f"""
    SELECT {_LIST_EXECUTION_COLUMNS}, run.last_contact_at, run.recorded_by, {_BASELINE_COLUMNS}
    FROM run {_WITH_BASELINE}
"""  # noqa: S608

# One project's runs, bound after the subject prefix width, read backwards
# along `idx_run_project_started_at`.
_LIST_RUNS = f"""
    {_SELECT_RUN_LIST}
    WHERE run.project = ?
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT ? OFFSET ?
"""  # noqa: S608

# The runs past a `RunKey` in the newest-first order, bound as its
# `started_at`, then its id: a tie on `started_at` is settled by id, as the
# order settles it, and `idx_run_project_started_at` starts the scan at the
# key.
_AFTER_RUN_KEY = "(run.started_at, run.id) < (?, ?)"

_LIST_RUNS_AFTER = f"""
    {_SELECT_RUN_LIST}
    WHERE run.project = ? AND {_AFTER_RUN_KEY}
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT ? OFFSET ?
"""  # noqa: S608

# The runs holding one `(key, value)` metadata pair. `value` is NULL for any
# row without a captured value, and NULL never equals a bound string, so a
# key whose value was dropped never matches.
_RUNS_HOLDING_PAIR = "SELECT rm.run_id FROM run_metadata rm WHERE rm.key = ? AND rm.value = ?"


def _list_runs_by_metadata(pair_count: int, *, after: bool = False) -> str:
    """`_LIST_RUNS` narrowed to the runs holding each of `pair_count`
    `(key, value)` pairs, bound after the subject prefix width, then to the
    project, bound next, and with `after` to the runs past a `RunKey`
    (`_AFTER_RUN_KEY`), bound after that.

    `id IN (subquery)`, not a correlated `EXISTS`: with `EXISTS`, SQLite
    anchors on `rm.run_id = run.id` and probes the primary-key autoindex
    once per `run` row, so cost grows with the total run count. Each pair's
    uncorrelated `SELECT` seeks `idx_run_metadata_key_value` once, the
    `INTERSECT` keeps the runs every pair found, and each is then looked up
    by primary key. The project is compared as `+run.project`, which keeps
    SQLite from using `idx_run_project_started_at` for it: without
    statistics it would otherwise scan every run of the project and probe
    the list for each, so cost would grow with the project's run count.
    `test_list_runs_by_metadata_uses_the_key_value_index` pins the plan.
    Only the module's own constants are interpolated."""
    holding_every_pair = " INTERSECT ".join([_RUNS_HOLDING_PAIR] * pair_count)
    past_key = f"AND {_AFTER_RUN_KEY}" if after else ""
    return f"""
        {_SELECT_RUN_LIST}
        WHERE run.id IN ({holding_every_pair}) AND +run.project = ? {past_key}
        ORDER BY run.started_at DESC, run.id DESC
        LIMIT ? OFFSET ?
    """  # noqa: S608


# How many of a project's runs started before `key` first appeared in it,
# as one statement so it reads one state of the database. `first_seen` is
# the earliest `started_at` among the project's runs holding any
# `run_metadata` row for `key`, whatever its status or source, found through
# `idx_run_metadata_key_value` (the `+` keeps the project's own index out of
# that seek, as in `_list_runs_by_metadata`); the count is served by
# `idx_run_project_started_at`. A key none of the project's runs ever
# carried has no `first_seen`, and every run of it predates the key. Binds
# the project, the project, the key, the project.
_COUNT_RUNS_PREDATING_KEY = """
    SELECT CASE WHEN first_seen.started_at IS NULL
                THEN (SELECT COUNT(*) FROM run WHERE run.project = ?)
                ELSE (SELECT COUNT(*) FROM run
                      WHERE run.project = ? AND run.started_at < first_seen.started_at)
           END
    FROM (
        SELECT MIN(run.started_at) AS started_at
        FROM run_metadata rm
        JOIN run ON run.id = rm.run_id
        WHERE rm.key = ? AND +run.project = ?
    ) AS first_seen
"""

# A run that can be a baseline, restated from `is_baseline_candidate`: it
# ran to its end with a verdict on its tests. Only the module's constants
# are interpolated.
_COMPLETE = f"""
    run.finished_at IS NOT NULL AND run.interrupted = 0 AND run.interrupt_reason IS NULL
    AND run.exit_status IN ({", ".join(str(status) for status in sorted(BASELINE_EXIT_STATUSES))})
"""

# A finishing run's baseline: the latest complete run of its project
# earlier than it in the run list's order, on its branch, found by one
# backward seek of `idx_run_project_branch_started_at`. Binds the project,
# the branch, then the run's `started_at` and id.
_BRANCH_BASELINE = f"""
    SELECT run.id FROM run
    WHERE run.project = ? AND run.vcs_branch = ? AND {_COMPLETE} AND {_AFTER_RUN_KEY}
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT 1
"""  # noqa: S608

# The same on any branch, a run without one included, along
# `idx_run_project_started_at`. Binds the project, then the run's
# `started_at` and id.
_PROJECT_BASELINE = f"""
    SELECT run.id FROM run
    WHERE run.project = ? AND {_COMPLETE} AND {_AFTER_RUN_KEY}
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT 1
"""  # noqa: S608

_SET_BASELINE = "UPDATE run SET baseline_id = ? WHERE id = ?"

# What `compare` reads of a run, in stored order, along `idx_result_run_id`.
_SELECT_COMPARED_RESULTS = (
    "SELECT id, test_case_id, outcome FROM result WHERE run_id = ? ORDER BY id"
)

# A baseline's own still-failing streaks, which its run's extend.
_SELECT_STREAKS = """
    SELECT test_case_id, streak, streak_since FROM result_change
    WHERE run_id = ? AND change = 'still_failing'
"""

_INSERT_RESULT_CHANGE = """
    INSERT INTO result_change (
        run_id, test_case_id, change, ordinal, result_id, was, streak, streak_since
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def _count_changes(run_count: int) -> str:
    """How many tests of each change each of `run_count` runs' comparisons
    record, from `idx_result_change_run` alone. Only the literal `?` marker
    is interpolated."""
    placeholders = ",".join("?" * run_count)
    return f"""
        SELECT run_id, change, COUNT(*) FROM result_change
        WHERE run_id IN ({placeholders})
        GROUP BY run_id, change
    """  # noqa: S608


# `_row_to_change_entry`'s columns: the catalogue's identity, the change,
# the result's outcome and duration -- NULL for a missing test, whose
# `result_id` is -- and the stored `was`, ordinal and streak.
_SELECT_CHANGES = """
    SELECT tc.node_id, tc.file_path, tc.class_name, tc.function_name, tc.param_id,
           rc.change, r.outcome, rc.was, r.duration, rc.ordinal, rc.streak, rc.streak_since
    FROM result_change rc
    JOIN test_case tc ON tc.id = rc.test_case_id
    LEFT JOIN result r ON r.id = rc.result_id
    WHERE rc.run_id = ?
"""

# A change's place in `CHANGE_ORDER`, as SQL. Only the module's constants
# are interpolated.
_CHANGE_RANK = (
    "CASE rc.change "
    + " ".join(f"WHEN '{change}' THEN {rank}" for rank, change in enumerate(CHANGE_ORDER))
    + " END"
)


def _list_changes(change_count: int) -> str:
    """A page of one run's changes of `change_count` kinds, bound after the
    run id, in `CHANGE_ORDER`, then by ordinal. With one kind the order is
    the ordinal alone, so `idx_result_change_run` yields the rows in order
    without a sort. Only the module's constants and the literal `?` marker
    are interpolated."""
    placeholders = ",".join("?" * change_count)
    order = "rc.ordinal" if change_count == 1 else f"{_CHANGE_RANK}, rc.ordinal"
    return f"""
        {_SELECT_CHANGES} AND rc.change IN ({placeholders})
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """  # noqa: S608


# A run's changed results' positions, bound after the run id; its
# `baseline_id`, read in the same snapshot, says whether it was compared with
# another run at all.
_SELECT_RESULT_CHANGE_POSITIONS = f"""
    SELECT ordinal, change FROM result_change
    WHERE run_id = ? AND change IN ({", ".join(f"'{change}'" for change in sorted(RESULT_CHANGES))})
    ORDER BY ordinal
"""  # noqa: S608

_SELECT_BASELINE_ID = "SELECT baseline_id FROM run WHERE id = ?"

# One result's position -- how many of its run's results were stored before
# it, counted along `idx_result_run_id` alone -- and its change, through
# `result_change`'s primary key.
_SELECT_RESULT_CHANGE = """
    SELECT (SELECT COUNT(*) FROM result earlier
             WHERE earlier.run_id = r.run_id AND earlier.id < r.id),
           rc.change, rc.was, rc.streak, rc.streak_since
    FROM result r
    LEFT JOIN result_change rc ON rc.run_id = r.run_id AND rc.test_case_id = r.test_case_id
    WHERE r.run_id = ? AND r.node_id = ?
"""

# Conflict target is `(project, node_id)`, the catalogue's identity key, so
# two projects never share a row. Every right-hand side reads the row as it
# was before the update, so the order of the assignments does not matter.
#
# The row holds the one decomposition of `node_id` -- file, class, function,
# parameter -- that every result of it reads through `test_case_id`. It
# follows the newest run, under the same guard as `last_seen_run_id`, so a
# late report of an older run never changes what newer runs read.
_UPSERT_TEST_CASE = """
    INSERT INTO test_case (
        project, node_id, file_path, class_name, function_name,
        param_id, first_seen_at, last_seen_at, last_seen_run_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(project, node_id) DO UPDATE SET
        file_path        = CASE WHEN excluded.last_seen_at > test_case.last_seen_at
                                THEN excluded.file_path ELSE test_case.file_path END,
        class_name       = CASE WHEN excluded.last_seen_at > test_case.last_seen_at
                                THEN excluded.class_name ELSE test_case.class_name END,
        function_name    = CASE WHEN excluded.last_seen_at > test_case.last_seen_at
                                THEN excluded.function_name ELSE test_case.function_name END,
        param_id         = CASE WHEN excluded.last_seen_at > test_case.last_seen_at
                                THEN excluded.param_id ELSE test_case.param_id END,
        last_seen_run_id = CASE WHEN excluded.last_seen_at > test_case.last_seen_at
                                THEN excluded.last_seen_run_id ELSE test_case.last_seen_run_id END,
        first_seen_at    = MIN(test_case.first_seen_at, excluded.first_seen_at),
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

# `ON CONFLICT(<primary key>) DO NOTHING` makes metadata write-once: a
# replay, even one carrying different values, never changes a stored row,
# and the first report to carry a key keeps it whichever source a later row
# comes from. The plugin sends a file's keys from the start report on and the
# session's own values only in the finish report, so a file's value wins.
# Not `INSERT OR IGNORE`, which would also skip a row that fails a CHECK
# constraint and commit the session without it.
_INSERT_METADATA_FILE = """
    INSERT INTO run_metadata_file (run_id, source_file, content_type, status)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(run_id, source_file) DO NOTHING
"""

# A new key is also dropped once its run holds `MAX_METADATA_ENTRIES` rows:
# each report is bounded on its way in, but a run can be sent any number of
# reports, and its metadata is returned whole. The count sees the rows this
# statement's earlier executions inserted, and is served by the primary
# key's index. An upsert on a `SELECT` needs a `WHERE` for the parser to
# tell `ON CONFLICT` from a join's `ON`.
_INSERT_METADATA_ENTRY = """
    INSERT INTO run_metadata (run_id, key, name, value, status, source, source_file, declared)
    SELECT ?, ?, ?, ?, ?, ?, ?, ?
    WHERE (SELECT COUNT(*) FROM run_metadata WHERE run_id = ?) < ?
    ON CONFLICT(run_id, key) DO NOTHING
"""

# `_row_to_metadata_entry`'s seven columns. `ORDER BY key` walks the primary
# key's index for the run, so no sort is needed; `BINARY` compares UTF-8
# bytes, whose order is code point order.
_SELECT_RUN_METADATA = """
    SELECT key, name, value, status, source, source_file, declared
    FROM run_metadata WHERE run_id = ?
    ORDER BY key
"""

_SELECT_RUN_METADATA_FILES = """
    SELECT source_file, content_type, status
    FROM run_metadata_file WHERE run_id = ?
    ORDER BY source_file
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
# read as in `_LIST_EXECUTION_COLUMNS`. `failure_repr` and `traceback` are never
# loaded: `substr(x, 1, 0)` is '' for a stored value and NULL for none, which
# is all the emptiness rule needs of them. No captured-output column is
# selected. Binds `_LIST_MESSAGE_PREFIX_BYTES`, then the run id.
_SELECT_RESULT_LIST = f"""
    SELECT {_RESULT_COLUMNS},
           r.failure_type,
           CASE WHEN r.failure_message = '' THEN ''
                ELSE substr(CAST(r.failure_message AS BLOB), 1, ?) END,
           r.failure_message_truncated,
           r.failure_path, r.failure_lineno,
           substr(r.failure_repr, 1, 0), r.failure_repr_truncated,
           substr(r.traceback, 1, 0), r.traceback_truncated,
           r.skip_reason, r.skip_reason_truncated,
           r.xfail_reason, r.xfail_reason_truncated
    FROM result r
    JOIN test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = ?
"""  # noqa: S608

_LIST_RESULTS = f"{_SELECT_RESULT_LIST} ORDER BY r.id LIMIT ? OFFSET ?"


def _list_results_with_outcomes(outcome_count: int) -> str:
    """`_LIST_RESULTS` narrowed to the results holding one of
    `outcome_count` outcomes, bound after the run id. `idx_result_run_id`
    still finds the run's results, in `r.id` order, and the outcome is
    checked on each. Only the literal `?` marker is interpolated."""
    placeholders = ",".join("?" * outcome_count)
    return f"{_SELECT_RESULT_LIST} AND r.outcome IN ({placeholders}) ORDER BY r.id LIMIT ? OFFSET ?"


# `list_history`' SELECT: the project and `node_id` resolve through the
# unique `idx_test_case_project_node_id` to one `test_case.id`, then
# `idx_result_test_case_id` finds that test's results, then `run` is read by
# primary key, and each result's change by `result_change`'s. Same execution
# columns and total order as `_LIST_RUNS`.
_LIST_HISTORY = f"""
    SELECT {_LIST_EXECUTION_COLUMNS}, run.last_contact_at, r.outcome, r.duration, rc.change
    FROM test_case tc
    JOIN result r ON r.test_case_id = tc.id
    JOIN run ON run.id = r.run_id
    LEFT JOIN result_change rc ON rc.run_id = r.run_id AND rc.test_case_id = r.test_case_id
    WHERE tc.project = ? AND tc.node_id = ?
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT ? OFFSET ?
"""  # noqa: S608

_LIST_HISTORY_AFTER = f"""
    SELECT {_LIST_EXECUTION_COLUMNS}, run.last_contact_at, r.outcome, r.duration, rc.change
    FROM test_case tc
    JOIN result r ON r.test_case_id = tc.id
    JOIN run ON run.id = r.run_id
    LEFT JOIN result_change rc ON rc.run_id = r.run_id AND rc.test_case_id = r.test_case_id
    WHERE tc.project = ? AND tc.node_id = ? AND {_AFTER_RUN_KEY}
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT ? OFFSET ?
"""  # noqa: S608

_SELECT_TEST_CASE = """
    SELECT node_id, file_path, class_name, function_name, param_id,
           first_seen_at, last_seen_at, last_seen_run_id
    FROM test_case WHERE project = ? AND node_id = ?
"""

# Ordered by `key`, i.e. alphabetically by section name.
_LIST_SETTINGS = """
    SELECT project, namespace, key, value, updated_at
    FROM project_setting WHERE project = ? AND namespace = ? ORDER BY key
"""

_PROBE_SETTING_EXISTS = """
    SELECT 1 FROM project_setting WHERE project = ? AND namespace = ? AND key = ?
"""

_COUNT_SETTINGS = "SELECT COUNT(*) FROM project_setting WHERE project = ? AND namespace = ?"

_UPSERT_SETTING = """
    INSERT INTO project_setting (project, namespace, key, value, updated_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT (project, namespace, key) DO UPDATE SET
        value = excluded.value,
        updated_at = excluded.updated_at
"""

# `_DELETE_SETTING`: `rowcount == 1` is the "it existed" answer.
_DELETE_SETTING = "DELETE FROM project_setting WHERE project = ? AND namespace = ? AND key = ?"

# Projects, keyed by name. `ON CONFLICT DO NOTHING` lets `create_project`
# tell a taken name from any other failure by the row count alone.
_INSERT_PROJECT = """
    INSERT INTO project (name, created_at) VALUES (?, ?)
    ON CONFLICT (name) DO NOTHING
"""

_SELECT_PROJECT = "SELECT name, created_at FROM project WHERE name = ?"

_LIST_PROJECTS = "SELECT name, created_at FROM project ORDER BY name"

# Members, keyed by project and user. A project's members and one member's
# role are read along the primary key, a user's memberships along
# `idx_project_member_account`; `BINARY` order is code point order.
_SELECT_MEMBER_ROLE = "SELECT role FROM project_member WHERE project = ? AND account = ?"

_LIST_MEMBERS = """
    SELECT project, account, role FROM project_member WHERE project = ? ORDER BY account
"""

_LIST_MEMBERSHIPS = """
    SELECT project, account, role FROM project_member WHERE account = ? ORDER BY project
"""

_UPSERT_MEMBER = """
    INSERT INTO project_member (project, account, role) VALUES (?, ?, ?)
    ON CONFLICT (project, account) DO UPDATE SET role = excluded.role
"""

# `rowcount == 1` is the "it was a member" answer.
_DELETE_MEMBER = "DELETE FROM project_member WHERE project = ? AND account = ?"

# The per-run aggregate read. It filters on `result.run_id` and joins
# `test_case` by primary key; `file_path` is only read, so it needs no index.
# Ordered by `r.id`, the order the results were recorded in, rather than
# whatever order the index the planner picks yields: `UNIQUE (run_id,
# node_id)`'s would yield node-id order.
_SELECT_RUN_CASE_OUTCOMES = """
    SELECT tc.file_path, r.outcome
    FROM result r
    JOIN test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = ?
    ORDER BY r.id
"""


def _count_outcomes(run_count: int) -> str:
    """How many results of each outcome each of `run_count` runs holds,
    read along `idx_result_run_id`. Only the literal `?` marker is
    interpolated."""
    placeholders = ",".join("?" * run_count)
    return f"""
        SELECT run_id, outcome, COUNT(*) FROM result
        WHERE run_id IN ({placeholders})
        GROUP BY run_id, outcome
    """  # noqa: S608


# Users and tokens. A user name is its key, and a token is found by its
# digest through the `UNIQUE` constraint's index. `ON CONFLICT DO NOTHING`
# lets `create_user` tell a taken name from any other failure by the row
# count alone.
_PROBE_ANY_USER = "SELECT EXISTS (SELECT 1 FROM account)"

_INSERT_USER = """
    INSERT INTO account (name, admin, disabled, created_at) VALUES (?, ?, 0, ?)
    ON CONFLICT (name) DO NOTHING
"""

_USER_COLUMNS = "name, admin, disabled, created_at, password_hash IS NOT NULL"

# Only on a database the local store did not make, and only while it has no
# user: one statement, so it cannot race another first start.
_INSERT_FIRST_ADMIN = """
    INSERT INTO account (name, admin, disabled, created_at, password_hash)
    SELECT ?, 1, 0, ?, ?
    WHERE NOT EXISTS (SELECT 1 FROM account)
      AND NOT EXISTS (SELECT 1 FROM meta WHERE key = 'origin' AND value = 'local')
"""

_SELECT_PASSWORD_HASH = "SELECT password_hash FROM account WHERE name = ? AND disabled = 0"  # noqa: S105

_SET_PASSWORD = "UPDATE account SET password_hash = ? WHERE name = ?"  # noqa: S105

# The compare-and-set a password change checked against the current
# password makes: it loses to any change made while it was checked.
_REPLACE_PASSWORD = """
    UPDATE account SET password_hash = ?
    WHERE name = ? AND password_hash = ? AND disabled = 0
"""  # noqa: S105

# Login tokens are the only ones with an expiry.
_REVOKE_LOGIN_TOKENS = """
    UPDATE access_token SET revoked_at = ?
    WHERE account = ? AND expires_at IS NOT NULL AND revoked_at IS NULL
"""  # noqa: S105

# Inserts nothing unless the user is enabled and still has the hash the
# password was checked against.
_INSERT_LOGIN_TOKEN = """
    INSERT INTO access_token (
        account, digest, label, can_read, can_record, can_manage, can_admin,
        created_at, expires_at
    )
    SELECT name, ?, ?, 1, 0, 1, admin, ?, ? FROM account
    WHERE name = ? AND password_hash = ? AND disabled = 0
"""  # noqa: S105

# Never the token just made, whatever its expiry.
_DELETE_EXPIRED_LOGIN_TOKENS = """
    DELETE FROM access_token
    WHERE account = ? AND expires_at IS NOT NULL AND expires_at <= ? AND id <> ?
"""  # noqa: S105

_SELECT_USER = f"SELECT {_USER_COLUMNS} FROM account WHERE name = ?"  # noqa: S608

_LIST_USERS = f"SELECT {_USER_COLUMNS} FROM account ORDER BY name"  # noqa: S608

# A NULL parameter leaves its column as it is.
_UPDATE_USER = """
    UPDATE account SET admin = COALESCE(?, admin), disabled = COALESCE(?, disabled)
    WHERE name = ?
"""

_INSERT_TOKEN = """
    INSERT INTO access_token (
        account, digest, label, can_read, can_record, can_manage, can_admin, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""  # noqa: S105

_TOKEN_COLUMNS = (
    "id, account, label, can_read, can_record, can_manage, can_admin,"  # noqa: S105
    " created_at, revoked_at, expires_at"
)

_SELECT_TOKEN = f"SELECT {_TOKEN_COLUMNS} FROM access_token WHERE id = ?"  # noqa: S608

_LIST_TOKENS = f"SELECT {_TOKEN_COLUMNS} FROM access_token ORDER BY id"  # noqa: S608

_LIST_USER_TOKENS = f"""
    SELECT {_TOKEN_COLUMNS} FROM access_token WHERE account = ? ORDER BY id
"""  # noqa: S608

# `account IS COALESCE(?, account)`: with no user given, any token.
_REVOKE_TOKEN = """
    UPDATE access_token SET revoked_at = ?
    WHERE id = ? AND revoked_at IS NULL AND account IS COALESCE(?, account)
"""  # noqa: S105

_AUTHENTICATE = """
    SELECT a.name, a.admin, t.can_read, t.can_record, t.can_manage, t.can_admin,
           t.id, t.expires_at
    FROM access_token t
    JOIN account a ON a.name = t.account
    WHERE t.digest = ? AND t.revoked_at IS NULL AND a.disabled = 0
      AND (t.expires_at IS NULL OR t.expires_at > ?)
"""


# The ids an `INTEGER PRIMARY KEY` can hold; a lookup by any other matches
# nothing.
_MAX_ID = 2**63 - 1


def _scope_columns(scopes: frozenset[str]) -> tuple[int, int, int, int]:
    """The `can_read`, `can_record`, `can_manage` and `can_admin` values for
    `scopes`."""
    return (
        int(READ_SCOPE in scopes),
        int(RECORD_SCOPE in scopes),
        int(MANAGE_SCOPE in scopes),
        int(ADMIN_SCOPE in scopes),
    )


def _decode_scopes(
    can_read: object, can_record: object, can_manage: object, can_admin: object
) -> frozenset[str]:
    held = zip(
        (READ_SCOPE, RECORD_SCOPE, MANAGE_SCOPE, ADMIN_SCOPE),
        (can_read, can_record, can_manage, can_admin),
    )
    return frozenset(scope for scope, flag in held if flag)


def _check_member_row(project: str, role: str) -> None:
    """Refuse what no member row may hold: `default`, whose every user is an
    editor without one, or a role outside `ROLES`."""
    if project == DEFAULT_PROJECT:
        raise ValueError("default takes no members: every user is an editor of it")
    check_role(role)


def _row_to_membership(row: tuple[object, ...]) -> Membership:
    project, account, role = row
    return Membership(project=cast(str, project), user=cast(str, account), role=cast(str, role))


def _row_to_user(row: tuple[object, ...]) -> User:
    name, admin, disabled, created_at, has_password = row
    return User(
        name=cast(str, name),
        admin=bool(admin),
        disabled=bool(disabled),
        created_at=_datetime(created_at),
        has_password=bool(has_password),
    )


def _row_to_token(row: tuple[object, ...]) -> Token:
    (
        token_id,
        account,
        label,
        can_read,
        can_record,
        can_manage,
        can_admin,
        created_at,
        revoked_at,
        expires_at,
    ) = row
    return Token(
        id=cast(int, token_id),
        user=cast(str, account),
        label=cast(str, label),
        scopes=_decode_scopes(can_read, can_record, can_manage, can_admin),
        created_at=_datetime(created_at),
        revoked_at=_opt_datetime(revoked_at),
        expires_at=_opt_datetime(expires_at),
    )


def _opt_isoformat_utc(moment: datetime | None) -> str | None:
    return None if moment is None else isoformat_utc(moment)


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


def _decode_baseline(row: Sequence[object]) -> BaselineRef | None:
    """The three `_BASELINE_COLUMNS`; `None` for a run with no baseline."""
    baseline_id, started_at, branch = row
    if baseline_id is None:
        return None
    return BaselineRef(
        run_id=cast(str, baseline_id),
        started_at=_datetime(started_at),
        branch=cast("str | None", branch),
    )


def _row_to_run_list_entry(row: tuple[object, ...]) -> RunListEntry:
    return RunListEntry.from_execution(
        _decode_execution(row[:12]),
        last_contact_at=_opt_datetime(row[12]),
        recorded_by=cast("str | None", row[13]),
        baseline=_decode_baseline(row[14:17]),
    )


def _row_to_history_entry(row: tuple[object, ...]) -> HistoryEntry:
    last_contact_at, outcome, duration, change = row[12:]
    return HistoryEntry.from_execution(
        _decode_execution(row[:12]),
        last_contact_at=_opt_datetime(last_contact_at),
        outcome=cast(str, outcome),
        duration=cast("float | None", duration),
        change=cast("str | None", change),
    )


def _decode_streak(runs: object, since: object) -> Streak | None:
    return None if runs is None else Streak(runs=cast(int, runs), since=cast(str, since))


def _row_to_change_entry(row: tuple[object, ...]) -> ChangeEntry:
    """A `_SELECT_CHANGES` row. A missing test has no result, so no
    position either."""
    change, outcome, was, duration, ordinal, streak, streak_since = row[5:]
    return ChangeEntry(
        identity=_decode_identity(row[:5]),
        change=cast(str, change),
        outcome=cast("str | None", outcome),
        was=cast("str | None", was),
        duration=cast("float | None", duration),
        position=None if outcome is None else cast(int, ordinal),
        streak=_decode_streak(streak, streak_since),
    )


def _change_rows(run_id: str, rows: Sequence[ChangeRow]) -> list[tuple[object, ...]]:
    """The `_INSERT_RESULT_CHANGE` parameters of a comparison's rows."""
    return [
        (
            run_id,
            row.test_case_id,
            row.change,
            row.ordinal,
            row.result_id,
            row.was,
            None if row.streak is None else row.streak.runs,
            None if row.streak is None else row.streak.since,
        )
        for row in rows
    ]


def _fix_comparison(conn: sqlite3.Connection, run_id: str, project: str) -> None:
    """Compare the run the report being written gave its exit status with
    its baseline, inside that report's transaction and after its results:
    choose the baseline, record it, and store the rows `compare` returns.
    The report is the only one that ever gives the run an exit status, so
    this runs once per run."""
    execution = _decode_execution(conn.execute(_SELECT_RUN, (run_id,)).fetchone()[:12])
    after = (isoformat_utc(execution.started_at), run_id)
    branch = None if execution.vcs is None else execution.vcs.branch
    found = None
    if branch is not None:
        found = conn.execute(_BRANCH_BASELINE, (project, branch, *after)).fetchone()
    if found is None:
        found = conn.execute(_PROJECT_BASELINE, (project, *after)).fetchone()
    if found is None:
        return
    baseline_id = cast(str, found[0])
    conn.execute(_SET_BASELINE, (baseline_id, run_id))
    results = conn.execute(_SELECT_COMPARED_RESULTS, (run_id,)).fetchall()
    baseline_results = conn.execute(_SELECT_COMPARED_RESULTS, (baseline_id,)).fetchall()
    streaks = {
        cast(int, test_case_id): Streak(runs=cast(int, runs), since=cast(str, since))
        for test_case_id, runs, since in conn.execute(_SELECT_STREAKS, (baseline_id,))
    }
    rows = compare(
        results,
        ((test_case_id, outcome) for _id, test_case_id, outcome in baseline_results),
        streaks,
        baseline_id=baseline_id,
        ran_to_its_end=ran_to_its_end(execution),
    )
    if rows:
        conn.executemany(_INSERT_RESULT_CHANGE, _change_rows(run_id, rows))


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


def _row_to_metadata_entry(row: tuple[object, ...]) -> MetadataEntry:
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


def _run_key_params(after: RunKey | None) -> tuple[str, ...]:
    """`_AFTER_RUN_KEY`'s two parameters, or none without a key."""
    return () if after is None else (isoformat_utc(after.started_at), after.run_id)


def _row_to_metadata_file(row: tuple[object, ...]) -> MetadataFile:
    """A `_SELECT_RUN_METADATA_FILES` row."""
    source_file, content_type, status = row
    return MetadataFile(
        source_file=cast(str, source_file),
        content_type=cast(str, content_type),
        status=cast(str, status),
    )


def _row_to_catalogue_entry(row: tuple[object, ...]) -> CatalogueEntry:
    first_seen_at, last_seen_at, last_seen_run_id = row[5:]
    return CatalogueEntry(
        identity=_decode_identity(row[:5]),
        first_seen_at=_datetime(first_seen_at),
        last_seen_at=_datetime(last_seen_at),
        last_seen_run_id=cast("str | None", last_seen_run_id),
    )


def _catalogue_rows(
    project: str, execution: Execution, results: Sequence[Result]
) -> list[tuple[str, str, str, str | None, str, str | None, str, str, str]]:
    started_at = isoformat_utc(execution.started_at)
    run_id = execution.identity.value
    # Keyed by node_id so a report carrying the same node id twice (the
    # service rejects that) still yields one upsert row rather than a batch
    # containing a duplicate key.
    by_node_id: dict[str, CaseIdentity] = {
        result.identity.node_id: result.identity for result in results
    }
    return [
        (
            project,
            identity.node_id,
            identity.file_path,
            identity.class_name,
            identity.function_name,
            identity.param_id,
            started_at,  # first_seen_at -- the MIN clause decides on conflict
            started_at,  # last_seen_at -- the MAX/CASE clause decides on conflict
            run_id,  # last_seen_run_id
        )
        for identity in by_node_id.values()
    ]


def _metadata_file_rows(run_id: str, metadata: RunMetadata) -> list[tuple[str, str, str, str]]:
    return [(run_id, file.source_file, file.content_type, file.status) for file in metadata.files]


def _metadata_entry_rows(run_id: str, metadata: RunMetadata) -> list[tuple[object, ...]]:
    return [
        (
            run_id,
            entry.key,
            entry.name,
            entry.value,
            entry.status,
            entry.source,
            entry.source_file,
            1 if entry.declared else 0,
            run_id,
            MAX_METADATA_ENTRIES,
        )
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
            _opt_isoformat_utc(result.started_at),
            _opt_isoformat_utc(result.finished_at),
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


def _resolve_test_case_ids(
    conn: sqlite3.Connection, project: str, node_ids: Sequence[str]
) -> dict[str, int]:
    """The catalogue row id of each node id in `project`. Selecting by node
    id alone would find another project's row of the same node id, and
    attach this run's results to it."""
    resolved: dict[str, int] = {}
    for start in range(0, len(node_ids), _MAX_PLACEHOLDERS):
        batch = node_ids[start : start + _MAX_PLACEHOLDERS]
        # `placeholders` is built only from the literal `?` marker, repeated
        # once per batch item -- no value is ever interpolated into the SQL
        # text itself, so this is not the injection pattern S608 flags.
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT id, node_id FROM test_case WHERE project = ? AND node_id IN ({placeholders})",  # noqa: S608
            (project, *batch),
        ).fetchall()
        for row_id, row_node_id in rows:
            resolved[cast(str, row_node_id)] = cast(int, row_id)
    return resolved


def _row_to_project(row: tuple[object, ...]) -> Project:
    name, created_at = row
    return Project(name=cast(str, name), created_at=_datetime(created_at))


def _row_to_project_setting(row: tuple[object, ...]) -> ProjectSetting:
    project, namespace, key, value, updated_at = row
    return ProjectSetting(
        project=cast(str, project),
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

    One connection per store, shared across threads
    (`check_same_thread=False`) and opened via `open_database`, which sets
    file permissions and WAL. Every read, every write transaction and
    `close` hold `self._lock` to serialise the threads sharing it;
    `BEGIN IMMEDIATE` and the busy timeout handle another connection on the
    same file. Neither substitutes for the other.
    """

    def __init__(self, path: Path, *, local: bool = False) -> None:
        """Open the database at `path`; `local` says pytest-vantage's local
        store is opening it (`open_database`)."""
        self._conn = open_database(path, local=local)
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
        project: str,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
        recorded_by: str | None = None,
    ) -> bool:
        # One transaction, fixed order: project and run probes, run upsert,
        # catalogue upsert, surrogate-key resolve, result insert, metadata
        # inserts. The order is required -- `PRAGMA foreign_keys=ON` is set on
        # every connection, so each row's `run_id`/`test_case_id` referent
        # must exist first.
        run_id = execution.identity.value
        with self._write_transaction() as conn:
            # Raising rolls the transaction back, though nothing is written
            # yet.
            if conn.execute(_PROBE_PROJECT, (project,)).fetchone() is None:
                raise UnknownProjectError(f"there is no project named {project!r}")
            stored = conn.execute(_SELECT_RUN_STATE, (run_id,)).fetchone()
            if stored is not None and stored[1] != recorded_by:
                raise ForeignRunError(f"run {run_id} was recorded by another user")
            if stored is not None and stored[2] != project:
                raise ProjectMismatchError(f"run {run_id} was recorded in another project")
            if stored is not None and stored[0] is not None:
                # A finished run is final: a report reaching it later is a
                # replay and adds nothing, whatever results it carries.
                return False
            created = stored is None

            conn.execute(
                _UPSERT_RUN,
                (
                    run_id,
                    project,
                    isoformat_utc(received_at),
                    isoformat_utc(received_at),
                    isoformat_utc(execution.started_at),
                    _opt_isoformat_utc(execution.finished_at),
                    execution.exit_status,
                    1 if execution.interrupted else 0,
                    execution.interrupt_reason,
                    *_vcs_columns(execution.vcs),
                    recorded_by,
                ),
            )

            if results:
                catalogue_rows = _catalogue_rows(project, execution, results)
                conn.executemany(_UPSERT_TEST_CASE, catalogue_rows)
                test_case_ids = _resolve_test_case_ids(
                    conn, project, [row[1] for row in catalogue_rows]
                )
                conn.executemany(_INSERT_RESULT, _result_rows(execution, results, test_case_ids))

            if metadata.files:
                conn.executemany(_INSERT_METADATA_FILE, _metadata_file_rows(run_id, metadata))
            if metadata.entries:
                conn.executemany(_INSERT_METADATA_ENTRY, _metadata_entry_rows(run_id, metadata))

            # A finished run returned above, so a report with an exit status
            # is the one that gives the run its own.
            if execution.exit_status is not None:
                _fix_comparison(conn, run_id, project)
        return created

    def get_execution(self, execution_id: str) -> Execution | None:
        row = self._fetchone(_SELECT_RUN, (execution_id,))
        return None if row is None else _decode_execution(row[:12])

    def touch_last_contact(self, execution_id: str, contacted_at: datetime) -> bool:
        with self._lock:
            formatted = isoformat_utc(contacted_at)
            cursor = self._conn.execute(_TOUCH_LAST_CONTACT, (formatted, execution_id, formatted))
            return cursor.rowcount == 1

    def count_executions(self) -> int:
        return self._count("SELECT COUNT(*) FROM run")

    def get_results(self, execution_id: str) -> Sequence[Result]:
        rows = self._fetchall(_SELECT_RESULTS_FOR_RUN, (execution_id,))
        return [_row_to_result(row) for row in rows]

    def count_results(self) -> int:
        return self._count("SELECT COUNT(*) FROM result")

    def get_catalogue_entry(self, node_id: str, *, project: str) -> CatalogueEntry | None:
        row = self._fetchone(_SELECT_TEST_CASE, (project, node_id))
        return None if row is None else _row_to_catalogue_entry(row)

    def list_runs(
        self, *, project: str, limit: int, offset: int, after: RunKey | None = None
    ) -> Page[RunListEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        sql = _LIST_RUNS if after is None else _LIST_RUNS_AFTER
        params = [
            _LIST_SUBJECT_PREFIX_BYTES,
            project,
            *_run_key_params(after),
            page_limit + 1,
            offset,
        ]
        rows = self._fetchall(sql, params)
        return _page(rows, page_limit, _row_to_run_list_entry)

    def list_runs_with_metadata_horizon(
        self,
        *,
        project: str,
        filters: Sequence[tuple[str, str]],
        limit: int,
        offset: int,
        after: RunKey | None = None,
    ) -> tuple[Page[RunListEntry], tuple[int, ...]]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        # A repeated pair narrows nothing further; dropping it keeps the query
        # one seek per distinct pair.
        pairs = list(dict.fromkeys(filters))
        if pairs:
            sql = _list_runs_by_metadata(len(pairs), after=after is not None)
        else:
            sql = _LIST_RUNS if after is None else _LIST_RUNS_AFTER
        params = [
            _LIST_SUBJECT_PREFIX_BYTES,
            *(part for pair in pairs for part in pair),
            project,
            *_run_key_params(after),
            page_limit + 1,
            offset,
        ]
        with self._read_snapshot() as conn:
            rows = conn.execute(sql, params).fetchall()
            predating = tuple(
                int(
                    conn.execute(
                        _COUNT_RUNS_PREDATING_KEY, (project, project, key, project)
                    ).fetchone()[0]
                )
                for key in dict.fromkeys(key for key, _value in pairs)
            )
        return _page(rows, page_limit, _row_to_run_list_entry), predating

    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        row = self._fetchone(_SELECT_RUN, (execution_id,))
        if row is None:
            return None
        execution = _decode_execution(row[:12])
        return RunDetail(
            execution=execution,
            last_contact_at=_opt_datetime(row[12]),
            recorded_by=cast("str | None", row[13]),
            project=cast(str, row[14]),
            comparison=comparison_of(execution, _decode_baseline(row[15:18])),
        )

    def get_run_metadata(self, execution_id: str) -> RunMetadata | None:
        with self._read_snapshot() as conn:
            if conn.execute(_PROBE_RUN_EXISTS, (execution_id,)).fetchone() is None:
                return None
            file_rows = conn.execute(_SELECT_RUN_METADATA_FILES, (execution_id,)).fetchall()
            entry_rows = conn.execute(_SELECT_RUN_METADATA, (execution_id,)).fetchall()
        return RunMetadata(
            files=tuple(_row_to_metadata_file(row) for row in file_rows),
            entries=tuple(_row_to_metadata_entry(row) for row in entry_rows),
        )

    def list_results(
        self,
        execution_id: str,
        *,
        limit: int,
        offset: int,
        outcomes: Collection[str] | None = None,
    ) -> Page[ResultListEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        wanted: tuple[str, ...] = ()
        if outcomes is None:
            sql = _LIST_RESULTS
        else:
            # A word outside the vocabulary matches no stored row anyway, so
            # the statement binds at most one marker per outcome.
            wanted = tuple(sorted(OUTCOMES.intersection(outcomes)))
            sql = _list_results_with_outcomes(len(wanted))
        rows = self._fetchall(
            sql,
            (_LIST_MESSAGE_PREFIX_BYTES, execution_id, *wanted, page_limit + 1, offset),
        )
        return _page(rows, page_limit, _row_to_result_list_entry)

    def get_result(self, execution_id: str, *, node_id: str) -> Result | None:
        row = self._fetchone(_SELECT_RESULT, (execution_id, node_id))
        return None if row is None else _row_to_result(row)

    def list_history(
        self,
        *,
        project: str,
        node_id: str,
        limit: int,
        offset: int,
        after: RunKey | None = None,
    ) -> Page[HistoryEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        rows = self._fetchall(
            _LIST_HISTORY if after is None else _LIST_HISTORY_AFTER,
            (
                _LIST_SUBJECT_PREFIX_BYTES,
                project,
                node_id,
                *_run_key_params(after),
                page_limit + 1,
                offset,
            ),
        )
        return _page(rows, page_limit, _row_to_history_entry)

    def list_settings(self, namespace: str, *, project: str) -> Sequence[ProjectSetting]:
        rows = self._fetchall(_LIST_SETTINGS, (project, namespace))
        return tuple(_row_to_project_setting(row) for row in rows)

    def upsert_setting(
        self,
        namespace: str,
        key: str,
        *,
        project: str,
        value: str,
        updated_at: datetime,
        max_keys: int | None = None,
    ) -> bool:
        # Probe first, as `record_session` does: `rowcount` cannot
        # distinguish insert from update under `DO UPDATE`. The count shares
        # the write transaction, so no other writer can add a key between it
        # and the insert; raising rolls the transaction back.
        formatted = isoformat_utc(updated_at)
        with self._write_transaction() as conn:
            if conn.execute(_PROBE_PROJECT, (project,)).fetchone() is None:
                raise UnknownProjectError(f"there is no project named {project!r}")
            probe = (project, namespace, key)
            created = conn.execute(_PROBE_SETTING_EXISTS, probe).fetchone() is None
            if created and max_keys is not None:
                (held,) = conn.execute(_COUNT_SETTINGS, (project, namespace)).fetchone()
                if held >= max_keys:
                    raise NamespaceFullError(f"{namespace!r} already holds {held} keys")
            conn.execute(_UPSERT_SETTING, (project, namespace, key, value, formatted))
        return created

    def delete_setting(self, namespace: str, key: str, *, project: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(_DELETE_SETTING, (project, namespace, key))
            return cursor.rowcount == 1

    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]:
        rows = self._fetchall(_SELECT_RUN_CASE_OUTCOMES, (execution_id,))
        return tuple((cast(str, file_path), cast(str, outcome)) for file_path, outcome in rows)

    def count_outcomes(self, execution_ids: Sequence[str]) -> Mapping[str, Mapping[str, int]]:
        ids = list(dict.fromkeys(execution_ids))
        counts: dict[str, dict[str, int]] = {}
        if not ids:
            return counts
        with self._read_snapshot() as conn:
            for start in range(0, len(ids), _MAX_PLACEHOLDERS):
                batch = ids[start : start + _MAX_PLACEHOLDERS]
                for run_id, outcome, count in conn.execute(_count_outcomes(len(batch)), batch):
                    counts.setdefault(run_id, {})[outcome] = count
        return counts

    def count_changes(self, execution_ids: Sequence[str]) -> Mapping[str, Mapping[str, int]]:
        ids = list(dict.fromkeys(execution_ids))
        counts: dict[str, dict[str, int]] = {}
        if not ids:
            return counts
        with self._read_snapshot() as conn:
            for start in range(0, len(ids), _MAX_PLACEHOLDERS):
                batch = ids[start : start + _MAX_PLACEHOLDERS]
                for run_id, change, count in conn.execute(_count_changes(len(batch)), batch):
                    counts.setdefault(run_id, {})[change] = count
        return counts

    def list_changes(
        self,
        execution_id: str,
        *,
        limit: int,
        offset: int,
        changes: Collection[str] | None = None,
    ) -> Page[ChangeEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        wanted = wanted_changes(changes)
        if not wanted:
            return Page(items=(), has_more=False)
        rows = self._fetchall(
            _list_changes(len(wanted)), (execution_id, *wanted, page_limit + 1, offset)
        )
        return _page(rows, page_limit, _row_to_change_entry)

    def get_run_changes(self, execution_id: str) -> Sequence[tuple[int, str]] | None:
        with self._read_snapshot() as conn:
            found = conn.execute(_SELECT_BASELINE_ID, (execution_id,)).fetchone()
            if found is None or found[0] is None:
                return None
            rows = conn.execute(_SELECT_RESULT_CHANGE_POSITIONS, (execution_id,)).fetchall()
        return tuple((cast(int, ordinal), cast(str, change)) for ordinal, change in rows)

    def get_result_change(self, execution_id: str, *, node_id: str) -> ResultChange | None:
        row = self._fetchone(_SELECT_RESULT_CHANGE, (execution_id, node_id))
        if row is None:
            return None
        position, change, was, streak, streak_since = row
        return ResultChange(
            position=cast(int, position),
            change=cast("str | None", change),
            was=cast("str | None", was),
            streak=_decode_streak(streak, streak_since),
        )

    def create_project(self, name: str, *, created_at: datetime) -> Project:
        with self._lock:
            cursor = self._conn.execute(_INSERT_PROJECT, (name, isoformat_utc(created_at)))
            if cursor.rowcount != 1:
                raise ProjectExistsError(f"there is already a project named {name!r}")
        return Project(name=name, created_at=created_at)

    def get_project(self, name: str) -> Project | None:
        row = self._fetchone(_SELECT_PROJECT, (name,))
        return None if row is None else _row_to_project(row)

    def list_projects(self) -> Sequence[Project]:
        return tuple(_row_to_project(row) for row in self._fetchall(_LIST_PROJECTS, ()))

    def get_member_role(self, user: str, *, project: str) -> str | None:
        row = self._fetchone(_SELECT_MEMBER_ROLE, (project, user))
        return None if row is None else cast(str, row[0])

    def list_members(self, *, project: str) -> Sequence[Membership]:
        return tuple(_row_to_membership(row) for row in self._fetchall(_LIST_MEMBERS, (project,)))

    def list_memberships(self, user: str) -> Sequence[Membership]:
        rows = self._fetchall(_LIST_MEMBERSHIPS, (user,))
        return tuple(_row_to_membership(row) for row in rows)

    def set_member(self, user: str, *, project: str, role: str) -> bool:
        _check_member_row(project, role)
        # The probes share the write transaction, so the project and the user
        # they find are there at the upsert, and no other writer can add or
        # remove the row between the member probe and the upsert. The project
        # is probed first, so naming neither is answered with the project.
        with self._write_transaction() as conn:
            if conn.execute(_PROBE_PROJECT, (project,)).fetchone() is None:
                raise UnknownProjectError(f"there is no project named {project!r}")
            if conn.execute(_SELECT_USER, (user,)).fetchone() is None:
                raise UnknownUserError(f"there is no user named {user!r}")
            created = conn.execute(_SELECT_MEMBER_ROLE, (project, user)).fetchone() is None
            conn.execute(_UPSERT_MEMBER, (project, user, role))
        return created

    def remove_member(self, user: str, *, project: str) -> bool:
        with self._lock:
            return self._conn.execute(_DELETE_MEMBER, (project, user)).rowcount == 1

    def access_required(self) -> bool:
        return bool(self._count(_PROBE_ANY_USER))

    def create_first_admin(
        self, name: str, *, password_hash: str, created_at: datetime
    ) -> User | None:
        with self._write_transaction() as conn:
            cursor = conn.execute(
                _INSERT_FIRST_ADMIN, (name, isoformat_utc(created_at), password_hash)
            )
        if cursor.rowcount != 1:
            return None
        return User(name=name, admin=True, disabled=False, created_at=created_at, has_password=True)

    def create_user(self, name: str, *, admin: bool, created_at: datetime) -> User:
        with self._lock:
            cursor = self._conn.execute(
                _INSERT_USER, (name, 1 if admin else 0, isoformat_utc(created_at))
            )
            if cursor.rowcount != 1:
                raise UserExistsError(f"there is already a user named {name!r}")
        return User(name=name, admin=admin, disabled=False, created_at=created_at)

    def get_user(self, name: str) -> User | None:
        row = self._fetchone(_SELECT_USER, (name,))
        return None if row is None else _row_to_user(row)

    def list_users(self) -> Sequence[User]:
        return tuple(_row_to_user(row) for row in self._fetchall(_LIST_USERS, ()))

    def update_user(
        self, name: str, *, admin: bool | None = None, disabled: bool | None = None
    ) -> User | None:
        with self._write_transaction() as conn:
            conn.execute(
                _UPDATE_USER,
                (
                    None if admin is None else int(admin),
                    None if disabled is None else int(disabled),
                    name,
                ),
            )
            row = conn.execute(_SELECT_USER, (name,)).fetchone()
        return None if row is None else _row_to_user(row)

    def get_password_hash(self, name: str) -> str | None:
        row = self._fetchone(_SELECT_PASSWORD_HASH, (name,))
        return None if row is None else cast("str | None", row[0])

    def set_password(
        self,
        name: str,
        *,
        password_hash: str,
        changed_at: datetime,
        replacing: str | None = None,
    ) -> bool:
        with self._write_transaction() as conn:
            if replacing is None:
                cursor = conn.execute(_SET_PASSWORD, (password_hash, name))
            else:
                cursor = conn.execute(_REPLACE_PASSWORD, (password_hash, name, replacing))
            if cursor.rowcount != 1:
                return False
            conn.execute(_REVOKE_LOGIN_TOKENS, (isoformat_utc(changed_at), name))
        return True

    def create_login_token(
        self,
        name: str,
        *,
        password_hash: str,
        digest: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> Token | None:
        created = isoformat_utc(created_at)
        with self._write_transaction() as conn:
            cursor = conn.execute(
                _INSERT_LOGIN_TOKEN,
                (
                    digest,
                    LOGIN_TOKEN_LABEL,
                    created,
                    isoformat_utc(expires_at),
                    name,
                    password_hash,
                ),
            )
            if cursor.rowcount != 1:
                return None
            token_id = cursor.lastrowid
            conn.execute(_DELETE_EXPIRED_LOGIN_TOKENS, (name, created, token_id))
            row = conn.execute(_SELECT_TOKEN, (token_id,)).fetchone()
        return _row_to_token(row)

    def create_token(
        self,
        user: str,
        *,
        digest: str,
        label: str,
        scopes: frozenset[str],
        created_at: datetime,
    ) -> Token:
        # The probe shares the write transaction, so the user it finds is
        # there when the token is inserted: users are never deleted anyway.
        with self._write_transaction() as conn:
            if conn.execute(_SELECT_USER, (user,)).fetchone() is None:
                raise UnknownUserError(f"there is no user named {user!r}")
            cursor = conn.execute(
                _INSERT_TOKEN,
                (user, digest, label, *_scope_columns(scopes), isoformat_utc(created_at)),
            )
            row = conn.execute(_SELECT_TOKEN, (cursor.lastrowid,)).fetchone()
        return _row_to_token(row)

    def list_tokens(self, *, user: str | None = None) -> Sequence[Token]:
        rows = (
            self._fetchall(_LIST_TOKENS, ())
            if user is None
            else self._fetchall(_LIST_USER_TOKENS, (user,))
        )
        return tuple(_row_to_token(row) for row in rows)

    def get_token(self, token_id: int) -> Token | None:
        if not 0 < token_id <= _MAX_ID:
            return None
        row = self._fetchone(_SELECT_TOKEN, (token_id,))
        return None if row is None else _row_to_token(row)

    def revoke_token(self, token_id: int, *, revoked_at: datetime, user: str | None = None) -> bool:
        if not 0 < token_id <= _MAX_ID:
            # `sqlite3` cannot bind a larger integer at all.
            return False
        with self._lock:
            cursor = self._conn.execute(_REVOKE_TOKEN, (isoformat_utc(revoked_at), token_id, user))
            return cursor.rowcount == 1

    def authenticate(self, digest: str, *, now: datetime) -> Grant | None:
        row = self._fetchone(_AUTHENTICATE, (digest, isoformat_utc(now)))
        if row is None:
            return None
        name, admin, can_read, can_record, can_manage, can_admin, token_id, expires_at = row
        return Grant(
            user=cast(str, name),
            admin=bool(admin),
            scopes=_decode_scopes(can_read, can_record, can_manage, can_admin),
            token_id=cast(int, token_id),
            expires_at=_opt_datetime(expires_at),
        )

    def close(self) -> None:
        # Under the lock, so a statement another thread has in flight
        # finishes first; every later call then fails on the closed
        # connection.
        with self._lock:
            self._conn.close()
