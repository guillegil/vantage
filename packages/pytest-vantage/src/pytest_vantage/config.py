"""Where the plugin is allowed to send a report, and how long a report may take.

The address the plugin POSTs to can arrive from ``--vantage-server``, the
``vantage_server`` ini value or the ``VANTAGE_SERVER`` environment variable
-- any of which can be set from outside the developer's control, for
example by CI. An allow-list of exactly two schemes, ``http`` and
``https``, is the whole defence: anything else is refused before
``urllib``'s ``file:``/``ftp:`` handlers are ever reached.

`resolve_settings` is the one place a configured value is checked. Every
problem -- a value of the wrong type, a bad scheme, no host, credentials, a
query or fragment, an unusable port, a timeout the socket layer cannot use
-- is a `VantageConfigError` naming the option it came from, so nothing
malformed ever reaches the socket layer.

`resolve_mode` and `resolve_local_database` do the same for where a run is
kept: on the server, in a local SQLite database, or both.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# The server's default bind address too, so `--vantage` alone reaches a
# server started with no options.
_DEFAULT_ADDRESS = "http://127.0.0.1:8765"
# Bounds each reporting request as a whole; the preflight and the liveness
# requests are never held longer.
_DEFAULT_REPORT_TIMEOUT = 10.0
# Ceiling on a liveness request (start-write, heartbeat): a small fixed
# payload that must not stall as long as the finish report may.
_MAX_SHORT_TIMEOUT = 2.0


class VantageConfigError(ValueError):
    """A configured value cannot be used -- the session should not proceed."""


@dataclass(frozen=True)
class ReportSettings:
    """A validated destination and time bound for this session's reports."""

    address: str
    timeout: float


