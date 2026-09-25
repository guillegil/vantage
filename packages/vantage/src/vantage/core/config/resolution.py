"""Where the server's own database and bind address come from.

**Pure -- no filesystem access, ever.** Resolution only computes a path; it
never stats, creates or opens anything. If resolving created the directory,
merely asking where the database would go -- to display it, or to validate a
`--database` value that turns out to be a typo -- would materialise it as a
side effect. Creating anything belongs to whoever acts on the resolved path
(`service/cli.py`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
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


class ConfigSource(str, Enum):
    """Where a resolved value came from. **Never `StrEnum`** -- that is
    3.11+ and the supported floor is Python 3.10.
    """

    CLI = "cli"
    ENV = "env"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Everything `vantage serve` needs to start, already resolved."""

    database_path: Path
    database_source: ConfigSource
    host: str
    port: int
    grace_period_seconds: float
    grace_source: ConfigSource


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
    ``~/.local/share/vantage/vantage.db``. The plugin's activation switch is
    flag-only so shared configuration can never silently turn recording on;
    the environment is fine here because this server is started deliberately
    by whoever runs it.

    Host, port and the grace period each take only a CLI value or a fixed
    default; none has an environment variable.
    """
    database_path, database_source = _resolve_database_path(
        cli_database, env_database, home, xdg_data_home
    )
    grace_period_seconds, grace_source = _resolve_grace_period(cli_grace_period)
    return ServerConfig(
        database_path=database_path,
        database_source=database_source,
        host=cli_host if cli_host is not None else _DEFAULT_HOST,
        port=cli_port if cli_port is not None else _DEFAULT_PORT,
        grace_period_seconds=grace_period_seconds,
        grace_source=grace_source,
    )


class ServerConfigError(ValueError):
    """A configuration value this server cannot run with.

    Raised during resolution, before anything binds a port or opens a
    database, so an unusable value fails at startup rather than producing a
    server that runs and quietly answers wrong.
    """


def _resolve_grace_period(cli_grace_period: float | None) -> tuple[float, ConfigSource]:
    if cli_grace_period is not None:
        # `argparse type=float` accepts 0, -1, nan and inf. Any of them breaks
        # abandonment derivation for every unfinished run, including sessions
        # heartbeating normally -- a silently useless server rather than one
        # that refuses to start.
        if not math.isfinite(cli_grace_period) or cli_grace_period <= 0:
            raise ServerConfigError(
                f"--grace-period must be a positive number of seconds, got {cli_grace_period!r}"
            )
        return cli_grace_period, ConfigSource.CLI
    return _DEFAULT_GRACE_PERIOD_SECONDS, ConfigSource.DEFAULT


def _resolve_database_path(
    cli_database: str | None,
    env_database: str | None,
    home: Path,
    xdg_data_home: str | None,
) -> tuple[Path, ConfigSource]:
    if cli_database is not None:
        return Path(cli_database), ConfigSource.CLI
    if env_database is not None:
        return Path(env_database), ConfigSource.ENV
    data_home = Path(xdg_data_home) if xdg_data_home else home / ".local" / "share"
    return data_home / "vantage" / "vantage.db", ConfigSource.DEFAULT


DEFAULT_GRACE_PERIOD_SECONDS = _DEFAULT_GRACE_PERIOD_SECONDS
"""Public alias for `service/app.py`'s `create_app` default, so the "30 beats"
derivation lives in one place rather than as a bare literal at the call site."""

__all__ = [
    "ConfigSource",
    "DEFAULT_GRACE_PERIOD_SECONDS",
    "ServerConfig",
    "ServerConfigError",
    "resolve_server_config",
]
