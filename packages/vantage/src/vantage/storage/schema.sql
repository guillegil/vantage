-- Vantage database schema.
--
-- All thirteen tables and fifteen indexes are applied whole, in one
-- transaction, the first time a database is opened
-- (vantage/storage/connection.py) -- including the tables and columns nothing
-- writes yet. Every statement is IF NOT EXISTS so two processes opening the
-- same fresh database race safely.
--
-- There is no migration framework. This file stamps `meta.schema_version` as
-- its last statement, and connection.py refuses any database whose stamp is
-- absent or differs from the version the build expects.
--
-- Conventions: timestamps are ISO-8601 UTC TEXT; booleans are INTEGER 0/1;
-- JSON-shaped values are TEXT written with stdlib `json`. A column whose
-- content is unbounded by nature carries a sibling `<name>_truncated`
-- INTEGER NOT NULL DEFAULT 0 flag.
--
-- Foreign keys are declared here but only enforced when a connection turns
-- on `PRAGMA foreign_keys=ON` (vantage/storage/connection.py, every
-- connection) -- SQLite ignores unenforced foreign keys by default.

-- ---------------------------------------------------------------------------
-- meta -- `schema_version` (stamped at the end of this file), plus the
-- best-effort `created_at`/`created_by` rows connection.py writes.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- run -- one row per recorded session. Only `id`, the four timestamps,
-- `exit_status`, `interrupted`, `interrupt_reason` and the `vcs_*` columns
-- are written today; the rest keep their defaults.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run (
    id                            TEXT PRIMARY KEY,
    received_at                   TEXT NOT NULL,
    last_contact_at               TEXT NULL,
    started_at                    TEXT NOT NULL,
    finished_at                   TEXT NULL,
    exit_status                   INTEGER NULL,
    interrupted                   INTEGER NOT NULL DEFAULT 0,
    interrupt_reason              TEXT NULL,
    interrupt_reason_truncated    INTEGER NOT NULL DEFAULT 0,
    hostname                      TEXT NULL,
    username                      TEXT NULL,
    python_version                TEXT NULL,
    pytest_version                TEXT NULL,
    platform                      TEXT NULL,
    command_line                  TEXT NULL,
    command_line_truncated        INTEGER NOT NULL DEFAULT 0,
    vantage_version                TEXT NULL,
    root_dir                      TEXT NULL,
    invocation_dir                TEXT NULL,
    plugins                       TEXT NULL,
    plugins_truncated             INTEGER NOT NULL DEFAULT 0,
    xdist_enabled                 INTEGER NULL,
    xdist_worker_count            INTEGER NULL,
    vcs_commit                    TEXT NULL,
    vcs_branch                    TEXT NULL,
    vcs_commit_subject            TEXT NULL,
    vcs_commit_subject_truncated  INTEGER NOT NULL DEFAULT 0,
    vcs_dirty                     INTEGER NULL,
    vcs_root                      TEXT NULL,
    collected_count                INTEGER NULL
);

-- ---------------------------------------------------------------------------
-- test_case -- the catalogue: one row per test ever seen, keyed by pytest
-- node id. `node_id`'s uniqueness comes from `idx_test_case_node_id` below,
-- the catalogue upsert's conflict target. `stable_id` currently holds the
-- same string as `node_id`; the `flake_*`, `param_signature*` and
-- `param_drift_detected_at` columns are not written yet.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_case (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    stable_id                TEXT NOT NULL UNIQUE,
    node_id                  TEXT NOT NULL,
    file_path                TEXT NOT NULL,
    class_name               TEXT NULL,
    function_name            TEXT NOT NULL,
    param_id                 TEXT NULL,
    first_seen_at            TEXT NOT NULL,
    last_seen_at             TEXT NOT NULL,
    last_seen_run_id         TEXT NULL REFERENCES run (id),
    flake_score              REAL NULL,
    flake_window             INTEGER NULL,
    flake_computed_at        TEXT NULL,
    param_signature          TEXT NULL,
    param_signature_seen_at  TEXT NULL,
    param_drift_detected_at  TEXT NULL
);

