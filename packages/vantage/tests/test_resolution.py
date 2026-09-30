"""`resolve_server_config`: precedence, the values it refuses, and which
database a value names -- with the redaction that shows a PostgreSQL URL
without its password.

Plain function calls throughout -- no server, no pytest session, and no
filesystem I/O: resolving a path answers a question and must never act on
the answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from vantage.core.config.database import (
    PostgresTarget,
    SqliteTarget,
    UnsupportedDatabaseURLError,
    database_target,
    redact_message,
    redacted,
)
from vantage.core.config.hosts import HostRule
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
    cli_allowed_hosts: tuple[str, ...] = (),
    env_allowed_hosts: str | None = None,
    home: Path | None = Path("/home/nobody"),
    xdg_data_home: str | None = None,
) -> ServerConfig:
    return resolve_server_config(
        cli_database=cli_database,
        env_database=env_database,
        cli_host=cli_host,
        cli_port=cli_port,
        cli_grace_period=cli_grace_period,
        cli_allowed_hosts=cli_allowed_hosts,
        env_allowed_hosts=env_allowed_hosts,
        home=home,
        xdg_data_home=xdg_data_home,
    )


def test_cli_database_takes_precedence_over_env_and_default() -> None:
    config = _resolve(cli_database="/explicit/vantage.db", env_database="/env/vantage.db")

    assert config.database == SqliteTarget(Path("/explicit/vantage.db"))


def test_env_database_used_when_no_cli_value() -> None:
    config = _resolve(env_database="/env/vantage.db")

    assert config.database == SqliteTarget(Path("/env/vantage.db"))


def test_default_database_uses_xdg_data_home_when_set() -> None:
    config = _resolve(xdg_data_home="/xdg/data")

    assert config.database == SqliteTarget(Path("/xdg/data/vantage/vantage.db"))


def test_default_database_falls_back_to_home_when_xdg_data_home_unset() -> None:
    config = _resolve(home=Path("/home/nobody"), xdg_data_home=None)

    assert config.database == SqliteTarget(Path("/home/nobody/.local/share/vantage/vantage.db"))


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

    assert config.database == SqliteTarget(Path("/home/nobody/.local/share/vantage/vantage.db"))


def test_an_empty_database_flag_falls_through_to_the_environment() -> None:
    config = _resolve(cli_database="", env_database="/env/vantage.db")

    assert config.database == SqliteTarget(Path("/env/vantage.db"))


_URL = "postgresql://vantage:s3cret@db.example:5432/vantage?sslmode=require"


@pytest.mark.parametrize(
    ("cli_database", "env_database"),
    [(_URL, None), (None, _URL), (_URL, "/env/vantage.db")],
    ids=["flag", "environment", "flag-over-environment"],
)
def test_a_postgresql_url_names_a_postgresql_database(
    cli_database: str | None, env_database: str | None
) -> None:
    """Taken as given, never as a path: a path would create a directory
    named `postgresql:` in the working directory."""
    config = _resolve(cli_database=cli_database, env_database=env_database, home=None)

    assert config.database == PostgresTarget(_URL)


def test_a_path_flag_beats_a_url_in_the_environment() -> None:
    config = _resolve(cli_database="/explicit/vantage.db", env_database=_URL)

    assert config.database == SqliteTarget(Path("/explicit/vantage.db"))


@pytest.mark.parametrize(
    ("value", "url"),
    [
        ("postgres://h/db", "postgres://h/db"),
        ("POSTGRESQL://u:pw@h/db", "postgresql://u:pw@h/db"),
        ("Postgres://u:PW@H/DB", "postgres://u:PW@H/DB"),
        ("postgresql:///vantage", "postgresql:///vantage"),
    ],
)
def test_the_scheme_is_matched_in_any_case_and_lower_cased(value: str, url: str) -> None:
    """libpq recognises a URL by the lower-case scheme only, and reads
    anything else as a `key=value` string whose parse error quotes the
    whole value, password included. Only the scheme is changed."""
    assert database_target(value) == PostgresTarget(url)


@pytest.mark.parametrize(
    "value",
    ["vantage.db", "postgresql:/one-slash", "./postgresql://h/db", "runs/v.db"],
)
def test_any_other_value_is_a_sqlite_path(value: str) -> None:
    assert database_target(value) == SqliteTarget(Path(value))


@pytest.mark.parametrize(
    "value",
    [
        "postgresql+psycopg://u:s3cret@h/db",
        "postgresqlx://u:s3cret@h/db",
        "mysql://u:s3cret@h/db",
        "sqlite:///vantage.db",
    ],
)
def test_a_url_of_another_scheme_is_refused_without_repeating_it(value: str) -> None:
    """Taken as a path it would create directories named after the URL and
    show it, password included, wherever the path is shown."""
    with pytest.raises(UnsupportedDatabaseURLError) as refused:
        database_target(value)
    assert "s3cret" not in str(refused.value)

    with pytest.raises(ServerConfigError) as typed:
        _resolve(cli_database=value)
    with pytest.raises(ServerConfigError) as from_environment:
        _resolve(env_database=value)

    assert str(typed.value).startswith("--database: ")
    assert str(from_environment.value).startswith("VANTAGE_DATABASE: ")
    assert "s3cret" not in str(typed.value) + str(from_environment.value)


def test_a_postgresql_target_shows_its_url_redacted() -> None:
    """So printing a resolved configuration never prints the password."""
    shown = repr(_resolve(cli_database=_URL))

    assert "s3cret" not in shown
    assert "postgresql://vantage:***@db.example:5432/vantage?sslmode=require" in shown


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

    assert config.database == SqliteTarget(Path(expected))


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

    assert config.database == SqliteTarget(Path("/home/nobody/.local/share/vantage/vantage.db"))


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


def test_a_loopback_default_answers_localhost_and_ip_literals_alone() -> None:
    assert _resolve().hosts == HostRule(frozenset())


def test_a_wide_bind_answers_every_name_unless_some_are_allowed() -> None:
    assert _resolve(cli_host="0.0.0.0").hosts is None  # noqa: S104
    assert _resolve(cli_host="0.0.0.0", env_allowed_hosts="vantage.example.com").hosts == (  # noqa: S104
        HostRule(frozenset({"vantage.example.com"}))
    )


def test_allowed_host_flags_replace_the_environment() -> None:
    """As the database flag replaces its variable: the command line is the
    most specific source, and a variable set for the container is not
    added to what a flag names."""
    config = _resolve(
        cli_allowed_hosts=("One.example.com", "two.example.com."),
        env_allowed_hosts="three.example.com",
    )

    assert config.hosts == HostRule(frozenset({"one.example.com", "two.example.com"}))


@pytest.mark.parametrize(
    ("value", "names"),
    [
        ("one.example.com", {"one.example.com"}),
        ("one.example.com, Two.example.com", {"one.example.com", "two.example.com"}),
        ("one.example.com,", {"one.example.com"}),
        (",,", set()),
        ("", set()),
    ],
)
def test_the_environment_names_hosts_comma_separated(value: str, names: set[str]) -> None:
    assert _resolve(env_allowed_hosts=value).hosts == HostRule(frozenset(names))


@pytest.mark.parametrize(
    ("setting", "source"),
    [
        ({"cli_allowed_hosts": ("vantage.example.com:8765",)}, "--allowed-host"),
        ({"cli_allowed_hosts": ("",)}, "--allowed-host"),
        ({"cli_allowed_hosts": ("https://vantage.example.com",)}, "--allowed-host"),
        ({"env_allowed_hosts": "ok.example.com,*.example.com"}, "VANTAGE_ALLOWED_HOSTS"),
    ],
    ids=["port", "empty-flag", "scheme", "wildcard"],
)
def test_an_allowed_host_that_is_not_a_host_name_is_refused_naming_its_source(
    setting: dict[str, object], source: str
) -> None:
    with pytest.raises(ServerConfigError, match=f"^{source}: .* is not a host name"):
        _resolve(**setting)  # type: ignore[arg-type]


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
        cli_allowed_hosts=(),
        env_allowed_hosts=None,
        home=tmp_path,
        xdg_data_home=str(xdg_data_home),
    )

    assert config.database == SqliteTarget(xdg_data_home / "vantage" / "vantage.db")
    assert not xdg_data_home.exists()


# --- redaction ----------------------------------------------------------------

_REDACTIONS = {
    "user-password": (
        "postgresql://vantage:s3cret@db.example:5432/vantage",
        "postgresql://vantage:***@db.example:5432/vantage",
    ),
    "percent-encoded": (
        "postgresql://vantage:s3%40cr%2Fet@db.example/vantage",
        "postgresql://vantage:***@db.example/vantage",
    ),
    "unencoded-at": (
        "postgresql://vantage:s3@cret@db.example/vantage",
        "postgresql://vantage:***@db.example/vantage",
    ),
    "unencoded-slash-question-hash": (
        "postgresql://vantage:s3/c?r#et@db.example/vantage",
        "postgresql://vantage:***@db.example/vantage",
    ),
    "query-password": (
        "postgresql://db.example/vantage?sslmode=require&password=s3cret&connect_timeout=5",
        "postgresql://db.example/vantage?sslmode=require&password=***&connect_timeout=5",
    ),
    "query-password-first": (
        "postgresql://db.example/vantage?password=s3cret",
        "postgresql://db.example/vantage?password=***",
    ),
    "query-password-any-case": (
        "postgresql://db.example/vantage?PassWord=s3cret",
        "postgresql://db.example/vantage?PassWord=***",
    ),
    "query-password-encoded-name": (
        "postgresql://db.example/vantage?pass%77ord=s3cret",
        "postgresql://db.example/vantage?pass%77ord=***",
    ),
    "query-password-unencoded-at": (
        "postgresql://db.example/vantage?password=s3@cret",
        "postgresql://db.example/vantage?password=***",
    ),
    "query-password-unencoded-ampersand": (
        "postgresql://db.example/vantage?password=s3&cr&et&sslmode=require",
        "postgresql://db.example/vantage?password=***&sslmode=require",
    ),
    "query-sslpassword": (
        "postgresql://db.example/vantage?sslpassword=s3cret&sslmode=verify-full",
        "postgresql://db.example/vantage?sslpassword=***&sslmode=verify-full",
    ),
    "both": (
        "postgresql://vantage:s3cret@db.example/vantage?password=s3cret",
        "postgresql://vantage:***@db.example/vantage?password=***",
    ),
    "ipv6-and-several-hosts": (
        "postgresql://vantage:s3cret@[::1]:5432,db.example:5433/vantage",
        "postgresql://vantage:***@[::1]:5432,db.example:5433/vantage",
    ),
}


@pytest.mark.parametrize(("url", "expected"), _REDACTIONS.values(), ids=_REDACTIONS)
def test_redacted_hides_every_password_a_url_carries(url: str, expected: str) -> None:
    assert redacted(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://db.example:5432/vantage",
        "postgresql://vantage@db.example/vantage",
        "postgresql://vantage:@db.example/vantage",
        "postgresql:///vantage?host=/run/postgresql",
        "postgresql://[::1]:5432/vantage",
        "postgresql://",
    ],
)
def test_redacted_leaves_a_url_without_a_password_as_it_is(url: str) -> None:
    assert redacted(url) == url


def test_an_at_sign_after_the_host_can_hide_more_than_the_password() -> None:
    """Everything between the first `:` and the last `@` is taken for the
    password, since an unencoded one may hold `@`: a URL with an `@` in its
    query then loses its port too, and never shows the password."""
    shown = redacted("postgresql://db.example:5432/vantage?password=s3@cret")

    assert "s3" not in shown
    assert "cret" not in shown
    assert shown.startswith("postgresql://db.example:")


def test_redact_message_hides_the_password_as_written_and_decoded() -> None:
    url = "postgresql://vantage:s3%23cret@db.example/vantage?password=an%20other"
    message = (
        f"connecting to {url} failed: invalid percent-encoded token: "
        '"s3%23cret"; the password s3#cret or "an other" was refused'
    )

    shown = redact_message(message, url)

    for secret in ("s3%23cret", "s3#cret", "an%20other", "an other"):
        assert secret not in shown
    assert shown.startswith("connecting to postgresql://vantage:***@db.example/vantage")


# What libpq says of a URL whose password holds an unencoded `@` or `/`
# (or `&`, in a query parameter): it splits the password into other fields
# and quotes any one of them alone.
_SPLIT_PASSWORDS = {
    "host-after-an-at": (
        "postgresql://vantage:s3@cr3t@db.example/vantage",
        "failed to resolve host 'cr3t@db.example'",
        "cr3t",
    ),
    "password-before-an-at": (
        "postgresql://vantage:s3cr%zz@h0st@db.example/vantage",
        'invalid percent-encoded token: "s3cr%zz"',
        "s3cr%zz",
    ),
    "host-between-an-at-and-a-colon": (
        "postgresql://vantage:s3cr@h0st:p0rt@db.example/vantage",
        "failed to resolve host 'h0st': [Errno -8] Servname not supported",
        "h0st",
    ),
    "database-after-a-slash": (
        "postgresql://localhost:5432/cr3t@db.example/vantage",
        'FATAL: database "cr3t@db.example/vantage" does not exist',
        "cr3t",
    ),
    "parameter-after-an-ampersand": (
        "postgresql://db.example/vantage?password=s3&cr3t",
        'missing key/value separator "=" in URI query parameter: "cr3t"',
        "cr3t",
    ),
}


@pytest.mark.parametrize(
    ("url", "message", "piece"), _SPLIT_PASSWORDS.values(), ids=_SPLIT_PASSWORDS
)
def test_redact_message_hides_each_field_libpq_splits_a_password_into(
    url: str, message: str, piece: str
) -> None:
    assert piece not in redact_message(message, url)


def test_redact_message_leaves_a_message_without_the_password_alone() -> None:
    message = 'connection to server at "127.0.0.1", port 5432 failed: Connection refused'

    assert redact_message(message, "postgresql://vantage:s3cret@127.0.0.1/db") == message
