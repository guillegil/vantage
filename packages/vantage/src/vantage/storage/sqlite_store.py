"""SQLite adapter for `ExecutionStore` (RQ-30.1, RQ-38.1) -- design.md D5, D8,
D19-D22, D25-D27, D33.

A run row is now written by `INSERT ... ON CONFLICT(id) DO UPDATE`, not
`DO NOTHING` (D25): a start-write and a finish-write share one `id`, and the
finish-write must be able to apply over a row the start-write already
created. The `DO UPDATE`'s own `WHERE run.exit_status IS NULL AND
excluded.exit_status IS NOT NULL` is the monotonic guard -- `exit_status`,
never `finished_at`, is the discriminator, because a Ctrl-C session reports
an integer exit status with a null finish time. `received_at` and
`started_at` are never advanced on the conflict path.

Under `DO UPDATE`, `cursor.rowcount` can no longer answer "was this row
created?" -- an applied conflict also reports one changed row (D26). One
`SELECT 1 FROM run WHERE id = ?` immediately after `BEGIN IMMEDIATE`, before
the write, decides `created` instead. This *is* a `SELECT`-then-write shape,
and the reasoning that used to rule that out still applies to every other
use of this connection -- but not here: `BEGIN IMMEDIATE` takes the
`RESERVED` lock for the whole transaction before the `SELECT` runs, and
`self._lock` serialises the server's threadpool, so the probe reads under
precisely the lock the write will write under. There is no window here for
two concurrent replays to both pass the `SELECT` and both attempt the write.

Concurrency is a two-layer net (D8), and neither layer alone is sufficient:
a `threading.Lock` held across the whole transaction serialises the several
threads of one process (the server runs this handler in a threadpool), while
`open_database`'s WAL mode, `check_same_thread=False` connection and 5s busy
timeout are the cross-process net for two separate processes sharing one
file -- no in-process lock can reach across a process boundary. Every write
takes the lock up front with `BEGIN IMMEDIATE`, never a deferred
transaction, because a deferred transaction that upgrades to a write
mid-statement is the classic two-writer deadlock.

Results and the catalogue join the same transaction (D22): run insert, the
catalogue upsert, the surrogate-key resolve, and the result insert are four
statements inside one `BEGIN IMMEDIATE` -- never four separate calls, and
never `RETURNING` (needs SQLite >= 3.35, above the 3.10 floor). The
catalogue upsert (D20) advances `last_seen_at` with `MAX` rather than an
unconditional overwrite, so a late-arriving report with an older
`run.started_at` cannot roll a test's last-seen timestamp backwards. The
result insert (D19 layer 3) is `ON CONFLICT(run_id, node_id, attempt) DO
NOTHING`, which makes a replayed report a silent no-op rather than an error
(RQ-41).

`last_contact_at` is written by the creating report only (D27): the insert
branch of `_UPSERT_RUN` sets it to `received_at`, and the conflict branch
never advances it -- a finished or interrupted run is not stale, it is done.
`touch_last_contact`'s `_TOUCH_LAST_CONTACT` is its own monotonic `UPDATE`
(D33), mirroring `_UPSERT_RUN`'s `exit_status` guard with a `last_contact_at
< ?` comparison instead. That comparison is lexicographic, correct only at
fixed width -- `_fixed_width_isoformat` mirrors
`pytest_vantage.recorder.isoformat_utc` so every value this module writes to
`last_contact_at` carries the same width, the same latent hazard D27 records
for `test_case.last_seen_at`'s `MAX` but does not fix.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.projection import (
    LIST_COMMIT_SUBJECT_CHARS,
    LIST_FAILURE_MESSAGE_CHARS,
    FailureProjection,
    VcsProjection,
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

# SQLITE_MAX_VARIABLE_NUMBER is 999 on older SQLite builds; 500 leaves
# headroom without needing to introspect the running library's compile-time
# limit (design.md D20).
_MAX_PLACEHOLDERS = 500

# `last_contact_at` is written on the insert branch only (D27) -- the
# conflict branch's `DO UPDATE SET` list never names it, so a finish-write
# applied over an existing start-only row leaves it exactly where the
# creating report set it.
#
# The six `vcs_*` columns join the conflict branch under the SAME row-level
# `exit_status` guard (design.md D48) -- not a second, independent
# condition. `COALESCE(excluded.vcs_*, run.vcs_*)` is monotonic in the only
# direction that matters: null -> value, never value -> null, so a
# finish-write that carries no vcs data cannot clobber a snapshot the
# start-write already recorded. `vcs_commit_subject_truncated` is NOT
# coalesced independently -- its `CASE` keys on whether the INCOMING subject
# is non-null, so the flag always travels with the value it describes,
# never surviving a subject that came from the other report.
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

# D33's monotonic guard: mirrors `_UPSERT_RUN`'s `exit_status` `WHERE`, but on
# `last_contact_at` itself rather than a separate discriminator column -- an
# out-of-order beat (an earlier or equal `contacted_at` than what is already
# stored) changes zero rows, exactly like a reordered start-write changes
# zero rows under `_UPSERT_RUN`.
_TOUCH_LAST_CONTACT = """
    UPDATE run
       SET last_contact_at = ?
     WHERE id = ?
       AND (last_contact_at IS NULL OR last_contact_at < ?)
