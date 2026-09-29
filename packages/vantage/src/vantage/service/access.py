"""Who is asking, and whether they may: the dependency every route declares
but the capability advertisement and the interface document.

**A server with no user is open.** `vantage` gives every database it
serves an admin before serving it (`cli.py`), except one pytest-vantage's
local store made, which holds one person's runs on a test machine: served
with no user, it serves anyone, so browsing it needs no token. Once a user
exists every route here needs `Authorization: Bearer <token>`, or a browser
session's cookie, holding a live, unexpired token of an enabled user whose
grant covers the route's scope (`Grant.allows`).

**A browser signs in for a session cookie instead** (`SESSION_COOKIE`,
`routes/session.py`): HttpOnly, so page script never holds the token, and
holding a login token. A request is authorized by one credential, never
two: an `Authorization` header decides alone, whatever the cookie says,
so the plugin and CI act as their tokens do everywhere; the cookie is read
only when there is no header, and never on an open server, where it could
authenticate nobody and a stale one must not refuse anybody. Before the
cookie is looked up the request must come from vantage's own pages
(`require_same_origin`): SameSite keeps a cookie from other sites but not
from another port of the same host, so a page served there could otherwise
revoke tokens with an admin's session. The browser says where a request
comes from in `Sec-Fetch-Site`, which page script cannot set; a request a
browser marks `same-origin` passes, and so does a read it marks `none` (an
address typed or bookmarked) or does not mark at all (a script, or an old
browser, which cannot read a cross-origin answer anyway). There is no
fallback to `Origin` or `Host`, which a proxy may rewrite: every browser
that keeps a Secure cookie sends the header, over HTTPS and on loopback;
over plain HTTP to another host it sends none and keeps no cookie either.

**The first user closes a running server.** A user is made with `vantage
user add`, often against a database a server is already serving, so while
the server is open each request asks the store whether a user exists yet.
Users are never deleted, so once one does, `app.state.access_required`
remembers it and the store is not asked again.

**Managing users, tokens and members needs a user, always**
(`requires_admin_token`, and the members dependencies). On an open server
the anonymous caller may do anything else, but not this:
the one thing it could do here is make the first user, which only the
`vantage` command does. It is refused for who it is, never for what the
store says, so a request racing the first `vantage user add` cannot slip
through between the two, reads included.

**Logging in, signing a browser in and changing a password take a name
and a password, not a token** (`requires_closed_server`), so they declare
no scope: the password is the credential, and a stolen login token cannot
take it over. On an open server nobody has a password, and all three
answer `409 open_server`.

**Within a project, a request needs a role there as well as its scope**
(`require_role`): viewer to read, editor to record and to change section
definitions, owner to manage members. The role is `effective_role`'s --
owner for an admin, editor in `default`, otherwise the caller's member row
-- read from the store on every request and never kept in `app.state`, so
a change applies from the next request on every server process; a request
already authorized finishes on the role it read, as one authorized just
before a token's revocation does. Scopes and roles only narrow each other.
The anonymous caller of an open server passes every role check, but the
members routes refuse it (`OpenServerError`): nobody is a member of
anything until users exist.

**Every refusal of who asks comes before every refusal of what is asked.**
A project in a path (`_project_access`) is resolved once the caller is
authorized -- `401` for no credential, `403 cross_site_request` for a
session cookie from elsewhere, `401` for a token that is not valid, `403
insufficient_scope`, then, on the members routes, `409 open_server` -- so
a caller who may not read learns nothing of which projects exist; then
`404 unknown_project`, then `403 not_a_member` or `403 insufficient_role`,
and only then the route's own checks of its body, query and path. A name
no project can have is answered without asking the store; one that can is
looked up, and projects are never deleted, so a project found stays found
for the rest of the request. A run id in a path
(`_run_access`) is checked the same way: the caller first, then the id's
shape (`422`), the run (`404 unknown_run`), the caller's role in the run's
project, and only then the route's query. A run id the plugin makes is a
random `uuid4`, so answering `unknown_run` before a `403` reveals nothing
guessable, and neither `403` names the project. A report names its project in its body,
so `POST /runs` checks the role after the report is read and validated
(`ingest`'s `admit`).

**A token in the header is checked wherever it is sent.** On an open
server no token authenticates, since tokens belong to users; a request
carrying one is refused rather than served as if it carried none, so a
client that means to act as a user finds out it is not one. A session
cookie is not: the browser sends it unasked, and to every port of its
host, so the one an open server receives may be another vantage's.

**A plain `def`.** Authenticating reads the store, which never happens on
the event loop, so FastAPI runs the dependency in its threadpool, as it runs
the routes that read the store. On `POST /runs` it runs before the body is
read, so a request that may not record is refused unread.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Path, Request

from vantage.core.domain.access import (
    ADMIN_SCOPE,
    MANAGE_SCOPE,
    READ_SCOPE,
    RECORD_SCOPE,
    token_digest,
    well_formed_token,
)
from vantage.core.domain.execution import IDENTITY_PATTERN
from vantage.core.domain.projects import (
    DEFAULT_PROJECT,
    EDITOR_ROLE,
    OWNER_ROLE,
    VIEWER_ROLE,
    Project,
    can_name_a_project,
    effective_role,
    role_covers,
)
from vantage.core.ports.storage import ExecutionStore, RunDetail
from vantage.service.errors import (
    CrossSiteRequestError,
    InsufficientRoleError,
    InsufficientScopeError,
    NoSuchProjectError,
    NotAMemberError,
    OpenServerError,
    UnauthenticatedError,
    UnknownRunError,
)

SESSION_COOKIE = "__Host-vantage_session"
"""The cookie a browser session's login token travels in. The `__Host-`
prefix makes browsers keep it only when it is Secure, for `Path=/` and with
no `Domain`, so no other host -- a sibling under the same domain included
-- can set or overwrite it."""

# The methods that only read, which a browser may send unmarked or marked
# `none` and still hold the session cookie.
_READING_METHODS = frozenset({"GET", "HEAD"})


@dataclass(frozen=True, slots=True)
class Caller:
    """Who a request acts as: the user its token belongs to, or `None` on
    a server that has no user, whether that user is an admin now, whether
    the token may administer now -- it holds the admin scope and its user
    is an admin -- and when the token stops authenticating, set for a login
    token alone."""

    user: str | None
    admin: bool = False
    administers: bool = False
    expires_at: datetime | None = None


def _server_open(app: FastAPI, store: ExecutionStore) -> bool:
    if app.state.access_required:
        return False
    if store.access_required():
        app.state.access_required = True
        return False
    return True


def require_same_origin(request: Request) -> None:
    """Refuse `request` with `CrossSiteRequestError` unless the browser
    marked it `Sec-Fetch-Site: same-origin`, or it reads and was marked
    `none` or not marked at all. The header's value is never repeated."""
    site = request.headers.get("sec-fetch-site")
    if site == "same-origin":
        return
    if request.method in _READING_METHODS and site in (None, "none"):
        return
    raise CrossSiteRequestError()


