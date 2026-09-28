"""PostgreSQL adapter for `ExecutionStore`.

Several server processes may share one database, so nothing here relies on
a lock inside the process. Every call borrows a pooled connection and runs
in a transaction of its own, and the database serialises what must be
serialised:

- A report is one transaction. The run upsert's `ON CONFLICT (id) DO UPDATE
  ... WHERE run.exit_status IS NULL AND excluded.exit_status IS NOT NULL`
  applies a finish over a start, never the reverse, as the SQLite adapter
  does, and only over a run the same user recorded. Whether it created the
  run is read from that statement itself -- `RETURNING xmax = 0` is true
  only for a row it inserted, and it returns no row when the conflict's
  `WHERE` declined -- never from a probe another process could overtake.
  The run row is then locked, so concurrent reports of one run write their
  results and count their metadata one at a time, and a report of another
  user's run, or of another project's, is refused there, rolling back a
  transaction that changed nothing. A report naming a project that does
  not exist is refused before anything is written; projects are never
  renamed or deleted, so one found stays.
- A user name is its key, so `create_user` is one insert that does nothing
  on a taken name, and `create_token` one insert that selects its user:
  users are never deleted, so the user it finds stays.
- The catalogue is upserted in node-id order, so two reports sharing tests
  lock those rows in one order and never deadlock. `first_seen_at` takes
  the earlier time, `last_seen_at` the later, and the identity follows the
  newest run, as in the SQLite adapter.
- `upsert_setting` counts a namespace's keys and writes under an advisory
  lock on the namespace, held to the end of its transaction.
- `set_member` probes the project, then the user, then upserts the member
  row, in one transaction. Projects and users are never deleted, so what
  the probes find stays, and the foreign keys back them; whether it added
  the row is read from the upsert's `RETURNING xmax = 0`, so of two calls
  adding one member exactly one says it did. The write locks the one
  member row, and an insert's foreign keys take `FOR KEY SHARE` on the
  project and user rows, which conflicts with nothing else the store takes
  on them.
  `remove_member` is one `DELETE`; a set and a remove racing on one row end
  as whichever commits last.
- `touch_last_contact` is one conditional `UPDATE`, which never moves the
  contact backwards.
- A transaction ended by a serialization failure or a deadlock is run again,
  a bounded number of times; any other error propagates.
- A read that must see one state -- a run page with its metadata horizons, a
  run's metadata -- is one `REPEATABLE READ` transaction; every other read is
  a single statement.

Timestamps are `timestamptz`, compared as instants and read back in UTC,
the zone every session is set to. PostgreSQL text cannot hold U+0000, so it
becomes U+FFFD in everything written, and a lookup by a value holding one
matches nothing, as it would in a store that never held one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import TypeVar, cast

from psycopg import errors

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
from vantage.core.domain.execution import Execution, Identity, VcsContext
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES
from vantage.core.domain.projection import (
    LIST_COMMIT_SUBJECT_CHARS,
    LIST_FAILURE_MESSAGE_CHARS,
)
from vantage.core.domain.projects import DEFAULT_PROJECT, Membership, Project, check_role
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
    ForeignRunError,
    HistoryEntry,
    MetadataEntry,
    MetadataFile,
    NamespaceFullError,
    Page,
    ProjectExistsError,
    ProjectMismatchError,
    ProjectSetting,
    ResultListEntry,
    RunDetail,
    RunKey,
    RunListEntry,
    RunMetadata,
    UnknownProjectError,
    UnknownUserError,
    UserExistsError,
)
from vantage.storage.postgres.connection import (
    PgConnection,
    live_connection,
    open_pool,
    prepare_database,
)

T = TypeVar("T")
Row = tuple[object, ...]

# How many times a transaction is run before its serialization failure or
# deadlock propagates.
_MAX_ATTEMPTS = 5

# The first key of `upsert_setting`'s advisory lock ("vset"), vantage's own
# so it never meets another application's lock on the same database.
_SETTINGS_LOCK_CLASS = 0x76736574

_SNAPSHOT = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"

# `last_contact_at` and `recorded_by` are set on the insert branch only.
# The `vcs_*` columns update under the `exit_status` guard, each through
# `COALESCE`, so a report without VCS data never nulls one an earlier report
# recorded, and the truncation flag follows whichever subject is kept. The
# guard names the recorder too, so another user's report never finishes a
# run, even before `record_session` refuses it.
_UPSERT_RUN = """
    INSERT INTO vantage.run AS run (
        id, project, received_at, last_contact_at, started_at, finished_at,
        exit_status, interrupted, interrupt_reason,
        vcs_commit, vcs_branch, vcs_commit_subject, vcs_commit_subject_truncated,
        vcs_dirty, vcs_root, recorded_by
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
       AND run.recorded_by IS NOT DISTINCT FROM excluded.recorded_by
       AND run.project = excluded.project
    RETURNING xmax = 0
"""

# The conflict path of `_UPSERT_RUN` already holds this lock; taking it
# explicitly holds it on every path, so the exit status it reads is still the
# stored one.
_LOCK_RUN = "SELECT exit_status, recorded_by, project FROM vantage.run WHERE id = %s FOR UPDATE"

_PROBE_PROJECT = "SELECT 1 FROM vantage.project WHERE name = %s"

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

# `last_contact_at`, `recorded_by` and `project` come last, so the first
# twelve values decode as an `Execution`.
_SELECT_RUN = f"""
    SELECT {_EXECUTION_COLUMNS}, run.last_contact_at, run.recorded_by, run.project
    FROM vantage.run AS run WHERE run.id = %s
"""  # noqa: S608

_SELECT_RUN_LIST = f"""
    SELECT {_LIST_EXECUTION_COLUMNS}, run.last_contact_at, run.recorded_by
    FROM vantage.run AS run
"""  # noqa: S608

_LIST_RUNS = f"""
    {_SELECT_RUN_LIST}
    WHERE run.project = %s
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT %s OFFSET %s
"""  # noqa: S608

# The runs past a `RunKey` in the newest-first order, bound as its
# `started_at`, then its id: a row comparison, which
# `run_project_started_at`'s `(project, started_at, id)` serves as one range.
_AFTER_RUN_KEY = "(run.started_at, run.id) < (%s, %s)"

_LIST_RUNS_AFTER = f"""
    {_SELECT_RUN_LIST}
    WHERE run.project = %s AND {_AFTER_RUN_KEY}
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT %s OFFSET %s
"""  # noqa: S608

# The runs holding one `(key, value)` pair, bound as key, key, value, value.
# The digests find the rows through `run_metadata_key_value`; comparing the
# text as well keeps the match exact. `value` is NULL for a row without a
# captured value, and NULL equals nothing, so such a key never matches.
_RUNS_HOLDING_PAIR = """
    SELECT rm.run_id FROM vantage.run_metadata rm
    WHERE vantage.text_key(rm.key) = vantage.text_key(%s) AND rm.key = %s
      AND vantage.text_key(rm.value) = vantage.text_key(%s) AND rm.value = %s
"""


def _list_runs_by_metadata(pair_count: int, *, after: bool = False) -> str:
    """`_LIST_RUNS` narrowed to the runs holding each of `pair_count` pairs,
    then to the project, and with `after` to the runs past a `RunKey`
    (`_AFTER_RUN_KEY`), bound in that order before the limit and offset.
    Only the module's own constants are interpolated."""
    holding_every_pair = " INTERSECT ".join([_RUNS_HOLDING_PAIR] * pair_count)
    past_key = f"AND {_AFTER_RUN_KEY}" if after else ""
    return f"""
        {_SELECT_RUN_LIST}
        WHERE run.id IN ({holding_every_pair}) AND run.project = %s {past_key}
        ORDER BY run.started_at DESC, run.id DESC
        LIMIT %s OFFSET %s
    """  # noqa: S608


# How many of a project's runs started before `key` first appeared in it,
# bound as project, project, key, key, project. `first_seen` is the earliest
# start among the project's runs holding any row for the key, whatever its
# status or source; a key none of them ever carried has none, and every run
# of the project predates it.
_COUNT_RUNS_PREDATING_KEY = """
    SELECT CASE WHEN first_seen.started_at IS NULL
                THEN (SELECT count(*) FROM vantage.run WHERE run.project = %s)
                ELSE (SELECT count(*) FROM vantage.run
                      WHERE run.project = %s AND run.started_at < first_seen.started_at)
           END
    FROM (
        SELECT min(run.started_at) AS started_at
        FROM vantage.run_metadata rm
        JOIN vantage.run AS run ON run.id = rm.run_id
        WHERE vantage.text_key(rm.key) = vantage.text_key(%s) AND rm.key = %s
          AND run.project = %s
    ) AS first_seen
"""

_COUNT_PROJECT_RUNS = "SELECT count(*) FROM vantage.run WHERE project = %s"

_COUNT_RUNS = "SELECT count(*) FROM vantage.run"

# Every right-hand side reads the row as it was before the update. The
# identity columns and `last_seen_run_id` follow the newest run, so a late
# report of an older one never changes what newer runs read. `RETURNING id`
# answers with the row's id whichever branch ran.
_UPSERT_TEST_CASE = """
    INSERT INTO vantage.test_case AS tc (
        project, node_id, file_path, class_name, function_name,
        param_id, first_seen_at, last_seen_at, last_seen_run_id
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (project, vantage.text_key(node_id)) DO UPDATE SET
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

# `source_file` keeps the database's collation, so the code point order the
# other adapters give is asked for here.
_SELECT_RUN_METADATA_FILES = """
    SELECT source_file, content_type, status
    FROM vantage.run_metadata_file WHERE run_id = %s
    ORDER BY source_file COLLATE "C"
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
    WHERE tc.project = %s
      AND vantage.text_key(tc.node_id) = vantage.text_key(%s) AND tc.node_id = %s
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT %s OFFSET %s
"""  # noqa: S608

_LIST_HISTORY_AFTER = f"""
    SELECT {_LIST_EXECUTION_COLUMNS}, run.last_contact_at, r.outcome, r.duration
    FROM vantage.test_case tc
    JOIN vantage.result r ON r.test_case_id = tc.id
    JOIN vantage.run AS run ON run.id = r.run_id
    WHERE tc.project = %s
      AND vantage.text_key(tc.node_id) = vantage.text_key(%s) AND tc.node_id = %s
      AND {_AFTER_RUN_KEY}
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT %s OFFSET %s
"""  # noqa: S608

_SELECT_TEST_CASE = """
    SELECT node_id, file_path, class_name, function_name, param_id,
           first_seen_at, last_seen_at, last_seen_run_id
    FROM vantage.test_case
    WHERE project = %s
      AND vantage.text_key(node_id) = vantage.text_key(%s) AND node_id = %s
"""

_LIST_SETTINGS = """
    SELECT project, namespace, key, value, updated_at
    FROM vantage.project_setting WHERE project = %s AND namespace = %s ORDER BY key
"""

# Keyed on the project and the namespace together; a project name holds no
# `/`, so the text names one pair, and a hash collision only makes two
# writers wait for each other.
_LOCK_NAMESPACE = "SELECT pg_advisory_xact_lock(%s, hashtext(%s || '/' || %s))"

_PROBE_SETTING = """
    SELECT 1 FROM vantage.project_setting WHERE project = %s AND namespace = %s AND key = %s
"""

_COUNT_SETTINGS = """
    SELECT count(*) FROM vantage.project_setting WHERE project = %s AND namespace = %s
"""

_UPSERT_SETTING = """
    INSERT INTO vantage.project_setting (project, namespace, key, value, updated_at)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (project, namespace, key) DO UPDATE SET
        value = excluded.value,
        updated_at = excluded.updated_at
    RETURNING xmax = 0
"""

_DELETE_SETTING = """
    DELETE FROM vantage.project_setting WHERE project = %s AND namespace = %s AND key = %s
"""

# Inserts nothing on a taken name, so the row count tells the two apart.
_INSERT_PROJECT = """
    INSERT INTO vantage.project (name, created_at) VALUES (%s, %s)
    ON CONFLICT (name) DO NOTHING
"""

_SELECT_PROJECT = "SELECT name, created_at FROM vantage.project WHERE name = %s"

_LIST_PROJECTS = "SELECT name, created_at FROM vantage.project ORDER BY name"

# Members, keyed by project and user, both `COLLATE "C"`: code point order,
# as the other adapters sort.
_SELECT_MEMBER_ROLE = """
    SELECT role FROM vantage.project_member WHERE project = %s AND account = %s
"""

_LIST_MEMBERS = """
    SELECT project, account, role FROM vantage.project_member
    WHERE project = %s ORDER BY account
"""

_LIST_MEMBERSHIPS = """
    SELECT project, account, role FROM vantage.project_member
    WHERE account = %s ORDER BY project
"""

_PROBE_USER = "SELECT 1 FROM vantage.account WHERE name = %s"

# `RETURNING xmax = 0` is true only for a row this statement inserted.
_UPSERT_MEMBER = """
    INSERT INTO vantage.project_member (project, account, role) VALUES (%s, %s, %s)
    ON CONFLICT (project, account) DO UPDATE SET role = excluded.role
    RETURNING xmax = 0
"""

_DELETE_MEMBER = "DELETE FROM vantage.project_member WHERE project = %s AND account = %s"

_SELECT_RUN_CASE_OUTCOMES = """
    SELECT tc.file_path, r.outcome
    FROM vantage.result r
    JOIN vantage.test_case tc ON tc.id = r.test_case_id
    WHERE r.run_id = %s
    ORDER BY r.id
"""


_PROBE_ANY_USER = "SELECT EXISTS (SELECT 1 FROM vantage.account)"

_INSERT_USER = """
    INSERT INTO vantage.account (name, admin, disabled, created_at)
    VALUES (%s, %s, false, %s)
    ON CONFLICT (name) DO NOTHING
"""

_USER_COLUMNS = "name, admin, disabled, created_at, password_hash IS NOT NULL"

# One statement, so of two servers starting on one new database, one
# inserts: the other waits on the first's row and then finds a user.
_INSERT_FIRST_ADMIN = f"""
    INSERT INTO vantage.account (name, admin, disabled, created_at, password_hash)
    SELECT %s, true, false, %s, %s
    WHERE NOT EXISTS (SELECT 1 FROM vantage.account)
      AND NOT EXISTS (SELECT 1 FROM vantage.meta WHERE key = 'origin' AND value = 'local')
    ON CONFLICT (name) DO NOTHING
    RETURNING {_USER_COLUMNS}
"""  # noqa: S608

_SELECT_PASSWORD_HASH = """
    SELECT password_hash FROM vantage.account WHERE name = %s AND NOT disabled
"""  # noqa: S105

_SET_PASSWORD = "UPDATE vantage.account SET password_hash = %s WHERE name = %s RETURNING name"  # noqa: S105

# The compare-and-set a password change checked against the current
# password makes: it loses to any change made while it was checked.
_REPLACE_PASSWORD = """
    UPDATE vantage.account SET password_hash = %s
    WHERE name = %s AND password_hash = %s AND NOT disabled
    RETURNING name
"""  # noqa: S105

# Login tokens are the only ones with an expiry.
_REVOKE_LOGIN_TOKENS = """
    UPDATE vantage.access_token SET revoked_at = %s
    WHERE account = %s AND expires_at IS NOT NULL AND revoked_at IS NULL
"""  # noqa: S105

_SELECT_USER = f"SELECT {_USER_COLUMNS} FROM vantage.account WHERE name = %s"  # noqa: S608

_LIST_USERS = f"SELECT {_USER_COLUMNS} FROM vantage.account ORDER BY name"  # noqa: S608

# A NULL parameter leaves its column as it is.
_UPDATE_USER = f"""
    UPDATE vantage.account
       SET admin = COALESCE(%s, admin), disabled = COALESCE(%s, disabled)
     WHERE name = %s
    RETURNING {_USER_COLUMNS}
"""  # noqa: S608

_TOKEN_COLUMNS = (
    "id, account, label, can_read, can_record, can_manage, can_admin,"  # noqa: S105
    " created_at, revoked_at, expires_at"
)

# Inserts nothing, and returns no row, when there is no such user.
_INSERT_TOKEN = f"""
    INSERT INTO vantage.access_token (
        account, digest, label, can_read, can_record, can_manage, can_admin, created_at
    )
    SELECT name, %s, %s, %s, %s, %s, %s, %s FROM vantage.account WHERE name = %s
    RETURNING {_TOKEN_COLUMNS}
"""  # noqa: S608

# Inserts nothing unless the user is enabled and still has the hash the
# password was checked against. `FOR SHARE` conflicts with the row lock a
# password change or a disable takes, so a login either commits first --
# and the change then revokes its token -- or waits for the change and,
# reading the row again, inserts nothing: under READ COMMITTED a plain
# read would have gone on with the old hash.
_INSERT_LOGIN_TOKEN = f"""
    INSERT INTO vantage.access_token (
        account, digest, label, can_read, can_record, can_manage, can_admin,
        created_at, expires_at
    )
    SELECT a.name, %s, %s, true, false, true, a.admin, %s, %s
      FROM vantage.account a
     WHERE a.name = %s AND a.password_hash = %s AND NOT a.disabled
       FOR SHARE OF a
    RETURNING {_TOKEN_COLUMNS}
"""  # noqa: S608

# Never the token just made, whatever its expiry.
_DELETE_EXPIRED_LOGIN_TOKENS = """
    DELETE FROM vantage.access_token WHERE account = %s AND expires_at <= %s AND id <> %s
"""  # noqa: S105

_SELECT_TOKEN = f"SELECT {_TOKEN_COLUMNS} FROM vantage.access_token WHERE id = %s"  # noqa: S608

_LIST_TOKENS = f"SELECT {_TOKEN_COLUMNS} FROM vantage.access_token ORDER BY id"  # noqa: S608

_LIST_USER_TOKENS = f"""
    SELECT {_TOKEN_COLUMNS} FROM vantage.access_token WHERE account = %s ORDER BY id
"""  # noqa: S608

# `account = COALESCE(%s, account)`: with no user given, any token.
_REVOKE_TOKEN = """
    UPDATE vantage.access_token SET revoked_at = %s
    WHERE id = %s AND revoked_at IS NULL AND account = COALESCE(%s::text, account)
"""  # noqa: S105

_AUTHENTICATE = """
    SELECT a.name, a.admin, t.can_read, t.can_record, t.can_manage, t.can_admin,
           t.id, t.expires_at
    FROM vantage.access_token t
    JOIN vantage.account a ON a.name = t.account
    WHERE t.digest = %s AND t.revoked_at IS NULL AND NOT a.disabled
      AND (t.expires_at IS NULL OR t.expires_at > %s)
"""

# The ids a `bigint` identity column can hold; a lookup by any other
# matches nothing.
_MAX_ID = 2**63 - 1


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


def _row_to_membership(row: Row) -> Membership:
    project, account, role = row
    return Membership(project=cast(str, project), user=cast(str, account), role=cast(str, role))


def _row_to_user(row: Row) -> User:
    name, admin, disabled, created_at, has_password = row
    return User(
        name=cast(str, name),
        admin=bool(admin),
        disabled=bool(disabled),
        created_at=_utc(created_at),
        has_password=bool(has_password),
    )


def _row_to_token(row: Row) -> Token:
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
        created_at=_utc(created_at),
        revoked_at=_opt_utc(revoked_at),
        expires_at=_opt_utc(expires_at),
    )


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
    """A `NOT NULL` timestamp, with the `timezone.utc` the other adapters
    return rather than the session zone psycopg attaches."""
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
        _decode_execution(row[:12]),
        last_contact_at=_opt_utc(row[12]),
        recorded_by=cast("str | None", row[13]),
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


def _run_key_params(after: RunKey | None) -> tuple[object, ...]:
    """`_AFTER_RUN_KEY`'s two parameters, or none without a key."""
    return () if after is None else (after.started_at, after.run_id)


def _row_to_metadata_file(row: Row) -> MetadataFile:
    """A `_SELECT_RUN_METADATA_FILES` row."""
    source_file, content_type, status = row
    return MetadataFile(
        source_file=cast(str, source_file),
        content_type=cast(str, content_type),
        status=cast(str, status),
    )


def _row_to_catalogue_entry(row: Row) -> CatalogueEntry:
    first_seen_at, last_seen_at, last_seen_run_id = row[5:]
    return CatalogueEntry(
        identity=_decode_identity(row[:5]),
        first_seen_at=_utc(first_seen_at),
        last_seen_at=_utc(last_seen_at),
        last_seen_run_id=cast("str | None", last_seen_run_id),
    )


def _row_to_project(row: Row) -> Project:
    name, created_at = row
    return Project(name=cast(str, name), created_at=_utc(created_at))


def _row_to_project_setting(row: Row) -> ProjectSetting:
    project, namespace, key, value, updated_at = row
    return ProjectSetting(
        project=cast(str, project),
        namespace=cast(str, namespace),
        key=cast(str, key),
        value=cast(str, value),
        updated_at=_utc(updated_at),
    )


def _catalogue_rows(project: str, execution: Execution, results: Sequence[Result]) -> list[Row]:
    """One `_UPSERT_TEST_CASE` row per node id of `project`, in node-id
    order: every report locks the rows it shares with another in the same
    order, and a report has one project."""
    run_id = execution.identity.value
    by_node_id: dict[str, CaseIdentity] = {
        _stored(result.identity.node_id): result.identity for result in results
    }
    return [
        _params(
            project,
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
    return {cast(str, row[1]): test_case_id for row, test_case_id in zip(rows, ids)}


def _count_runs_predating(conn: PgConnection, project: str, key: str) -> int:
    if _unmatchable(key):
        row = conn.execute(_COUNT_PROJECT_RUNS, (project,)).fetchone()
    else:
        row = conn.execute(
            _COUNT_RUNS_PREDATING_KEY, (project, project, key, key, project)
        ).fetchone()
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
                with live_connection(self._pool) as conn, conn.transaction():
                    if snapshot:
                        conn.execute(_SNAPSHOT)
                    return work(conn)
            except (errors.SerializationFailure, errors.DeadlockDetected):
                if attempt >= _MAX_ATTEMPTS:
                    raise
                attempt += 1

    def _fetchone(self, sql: str, params: Sequence[object] = ()) -> Row | None:
        """One statement, its own transaction on an autocommit connection."""
        with live_connection(self._pool) as conn:
            return conn.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: Sequence[object]) -> list[Row]:
        with live_connection(self._pool) as conn:
            return conn.execute(sql, params).fetchall()

    def _count(self, sql: str) -> int:
        row = self._fetchone(sql)
        return 0 if row is None else int(cast(int, row[0]))

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
        # One transaction, in the order every foreign key needs: run,
        # catalogue, results, metadata.
        if _unmatchable(project):
            raise UnknownProjectError("there is no such project")
        run_id = execution.identity.value
        run_row = _params(
            run_id,
            project,
            received_at,
            received_at,
            execution.started_at,
            execution.finished_at,
            execution.exit_status,
            execution.interrupted,
            execution.interrupt_reason,
            *_vcs_columns(execution.vcs),
            recorded_by,
        )
        catalogue_rows = _catalogue_rows(project, execution, results)
        file_rows = [
            _params(run_id, file.source_file, file.content_type, file.status)
            for file in metadata.files
        ]
        entry_rows = _metadata_entry_rows(run_id, metadata)

        def write(conn: PgConnection) -> bool:
            # Projects are never deleted, so the one found stays until the
            # commit; the run's foreign key would refuse a missing one too,
            # but only after writing, and as a driver error.
            if conn.execute(_PROBE_PROJECT, (project,)).fetchone() is None:
                raise UnknownProjectError(f"there is no project named {project!r}")
            upserted = conn.execute(_UPSERT_RUN, run_row).fetchone()
            locked = conn.execute(_LOCK_RUN, (run_id,)).fetchone()
            # The upsert declined to touch another user's run, or another
            # project's, and raising rolls back a transaction that changed
            # nothing.
            if locked is not None and locked[1] != recorded_by:
                raise ForeignRunError(f"run {run_id} was recorded by another user")
            if locked is not None and locked[2] != project:
                raise ProjectMismatchError(f"run {run_id} was recorded in another project")
            if upserted is None and locked is not None and locked[0] is not None:
                # The upsert changed nothing and the run has an exit status,
                # so it was finished before this report. A finished run is
                # final: a report reaching it later is a replay and adds
                # nothing, whatever results it carries.
                return False
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
        with live_connection(self._pool) as conn:
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

    def get_catalogue_entry(self, node_id: str, *, project: str) -> CatalogueEntry | None:
        if _unmatchable(node_id, project):
            return None
        row = self._fetchone(_SELECT_TEST_CASE, (project, node_id, node_id))
        return None if row is None else _row_to_catalogue_entry(row)

    def list_runs(
        self, *, project: str, limit: int, offset: int, after: RunKey | None = None
    ) -> Page[RunListEntry]:
        page_limit = min(limit, MAX_PAGE_ITEMS)
        if _unmatchable(project):
            return Page(items=(), has_more=False)
        sql = _LIST_RUNS if after is None else _LIST_RUNS_AFTER
        rows = self._fetchall(sql, (project, *_run_key_params(after), page_limit + 1, offset))
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
        # A repeated pair narrows nothing further.
        pairs = list(dict.fromkeys(filters))
        keys = list(dict.fromkeys(key for key, _value in pairs))

        def read(conn: PgConnection) -> tuple[list[Row], tuple[int, ...]]:
            if any(_unmatchable(key, value) for key, value in pairs):
                rows: list[Row] = []
            else:
                if pairs:
                    sql = _list_runs_by_metadata(len(pairs), after=after is not None)
                else:
                    sql = _LIST_RUNS if after is None else _LIST_RUNS_AFTER
                params = [
                    *(part for key, value in pairs for part in (key, key, value, value)),
                    project,
                    *_run_key_params(after),
                    page_limit + 1,
                    offset,
                ]
                rows = conn.execute(sql, params).fetchall()
            return rows, tuple(_count_runs_predating(conn, project, key) for key in keys)

        if _unmatchable(project):
            # No project holds U+0000, so it has no runs to page or count.
            return Page(items=(), has_more=False), tuple(0 for _key in keys)

        rows, predating = self._transaction(read, snapshot=True)
        return _page(rows, page_limit, _row_to_run_list_entry), predating

    def get_run_detail(self, execution_id: str) -> RunDetail | None:
        if _unmatchable(execution_id):
            return None
        row = self._fetchone(_SELECT_RUN, (execution_id,))
        if row is None:
            return None
        return RunDetail(
            execution=_decode_execution(row[:12]),
            last_contact_at=_opt_utc(row[12]),
            recorded_by=cast("str | None", row[13]),
            project=cast(str, row[14]),
        )

    def get_run_metadata(self, execution_id: str) -> RunMetadata | None:
        if _unmatchable(execution_id):
            return None

        def read(conn: PgConnection) -> RunMetadata | None:
            if conn.execute(_PROBE_RUN, (execution_id,)).fetchone() is None:
                return None
            file_rows = conn.execute(_SELECT_RUN_METADATA_FILES, (execution_id,)).fetchall()
            entry_rows = conn.execute(_SELECT_RUN_METADATA, (execution_id,)).fetchall()
            return RunMetadata(
                files=tuple(_row_to_metadata_file(row) for row in file_rows),
                entries=tuple(_row_to_metadata_entry(row) for row in entry_rows),
            )

        return self._transaction(read, snapshot=True)

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
        if _unmatchable(node_id, project):
            return Page(items=(), has_more=False)
        rows = self._fetchall(
            _LIST_HISTORY if after is None else _LIST_HISTORY_AFTER,
            (project, node_id, node_id, *_run_key_params(after), page_limit + 1, offset),
        )
        return _page(rows, page_limit, _row_to_history_entry)

    def list_settings(self, namespace: str, *, project: str) -> Sequence[ProjectSetting]:
        if _unmatchable(namespace, project):
            return ()
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
        if _unmatchable(project):
            raise UnknownProjectError("there is no such project")
        namespace, key, value = _stored(namespace), _stored(key), _stored(value)

        def write(conn: PgConnection) -> bool:
            # Every writer of the project's namespace takes this lock first,
            # so the count below cannot be passed by a key another process
            # adds before this transaction commits.
            conn.execute(_LOCK_NAMESPACE, (_SETTINGS_LOCK_CLASS, project, namespace))
            if conn.execute(_PROBE_PROJECT, (project,)).fetchone() is None:
                raise UnknownProjectError(f"there is no project named {project!r}")
            if (
                max_keys is not None
                and conn.execute(_PROBE_SETTING, (project, namespace, key)).fetchone() is None
            ):
                counted = conn.execute(_COUNT_SETTINGS, (project, namespace)).fetchone()
                held = 0 if counted is None else int(cast(int, counted[0]))
                if held >= max_keys:
                    raise NamespaceFullError(f"{namespace!r} already holds {held} keys")
            upserted = conn.execute(
                _UPSERT_SETTING, (project, namespace, key, value, updated_at)
            ).fetchone()
            return upserted is not None and bool(upserted[0])

        return self._transaction(write)

    def delete_setting(self, namespace: str, key: str, *, project: str) -> bool:
        if _unmatchable(namespace, key, project):
            return False
        with live_connection(self._pool) as conn:
            return conn.execute(_DELETE_SETTING, (project, namespace, key)).rowcount == 1

    def get_run_case_outcomes(self, execution_id: str) -> Sequence[tuple[str, str]]:
        if _unmatchable(execution_id):
            return ()
        rows = self._fetchall(_SELECT_RUN_CASE_OUTCOMES, (execution_id,))
        return tuple((cast(str, file_path), cast(str, outcome)) for file_path, outcome in rows)

    def create_project(self, name: str, *, created_at: datetime) -> Project:
        with live_connection(self._pool) as conn:
            inserted = conn.execute(_INSERT_PROJECT, _params(name, created_at)).rowcount
        if inserted != 1:
            raise ProjectExistsError(f"there is already a project named {name!r}")
        return Project(name=name, created_at=created_at)

    def get_project(self, name: str) -> Project | None:
        if _unmatchable(name):
            return None
        row = self._fetchone(_SELECT_PROJECT, (name,))
        return None if row is None else _row_to_project(row)

    def list_projects(self) -> Sequence[Project]:
        return tuple(_row_to_project(row) for row in self._fetchall(_LIST_PROJECTS, ()))

    def get_member_role(self, user: str, *, project: str) -> str | None:
        if _unmatchable(user, project):
            return None
        row = self._fetchone(_SELECT_MEMBER_ROLE, (project, user))
        return None if row is None else cast(str, row[0])

    def list_members(self, *, project: str) -> Sequence[Membership]:
        if _unmatchable(project):
            return ()
        return tuple(_row_to_membership(row) for row in self._fetchall(_LIST_MEMBERS, (project,)))

    def list_memberships(self, user: str) -> Sequence[Membership]:
        if _unmatchable(user):
            return ()
        rows = self._fetchall(_LIST_MEMBERSHIPS, (user,))
        return tuple(_row_to_membership(row) for row in rows)

    def set_member(self, user: str, *, project: str, role: str) -> bool:
        _check_member_row(project, role)
        if _unmatchable(project):
            raise UnknownProjectError("there is no such project")

        def write(conn: PgConnection) -> bool:
            # The project is probed first, so naming neither is answered with
            # the project; a user name holding U+0000 is then refused without
            # being sent.
            if conn.execute(_PROBE_PROJECT, (project,)).fetchone() is None:
                raise UnknownProjectError(f"there is no project named {project!r}")
            if _unmatchable(user):
                raise UnknownUserError("there is no such user")
            if conn.execute(_PROBE_USER, (user,)).fetchone() is None:
                raise UnknownUserError(f"there is no user named {user!r}")
            upserted = conn.execute(_UPSERT_MEMBER, (project, user, role)).fetchone()
            return upserted is not None and bool(upserted[0])

        return self._transaction(write)

    def remove_member(self, user: str, *, project: str) -> bool:
        if _unmatchable(user, project):
            return False
        with live_connection(self._pool) as conn:
            return conn.execute(_DELETE_MEMBER, (project, user)).rowcount == 1

    def access_required(self) -> bool:
        row = self._fetchone(_PROBE_ANY_USER)
        return row is not None and bool(row[0])

    def create_first_admin(
        self, name: str, *, password_hash: str, created_at: datetime
    ) -> User | None:
        row = self._fetchone(_INSERT_FIRST_ADMIN, _params(name, created_at, password_hash))
        return None if row is None else _row_to_user(row)

    def create_user(self, name: str, *, admin: bool, created_at: datetime) -> User:
        with live_connection(self._pool) as conn:
            inserted = conn.execute(_INSERT_USER, _params(name, admin, created_at)).rowcount
        if inserted != 1:
            raise UserExistsError(f"there is already a user named {name!r}")
        return User(name=name, admin=admin, disabled=False, created_at=created_at)

    def get_user(self, name: str) -> User | None:
        if _unmatchable(name):
            return None
        row = self._fetchone(_SELECT_USER, (name,))
        return None if row is None else _row_to_user(row)

    def list_users(self) -> Sequence[User]:
        return tuple(_row_to_user(row) for row in self._fetchall(_LIST_USERS, ()))

    def update_user(
        self, name: str, *, admin: bool | None = None, disabled: bool | None = None
    ) -> User | None:
        if _unmatchable(name):
            return None
        row = self._fetchone(_UPDATE_USER, (admin, disabled, name))
        return None if row is None else _row_to_user(row)

    def get_password_hash(self, name: str) -> str | None:
        if _unmatchable(name):
            return None
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
        if _unmatchable(name):
            return False

        def work(conn: PgConnection) -> bool:
            # The UPDATE locks the account row first, as a login's
            # `FOR SHARE` does, so the two never deadlock; the revocation,
            # a statement of its own, sees any login that committed before
            # the lock was granted.
            if replacing is None:
                changed = conn.execute(_SET_PASSWORD, (password_hash, name)).fetchone()
            else:
                changed = conn.execute(
                    _REPLACE_PASSWORD, (password_hash, name, replacing)
                ).fetchone()
            if changed is None:
                return False
            conn.execute(_REVOKE_LOGIN_TOKENS, (changed_at, name))
            return True

        return self._transaction(work)

    def create_login_token(
        self,
        name: str,
        *,
        password_hash: str,
        digest: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> Token | None:
        if _unmatchable(name):
            return None

        def work(conn: PgConnection) -> Token | None:
            row = conn.execute(
                _INSERT_LOGIN_TOKEN,
                (digest, LOGIN_TOKEN_LABEL, created_at, expires_at, name, password_hash),
            ).fetchone()
            if row is None:
                return None
            token = _row_to_token(row)
            conn.execute(_DELETE_EXPIRED_LOGIN_TOKENS, (name, created_at, token.id))
            return token

        # Two logins of one user deleting the same expired rows can
        # deadlock; `_transaction` runs the loser again.
        return self._transaction(work)

    def create_token(
        self,
        user: str,
        *,
        digest: str,
        label: str,
        scopes: frozenset[str],
        created_at: datetime,
    ) -> Token:
        row = None
        if not _unmatchable(user):
            row = self._fetchone(
                _INSERT_TOKEN,
                _params(
                    digest,
                    label,
                    READ_SCOPE in scopes,
                    RECORD_SCOPE in scopes,
                    MANAGE_SCOPE in scopes,
                    ADMIN_SCOPE in scopes,
                    created_at,
                    user,
                ),
            )
        if row is None:
            raise UnknownUserError(f"there is no user named {user!r}")
        return _row_to_token(row)

    def list_tokens(self, *, user: str | None = None) -> Sequence[Token]:
        if user is None:
            rows = self._fetchall(_LIST_TOKENS, ())
        elif _unmatchable(user):
            rows = []
        else:
            rows = self._fetchall(_LIST_USER_TOKENS, (user,))
        return tuple(_row_to_token(row) for row in rows)

    def get_token(self, token_id: int) -> Token | None:
        if not 0 < token_id <= _MAX_ID:
            return None
        row = self._fetchone(_SELECT_TOKEN, (token_id,))
        return None if row is None else _row_to_token(row)

    def revoke_token(self, token_id: int, *, revoked_at: datetime, user: str | None = None) -> bool:
        if not 0 < token_id <= _MAX_ID or (user is not None and _unmatchable(user)):
            return False
        with live_connection(self._pool) as conn:
            cursor = conn.execute(_REVOKE_TOKEN, (revoked_at, token_id, user))
            return cursor.rowcount == 1

    def authenticate(self, digest: str, *, now: datetime) -> Grant | None:
        if _unmatchable(digest):
            return None
        row = self._fetchone(_AUTHENTICATE, (digest, now))
        if row is None:
            return None
        name, admin, can_read, can_record, can_manage, can_admin, token_id, expires_at = row
        return Grant(
            user=cast(str, name),
            admin=bool(admin),
            scopes=_decode_scopes(can_read, can_record, can_manage, can_admin),
            token_id=cast(int, token_id),
            expires_at=_opt_utc(expires_at),
        )

    def close(self) -> None:
        self._pool.close()
