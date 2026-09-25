"""The plugin POSTs to a URL that can come from CLI, ini or an environment
variable, so the scheme allow-list is the whole defence against an
unintended outbound request. Only ``http`` and ``https`` are ever accepted.

The bare-host case is the sneaky one: ``urlparse("localhost:8765")`` does
not raise -- it happily returns a scheme of ``"localhost"`` and a path of
``"8765"``. It parsed. That is not the same as being valid, and the
allow-list catches it the same way it catches ``ftp://`` -- by rejecting
whatever scheme it actually got.

Beyond the scheme, an address must name a host and, if it gives a port, a
usable one. Anything else is refused here, as a configuration error, rather
than reaching the socket layer where it would either probe a host nobody
named or raise something the preflight does not expect.
"""

from __future__ import annotations

import socket

import pytest
from pytest_vantage.config import VantageConfigError, resolve_and_validate_address
from pytest_vantage.plugin import _preflight_reachable


def test_file_scheme_is_refused() -> None:
    with pytest.raises(VantageConfigError, match="file"):
        resolve_and_validate_address("file:///etc/passwd")


def test_ftp_scheme_is_refused() -> None:
    with pytest.raises(VantageConfigError, match="ftp"):
        resolve_and_validate_address("ftp://example.com")


def test_bare_host_with_no_scheme_is_refused() -> None:
    """``urlparse`` parses this without error -- scheme ``"localhost"``,
    path ``"8765"`` -- so "it parsed" alone must not be read as "it is
    valid". The allow-list rejects it anyway, for the same reason it
    rejects any other scheme outside {http, https}.
    """
    with pytest.raises(VantageConfigError, match="localhost"):
        resolve_and_validate_address("localhost:8765")


@pytest.mark.parametrize(
    "address",
    ["http://", "https://", "http:///x", "http:///localhost:8765", "http://:8765"],
)
def test_an_address_without_a_host_is_refused(address: str) -> None:
    """With no host, the socket layer resolves ``None`` to loopback, so the
    preflight would probe a server nobody named. ``http:///localhost:8765``
    is the realistic typo: the host lands in the path.
    """
    with pytest.raises(VantageConfigError, match="host") as excinfo:
        resolve_and_validate_address(address)
    assert repr(address) in str(excinfo.value)


@pytest.mark.parametrize(
    "address",
    [
        "http://127.0.0.1:99999",
        "http://127.0.0.1:0",
        "http://127.0.0.1:abc",
        "http://vantage..local:8765",
        "http://" + "a" * 64 + ".local:8765",
        "http://[::1:8765",
    ],
)
def test_a_malformed_port_or_host_is_refused(address: str) -> None:
    """Each of these used to pass validation and then raise a ``ValueError``
    (a port out of range or not a number, an empty or over-long host label,
    an unterminated IPv6 bracket) from inside the preflight."""
    with pytest.raises(VantageConfigError) as excinfo:
        resolve_and_validate_address(address)
    assert repr(address) in str(excinfo.value)


def test_the_message_names_the_option_the_address_came_from() -> None:
    with pytest.raises(VantageConfigError, match="^VANTAGE_SERVER "):
        resolve_and_validate_address("ftp://example.com", option="VANTAGE_SERVER")


@pytest.mark.parametrize(
    "address",
    [
        "http://127.0.0.1:8765",
        "https://vantage.example.com",
        "http://[::1]:8765",
        "http://localhost",
        "https://vantage.example.com:443/",
    ],
)
def test_a_well_formed_address_is_returned_unchanged(address: str) -> None:
    assert resolve_and_validate_address(address) == address


def test_the_preflight_treats_an_unencodable_host_as_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation refuses such a host first; this is the backstop for one
    that gets past it. The socket layer IDNA-encodes the host and raises
    ``UnicodeError``, which is not an ``OSError``: it must still read as
    "cannot reach", never escape ``pytest_configure``.
    """

    def _raise(*args: object, **kwargs: object) -> socket.socket:
        raise UnicodeError("label empty or too long")

    monkeypatch.setattr(socket, "create_connection", _raise)

    assert _preflight_reachable("http://example.com:8765", timeout=1.0) is False
