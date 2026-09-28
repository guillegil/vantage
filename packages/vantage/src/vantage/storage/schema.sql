-- Vantage database schema.
--
-- Applied whole, in one transaction, the first time a database is opened
-- (vantage/storage/connection.py), which also stamps `meta.schema_version`
-- in that same transaction. Every statement is IF NOT EXISTS so two
-- processes opening the same fresh database race safely.
--
-- There is no migration framework: connection.py refuses any database whose
-- stamp is absent or differs from the version the build expects. A table or
-- column is added when something writes it, never ahead of that.
--
-- Conventions: timestamps are fixed-width ISO-8601 UTC TEXT, so text order
-- is chronological order; booleans are INTEGER 0/1. A column whose content
-- is unbounded by nature carries a sibling `<name>_truncated` INTEGER NOT
-- NULL DEFAULT 0 flag.
--
-- Foreign keys are declared here but only enforced when a connection turns
-- on `PRAGMA foreign_keys=ON` (vantage/storage/connection.py, every
-- connection) -- SQLite ignores unenforced foreign keys by default.
--
-- A few values are recorded for whoever reads the database directly and
-- are returned by no route: `meta.created_at`/`created_by`,
-- `run.received_at` and `test_case.first_seen_at`/`last_seen_run_id`. The
-- tests read them to check what a write stored.

-- ---------------------------------------------------------------------------
-- meta -- `schema_version` and `origin`, written with the tables, plus the
-- best-effort `created_at`/`created_by` rows connection.py writes. `origin`
-- is `local` when pytest-vantage's local store made the database and
-- `server` otherwise, and never changes: a server gives only a `server`
-- database its first admin.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- account -- one row per user; `user` is a reserved word in PostgreSQL. A
-- user is disabled, never deleted, so every `run.recorded_by` names one,
-- and a database that has had a user always has one: the server requires
-- a token from then on. A `server` database has one from the server's
-- first start. `name` is short and lower case (`core/domain/access.py`);
-- `password_hash` is a scrypt PHC string (`core/domain/passwords.py`), or
-- NULL for a user who cannot log in.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account (
    name           TEXT PRIMARY KEY,
    admin          INTEGER NOT NULL CHECK (admin IN (0, 1)),
    disabled       INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
    created_at     TEXT NOT NULL,
    password_hash  TEXT NULL
);

-- ---------------------------------------------------------------------------
-- access_token -- one row per token ever made, revoked ones included,
-- except for login tokens, the only ones with an `expires_at`: a login
-- token's row is deleted once it has expired, at its user's next login.
-- `digest` is the token's SHA-256 in hex; the token itself is never stored.
-- Each `can_*` column is one scope the token holds, and it holds at least
-- one.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS access_token (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL REFERENCES account (name),
    digest      TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    can_read    INTEGER NOT NULL CHECK (can_read IN (0, 1)),
    can_record  INTEGER NOT NULL CHECK (can_record IN (0, 1)),
    can_manage  INTEGER NOT NULL CHECK (can_manage IN (0, 1)),
    can_admin   INTEGER NOT NULL CHECK (can_admin IN (0, 1)),
    created_at  TEXT NOT NULL,
    revoked_at  TEXT NULL,
    expires_at  TEXT NULL,
    CHECK (can_read + can_record + can_manage + can_admin > 0)
);

