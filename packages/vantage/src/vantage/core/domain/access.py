"""Users and their API tokens: the scopes a token can hold, the rules a
user name and a token follow, and the values the store hands back.

A token is shown once, when it is made, and never stored: the store keeps
its SHA-256 (`token_digest`) and finds a token by it. A token carries 32
random bytes, so there is nothing to guess and a fast hash is enough; and
since nobody can choose the text of a digest, looking one up needs no
constant-time comparison.

A user is disabled, never deleted, so a run always names an existing user
as the one who recorded it, and a server that has had a user stays closed.
A database the server made has a user from its first start: `vantage`
gives it the admin `admin` before serving it.

A user who has a password (`core/domain/passwords.py`) can trade it for a
login token -- holding read and manage, and admin for an admin, never
record -- which expires after `LOGIN_TOKEN_LIFETIME` and is revoked
whenever that user's password is set. Every other token is made by an
admin or on the command line, never expires, and survives password
changes.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

READ_SCOPE = "read"
"""Read runs, results, history, section definitions and members, in the
projects the user may read."""

RECORD_SCOPE = "record"
"""Send session reports and heartbeats: what the plugin needs."""

MANAGE_SCOPE = "manage"
"""Change what one project shares: its section definitions, for an editor
of it, and its members, for an owner. Any user's token may hold it; the
user's role in the project bounds it."""

ADMIN_SCOPE = "admin"
"""Manage who may use the server: users, tokens and projects. Only a token
of an admin user can hold it, and it grants nothing once its user stops
being one."""

SCOPES = frozenset({READ_SCOPE, RECORD_SCOPE, MANAGE_SCOPE, ADMIN_SCOPE})

DEFAULT_SCOPES = frozenset({READ_SCOPE, RECORD_SCOPE})
"""What a token holds when its maker names no scope: everything but
administration, which is asked for by name."""

USER_NAME_PATTERN = r"\A[a-z0-9][a-z0-9._-]{0,63}\Z"
"""Lower case, so two names never differ by case alone, and nothing that
needs quoting on a command line or in a URL path."""

_USER_NAME_RE = re.compile(USER_NAME_PATTERN)

MAX_TOKEN_LABEL_CHARS = 200

TOKEN_PREFIX = "vantage_"  # noqa: S105
"""Makes a token recognisable where it should not be, such as in a
committed file a secret scanner reads."""

LOGIN_TOKEN_LABEL = "login"  # noqa: S105
"""The label of every token a login hands out."""

LOGIN_TOKEN_LIFETIME = timedelta(hours=12)
"""How long a login token authenticates. There is no refresh: log in again."""

_TOKEN_RE = re.compile(r"\A[\x21-\x7e]{1,512}\Z")


class InvalidUserNameError(ValueError):
    """A user name that `USER_NAME_PATTERN` does not match."""


class InvalidTokenLabelError(ValueError):
    """A token label that is too long or holds a control character."""


class InvalidScopesError(ValueError):
    """No scope, or one that `SCOPES` does not hold."""


def check_user_name(name: str) -> str:
    """`name`, if it can name a user."""
    if not _USER_NAME_RE.match(name):
        raise InvalidUserNameError(
            "a user name is 1 to 64 characters of a-z, 0-9, '.', '_' and '-', "
            "starting with a letter or a digit"
        )
    return name


def plain_character(character: str) -> bool:
    """Neither a control character, which would break the one line a token
    is listed on or hide in a password, nor a lone surrogate, which no store
    can encode -- the command line hands one over for a byte its locale
    cannot decode."""
    point = ord(character)
    return not (point < 0x20 or 0x7F <= point < 0xA0 or 0xD800 <= point <= 0xDFFF)


def check_token_label(label: str) -> str:
    """`label`, if it can label a token: at most `MAX_TOKEN_LABEL_CHARS`
    characters, none of them a control character or a lone surrogate."""
    if len(label) > MAX_TOKEN_LABEL_CHARS or not all(map(plain_character, label)):
        raise InvalidTokenLabelError(
            f"a token label is at most {MAX_TOKEN_LABEL_CHARS} characters, "
            "with no control characters"
        )
    return label


def check_scopes(scopes: Iterable[str]) -> frozenset[str]:
    """`scopes` as a set, if it names at least one scope and no unknown one."""
    held = frozenset(scopes)
    if not held or not held <= SCOPES:
        raise InvalidScopesError(f"a token holds one or more of {', '.join(sorted(SCOPES))}")
    return held


def new_token() -> str:
    """A fresh token: `TOKEN_PREFIX` and 32 random bytes, URL-safe."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def well_formed_token(text: str) -> bool:
    """Whether `text` could be a token at all: printable ASCII with no
    space, at most 512 characters. Anything else can never match one, so
    it is refused without asking the store."""
    return _TOKEN_RE.match(text) is not None


def token_digest(token: str) -> str:
    """What the store keeps of a token: its SHA-256, in hex."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class User:
    """One user of the server. `admin` users may hold the admin scope and
    act as an owner of every project; a `disabled` one's tokens
    authenticate nothing. `has_password` says
    whether the user can log in; the password's hash never leaves the
    store but through `get_password_hash`."""

    name: str
    admin: bool
    disabled: bool
    created_at: datetime
    has_password: bool = False


@dataclass(frozen=True, slots=True)
class Token:
    """One token, without the token itself, which is never stored. A
    revoked token keeps its row, with the time it was revoked. Only a login
    token has an `expires_at`; its row is deleted once it has expired, at
    its user's next login, so the lists of tokens stay short."""

    id: int
    user: str
    label: str
    scopes: frozenset[str]
    created_at: datetime
    revoked_at: datetime | None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Grant:
    """What a live token of an enabled user lets its bearer do: act as
    `user`, within `scopes`. `admin` is the user's standing now, not when
    the token was made."""

    user: str
    admin: bool
    scopes: frozenset[str]

    def allows(self, scope: str) -> bool:
        """Whether this grant covers `scope`. The admin scope needs an
        admin user as well as a token holding it."""
        return scope in self.scopes and (scope != ADMIN_SCOPE or self.admin)


__all__ = [
    "ADMIN_SCOPE",
    "DEFAULT_SCOPES",
    "MAX_TOKEN_LABEL_CHARS",
    "READ_SCOPE",
    "RECORD_SCOPE",
    "SCOPES",
    "TOKEN_PREFIX",
    "USER_NAME_PATTERN",
    "Grant",
    "InvalidScopesError",
    "InvalidTokenLabelError",
    "InvalidUserNameError",
    "Token",
    "User",
    "check_scopes",
    "check_token_label",
    "check_user_name",
    "new_token",
    "token_digest",
    "well_formed_token",
]