"""

_SELECT_RUN = """
    SELECT id, started_at, finished_at, exit_status, interrupted, interrupt_reason,
           vcs_commit, vcs_branch, vcs_commit_subject, vcs_commit_subject_truncated,
           vcs_dirty, vcs_root
    FROM run WHERE id = ?
"""

# `get_run_detail`'s SELECT -- `_SELECT_RUN`'s columns plus `last_contact_at`
# appended last, so the first twelve values still unpack straight into
# `_row_to_execution` unchanged (design.md D57, D58).
_SELECT_RUN_DETAIL = """
    SELECT id, started_at, finished_at, exit_status, interrupted, interrupt_reason,
           vcs_commit, vcs_branch, vcs_commit_subject, vcs_commit_subject_truncated,
           vcs_dirty, vcs_root, last_contact_at
    FROM run WHERE id = ?
"""

# `list_runs`' SELECT -- the lean-list projection happens here, in SQL, not
# in Python after the fact (design.md D57): `substr`/`length` bound the
# commit subject to `LIST_COMMIT_SUBJECT_CHARS` *before* it leaves SQLite,
# so a 64 KiB subject is never pulled into memory only to be sliced down.
# `vcs_root` is selected only to feed the null-projection check below -- it
# never appears in `RunListEntry`'s `VcsProjection` (design.md D59). The
# `COALESCE` in the `CASE` is load-bearing: `length(NULL) > ?` is SQL
# `NULL`, not `0`, and a null subject must produce a `0` (false) flag, never
# a null one (design.md D60).
_LIST_RUNS = """
    SELECT id, started_at, finished_at, exit_status, interrupted, interrupt_reason,
           last_contact_at,
           vcs_commit, vcs_branch,
           substr(vcs_commit_subject, 1, ?)                             AS commit_subject,
           CASE WHEN vcs_commit_subject_truncated = 1
                  OR COALESCE(length(vcs_commit_subject) > ?, 0) = 1
                THEN 1 ELSE 0 END                                       AS commit_subject_truncated,
           vcs_dirty, vcs_root
    FROM run
    ORDER BY started_at DESC, id DESC
    LIMIT ? OFFSET ?
"""

# The `metadata_key`/`metadata_value`-filtered sibling of `_LIST_RUNS`
# (design.md D100) -- same columns, same total order, one `WHERE EXISTS`
# added rather than a second, diverging query. The subquery is served by
# `idx_run_metadata_key_value`: SQLite can seek `rm.key = ?` and, within that,
# `rm.value = ?` directly off the index without touching `run_metadata`'s
# table rows at all. `rm.value = ?` also does the status filtering for free
# -- `value` is NULL for any non-`'captured'` row (schema.sql), and SQL NULL
# never equals a bound string, so a declared-but-dropped entry can never
# satisfy this filter by accident.
_LIST_RUNS_BY_METADATA = """
    SELECT id, started_at, finished_at, exit_status, interrupted, interrupt_reason,
           last_contact_at,
           vcs_commit, vcs_branch,
           substr(vcs_commit_subject, 1, ?)                             AS commit_subject,
           CASE WHEN vcs_commit_subject_truncated = 1
                  OR COALESCE(length(vcs_commit_subject) > ?, 0) = 1
                THEN 1 ELSE 0 END                                       AS commit_subject_truncated,
           vcs_dirty, vcs_root
    FROM run
    WHERE EXISTS (
        SELECT 1 FROM run_metadata rm
        WHERE rm.run_id = run.id AND rm.key = ? AND rm.value = ?
    )
    ORDER BY started_at DESC, id DESC
    LIMIT ? OFFSET ?
"""

# Q2's horizon, first half (design.md D100): the earliest `started_at` among
# runs holding ANY `run_metadata` row for `key`, of any status -- left-
# anchored on `key` alone, so this is the same `idx_run_metadata_key_value`
# seeked on its leading column only, not the two-column point lookup
# `_LIST_RUNS_BY_METADATA` performs.
_METADATA_KEY_FIRST_SEEN = """
    SELECT MIN(run.started_at)
    FROM run_metadata rm
    JOIN run ON run.id = rm.run_id
    WHERE rm.key = ?
