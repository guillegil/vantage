"""Every `read` path leaves stored data unchanged: a content digest over
every table, before and after calling every path the interface document tags
`read`, plus a falsifier.

**The read surface is whatever `openapi/v1.yaml` tags `read`** --
`_read_operations` derives the call set from the document itself, so a path
added with the tag but no binding here fails the binding-completeness
assertion rather than being silently skipped. A read route the document does
not tag `read` is not checked at all.

**Why rows, not file bytes.** The store opens WAL: a write lands in `-wal`
and leaves the main file's bytes as they were, while a checkpoint rewrites
them with no row changing. A file hash therefore neither catches a write nor
is reliably stable across reads. The digest is taken over every table's rows,
through the store's own connection, with `count_executions()` and
`count_results()` held unchanged beside it.

`test_a_writing_endpoint_tagged_read_fails_the_harness` proves
`_run_read_only_proof` can report a mismatch, so the read-only check is not
vacuously green.

**Two projects, each with a run, a result and a section.** The per-project
reads are asked of both, and of a project that does not exist and one no
project can be named, so a lookup that made the project it missed, or read
across projects and wrote as it went, is caught. On the closed database the
other project also has a member, so reading its members reads a row.

**On an open database and on a closed one.** Once a user exists every read
authenticates first, so the closed run proves that authenticating writes
nothing either, and it is the only way the users and tokens reads get past
their refusal to an anonymous caller.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from vantage.core.domain.access import RECORD_SCOPE, SCOPES, new_token, token_digest
from vantage.core.domain.projects import DEFAULT_PROJECT, EDITOR_ROLE
from vantage.core.ports.storage import MetadataEntry, MetadataFile, RunMetadata
from vantage.service.app import create_app
from vantage.service.routes.sections import TEST_SECTIONS_NAMESPACE
from vantage.service.schemas import SectionValue
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import _execution, _result, _vcs

_DOCUMENT_BYTES = (
    importlib.resources.files("vantage.service.openapi").joinpath("v1.yaml").read_bytes()
)
_Call = Callable[[], object]

_RUN_ID = "7" * 32
_OTHER_RUN_ID = "9" * 32
_OTHER_PROJECT = "firmware"
_NODE_ID = "tests/test_read_only_probe.py::test_x"
_SEEDED_AT = datetime(2026, 8, 15, 9, 0, 0, tzinfo=timezone.utc)


def _document() -> dict[str, Any]:
    return dict(yaml.safe_load(_DOCUMENT_BYTES))


def _read_operations(document: Mapping[str, Any]) -> set[tuple[str, str]]:
    """`(METHOD, path)` pairs the document tags `read`."""
    return {
        (method.upper(), path)
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
        if "read" in operation.get("tags", [])
    }


def _table_names(conn: sqlite3.Connection) -> list[str]:
    """Enumerated, never hard-coded -- a table added later is covered."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master"
        " WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _logical_content_digest(conn: sqlite3.Connection) -> bytes:
    """Every table's rows, in rowid order (module docstring)."""
    hasher = hashlib.sha256()
    for table in _table_names(conn):
        hasher.update(table.encode("utf-8"))
        # `table` comes only from `sqlite_master`, never client input -- not
        # the interpolation hazard S608 flags.
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()  # noqa: S608
        for row in rows:
            hasher.update(repr(row).encode("utf-8"))
    return hasher.digest()


# A snapshot's three positions -- named once, indexed everywhere else, so the
# pairing (before, after) does not have to be spelled out six times.
_LOGICAL, _EXECUTIONS, _RESULTS = range(3)
_Snapshot = tuple[bytes, int, int]


def _snapshot(conn: sqlite3.Connection, store: SqliteExecutionStore) -> _Snapshot:
    return (_logical_content_digest(conn), store.count_executions(), store.count_results())


