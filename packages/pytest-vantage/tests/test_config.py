"""Where to report and how long a report may take.

Each value has a fixed precedence -- command line, then (for the address)
the ``VANTAGE_SERVER`` environment variable, then the ini value, then the
default -- and only the value that wins is read. `resolve_settings` is the
one place a configured value is checked: every problem is a
`VantageConfigError` naming the option, which the plugin turns into a
pytest usage error, never an INTERNALERROR traceback.
"""

from __future__ import annotations

import math
import socket

import pytest
from pytest_vantage.config import (
    DEFAULT_PROJECT,
    VantageConfigError,
    resolve_project,
    resolve_report_timeout,
    resolve_server_address,
    resolve_settings,
)
from warnings_summary import vantage_warnings

_PASSING_TEST = "def test_it():\n    assert True\n"


def _closed_port_address() -> str:
    """An address where a connect is refused: an ephemeral loopback port,
    bound and closed again, so no fixed port has to be assumed free."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}"


# --- Precedence ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("cli_url", "env_url", "ini_url", "expected"),
    [
        ("http://cli:1", "http://env:2", "http://ini:3", "http://cli:1"),
        (None, "http://env:2", "http://ini:3", "http://env:2"),
        (None, None, "http://ini:3", "http://ini:3"),
        (None, None, None, "http://127.0.0.1:8765"),
    ],
)
def test_address_precedence_is_cli_then_environment_then_ini_then_default(
    cli_url: str | None, env_url: str | None, ini_url: str | None, expected: str
) -> None:
    assert resolve_server_address(cli_url=cli_url, env_url=env_url, ini_url=ini_url) == expected


@pytest.mark.parametrize(
    ("cli_timeout", "ini_timeout", "expected"),
    [(3.0, "7", 3.0), (None, "7", 7.0), (None, None, 10.0)],
)
def test_timeout_precedence_is_cli_then_ini_then_default(
    cli_timeout: float | None, ini_timeout: str | None, expected: float
) -> None:
    assert resolve_report_timeout(cli_timeout=cli_timeout, ini_timeout=ini_timeout) == expected


# --- Timeout validation ---------------------------------------------------------


@pytest.mark.parametrize("ini_timeout", [5, 5.5, "2.5", "0.001", "86400"])
def test_an_ini_timeout_may_be_a_number_or_numeric_text(ini_timeout: str | float) -> None:
    """pytest's native TOML table hands `getini` an int or a float; the
    ini-file forms hand it text. Both mean seconds."""
    assert resolve_report_timeout(cli_timeout=None, ini_timeout=ini_timeout) == float(ini_timeout)


@pytest.mark.parametrize("cli_timeout", [-1.0, -0.5, 0.0, math.nan, math.inf, 99999999999.0])
def test_a_cli_timeout_the_socket_layer_cannot_use_is_refused(cli_timeout: float) -> None:
    """A negative timeout makes the socket layer raise, zero makes the
    connect non-blocking so a listening server reads as unreachable, and
    NaN, infinity or a value too large for the socket layer's clock get
    past the preflight only to fail the finish report."""
    with pytest.raises(VantageConfigError, match="--vantage-timeout"):
        resolve_report_timeout(cli_timeout=cli_timeout, ini_timeout=None)


@pytest.mark.parametrize("ini_timeout", ["-1", "0", "nan", "inf", "99999999999", "ten", "5s", ""])
def test_an_ini_timeout_that_is_not_a_usable_number_is_refused(ini_timeout: str) -> None:
    with pytest.raises(VantageConfigError, match="vantage_timeout"):
        resolve_report_timeout(cli_timeout=None, ini_timeout=ini_timeout)


class _ConfigDouble:
    """Hands `resolve_settings` an ini value of any type, as pytest before
    8.4 does: a list from the TOML table reaches the plugin unconverted."""

    def __init__(self, ini: dict[str, object]) -> None:
        self._ini = ini

    def getoption(self, name: str, default: object = None) -> object:
        return None

    def getini(self, name: str) -> object:
        return self._ini.get(name)


@pytest.mark.parametrize(
    ("ini", "option"),
    [
        ({"vantage_server": ["http://127.0.0.1:1"]}, "vantage_server"),
        ({"vantage_server": 8765}, "vantage_server"),
        ({"vantage_timeout": ["5"]}, "vantage_timeout"),
        ({"vantage_timeout": {"seconds": 5}}, "vantage_timeout"),
        ({"vantage_timeout": True}, "vantage_timeout"),
    ],
    ids=["address-list", "address-number", "timeout-list", "timeout-table", "timeout-bool"],
)
def test_an_ini_value_of_the_wrong_type_is_refused_naming_the_option(
    monkeypatch: pytest.MonkeyPatch, ini: dict[str, object], option: str
) -> None:
    """A TOML list or table where a URL or a number of seconds belongs is a
    configuration error naming the option, not an `AttributeError` or
    `TypeError` escaping `pytest_configure` as an INTERNALERROR. `true` is
    no number of seconds, although Python would read it as 1."""
    monkeypatch.delenv("VANTAGE_SERVER", raising=False)

    with pytest.raises(VantageConfigError, match=option):
        resolve_settings(_ConfigDouble(ini))  # type: ignore[arg-type]


# --- Reading the real configuration --------------------------------------------


def test_settings_read_the_ini_address_and_timeout(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    pytester.makeini("[pytest]\nvantage_server = http://ini-host:9000\nvantage_timeout = 4\n")

    settings = resolve_settings(pytester.parseconfig())

    assert (settings.address, settings.timeout) == ("http://ini-host:9000", 4.0)


def test_the_environment_address_outranks_the_ini_one(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VANTAGE_SERVER", "http://env-host:9000")
    pytester.makeini("[pytest]\nvantage_server = http://ini-host:9000\n")

    assert resolve_settings(pytester.parseconfig()).address == "http://env-host:9000"


def test_a_command_line_value_means_a_bad_ini_value_is_never_read(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the value that wins is read, so an override on the command line
    rescues a session from a broken committed value."""
    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    pytester.makeini("[pytest]\nvantage_server = ftp://nowhere\nvantage_timeout = ten\n")

    settings = resolve_settings(
        pytester.parseconfig("--vantage-server=http://cli-host:9000", "--vantage-timeout=3")
    )

    assert (settings.address, settings.timeout) == ("http://cli-host:9000", 3.0)


