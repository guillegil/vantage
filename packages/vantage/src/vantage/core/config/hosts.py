"""Which host names a server answers, read from a request's `Host`.

**Why a server checks at all.** A browser decides what a page may read by
the name in its address, not by the address the name resolves to. A page
on any domain can point its own name at 127.0.0.1 once it has loaded (DNS
rebinding); the browser then sends that page's requests to a vantage on
this machine's loopback with the page's name in `Host`, and hands the page
the answers as its own. Against a server with no user -- a local store's
database -- that is every run it holds, read and written from a stranger's
page. A server that answers only the names it is known by refuses those
requests before anything else sees them.

**What a rule answers** (`HostRule.answers`): `localhost`; any IP literal,
IPv4 or bracketed IPv6, since a page cannot make its own address an IP
literal while rebinding a name, and a page whose address is one reaches
that address alone; and the names an operator allows, such as a reverse
proxy's public name. A name is compared in any case, with one trailing dot
dropped; the port is ignored, since the port a browser names is the one it
connected to.

**When there is a rule** (`host_rule`): always on a loopback bind -- a
socket holding an address in 127.0.0.0/8 or ::1 -- which is where a
rebinding page reaches; on any other bind only when names are allowed,
since a server bound wide is reached by names nobody here can list. The
address judged is the one the listening socket holds, never the text the
server was told to bind: `127.1`, `2130706433`, `ip6-localhost` or the
machine's own name, which Debian's /etc/hosts maps to 127.0.1.1, all bind
loopback without reading as it.

Pure: standard library only, no name is ever resolved.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

LOCALHOST = "localhost"

# A label of a host name: letters, digits and hyphens, not at either end,
# and underscores, which container runtimes put in the names they resolve.
_LABEL = r"(?!-)[a-z0-9_-]{1,63}(?<!-)"
_HOST_NAME = re.compile(rf"\A{_LABEL}(?:\.{_LABEL})*\Z")
_MAX_HOST_NAME_CHARS = 253

# The port of a `Host`, which may be empty.
_PORT = re.compile(r"\A[0-9]*\Z")


class HostNameError(ValueError):
    """A value that is not a host name a rule can allow."""


def allowed_host_name(value: str) -> str:
    """`value` as a rule compares it -- lower-cased, without one trailing
    dot -- or `HostNameError` when it is not a host name alone: a scheme, a
    port, a path, a wildcard, a character no host name holds, or nothing."""
    name = _normalised(value.strip())
    if name is None or not _is_host_name(name):
        raise HostNameError(
            f"{value!r} is not a host name: give the name alone, as it appears in the address "
            "a browser or the plugin uses, with no scheme, port, path or wildcard"
        )
    return name


@dataclass(frozen=True, slots=True)
class HostRule:
    """The host names a server answers: `localhost`, every IP literal, and
    `names`, each as `allowed_host_name` returns it."""

    names: frozenset[str]

    def answers(self, host: str | None) -> bool:
        """Whether a request whose `Host` header is `host` -- `None` when it
        carried none -- names this server."""
        name = request_host(host)
        if name is None:
            return False
        return name == LOCALHOST or name in self.names or _is_ip_literal(name)


def host_rule(bound: str, allowed: frozenset[str]) -> HostRule | None:
    """The rule a server whose listening socket holds the address `bound`,
    allowing the names `allowed`, answers by; `None` for a wide bind
    allowing none, which answers every name."""
    if not allowed and not is_loopback(bound):
        return None
    return HostRule(frozenset(allowed))


def is_loopback(bound: str) -> bool:
    """Whether `bound`, an address as a socket reports the one it holds, is
    a loopback address: in 127.0.0.0/8, or ::1 (an IPv4-mapped loopback
    address too). A name is none, since what it binds is only known once
    bound."""
    try:
        address = ipaddress.ip_address(bound)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def request_host(host: str | None) -> str | None:
    """The host a `Host` header names, without its port, lower-cased and
    without one trailing dot -- an IPv6 literal with its brackets -- or
    `None` for no header, an empty one, or one no request line could carry."""
    if not host:
        return None
    if host.startswith("["):
        literal, bracket, rest = host[1:].partition("]")
        if not bracket or not _is_port(rest) or not _is_ipv6(literal):
            return None
        return f"[{literal.lower()}]"
    name, colon, port = host.partition(":")
    if colon and not _PORT.match(port):
        return None
    return _normalised(name)


def _normalised(name: str) -> str | None:
    name = name.lower()
    if name.endswith("."):
        name = name[:-1]
    return name or None


def _is_host_name(name: str) -> bool:
    return len(name) <= _MAX_HOST_NAME_CHARS and _HOST_NAME.match(name) is not None


def _is_port(rest: str) -> bool:
    return rest == "" or (rest.startswith(":") and _PORT.match(rest[1:]) is not None)


def _is_ipv6(literal: str) -> bool:
    try:
        ipaddress.IPv6Address(literal)
    except ValueError:
        return False
    return True


def _is_ip_literal(name: str) -> bool:
    """Whether `name`, as `request_host` returns it, is an IP literal: a
    bracketed IPv6 address, or an IPv4 one in dotted decimal, the only form
    a browser writes."""
    if name.startswith("["):
        return True
    try:
        ipaddress.IPv4Address(name)
    except ValueError:
        return False
    return True


__all__ = [
    "LOCALHOST",
    "HostNameError",
    "HostRule",
    "allowed_host_name",
    "host_rule",
    "is_loopback",
    "request_host",
]