@dataclass(frozen=True)
class _ReadOnlyProof:
    """`(logical, executions, results)`, before and after every op in `ops`
    ran. Asserts nothing itself -- the caller decides."""

    before: _Snapshot
    after: _Snapshot


def _run_read_only_proof(
    *,
    store: SqliteExecutionStore,
    ops: set[tuple[str, str]],
    bindings: Mapping[tuple[str, str], tuple[_Call, ...]],
) -> _ReadOnlyProof:
    conn = store._conn  # noqa: SLF001 -- reads what the routes' own connection sees
    before = _snapshot(conn, store)
    for op in sorted(ops):
        for call in bindings[op]:
            call()
    return _ReadOnlyProof(before=before, after=_snapshot(conn, store))


def _seed_database(db_path: Path, *, closed: bool = False) -> dict[str, str]:
    """Two projects, `default` and `_OTHER_PROJECT`, each with one run, one
    result, the run's metadata and one section, from a writer closed before
    the store under test opens. With `closed`, also an admin `alice` with a
    token holding every scope, and a user `bob`, an editor of
    `_OTHER_PROJECT`, with a record-only one; the headers that send alice's
    token are returned, and bob's are kept for `_read_bindings`."""
    writer = SqliteExecutionStore(db_path)
    headers: dict[str, str] = {}
    if closed:
        for name, admin, scopes in (("alice", True, SCOPES), ("bob", False, {RECORD_SCOPE})):
            writer.create_user(name, admin=admin, created_at=_SEEDED_AT)
            token = new_token()
            writer.create_token(
                name,
                digest=token_digest(token),
                label="",
                scopes=frozenset(scopes),
                created_at=_SEEDED_AT,
            )
            headers[name] = f"Bearer {token}"
    writer.create_project(_OTHER_PROJECT, created_at=_SEEDED_AT)
    if closed:
        writer.set_member("bob", project=_OTHER_PROJECT, role=EDITOR_ROLE)
    for run_id, project in ((_RUN_ID, DEFAULT_PROJECT), (_OTHER_RUN_ID, _OTHER_PROJECT)):
        writer.record_session(
            _execution(run_id, started=_SEEDED_AT, vcs=_vcs()),
            results=(_result(_NODE_ID),),
            received_at=_SEEDED_AT,
            metadata=RunMetadata(
                files=(
                    MetadataFile(source_file="fw.json", content_type="json", status="captured"),
                ),
                entries=(
                    MetadataEntry(
                        key="firmware_version",
                        value="2.1",
                        source_file="fw.json",
                        status="captured",
                    ),
                    MetadataEntry(
                        key="bench",
                        value="lab-3",
                        source_file=None,
                        status="captured",
                        source="session",
                        declared=False,
                    ),
                ),
            ),
            project=project,
        )
        writer.upsert_setting(
            TEST_SECTIONS_NAMESPACE,
            "Probe",
            project=project,
            value=SectionValue(prefix="tests/").model_dump_json(),
            updated_at=_SEEDED_AT,
        )
    writer.close()
    return headers


_UNKNOWN_RUN_ID = "0" * 32


