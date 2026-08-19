-- Vantage database schema (RQ-29: complete schema from first use, ADR-5).
--
-- All ten tables and their fourteen indexes are declared here, whole, and
-- applied the first time a database is opened (vantage/storage/connection.py,
-- PR4). Every statement is IF NOT EXISTS so a second process opening the same
-- fresh database races safely (design.md D8) and reopening an existing
-- database issues no schema-altering statement (RQ-29.2).
--
-- Milestone 1 populates only the marked `run` columns; the other nine tables
-- exist empty until the milestone that writes them. No migration framework
-- ships in Phase 1 (ADR-5) -- `meta.schema_version` is the seam, not a
-- substitute for one. This file stamps that seam itself, as its own last
-- statement (ADR-0013, design.md D28): a database created without ever
-- passing through this file has no `schema_version` row at all, which
-- `vantage/storage/connection.py`'s open-time refusal treats as "absent",
-- refused the same as an explicitly older version.
--
-- Column-by-column traceability to requirements lives in
-- docs/schema-manifest.md, not in this file's comments -- this file is the
-- implementation the manifest is inspected against.
--
-- Conventions: timestamps are ISO-8601 UTC TEXT; booleans are INTEGER 0/1;
-- JSON-shaped values are TEXT written with stdlib `json`. A column whose
-- content is unbounded by nature carries a sibling `<name>_truncated`
-- INTEGER NOT NULL DEFAULT 0 (Milestone 2 populates the flag; the schema
-- makes it expressible now).
--
-- Foreign keys are declared here but only enforced when a connection turns
-- on `PRAGMA foreign_keys=ON` (vantage/storage/connection.py, every
-- connection) -- SQLite ignores unenforced foreign keys by default.

-- ---------------------------------------------------------------------------
-- meta -- schema_version stamp (ADR-5): a seam for a future migration, not a
-- migration framework itself. Populated at creation, not by this file.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- run -- the only table Milestone 1 populates. See docs/schema-manifest.md
-- for which columns are driven by which requirement and populated by which
-- milestone.
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
-- test_case -- the RQ-13 catalogue. `node_id` is the Phase 1 identity key
-- (UNIQUE, enforced by an explicit index below, not an inline constraint, so
-- it is one of the thirteen indexes the manifest counts). `stable_id`
-- supersedes it in Phase 2 and carries its own inline UNIQUE constraint
-- until then.
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
-- result -- one row per test phase resolution (RQ-4: `outcome` is composite,
-- resolved at teardown, not streamed from `call`). `UNIQUE(run_id, node_id,
-- attempt)` is the schema-level backstop for RQ-12's double delivery under
-- xdist.
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
-- result_marker (RQ-7)
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
-- result_parameter (RQ-6)
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
-- result_log (Phase 2 -- structured per-test logs, filterable by severity;
-- `level_no` is numeric so `WHERE level_no >= 30` needs no application code).
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
-- result_fixture (Phase 2)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS result_fixture (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id   INTEGER NOT NULL REFERENCES result (id),
    name        TEXT NOT NULL,
    scope       TEXT NULL,
    position    INTEGER NOT NULL
);

-- ---------------------------------------------------------------------------
-- artifact (Phase 2 -- content-addressed; the same screenshot from two
-- hundred runs is stored once. Its on-disk store is the `artifacts/`
-- directory created at 0700, design.md D9, RQ-40.2).
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
-- result_artifact (Phase 2)
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
-- Indexes -- fourteen in total (docs/schema-manifest.md enumerates the same
-- list). The failure index (5) is RQ-8's criterion that twenty tests failing
-- at one source line come back as one `GROUP BY failure_path, failure_lineno`
-- group. `run(received_at)` (2) is the arrival-order index the Milestone 4
-- read API needs, created now per ADR-5. `run(last_contact_at)` (14) is the
-- liveness-derivation index this change adds (RQ-44).
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

-- ---------------------------------------------------------------------------
-- Schema version stamp (ADR-0013, design.md D28) -- must be the last
-- statement in this file. `_apply_schema` in connection.py wraps the whole
-- script in one `BEGIN IMMEDIATE` ... `COMMIT`, so this stamp commits
-- atomically with the schema it describes: no crash between "tables exist"
-- and "version recorded" can leave one without the other. `OR IGNORE` is the
-- DML register of the same `IF NOT EXISTS` idempotence the rest of this file
-- relies on, so reapplying this script against an already-stamped database
-- changes nothing.
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '2');
