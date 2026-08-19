"""Where the server's own database and bind address come from (design.md D11).

**Pure -- no filesystem access, ever.** Resolution only computes a path; it
never stats, creates or opens anything. That is the whole of the
threat-matrix "Path authority" defence: if resolving *created* the
directory, then merely asking where the database would go -- to display it,
to validate a `--database` value that turns out to be a typo -- would
materialise it as a side effect of asking. Creating anything belongs to
whoever acts on the resolved path (`service/cli.py`), never to resolution
itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765

# design.md D34: the default grace period is a multiple of the default
# heartbeat interval, not an invented round number -- the "hint" name marks
# that `pytest_vantage.recorder._BEAT_INTERVAL_SECONDS` is declared
# separately, on the other side of the HTTP boundary (RQ-24/ADR-9 forbid
# sharing code across it); this copy only derives a default, so a divergence
# between the two changes the multiple, never correctness.
_BEAT_INTERVAL_HINT_SECONDS = 30.0
_DEFAULT_GRACE_BEATS = 30
_DEFAULT_GRACE_PERIOD_SECONDS = _DEFAULT_GRACE_BEATS * _BEAT_INTERVAL_HINT_SECONDS  # 900.0


class ConfigSource(str, Enum):
    """Where a resolved value came from. **Never `StrEnum`** -- that is
    3.11+ and the floor is 3.10 (CLAUDE.md).
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
    ``~/.local/share/vantage/vantage.db``. Environment configuration is
    allowed here although RQ-2 forbids it on the plugin -- the threat
    differs, not the mechanism: RQ-2 stops a committed value silently
    enabling recording in someone else's project, while this server is
    started deliberately by whoever runs it.

    Host, port and the grace period each take only a CLI value or a fixed
    default -- design.md D11/D34 name no environment variable for any of
    the three.
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


def _resolve_grace_period(cli_grace_period: float | None) -> tuple[float, ConfigSource]:
    if cli_grace_period is not None:
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
"""Public alias for `service/app.py`'s `create_app` default (design.md D34) --
so the "30 beats" derivation lives in exactly one place rather than being
duplicated as a bare literal at the `create_app` call site."""

__all__ = [
    "ConfigSource",
    "DEFAULT_GRACE_PERIOD_SECONDS",
    "ServerConfig",
    "resolve_server_config",
]
