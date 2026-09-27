-- Vantage database schema for PostgreSQL.
--
-- The same tables, columns, constraints and meaning as the SQLite schema
-- (vantage/storage/schema.sql, which documents each table), in native
-- types: `timestamptz` for every timestamp, `boolean` for every flag,
-- `bigint` for integers and identity keys, `double precision` for
-- durations, `text` for text. Everything lives in the `vantage` schema, so
-- no table collides with anything else in the database.
--
-- Applied whole, in one transaction, by vantage/storage/postgres/connection.py
-- when the `vantage` schema holds no `meta` table, in the same transaction
-- that stamps `meta.schema_version`. That transaction holds an advisory
-- lock, so no two servers ever run this at once, and no statement needs
-- IF NOT EXISTS: running it twice is a bug to fail on, not a race to
-- tolerate. The schema itself may exist already, empty, made by whoever
-- granted the rights to use it.
--
-- Every text key -- run ids, user names and the columns referring to them,
-- setting names, metadata keys -- is `COLLATE "C"`: byte order, which for UTF-8 is
-- code point order, as SQLite and the in-memory store order text. A
-- database's default collation is usually a linguistic one.
--
-- A btree entry must fit in a third of a page, about 2.7 kB, and node ids,
-- metadata keys and values and declared file paths have no such bound: a
-- pytest parametrize id is never shortened. Uniqueness and lookups on them
-- therefore go through `text_key`, a SHA-256 of the text, never the text
-- itself; every lookup compares the text as well.

-- The key an unbounded text value is indexed by. `convert_to` is only
-- STABLE because a conversion can be redefined; from a UTF-8 database to
-- UTF-8 it converts nothing, and the adapter opens nothing else, so the
-- function is IMMUTABLE as an index expression requires.
CREATE FUNCTION vantage.text_key(value text) RETURNS bytea
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$ SELECT pg_catalog.sha256(pg_catalog.convert_to(value, 'UTF8')) $$;

CREATE TABLE vantage.meta (
    key   text COLLATE "C" PRIMARY KEY,
    value text NOT NULL
);

-- A user name is at most 64 characters, so it is indexed as it is.
CREATE TABLE vantage.account (
    name        text COLLATE "C" PRIMARY KEY,
    admin       boolean NOT NULL,
    disabled    boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL
);

CREATE TABLE vantage.access_token (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account     text COLLATE "C" NOT NULL REFERENCES vantage.account (name),
    digest      text COLLATE "C" NOT NULL UNIQUE,
    label       text NOT NULL,
    can_read    boolean NOT NULL,
    can_record  boolean NOT NULL,
    can_admin   boolean NOT NULL,
    created_at  timestamptz NOT NULL,
    revoked_at  timestamptz NULL,
    CHECK (can_read OR can_record OR can_admin)
);

-- A project name is at most 64 characters, so it is indexed as it is.
-- `default` is written in the transaction that creates the schema.
CREATE TABLE vantage.project (
    name        text COLLATE "C" PRIMARY KEY,
    created_at  timestamptz NOT NULL
);

CREATE TABLE vantage.run (
    id                            text COLLATE "C" PRIMARY KEY,
    project                       text COLLATE "C" NOT NULL REFERENCES vantage.project (name),
    received_at                   timestamptz NOT NULL,
    last_contact_at               timestamptz NULL,
    started_at                    timestamptz NOT NULL,
    finished_at                   timestamptz NULL,
    exit_status                   bigint NULL,
    interrupted                   boolean NOT NULL DEFAULT false,
    interrupt_reason              text NULL,
    vcs_commit                    text NULL,
    vcs_branch                    text NULL,
    vcs_commit_subject            text NULL,
    vcs_commit_subject_truncated  boolean NOT NULL DEFAULT false,
    vcs_dirty                     boolean NULL,
    vcs_root                      text NULL,
    recorded_by                   text COLLATE "C" NULL REFERENCES vantage.account (name)
);

-- The catalogue. `(project, node_id)` is unique through
-- `test_case_node_key` below.
CREATE TABLE vantage.test_case (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project           text COLLATE "C" NOT NULL REFERENCES vantage.project (name),
    node_id           text NOT NULL,
    file_path         text NOT NULL,
    class_name        text NULL,
    function_name     text NOT NULL,
    param_id          text NULL,
    first_seen_at     timestamptz NOT NULL,
    last_seen_at      timestamptz NOT NULL,
    last_seen_run_id  text COLLATE "C" NULL REFERENCES vantage.run (id)
);