@pytest.mark.skipif(pytest.version_tuple < (9,), reason="the native [tool.pytest] table is new")
@pytest.mark.parametrize("value", ["5", "5.5"])
def test_a_numeric_timeout_in_the_native_toml_table_is_accepted(
    pytester: pytest.Pytester, value: str
) -> None:
    pytester.makepyprojecttoml(f"[tool.pytest]\nvantage_timeout = {value}\n")

    assert resolve_settings(pytester.parseconfig()).timeout == float(value)


# --- The project ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("cli", "env", "ini", "expected"),
    [
        ("from-cli", "from-env", "from-ini", "from-cli"),
        (None, "from-env", "from-ini", "from-env"),
        (None, None, "from-ini", "from-ini"),
        (None, None, None, DEFAULT_PROJECT),
    ],
)
def test_project_precedence_is_cli_then_environment_then_ini_then_default(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    cli: str | None,
    env: str | None,
    ini: str | None,
    expected: str,
) -> None:
    """The address's precedence: the most session-specific source first,
    and CI's environment over a value committed for everyone."""
    monkeypatch.delenv("VANTAGE_PROJECT", raising=False)
    if env is not None:
        monkeypatch.setenv("VANTAGE_PROJECT", env)
    if ini is not None:
        pytester.makeini(f"[pytest]\nvantage_project = {ini}\n")
    args = [f"--vantage-project={cli}"] if cli is not None else []

    assert resolve_project(pytester.parseconfig(*args)) == expected


@pytest.mark.parametrize(
    ("cli", "env", "expected"),
    [("from-cli", "Not A Project", "from-cli"), (None, "from-env", "from-env")],
    ids=["command line over a broken environment and ini", "environment over a broken ini"],
)
def test_a_project_that_wins_means_the_broken_ones_below_it_are_never_checked(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    cli: str | None,
    env: str,
    expected: str,
) -> None:
    """Only the winning source is read, so a broken committed value cannot
    stop a session that overrides it."""
    monkeypatch.setenv("VANTAGE_PROJECT", env)
    pytester.makeini("[pytest]\nvantage_project = ../Broken\n")
    args = [f"--vantage-project={cli}"] if cli is not None else []

    assert resolve_project(pytester.parseconfig(*args)) == expected


