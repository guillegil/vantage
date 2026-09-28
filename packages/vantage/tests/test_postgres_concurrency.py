"""Several `PostgresExecutionStore`s on one database -- each with a pool of
its own, as separate server processes have -- writing at once from threads:
every run, result, catalogue row and metadata row lands exactly once, the
section bound holds across them, and nothing deadlocks or fails. A read
that must see one state sees it, whatever another store commits meanwhile.
And several stores opening one empty database at the same moment: exactly
one creates the schema, and every one of them works. Projects, too: one
name created at once through several stores is created once, a run keeps
the project its first report gave it, and each project keeps its own
catalogue rows and its own section bound, however its writers interleave
with another project's. And the first admin and logins: servers starting
at once on one new database create one admin, and a login overlapping a
password change never leaves a live token made with the old password.

Every thread is a daemon and is joined with a timeout, as in
`test_concurrency.py`: a deadlock must fail the test, not hang the suite.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial

import psycopg
import pytest
from psycopg import sql
from vantage.core.domain.access import LOGIN_TOKEN_LIFETIME, Token, User, token_digest
from vantage.core.domain.execution import Execution
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES
from vantage.core.domain.projects import DEFAULT_PROJECT
from vantage.core.domain.sections import MAX_SECTIONS
from vantage.core.ports.storage import (
    MetadataEntry,
    MetadataFile,
    NamespaceFullError,
    ProjectExistsError,
    ProjectMismatchError,
    RunMetadata,
)
from vantage.service.routes.sections import TEST_SECTIONS_NAMESPACE
from vantage.storage.postgres import PostgresExecutionStore
from vantage.storage.postgres import connection as postgres_connection
from vantage.storage.postgres import store as store_module
from vantage_port_contract import (
    _HASH,
    _OTHER_HASH,
    StoredMetadata,
    _execution,
    _result,
    _start_only_execution,
)

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


def _await_lock_waiters(url: str, count: int) -> None:
    """Return once `count` sessions on the database `url` names are waiting
    for a lock. Fails if they never are, within the timeout."""
    deadline = time.monotonic() + _JOIN_TIMEOUT_SECONDS
    with psycopg.connect(url, autocommit=True) as conn:
        while True:
            row = conn.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ).fetchone()
            if row is not None and row[0] >= count:
                return
            assert time.monotonic() < deadline, f"{count} sessions never waited for a lock"
            time.sleep(0.01)


def _default_isolation(url: str, isolation: str | None) -> None:
    """Make `isolation` the default of every session later opened on the
    database `url` names, as an administrator may have: the stores must
    work whatever it is."""
    if isolation is None:
        return
    with psycopg.connect(url, autocommit=True) as conn:
        database = conn.info.dbname
        conn.execute(
            sql.SQL("ALTER DATABASE {} SET default_transaction_isolation = {}").format(
                sql.Identifier(database), sql.Literal(isolation)
            )
        )


# The database's default isolation level: the server's own, and the two a
# store must not be misled by.
_DEFAULT_ISOLATIONS = pytest.mark.parametrize(
    "default_isolation",
    [None, "repeatable read", "serializable"],
    ids=["read-committed", "repeatable-read", "serializable"],
)


@pytest.fixture
def default_isolation() -> str | None:
    """The server's own default, unless a test parametrises this name with
    `_DEFAULT_ISOLATIONS`, which overrides it."""
    return None


@pytest.fixture
def stores(
    postgres_url: str, default_isolation: str | None
) -> Iterator[list[PostgresExecutionStore]]:
    """Three stores on one database, as three server processes would be,
    opened once the database defaults to `default_isolation`."""
    _default_isolation(postgres_url, default_isolation)
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
            was_created = store.record_session(
                execution, results=results, received_at=_BASE, project=DEFAULT_PROJECT
            )
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
        entry = reader.get_catalogue_entry(node_id, project=DEFAULT_PROJECT)
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
            project=DEFAULT_PROJECT,
        )

    errors = _run_concurrently([partial(_report, stores[seat % 3], seat) for seat in range(12)])

    assert errors == []
    stored = postgres_metadata(run_id)
    assert stored.files == (shared_file,)
    keys = [entry.key for entry in stored.entries]
    assert len(keys) == MAX_METADATA_ENTRIES
    assert len(set(keys)) == len(keys)
    assert "board" in keys


@_DEFAULT_ISOLATIONS
def test_section_posts_racing_for_the_last_slot_across_stores_never_pass_the_bound(
    stores: list[PostgresExecutionStore],
    monkeypatch: pytest.MonkeyPatch,
    default_isolation: str | None,
) -> None:
    """Each store counts and writes in its own transaction; counting what is
    committed and then writing would let a racer on every store see the last
    slot free. Exactly one racer gets it -- also where the database defaults
    to REPEATABLE READ, under which a racer's count would read the snapshot
    its first statement took, before the lock it waited for."""
    # A count that takes a while after reading its snapshot keeps every
    # racer's count ahead of the first commit, unless the store makes the
    # racers take turns; a racer is otherwise done too fast to overlap.
    monkeypatch.setattr(
        store_module,
        "_COUNT_SETTINGS",
        "WITH pause AS MATERIALIZED (SELECT pg_sleep(0.02))"
        " SELECT count(*) FROM vantage.project_setting, pause"
        " WHERE project = %s AND namespace = %s",
    )
    for index in range(MAX_SECTIONS - 1):
        stores[0].upsert_setting(
            TEST_SECTIONS_NAMESPACE,
            f"Seeded{index:03d}",
            value="{}",
            updated_at=_BASE,
            project=DEFAULT_PROJECT,
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
                project=DEFAULT_PROJECT,
            )
        except NamespaceFullError:
            outcomes.append("full")
        else:
            outcomes.append("created")

    errors = _run_concurrently([partial(_race, stores[index % 3], index) for index in range(16)])

    assert errors == []
    assert sorted(outcomes) == ["created"] + ["full"] * 15
    assert (
        len(stores[1].list_settings(TEST_SECTIONS_NAMESPACE, project=DEFAULT_PROJECT))
        == MAX_SECTIONS
    )


def test_the_filtered_page_and_its_horizon_are_read_from_one_snapshot(
    stores: list[PostgresExecutionStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another server commits a keyed run while this one is between the
    page and the horizon. Both must describe the state the page was read
    from: no match, and every run predating the key nothing carried yet --
    never the page from before the commit and the count from after it."""
    reader, other_server = stores[0], stores[1]
    keyed = RunMetadata(
        files=(MetadataFile(source_file="m.json", content_type="json", status="captured"),),
        entries=(MetadataEntry(key="fw", value="2.1", source_file="m.json", status="captured"),),
    )
    for index in range(3):
        other_server.record_session(
            _execution(f"{index:032x}", started=_BASE + timedelta(minutes=index)),
            results=(),
            received_at=_BASE,
            project=DEFAULT_PROJECT,
        )
    count = store_module._count_runs_predating
    fired: list[str] = []

    def _commit_a_keyed_run_first(
        conn: postgres_connection.PgConnection, project: str, key: str
    ) -> int:
        if not fired:
            fired.append(key)
            other_server.record_session(
                _execution("f" * 32, started=_BASE - timedelta(hours=1)),
                results=(),
                received_at=_BASE,
                metadata=keyed,
                project=DEFAULT_PROJECT,
            )
        return count(conn, project, key)

    monkeypatch.setattr(store_module, "_count_runs_predating", _commit_a_keyed_run_first)

    page, predating = reader.list_runs_with_metadata_horizon(
        filters=[("fw", "2.1")], limit=10, offset=0, project=DEFAULT_PROJECT
    )

    assert fired == ["fw"]
    assert (page.items, predating) == ((), (3,))
    assert reader.count_executions() == 4


