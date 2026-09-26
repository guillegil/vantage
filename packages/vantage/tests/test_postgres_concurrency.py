"""Several `PostgresExecutionStore`s on one database -- each with a pool of
its own, as separate server processes have -- writing at once from threads:
every run, result, catalogue row and metadata row lands exactly once, the
section bound holds across them, and nothing deadlocks or fails. And
several stores opening one empty database at the same moment: exactly one
creates the schema, and every one of them works.

Every thread is a daemon and is joined with a timeout, as in
`test_concurrency.py`: a deadlock must fail the test, not hang the suite.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from functools import partial

import pytest
from vantage.core.domain.execution import Execution
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES
from vantage.core.domain.sections import MAX_SECTIONS
from vantage.core.ports.storage import MetadataEntry, MetadataFile, NamespaceFullError, RunMetadata
from vantage.service.routes.sections import TEST_SECTIONS_NAMESPACE
from vantage.storage.postgres import PostgresExecutionStore
from vantage.storage.postgres import connection as postgres_connection
from vantage.storage.postgres import store as store_module
from vantage_port_contract import StoredMetadata, _execution, _result, _start_only_execution

pytest_plugins = ["postgres_fixtures"]
pytestmark = pytest.mark.postgres

_JOIN_TIMEOUT_SECONDS = 60
_BASE = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)


def _run_concurrently(targets: list[Callable[[], object]]) -> list[BaseException]:
    """Start every target at once, behind a barrier, and return whatever they
    raised. Fails if any thread is still running after the timeout."""
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(targets))

    def _wrap(target: Callable[[], object]) -> Callable[[], None]:
        def _run() -> None:
            try:
                barrier.wait()
                target()
            except BaseException as exc:  # noqa: BLE001 -- collected for the caller's assertion
                errors.append(exc)

        return _run

    threads = [threading.Thread(target=_wrap(target), daemon=True) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
    assert not any(thread.is_alive() for thread in threads), "a thread never finished"
    return errors


@pytest.fixture
def stores(postgres_url: str) -> Iterator[list[PostgresExecutionStore]]:
    """Three stores on one database, as three server processes would be."""
    opened = [PostgresExecutionStore(postgres_url, max_connections=4) for _ in range(3)]
    try:
        yield opened
    finally:
        for store in opened:
            store.close()


def test_every_report_from_every_store_lands_exactly_once(
    stores: list[PostgresExecutionStore],
) -> None:
    """Six runs share forty tests. Each run is reported as a start, four
    slices of results and a finish carrying every result, and every report
    is sent twice; the reports are dealt across twelve threads on three
    stores, and some list their tests in reverse, so two reports lock the
    catalogue rows they share in opposite orders unless the store orders
    them."""
    node_ids = [f"tests/test_shared.py::test_{index:02d}" for index in range(40)]
    runs = [f"{index:032x}" for index in range(6)]
    started = {run_id: _BASE + timedelta(minutes=index) for index, run_id in enumerate(runs)}
    reports: list[tuple[str, Execution, list[str]]] = []
    for run_id in runs:
        start = _start_only_execution(run_id, started=started[run_id])
        finish = _execution(run_id, started=started[run_id])
        reports.append((run_id, start, []))
        for first in range(0, 40, 10):
            reports.append((run_id, start, node_ids[first : first + 10]))
        reports.append((run_id, finish, node_ids))
    # Each report twice, dealt in an order that mixes runs and kinds.
    deck = [report for report in reports for _copy in range(2)]
    deck = [deck[(index * 7) % len(deck)] for index in range(len(deck))]
    created: list[tuple[str, bool]] = []

    def _send(store: PostgresExecutionStore, hand: list[tuple[str, Execution, list[str]]]) -> None:
        for turn, (run_id, execution, reported) in enumerate(hand):
            ordered = reported if turn % 2 else list(reversed(reported))
            results = [_result(node_id) for node_id in ordered]
            was_created = store.record_session(execution, results=results, received_at=_BASE)
            created.append((run_id, was_created))

    hands = [deck[seat::12] for seat in range(12)]
    errors = _run_concurrently(
        [partial(_send, stores[seat % 3], hand) for seat, hand in enumerate(hands)]
    )

    assert errors == []
    reader = stores[0]
    assert reader.count_executions() == len(runs)
    assert reader.count_results() == len(runs) * len(node_ids)
    assert sorted(run_id for run_id, was_created in created if was_created) == runs
    for run_id in runs:
        assert reader.get_execution(run_id) == _execution(run_id, started=started[run_id])
        stored = sorted(result.identity.node_id for result in reader.get_results(run_id))
        assert stored == node_ids
    newest = runs[-1]
    for node_id in node_ids:
        entry = reader.get_catalogue_entry(node_id)
        assert entry is not None
        assert entry.first_seen_at == started[runs[0]]
        assert entry.last_seen_at == started[newest]
        assert entry.last_seen_run_id == newest


def test_a_runs_metadata_lands_once_and_its_bound_holds_across_stores(
    stores: list[PostgresExecutionStore], postgres_metadata: StoredMetadata
) -> None:
    """Twelve reports of one run, each with forty keys of its own and one
    file and key they all share, arrive at once through three stores: the
    shared rows are stored once, and the run keeps no more than the bound
    of keys, however the reports interleave."""
    run_id = "c" * 32
    shared_file = MetadataFile(source_file="board.json", content_type="json", status="captured")
    shared_key = MetadataEntry(
        key="board", value="rev-b", source_file="board.json", status="captured"
    )

    def _report(store: PostgresExecutionStore, seat: int) -> None:
        own = tuple(
            MetadataEntry(
                key=f"seat{seat:02d}.key{index:02d}",
                value="v",
                source_file=None,
                status="captured",
                source="session",
                declared=False,
            )
            for index in range(40)
        )
        store.record_session(
            _start_only_execution(run_id, started=_BASE),
            results=(),
            received_at=_BASE,
            metadata=RunMetadata(files=(shared_file,), entries=(shared_key, *own)),
        )

    errors = _run_concurrently([partial(_report, stores[seat % 3], seat) for seat in range(12)])

    assert errors == []
    stored = postgres_metadata(run_id)
    assert stored.files == (shared_file,)
    keys = [entry.key for entry in stored.entries]
    assert len(keys) == MAX_METADATA_ENTRIES
    assert len(set(keys)) == len(keys)
    assert "board" in keys


def test_section_posts_racing_for_the_last_slot_across_stores_never_pass_the_bound(
    stores: list[PostgresExecutionStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each store counts and writes in its own transaction; counting what is
    committed and then writing would let a racer on every store see the last
    slot free. Exactly one racer gets it."""
    # A count that takes a while after reading its snapshot keeps every
    # racer's count ahead of the first commit, unless the store makes the
    # racers take turns; a racer is otherwise done too fast to overlap.
    monkeypatch.setattr(
        store_module,
        "_COUNT_SETTINGS",
        "WITH pause AS MATERIALIZED (SELECT pg_sleep(0.02))"
        " SELECT count(*) FROM vantage.user_setting, pause WHERE namespace = %s",
    )
    for index in range(MAX_SECTIONS - 1):
        stores[0].upsert_setting(
            TEST_SECTIONS_NAMESPACE, f"Seeded{index:03d}", value="{}", updated_at=_BASE
        )
    outcomes: list[str] = []

    def _race(store: PostgresExecutionStore, index: int) -> None:
        try:
            store.upsert_setting(
                TEST_SECTIONS_NAMESPACE,
                f"Racer{index:02d}",
                value="{}",
                updated_at=_BASE,
                max_keys=MAX_SECTIONS,
            )
        except NamespaceFullError:
            outcomes.append("full")
        else:
            outcomes.append("created")

    errors = _run_concurrently([partial(_race, stores[index % 3], index) for index in range(16)])

    assert errors == []
    assert sorted(outcomes) == ["created"] + ["full"] * 15
    assert len(stores[1].list_settings(TEST_SECTIONS_NAMESPACE)) == MAX_SECTIONS


def test_stores_opening_one_empty_database_at_once_create_the_schema_once(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every store finds the schema missing; only the one holding the lock
    may create it, and the others must then find it stamped rather than
    create it again or fail on it."""
    creations: list[None] = []
    create = postgres_connection._create_schema

    def _counted(conn: postgres_connection.PgConnection) -> None:
        creations.append(None)
        create(conn)

    monkeypatch.setattr(postgres_connection, "_create_schema", _counted)
    opened: list[PostgresExecutionStore] = []

    def _open() -> None:
        opened.append(PostgresExecutionStore(postgres_url, max_connections=2))

    try:
        errors = _run_concurrently([_open for _ in range(4)])

        assert errors == []
        assert len(creations) == 1
        for index, store in enumerate(opened):
            store.record_session(_execution(f"{index:032x}"), results=(), received_at=_BASE)
        assert all(store.count_executions() == len(opened) for store in opened)
    finally:
        for store in opened:
            store.close()
