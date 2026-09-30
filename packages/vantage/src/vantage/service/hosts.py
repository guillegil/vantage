"""Answer only the host names the server is known by.

**A pure-ASGI middleware, like `service/web.py`'s.** `KnownHosts` reads
the request's `Host` and, when the rule (`core/config/hosts.py`) does not
answer it, refuses with `421 misdirected_request` on every path and method
before the web client or any route runs, so a page that pointed its own
name at this machine can neither read the page nor ask the API anything.
It sits inside `SecurityHeaders`, so a refusal carries every header an
answer does, and outside `WebClient` and the router. The header is never
repeated in the refusal, and a request carrying no `Host`, or more than
one, is refused too.

**Only when the command asks.** `create_app` adds it only when given a
rule, which `cli.py` resolves from the bind address and the names allowed;
an app built for a test answers every name, `TestClient`'s `testserver`
included.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from vantage.core.config.hosts import HostRule
from vantage.service.errors import MisdirectedRequestError, rejection_response


class KnownHosts:
    """Refuse every HTTP request whose `Host` `rule` does not answer."""

    def __init__(self, app: ASGIApp, rule: HostRule) -> None:
        self.app = app
        self.rule = rule

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Nothing here speaks WebSocket, and the router refuses one itself;
        # the lifespan carries no request.
        if scope["type"] != "http" or self.rule.answers(_host(scope)):
            await self.app(scope, receive, send)
            return
        await rejection_response(MisdirectedRequestError())(scope, receive, send)


def _host(scope: Scope) -> str | None:
    """The one `Host` a request carried, or `None` for none or several."""
    values = [value for name, value in scope["headers"] if name.lower() == b"host"]
    if len(values) != 1:
        return None
    return str(values[0].decode("latin-1"))


__all__ = ["KnownHosts"]