def _read_bindings(
    client: TestClient, bob: str | None = None
) -> dict[tuple[str, str], tuple[_Call, ...]]:
    """One or more callables per `read`-tagged path.

    Every route with an error branch gets one call per branch (happy path,
    plus its `404` and/or `422` variants), so a read path that wrote only on
    a miss -- an audit log on `404`, say -- is caught too. A route with no
    such branch keeps its single happy-path call. A per-project read is
    asked of both seeded projects, and answers `404` for the missing ones.
    With `bob`, the
    authorization header of a user who is no admin, the users, tokens and
    members reads are also asked with it, to be refused."""
    run = f"/api/v1/runs/{_RUN_ID}"
    refused = {} if bob is None else {"Authorization": bob}
    unknown_run = f"/api/v1/runs/{_UNKNOWN_RUN_ID}"
    other_run = f"/api/v1/runs/{_OTHER_RUN_ID}"
    projects = [f"/api/v1/projects/{name}" for name in (DEFAULT_PROJECT, _OTHER_PROJECT)]
    # A name no project has, and one no project can have.
    missing = [f"/api/v1/projects/{name}" for name in ("ghost", "NOT-A-NAME")]
    node = {"node_id": _NODE_ID}
    return {
        ("GET", "/projects"): (lambda: client.get("/api/v1/projects"),),
        ("GET", "/projects/{project}/runs"): (
            *(partial(client.get, f"{project}/runs") for project in projects),
            *(partial(client.get, f"{project}/runs") for project in missing),  # 404
            partial(client.get, f"{projects[0]}/runs", params={"limit": 0}),  # 422
            # The metadata filter, and its unpaired-parameter rejection branch.
            partial(
                client.get,
                f"{projects[0]}/runs",
                params={"metadata_key": "firmware_version", "metadata_value": "2.1"},
            ),
            # 422 (InvalidMetadataFilterError)
            partial(client.get, f"{projects[0]}/runs", params={"metadata_key": "firmware_version"}),
        ),
        ("GET", "/runs/{run_id}"): (
            lambda: client.get(run),
            lambda: client.get(other_run),
            lambda: client.get(unknown_run),  # 404 (UnknownRunError)
        ),
        ("GET", "/runs/{run_id}/metadata"): (
            lambda: client.get(f"{run}/metadata"),
            lambda: client.get(f"{unknown_run}/metadata"),  # 404 (UnknownRunError)
        ),
        ("GET", "/runs/{run_id}/results"): (
            lambda: client.get(f"{run}/results"),
            lambda: client.get(f"{unknown_run}/results"),  # 404 (UnknownRunError)
            lambda: client.get(f"{run}/results", params={"limit": 0}),  # 422
        ),
        ("GET", "/runs/{run_id}/result"): (
            lambda: client.get(f"{run}/result", params={"node_id": _NODE_ID}),
            # 404 (UnknownRunError)
            lambda: client.get(f"{unknown_run}/result", params={"node_id": _NODE_ID}),
            # 404 (UnknownResultError)
            lambda: client.get(f"{run}/result", params={"node_id": "no-such-node"}),
            lambda: client.get(f"{run}/result"),  # 422 (InvalidIdentityError)
        ),
        ("GET", "/projects/{project}/tests/history"): (
            *(partial(client.get, f"{project}/tests/history", params=node) for project in projects),
            # 404 (NoSuchProjectError)
            *(partial(client.get, f"{project}/tests/history", params=node) for project in missing),
            partial(client.get, f"{projects[0]}/tests/history"),  # 422 (InvalidIdentityError)
        ),
        ("GET", "/capabilities"): (lambda: client.get("/api/v1/capabilities"),),
        ("GET", "/openapi.yaml"): (lambda: client.get("/api/v1/openapi.yaml"),),
        ("GET", "/projects/{project}/config/sections"): (
            *(partial(client.get, f"{project}/config/sections") for project in projects),
            # 404 (NoSuchProjectError)
            *(partial(client.get, f"{project}/config/sections") for project in missing),
        ),
        ("GET", "/runs/{run_id}/sections"): (
            lambda: client.get(f"{run}/sections"),
            lambda: client.get(f"{unknown_run}/sections"),  # 404 (UnknownRunError)
            lambda: client.get(f"{other_run}/sections"),  # the other project's sections
        ),
        ("GET", "/projects/{project}/members"): (
            *(partial(client.get, f"{project}/members") for project in projects),
            # 404 (NoSuchProjectError), or 409 when open
            *(partial(client.get, f"{project}/members") for project in missing),
            # 403, or 409 when open
            partial(client.get, f"{projects[1]}/members", headers=refused),
        ),
        ("GET", "/users"): (
            lambda: client.get("/api/v1/users"),
            lambda: client.get("/api/v1/users", headers=refused),  # 403, or 409 when open
        ),
        ("GET", "/session"): (
            lambda: client.get("/api/v1/session"),
            lambda: client.get("/api/v1/session", headers=refused),
        ),
        ("GET", "/tokens"): (
            lambda: client.get("/api/v1/tokens"),
            lambda: client.get("/api/v1/tokens", params={"user": "alice"}),
            lambda: client.get("/api/v1/tokens", params={"user": "ghost"}),
            lambda: client.get("/api/v1/tokens", params={"user": "NOT-A-NAME"}),
            lambda: client.get("/api/v1/tokens", headers=refused),  # 403, or 409 when open
        ),
    }


