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
from pytest_vantage.boundary import VantageWarning
from pytest_vantage.config import (
    VantageConfigError,
    resolve_failure_text_capture,
    resolve_metadata_capture,
    resolve_report_timeout,
    resolve_server_address,
    resolve_settings,
)

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


@pytest.mark.parametrize("ini_timeout", [5, 5.5, "2.5", "0.001"])
def test_an_ini_timeout_may_be_a_number_or_numeric_text(ini_timeout: str | float) -> None:
    """pytest's native TOML table hands `getini` an int or a float; the
    ini-file forms hand it text. Both mean seconds."""
    assert resolve_report_timeout(cli_timeout=None, ini_timeout=ini_timeout) == float(ini_timeout)


@pytest.mark.parametrize("cli_timeout", [-1.0, -0.5, 0.0, math.nan, math.inf])
def test_a_cli_timeout_that_is_not_finite_and_positive_is_refused(cli_timeout: float) -> None:
    """A negative timeout makes the socket layer raise, zero makes the
    connect non-blocking so a listening server reads as unreachable, and
    NaN or infinity get past the preflight only to fail the finish report."""
    with pytest.raises(VantageConfigError, match="--vantage-timeout"):
        resolve_report_timeout(cli_timeout=cli_timeout, ini_timeout=None)


@pytest.mark.parametrize("ini_timeout", ["-1", "0", "nan", "inf", "ten", "5s", ""])
def test_an_ini_timeout_that_is_not_a_finite_positive_number_is_refused(ini_timeout: str) -> None:
    with pytest.raises(VantageConfigError, match="vantage_timeout"):
        resolve_report_timeout(cli_timeout=None, ini_timeout=ini_timeout)


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
    "negative timeout on the command line": ("", {}, ["--vantage-timeout=-1"], "--vantage-timeout"),
    "zero timeout on the command line": ("", {}, ["--vantage-timeout=0"], "--vantage-timeout"),
    "nan timeout on the command line": ("", {}, ["--vantage-timeout=nan"], "--vantage-timeout"),
    "non-numeric timeout in the ini file": ("vantage_timeout = ten", {}, [], "vantage_timeout"),
    "negative timeout in the ini file": ("vantage_timeout = -1", {}, [], "vantage_timeout"),
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
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    if ini_line:
        pytester.makeini(f"[pytest]\n{ini_line}\n")
    pytester.makepyfile(test_sample=_PASSING_TEST)

    result = pytester.runpytest("--vantage", *args)

    output = result.stdout.str() + result.stderr.str()
    assert result.ret == pytest.ExitCode.USAGE_ERROR, output
    assert "INTERNALERROR" not in output
    assert any(line.startswith("ERROR: ") and option in line for line in result.stderr.lines), (
        output
    )


def test_an_invalid_ini_value_is_never_read_without_vantage(
    pytester: pytest.Pytester,
) -> None:
    """The differential counterpart: the same committed values do nothing
    at all unless recording was asked for."""
    pytester.makeini("[pytest]\nvantage_server = ftp://127.0.0.1:1\nvantage_timeout = ten\n")
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

    with pytest.warns(VantageWarning, match="cannot reach"):
        result = pytester.runpytest("--vantage", f"--vantage-server={address}")

    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1)


# --- Capture opt-ins -------------------------------------------------------------


def test_resolve_failure_text_capture_true_only_when_activated_and_cli_opt_in() -> None:
    """The exhaustive truth table: only `activated=True, cli_opt_in=True`
    resolves `True` -- capture is absent by default."""
    assert resolve_failure_text_capture(activated=False, cli_opt_in=False) is False
    assert resolve_failure_text_capture(activated=False, cli_opt_in=True) is False
    assert resolve_failure_text_capture(activated=True, cli_opt_in=False) is False
    assert resolve_failure_text_capture(activated=True, cli_opt_in=True) is True


def test_resolve_metadata_capture_true_only_when_activated_and_cli_opt_in() -> None:
    """The exhaustive truth table: only `activated=True, cli_opt_in=True`
    resolves `True` -- capture is absent by default."""
    assert resolve_metadata_capture(activated=False, cli_opt_in=False) is False
    assert resolve_metadata_capture(activated=False, cli_opt_in=True) is False
    assert resolve_metadata_capture(activated=True, cli_opt_in=False) is False
    assert resolve_metadata_capture(activated=True, cli_opt_in=True) is True
