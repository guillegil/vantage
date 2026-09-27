"""`vantage user`, `vantage token` and `vantage project`: managing a
database's users, their tokens and its projects where the database is
(`service/manage.py`).

The commands run in process through `cli.main`, as the `vantage` entry
point runs them, and every result is read back through a store of its own,
as a server on the same database would read it. One test runs the command
where the web framework cannot be imported.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from vantage.core.domain.access import ADMIN_SCOPE, READ_SCOPE, RECORD_SCOPE, token_digest
from vantage.core.domain.projects import DEFAULT_PROJECT
from vantage.core.ports.storage import ExecutionStore
from vantage.service import cli
from vantage.storage.sqlite_store import SqliteExecutionStore

# `postgres_url`, for the command against the real PostgreSQL adapter.
pytest_plugins = ["postgres_fixtures"]


@pytest.fixture(autouse=True)
def _no_ambient_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VANTAGE_DATABASE", raising=False)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "data" / "vantage.db"


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    """`vantage *argv`: its exit status, stdout and stderr."""
    with pytest.raises(SystemExit) as exited:
        cli.main(list(argv))
    captured = capsys.readouterr()
    return int(exited.value.code or 0), captured.out, captured.err


@pytest.fixture
def stored(database: Path) -> Iterator[ExecutionStore]:
    """A store on `database` that already has a user `alice`, an admin, and
    `bob`, who is not."""
    store = SqliteExecutionStore(database)
    now = datetime.now(timezone.utc)
    store.create_user("alice", admin=True, created_at=now)
    store.create_user("bob", admin=False, created_at=now)
    try:
        yield store
    finally:
        store.close()


def test_adding_the_first_user_creates_the_database_and_says_it_is_closed(
    capsys: pytest.CaptureFixture[str], database: Path
) -> None:
    status, out, err = _run(capsys, "user", "add", "alice", "--admin", "--database", str(database))

    assert (status, out) == (0, "")
    assert err == (
        "vantage: added admin user alice\n"
        "vantage: this database has a user now, so every server on it requires a token: "
        "vantage token create alice\n"
    )
    store = SqliteExecutionStore(database)
    try:
        user = store.get_user("alice")
        assert user is not None and (user.admin, user.disabled) == (True, False)
        assert store.access_required() is True
    finally:
        store.close()


def test_adding_a_later_user_says_only_that(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    status, _out, err = _run(capsys, "user", "add", "carol", "--database", str(database))

    assert (status, err) == (0, "vantage: added user carol\n")
    carol = stored.get_user("carol")
    assert carol is not None and carol.admin is False


@pytest.mark.parametrize("command", ["user", "project"])
def test_a_refused_name_creates_no_database(
    capsys: pytest.CaptureFixture[str], database: Path, command: str
) -> None:
    """The name is checked before the database is opened, so a refusal
    leaves nothing behind, not even an empty database."""
    status, out, err = _run(capsys, command, "add", "Bad", "--database", str(database))

    assert (status, out) == (1, "")
    assert err.startswith(f"vantage: a {command} name is 1 to 64 characters")
    assert not database.parent.exists()


@pytest.mark.parametrize(
    ("name", "refusal"),
    [
        ("alice", "vantage: there is already a user named alice\n"),
        (
            "Alice",
            "vantage: a user name is 1 to 64 characters of a-z, 0-9, '.', '_' and '-', "
            "starting with a letter or a digit\n",
        ),
        (
            "-alice",
            "vantage: a user name is 1 to 64 characters of a-z, 0-9, '.', '_' and '-', "
            "starting with a letter or a digit\n",
        ),
    ],
)
def test_a_name_that_cannot_be_added_is_one_line(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    name: str,
    refusal: str,
) -> None:
    status, out, err = _run(capsys, "user", "add", "--database", str(database), "--", name)

    assert (status, out, err) == (1, "", refusal)
    assert [user.name for user in stored.list_users()] == ["alice", "bob"]


def test_users_list_in_name_order(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    stored.update_user("bob", disabled=True)

    status, out, _err = _run(capsys, "user", "list", "--database", str(database))

    assert status == 0
    header, alice, bob = out.splitlines()
    assert header.split() == ["NAME", "ADMIN", "STATE", "CREATED"]
    assert alice.split()[:3] == ["alice", "yes", "enabled"]
    assert bob.split()[:3] == ["bob", "no", "disabled"]


def test_listing_users_of_a_database_with_none_prints_nothing(
    capsys: pytest.CaptureFixture[str], database: Path
) -> None:
    SqliteExecutionStore(database).close()

    assert _run(capsys, "user", "list", "--database", str(database)) == (0, "", "")


@pytest.mark.parametrize(
    "argv",
    [
        ["user", "list"],
        ["user", "update", "alice", "--disable"],
        ["token", "create", "alice"],
        ["token", "list"],
        ["token", "revoke", "1"],
        ["project", "list"],
    ],
)
def test_no_command_but_adding_a_user_creates_a_database(
    capsys: pytest.CaptureFixture[str], database: Path, argv: list[str]
) -> None:
    status, out, err = _run(capsys, *argv, "--database", str(database))

    assert (status, out, err) == (1, "", f"vantage: there is no database at {database}\n")
    assert not database.parent.exists()


def test_the_database_comes_from_vantage_database_when_not_named(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VANTAGE_DATABASE", str(database))

    status, out, _err = _run(capsys, "user", "list")

    assert status == 0
    assert "alice" in out


def test_updating_a_user_changes_what_it_is_told_and_shows_the_user(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    status, out, _err = _run(
        capsys, "user", "update", "bob", "--admin", "--disable", "--database", str(database)
    )
    bob = stored.get_user("bob")

    assert status == 0
    assert out.splitlines()[1].split()[:3] == ["bob", "yes", "disabled"]
    assert bob is not None and (bob.admin, bob.disabled) == (True, True)

    _run(capsys, "user", "update", "bob", "--no-admin", "--enable", "--database", str(database))
    bob = stored.get_user("bob")
    assert bob is not None and (bob.admin, bob.disabled) == (False, False)


@pytest.mark.parametrize(
    ("argv", "refusal"),
    [
        (["carol", "--disable"], "vantage: there is no user named carol\n"),
        (["bob"], "vantage: say what to change: --admin, --no-admin, --disable or --enable\n"),
    ],
)
def test_an_update_that_cannot_be_made_is_one_line(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    argv: list[str],
    refusal: str,
) -> None:
    status, out, err = _run(capsys, "user", "update", *argv, "--database", str(database))

    assert (status, out, err) == (1, "", refusal)


def test_contradicting_flags_are_a_usage_error(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    status, _out, err = _run(
        capsys, "user", "update", "bob", "--admin", "--no-admin", "--database", str(database)
    )

    assert status == 2
    assert "not allowed with argument" in err


def test_a_created_token_is_printed_alone_once_and_grants_its_scopes(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    status, out, err = _run(
        capsys,
        "token",
        "create",
        "bob",
        "--scope",
        "record",
        "--label",
        "ci nightly",
        "--database",
        str(database),
    )

    token = out.removesuffix("\n")
    assert status == 0
    assert out == f"{token}\n" and token.startswith("vantage_")
    (created,) = stored.list_tokens()
    assert err == f"vantage: created token {created.id} of bob with record; it is shown this once\n"
    assert (created.user, created.label) == ("bob", "ci nightly")
    assert created.scopes == frozenset({RECORD_SCOPE})
    grant = stored.authenticate(token_digest(token))
    assert grant is not None and grant.scopes == {RECORD_SCOPE}
    assert token not in err


def test_a_token_holds_read_and_record_unless_told_otherwise(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    status, out, _err = _run(capsys, "token", "create", "alice", "--database", str(database))

    assert status == 0
    grant = stored.authenticate(token_digest(out.strip()))
    assert grant is not None and grant.scopes == {READ_SCOPE, RECORD_SCOPE}


def test_an_admin_token_for_an_admin(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    status, out, _err = _run(
        capsys, "token", "create", "alice", "--scope", "admin", "--database", str(database)
    )

    grant = stored.authenticate(token_digest(out.strip()))
    assert status == 0
    assert grant is not None and grant.allows(ADMIN_SCOPE)


@pytest.mark.parametrize(
    ("argv", "refusal"),
    [
        (
            ["bob", "--scope", "admin"],
            "vantage: bob is not an admin, so the admin scope would grant nothing\n",
        ),
        (["carol"], "vantage: there is no user named carol\n"),
        (
            ["bob", "--label", "two\nlines"],
            "vantage: a token label is at most 200 characters, with no control characters\n",
        ),
    ],
)
def test_a_token_that_cannot_be_created_is_one_line_and_nothing_is_stored(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    argv: list[str],
    refusal: str,
) -> None:
    status, out, err = _run(capsys, "token", "create", *argv, "--database", str(database))

    assert (status, out, err) == (1, "", refusal)
    assert stored.list_tokens() == ()


def test_a_token_for_a_disabled_user_says_it_authenticates_nothing_yet(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    stored.update_user("bob", disabled=True)

    status, _out, err = _run(capsys, "token", "create", "bob", "--database", str(database))

    assert status == 0
    assert err.splitlines()[-1] == (
        "vantage: bob is disabled, so the token authenticates nothing until they are enabled"
    )


def test_tokens_list_oldest_first_for_everyone_or_one_user(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    for argv in (["alice", "--label", "laptop"], ["bob"], ["alice", "--scope", "read"]):
        _run(capsys, "token", "create", *argv, "--database", str(database))
    first, _second, third = stored.list_tokens()
    stored.revoke_token(first.id, revoked_at=datetime.now(timezone.utc))

    _status, everyone, _err = _run(capsys, "token", "list", "--database", str(database))
    _status, alices, _err = _run(capsys, "token", "list", "alice", "--database", str(database))

    header, *rows = everyone.splitlines()
    assert header.split() == ["ID", "USER", "SCOPES", "CREATED", "REVOKED", "LABEL"]
    assert [row.split()[1] for row in rows] == ["alice", "bob", "alice"]
    assert rows[0].split()[2] == "read,record"
    assert rows[0].split()[-1] == "laptop"
    assert len(rows[0].split()) == 6 and len(rows[1].split()) == 4
    assert [row.split()[0] for row in alices.splitlines()[1:]] == [str(first.id), str(third.id)]


def test_revoking_a_token_stops_it_authenticating(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    _status, out, _err = _run(capsys, "token", "create", "bob", "--database", str(database))
    (created,) = stored.list_tokens()

    revoked = _run(capsys, "token", "revoke", str(created.id), "--database", str(database))
    again = _run(capsys, "token", "revoke", str(created.id), "--database", str(database))

    assert revoked == (0, "", f"vantage: revoked token {created.id}\n")
    assert again == (
        1,
        "",
        f"vantage: there is no token {created.id} to revoke, or it is revoked already\n",
    )
    assert stored.authenticate(token_digest(out.strip())) is None


@pytest.mark.parametrize("token_id", ["0", "-1", str(2**64)])
def test_revoking_an_id_no_token_can_have_is_one_line(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore, token_id: str
) -> None:
    status, _out, err = _run(capsys, "token", "revoke", "--database", str(database), "--", token_id)

    assert status == 1
    assert err == f"vantage: there is no token {token_id} to revoke, or it is revoked already\n"


# --- Projects -------------------------------------------------------------------------

_PROJECT_NAME_RULE = (
    "vantage: a project name is 1 to 64 characters of a-z, 0-9, '.', '_' and '-', "
    "starting with a letter or a digit\n"
)


def _project_names(database: Path) -> list[str]:
    store = SqliteExecutionStore(database)
    try:
        return [project.name for project in store.list_projects()]
    finally:
        store.close()


def test_adding_a_project_creates_a_missing_database_and_says_it_added_it(
    capsys: pytest.CaptureFixture[str], database: Path
) -> None:
    """Setting up a server's projects comes before serving it, as its users
    do."""
    status, out, err = _run(capsys, "project", "add", "firmware", "--database", str(database))

    assert (status, out, err) == (0, "", "vantage: added project firmware\n")
    assert _project_names(database) == [DEFAULT_PROJECT, "firmware"]


@pytest.mark.parametrize(
    ("name", "refusal"),
    [
        ("default", "vantage: there is already a project named default\n"),
        ("firmware", "vantage: there is already a project named firmware\n"),
        ("Firmware", _PROJECT_NAME_RULE),
        ("-firmware", _PROJECT_NAME_RULE),
        ("../firmware", _PROJECT_NAME_RULE),
        ("f" * 65, _PROJECT_NAME_RULE),
    ],
)
def test_a_project_that_cannot_be_added_is_one_line_and_changes_nothing(
    capsys: pytest.CaptureFixture[str], database: Path, name: str, refusal: str
) -> None:
    _run(capsys, "project", "add", "firmware", "--database", str(database))

    status, out, err = _run(capsys, "project", "add", "--database", str(database), "--", name)

    assert (status, out, err) == (1, "", refusal)
    assert _project_names(database) == [DEFAULT_PROJECT, "firmware"]


def test_projects_list_by_name_with_default_among_them(
    capsys: pytest.CaptureFixture[str], database: Path
) -> None:
    for name in ("zephyr", "boards", "e2e"):
        _run(capsys, "project", "add", name, "--database", str(database))

    status, out, err = _run(capsys, "project", "list", "--database", str(database))

    assert (status, err) == (0, "")
    header, *rows = out.splitlines()
    assert header.split() == ["NAME", "CREATED"]
    assert [row.split()[0] for row in rows] == ["boards", "default", "e2e", "zephyr"]
    assert all(len(row.split()) == 2 for row in rows)


def test_a_new_database_lists_default_alone(
    capsys: pytest.CaptureFixture[str], database: Path
) -> None:
    SqliteExecutionStore(database).close()

    status, out, _err = _run(capsys, "project", "list", "--database", str(database))

    assert status == 0
    assert [row.split()[0] for row in out.splitlines()] == ["NAME", DEFAULT_PROJECT]


def test_a_postgresql_database_is_managed_the_same_way(
    capsys: pytest.CaptureFixture[str], postgres_url: str
) -> None:
    added = _run(capsys, "user", "add", "alice", "--database", postgres_url)
    status, out, _err = _run(
        capsys, "token", "create", "alice", "--scope", "record", "--database", postgres_url
    )

    assert added[0] == 0
    assert status == 0
    from vantage.storage.postgres import PostgresExecutionStore

    store = PostgresExecutionStore(postgres_url)
    try:
        grant = store.authenticate(token_digest(out.strip()))
    finally:
        store.close()
    assert grant is not None and grant.user == "alice"


def test_a_postgresql_databases_projects_are_managed_the_same_way(
    capsys: pytest.CaptureFixture[str], postgres_url: str
) -> None:
    added = _run(capsys, "project", "add", "firmware", "--database", postgres_url)
    again = _run(capsys, "project", "add", "firmware", "--database", postgres_url)
    taken = _run(capsys, "project", "add", "default", "--database", postgres_url)
    status, out, _err = _run(capsys, "project", "list", "--database", postgres_url)

    assert added == (0, "", "vantage: added project firmware\n")
    assert again == (1, "", "vantage: there is already a project named firmware\n")
    assert taken == (1, "", "vantage: there is already a project named default\n")
    assert status == 0
    assert [row.split()[0] for row in out.splitlines()] == ["NAME", DEFAULT_PROJECT, "firmware"]


def test_managing_users_needs_no_server_extra(tmp_path: Path) -> None:
    """A database's users are managed where only `vantage`'s base install
    is: the command runs with FastAPI, Starlette and uvicorn unimportable."""
    program = (
        "import sys\n"
        "for name in ('fastapi', 'starlette', 'uvicorn'):\n"
        "    sys.modules[name] = None\n"
        "from vantage.service.cli import main\n"
        "main()\n"
    )
    database = tmp_path / "vantage.db"
    environment = {name: value for name, value in os.environ.items() if name != "VANTAGE_DATABASE"}

    def vantage(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 -- the interpreter running this test
            [sys.executable, "-c", program, *argv, "--database", str(database)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )

    added = vantage("user", "add", "alice")
    created = vantage("token", "create", "alice")
    project = vantage("project", "add", "firmware")
    listed = vantage("project", "list")

    assert (added.returncode, created.returncode) == (0, 0), added.stderr + created.stderr
    assert created.stdout.startswith("vantage_")
    assert (project.returncode, listed.returncode) == (0, 0), project.stderr + listed.stderr
    assert "firmware" in listed.stdout
