"""Projects: what a run belongs to, and the rule a project's name follows.

Every run belongs to one project, named by the report that creates it and
never changed afterwards. The test catalogue, a test's history, the section
definitions and the run list are each read within one project, so two
projects never mix their tests, however alike their node ids.

Nothing renames or deletes a project, so "project X exists" only ever turns
from false to true: a check that one exists stays true, and every row that
names a project keeps naming one. `default` exists in every database from
the moment it is created, for the runs that name no project.

Once a database has users, a user acts in a project with a role: a
`viewer` reads it, an `editor` also records runs and edits its section
definitions, an `owner` also manages its members. A member's role is a
stored row, with two rules no row holds (`effective_role`): every user is
an editor of `default`, which takes no rows, and an admin acts as an owner
of every project. A token's scopes still apply on top: scopes and roles
only narrow each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

DEFAULT_PROJECT = "default"
"""The project of a run whose report names none."""

PROJECT_NAME_PATTERN = r"\A[a-z0-9][a-z0-9._-]{0,63}\Z"
"""The user-name rule: lower case, so two names never differ by case alone;
at most 64 characters, so it is indexed as it is; nothing a URL path
segment or a command line must quote."""

_PROJECT_NAME_RE = re.compile(PROJECT_NAME_PATTERN)


class InvalidProjectNameError(ValueError):
    """A project name that `PROJECT_NAME_PATTERN` does not match."""


def check_project_name(name: str) -> str:
    """`name`, if it can name a project."""
    if not _PROJECT_NAME_RE.match(name):
        raise InvalidProjectNameError(
            "a project name is 1 to 64 characters of a-z, 0-9, '.', '_' and '-', "
            "starting with a letter or a digit"
        )
    return name


def can_name_a_project(name: str) -> bool:
    """Whether `name` could name a project. One that cannot names none, and
    is answered without asking the store, which also keeps U+0000 from
    every adapter."""
    return _PROJECT_NAME_RE.match(name) is not None


@dataclass(frozen=True, slots=True)
class Project:
    """One project."""

    name: str
    created_at: datetime


VIEWER_ROLE = "viewer"
"""Reads the project: its runs, history, section definitions and members."""

EDITOR_ROLE = "editor"
"""Also records runs into the project and edits its section definitions."""

OWNER_ROLE = "owner"
"""Also manages the project's members."""

ROLES = frozenset({VIEWER_ROLE, EDITOR_ROLE, OWNER_ROLE})

_RANK = {VIEWER_ROLE: 0, EDITOR_ROLE: 1, OWNER_ROLE: 2}

DEFAULT_PROJECT_ROLE = EDITOR_ROLE
"""Every user's role in `default`, which takes no member rows: a server with
one team needs no membership set up at all."""


class InvalidRoleError(ValueError):
    """A role that `ROLES` does not hold."""


def check_role(role: str) -> str:
    """`role`, if it is one."""
    if role not in ROLES:
        raise InvalidRoleError("a role is viewer, editor or owner")
    return role


def role_covers(held: str, needed: str) -> bool:
    """Whether a member with the role `held` may do what needs `needed`."""
    return _RANK[held] >= _RANK[needed]


def effective_role(project: str, *, admin: bool, stored: str | None) -> str | None:
    """The role a user acts with in `project`, given whether they are an
    admin now and the role their member row holds, if any: `OWNER_ROLE` for
    an admin, `DEFAULT_PROJECT_ROLE` in `default`, otherwise the row's; None
    for a user with no role there. The only statement of the two rules no
    row holds."""
    if admin:
        return OWNER_ROLE
    if project == DEFAULT_PROJECT:
        return DEFAULT_PROJECT_ROLE
    return stored


@dataclass(frozen=True, slots=True)
class Membership:
    """One user's role in one project, as a member row stores it."""

    project: str
    user: str
    role: str


__all__ = [
    "DEFAULT_PROJECT",
    "DEFAULT_PROJECT_ROLE",
    "EDITOR_ROLE",
    "OWNER_ROLE",
    "PROJECT_NAME_PATTERN",
    "ROLES",
    "VIEWER_ROLE",
    "InvalidProjectNameError",
    "InvalidRoleError",
    "Membership",
    "Project",
    "can_name_a_project",
    "check_project_name",
    "check_role",
    "effective_role",
    "role_covers",
]