-- ---------------------------------------------------------------------------
-- project -- one row per project, keyed by its short, lower-case name
-- (`core/domain/projects.py`). `default` is written in the transaction that
-- creates the database (connection.py). A project is never renamed or
-- deleted, so every row naming one keeps naming one.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project (
    name        TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- project_member -- one row per member of a project, with their role in it.
-- `default` takes none: every user is an editor of it without one, and an
-- admin acts as an owner of every project (`effective_role`). The adapters
-- refuse a `default` row rather than a CHECK, so `DEFAULT_PROJECT` stays the
-- only statement of that name. Users and projects are never deleted, so a
-- row never dangles; a disabled user's rows stay, and grant nothing while
-- their tokens authenticate nothing.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_member (
    project  TEXT NOT NULL REFERENCES project (name),
    account  TEXT NOT NULL REFERENCES account (name),
    role     TEXT NOT NULL CHECK (role IN ('viewer', 'editor', 'owner')),
    PRIMARY KEY (project, account)
);

-- ---------------------------------------------------------------------------
-- run -- one row per recorded session. `project` is the one the report that
-- created the run named, and never changes. `recorded_by` is the user whose
-- token created the run, NULL when it was recorded without one.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run (
    id                            TEXT PRIMARY KEY,
    project                       TEXT NOT NULL REFERENCES project (name),
    received_at                   TEXT NOT NULL,
    last_contact_at               TEXT NULL,
    started_at                    TEXT NOT NULL,
    finished_at                   TEXT NULL,
    exit_status                   INTEGER NULL,
    interrupted                   INTEGER NOT NULL DEFAULT 0,
    interrupt_reason              TEXT NULL,
    vcs_commit                    TEXT NULL,
    vcs_branch                    TEXT NULL,
    vcs_commit_subject            TEXT NULL,
    vcs_commit_subject_truncated  INTEGER NOT NULL DEFAULT 0,
    vcs_dirty                     INTEGER NULL,
    vcs_root                      TEXT NULL,
    recorded_by                   TEXT NULL REFERENCES account (name)
);

-- ---------------------------------------------------------------------------
-- test_case -- the catalogue: one row per test ever seen in a project,
-- keyed by project and pytest node id. That pair's uniqueness comes from
-- `idx_test_case_project_node_id` below, the catalogue upsert's conflict
-- target, so two projects never share a row however alike their node ids.
-- Every result of a node id reads its file, class, function and parameter
-- from its project's row, as the project's newest run reported them;
-- `last_seen_at` decides which run that is.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_case (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    project                  TEXT NOT NULL REFERENCES project (name),
    node_id                  TEXT NOT NULL,
    file_path                TEXT NOT NULL,
    class_name               TEXT NULL,
    function_name            TEXT NOT NULL,
    param_id                 TEXT NULL,
    first_seen_at            TEXT NOT NULL,
    last_seen_at             TEXT NOT NULL,
    last_seen_run_id         TEXT NULL REFERENCES run (id)
);

-- ---------------------------------------------------------------------------
-- result -- one row per test per run. `outcome` is the test's overall
-- outcome across setup, call and teardown, not the `call` phase alone.
-- `UNIQUE(run_id, node_id)` keeps a result delivered twice (xdist reports it
-- from both worker and controller) to one row.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS result (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                   TEXT NOT NULL REFERENCES run (id),
    test_case_id             INTEGER NOT NULL REFERENCES test_case (id),
    node_id                  TEXT NOT NULL,
    outcome                  TEXT NOT NULL
        CHECK (outcome IN ('passed', 'failed', 'error', 'skipped', 'xfailed', 'xpassed')),
    duration                 REAL NULL,
    started_at               TEXT NULL,
    finished_at              TEXT NULL,
    setup_outcome            TEXT NULL,
    call_outcome             TEXT NULL,
    teardown_outcome         TEXT NULL,
    setup_duration           REAL NULL,
    call_duration            REAL NULL,
    teardown_duration        REAL NULL,
    failure_type             TEXT NULL,
    failure_message          TEXT NULL,
    failure_message_truncated INTEGER NOT NULL DEFAULT 0,
    failure_path              TEXT NULL,
    failure_lineno            INTEGER NULL,
    failure_repr              TEXT NULL,
    failure_repr_truncated    INTEGER NOT NULL DEFAULT 0,
    traceback                 TEXT NULL,
    traceback_truncated       INTEGER NOT NULL DEFAULT 0,
    skip_reason                TEXT NULL,
    skip_reason_truncated      INTEGER NOT NULL DEFAULT 0,
    xfail_reason                TEXT NULL,
    xfail_reason_truncated      INTEGER NOT NULL DEFAULT 0,
    worker_id                   TEXT NULL,
    captured_stdout              TEXT NULL,
    captured_stdout_truncated    INTEGER NOT NULL DEFAULT 0,
    captured_stderr               TEXT NULL,
    captured_stderr_truncated     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (run_id, node_id)
);

-- ---------------------------------------------------------------------------
-- project_setting -- a project's namespaced, server-persisted settings, such
-- as its section definitions. Generic storage, specific validation: `value`
-- is JSON text this schema does not describe and this adapter never parses;
-- each namespace's shape is validated by an ordinary Pydantic model in
-- `vantage.service`.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_setting (
    project     TEXT NOT NULL REFERENCES project (name),
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (project, namespace, key)
);

-- ---------------------------------------------------------------------------
-- run_metadata_file -- one row per DECLARED metadata file, captured or not,
-- so a file that contributed no keys still records why in `status`.
-- `source_file` is the declared, rootpath-relative path exactly as written --
-- never the resolved one, which is absolute and can carry a username.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_metadata_file (
    run_id       TEXT NOT NULL REFERENCES run (id),
    source_file  TEXT NOT NULL,
    content_type TEXT NOT NULL CHECK (content_type IN ('json', 'yaml')),
    status       TEXT NOT NULL CHECK (status IN (
                     'captured', 'not_found', 'path_rejected', 'too_large',
                     'not_text', 'unreadable', 'over_budget', 'malformed')),
    PRIMARY KEY (run_id, source_file)
);

-- ---------------------------------------------------------------------------
-- run_metadata -- one row per key a run reported: every key of a declared
-- file (`source` 'file', with its `source_file`), then every key the test
-- session set itself, and every declared key nothing gave a value (`source`
-- 'session', no file). `value` is NULL whenever `status` is not 'captured':
-- a key without a value is a row, never a missing row. All values are TEXT,
-- numbers included: comparison is string equality, and a declaration names
-- keys, not types. `name` is the display name that run's declaration gave
-- the key, and `declared` whether it named the key at all; a file's key
-- always is.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_metadata (
    run_id       TEXT NOT NULL REFERENCES run (id),
    key          TEXT NOT NULL,
    name         TEXT NULL,
    value        TEXT NULL,
    status       TEXT NOT NULL CHECK (status IN (
                     'captured', 'absent', 'not_scalar', 'value_too_large',
                     'source_unavailable')),
    source       TEXT NOT NULL CHECK (source IN ('file', 'session')),
    source_file  TEXT NULL,
    declared     INTEGER NOT NULL CHECK (declared IN (0, 1)),
    PRIMARY KEY (run_id, key),
    CHECK ((source = 'file') = (source_file IS NOT NULL))
);

-- ---------------------------------------------------------------------------
-- Indexes -- each serves a statement in sqlite_store.py.
-- `run(project, started_at, id)`: a project's run list in its order, the
-- cursor's range, and the metadata horizon count. It leads with the
-- project because every read of the run list is within one.
-- `result(run_id)`: one run's results in insertion order, without the
-- temporary sort the `(run_id, node_id)` unique index would need.
-- `result(test_case_id)`: one test's history.
-- `test_case(project, node_id)`: the catalogue upsert's conflict target and
-- every lookup by node id, which is always within one project.
-- `run_metadata(key, value)`: filtering runs by metadata key/value pairs,
-- a full scan without it.
-- `access_token(digest)`, from its UNIQUE constraint: authenticating a
-- request.
-- `project_member`'s primary key: a project's members and one member's
-- role. `project_member(account, project)`: a user's memberships, which the
-- project list is filtered by.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_run_project_started_at
    ON run (project, started_at, id);
CREATE INDEX IF NOT EXISTS idx_result_run_id
    ON result (run_id);
CREATE INDEX IF NOT EXISTS idx_result_test_case_id
    ON result (test_case_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_test_case_project_node_id
    ON test_case (project, node_id);
CREATE INDEX IF NOT EXISTS idx_run_metadata_key_value
    ON run_metadata (key, value);
CREATE INDEX IF NOT EXISTS idx_project_member_account
    ON project_member (account, project);