-- ---------------------------------------------------------------------------
-- result -- one row per test per run. `outcome` is the test's overall
-- outcome across setup, call and teardown, not the `call` phase alone.
-- `UNIQUE(run_id, node_id, attempt)` keeps a result delivered twice (xdist
-- reports it from both worker and controller) to one row; `attempt` is
-- always 0 today.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS result (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                   TEXT NOT NULL REFERENCES run (id),
    test_case_id             INTEGER NOT NULL REFERENCES test_case (id),
    node_id                  TEXT NOT NULL,
    attempt                  INTEGER NOT NULL DEFAULT 0,
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
    UNIQUE (run_id, node_id, attempt)
);

-- ---------------------------------------------------------------------------
-- result_marker -- markers applied to a test. Not written yet.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS result_marker (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id         INTEGER NOT NULL REFERENCES result (id),
    name              TEXT NOT NULL,
    args              TEXT NULL,
    args_truncated    INTEGER NOT NULL DEFAULT 0,
    kwargs            TEXT NULL,
    kwargs_truncated  INTEGER NOT NULL DEFAULT 0,
    origin            TEXT NOT NULL
        CHECK (origin IN ('function', 'class', 'module', 'package', 'session', 'config'))
);

-- ---------------------------------------------------------------------------
-- result_parameter -- a parametrized test's argument values. Not written yet.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS result_parameter (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id               INTEGER NOT NULL REFERENCES result (id),
    name                    TEXT NOT NULL,
    position                INTEGER NOT NULL,
    value_repr              TEXT NULL,
    value_repr_truncated    INTEGER NOT NULL DEFAULT 0,
    value_type              TEXT NULL
);

-- ---------------------------------------------------------------------------
-- result_log -- structured per-test log records. Not written yet. `level_no`
-- is numeric so `WHERE level_no >= 30` needs no application code.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS result_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id            INTEGER NOT NULL REFERENCES result (id),
    sequence             INTEGER NOT NULL,
    phase                TEXT CHECK (phase IN ('setup', 'call', 'teardown')),
    created_at           TEXT NOT NULL,
    level_no             INTEGER NOT NULL,
    level_name           TEXT NOT NULL,
    logger_name          TEXT NULL,
    message              TEXT NOT NULL,
    message_truncated    INTEGER NOT NULL DEFAULT 0,
    path                 TEXT NULL,
    lineno               INTEGER NULL
);

-- ---------------------------------------------------------------------------
-- result_fixture -- fixtures a test used. Not written yet.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS result_fixture (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id   INTEGER NOT NULL REFERENCES result (id),
    name        TEXT NOT NULL,
    scope       TEXT NULL,
    position    INTEGER NOT NULL
);

