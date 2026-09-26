"""`resolve_server_config`: precedence, and the values it refuses.

Plain function calls throughout -- no server, no pytest session, and no
filesystem I/O: resolving a path answers a question and must never act on
the answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from vantage.core.config.resolution import (
    ServerConfig,
    ServerConfigError,
    resolve_server_config,
)


def _resolve(
    *,
    cli_database: str | None = None,
    env_database: str | None = None,
    cli_host: str | None = None,
    cli_port: int | None = None,
    cli_grace_period: float | None = None,
    home: Path | None = Path("/home/nobody"),
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


def test_env_database_used_when_no_cli_value() -> None:
    config = _resolve(env_database="/env/vantage.db")

    assert config.database_path == Path("/env/vantage.db")


def test_default_database_uses_xdg_data_home_when_set() -> None:
    config = _resolve(xdg_data_home="/xdg/data")

    assert config.database_path == Path("/xdg/data/vantage/vantage.db")


def test_default_database_falls_back_to_home_when_xdg_data_home_unset() -> None:
    config = _resolve(home=Path("/home/nobody"), xdg_data_home=None)

    assert config.database_path == Path("/home/nobody/.local/share/vantage/vantage.db")


@pytest.mark.parametrize(
    ("cli_database", "env_database"),
    [("", None), (None, ""), ("", "")],
    ids=["empty-flag", "empty-env", "both-empty"],
)
def test_an_empty_database_setting_counts_as_unset(
    cli_database: str | None, env_database: str | None
) -> None:
    """`--database "$VAR"` or `VANTAGE_DATABASE=` with nothing behind it
    means "not configured". Taken literally it is `Path("")`, the current
    directory, and startup then acts on that directory as if it were the
    database's.
    """
    config = _resolve(cli_database=cli_database, env_database=env_database)

    assert config.database_path == Path("/home/nobody/.local/share/vantage/vantage.db")


def test_an_empty_database_flag_falls_through_to_the_environment() -> None:
    config = _resolve(cli_database="", env_database="/env/vantage.db")

    assert config.database_path == Path("/env/vantage.db")


@pytest.mark.parametrize(
    ("cli_database", "env_database", "xdg_data_home", "expected"),
    [
        ("/explicit/v.db", None, None, "/explicit/v.db"),
        (None, "/env/v.db", None, "/env/v.db"),
        (None, None, "/xdg/data", "/xdg/data/vantage/vantage.db"),
    ],
    ids=["flag", "environment", "xdg-data-home"],
)
def test_no_home_directory_is_needed_unless_the_default_path_is(
    cli_database: str | None, env_database: str | None, xdg_data_home: str | None, expected: str
) -> None:
    """A container run as a uid with no passwd entry and no `HOME` has no
    home directory; the command passes `None` for it."""
    config = _resolve(
        cli_database=cli_database,
        env_database=env_database,
        xdg_data_home=xdg_data_home,
        home=None,
    )

    assert config.database_path == Path(expected)


def test_the_default_path_without_a_home_directory_is_refused() -> None:
    with pytest.raises(ServerConfigError, match="--database"):
        _resolve(home=None, xdg_data_home="relative/data")


@pytest.mark.parametrize("xdg_data_home", ["relative/data", "./data", "data", "~/data"])
def test_default_database_ignores_a_relative_xdg_data_home(xdg_data_home: str) -> None:
    """The XDG Base Directory spec says a relative value is invalid and must
    be ignored. Used as-is it would put the database wherever the server
    happened to be started, splitting history across directories.
    """
    config = _resolve(home=Path("/home/nobody"), xdg_data_home=xdg_data_home)

    assert config.database_path == Path("/home/nobody/.local/share/vantage/vantage.db")


def test_default_host_and_port() -> None:
    config = _resolve()

    assert config.host == "127.0.0.1"
    assert config.port == 8765


def test_cli_host_and_port_override_the_default() -> None:
    config = _resolve(cli_host="0.0.0.0", cli_port=9000)  # noqa: S104

    assert config.host == "0.0.0.0"  # noqa: S104
    assert config.port == 9000


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_host_is_refused(value: str) -> None:
    """`--host "$VAR"` with the variable unset arrives as an empty host,
    which asyncio binds as every interface -- the opposite of the loopback
    default the operator meant. Refusing it surfaces the unset variable.
    """
    with pytest.raises(ServerConfigError, match="--host"):
        _resolve(cli_host=value)


@pytest.mark.parametrize("value", [" 127.0.0.1", "127.0.0.1 ", "\t127.0.0.1\n"])
def test_whitespace_around_a_host_is_dropped(value: str) -> None:
    """No address contains whitespace. Kept, `"127.0.0.1 "` fails to
    resolve at bind time, and is not the loopback default the wide-bind
    warning compares against, so a loopback start warns first."""
    assert _resolve(cli_host=value).host == "127.0.0.1"


@pytest.mark.parametrize("port", [-1, 0, 65536, 70000])
def test_a_port_outside_the_bindable_range_is_refused(port: int) -> None:
    """`argparse type=int` accepts any integer; uvicorn only fails on it
    after the database has been created. 0 would bind a random port that no
    plugin could find.
    """
    with pytest.raises(ServerConfigError, match="--port"):
        _resolve(cli_port=port)


@pytest.mark.parametrize("port", [1, 65535])
def test_both_ends_of_the_port_range_are_accepted(port: int) -> None:
    assert _resolve(cli_port=port).port == port


def test_default_grace_period_is_900_seconds() -> None:
    """900.0 seconds, expressed in source as `30 * 30.0` -- a multiple of the
    default heartbeat interval, not an invented round number. CLI-only, like
    `host` and `port`."""
    config = _resolve()

    assert config.grace_period_seconds == 900.0


def test_cli_grace_period_overrides_the_default() -> None:
    config = _resolve(cli_grace_period=60.0)

    assert config.grace_period_seconds == 60.0


_ONE_YEAR_SECONDS = 365 * 24 * 60 * 60.0


@pytest.mark.parametrize(
    "value",
    [0.0, -1.0, 1e-7, 5e-7, float("nan"), float("inf"), _ONE_YEAR_SECONDS + 1, 1e14, 1e100],
)
def test_a_nonsensical_grace_period_is_refused_at_resolution(value: float) -> None:
    """`argparse type=float` accepts 0, -1, nan, inf and 1e14.

    0 and -1 make every unfinished run derive as abandoned the instant it is
    read -- including sessions heartbeating normally, and so do 1e-7 and
    5e-7, which `timedelta` rounds to zero. nan, inf and 1e14
    cannot become the `timedelta` `create_app` builds; the cap sits at one
    year, far below where that happens. The server must refuse before it
    opens the database, rather than fail with a traceback after it.
    """
    with pytest.raises(ServerConfigError, match="--grace-period"):
        _resolve(cli_grace_period=value)


def test_a_grace_period_of_one_microsecond_is_accepted() -> None:
    assert _resolve(cli_grace_period=1e-6).grace_period_seconds == 1e-6


def test_a_grace_period_of_one_year_is_accepted() -> None:
    assert _resolve(cli_grace_period=_ONE_YEAR_SECONDS).grace_period_seconds == _ONE_YEAR_SECONDS


def test_resolution_creates_no_directory(tmp_path: Path) -> None:
    """`resolve_server_config` is pure: asking where the database would go
    must not materialise it. `xdg_data_home` names a directory that does
    not exist on disk; resolving against it must leave it that way.
    """
    xdg_data_home = tmp_path / "xdg-data"

    config = resolve_server_config(
        cli_database=None,
        env_database=None,
        cli_host=None,
        cli_port=None,
        cli_grace_period=None,
        home=tmp_path,
        xdg_data_home=str(xdg_data_home),
    )

    assert config.database_path == xdg_data_home / "vantage" / "vantage.db"
    assert not xdg_data_home.exists()