class _ProjectConfigDouble:
    """Hands `resolve_project` a command-line value and an ini value of any
    type, and counts every ini read."""

    def __init__(self, cli: object = None, ini: object = None) -> None:
        self._cli = cli
        self._ini = ini
        self.ini_reads: list[str] = []

    def getoption(self, name: str, default: object = None) -> object:
        return self._cli if name == "vantage_project" else default

    def getini(self, name: str) -> object:
        self.ini_reads.append(name)
        return self._ini


@pytest.mark.parametrize(
    ("cli", "env"), [("from-cli", None), (None, "from-env")], ids=["command line", "environment"]
)
def test_the_ini_project_is_not_even_read_when_another_source_wins(
    monkeypatch: pytest.MonkeyPatch, cli: str | None, env: str | None
) -> None:
    """A value pytest cannot convert raises from `getini` itself, so a
    session that overrides it must never ask."""
    monkeypatch.delenv("VANTAGE_PROJECT", raising=False)
    if env is not None:
        monkeypatch.setenv("VANTAGE_PROJECT", env)
    config = _ProjectConfigDouble(cli=cli, ini=["unused"])

    project = resolve_project(config)  # type: ignore[arg-type]

    assert project == (cli or env)
    assert config.ini_reads == []


def test_an_empty_project_is_unset_at_every_level(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty value passes the choice on rather than naming a project
    no server could have: `--vantage-project=` falls to the environment,
    an empty variable to the ini value, an empty ini value to `default`."""
    monkeypatch.setenv("VANTAGE_PROJECT", "")
    pytester.makeini("[pytest]\nvantage_project = from-ini\n")
    assert resolve_project(pytester.parseconfig("--vantage-project=")) == "from-ini"

    monkeypatch.setenv("VANTAGE_PROJECT", "from-env")
    assert resolve_project(pytester.parseconfig("--vantage-project=")) == "from-env"

    monkeypatch.setenv("VANTAGE_PROJECT", "")
    pytester.makeini("[pytest]\nvantage_project =\n")
    assert resolve_project(pytester.parseconfig("--vantage-project=")) == DEFAULT_PROJECT


@pytest.mark.parametrize("ini", [["foo"], 5, {"name": "foo"}, True], ids=repr)
def test_a_non_string_ini_project_is_refused_naming_the_option(
    monkeypatch: pytest.MonkeyPatch, ini: object
) -> None:
    """A TOML list, number or table is no project name: a configuration
    error naming where it came from, never a `TypeError` from the rule."""
    monkeypatch.delenv("VANTAGE_PROJECT", raising=False)

    with pytest.raises(VantageConfigError, match=r"^vantage_project ini value must be a project"):
        resolve_project(_ProjectConfigDouble(ini=ini))  # type: ignore[arg-type]


_RULE = (
    "must be a project name: 1 to 64 characters of a-z, 0-9, '.', '_' and '-', "
    "starting with a letter or a digit"
)


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("--vantage-project", "Foo"),
        ("--vantage-project", "a" * 65),
        ("VANTAGE_PROJECT", "../x"),
        ("VANTAGE_PROJECT", "-leading-dash"),
        ("vantage_project ini value", "has space"),
        ("vantage_project ini value", "caf\u00e9"),
    ],
)
def test_an_invalid_project_is_refused_naming_its_source_and_the_value(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, source: str, value: str
) -> None:
    """The message says which of three places to fix and what is there.
    Nothing is lower-cased or trimmed into shape: `Foo` read as `foo`
    would file the run in a project nobody named."""
    monkeypatch.delenv("VANTAGE_PROJECT", raising=False)
    args: list[str] = []
    if source == "--vantage-project":
        args = [f"--vantage-project={value}"]
    elif source == "VANTAGE_PROJECT":
        monkeypatch.setenv("VANTAGE_PROJECT", value)
    else:
        pytester.makeini(f"[pytest]\nvantage_project = {value}\n")

    with pytest.raises(VantageConfigError) as refused:
        resolve_project(pytester.parseconfig(*args))

    assert str(refused.value) == f"{source} {_RULE} (got {value!r})"


@pytest.mark.parametrize("value", ["a" * 64, "0", "a.b_c-d", "9lives"])
def test_every_name_the_rule_allows_is_taken_as_given(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.delenv("VANTAGE_PROJECT", raising=False)

    assert resolve_project(pytester.parseconfig(f"--vantage-project={value}")) == value


# --- The plugin's response to an invalid configuration --------------------------

_INVALID_CONFIGURATIONS = {
    "ftp scheme on the command line": (
        "",
        {},
        ["--vantage-server=ftp://127.0.0.1:1"],
        "--vantage-server",
    ),
    "port out of range": ("", {}, ["--vantage-server=http://127.0.0.1:99999"], "--vantage-server"),
    "no host": ("", {}, ["--vantage-server=http://:8765"], "--vantage-server"),
    "empty host label": (
        "",
        {},
        ["--vantage-server=http://vantage..local:8765"],
        "--vantage-server",
    ),
    "non-numeric port in the environment": (
        "",
        {"VANTAGE_SERVER": "http://127.0.0.1:abc"},
        [],
        "VANTAGE_SERVER",
    ),
    "ftp scheme in the ini file": ("vantage_server = ftp://127.0.0.1:1", {}, [], "vantage_server"),
    "credentials on the command line": (
        "",
        {},
        ["--vantage-server=http://user:s3cret-pw@127.0.0.1:1"],
        "--vantage-server",
    ),
    "query in the environment": (
        "",
        {"VANTAGE_SERVER": "http://127.0.0.1:1?x=1"},
        [],
        "VANTAGE_SERVER",
    ),
    "negative timeout on the command line": ("", {}, ["--vantage-timeout=-1"], "--vantage-timeout"),
    "zero timeout on the command line": ("", {}, ["--vantage-timeout=0"], "--vantage-timeout"),
    "nan timeout on the command line": ("", {}, ["--vantage-timeout=nan"], "--vantage-timeout"),
    "oversized timeout on the command line": (
        "",
        {},
        ["--vantage-timeout=99999999999"],
        "--vantage-timeout",
    ),
    "non-numeric timeout in the ini file": ("vantage_timeout = ten", {}, [], "vantage_timeout"),
    "negative timeout in the ini file": ("vantage_timeout = -1", {}, [], "vantage_timeout"),
    "upper-case project on the command line": (
        "",
        {},
        ["--vantage-project=Foo"],
        "--vantage-project",
    ),
    "path-like project in the environment": (
        "",
        {"VANTAGE_PROJECT": "../x"},
        [],
        "VANTAGE_PROJECT",
    ),
    "over-long project in the ini file": (
        f"vantage_project = {'a' * 65}",
        {},
        [],
        "vantage_project",
    ),
}


@pytest.mark.parametrize(
    ("ini_line", "environment", "args", "option"),
    list(_INVALID_CONFIGURATIONS.values()),
    ids=list(_INVALID_CONFIGURATIONS),
)
def test_an_invalid_configuration_is_a_usage_error_naming_the_option(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    ini_line: str,
    environment: dict[str, str],
    args: list[str],
    option: str,
) -> None:
    """One line naming the option and exit status 4, the same for every
    source and every kind of problem -- never an INTERNALERROR traceback."""
    monkeypatch.delenv("VANTAGE_SERVER", raising=False)
    monkeypatch.delenv("VANTAGE_PROJECT", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    if ini_line:
        pytester.makeini(f"[pytest]\n{ini_line}\n")
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest("--vantage", *args)

    output = result.stdout.str() + result.stderr.str()
    assert result.ret == pytest.ExitCode.USAGE_ERROR, output
    assert "INTERNALERROR" not in output
    assert "s3cret-pw" not in output
    assert any(line.startswith("ERROR: ") and option in line for line in result.stderr.lines), (
        output
    )


def test_an_invalid_ini_value_is_never_read_without_vantage(
    pytester: pytest.Pytester,
) -> None:
    """The differential counterpart: the same committed values do nothing
    at all unless recording was asked for."""
    pytester.makeini(
        "[pytest]\nvantage_server = ftp://127.0.0.1:1\nvantage_timeout = ten\n"
        "vantage_project = Not/A/Project\n"
    )
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest()

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, warnings=0)


@pytest.mark.skipif(pytest.version_tuple < (9,), reason="the native [tool.pytest] table is new")
def test_a_numeric_native_toml_timeout_lets_the_session_run(pytester: pytest.Pytester) -> None:
    """``vantage_timeout = 5`` is the natural way to write seconds in the
    native table; it must reach the preflight, not crash reading it. The
    refused connection then warns once and the suite runs unrecorded."""
    pytester.makepyprojecttoml("[tool.pytest]\nvantage_timeout = 5\n")
    pytester.makepyfile(test_sample=_PASSING_TEST)
    address = _closed_port_address()

    result = pytester.runpytest("--vantage", f"--vantage-server={address}")

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, warnings=1)
    (warned,) = vantage_warnings(result)
    assert "cannot reach" in warned
