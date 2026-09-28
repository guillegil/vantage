"""`vantage user`, `vantage token` and `vantage project` -- manage the users
of a database, their tokens and its projects, where the database is,
without a server.

**The database is found as the server finds it**: `--database`, then
`VANTAGE_DATABASE`, then the default path, SQLite or PostgreSQL alike, and
it is opened the way `vantage` opens it, so every refusal is the same one
line. A server may be serving it meanwhile: the first user added closes that
server at its next request (`service/access.py`).

**Only `user add` and `project add` create a database.** Setting up a
server's users and projects comes before serving it, so adding one to a
SQLite path nothing holds yet creates the database there. Every other
command needs one to exist, and answers a missing SQLite file in one line
rather than leaving an empty database behind.

**A token is printed once, alone, on stdout**, so `$(vantage token create
...)` captures it and nothing else; what the command did goes to stderr.
Only its digest is stored, so it can never be shown again.

**A password is never an argument.** `vantage user password` asks for it
twice on the terminal, or reads it from stdin with `--password-stdin`,
never from an option or the environment, which `ps` and shell history
would show. It is never printed, and whoever can open the database may set
anyone's: it is how a lost admin password is recovered.

Nothing here imports FastAPI or uvicorn: a database's users are managed
where the `server` extra is not installed.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import NoReturn

from vantage.core.config.database import DatabaseTarget, SqliteTarget
from vantage.core.config.resolution import ServerConfigError, resolve_database
from vantage.core.domain.access import (
    ADMIN_SCOPE,
    DEFAULT_SCOPES,
    SCOPES,
    InvalidTokenLabelError,
    InvalidUserNameError,
    Token,
    User,
    check_scopes,
    check_token_label,
    check_user_name,
    new_token,
    token_digest,
)
from vantage.core.domain.passwords import InvalidPasswordError, check_password, hash_password
from vantage.core.domain.projects import InvalidProjectNameError, check_project_name
from vantage.core.ports.storage import (
    ExecutionStore,
    ProjectExistsError,
    UnknownUserError,
    UserExistsError,
)

# The exit status of a command Ctrl-C stopped, as a shell reports it.
_INTERRUPTED = 130

_USER_HEADER = ["NAME", "ADMIN", "STATE", "PASSWORD", "CREATED"]


def _refuse(message: str) -> NoReturn:
    print(f"vantage: {message}", file=sys.stderr)
    raise SystemExit(1)


def _say(message: str) -> None:
    print(f"vantage: {message}", file=sys.stderr)


def _add_database_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        default=None,
        help=(
            "Path to the SQLite database file, or a postgresql:// URL "
            "(default: VANTAGE_DATABASE, then the one vantage serves without options)."
        ),
    )


def _open(database: str | None, *, create: bool = False) -> ExecutionStore:
    """The store `database` names, resolved and opened as `vantage` opens
    it, or a one-line refusal."""
    # Imported here, not at the top: `cli` hands `vantage user` and
    # `vantage token` to this module from inside `main`.
    from vantage.service import cli

    try:
        target: DatabaseTarget = resolve_database(
            cli_database=database,
            env_database=os.environ.get("VANTAGE_DATABASE"),
            home=cli.home_directory(),
            xdg_data_home=os.environ.get("XDG_DATA_HOME"),
        )
    except ServerConfigError as exc:
        _refuse(str(exc))
    if isinstance(target, SqliteTarget) and not create and not target.path.exists():
        _refuse(f"there is no database at {target.path}")
    return cli.open_store(target)


def _instant(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(row[column]) for row in (header, *rows)) for column in range(len(header))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in (header, *rows)
    )


def _user_row(user: User) -> list[str]:
    return [
        user.name,
        "yes" if user.admin else "no",
        "disabled" if user.disabled else "enabled",
        "yes" if user.has_password else "no",
        _instant(user.created_at),
    ]


def _token_row(token: Token) -> list[str]:
    return [
        str(token.id),
        token.user,
        ",".join(sorted(token.scopes)),
        _instant(token.created_at),
        "" if token.expires_at is None else _instant(token.expires_at),
        "" if token.revoked_at is None else _instant(token.revoked_at),
        token.label,
    ]


def _user_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vantage user",
        description=(
            "Manage the users of a vantage database. Once it has one, every server on it "
            "requires a token. A server gives a database with no user the user admin before "
            "serving it, unless pytest-vantage's local store made it; such a database needs no "
            "token until it has a user."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    add = commands.add_parser("add", help="Add an enabled user.")
    add.add_argument("name", help="1 to 64 characters of a-z, 0-9, '.', '_' and '-'.")
    add.add_argument(
        "--admin",
        action="store_true",
        help="May hold the admin scope, to change sections and manage users and tokens.",
    )
    _add_database_option(add)
    listing = commands.add_parser("list", help="List every user.")
    _add_database_option(listing)
    update = commands.add_parser("update", help="Change whether a user is an admin, or enabled.")
    update.add_argument("name")
    admin = update.add_mutually_exclusive_group()
    admin.add_argument("--admin", dest="admin", action="store_const", const=True, default=None)
    admin.add_argument("--no-admin", dest="admin", action="store_const", const=False)
    disabled = update.add_mutually_exclusive_group()
    disabled.add_argument(
        "--disable",
        dest="disabled",
        action="store_const",
        const=True,
        default=None,
        help="Its tokens authenticate nothing until it is enabled again.",
    )
    disabled.add_argument("--enable", dest="disabled", action="store_const", const=False)
    _add_database_option(update)
    # No abbreviations here: `--password` must never be taken for
    # `--password-stdin`, nor look like an option that takes a password.
    password = commands.add_parser(
        "password",
        allow_abbrev=False,
        help=(
            "Set a user's password, to log in with, asking for it twice on the terminal; "
            "their login tokens are revoked."
        ),
    )
    password.add_argument("name")
    password.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin instead, less one trailing newline.",
    )
    _add_database_option(password)
    return parser


def _token_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vantage token",
        description="Manage the tokens a vantage database's users authenticate with.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    create = commands.add_parser(
        "create", help="Create a token for a user and print it, once, on stdout."
    )
    create.add_argument("user")
    create.add_argument(
        "--scope",
        dest="scopes",
        action="append",
        choices=sorted(SCOPES),
        help=(
            "A scope the token holds; repeat for more. read: read runs; record: send "
            "reports, what pytest-vantage needs; admin: change sections and manage users "
            "and tokens, for an admin user "
            f"(default: {' and '.join(sorted(DEFAULT_SCOPES))})."
        ),
    )
    create.add_argument("--label", default="", help="What the token is for, to tell it apart.")
    _add_database_option(create)
    listing = commands.add_parser("list", help="List tokens, revoked ones included.")
    listing.add_argument("user", nargs="?", help="Only this user's tokens.")
    _add_database_option(listing)
    revoke = commands.add_parser("revoke", help="Revoke a token by the id `list` shows.")
    revoke.add_argument("id", type=int)
    _add_database_option(revoke)
    return parser


def _project_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vantage project",
        description=(
            "Manage the projects of a vantage database. Every run belongs to one; a run that "
            "names none belongs to default, which every database has."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    add = commands.add_parser("add", help="Add a project, for runs to name.")
    add.add_argument("name", help="1 to 64 characters of a-z, 0-9, '.', '_' and '-'.")
    _add_database_option(add)
    listing = commands.add_parser("list", help="List every project.")
    _add_database_option(listing)
    return parser


def project(argv: Sequence[str]) -> int:
    """`vantage project ...`: 0 when done, 1 with one line on stderr when
    it could not be. Nothing about a project changes once it is made."""
    args = _project_parser().parse_args(list(argv))
    if args.command == "add":
        # Before the database is opened: a refused name creates nothing.
        try:
            check_project_name(args.name)
        except InvalidProjectNameError as exc:
            _refuse(str(exc))
    store = _open(args.database, create=args.command == "add")
    try:
        if args.command == "add":
            try:
                store.create_project(args.name, created_at=datetime.now(timezone.utc))
            except ProjectExistsError:
                _refuse(f"there is already a project named {args.name}")
            _say(f"added project {args.name}")
        else:
            rows = [[found.name, _instant(found.created_at)] for found in store.list_projects()]
            print(_table(["NAME", "CREATED"], rows))
    finally:
        with contextlib.suppress(Exception):
            store.close()
    return 0


def user(argv: Sequence[str]) -> int:
    """`vantage user ...`: 0 when done, 1 with one line on stderr when it
    could not be."""
    args = _user_parser().parse_args(list(argv))
    if args.command in ("add", "password"):
        # Before the database is opened: a refused name creates nothing,
        # and nobody types a password only to have the name refused.
        try:
            check_user_name(args.name)
        except InvalidUserNameError as exc:
            _refuse(str(exc))
    store = _open(args.database, create=args.command == "add")
    try:
        if args.command == "add":
            _add_user(store, args.name, admin=args.admin)
        elif args.command == "list":
            users = store.list_users()
            if users:
                print(_table(_USER_HEADER, list(map(_user_row, users))))
        elif args.command == "password":
            return _set_password(store, args.name, from_stdin=args.password_stdin)
        else:
            _update_user(store, args.name, admin=args.admin, disabled=args.disabled)
    finally:
        with contextlib.suppress(Exception):
            store.close()
    return 0


def _add_user(store: ExecutionStore, name: str, *, admin: bool) -> None:
    first = not store.access_required()
    try:
        store.create_user(name, admin=admin, created_at=datetime.now(timezone.utc))
    except UserExistsError:
        _refuse(f"there is already a user named {name}")
    _say(f"added {'admin ' if admin else ''}user {name}")
    if first:
        _say(
            "this database has a user now, so every server on it requires a token: "
            f"vantage token create {name}, or vantage user password {name} to log in"
        )


def _update_user(
    store: ExecutionStore, name: str, *, admin: bool | None, disabled: bool | None
) -> None:
    if admin is None and disabled is None:
        _refuse("say what to change: --admin, --no-admin, --disable or --enable")
    updated = store.update_user(name, admin=admin, disabled=disabled)
    if updated is None:
        _refuse(f"there is no user named {name}")
    print(_table(_USER_HEADER, [_user_row(updated)]))


def _set_password(store: ExecutionStore, name: str, *, from_stdin: bool) -> int:
    """Set `name`'s password: 0 when done, 130 when Ctrl-C stopped it at a
    prompt, 1 with one line on stderr when it could not be."""
    found = store.get_user(name)
    if found is None:
        _refuse(f"there is no user named {name}")
    try:
        password = _read_stdin_password() if from_stdin else _ask_password(name)
    except KeyboardInterrupt:
        # Opens a line of its own: the prompt's line was never ended.
        print("\nvantage: nothing was changed", file=sys.stderr)
        return _INTERRUPTED
    try:
        check_password(password)
    except InvalidPasswordError as exc:
        _refuse(str(exc))
    if not store.set_password(
        name, password_hash=hash_password(password), changed_at=datetime.now(timezone.utc)
    ):
        _refuse(f"there is no user named {name}")
    _say(f"set the password of {name}; their login tokens are revoked")
    if found.disabled:
        _say(
            f"{name} is disabled, so they cannot log in until enabled: "
            f"vantage user update {name} --enable"
        )
    return 0


def _read_stdin_password() -> str:
    """All of stdin less one trailing newline. Anything else it holds -- a
    second line, a carriage return -- is a control character, which the
    rule refuses rather than this trimming it."""
    if sys.stdin is None:
        _refuse("there is no stdin to read the password from")
    try:
        text = sys.stdin.read()
    except UnicodeDecodeError:
        _refuse("the password on stdin is not text in this locale's encoding")
    if text.endswith("\r\n"):
        return text[:-2]
    return text.removesuffix("\n")


def _ask_password(name: str) -> str:
    """The password, typed twice on the terminal. Refused without one: with
    stdin a pipe, `getpass` would read the terminal behind it, or echo the
    password where there is none."""
    if sys.stdin is None or not sys.stdin.isatty():
        _refuse("there is no terminal to ask for the password on: pipe it in with --password-stdin")
    try:
        password = getpass.getpass(f"New password for {name}: ")
        again = getpass.getpass("Again: ")
    except EOFError:
        print(file=sys.stderr)
        _refuse("nothing was changed")
    if password != again:
        _refuse("the passwords do not match; nothing was changed")
    return password


def token(argv: Sequence[str]) -> int:
    """`vantage token ...`: 0 when done, 1 with one line on stderr when it
    could not be."""
    args = _token_parser().parse_args(list(argv))
    store = _open(args.database)
    try:
        if args.command == "create":
            _create_token(store, args.user, scopes=args.scopes, label=args.label)
        elif args.command == "list":
            tokens = store.list_tokens(user=args.user)
            if tokens:
                header = ["ID", "USER", "SCOPES", "CREATED", "EXPIRES", "REVOKED", "LABEL"]
                print(_table(header, list(map(_token_row, tokens))))
        elif not store.revoke_token(args.id, revoked_at=datetime.now(timezone.utc)):
            _refuse(f"there is no token {args.id} to revoke, or it is revoked already")
        else:
            _say(f"revoked token {args.id}")
    finally:
        with contextlib.suppress(Exception):
            store.close()
    return 0


def _create_token(
    store: ExecutionStore, name: str, *, scopes: list[str] | None, label: str
) -> None:
    held = check_scopes(scopes or DEFAULT_SCOPES)
    try:
        check_token_label(label)
    except InvalidTokenLabelError as exc:
        _refuse(str(exc))
    owner = store.get_user(name)
    if owner is None:
        _refuse(f"there is no user named {name}")
    if ADMIN_SCOPE in held and not owner.admin:
        _refuse(f"{name} is not an admin, so the admin scope would grant nothing")
    secret = new_token()
    try:
        created = store.create_token(
            name,
            digest=token_digest(secret),
            label=label,
            scopes=held,
            created_at=datetime.now(timezone.utc),
        )
    except UnknownUserError:
        _refuse(f"there is no user named {name}")
    print(secret)
    _say(
        f"created token {created.id} of {name} with {', '.join(sorted(held))}; "
        "it is shown this once"
    )
    if owner.disabled:
        _say(f"{name} is disabled, so the token authenticates nothing until they are enabled")


__all__ = ["project", "token", "user"]