def _authorize_token(store: ExecutionStore, token: str, scope: str) -> Caller:
    # A token that could never be one is refused without asking the store.
    if not well_formed_token(token):
        raise UnauthenticatedError.invalid()
    grant = store.authenticate(token_digest(token), now=datetime.now(timezone.utc))
    if grant is None:
        raise UnauthenticatedError.invalid()
    if not grant.allows(scope):
        raise InsufficientScopeError(scope)
    return Caller(
        user=grant.user,
        admin=grant.admin,
        administers=grant.allows(ADMIN_SCOPE),
        expires_at=grant.expires_at,
    )


def authorize(request: Request, scope: str) -> Caller:
    """The caller of `request`, if it may act within `scope`; otherwise
    `UnauthenticatedError`, `CrossSiteRequestError` or
    `InsufficientScopeError`, in the order the module docstring gives."""
    store: ExecutionStore = request.app.state.store
    header = request.headers.get("authorization")
    if header is not None:
        scheme, _, token = header.strip().partition(" ")
        if scheme.lower() != "bearer":
            raise UnauthenticatedError.invalid()
        return _authorize_token(store, token.strip(), scope)
    if _server_open(request.app, store):
        return Caller(user=None)
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie is None:
        raise UnauthenticatedError.missing()
    require_same_origin(request)
    return _authorize_token(store, cookie, scope)


def _requires(scope: str) -> Callable[[Request], Caller]:
    def dependency(request: Request) -> Caller:
        return authorize(request, scope)

    dependency.__name__ = f"requires_{scope}"
    return dependency


requires_read = _requires(READ_SCOPE)
"""Reading runs, results, history, sections and members; within a project,
`requires_read_project` or `requires_read_run` adds the role."""

requires_record = _requires(RECORD_SCOPE)
"""Sending a report or a heartbeat; the role is checked once the project
is known (`requires_record_run`, and `ingest`'s `admit`)."""

requires_admin = _requires(ADMIN_SCOPE)
"""Changing what every user shares that no project path names: adding a
project."""


