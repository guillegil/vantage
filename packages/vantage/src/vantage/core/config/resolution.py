"""Where the server's own database and bind address come from.

**Pure -- no filesystem access, ever.** Resolution only computes a path; it
never stats, creates or opens anything. If resolving created the directory,
merely asking where the database would go -- to display it, or to validate a
`--database` value that turns out to be a typo -- would materialise it as a
side effect. Creating anything belongs to whoever acts on the resolved path
(`service/cli.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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

    database_path: Path
    host: str
    port: int
    grace_period_seconds: float


def resolve_server_config(
    *,
    cli_database: str | None,
    env_database: str | None,
    cli_host: str | None,
    cli_port: int | None,
    cli_grace_period: float | None,
    home: Path,
    xdg_data_home: str | None,
) -> ServerConfig:
    """Resolve the server's database path, bind address, and grace period.

    Database precedence: ``--database`` > ``VANTAGE_DATABASE`` >
    ``$XDG_DATA_HOME/vantage/vantage.db``, default
    ``~/.local/share/vantage/vantage.db``. An empty value counts as unset,
    and a relative ``XDG_DATA_HOME`` is ignored. The plugin's activation
    switch is flag-only so shared configuration can never silently turn
    recording on; the environment is fine here because this server is
    started deliberately by whoever runs it.

    Host, port and the grace period each take only a CLI value or a fixed
    default; none has an environment variable. A value the server cannot run
    with raises `ServerConfigError` here, before anything is created.
    """
    return ServerConfig(
        database_path=_resolve_database_path(cli_database, env_database, home, xdg_data_home),
        host=_resolve_host(cli_host),
        port=_resolve_port(cli_port),
        grace_period_seconds=_resolve_grace_period(cli_grace_period),
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
    if not cli_host.strip():
        raise ServerConfigError(
            f"--host must name a bind address, got {cli_host!r}; omit it to bind {_DEFAULT_HOST}"
        )
    return cli_host


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
    # `create_app` refuses both, but only after the database is open, so the
    # refusal comes here first, as one line. The chained comparison is false
    # for nan.
    if not 0 < cli_grace_period <= _MAX_GRACE_PERIOD_SECONDS:
        raise ServerConfigError(
            f"--grace-period must be more than 0 and at most {_MAX_GRACE_PERIOD_SECONDS:.0f} "
            f"seconds (365 days), got {cli_grace_period!r}"
        )
    return cli_grace_period


def _resolve_database_path(
    cli_database: str | None,
    env_database: str | None,
    home: Path,
    xdg_data_home: str | None,
) -> Path:
    # An empty value is unset, not `Path("")`: that is the current directory,
    # and `--database "$VAR"` with the variable unset does not mean "here".
    if cli_database:
        return Path(cli_database)
    if env_database:
        return Path(env_database)
    # The XDG Base Directory spec says a relative XDG_DATA_HOME is invalid and
    # must be ignored; used as-is it would move the database with the cwd.
    xdg = Path(xdg_data_home) if xdg_data_home else None
    data_home = xdg if xdg is not None and xdg.is_absolute() else home / ".local" / "share"
    return data_home / "vantage" / "vantage.db"


DEFAULT_GRACE_PERIOD_SECONDS = _DEFAULT_GRACE_PERIOD_SECONDS
"""Public alias for `service/app.py`'s `create_app` default, so the "30 beats"
derivation lives in one place rather than as a bare literal at the call site."""

__all__ = [
    "DEFAULT_GRACE_PERIOD_SECONDS",
    "ServerConfig",
    "ServerConfigError",
    "resolve_server_config",
]
