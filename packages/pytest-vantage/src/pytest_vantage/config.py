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


__all__ = ["VantageConfigError", "resolve_and_validate_address"]
