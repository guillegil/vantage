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


def resolve_server_config(
    *,
    cli_database: str | None,
    env_database: str | None,
    cli_host: str | None,
    cli_port: int | None,
    home: Path,
    xdg_data_home: str | None,
) -> ServerConfig:
    """Resolve the server's database path and bind address.

    Database precedence: ``--database`` > ``VANTAGE_DATABASE`` >
    ``$XDG_DATA_HOME/vantage/vantage.db``, default
    ``~/.local/share/vantage/vantage.db``. Environment configuration is
    allowed here although RQ-2 forbids it on the plugin -- the threat
    differs, not the mechanism: RQ-2 stops a committed value silently
    enabling recording in someone else's project, while this server is
    started deliberately by whoever runs it.

    Host and port take only a CLI value or the fixed default -- design.md
    D11 names no environment variable for either.
    """
    database_path, database_source = _resolve_database_path(
        cli_database, env_database, home, xdg_data_home
    )
    return ServerConfig(
        database_path=database_path,
        database_source=database_source,
        host=cli_host if cli_host is not None else _DEFAULT_HOST,
        port=cli_port if cli_port is not None else _DEFAULT_PORT,
    )


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


__all__ = ["ConfigSource", "ServerConfig", "resolve_server_config"]