def resolve_and_validate_address(address: str, *, option: str = "vantage server address") -> str:
    """Return ``address`` unchanged if it is an ``http`` or ``https`` URL
    naming a host and, if it gives one, a port from 1 to 65535, with no
    user name, password, query or fragment.

    Raises ``VantageConfigError`` naming ``option`` and the address otherwise.
    ``urlparse`` does not raise on a bare host with no scheme at all (e.g.
    ``"localhost:8765"`` parses to scheme ``"localhost"``, path ``"8765"``)
    -- it having parsed is not the same as it being valid, and the
    allow-list rejects that case the same way it rejects ``ftp://``: by
    scheme, not by success of the parse.

    Credentials are refused first, and without repeating the address, so a
    password never reaches a log: urllib would take ``user:pw@host`` for the
    host and every request would fail. A query or a fragment would swallow
    or drop every route appended to the address.

    The host is checked the way the socket layer will encode it, as IDNA,
    so an empty or over-long label is a configuration error here rather
    than an unexpected exception from the preflight's connect.
    """
    try:
        parsed = urlparse(address)
    except ValueError as exc:
        raise VantageConfigError(f"{option} {address!r} is not a valid URL: {exc}") from None
    if "@" in parsed.netloc:
        raise VantageConfigError(
            f"{option} must not carry a user name or password (the part before '@')"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise VantageConfigError(f"{option} {address!r} is not a valid URL: {exc}") from None
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise VantageConfigError(
            f"{option} {address!r} must use http:// or https:// (got scheme {parsed.scheme!r})"
        )
    if not parsed.hostname:
        raise VantageConfigError(
            f"{option} {address!r} must name a host, as in http://127.0.0.1:8765"
        )
    if port == 0:
        raise VantageConfigError(f"{option} {address!r} must use a port from 1 to 65535")
    if "?" in address or "#" in address:
        raise VantageConfigError(f"{option} {address!r} must not carry a query or a fragment")
    try:
        parsed.hostname.encode("idna")
    except UnicodeError:
        raise VantageConfigError(
            f"{option} {address!r} has an invalid host name {parsed.hostname!r}"
        ) from None
    return address


def resolve_server_address(*, cli_url: str | None, env_url: str | None, ini_url: object) -> str:
    """Where to report to: `--vantage-server` > `VANTAGE_SERVER` > the
    `vantage_server` ini value > the default (`http://127.0.0.1:8765`).

    CLI over environment over ini mirrors the server's own precedence
    (`resolve_server_config`): the most explicit, session-specific source
    wins, and an environment variable -- how CI configures a container --
    outranks a value committed to `pyproject.toml` or `pytest.ini`, which
    everyone who checks the project out shares.

    Validates the address that wins, naming its source, before returning
    it -- every caller gets a validated address, never a raw configured
    string. The ini value is anything pytest read: before pytest 8.4 a list
    in the TOML table arrives as a list.
    """
    sources: tuple[tuple[str, object], ...] = (
        ("--vantage-server", cli_url),
        ("VANTAGE_SERVER", env_url),
        ("vantage_server ini value", ini_url),
    )
    for option, url in sources:
        if url is None or url == "":
            continue
        if not isinstance(url, str):
            raise VantageConfigError(f"{option} must be a URL (got {url!r})")
        return resolve_and_validate_address(url, option=option)
    return _DEFAULT_ADDRESS


def _positive_seconds(raw: object, option: str) -> float:
    """A zero timeout makes the connect non-blocking, a negative one makes
    the socket layer raise, and NaN, infinity or anything above
    ``threading.TIMEOUT_MAX`` (the largest timeout Python's blocking calls
    accept) get past the preflight's ``min(2.0, t)`` only to fail the
    finish report -- none is usable. Only text or a number is read as
    seconds: a list or table from TOML is not, and neither is a boolean,
    which Python would read as 0 or 1."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise VantageConfigError(f"{option} must be a number of seconds (got {raw!r})")
    try:
        seconds = float(raw)
    except ValueError:
        raise VantageConfigError(f"{option} must be a number of seconds (got {raw!r})") from None
    # A chained comparison is False for NaN, so NaN is refused here too.
    if not 0 < seconds <= threading.TIMEOUT_MAX:
        raise VantageConfigError(
            f"{option} must be a number of seconds above zero and at most "
            f"{threading.TIMEOUT_MAX:.0f} (got {raw!r})"
        )
    return seconds


def resolve_report_timeout(*, cli_timeout: float | None, ini_timeout: object) -> float:
    """The bound on each reporting request: `--vantage-timeout` > the
    `vantage_timeout` ini value > the default (`10.0` seconds). No
    environment variable is defined for the timeout -- only the address has
    one.

    The ini value is text in an ini file and a number in pytest's native
    TOML table; both are accepted, and nothing else.
    """
    if cli_timeout is not None:
        return _positive_seconds(cli_timeout, "--vantage-timeout")
    if ini_timeout is not None:
        return _positive_seconds(ini_timeout, "vantage_timeout ini value")
    return _DEFAULT_REPORT_TIMEOUT


def _read_ini(config: pytest.Config, name: str) -> Any:
    """pytest converts an ini value to its registered type while reading it
    and raises on a mismatch (``vantage_timeout = ten``, or a quoted number
    in the native TOML table); that is a configuration error like any
    other."""
    try:
        return config.getini(name)
    except (TypeError, ValueError) as exc:
        raise VantageConfigError(f"invalid {name} ini value: {exc}") from None


def resolve_settings(config: pytest.Config) -> ReportSettings:
    """Read and validate where to report and how long a report may take.

    Only the source that wins is read, so a broken committed ini value
    cannot stop a session that overrides it on the command line.
    """
    cli_url = config.getoption("vantage_server")
    env_url = os.environ.get("VANTAGE_SERVER")
    ini_url = None if cli_url or env_url else _read_ini(config, "vantage_server")
    cli_timeout = config.getoption("vantage_timeout")
    ini_timeout = None if cli_timeout is not None else _read_ini(config, "vantage_timeout")
    return ReportSettings(
        address=resolve_server_address(cli_url=cli_url, env_url=env_url, ini_url=ini_url),
        timeout=resolve_report_timeout(cli_timeout=cli_timeout, ini_timeout=ini_timeout),
    )


SERVER_MODE = "server"
LOCAL_MODE = "local"
BACKUP_MODE = "server+backup"
SERVER_AND_LOCAL_MODE = "server+local"
MODES = (SERVER_MODE, LOCAL_MODE, BACKUP_MODE, SERVER_AND_LOCAL_MODE)

# The schemes `vantage --database` reads as a PostgreSQL URL, in any case.
_POSTGRES_SCHEMES = frozenset({"postgresql", "postgres"})
# An RFC 3986 scheme followed by `//`: a URL, never a file path.
_URL_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://")


def resolve_mode(config: pytest.Config) -> str:
    """Where a recorded run goes: `--vantage-mode` > the `vantage_mode` ini
    value > `server`. Like the address, it says where, never whether, so the
    ini value is honoured."""
    cli_mode = config.getoption("vantage_mode", default=None)
    if cli_mode:
        option, mode = "--vantage-mode", cli_mode
    else:
        option, mode = "vantage_mode ini value", _read_ini(config, "vantage_mode")
        if mode is None or mode == "":
            return SERVER_MODE
    if mode not in MODES:
        raise VantageConfigError(f"{option} must be one of {', '.join(MODES)} (got {mode!r})")
    return str(mode)


def _local_database_value(raw: object, option: str, base: Path) -> Path:
    if not isinstance(raw, str):
        raise VantageConfigError(f"{option} must be a file path (got {raw!r})")
    scheme, separator, _rest = raw.partition("://")
    if separator and scheme.lower() in _POSTGRES_SCHEMES:
        # Never repeated back: the URL may carry a password.
        raise VantageConfigError(
            f"{option} must be a SQLite file path, not a PostgreSQL URL: "
            "local storage is SQLite only"
        )
    if _URL_PREFIX.match(raw):
        # Taken as a path, it would create directories named after the URL
        # and show it, password included, in the session header.
        raise VantageConfigError(
            f"{option} must be a SQLite file path, not a {scheme.lower()}:// URL"
        )
    if "\0" in raw:
        raise VantageConfigError(f"{option} must not contain a NUL character")
    try:
        path = base / Path(raw).expanduser()
    except RuntimeError as exc:  # `~` with no home directory to expand it to
        raise VantageConfigError(f"{option} {raw!r} cannot be expanded: {exc}") from None
    try:
        is_directory = path.is_dir()
    except OSError:
        # Not knowable from here; opening the database says what is wrong.
        is_directory = False
    if is_directory:
        raise VantageConfigError(f"{option} {str(path)!r} is a directory, not a database file")
    return path


def resolve_local_database(config: pytest.Config, *, default: Callable[[], Path]) -> Path:
    """The local SQLite database: `--vantage-local-database` > the
    `vantage_local_database` ini value > `default()`, the `vantage`
    command's own default, so `vantage` started with no options serves what
    was stored here. There is no environment variable for it.

    A relative path is taken from where pytest was started when typed, and
    from the ini file's directory when configured there, as pytest reads its
    own path options; a file committed with the project means the same
    place whichever directory pytest runs from.
    """
    cli_value = config.getoption("vantage_local_database", default=None)
    if cli_value:
        return _local_database_value(
            cli_value, "--vantage-local-database", Path(config.invocation_params.dir)
        )
    ini_value = _read_ini(config, "vantage_local_database")
    if ini_value is not None and ini_value != "":
        # With no ini file the value came from `-o`, typed like an option.
        inipath = getattr(config, "inipath", None)
        base = Path(inipath).parent if inipath else Path(config.invocation_params.dir)
        return _local_database_value(ini_value, "vantage_local_database ini value", base)
    return default()


def resolve_liveness_timeout(report_timeout: float) -> float:
    """The bound on a liveness request (start-write, heartbeat): `min(2.0,
    report_timeout)`.

    Taking the smaller honours both ceilings: a user who configured a
    timeout below 2.0 seconds never gets more than they asked for, while a
    larger (or default) timeout still leaves a liveness request bounded well
    below the time a full report is allowed to take.
    """
    return min(_MAX_SHORT_TIMEOUT, report_timeout)


__all__ = [
    "BACKUP_MODE",
    "LOCAL_MODE",
    "MODES",
    "SERVER_AND_LOCAL_MODE",
    "SERVER_MODE",
    "ReportSettings",
    "VantageConfigError",
    "resolve_and_validate_address",
    "resolve_liveness_timeout",
    "resolve_local_database",
    "resolve_mode",
    "resolve_report_timeout",
    "resolve_server_address",
    "resolve_settings",
]