@_DEFAULT_ISOLATIONS
def test_stores_opening_one_empty_database_at_once_create_the_schema_once(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch, default_isolation: str | None
) -> None:
    """Every store finds the schema missing; only the one holding the lock
    may create it, and the others must then find it stamped rather than
    create it again or fail on it -- or, under a REPEATABLE READ default,
    read the stamp from a snapshot older than its creation, and refuse the
    database for having none."""
    _default_isolation(postgres_url, default_isolation)
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
            store.record_session(
                _execution(f"{index:032x}"), results=(), received_at=_BASE, project=DEFAULT_PROJECT
            )
        assert all(store.count_executions() == len(opened) for store in opened)
        for store in opened:
            assert [project.name for project in store.list_projects()] == [DEFAULT_PROJECT]
    finally:
        for store in opened:
            store.close()


def test_racing_to_create_one_project_across_stores_creates_it_once(
    stores: list[PostgresExecutionStore],
) -> None:
    """Twelve creations of one name through three stores: one inserts it,
    every other one finds it there, and nothing fails otherwise."""
    outcomes: list[str] = []

    def _create(store: PostgresExecutionStore) -> None:
        try:
            store.create_project("firmware", created_at=_BASE)
        except ProjectExistsError:
            outcomes.append("exists")
        else:
            outcomes.append("created")

    errors = _run_concurrently([partial(_create, stores[seat % 3]) for seat in range(12)])

    assert errors == []
    assert sorted(outcomes) == ["created"] + ["exists"] * 11
    for store in stores:
        assert [project.name for project in store.list_projects()] == [
            DEFAULT_PROJECT,
            "firmware",
        ]


