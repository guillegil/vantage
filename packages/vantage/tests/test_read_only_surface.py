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
stays stable across reads. The digest is taken over every table's rows,
through the store's own connection, with `count_executions()` and
`count_results()` held unchanged beside it.

`test_a_writing_endpoint_tagged_read_fails_the_harness` proves
`_run_read_only_proof` can report a mismatch, so the read-only check is not
vacuously green.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient
from vantage.service.app import create_app
from vantage.storage.sqlite_store import SqliteExecutionStore
from vantage_port_contract import _execution, _result, _vcs

_DOCUMENT_BYTES = (
    importlib.resources.files("vantage.service.openapi").joinpath("v1.yaml").read_bytes()
)
_Call = Callable[[], object]

_RUN_ID = "7" * 32
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
    """The strong half of the digest pair (module docstring)."""
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


def _seed_database(db_path: Path) -> None:
    """One run, one result, via `record_session`, from a writer closed
    before the store under test opens."""
    writer = SqliteExecutionStore(db_path)
    writer.record_session(
        _execution(_RUN_ID, started=_SEEDED_AT, vcs=_vcs()),
        results=(_result(_NODE_ID),),
        received_at=_SEEDED_AT,
    )
    writer.close()


_UNKNOWN_RUN_ID = "0" * 32


def _read_bindings(client: TestClient) -> dict[tuple[str, str], tuple[_Call, ...]]:
    """One or more callables per `read`-tagged path.

    Every route with an error branch gets one call per branch (happy path,
    plus its `404` and/or `422` variants), so a read path that wrote only on
    a miss -- an audit log on `404`, say -- is caught too. A route with no
    such branch keeps its single happy-path call."""
    run = f"/api/v1/runs/{_RUN_ID}"
    unknown_run = f"/api/v1/runs/{_UNKNOWN_RUN_ID}"
    return {
        ("GET", "/runs"): (
            lambda: client.get("/api/v1/runs"),
            lambda: client.get("/api/v1/runs", params={"limit": 0}),  # 422
            # The metadata filter, and its both-or-neither rejection branch.
            lambda: client.get(
                "/api/v1/runs", params={"metadata_key": "firmware_version", "metadata_value": "2.1"}
            ),
            lambda: client.get(
                "/api/v1/runs", params={"metadata_key": "firmware_version"}
            ),  # 422 (InvalidMetadataFilterError)
        ),
        ("GET", "/runs/{run_id}"): (
            lambda: client.get(run),
            lambda: client.get(unknown_run),  # 404 (UnknownRunError)
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
        ("GET", "/tests/history"): (
            lambda: client.get("/api/v1/tests/history", params={"node_id": _NODE_ID}),
            lambda: client.get("/api/v1/tests/history"),  # 422 (InvalidIdentityError)
        ),
        ("GET", "/capabilities"): (lambda: client.get("/api/v1/capabilities"),),
        ("GET", "/openapi.yaml"): (lambda: client.get("/api/v1/openapi.yaml"),),
        ("GET", "/config/sections"): (lambda: client.get("/api/v1/config/sections"),),
        ("GET", "/runs/{run_id}/sections"): (
            lambda: client.get(f"{run}/sections"),
            lambda: client.get(f"{unknown_run}/sections"),  # 404 (UnknownRunError)
        ),
    }


def test_a_writing_endpoint_tagged_read_fails_the_harness(tmp_path: Path) -> None:
    """**The falsifier.** A test-local copy of the binding table with
    `POST /api/v1/runs` temporarily registered as if it were `read`; the
    digest-pair harness must report a mismatch -- proving the check is not
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


def test_logical_content_digest_unchanged_after_every_read_path(tmp_path: Path) -> None:
    """Calling every read path leaves the stored data unchanged."""
    db_path = tmp_path / "store" / "vantage.db"
    _seed_database(db_path)

    store = SqliteExecutionStore(db_path)
    try:
        client = TestClient(create_app(store))
        read_ops = _read_operations(_document())
        bindings = _read_bindings(client)
        assert set(bindings) == read_ops, "binding table incomplete for this run"

        proof = _run_read_only_proof(store=store, ops=read_ops, bindings=bindings)

        assert proof.before[_LOGICAL] == proof.after[_LOGICAL]
        assert proof.before[_EXECUTIONS] == proof.after[_EXECUTIONS] == 1
        assert proof.before[_RESULTS] == proof.after[_RESULTS] == 1
    finally:
        store.close()