def require_role(store: ExecutionStore, caller: Caller, project: str, needed: str) -> None:
    """Refuse `caller` unless their role in `project`, which exists, covers
    `needed`: `NotAMemberError` for none, `InsufficientRoleError` for one
    below it. The anonymous caller of an open server passes. Only a member
    row needs the store: an admin's role and every user's in `default` are
    `effective_role`'s alone."""
    if caller.user is None:
        return
    stored = None
    if not caller.admin and project != DEFAULT_PROJECT:
        stored = store.get_member_role(caller.user, project=project)
    held = effective_role(project, admin=caller.admin, stored=stored)
    if held is None:
        raise NotAMemberError()
    if not role_covers(held, needed):
        raise InsufficientRoleError(needed)


def _project_access(
    scope: str, role: str, *, needs_user: bool = False
) -> Callable[[Request, str], Project]:
    """A dependency resolving the project the path names for a caller
    holding `scope` and at least `role` in it -- and, with `needs_user`,
    refusing an open server's anonymous caller -- in the order the module
    docstring gives."""

    def dependency(request: Request, project: str = Path()) -> Project:
        caller = authorize(request, scope)
        if needs_user and caller.user is None:
            raise OpenServerError()
        if not can_name_a_project(project):
            raise NoSuchProjectError()
        store: ExecutionStore = request.app.state.store
        found = store.get_project(project)
        if found is None:
            raise NoSuchProjectError()
        require_role(store, caller, found.name, role)
        return found

    dependency.__name__ = f"requires_{scope}_project_as_{role}"
    return dependency


requires_read_project = _project_access(READ_SCOPE, VIEWER_ROLE)
"""Reading within the project the path names: its runs, a test's history
and its sections."""

requires_edit_project = _project_access(MANAGE_SCOPE, EDITOR_ROLE)
"""Changing what the project the path names shares: its sections."""

requires_read_members = _project_access(READ_SCOPE, VIEWER_ROLE, needs_user=True)
"""Listing the members of the project the path names."""

requires_manage_members = _project_access(MANAGE_SCOPE, OWNER_ROLE, needs_user=True)
"""Setting or removing a member of the project the path names."""


def _run_access(
    authorizes: Callable[[Request], Caller], role: str
) -> Callable[[Request, Caller, str], RunDetail]:
    """A dependency resolving the run the path's `run_id` names for a
    caller `authorizes` lets through with at least `role` in the run's
    project. The caller is a sub-dependency, which FastAPI resolves before
    this one's own path parameter, so a caller refused for who they are is
    never told the id is malformed; a route taking it declares no `run_id`
    of its own, and reads the id from the `RunDetail`."""

    def dependency(
        request: Request,
        caller: Caller = Depends(authorizes),
        run_id: str = Path(pattern=IDENTITY_PATTERN),
    ) -> RunDetail:
        store: ExecutionStore = request.app.state.store
        detail = store.get_run_detail(run_id)
        if detail is None:
            raise UnknownRunError()
        require_role(store, caller, detail.project, role)
        return detail

    dependency.__name__ = f"{authorizes.__name__}_run_as_{role}"
    return dependency


requires_read_run = _run_access(requires_read, VIEWER_ROLE)
"""Reading one run: its detail, metadata, results and section summary."""

requires_record_run = _run_access(requires_record, EDITOR_ROLE)
"""Keeping one run alive: its heartbeat."""


def requires_admin_token(request: Request) -> Caller:
    """Managing users and tokens: a live admin-scope token of an enabled
    admin user, on every server. The anonymous caller an open server lets
    through is refused with `OpenServerError`."""
    caller = authorize(request, ADMIN_SCOPE)
    if caller.user is None:
        raise OpenServerError()
    return caller


async def requires_same_origin(request: Request) -> None:
    """Signing in or out of a browser session: `require_same_origin`, which
    these routes ask whether or not a cookie is sent, so another port's
    page can neither sign a browser in as someone else nor sign it out.
    `async`, since it reads a header alone and is not worth a thread."""
    require_same_origin(request)


def requires_closed_server(request: Request) -> None:
    """Logging in, signing a browser in or changing a password: refused
    with `OpenServerError` while the database has no user, before the body
    is read. Any `Authorization` header is ignored, since the body carries
    the credentials."""
    store: ExecutionStore = request.app.state.store
    if _server_open(request.app, store):
        raise OpenServerError()


__all__ = [
    "SESSION_COOKIE",
    "Caller",
    "authorize",
    "require_role",
    "require_same_origin",
    "requires_admin",
    "requires_admin_token",
    "requires_closed_server",
    "requires_edit_project",
    "requires_manage_members",
    "requires_read",
    "requires_read_members",
    "requires_read_project",
    "requires_read_run",
    "requires_record",
    "requires_record_run",
    "requires_same_origin",
]
