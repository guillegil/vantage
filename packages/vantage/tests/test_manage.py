"""`vantage user`, `vantage token` and `vantage project`: managing a
database's users, their tokens and its projects where the database is
(`service/manage.py`).

The commands run in process through `cli.main`, as the `vantage` entry
point runs them, and every result is read back through a store of its own,
as a server on the same database would read it. One test runs the command
where the web framework cannot be imported.

A password is typed at a stand-in for `getpass.getpass`, with stdin a
stand-in terminal, or piped in through a stand-in stdin. Every password is
hashed at a tiny cost (`password_fixtures`), a command's and those a test
stores itself alike.
"""

from __future__ import annotations

import getpass
import io
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

import pytest
from vantage.core.domain.access import (
    ADMIN_SCOPE,
    DEFAULT_SCOPES,
    LOGIN_TOKEN_LIFETIME,
    READ_SCOPE,
    RECORD_SCOPE,
    new_token,
    token_digest,
)
from vantage.core.domain.passwords import hash_password, verify_password
from vantage.core.domain.projects import DEFAULT_PROJECT
from vantage.core.ports.storage import ExecutionStore
from vantage.service import cli
from vantage.storage.sqlite_store import SqliteExecutionStore

# `postgres_url`, for the command against the real PostgreSQL adapter;
# `cheap_passwords`, for every password hashed here.
pytest_plugins = ["postgres_fixtures", "password_fixtures"]

pytestmark = pytest.mark.usefixtures("cheap_passwords")


_PASSWORD = "correct horse battery staple"


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
        "vantage token create alice, or vantage user password alice to log in\n"
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


@pytest.mark.parametrize(
    ("argv", "kind"),
    [
        (["user", "add"], "user"),
        (["user", "password", "--password-stdin"], "user"),
        (["project", "add"], "project"),
    ],
    ids=["user-add", "user-password", "project-add"],
)
def test_a_refused_name_creates_no_database(
    capsys: pytest.CaptureFixture[str], database: Path, argv: list[str], kind: str
) -> None:
    """The name is checked before the database is opened, so a refusal
    leaves nothing behind, not even an empty database."""
    status, out, err = _run(capsys, *argv, "Bad", "--database", str(database))

    assert (status, out) == (1, "")
    assert err.startswith(f"vantage: a {kind} name is 1 to 64 characters")
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