-- ---------------------------------------------------------------------------
-- artifact -- content-addressed files, so the same screenshot from two
-- hundred runs is stored once. Not written yet. The on-disk store is the
-- owner-only `artifacts/` directory connection.py creates beside the database.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artifact (
    content_hash       TEXT PRIMARY KEY,
    algorithm          TEXT NOT NULL DEFAULT 'sha256',
    size_bytes         INTEGER NOT NULL,
    media_type         TEXT NULL,
    content            BLOB NULL,
    external_path      TEXT NULL,
    first_stored_at    TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- result_artifact -- links a result to its artifacts. Not written yet.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS result_artifact (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id      INTEGER NOT NULL REFERENCES result (id),
    content_hash   TEXT NOT NULL REFERENCES artifact (content_hash),
    label          TEXT NOT NULL,
    phase          TEXT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE (result_id, content_hash, label)
);

-- ---------------------------------------------------------------------------
-- user_setting -- namespaced, server-persisted user preferences. Generic
-- storage, specific validation: `value` is JSON text this schema does not
-- describe and this adapter never parses; each namespace's shape is validated
-- by an ordinary Pydantic model in `vantage.service`.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_setting (
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
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
    content_type TEXT NOT NULL CHECK (content_type IN ('json', 'yaml', 'toml')),
    status       TEXT NOT NULL CHECK (status IN (
                     'captured', 'not_found', 'path_rejected', 'too_large',
                     'not_text', 'unreadable', 'over_budget', 'malformed')),
    PRIMARY KEY (run_id, source_file)
);

-- ---------------------------------------------------------------------------
-- run_metadata -- one row per DECLARED key. `value` is NULL whenever `status`
-- is not 'captured': a declared-but-uncaptured key is a row, never a missing
-- row. All values are TEXT, numbers included: comparison is string
-- equality, and the declaration names keys, not types.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_metadata (
    run_id       TEXT NOT NULL REFERENCES run (id),
    key          TEXT NOT NULL,
    value        TEXT NULL,
    source_file  TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN (
                     'captured', 'absent', 'not_scalar', 'value_too_large',
                     'source_unavailable')),
    PRIMARY KEY (run_id, key)
);

-- ---------------------------------------------------------------------------
-- Indexes -- fifteen in total. Some precede any query that uses them: the
-- failure index (5) is for grouping failures at one source line (`GROUP BY
-- failure_path, failure_lineno`), `run(received_at)` (2) for arrival order,
-- and `run(last_contact_at)` (14) for finding runs that have gone quiet.
-- `run_metadata(key, value)` (15) serves filtering runs by a declared
-- key/value pair, a full scan without it.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_run_started_at
    ON run (started_at);                                            -- 1
CREATE INDEX IF NOT EXISTS idx_run_received_at
    ON run (received_at);                                           -- 2
CREATE INDEX IF NOT EXISTS idx_result_run_id
    ON result (run_id);                                             -- 3
CREATE INDEX IF NOT EXISTS idx_result_test_case_id
    ON result (test_case_id);                                       -- 4
CREATE INDEX IF NOT EXISTS idx_result_failure_path_lineno
    ON result (failure_path, failure_lineno);                       -- 5
CREATE INDEX IF NOT EXISTS idx_result_outcome
    ON result (outcome);                                            -- 6
CREATE UNIQUE INDEX IF NOT EXISTS idx_test_case_node_id
    ON test_case (node_id);                                         -- 7
CREATE INDEX IF NOT EXISTS idx_test_case_last_seen_at
    ON test_case (last_seen_at);                                    -- 8
CREATE INDEX IF NOT EXISTS idx_result_log_result_id_sequence
    ON result_log (result_id, sequence);                            -- 9
CREATE INDEX IF NOT EXISTS idx_result_log_result_id_level_no
    ON result_log (result_id, level_no);                            -- 10
CREATE INDEX IF NOT EXISTS idx_result_marker_result_id_name
    ON result_marker (result_id, name);                             -- 11
CREATE INDEX IF NOT EXISTS idx_result_parameter_result_id
    ON result_parameter (result_id);                                -- 12
CREATE INDEX IF NOT EXISTS idx_result_artifact_content_hash
    ON result_artifact (content_hash);                              -- 13
CREATE INDEX IF NOT EXISTS idx_run_last_contact_at
    ON run (last_contact_at);                                       -- 14
CREATE INDEX IF NOT EXISTS idx_run_metadata_key_value
    ON run_metadata (key, value);                                   -- 15

-- ---------------------------------------------------------------------------
-- Schema version stamp -- must be the last statement in this file, and must
-- match `_SCHEMA_VERSION` in connection.py. `_apply_schema` wraps the whole
-- script in one `BEGIN IMMEDIATE` ... `COMMIT`, so the stamp commits
-- atomically with the tables it describes. `OR IGNORE` keeps a reapplication
-- against an already-stamped database a no-op.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '4');
