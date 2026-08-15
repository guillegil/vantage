"""Threat matrix "Outbound request target" (design.md D6/threat matrix):
the plugin POSTs to a URL that can come from CLI, ini or an environment
variable, so the scheme allow-list is the whole defence. Only ``http`` and
``https`` are ever accepted. No numbered requirement drives this row of the
matrix, so these tests carry no ``@pytest.mark.req`` -- the same convention
PR2's rot-detector used for a threat-matrix test with no requirement of its
own (design.md, tasks.md Review Workload Forecast).

The bare-host case is the sneaky one: ``urlparse("localhost:8765")`` does
not raise -- it happily returns a scheme of ``"localhost"`` and a path of
``"8765"``. It parsed. That is not the same as being valid, and the
allow-list catches it the same way it catches ``ftp://`` -- by rejecting
whatever scheme it actually got.
"""

from __future__ import annotations

import pytest
from pytest_vantage.config import VantageConfigError, resolve_and_validate_address


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


def test_http_is_accepted() -> None:
    assert resolve_and_validate_address("http://127.0.0.1:8765") == "http://127.0.0.1:8765"


def test_https_is_accepted() -> None:
    assert resolve_and_validate_address("https://vantage.example.com") == (
        "https://vantage.example.com"
    )