def test_one_new_run_reported_into_two_projects_at_once_lands_in_one(
    stores: list[PostgresExecutionStore],
) -> None:
    """Two servers take the first report of one run at the same moment, each
    naming a different project. The run is created once, in the project of
    whichever report got there first, and the other report is refused as a
    mismatch, storing none of its results -- never a run whose project the
    later report overwrote, nor its results under the other project's
    catalogue. Repeated for several runs, since one race may not overlap."""
    stores[0].create_project("firmware", created_at=_BASE)
    run_ids = [f"{index:032x}" for index in range(8)]
    node_id = "tests/test_shared.py::test_x"
    projects = {stores[1]: DEFAULT_PROJECT, stores[2]: "firmware"}

    for run_id in run_ids:
        landed: list[str] = []

        def _report(store: PostgresExecutionStore, run_id: str = run_id) -> None:
            store.record_session(
                _start_only_execution(run_id, started=_BASE),
                results=[_result(node_id)],
                received_at=_BASE,
                project=projects[store],
            )
            landed.append(projects[store])

        errors = _run_concurrently([partial(_report, store) for store in projects])

        assert len(landed) == 1, run_id
        assert [type(error) for error in errors] == [ProjectMismatchError], run_id
        detail = stores[0].get_run_detail(run_id)
        assert detail is not None
        assert detail.project == landed[0]
        assert len(stores[0].get_results(run_id)) == 1
        other = next(project for project in projects.values() if project != landed[0])
        history = stores[0].list_history(project=other, node_id=node_id, limit=50, offset=0)
        assert run_id not in {item.run_id for item in history.items}
    assert stores[0].count_executions() == len(run_ids)
    assert stores[0].count_results() == len(run_ids)


