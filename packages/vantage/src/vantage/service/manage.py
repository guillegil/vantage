"""`vantage user` and `vantage token` -- manage the users of a database and
their tokens, where the database is, without a server.

**The database is found as the server finds it**: `--database`, then
`VANTAGE_DATABASE`, then the default path, SQLite or PostgreSQL alike, and
it is opened the way `vantage` opens it, so every refusal is the same one
line. A server may be serving it meanwhile: the first user added closes that
server at its next request (`service/access.py`).

**Only `user add` creates a database.** Setting up a server's users comes
before serving it, so adding the first user to a SQLite path nothing holds
yet creates the database there. Every other command needs one to exist,
and answers a missing SQLite file in one line rather than leaving an empty
database behind.

**A token is printed once, alone, on stdout**, so `$(vantage token create
...)` captures it and nothing else; what the command did goes to stderr.
Only its digest is stored, so it can never be shown again.

Nothing here imports FastAPI or uvicorn: a database's users are managed
where the `server` extra is not installed.
"""

from __future__ import annotations

import argparse
import contextlib
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
from vantage.core.ports.storage import ExecutionStore, UnknownUserError, UserExistsError


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
        _instant(user.created_at),
    ]


def _token_row(token: Token) -> list[str]:
    return [
        str(token.id),
        token.user,
        ",".join(sorted(token.scopes)),
        _instant(token.created_at),
        "" if token.revoked_at is None else _instant(token.revoked_at),
        token.label,
    ]


def _user_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vantage user",
        description=(
            "Manage the users of a vantage database. Once it has one, every server on it "
            "requires a token."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    add = commands.add_parser("add", help="Add an enabled user.")
    add.add_argument("name", help="1 to 64 characters of a-z, 0-9, '.', '_' and '-'.")
    add.add_argument(
        "--admin", action="store_true", help="May hold the admin scope, to change sections."
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
            "reports, what pytest-vantage needs; admin: change sections, for an admin "
            f"user (default: {' and '.join(sorted(DEFAULT_SCOPES))})."
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


def user(argv: Sequence[str]) -> int:
    """`vantage user ...`: 0 when done, 1 with one line on stderr when it
    could not be."""
    args = _user_parser().parse_args(list(argv))
    store = _open(args.database, create=args.command == "add")
    try:
        if args.command == "add":
            _add_user(store, args.name, admin=args.admin)
        elif args.command == "list":
            users = store.list_users()
            if users:
                print(_table(["NAME", "ADMIN", "STATE", "CREATED"], list(map(_user_row, users))))
        else:
            _update_user(store, args.name, admin=args.admin, disabled=args.disabled)
    finally:
        with contextlib.suppress(Exception):
            store.close()
    return 0


def _add_user(store: ExecutionStore, name: str, *, admin: bool) -> None:
    try:
        check_user_name(name)
    except InvalidUserNameError as exc:
        _refuse(str(exc))
    first = not store.access_required()
    try:
        store.create_user(name, admin=admin, created_at=datetime.now(timezone.utc))
    except UserExistsError:
        _refuse(f"there is already a user named {name}")
    _say(f"added {'admin ' if admin else ''}user {name}")
    if first:
        _say(
            "this database has a user now, so every server on it requires a token: "
            f"vantage token create {name}"
        )


def _update_user(
    store: ExecutionStore, name: str, *, admin: bool | None, disabled: bool | None
) -> None:
    if admin is None and disabled is None:
        _refuse("say what to change: --admin, --no-admin, --disable or --enable")
    updated = store.update_user(name, admin=admin, disabled=disabled)
    if updated is None:
        _refuse(f"there is no user named {name}")
    print(_table(["NAME", "ADMIN", "STATE", "CREATED"], [_user_row(updated)]))


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
                header = ["ID", "USER", "SCOPES", "CREATED", "REVOKED", "LABEL"]
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


__all__ = ["token", "user"]
