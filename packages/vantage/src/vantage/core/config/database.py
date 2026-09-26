"""Which database the server uses -- a SQLite file or a PostgreSQL server --
and how a PostgreSQL URL is shown without its password.

Pure, like the rest of `vantage.core.config`: nothing here opens, resolves
or connects to anything. `urllib` is out of the core's reach, and libpq
reads a URL more loosely than `urllib` would anyway, so the URL is taken
apart by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

_POSTGRES_SCHEMES = frozenset({"postgresql", "postgres"})
# An RFC 3986 scheme followed by `//`: a URL, never a file path.
_URL_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://")
_MASK = "***"

# The libpq connection parameters that carry a secret: the user's password,
# the client key's passphrase, and an OAuth client secret.
_SECRET_PARAMETERS = frozenset({"password", "sslpassword", "oauth_client_secret"})

# One `key=value` parameter of a URL's query string. The key stops at `=`
# or `&`; the value runs on over every following `&` piece with no `=` in
# it, which libpq would reject as a parameter of its own, quoting it: a
# password left unencoded may hold `=`, `?`, `#`, `@` or `&`, and all of
# it has to be hidden.
_QUERY_PARAMETER = re.compile(r"[?&](?P<key>[^=&]*)=(?P<value>[^&]*(?:&[^=&]*(?=&|\Z))*)")
_PERCENT_ESCAPE = re.compile(rb"%([0-9A-Fa-f]{2})")

# What makes libpq split a password into other fields -- host, port,
# database, parameters -- that it may quote one at a time, each cut at any
# of `_URL_DELIMITERS`: an unencoded `@` or `/` in the user's, and an
# unencoded `&` in a query parameter's.
_SPLITS_THE_USER_PASSWORD = re.compile(r"[@/]")
_SPLITS_A_QUERY_PASSWORD = re.compile(r"&")
_URL_DELIMITERS = re.compile(r"[@:/?,&=\[\]]")


@dataclass(frozen=True, slots=True)
class SqliteTarget:
    """A SQLite database file."""

    path: Path


@dataclass(frozen=True, slots=True)
class PostgresTarget:
    """A PostgreSQL database, as a libpq connection URL.

    Shown redacted by `repr`, so printing a resolved configuration never
    prints the password it carries.
    """

    url: str

    def __repr__(self) -> str:
        return f"PostgresTarget(url={redacted(self.url)!r})"


DatabaseTarget: TypeAlias = SqliteTarget | PostgresTarget


class UnsupportedDatabaseURLError(ValueError):
    """A database value shaped like a URL whose scheme is not PostgreSQL's.

    The message never repeats the value: a URL may carry a password."""


def database_target(value: str) -> DatabaseTarget:
    """`value` as a database target: a URL whose scheme is `postgresql` or
    `postgres`, in any case, is a PostgreSQL database; any other URL
    (`mysql://`, `sqlite:///`, `postgresql+psycopg://`) is refused; anything
    else is a SQLite path, taken as given.

    Taking another URL as a path would create directories named after it and
    show it, password included, in every message about the path.

    The scheme is lower-cased. libpq recognises a URL only by the lower-case
    scheme, and reads anything else as a `key=value` connection string,
    whose parse error quotes the whole value, password included.
    """
    scheme, separator, rest = value.partition("://")
    if separator and scheme.lower() in _POSTGRES_SCHEMES:
        return PostgresTarget(f"{scheme.lower()}://{rest}")
    if _URL_PREFIX.match(value):
        raise UnsupportedDatabaseURLError(
            f"a {scheme.lower()}:// URL is not a database vantage can use: "
            "give a SQLite file path or a postgresql:// URL"
        )
    return SqliteTarget(Path(value))


def redacted(url: str) -> str:
    """`url` with every password it carries replaced by `***`:
    `postgresql://user:***@host:5432/db`, and `password=***` in the query
    string, as for every parameter in `_SECRET_PARAMETERS`.

    Everything between the first `:` after the scheme and the last `@` is
    taken to be the user's password, because a password left unencoded may
    hold `@`, `/`, `?` or `#`. A URL with an `@` in its path or query can
    therefore lose more than its password; it never shows one.
    """
    spans = _merged([(start, end) for start, end, _ in _password_spans(url)])
    if not spans:
        return url
    parts: list[str] = []
    shown_from = 0
    for start, end in spans:
        parts.append(url[shown_from:start])
        parts.append(_MASK)
        shown_from = end
    parts.append(url[shown_from:])
    return "".join(parts)


def redact_message(message: str, url: str) -> str:
    """`message` with every password `url` carries replaced by `***`, as
    written in the URL and percent-decoded.

    For text quoted from elsewhere -- a driver's error -- that may repeat
    part of the URL: libpq quotes a malformed percent-escape it cannot
    decode, and splits a password holding an unencoded `@` or `/` (`&` in
    a query parameter) into fields it quotes one at a time, such as a host
    it cannot find. Every piece of such a password is hidden then, however
    short, even where the same text means something else in the message.
    """
    secrets: set[str] = set()
    for start, end, splits in _password_spans(url):
        written = url[start:end]
        pieces = [written]
        if splits.search(written):
            pieces.extend(_URL_DELIMITERS.split(written))
        secrets.update(text for piece in pieces for text in (piece, _percent_decoded(piece)))
    # Longest first, so a secret containing another is replaced whole.
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        message = message.replace(secret, _MASK)
    return message


def _password_spans(url: str) -> list[tuple[int, int, re.Pattern[str]]]:
    """The `(start, end)` index of each non-empty password in `url`, with
    what would make libpq split it: the user's, and the value of each query
    parameter that carries a secret, named as libpq reads it,
    percent-decoded."""
    scheme_end = url.find("://")
    if scheme_end == -1:
        return []
    authority = scheme_end + len("://")
    spans: list[tuple[int, int, re.Pattern[str]]] = []
    last_at = url.rfind("@", authority)
    if last_at != -1:
        colon = url.find(":", authority, last_at)
        if colon != -1 and colon + 1 < last_at:
            spans.append((colon + 1, last_at, _SPLITS_THE_USER_PASSWORD))
    for parameter in _QUERY_PARAMETER.finditer(url, authority):
        start, end = parameter.span("value")
        name = _percent_decoded(parameter["key"]).strip().lower()
        if start < end and name in _SECRET_PARAMETERS:
            spans.append((start, end, _SPLITS_A_QUERY_PASSWORD))
    return spans


def _merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """`spans` in order, overlapping ones joined: a query password written
    with an unencoded `@` can overlap what is taken for the user's."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _percent_decoded(text: str) -> str:
    raw = _PERCENT_ESCAPE.sub(
        lambda escape: bytes((int(escape[1], 16),)), text.encode("utf-8", "surrogatepass")
    )
    return raw.decode("utf-8", "replace")


__all__ = [
    "DatabaseTarget",
    "PostgresTarget",
    "SqliteTarget",
    "database_target",
    "redact_message",
    "redacted",
]