def test_users_list_in_name_order_saying_who_has_a_password(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    stored.update_user("bob", disabled=True)
    stored.set_password(
        "alice", password_hash=hash_password(_PASSWORD), changed_at=datetime.now(timezone.utc)
    )

    status, out, _err = _run(capsys, "user", "list", "--database", str(database))

    assert status == 0
    header, alice, bob = out.splitlines()
    assert header.split() == ["NAME", "ADMIN", "STATE", "PASSWORD", "CREATED"]
    assert alice.split()[:4] == ["alice", "yes", "enabled", "yes"]
    assert bob.split()[:4] == ["bob", "no", "disabled", "no"]


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
        ["user", "password", "alice", "--password-stdin"],
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
    header, row = out.splitlines()
    assert header.split() == ["NAME", "ADMIN", "STATE", "PASSWORD", "CREATED"]
    assert row.split()[:4] == ["bob", "yes", "disabled", "no"]
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
    grant = stored.authenticate(token_digest(token), now=datetime.now(timezone.utc))
    assert grant is not None and grant.scopes == {RECORD_SCOPE}
    assert token not in err


def test_a_token_holds_read_and_record_unless_told_otherwise(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    status, out, _err = _run(capsys, "token", "create", "alice", "--database", str(database))

    assert status == 0
    grant = stored.authenticate(token_digest(out.strip()), now=datetime.now(timezone.utc))
    assert grant is not None and grant.scopes == {READ_SCOPE, RECORD_SCOPE}


def test_an_admin_token_for_an_admin(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    status, out, _err = _run(
        capsys, "token", "create", "alice", "--scope", "admin", "--database", str(database)
    )

    grant = stored.authenticate(token_digest(out.strip()), now=datetime.now(timezone.utc))
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
    assert header.split() == ["ID", "USER", "SCOPES", "CREATED", "EXPIRES", "REVOKED", "LABEL"]
    assert [row.split()[1] for row in rows] == ["alice", "bob", "alice"]
    assert rows[0].split()[2] == "read,record"
    assert rows[0].split()[-1] == "laptop"
    assert len(rows[0].split()) == 6 and len(rows[1].split()) == 4
    assert [row.split()[0] for row in alices.splitlines()[1:]] == [str(first.id), str(third.id)]


def _column(table: str, name: str) -> list[str]:
    """The cells under the heading `name` of a table a command printed, one
    per row, blank ones included."""
    header, *rows = table.splitlines()
    start = header.index(name)
    stop = start + len(name)
    while stop < len(header) and header[stop] == " ":
        stop += 1
    return [row[start : stop if stop < len(header) else None].strip() for row in rows]


def test_a_login_token_is_listed_with_when_it_expires(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore
) -> None:
    """A made token never expires, and says nothing there."""
    created_at = datetime(2026, 9, 28, 8, 30, tzinfo=timezone.utc)
    _run(capsys, "token", "create", "alice", "--label", "laptop", "--database", str(database))
    alices = hash_password(_PASSWORD)
    stored.set_password("alice", password_hash=alices, changed_at=created_at)
    stored.create_login_token(
        "alice",
        password_hash=alices,
        digest=token_digest(new_token()),
        created_at=created_at,
        expires_at=created_at + LOGIN_TOKEN_LIFETIME,
    )

    status, out, _err = _run(capsys, "token", "list", "--database", str(database))

    assert status == 0
    assert _column(out, "EXPIRES") == ["", "2026-09-28T20:30:00Z"]
    assert _column(out, "LABEL") == ["laptop", "login"]
    assert _column(out, "SCOPES")[1] == "admin,read"


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
    assert stored.authenticate(token_digest(out.strip()), now=datetime.now(timezone.utc)) is None


@pytest.mark.parametrize("token_id", ["0", "-1", str(2**64)])
def test_revoking_an_id_no_token_can_have_is_one_line(
    capsys: pytest.CaptureFixture[str], database: Path, stored: ExecutionStore, token_id: str
) -> None:
    status, _out, err = _run(capsys, "token", "revoke", "--database", str(database), "--", token_id)

    assert status == 1
    assert err == f"vantage: there is no token {token_id} to revoke, or it is revoked already\n"


# --- Passwords ------------------------------------------------------------------------

_PASSWORD_RULE = "vantage: a password is 15 to 256 characters, with no control characters\n"
_SET_FOR_BOB = "vantage: set the password of bob; their login tokens are revoked\n"
_NOTHING_CHANGED = "vantage: nothing was changed\n"


class _Terminal(io.StringIO):
    """A stdin that is a terminal, for `getpass` to prompt on."""

    def isatty(self) -> bool:
        return True


def _at_the_terminal(monkeypatch: pytest.MonkeyPatch, *typed: str | BaseException) -> list[str]:
    """Make stdin a terminal at whose prompts `typed` is entered in turn, or
    raised -- `KeyboardInterrupt` for Ctrl-C, `EOFError` for the end of
    input -- and return the prompts it showed."""
    prompts: list[str] = []
    entries = iter(typed)

    def _getpass(prompt: str = "Password: ", stream: TextIO | None = None) -> str:
        prompts.append(prompt)
        entry = next(entries, None)
        if entry is None:
            raise AssertionError(f"prompted with {prompt!r} and nothing left to type")
        if isinstance(entry, BaseException):
            raise entry
        return entry

    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr(getpass, "getpass", _getpass)
    return prompts


def _piped(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Make stdin a pipe holding `text`; `getpass` must not be reached."""

    def _getpass(prompt: str = "Password: ", stream: TextIO | None = None) -> str:
        raise AssertionError(f"prompted with {prompt!r} on a pipe")

    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    monkeypatch.setattr(getpass, "getpass", _getpass)


def _set_password(
    capsys: pytest.CaptureFixture[str], database: Path, *argv: str
) -> tuple[int, str, str]:
    """`vantage user password *argv` on `database`: its exit status, stdout
    and stderr."""
    return _run(capsys, "user", "password", *argv, "--database", str(database))


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"], ids=["none", "lf", "crlf"])
def test_a_password_piped_in_is_set_less_one_trailing_newline(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
) -> None:
    """`echo` and a file end it with a newline, one written on Windows with
    CR LF; neither is part of the password. It is printed nowhere."""
    _piped(monkeypatch, f"{_PASSWORD}{ending}")

    status, out, err = _set_password(capsys, database, "bob", "--password-stdin")

    assert (status, out, err) == (0, "", _SET_FOR_BOB)
    assert verify_password(_PASSWORD, stored.get_password_hash("bob"))
    bob = stored.get_user("bob")
    assert bob is not None and bob.has_password


@pytest.mark.parametrize("ending", ["\n\n", "\r\n\n", "\r"], ids=["two-lf", "crlf-lf", "cr"])
def test_only_one_trailing_newline_is_taken_off_and_the_rule_refuses_the_rest(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
) -> None:
    """Whatever else is left is a control character, which the rule refuses
    rather than the command trimming it: a password is never other than
    what was given."""
    _piped(monkeypatch, f"{_PASSWORD}{ending}")

    status, out, err = _set_password(capsys, database, "bob", "--password-stdin")

    assert (status, out, err) == (1, "", _PASSWORD_RULE)
    assert stored.get_password_hash("bob") is None


def test_a_piped_password_that_is_not_text_is_one_line(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xff" * 20), encoding="utf-8"))

    status, out, err = _set_password(capsys, database, "bob", "--password-stdin")

    assert (status, out) == (1, "")
    assert err == "vantage: the password on stdin is not text in this locale's encoding\n"
    assert stored.get_password_hash("bob") is None


def test_a_password_is_asked_for_twice_on_the_terminal(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts = _at_the_terminal(monkeypatch, _PASSWORD, _PASSWORD)

    status, out, err = _set_password(capsys, database, "bob")

    assert (status, out, err) == (0, "", _SET_FOR_BOB)
    assert prompts == ["New password for bob: ", "Again: "]
    assert verify_password(_PASSWORD, stored.get_password_hash("bob"))


def test_two_different_passwords_at_the_prompts_change_nothing(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _at_the_terminal(monkeypatch, _PASSWORD, f"{_PASSWORD}!")

    status, out, err = _set_password(capsys, database, "bob")

    assert (status, out) == (1, "")
    assert err == "vantage: the passwords do not match; nothing was changed\n"
    assert stored.get_password_hash("bob") is None


@pytest.mark.parametrize("typed", [(), (_PASSWORD,)], ids=["first-prompt", "second-prompt"])
def test_ctrl_c_at_a_prompt_changes_nothing_and_exits_as_a_shell_reports_it(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
    typed: tuple[str, ...],
) -> None:
    """On a line of its own, since the prompt's was never ended."""
    _at_the_terminal(monkeypatch, *typed, KeyboardInterrupt())

    status, out, err = _set_password(capsys, database, "bob")

    assert (status, out, err) == (130, "", f"\n{_NOTHING_CHANGED}")
    assert stored.get_password_hash("bob") is None


def test_a_password_piped_into_no_stdin_at_all_is_one_line(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With file descriptor 0 closed there is no `sys.stdin` to read."""
    _piped(monkeypatch, "")
    monkeypatch.setattr(sys, "stdin", None)

    status, out, err = _set_password(capsys, database, "bob", "--password-stdin")

    assert (status, out, err) == (1, "", "vantage: there is no stdin to read the password from\n")
    assert stored.get_password_hash("bob") is None


def test_the_end_of_input_at_a_prompt_changes_nothing(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _at_the_terminal(monkeypatch, _PASSWORD, EOFError())

    status, out, err = _set_password(capsys, database, "bob")

    assert (status, out, err) == (1, "", f"\n{_NOTHING_CHANGED}")
    assert stored.get_password_hash("bob") is None


@pytest.mark.parametrize("stdin", [io.StringIO(_PASSWORD), None], ids=["pipe", "none"])
def test_without_a_terminal_a_password_must_be_piped_in(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
    stdin: TextIO | None,
) -> None:
    """With stdin a pipe, `getpass` would read the terminal behind it, or,
    with none, echo the password as it is typed."""
    _piped(monkeypatch, "")
    monkeypatch.setattr(sys, "stdin", stdin)

    status, out, err = _set_password(capsys, database, "bob")

    assert (status, out) == (1, "")
    assert err == (
        "vantage: there is no terminal to ask for the password on: "
        "pipe it in with --password-stdin\n"
    )
    assert stored.get_password_hash("bob") is None


@pytest.mark.parametrize(
    "password",
    ["fourteen chars", "x" * 257, f"{_PASSWORD}\t", f"{_PASSWORD}\x7f", f"{_PASSWORD}\x85"],
    ids=["too-short", "too-long", "tab", "delete", "c1-control"],
)
def test_a_password_the_rule_refuses_is_one_line_saying_the_rule(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
    password: str,
) -> None:
    """The line states the rule, never the password."""
    _at_the_terminal(monkeypatch, password, password)

    status, out, err = _set_password(capsys, database, "bob")

    assert (status, out, err) == (1, "", _PASSWORD_RULE)
    assert stored.get_password_hash("bob") is None


def test_the_password_of_a_user_nobody_has_is_never_asked_for(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts = _at_the_terminal(monkeypatch)

    status, out, err = _set_password(capsys, database, "carol")

    assert (status, out, err) == (1, "", "vantage: there is no user named carol\n")
    assert prompts == []


def test_a_name_nobody_can_have_is_refused_by_the_rule_before_any_prompt(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts = _at_the_terminal(monkeypatch)

    status, out, err = _set_password(capsys, database, "Alice")

    assert (status, out) == (1, "")
    assert err.startswith("vantage: a user name is 1 to 64 characters")
    assert prompts == []


def test_setting_a_password_revokes_that_users_login_tokens_and_no_other_token(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A login token was opened with the password being replaced; a made
    token, for CI say, has nothing to do with it."""
    now = datetime.now(timezone.utc)
    tokens: dict[str, str] = {}
    for name in ("alice", "bob"):
        before = hash_password(f"{name}'s password before this one")
        stored.set_password(name, password_hash=before, changed_at=now)
        tokens[f"{name} login"] = new_token()
        stored.create_login_token(
            name,
            password_hash=before,
            digest=token_digest(tokens[f"{name} login"]),
            created_at=now,
            expires_at=now + LOGIN_TOKEN_LIFETIME,
        )
    tokens["bob made"] = new_token()
    stored.create_token(
        "bob",
        digest=token_digest(tokens["bob made"]),
        label="ci",
        scopes=DEFAULT_SCOPES,
        created_at=now,
    )
    _piped(monkeypatch, _PASSWORD)

    status, _out, err = _set_password(capsys, database, "bob", "--password-stdin")

    assert (status, err) == (0, _SET_FOR_BOB)
    authenticating = {
        held
        for held, secret in tokens.items()
        if stored.authenticate(token_digest(secret), now=now) is not None
    }
    assert authenticating == {"alice login", "bob made"}


def test_a_disabled_users_password_is_set_saying_they_cannot_log_in_yet(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored.update_user("bob", disabled=True)
    _piped(monkeypatch, _PASSWORD)

    status, out, err = _set_password(capsys, database, "bob", "--password-stdin")

    assert (status, out) == (0, "")
    assert err == (
        f"{_SET_FOR_BOB}"
        "vantage: bob is disabled, so they cannot log in until enabled: "
        "vantage user update bob --enable\n"
    )
    stored.update_user("bob", disabled=False)
    assert verify_password(_PASSWORD, stored.get_password_hash("bob"))


@pytest.mark.parametrize(
    "option",
    [["--password", _PASSWORD], [f"--password={_PASSWORD}"], ["--password"], ["--password-std"]],
    ids=["apart", "joined", "alone", "abbreviated"],
)
def test_a_password_is_never_taken_from_the_command_line(
    capsys: pytest.CaptureFixture[str],
    database: Path,
    stored: ExecutionStore,
    monkeypatch: pytest.MonkeyPatch,
    option: list[str],
) -> None:
    """The command line shows in `ps` and in shell history: there is no
    option to give a password with, so argparse refuses one -- nor is one
    that looks like it taken for an abbreviated `--password-stdin`, which
    would read a password where none was meant to be given."""
    _piped(monkeypatch, f"{_PASSWORD}\n")

    status, out, _err = _set_password(capsys, database, "bob", *option)

    assert (status, out) == (2, "")
    assert stored.get_password_hash("bob") is None


def test_a_postgresql_users_password_is_set_the_same_way(
    capsys: pytest.CaptureFixture[str], postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(capsys, "user", "add", "alice", "--database", postgres_url)
    _piped(monkeypatch, f"{_PASSWORD}\n")

    status, out, err = _run(
        capsys, "user", "password", "alice", "--password-stdin", "--database", postgres_url
    )

    assert (status, out) == (0, "")
    assert err == "vantage: set the password of alice; their login tokens are revoked\n"
    from vantage.storage.postgres import PostgresExecutionStore

    store = PostgresExecutionStore(postgres_url)
    try:
        assert verify_password(_PASSWORD, store.get_password_hash("alice"))
    finally:
        store.close()


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
        grant = store.authenticate(token_digest(out.strip()), now=datetime.now(timezone.utc))
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

    def vantage(*argv: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 -- the interpreter running this test
            [sys.executable, "-c", program, *argv, "--database", str(database)],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=environment,
        )

    added = vantage("user", "add", "alice")
    created = vantage("token", "create", "alice")
    password = vantage("user", "password", "alice", "--password-stdin", stdin=f"{_PASSWORD}\n")
    project = vantage("project", "add", "firmware")
    listed = vantage("project", "list")

    assert (added.returncode, created.returncode) == (0, 0), added.stderr + created.stderr
    assert created.stdout.startswith("vantage_")
    assert password.returncode == 0, password.stderr
    assert (project.returncode, listed.returncode) == (0, 0), project.stderr + listed.stderr
    assert "firmware" in listed.stdout