-- `(run_id, node_id)` is unique through `result_run_node_key` below.
CREATE TABLE vantage.result (
    id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                      text COLLATE "C" NOT NULL REFERENCES vantage.run (id),
    test_case_id                bigint NOT NULL REFERENCES vantage.test_case (id),
    node_id                     text NOT NULL,
    outcome                     text NOT NULL
        CHECK (outcome IN ('passed', 'failed', 'error', 'skipped', 'xfailed', 'xpassed')),
    duration                    double precision NULL,
    started_at                  timestamptz NULL,
    finished_at                 timestamptz NULL,
    setup_outcome               text NULL,
    call_outcome                text NULL,
    teardown_outcome            text NULL,
    setup_duration              double precision NULL,
    call_duration               double precision NULL,
    teardown_duration           double precision NULL,
    failure_type                text NULL,
    failure_message             text NULL,
    failure_message_truncated   boolean NOT NULL DEFAULT false,
    failure_path                text NULL,
    failure_lineno              bigint NULL,
    failure_repr                text NULL,
    failure_repr_truncated      boolean NOT NULL DEFAULT false,
    traceback                   text NULL,
    traceback_truncated         boolean NOT NULL DEFAULT false,
    skip_reason                 text NULL,
    skip_reason_truncated       boolean NOT NULL DEFAULT false,
    xfail_reason                text NULL,
    xfail_reason_truncated      boolean NOT NULL DEFAULT false,
    worker_id                   text NULL,
    captured_stdout             text NULL,
    captured_stdout_truncated   boolean NOT NULL DEFAULT false,
    captured_stderr             text NULL,
    captured_stderr_truncated   boolean NOT NULL DEFAULT false
);

-- `value` is text, never `jsonb`, so it reads back byte for byte as
-- written. A section name is at most 120 characters, so the key is
-- indexed as it is, which keeps it ordered.
CREATE TABLE vantage.project_setting (
    project     text COLLATE "C" NOT NULL REFERENCES vantage.project (name),
    namespace   text COLLATE "C" NOT NULL,
    key         text COLLATE "C" NOT NULL,
    value       text NOT NULL,
    updated_at  timestamptz NOT NULL,
    PRIMARY KEY (project, namespace, key)
);

-- `(run_id, source_file)` is unique through `run_metadata_file_key` below.
CREATE TABLE vantage.run_metadata_file (
    run_id       text COLLATE "C" NOT NULL REFERENCES vantage.run (id),
    source_file  text NOT NULL,
    content_type text NOT NULL CHECK (content_type IN ('json', 'yaml')),
    status       text NOT NULL CHECK (status IN (
                     'captured', 'not_found', 'path_rejected', 'too_large',
                     'not_text', 'unreadable', 'over_budget', 'malformed'))
);

-- `(run_id, key)` is unique through `run_metadata_key` below.
CREATE TABLE vantage.run_metadata (
    run_id       text COLLATE "C" NOT NULL REFERENCES vantage.run (id),
    key          text COLLATE "C" NOT NULL,
    name         text NULL,
    value        text NULL,
    status       text NOT NULL CHECK (status IN (
                     'captured', 'absent', 'not_scalar', 'value_too_large',
                     'source_unavailable')),
    source       text NOT NULL CHECK (source IN ('file', 'session')),
    source_file  text NULL,
    declared     boolean NOT NULL,
    CHECK ((source = 'file') = (source_file IS NOT NULL))
);

-- Indexes -- each serves a statement in store.py.
-- `run(project, started_at, id)`: a project's run list and history order,
-- read backwards, and the metadata horizon count; every read of the run
-- list is within one project.
-- `result(run_id, id)`: one run's results in insertion order, and every
-- other per-run read.
-- `result(test_case_id)`: one test's history.
-- The `text_key` indexes: the uniqueness the SQLite schema declares on node
-- ids within a project, metadata keys and declared files, each an upsert's
-- conflict target, and every lookup by node id or by metadata key and value.
-- `access_token(digest)`, from its UNIQUE constraint: authenticating a
-- request.
CREATE INDEX run_project_started_at ON vantage.run (project, started_at, id);
CREATE INDEX result_run_id ON vantage.result (run_id, id);
CREATE INDEX result_test_case_id ON vantage.result (test_case_id);
CREATE UNIQUE INDEX test_case_node_key
    ON vantage.test_case (project, vantage.text_key(node_id));
CREATE UNIQUE INDEX result_run_node_key ON vantage.result (run_id, vantage.text_key(node_id));
CREATE UNIQUE INDEX run_metadata_file_key
    ON vantage.run_metadata_file (run_id, vantage.text_key(source_file));
CREATE UNIQUE INDEX run_metadata_key ON vantage.run_metadata (run_id, vantage.text_key(key));
CREATE INDEX run_metadata_key_value
    ON vantage.run_metadata (vantage.text_key(key), vantage.text_key(value));
