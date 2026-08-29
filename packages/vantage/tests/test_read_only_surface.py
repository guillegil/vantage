"""The read-only proof: a digest pair over every document-declared `read`
path, plus its own falsifier (design.md D53, D65; Phase 7).

**The read surface is whatever `openapi/v1.yaml` tags `read` (D53)** --
`_read_operations` derives the call set from the document itself, never from
a list this module maintains independently, so a read path added without a
document tag is invisible here and a path added *with* the tag but no
binding fails `test_every_read_path_has_a_binding` rather than being
silently skipped.

**Why a naive before/after file hash flakes, and what removes each cause
(design.md D65).** The store opens WAL; a read connection can checkpoint the
main file on close, and `-wal`/`-shm` change for reasons unrelated to any
row changing. The proof is a pair instead: a logical content digest over
every table (the strong half), a main-file digest with the read store's one
connection pinned open across both digests (never `-wal`/`-shm`), plus
`count_executions()`/`count_results()` held unchanged. The fixture writer's
store is closed *before* the read store opens, so WAL is already
checkpointed and removed by the time the digest pair is first taken.

**The falsifier (task 7.1) comes first in this file's history, not by
accident.** `test_a_writing_endpoint_tagged_read_fails_the_harness` proves
`_run_read_only_proof` can report a mismatch before any test here is trusted
to prove the opposite -- an unfailable read-only check is exactly the
vacuous-check failure mode `api-interface-document`'s drift test already
rejected once (Phase 6), and this proof does not repeat it.
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
from vantage.storage.memory import InMemoryExecutionStore
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
    """`(METHOD, path)` pairs the document tags `read` (design.md D53)."""
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


def _main_file_digest(db_path: Path) -> bytes:
    """The weak half -- `.db` bytes only, never `-wal`/`-shm`."""
    return hashlib.sha256(db_path.read_bytes()).digest()


# A snapshot's four positions -- named once, indexed everywhere else, so the
# pairing (before, after) does not have to be spelled out eight times.
_LOGICAL, _MAIN_FILE, _EXECUTIONS, _RESULTS = range(4)
_Snapshot = tuple[bytes, bytes, int, int]


def _snapshot(conn: sqlite3.Connection, db_path: Path, store: SqliteExecutionStore) -> _Snapshot:
    return (
        _logical_content_digest(conn),
        _main_file_digest(db_path),
        store.count_executions(),
        store.count_results(),
    )


@dataclass(frozen=True)
class _ReadOnlyProof:
    """`(logical, main_file, executions, results)`, before and after every op
    in `ops` ran. Asserts nothing itself -- the caller decides."""

    before: _Snapshot
    after: _Snapshot


def _run_read_only_proof(
    *,
    store: SqliteExecutionStore,
    db_path: Path,
    ops: set[tuple[str, str]],
    bindings: Mapping[tuple[str, str], tuple[_Call, ...]],
) -> _ReadOnlyProof:
    conn = store._conn  # noqa: SLF001 -- the one pinned connection D65 requires
    before = _snapshot(conn, db_path, store)
    for op in sorted(ops):
        for call in bindings[op]:
            call()
    return _ReadOnlyProof(before=before, after=_snapshot(conn, db_path, store))


def _seed_database(db_path: Path) -> None:
    """One run, one result, via `record_session` -- writer closed before any
    read store opens (module docstring)."""
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

    **Verify round 1, SUGGESTION-4**: the proof used to call each read path
    exactly once, with valid input -- a read path that wrote only on its
    `404` or `422` branch (an audit-log-on-miss, say) would have sailed
    through undetected. Every route with an error branch now gets one call
    per branch (happy path, plus its `404` and/or `422` variants); a route
    with no such branch (`/capabilities`, `/openapi.yaml`) keeps its single
    happy-path call. `_run_read_only_proof` runs every call in the tuple."""
    run = f"/api/v1/runs/{_RUN_ID}"
    unknown_run = f"/api/v1/runs/{_UNKNOWN_RUN_ID}"
    return {
        ("GET", "/runs"): (
            lambda: client.get("/api/v1/runs"),
            lambda: client.get("/api/v1/runs", params={"limit": 0}),  # 422 (D61)
        ),
        ("GET", "/runs/{run_id}"): (
            lambda: client.get(run),
            lambda: client.get(unknown_run),  # 404 (UnknownRunError)
        ),
        ("GET", "/runs/{run_id}/results"): (
            lambda: client.get(f"{run}/results"),
            lambda: client.get(f"{unknown_run}/results"),  # 404 (UnknownRunError)
            lambda: client.get(f"{run}/results", params={"limit": 0}),  # 422 (D61)
        ),
        ("GET", "/runs/{run_id}/result"): (
            lambda: client.get(f"{run}/result", params={"node_id": _NODE_ID}),
            lambda: client.get(f"{unknown_run}/result", params={"node_id": _NODE_ID}),
            # 404 (UnknownRunError)
            lambda: client.get(f"{run}/result", params={"node_id": "no-such-node"}),
            # 404 (UnknownResultError)
            lambda: client.get(f"{run}/result"),  # 422 (InvalidIdentityError, D54)
        ),
        ("GET", "/tests/history"): (
            lambda: client.get("/api/v1/tests/history", params={"node_id": _NODE_ID}),
            lambda: client.get("/api/v1/tests/history"),  # 422 (InvalidIdentityError, D54)
        ),
        ("GET", "/capabilities"): (lambda: client.get("/api/v1/capabilities"),),
        ("GET", "/openapi.yaml"): (lambda: client.get("/api/v1/openapi.yaml"),),
        ("GET", "/config/sections"): (lambda: client.get("/api/v1/config/sections"),),
        ("GET", "/runs/{run_id}/sections"): (
            lambda: client.get(f"{run}/sections"),
            lambda: client.get(f"{unknown_run}/sections"),  # 404 (UnknownRunError)
        ),
    }


