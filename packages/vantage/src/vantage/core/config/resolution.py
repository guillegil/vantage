"""Where the server's own database, bind address and host names come from.

**Pure -- no filesystem or network access, ever.** Resolution only computes
a path, or takes a URL as given; it never stats, creates, opens or connects
to anything. If resolving created the directory, merely asking where the
database would go -- to display it, or to validate a `--database` value that
turns out to be a typo -- would materialise it as a side effect. Creating
anything belongs to whoever acts on the resolved target (`service/cli.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from vantage.core.config.database import (
    DatabaseTarget,
    SqliteTarget,
    UnsupportedDatabaseURLError,
    database_target,
)
from vantage.core.config.hosts import HostNameError, HostRule, allowed_host_name, host_rule

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765

# The default grace period is a multiple of the plugin's heartbeat interval.
# "Hint" because `pytest_vantage.recorder._BEAT_INTERVAL_SECONDS` is declared
# separately: the plugin shares no code with the server. This copy only
# derives a default, so a divergence changes the multiple, never correctness.
_BEAT_INTERVAL_HINT_SECONDS = 30.0
_DEFAULT_GRACE_BEATS = 30
_DEFAULT_GRACE_PERIOD_SECONDS = _DEFAULT_GRACE_BEATS * _BEAT_INTERVAL_HINT_SECONDS  # 900.0

# Longer than any live session goes quiet, and far inside what `timedelta`
# can hold -- `create_app` builds one from the grace period.
_MAX_GRACE_PERIOD_SECONDS = 365 * 24 * 60 * 60.0


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Everything the `vantage` command needs to start, already resolved."""

    database: DatabaseTarget
    host: str
    port: int
    grace_period_seconds: float
    hosts: HostRule | None
    """The host names the server answers, or `None` for every name
    (`core/config/hosts.py`)."""


def resolve_server_config(
    *,
    cli_database: str | None,
    env_database: str | None,
    cli_host: str | None,
    cli_port: int | None,
    cli_grace_period: float | None,
    cli_allowed_hosts: Sequence[str],
    env_allowed_hosts: str | None,
    home: Path | None,
    xdg_data_home: str | None,
) -> ServerConfig:
    """Resolve the server's database, bind address, grace period and the
    host names it answers.

    Database precedence: ``--database`` > ``VANTAGE_DATABASE`` >
    ``$XDG_DATA_HOME/vantage/vantage.db``, default
    ``~/.local/share/vantage/vantage.db``. A value whose scheme is
    ``postgresql://`` or ``postgres://`` names a PostgreSQL database; any
    other value is a SQLite path, and the default is always SQLite. An empty
    value counts as unset, and a relative ``XDG_DATA_HOME`` is ignored.
    `home` is `None` when the process has no home directory to find; that
    refuses only a start that falls back to the default path. The plugin's
    activation switch is flag-only so shared configuration can never
    silently turn recording on; the environment is fine here because this
    server is started deliberately by whoever runs it.

    Host, port and the grace period each take only a CLI value or a fixed
    default; none has an environment variable. The names allowed beyond
    `localhost` and IP literals come from every ``--allowed-host``, else
    from ``VANTAGE_ALLOWED_HOSTS``, comma-separated, else none: a container
    binds wide inside, and its operator names the proxy in front of it
    where they name its database. A value the server cannot run with
    raises `ServerConfigError` here, before anything is created.
    """
    host = _resolve_host(cli_host)
    return ServerConfig(
        database=_resolve_database(cli_database, env_database, home, xdg_data_home),
        host=host,
        port=_resolve_port(cli_port),
        grace_period_seconds=_resolve_grace_period(cli_grace_period),
        hosts=host_rule(host, _resolve_allowed_hosts(cli_allowed_hosts, env_allowed_hosts)),
    )


class ServerConfigError(ValueError):
    """A configuration value this server cannot run with.

    Raised during resolution, before anything binds a port or opens a
    database, so an unusable value fails at startup rather than producing a
    server that runs and quietly answers wrong.
    """


def _resolve_host(cli_host: str | None) -> str:
    if cli_host is None:
        return _DEFAULT_HOST
    # `--host "$VAR"` with the variable unset arrives as "", which asyncio
    # binds as every interface: the opposite of the loopback default meant.
    # Whitespace is never part of an address, and a padded one neither
    # resolves nor compares equal to the loopback default.
    host = cli_host.strip()
    if not host:
        raise ServerConfigError(
            f"--host must name a bind address, got {cli_host!r}; omit it to bind {_DEFAULT_HOST}"
        )
    return host