"""

# Q2's horizon, second half: how many runs are strictly older than
# `first_seen` -- served by `idx_run_started_at`, the same index `_LIST_RUNS`
# already orders by.
_COUNT_RUNS_BEFORE = "SELECT COUNT(*) FROM run WHERE started_at < ?"

# Conflict target is `node_id` -- the Phase 1 identity key (schema.sql
# comment). `stable_id` carries the identical string in Phase 1, so its own
# UNIQUE index cannot be violated by the row this statement updates.
_UPSERT_TEST_CASE = """
    INSERT INTO test_case (
        stable_id, node_id, file_path, class_name, function_name,
        param_id, first_seen_at, last_seen_at, last_seen_run_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(node_id) DO UPDATE SET
        file_path        = excluded.file_path,
        class_name       = excluded.class_name,
        function_name    = excluded.function_name,
        param_id         = excluded.param_id,
        last_seen_run_id = CASE WHEN excluded.last_seen_at > test_case.last_seen_at
                                THEN excluded.last_seen_run_id ELSE test_case.last_seen_run_id END,
        last_seen_at     = MAX(test_case.last_seen_at, excluded.last_seen_at)
"""

# Widened from 14 to 31 bound columns (design.md D80): the thirteen
# `FailureEvidence` fields plus the four `CapturedOutput` fields, appended
# after `worker_id` -- `worker_id` itself keeps its original position so the
# first fourteen values are unchanged (`_result_rows` below). `schema.sql`
# is NOT touched by this widening: RQ-29 already created these columns at
# first use, so nothing here is a migration.
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
    ON CONFLICT(run_id, node_id, attempt) DO NOTHING
"""

# `INSERT OR IGNORE` on the primary key is what makes D98's "written once,
# never updated" true mechanically, not by convention -- the same
# idempotence `_INSERT_RESULT`'s `ON CONFLICT ... DO NOTHING` already relies
# on. Both statements are appended after the result insert, inside the same
# `BEGIN IMMEDIATE ... COMMIT`: `PRAGMA foreign_keys=ON` is set on every
# connection, and both new tables reference `run(id)`, created earlier in
# the same transaction (design.md D98).
_INSERT_METADATA_FILE = """
    INSERT OR IGNORE INTO run_metadata_file (run_id, source_file, content_type, status)
    VALUES (?, ?, ?, ?)
"""

_INSERT_METADATA_ENTRY = """
    INSERT OR IGNORE INTO run_metadata (run_id, key, value, source_file, status)
    VALUES (?, ?, ?, ?, ?)
"""

# Widened from 16 to 33 columns (design.md D80) -- the full, unbounded
# record `get_results`/`get_result` return; every failure/captured-output
# column is selected here, unlike the lean `_LIST_RESULTS` below.
_SELECT_RESULTS_FOR_RUN = """
    SELECT r.node_id, tc.file_path, tc.class_name, tc.function_name, tc.param_id,
           r.outcome, r.duration, r.started_at, r.finished_at,
           r.setup_outcome, r.call_outcome, r.teardown_outcome,
           r.setup_duration, r.call_duration, r.teardown_duration, r.worker_id,
           r.failure_type, r.failure_message, r.failure_message_truncated,
           r.failure_path, r.failure_lineno,
           r.failure_repr, r.failure_repr_truncated,
           r.traceback, r.traceback_truncated,
           r.skip_reason, r.skip_reason_truncated,
           r.xfail_reason, r.xfail_reason_truncated,
           r.captured_stdout, r.captured_stdout_truncated,
           r.captured_stderr, r.captured_stderr_truncated
    FROM result r
    JOIN test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = ?
    ORDER BY r.id
"""

# `get_result`'s SELECT -- the same full-record column set as
# `_SELECT_RESULTS_FOR_RUN`, narrowed to one `node_id` (design.md D77, D78).
_SELECT_RESULT = """
    SELECT r.node_id, tc.file_path, tc.class_name, tc.function_name, tc.param_id,
           r.outcome, r.duration, r.started_at, r.finished_at,
           r.setup_outcome, r.call_outcome, r.teardown_outcome,
           r.setup_duration, r.call_duration, r.teardown_duration, r.worker_id,
           r.failure_type, r.failure_message, r.failure_message_truncated,
           r.failure_path, r.failure_lineno,
           r.failure_repr, r.failure_repr_truncated,
           r.traceback, r.traceback_truncated,
           r.skip_reason, r.skip_reason_truncated,
           r.xfail_reason, r.xfail_reason_truncated,
           r.captured_stdout, r.captured_stdout_truncated,
           r.captured_stderr, r.captured_stderr_truncated
    FROM result r
    JOIN test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = ? AND r.node_id = ?
"""

# `list_results`' SELECT -- the paginated, LEAN sibling of
# `_SELECT_RESULTS_FOR_RUN` (design.md D57, D76): same shape, same `r.id`
# order, `LIMIT`/`OFFSET` added, but the failure projection here is the
# `substr`/`length`-bounded, disjoined shape `_LIST_RUNS` already uses for
# the commit subject -- and no `failure_repr`/`traceback`/captured-output
# column is selected at all, the SQL half of D76's structural exclusion.
_LIST_RESULTS = """
    SELECT r.node_id, tc.file_path, tc.class_name, tc.function_name, tc.param_id,
           r.outcome, r.duration, r.started_at, r.finished_at,
           r.setup_outcome, r.call_outcome, r.teardown_outcome,
           r.setup_duration, r.call_duration, r.teardown_duration, r.worker_id,
           r.failure_type,
           substr(r.failure_message, 1, ?)                          AS failure_message,
           CASE WHEN r.failure_message_truncated = 1
                  OR COALESCE(length(r.failure_message) > ?, 0) = 1
                THEN 1 ELSE 0 END                                   AS failure_message_truncated,
           r.failure_path, r.failure_lineno, r.skip_reason, r.xfail_reason
    FROM result r
    JOIN test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = ?
    ORDER BY r.id
    LIMIT ? OFFSET ?
"""

# `list_history`' SELECT -- the join design.md D63 sizes: `node_id` (a bound
# parameter, never interpolated, matching `_resolve_test_case_ids`'s existing
# discipline) resolves through `idx_test_case_node_id` (unique) to one
# `test_case.id`, then `idx_result_test_case_id` for that test's results,
# then `run` by primary key. Same `substr`/`length` projection and the same
# total order as `_LIST_RUNS` (design.md D60, D61) -- both list a run,
# ordered the same way.
_LIST_HISTORY = """
    SELECT r.run_id, run.started_at, run.finished_at, run.last_contact_at,
           r.outcome, r.duration,
           run.vcs_commit, run.vcs_branch,
           substr(run.vcs_commit_subject, 1, ?)                     AS commit_subject,
           CASE WHEN run.vcs_commit_subject_truncated = 1
                  OR COALESCE(length(run.vcs_commit_subject) > ?, 0) = 1
                THEN 1 ELSE 0 END                                   AS commit_subject_truncated,
           run.vcs_dirty, run.vcs_root
    FROM test_case tc
    JOIN result r ON r.test_case_id = tc.id
    JOIN run ON run.id = r.run_id
    WHERE tc.node_id = ?
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT ? OFFSET ?
"""

_SELECT_TEST_CASE = """
    SELECT node_id, file_path, class_name, function_name, param_id,
           first_seen_at, last_seen_at, last_seen_run_id
    FROM test_case WHERE node_id = ?
"""

# `_LIST_SETTINGS`: alphabetical by key == alphabetical by section name
# (design.md D85, D86).
_LIST_SETTINGS = """
    SELECT namespace, key, value, updated_at
    FROM user_setting WHERE namespace = ? ORDER BY key
"""

# `_UPSERT_SETTING`: the existence probe precedes it inside the same
# `BEGIN IMMEDIATE`, because `rowcount` cannot distinguish insert from
# update under `DO UPDATE` (design.md D26's reason, unchanged).
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

# `_SELECT_RUN_CASE_OUTCOMES`: the per-run aggregate read (design.md D85,
# D86). No index on `test_case.file_path` -- deliberate, see design.md D86.
_SELECT_RUN_CASE_OUTCOMES = """
    SELECT tc.file_path, r.outcome
    FROM result r
    JOIN test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = ?
"""


def _fixed_width_isoformat(moment: datetime) -> str:
    """Fixed-width ISO-8601 UTC text: `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`.

    Mirrors `pytest_vantage.recorder.isoformat_utc` exactly (D27). Every
    `astimezone(timezone.utc)` first, rather than trusting the caller.
    `touch_last_contact` is a public port method whose signature accepts any
    `datetime`, and `strftime` alone would stamp a `+02:00` value with a
    `+00:00` suffix -- storing it two hours ahead of the truth and then
    comparing it lexicographically against genuinely-UTC rows. The in-memory
    adapter compares real `datetime` objects and gets that input right, so
    trusting the caller is also what would make the two adapters disagree.

    Used only for `last_contact_at`,
    the one column this module compares lexicographically (`< ?`); every
    other timestamp column keeps plain `.isoformat()`, unaffected.
    """
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _vcs_columns(vcs: VcsContext | None) -> tuple[object, ...]:
    """The six `vcs_*` `_UPSERT_RUN` parameters (design.md D48).

    `vcs_dirty` is `INTEGER NULL` with no default -- written as `1`, `0` or
    `NULL`, and never `0` for "unknown", which would have a run recorded
    outside a repository claim a clean working tree.
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


def _row_to_vcs_context(row: tuple[object, ...]) -> VcsContext | None:
    """The all-null normalisation rule (design.md D48), applied to the five
    value columns -- `vcs_commit_subject_truncated` is excluded from the
    check, matching `_to_execution`'s own rule (task 3.5/3.9): a run
    recorded outside a repository reads back as `None`, never as a
    `VcsContext` full of nulls."""
    commit, branch, commit_subject, commit_subject_truncated, dirty, root = row
    if (
        commit is None
        and branch is None
        and commit_subject is None
        and dirty is None
        and root is None
    ):
        return None
    return VcsContext(
        commit=cast("str | None", commit),
        branch=cast("str | None", branch),
        commit_subject=cast("str | None", commit_subject),
        commit_subject_truncated=bool(commit_subject_truncated),
        dirty=None if dirty is None else bool(dirty),
        root=cast("str | None", root),
    )


def _row_to_execution(row: tuple[object, ...]) -> Execution:
    (
        identity_value,
        started_at,
        finished_at,
        exit_status,
        interrupted,
        interrupt_reason,
        *vcs_row,
    ) = row
    # `id` and `started_at` are `NOT NULL` in `schema.sql` -- a `cast`, not a
    # runtime check, documents that without an assert statement (S101).
    return Execution(
        identity=Identity(cast(str, identity_value)),
        started_at=datetime.fromisoformat(cast(str, started_at)),
        finished_at=datetime.fromisoformat(finished_at) if isinstance(finished_at, str) else None,
        exit_status=exit_status if isinstance(exit_status, int) else None,
        interrupted=bool(interrupted),
        interrupt_reason=interrupt_reason if isinstance(interrupt_reason, str) else None,
        vcs=_row_to_vcs_context(tuple(vcs_row)),
    )


def _row_to_vcs_projection(
    commit: object,
    branch: object,
    commit_subject: object,
    commit_subject_truncated: object,
    dirty: object,
    root: object,
) -> VcsProjection | None:
    """The same all-null normalisation rule as `_row_to_vcs_context`
    (design.md D48), applied to the `_LIST_RUNS` projection columns.
    `root` is one of the five inputs to the null check even though
    `VcsProjection` itself carries no `root` field (design.md D59) -- a run
    whose only known field is `root` must not be misread as absent."""
    if (
        commit is None
        and branch is None
        and commit_subject is None
        and dirty is None
        and root is None
    ):
        return None
    return VcsProjection(
        commit=cast("str | None", commit),
        branch=cast("str | None", branch),
        commit_subject=cast("str | None", commit_subject),
        commit_subject_truncated=bool(commit_subject_truncated),
        dirty=None if dirty is None else bool(dirty),
    )


def _row_to_run_list_entry(row: tuple[object, ...]) -> RunListEntry:
    (
        identity_value,
        started_at,
        finished_at,
        exit_status,
        interrupted,
        interrupt_reason,
        last_contact_at,
        commit,
        branch,
        commit_subject,
        commit_subject_truncated,
        dirty,
        root,
    ) = row
    execution = Execution(
        identity=Identity(cast(str, identity_value)),
        started_at=datetime.fromisoformat(cast(str, started_at)),
        finished_at=datetime.fromisoformat(finished_at) if isinstance(finished_at, str) else None,
        exit_status=exit_status if isinstance(exit_status, int) else None,
        interrupted=bool(interrupted),
        interrupt_reason=interrupt_reason if isinstance(interrupt_reason, str) else None,
        vcs=None,  # the lean projection rides beside it in `vcs`, not here (design.md D57)
    )
    return RunListEntry(
        execution=execution,
        last_contact_at=(
            datetime.fromisoformat(last_contact_at) if isinstance(last_contact_at, str) else None
        ),
        vcs=_row_to_vcs_projection(
            commit, branch, commit_subject, commit_subject_truncated, dirty, root
        ),
    )


def _row_to_history_entry(row: tuple[object, ...]) -> HistoryEntry:
    (
        run_id,
        started_at,
        finished_at,
        last_contact_at,
        outcome,
        duration,
        commit,
        branch,
        commit_subject,
        commit_subject_truncated,
        dirty,
        root,
    ) = row
    return HistoryEntry(
        run_id=cast(str, run_id),
        started_at=datetime.fromisoformat(cast(str, started_at)),
        finished_at=datetime.fromisoformat(finished_at) if isinstance(finished_at, str) else None,
        last_contact_at=(
            datetime.fromisoformat(last_contact_at) if isinstance(last_contact_at, str) else None
        ),
        outcome=cast(str, outcome),
        duration=cast("float | None", duration),
        vcs=_row_to_vcs_projection(
            commit, branch, commit_subject, commit_subject_truncated, dirty, root
        ),
    )


def _row_to_failure_evidence(row: tuple[object, ...]) -> FailureEvidence | None:
    """The all-null-or-false normalisation rule (design.md D48, D77),
    applied to all thirteen stored `FailureEvidence` columns -- mirrors
    `_row_to_vcs_context`'s rule, widened from five value columns to
    thirteen: a result with no failure evidence at all reads back as
    `None`, never as a `FailureEvidence` full of nulls."""
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
    if (
        failure_type is None
        and failure_message is None
        and not failure_message_truncated
        and failure_path is None
        and failure_lineno is None
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


def _row_to_captured_output(row: tuple[object, ...]) -> CapturedOutput:
    """Never `None` (design.md D77's asymmetry) -- reads the four columns
    straight through, so a stored `''` reads back as `''`, never coerced to
    `None`."""
    stdout, stdout_truncated, stderr, stderr_truncated = row
    return CapturedOutput(
        stdout=cast("str | None", stdout),
        stdout_truncated=bool(stdout_truncated),
        stderr=cast("str | None", stderr),
        stderr_truncated=bool(stderr_truncated),
    )


def _row_to_failure_projection(
    failure_type: object,
    failure_message: object,
    failure_message_truncated: object,
    failure_path: object,
    failure_lineno: object,
    skip_reason: object,
    xfail_reason: object,
) -> FailureProjection | None:
    """The same all-null-or-false rule as `_row_to_failure_evidence`, over
    only the seven lean columns `_LIST_RESULTS` selects (design.md D76)."""
    if (
        failure_type is None
        and failure_message is None
        and not failure_message_truncated
        and failure_path is None
        and failure_lineno is None
        and skip_reason is None
        and xfail_reason is None
    ):
        return None
    return FailureProjection(
        failure_type=cast("str | None", failure_type),
        failure_message=cast("str | None", failure_message),
        failure_message_truncated=bool(failure_message_truncated),
        failure_path=cast("str | None", failure_path),
        failure_lineno=cast("int | None", failure_lineno),
        skip_reason=cast("str | None", skip_reason),
        xfail_reason=cast("str | None", xfail_reason),
    )


def _row_to_result(row: tuple[object, ...]) -> Result:
    (
        node_id,
        file_path,
        class_name,
        function_name,
        param_id,
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
        *failure_and_captured,
    ) = row
    return Result(
        identity=CaseIdentity(
            node_id=cast(str, node_id),
            file_path=cast(str, file_path),
            class_name=cast("str | None", class_name),
            function_name=cast(str, function_name),
            param_id=cast("str | None", param_id),
        ),
        outcome=cast(str, outcome),
        duration=cast("float | None", duration),
        started_at=datetime.fromisoformat(started_at) if isinstance(started_at, str) else None,
        finished_at=datetime.fromisoformat(finished_at) if isinstance(finished_at, str) else None,
        setup_outcome=cast("str | None", setup_outcome),
        call_outcome=cast("str | None", call_outcome),
        teardown_outcome=cast("str | None", teardown_outcome),
        setup_duration=cast("float | None", setup_duration),
        call_duration=cast("float | None", call_duration),
        teardown_duration=cast("float | None", teardown_duration),
        worker_id=cast("str | None", worker_id),
        failure=_row_to_failure_evidence(tuple(failure_and_captured[:13])),
        captured=_row_to_captured_output(tuple(failure_and_captured[13:])),
    )


def _row_to_result_list_entry(row: tuple[object, ...]) -> ResultListEntry:
    (
        node_id,
        file_path,
        class_name,
        function_name,
        param_id,
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
        failure_type,
        failure_message,
        failure_message_truncated,
        failure_path,
        failure_lineno,
        skip_reason,
        xfail_reason,
    ) = row
    return ResultListEntry(
        identity=CaseIdentity(
            node_id=cast(str, node_id),
            file_path=cast(str, file_path),
            class_name=cast("str | None", class_name),
            function_name=cast(str, function_name),
            param_id=cast("str | None", param_id),
        ),
        outcome=cast(str, outcome),
        duration=cast("float | None", duration),
        started_at=datetime.fromisoformat(started_at) if isinstance(started_at, str) else None,
        finished_at=datetime.fromisoformat(finished_at) if isinstance(finished_at, str) else None,
        setup_outcome=cast("str | None", setup_outcome),
        call_outcome=cast("str | None", call_outcome),
        teardown_outcome=cast("str | None", teardown_outcome),
        setup_duration=cast("float | None", setup_duration),
        call_duration=cast("float | None", call_duration),
        teardown_duration=cast("float | None", teardown_duration),
        worker_id=cast("str | None", worker_id),
        failure=_row_to_failure_projection(
            failure_type,
            failure_message,
            failure_message_truncated,
            failure_path,
            failure_lineno,
            skip_reason,
            xfail_reason,
        ),
    )


def _row_to_catalogue_entry(row: tuple[object, ...]) -> CatalogueEntry:
    (
        node_id,
        file_path,
        class_name,
        function_name,
        param_id,
        first_seen_at,
        last_seen_at,
        last_seen_run_id,
    ) = row
    return CatalogueEntry(
        identity=CaseIdentity(
            node_id=cast(str, node_id),
            file_path=cast(str, file_path),
            class_name=cast("str | None", class_name),
            function_name=cast(str, function_name),
            param_id=cast("str | None", param_id),
        ),
        first_seen_at=datetime.fromisoformat(cast(str, first_seen_at)),
        last_seen_at=datetime.fromisoformat(cast(str, last_seen_at)),
        last_seen_run_id=cast("str | None", last_seen_run_id),
    )


def _catalogue_rows(
    execution: Execution, results: Sequence[Result]
) -> list[tuple[str, str, str, str | None, str, str | None, str, str, str]]:
    started_at = execution.started_at.isoformat()
    run_id = execution.identity.value
    # Keyed by node_id so a report carrying the same node id twice (should
    # not happen -- the service layer rejects it, D19 layer 2) still yields
    # one upsert row rather than a batch containing a duplicate key.
    by_node_id: dict[str, CaseIdentity] = {
        result.identity.node_id: result.identity for result in results
    }
    return [
        (
            identity.node_id,  # stable_id -- identical to node_id in Phase 1 (D20)
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
    """The thirteen `FailureEvidence` `_INSERT_RESULT` parameters (design.md
    D77). `None` writes every column NULL/0, mirroring `_vcs_columns`'s
    `None` branch -- `failure=None` means no failure evidence at all, not
    an evidence record whose fields all happen to be null."""
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
    """The four `CapturedOutput` `_INSERT_RESULT` parameters (design.md
    D77). `captured.stdout`/`.stderr` are written through UNCHANGED -- never
    `value or None` or any other truthy check, which would collapse `""`
    (captured, empty) into SQL `NULL` (never captured) and destroy the
    distinction the `failure-evidence` capability requires."""
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
        updated_at=datetime.fromisoformat(cast(str, updated_at)),
    )


class SqliteExecutionStore:
    """Implements `vantage.core.ports.storage.ExecutionStore` against SQLite.

    One connection per process, `check_same_thread=False` (D8) -- opened via
    `open_database`, which already applies D9's permissions and WAL. Every
    write acquires `self._lock` for the whole transaction: the in-process
    half of D8's "neither lock alone is sufficient". `BEGIN IMMEDIATE` and
    the connection's busy timeout are the cross-process half, and neither
    substitutes for the other -- the Python lock has no effect on a second
    server process sharing the same file.
    """

    def __init__(self, path: Path) -> None:
        self._conn = open_database(path)
        self._lock = threading.Lock()

    def record_session(
        self,
        execution: Execution,
        *,
        results: Sequence[Result],
        received_at: datetime,
        metadata: RunMetadata = EMPTY_RUN_METADATA,
    ) -> bool:
        # Five statements, one transaction, one fixed order (design.md D22,
        # extended by D26): existence probe, run upsert, catalogue upsert,
        # surrogate-key resolve, result insert. The order is required, not
        # tidy -- `PRAGMA foreign_keys=ON` is set on every connection, so
        # `result.run_id`, `test_case.last_seen_run_id` and
        # `result.test_case_id` each need their referent to exist first.
        # The two metadata inserts (design.md D98) are appended after the
        # result insert, in the same transaction, for the same reason.
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                probe = self._conn.execute(
                    _PROBE_RUN_EXISTS, (execution.identity.value,)
                ).fetchone()
                created = probe is None

                self._conn.execute(
                    _UPSERT_RUN,
                    (
                        execution.identity.value,
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
                    self._conn.executemany(_UPSERT_TEST_CASE, catalogue_rows)

                    node_ids = [row[1] for row in catalogue_rows]
                    test_case_ids = _resolve_test_case_ids(self._conn, node_ids)

                    result_rows = _result_rows(execution, results, test_case_ids)
                    self._conn.executemany(_INSERT_RESULT, result_rows)

                if metadata.files:
                    self._conn.executemany(
                        _INSERT_METADATA_FILE,
                        _metadata_file_rows(execution.identity.value, metadata),
                    )
                if metadata.entries:
                    self._conn.executemany(
                        _INSERT_METADATA_ENTRY,
                        _metadata_entry_rows(execution.identity.value, metadata),
                    )
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            return created

    def get_execution(self, execution_id: str) -> Execution | None:
        row = self._conn.execute(_SELECT_RUN, (execution_id,)).fetchone()
        if row is None:
            return None
        return _row_to_execution(row)

    def touch_last_contact(self, execution_id: str, contacted_at: datetime) -> bool:
        with self._lock:
            formatted = _fixed_width_isoformat(contacted_at)
            cursor = self._conn.execute(_TOUCH_LAST_CONTACT, (formatted, execution_id, formatted))
            return cursor.rowcount == 1

    def count_executions(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM run").fetchone()
        return int(row[0])

    def get_results(self, execution_id: str) -> Sequence[Result]:
        rows = self._conn.execute(_SELECT_RESULTS_FOR_RUN, (execution_id,)).fetchall()
        return [_row_to_result(row) for row in rows]

    def count_results(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM result").fetchone()
        return int(row[0])

    def get_catalogue_entry(self, node_id: str) -> CatalogueEntry | None:
        row = self._conn.execute(_SELECT_TEST_CASE, (node_id,)).fetchone()
        if row is None:
            return None
        return _row_to_catalogue_entry(row)

    def list_runs(
        self,
        *,
        limit: int,
        offset: int,
        metadata_key: str | None = None,
        metadata_value: str | None = None,
    ) -> Page[RunListEntry]:
        # Fetch `min(limit, 200) + 1` rows (design.md D61): the extra row is
        # what distinguishes a truncated page from an exhausted one without
        # a second `COUNT` query that could race the first.
        page_limit = min(limit, MAX_PAGE_ITEMS)
        if metadata_key is not None and metadata_value is not None:
            rows = self._conn.execute(
                _LIST_RUNS_BY_METADATA,
                (
                    LIST_COMMIT_SUBJECT_CHARS,
                    LIST_COMMIT_SUBJECT_CHARS,
                    metadata_key,
                    metadata_value,
                    page_limit + 1,
                    offset,
                ),
            ).fetchall()
        else:
            rows = self._conn.execute(
                _LIST_RUNS,
                (
                    LIST_COMMIT_SUBJECT_CHARS,
                    LIST_COMMIT_SUBJECT_CHARS,
                    page_limit + 1,
                    offset,
                ),
            ).fetchall()
        has_more = len(rows) > page_limit
        items = tuple(_row_to_run_list_entry(row) for row in rows[:page_limit])
        return Page(items=items, has_more=has_more)

    def count_runs_predating_metadata_key(self, key: str) -> int:
        row = self._conn.execute(_METADATA_KEY_FIRST_SEEN, (key,)).fetchone()
        first_seen = row[0] if row else None
        if first_seen is None:
            return self.count_executions()
        before = self._conn.execute(_COUNT_RUNS_BEFORE, (first_seen,)).fetchone()
        return int(before[0])

    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        row = self._conn.execute(_SELECT_RUN_DETAIL, (execution_id,)).fetchone()
        if row is None:
            return None
        *execution_row, last_contact_at = row
        return RunDetail(
            execution=_row_to_execution(tuple(execution_row)),
            last_contact_at=(
                datetime.fromisoformat(last_contact_at)
                if isinstance(last_contact_at, str)
                else None
            ),
        )

    def list_results(self, execution_id: str, *, limit: int, offset: int) -> Page[ResultListEntry]:
        # The paginated, LEAN sibling of `get_results` (design.md D57, D76):
        # same `min(limit, 200) + 1` clamp/`has_more` mechanism as
        # `list_runs`, with the failure data bounded in SQL before it ever
        # leaves SQLite (design.md D76).
        page_limit = min(limit, MAX_PAGE_ITEMS)
        rows = self._conn.execute(
            _LIST_RESULTS,
            (
                LIST_FAILURE_MESSAGE_CHARS,
                LIST_FAILURE_MESSAGE_CHARS,
                execution_id,
                page_limit + 1,
                offset,
            ),
        ).fetchall()
        has_more = len(rows) > page_limit
        items = tuple(_row_to_result_list_entry(row) for row in rows[:page_limit])
        return Page(items=items, has_more=has_more)

    def get_result(self, execution_id: str, *, node_id: str) -> Result | None:
        row = self._conn.execute(_SELECT_RESULT, (execution_id, node_id)).fetchone()
        if row is None:
            return None
        return _row_to_result(row)

    def list_history(self, *, node_id: str, limit: int, offset: int) -> Page[HistoryEntry]:
        # `node_id` is always a bound parameter, never interpolated into SQL
        # (design.md D63; matches `_resolve_test_case_ids`'s discipline).
        page_limit = min(limit, MAX_PAGE_ITEMS)
        rows = self._conn.execute(
            _LIST_HISTORY,
            (
                LIST_COMMIT_SUBJECT_CHARS,
                LIST_COMMIT_SUBJECT_CHARS,
                node_id,
                page_limit + 1,
                offset,
            ),
        ).fetchall()
        has_more = len(rows) > page_limit
        items = tuple(_row_to_history_entry(row) for row in rows[:page_limit])
        return Page(items=items, has_more=has_more)

    def list_settings(self, namespace: str) -> Sequence[UserSetting]:
        rows = self._conn.execute(_LIST_SETTINGS, (namespace,)).fetchall()
        return tuple(_row_to_user_setting(row) for row in rows)

    def upsert_setting(self, namespace: str, key: str, *, value: str, updated_at: datetime) -> bool:
        # Same existence-probe-then-upsert shape as `record_session`
        # (design.md D26): `rowcount` cannot distinguish insert from update
        # under `DO UPDATE`.
        formatted = _fixed_width_isoformat(updated_at)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                probe = self._conn.execute(_PROBE_SETTING_EXISTS, (namespace, key)).fetchone()
                created = probe is None
                self._conn.execute(_UPSERT_SETTING, (namespace, key, value, formatted))
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            return created

    def delete_setting(self, namespace: str, key: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(_DELETE_SETTING, (namespace, key))
            return cursor.rowcount == 1

    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]:
        rows = self._conn.execute(_SELECT_RUN_CASE_OUTCOMES, (execution_id,)).fetchall()
        return tuple((cast(str, file_path), cast(str, outcome)) for file_path, outcome in rows)

    def close(self) -> None:
        self._conn.close()