# --- 7.1 --------------------------------------------------------------


def test_a_writing_endpoint_tagged_read_fails_the_harness(tmp_path: Path) -> None:
    """**The falsifier.** A test-local copy of the binding table with
    `POST /api/v1/runs` temporarily registered as if it were `read`; the
    digest-pair harness must report a mismatch -- proving the check is not
    vacuously green before it is trusted with the real document (threat
    matrix -- "A read path that writes")."""
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

        proof = _run_read_only_proof(
            store=store, db_path=db_path, ops=tampered_ops, bindings=tampered_bindings
        )

        assert proof.before[_LOGICAL] != proof.after[_LOGICAL]
        assert proof.before[_EXECUTIONS] != proof.after[_EXECUTIONS]
    finally:
        store.close()


# --- 7.2 --------------------------------------------------------------


def test_logical_content_digest_unchanged_after_every_read_path(tmp_path: Path) -> None:
    """*(history-read-api -> Read-only read surface -> Reading leaves stored
    data unchanged)*."""
    db_path = tmp_path / "store" / "vantage.db"
    _seed_database(db_path)

    store = SqliteExecutionStore(db_path)
    try:
        client = TestClient(create_app(store))
        read_ops = _read_operations(_document())
        bindings = _read_bindings(client)
        assert set(bindings) == read_ops, "binding table incomplete for this run"

        proof = _run_read_only_proof(store=store, db_path=db_path, ops=read_ops, bindings=bindings)

        assert proof.before[_LOGICAL] == proof.after[_LOGICAL]
        assert proof.before[_EXECUTIONS] == proof.after[_EXECUTIONS] == 1
        assert proof.before[_RESULTS] == proof.after[_RESULTS] == 1
    finally:
        store.close()


# --- 7.3 --------------------------------------------------------------


def test_main_file_digest_stable_despite_wal_checkpointing(tmp_path: Path) -> None:
    """*(Read-only read surface -> The main-file digest is stable despite
    WAL checkpointing)* -- connection pinning per the module docstring."""
    db_path = tmp_path / "store" / "vantage.db"
    _seed_database(db_path)

    store = SqliteExecutionStore(db_path)
    try:
        client = TestClient(create_app(store))
        read_ops = _read_operations(_document())
        bindings = _read_bindings(client)

        proof = _run_read_only_proof(store=store, db_path=db_path, ops=read_ops, bindings=bindings)

        assert proof.before[_MAIN_FILE] == proof.after[_MAIN_FILE]
    finally:
        store.close()


# --- 7.4 --------------------------------------------------------------


def test_every_read_path_has_a_binding() -> None:
    """A path tagged `read` in the document without a binding here fails
    this test rather than being silently skipped -- what keeps this proof
    from becoming the unfailable check the project rejected a generated
    document over (design.md D65)."""
    read_ops = _read_operations(_document())
    client = TestClient(create_app(InMemoryExecutionStore()))

    assert set(_read_bindings(client)) == read_ops
