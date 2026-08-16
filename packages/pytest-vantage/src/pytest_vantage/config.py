"""Where the plugin is allowed to send a report (design.md D6, threat matrix
"Outbound request target").

The address the plugin POSTs to can arrive from ``--vantage-server``, the
``vantage_server`` ini value or the ``VANTAGE_SERVER`` environment variable
-- any of which can be set from outside the developer's control, for
example by CI. An allow-list of exactly two schemes, ``http`` and
``https``, is the whole defence: anything else is refused before
``urllib``'s ``file:``/``ftp:`` handlers are ever reached.
"""

from __future__ import annotations

from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# design.md D11: "8765 is a memorable unregistered high port; the plugin's
# default address is the same, so --vantage alone means something."
_DEFAULT_ADDRESS = "http://127.0.0.1:8765"
# design.md D6: report_timeout, the bound on every socket operation of the
# report itself (distinct from connect_timeout, PR12's preflight probe).
_DEFAULT_REPORT_TIMEOUT = 10.0


class VantageConfigError(ValueError):
    """A configured value cannot be used -- the session should not proceed."""


def resolve_and_validate_address(address: str) -> str:
    """Return ``address`` unchanged if its scheme is ``http`` or ``https``.

    Raises ``VantageConfigError`` naming the offending scheme otherwise.
    ``urlparse`` does not raise on a bare host with no scheme at all (e.g.
    ``"localhost:8765"`` parses to scheme ``"localhost"``, path ``"8765"``)
    -- it having parsed is not the same as it being valid, and the
    allow-list rejects that case the same way it rejects ``ftp://``: by
    scheme, not by success of the parse.
    """
    scheme = urlparse(address).scheme
    if scheme not in _ALLOWED_SCHEMES:
        raise VantageConfigError(
            f"vantage server address {address!r} must use http:// or https:// "
            f"(got scheme {scheme!r})"
        )
    return address


def resolve_server_address(
    *, cli_url: str | None, env_url: str | None, ini_url: str | None
) -> str:
    """Where to report to: `--vantage-server` > `VANTAGE_SERVER` > the
    `vantage_server` ini value > the default (`http://127.0.0.1:8765`).

    CLI over environment over ini mirrors the server's own precedence
    (`resolve_server_config`, design.md D11): the most explicit,
    session-specific source wins, and an environment variable -- how CI
    configures a container -- outranks a value committed to `pyproject.toml`
    or `pytest.ini`, which everyone who checks the project out shares.

    Validates the resolved address's scheme before returning it (design.md
    D6, threat matrix "Outbound request target") -- every caller gets a
    validated address, never a raw configured string.
    """
    address = cli_url or env_url or ini_url or _DEFAULT_ADDRESS
    return resolve_and_validate_address(address)


def resolve_report_timeout(*, cli_timeout: float | None, ini_timeout: str | None) -> float:
    """The bound on the reporting request: `--vantage-timeout` > the
    `vantage_timeout` ini value > the default (`10.0` seconds, design.md
    D6's `report_timeout`). No environment variable is defined for the
    timeout -- only the address has one.
    """
    if cli_timeout is not None:
        return cli_timeout
    if ini_timeout is not None:
        return float(ini_timeout)
    return _DEFAULT_REPORT_TIMEOUT


__all__ = [
    "VantageConfigError",
    "resolve_and_validate_address",
    "resolve_report_timeout",
    "resolve_server_address",
]