def test_reports_of_one_node_id_into_two_projects_across_stores_keep_a_row_each(
    stores: list[PostgresExecutionStore], postgres_url: str
) -> None:
    """Two projects run the same forty tests, three runs each, reported as a
    start, slices and a finish through three stores at once, some listing
    their tests in reverse. The catalogue holds exactly one row per node id
    per project, each first and last seen within its own project's runs,
    and every result lands once."""
    stores[0].create_project("firmware", created_at=_BASE)
    node_ids = [f"tests/test_shared.py::test_{index:02d}" for index in range(40)]
    runs = {
        DEFAULT_PROJECT: [f"a{index:031x}" for index in range(3)],
        "firmware": [f"b{index:031x}" for index in range(3)],
    }
    started = {
        run_id: _BASE + timedelta(minutes=index)
        for project_runs in runs.values()
        for index, run_id in enumerate(project_runs)
    }
    reports: list[tuple[str, Execution, list[str]]] = []
    for project, project_runs in runs.items():
        for run_id in project_runs:
            start = _start_only_execution(run_id, started=started[run_id])
            for first in range(0, 40, 10):
                reports.append((project, start, node_ids[first : first + 10]))
            reports.append((project, _execution(run_id, started=started[run_id]), node_ids))
    # Dealt in an order that mixes projects, runs and kinds.
    deck = [reports[(index * 7) % len(reports)] for index in range(len(reports))]

    def _send(store: PostgresExecutionStore, hand: list[tuple[str, Execution, list[str]]]) -> None:
        for turn, (project, execution, reported) in enumerate(hand):
            ordered = reported if turn % 2 else list(reversed(reported))
            store.record_session(
                execution,
                results=[_result(node_id) for node_id in ordered],
                received_at=_BASE,
                project=project,
            )

    hands = [deck[seat::12] for seat in range(12)]
    errors = _run_concurrently(
        [partial(_send, stores[seat % 3], hand) for seat, hand in enumerate(hands)]
    )

    assert errors == []
    reader = stores[0]
    assert reader.count_results() == len(started) * len(node_ids)
    with psycopg.connect(postgres_url) as conn:
        rows = conn.execute(
            "SELECT project, count(*), count(DISTINCT node_id) FROM vantage.test_case"
            " GROUP BY project ORDER BY project"
        ).fetchall()
    assert rows == [(project, len(node_ids), len(node_ids)) for project in sorted(runs)]
    for project, project_runs in runs.items():
        for node_id in node_ids:
            entry = reader.get_catalogue_entry(node_id, project=project)
            assert entry is not None
            assert entry.first_seen_at == started[project_runs[0]]
            assert entry.last_seen_at == started[project_runs[-1]]
            assert entry.last_seen_run_id == project_runs[-1]
            history = reader.list_history(project=project, node_id=node_id, limit=50, offset=0)
            assert [item.run_id for item in history.items] == project_runs[::-1]