def _resolve_allowed_hosts(cli: Sequence[str], env: str | None) -> frozenset[str]:
    if cli:
        return frozenset(_allowed("--allowed-host", value) for value in cli)
    # Empty entries are dropped, so a trailing comma, or an empty variable,
    # allows nothing rather than refusing the start. An empty flag is still
    # refused: `--allowed-host "$VAR"` with the variable unset is a mistake.
    entries = [entry for entry in (env or "").split(",") if entry.strip()]
    return frozenset(_allowed("VANTAGE_ALLOWED_HOSTS", entry) for entry in entries)


def _allowed(source: str, value: str) -> str:
    try:
        return allowed_host_name(value)
    except HostNameError as exc:
        raise ServerConfigError(f"{source}: {exc}") from None


def _resolve_port(cli_port: int | None) -> int:
    if cli_port is None:
        return _DEFAULT_PORT
    # `argparse type=int` accepts any integer, and uvicorn rejects a bad one
    # only after the database exists. 0 binds a random port no plugin can find.
    if not 1 <= cli_port <= 65535:
        raise ServerConfigError(f"--port must be between 1 and 65535, got {cli_port}")
    return cli_port


def _resolve_grace_period(cli_grace_period: float | None) -> float:
    if cli_grace_period is None:
        return _DEFAULT_GRACE_PERIOD_SECONDS
    # `argparse type=float` accepts 0, -1, nan, inf and 1e14. The first two
    # make every unfinished run abandoned on sight, including sessions
    # heartbeating normally; the others cannot become a `timedelta` at all.
    # So does a positive value below half a microsecond, which `timedelta`
    # rounds to zero. `create_app` refuses all of them, but only after the
    # database is open, so the refusal comes here first, as one line. The
    # chained comparison is false for nan, and is checked before the
    # `timedelta` is built.
    if not 0 < cli_grace_period <= _MAX_GRACE_PERIOD_SECONDS or timedelta(
        seconds=cli_grace_period
    ) <= timedelta(0):
        raise ServerConfigError(
            f"--grace-period must be at least one microsecond and at most "
            f"{_MAX_GRACE_PERIOD_SECONDS:.0f} seconds (365 days), got {cli_grace_period!r}"
        )
    return cli_grace_period


def _resolve_database(
    cli_database: str | None,
    env_database: str | None,
    home: Path | None,
    xdg_data_home: str | None,
) -> DatabaseTarget:
    # An empty value is unset, not `Path("")`: that is the current directory,
    # and `--database "$VAR"` with the variable unset does not mean "here".
    if cli_database:
        return _target("--database", cli_database)
    if env_database:
        return _target("VANTAGE_DATABASE", env_database)
    default = default_sqlite_path(home=home, xdg_data_home=xdg_data_home)
    if default is None:
        raise ServerConfigError(
            "there is no home directory to put the default database under; "
            "pass --database or set VANTAGE_DATABASE"
        )
    return SqliteTarget(default)


def resolve_database(
    *,
    cli_database: str | None,
    env_database: str | None,
    home: Path | None,
    xdg_data_home: str | None,
) -> DatabaseTarget:
    """The database alone, resolved as `resolve_server_config` resolves it:
    for the commands that manage the database a server serves without
    serving it."""
    return _resolve_database(cli_database, env_database, home, xdg_data_home)


def _target(source: str, value: str) -> DatabaseTarget:
    try:
        return database_target(value)
    except UnsupportedDatabaseURLError as exc:
        raise ServerConfigError(f"{source}: {exc}") from None


def default_sqlite_path(*, home: Path | None, xdg_data_home: str | None) -> Path | None:
    """The default SQLite database: ``$XDG_DATA_HOME/vantage/vantage.db``,
    else ``~/.local/share/vantage/vantage.db`` under `home`, else `None`
    when there is no home directory to put it under.

    The one statement of the default, so the server and the plugin's local
    store agree on it: `vantage` run with no options serves what the tests
    stored locally. An empty ``XDG_DATA_HOME`` counts as unset.
    """
    # The XDG Base Directory spec says a relative XDG_DATA_HOME is invalid and
    # must be ignored; used as-is it would move the database with the cwd.
    xdg = Path(xdg_data_home) if xdg_data_home else None
    if xdg is not None and xdg.is_absolute():
        return xdg / "vantage" / "vantage.db"
    if home is None:
        return None
    return home / ".local" / "share" / "vantage" / "vantage.db"


DEFAULT_GRACE_PERIOD_SECONDS = _DEFAULT_GRACE_PERIOD_SECONDS
"""Public alias for `service/app.py`'s `create_app` default, so the "30 beats"
derivation lives in one place rather than as a bare literal at the call site."""

__all__ = [
    "DEFAULT_GRACE_PERIOD_SECONDS",
    "ServerConfig",
    "ServerConfigError",
    "default_sqlite_path",
    "resolve_database",
    "resolve_server_config",
]
