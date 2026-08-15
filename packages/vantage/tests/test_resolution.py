"""`resolve_server_config` precedence (design.md D11).

Plain function calls throughout -- no server, no filesystem I/O, no pytest
session. Purity itself (no directory ever created) is proved separately in
`test_path_authority.py`, which is the threat-matrix "Path authority" test.
"""

from __future__ import annotations

from pathlib import Path

from vantage.core.config.resolution import ConfigSource, ServerConfig, resolve_server_config


def _resolve(
    *,
    cli_database: str | None = None,
    env_database: str | None = None,
    cli_host: str | None = None,
    cli_port: int | None = None,
    home: Path = Path("/home/nobody"),
    xdg_data_home: str | None = None,
) -> ServerConfig:
    return resolve_server_config(
        cli_database=cli_database,
        env_database=env_database,
        cli_host=cli_host,
        cli_port=cli_port,
        home=home,
        xdg_data_home=xdg_data_home,
    )


def test_cli_database_takes_precedence_over_env_and_default() -> None:
    config = _resolve(cli_database="/explicit/vantage.db", env_database="/env/vantage.db")

    assert config.database_path == Path("/explicit/vantage.db")
    assert config.database_source is ConfigSource.CLI


def test_env_database_used_when_no_cli_value() -> None:
    config = _resolve(env_database="/env/vantage.db")

    assert config.database_path == Path("/env/vantage.db")
    assert config.database_source is ConfigSource.ENV


def test_default_database_uses_xdg_data_home_when_set() -> None:
    config = _resolve(xdg_data_home="/xdg/data")

    assert config.database_path == Path("/xdg/data/vantage/vantage.db")
    assert config.database_source is ConfigSource.DEFAULT


def test_default_database_falls_back_to_home_when_xdg_data_home_unset() -> None:
    config = _resolve(home=Path("/home/nobody"), xdg_data_home=None)

    assert config.database_path == Path("/home/nobody/.local/share/vantage/vantage.db")
    assert config.database_source is ConfigSource.DEFAULT


def test_config_source_is_a_str_enum_not_strenum() -> None:
    # `StrEnum` is 3.11+; the floor is 3.10 (CLAUDE.md, design.md D11).
    assert issubclass(ConfigSource, str)
    assert ConfigSource.CLI == "cli"
    assert ConfigSource.CLI.value == "cli"


def test_default_host_and_port() -> None:
    config = _resolve()

    assert config.host == "127.0.0.1"
    assert config.port == 8765


def test_cli_host_and_port_override_the_default() -> None:
    config = _resolve(cli_host="0.0.0.0", cli_port=9000)

    assert config.host == "0.0.0.0"
    assert config.port == 9000