def test_section_posts_racing_for_the_last_slot_across_stores_fill_each_project_once(
    stores: list[PostgresExecutionStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two projects, each one section short of the bound, and racers for
    both through three stores: each project's last slot goes to exactly one
    of its racers. Racers of different projects may run side by side, since
    neither can take the other's slot, but a count across projects would
    find both full and refuse every racer."""
    monkeypatch.setattr(
        store_module,
        "_COUNT_SETTINGS",
        "WITH pause AS MATERIALIZED (SELECT pg_sleep(0.02))"
        " SELECT count(*) FROM vantage.project_setting, pause"
        " WHERE project = %s AND namespace = %s",
    )
    projects = [DEFAULT_PROJECT, "firmware"]
    stores[0].create_project("firmware", created_at=_BASE)
    for project in projects:
        for index in range(MAX_SECTIONS - 1):
            stores[0].upsert_setting(
                TEST_SECTIONS_NAMESPACE,
                f"Seeded{index:03d}",
                value="{}",
                updated_at=_BASE,
                project=project,
            )
    outcomes: dict[str, list[str]] = {project: [] for project in projects}

    def _race(store: PostgresExecutionStore, project: str, index: int) -> None:
        try:
            store.upsert_setting(
                TEST_SECTIONS_NAMESPACE,
                f"Racer{index:02d}",
                value="{}",
                updated_at=_BASE,
                max_keys=MAX_SECTIONS,
                project=project,
            )
        except NamespaceFullError:
            outcomes[project].append("full")
        else:
            outcomes[project].append("created")

    errors = _run_concurrently(
        [partial(_race, stores[index % 3], projects[index % 2], index) for index in range(16)]
    )

    assert errors == []
    for project in projects:
        assert sorted(outcomes[project]) == ["created"] + ["full"] * 7
        assert (
            len(stores[1].list_settings(TEST_SECTIONS_NAMESPACE, project=project)) == MAX_SECTIONS
        )


@_DEFAULT_ISOLATIONS
def test_servers_starting_at_once_on_one_new_database_create_one_admin(
    stores: list[PostgresExecutionStore], postgres_url: str, default_isolation: str | None
) -> None:
    """Every server creates the same `admin`, so of several that find the
    database without a user, one inserts it and every other one, waiting on
    that row, inserts nothing -- rather than fail on it. A transaction
    holding the name without committing keeps every racer past its check
    for a user and waiting at the insert, so they all overlap; it then rolls
    back, as a start that failed would."""
    racers = 6
    created: list[User | None] = []

    def _start(store: PostgresExecutionStore) -> None:
        created.append(store.create_first_admin("admin", password_hash=_HASH, created_at=_BASE))

    with psycopg.connect(postgres_url) as holder:
        holder.execute(
            "INSERT INTO vantage.account (name, admin, disabled, created_at)"
            " VALUES ('admin', true, false, now())"
        )

        def _fail_the_holder() -> None:
            _await_lock_waiters(postgres_url, racers)
            holder.rollback()

        errors = _run_concurrently(
            [partial(_start, stores[seat % 3]) for seat in range(racers)] + [_fail_the_holder]
        )

    assert errors == []
    assert [user.name for user in created if user is not None] == ["admin"]
    assert created.count(None) == racers - 1
    assert [user.name for user in stores[0].list_users()] == ["admin"]
    assert stores[1].get_password_hash("admin") == _HASH


@_DEFAULT_ISOLATIONS
@pytest.mark.parametrize("checked", [False, True], ids=["set", "replaced"])
def test_logins_overlapping_a_password_change_leave_no_live_token_of_the_old_password(
    stores: list[PostgresExecutionStore],
    monkeypatch: pytest.MonkeyPatch,
    default_isolation: str | None,
    checked: bool,
) -> None:
    """Logins checked against the old password have inserted their tokens,
    and not yet committed, when another server sets a new one. Once both
    are done, every token those logins returned is revoked: the change waits
    for the logins it overlaps and then revokes their tokens, rather than
    revoke only what it can see and leave the rest live -- also where the
    database defaults to REPEATABLE READ, under which the revocation would
    read the snapshot the change took before it waited. A login with the
    old password after the change is refused."""
    stores[0].create_user("alice", admin=False, created_at=_BASE)
    stores[0].set_password("alice", password_hash=_HASH, changed_at=_BASE)
    logins = 4
    inserted = threading.Semaphore(0)
    holding = threading.Event()
    holding.set()
    to_token = store_module._row_to_token

    def _held_after_the_insert(row: tuple[object, ...]) -> Token:
        # Runs inside the login's transaction, between its insert and its
        # commit; holding it there stands in for a server slowed down
        # between the two.
        if holding.is_set():
            inserted.release()
            time.sleep(0.3)
        return to_token(row)

    monkeypatch.setattr(store_module, "_row_to_token", _held_after_the_insert)
    tokens: list[Token | None] = []
    changed_at = _BASE + timedelta(hours=1)

    def _login(store: PostgresExecutionStore, seat: int) -> None:
        tokens.append(
            store.create_login_token(
                "alice",
                password_hash=_HASH,
                digest=token_digest(f"login-{seat}"),
                created_at=_BASE,
                expires_at=_BASE + LOGIN_TOKEN_LIFETIME,
            )
        )

    def _change() -> None:
        for _ in range(logins):
            assert inserted.acquire(timeout=_JOIN_TIMEOUT_SECONDS), "a login never inserted"
        changed = stores[2].set_password(
            "alice",
            password_hash=_OTHER_HASH,
            changed_at=changed_at,
            replacing=_HASH if checked else None,
        )
        assert changed is True

    errors = _run_concurrently(
        [partial(_login, stores[seat % 2], seat) for seat in range(logins)] + [_change]
    )
    holding.clear()

    assert errors == []
    returned = [token for token in tokens if token is not None]
    assert len(returned) == logins
    for token in returned:
        assert stores[0].get_token(token.id) == replace(token, revoked_at=changed_at)
    for seat in range(logins):
        assert stores[1].authenticate(token_digest(f"login-{seat}"), now=_BASE) is None
    late = stores[0].create_login_token(
        "alice",
        password_hash=_HASH,
        digest=token_digest("late"),
        created_at=changed_at,
        expires_at=changed_at + LOGIN_TOKEN_LIFETIME,
    )
    assert late is None
