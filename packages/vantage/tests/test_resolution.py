"""`resolve_server_config` precedence (design.md D11).

Plain function calls throughout -- no server, no filesystem I/O, no pytest
session. Purity itself (no directory ever created) is proved separately in
`test_path_authority.py`, which is the threat-matrix "Path authority" test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from vantage.core.config.resolution import ConfigSource, ServerConfig, resolve_server_config


def _resolve(
    *,
    cli_database: str | None = None,
    env_database: str | None = None,
    cli_host: str | None = None,
    cli_port: int | None = None,
    cli_grace_period: float | None = None,
    home: Path = Path("/home/nobody"),
    xdg_data_home: str | None = None,
) -> ServerConfig:
    return resolve_server_config(
        cli_database=cli_database,
        env_database=env_database,
        cli_host=cli_host,
        cli_port=cli_port,
        cli_grace_period=cli_grace_period,
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
    assert isinstance(ConfigSource.CLI, str)
    assert ConfigSource.CLI.value == "cli"


def test_default_host_and_port() -> None:
    config = _resolve()

    assert config.host == "127.0.0.1"
    assert config.port == 8765


def test_cli_host_and_port_override_the_default() -> None:
    config = _resolve(cli_host="0.0.0.0", cli_port=9000)  # noqa: S104

    assert config.host == "0.0.0.0"  # noqa: S104
    assert config.port == 9000


@pytest.mark.req(id="RQ-44")
def test_default_grace_period_is_900_seconds_from_the_default_source() -> None:
    """design.md D34: 900.0 seconds, expressed in source as `30 * 30.0` -- a
    multiple of the default heartbeat interval, not an invented round
    number. No environment variable exists for this (CLI-only, matching
    `host`/`port`'s own precedent)."""
    config = _resolve()

    assert config.grace_period_seconds == 900.0
    assert config.grace_source is ConfigSource.DEFAULT


@pytest.mark.req(id="RQ-44")
def test_cli_grace_period_overrides_the_default() -> None:
    config = _resolve(cli_grace_period=60.0)

    assert config.grace_period_seconds == 60.0
    assert config.grace_source is ConfigSource.CLI


@pytest.mark.req(id="RQ-44")
def test_cli_main_carries_the_resolved_grace_period_into_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam between a resolved config and a running app, which neither
    half's own test can see.

    `test_cli_grace_period_overrides_the_default` proves resolution, and
    `test_create_app_exposes_the_configured_grace_period` proves the app
    stores what it is handed. Nothing proved that `main` passes one to the
    other -- dropping `grace_period_seconds=` from the `create_app` call
    leaves the whole suite green while `--grace-period 60` silently runs at
    the 900-second default. Verified by mutation.

    `uvicorn.run` is replaced because the point is the app it is handed, not
    serving it.
    """
    from vantage.service import cli

    served: dict[str, object] = {}

    def _capture(app: object, **_kwargs: object) -> None:
        served["app"] = app

    monkeypatch.setattr(cli.uvicorn, "run", _capture)

    cli.main(["--database", str(tmp_path / "v.db"), "--grace-period", "60"])

    app = served["app"]
    assert app.state.grace_period == 60.0  # type: ignore[attr-defined]