def test_a_writing_endpoint_tagged_read_fails_the_harness(tmp_path: Path) -> None:
    """**The falsifier.** A test-local copy of the binding table with
    `POST /api/v1/runs` temporarily registered as if it were `read`; the
    harness must report a mismatch -- proving the check is not
    vacuously green before it is trusted with the real document."""
    db_path = tmp_path / "store" / "vantage.db"
    _seed_database(db_path)

    store = SqliteExecutionStore(db_path)
    try:
        client = TestClient(create_app(store))
        tampered_ops = _read_operations(_document()) | {("POST", "/runs")}
        tampered_bindings: dict[tuple[str, str], tuple[_Call, ...]] = {
            **_read_bindings(client),
            ("POST", "/runs"): (
                lambda: client.post(
                    "/api/v1/runs",
                    json={
                        "run": {
                            "id": "8" * 32,
                            "started_at": "2026-08-15T09:14:02+00:00",
                            "finished_at": "2026-08-15T09:14:47+00:00",
                            "exit_status": 0,
                            "interrupted": False,
                            "interrupt_reason": None,
                        }
                    },
                ),
            ),
        }

        proof = _run_read_only_proof(store=store, ops=tampered_ops, bindings=tampered_bindings)

        assert proof.before[_LOGICAL] != proof.after[_LOGICAL]
        assert proof.before[_EXECUTIONS] != proof.after[_EXECUTIONS]
    finally:
        store.close()


@pytest.mark.parametrize("closed", [False, True], ids=["open", "closed"])
def test_logical_content_digest_unchanged_after_every_read_path(
    tmp_path: Path, closed: bool
) -> None:
    """Calling every read path leaves the stored data unchanged, on a
    database with no user and on one whose reads each authenticate."""
    db_path = tmp_path / "store" / "vantage.db"
    tokens = _seed_database(db_path, closed=closed)

    store = SqliteExecutionStore(db_path)
    try:
        headers = {"Authorization": tokens["alice"]} if closed else {}
        client = TestClient(create_app(store), headers=headers)
        read_ops = _read_operations(_document())
        bindings = _read_bindings(client, tokens.get("bob"))
        assert set(bindings) == read_ops, "binding table incomplete for this run"

        proof = _run_read_only_proof(store=store, ops=read_ops, bindings=bindings)

        # The users, tokens and members reads got past their refusal to an
        # anonymous caller, so their read of the store is proven too, not
        # only that.
        answered = [
            client.get(path).status_code
            for path in (
                "/api/v1/users",
                "/api/v1/tokens",
                f"/api/v1/projects/{_OTHER_PROJECT}/members",
            )
        ]
        assert answered == ([200, 200, 200] if closed else [409, 409, 409])
        # Both projects' rows are in the digest, and nothing asked made one.
        tables = set(_table_names(store._conn))  # noqa: SLF001
        assert {"project", "project_setting"} <= tables
        assert [project.name for project in store.list_projects()] == [
            DEFAULT_PROJECT,
            _OTHER_PROJECT,
        ]
        assert proof.before[_LOGICAL] == proof.after[_LOGICAL]
        assert proof.before[_EXECUTIONS] == proof.after[_EXECUTIONS] == 2
        assert proof.before[_RESULTS] == proof.after[_RESULTS] == 2
    finally:
        store.close()
