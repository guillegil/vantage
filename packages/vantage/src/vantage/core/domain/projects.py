"""Projects: what a run belongs to, and the rule a project's name follows.

Every run belongs to one project, named by the report that creates it and
never changed afterwards. The test catalogue, a test's history, the section
definitions and the run list are each read within one project, so two
projects never mix their tests, however alike their node ids.

Nothing renames or deletes a project, so "project X exists" only ever turns
from false to true: a check that one exists stays true, and every row that
names a project keeps naming one. `default` exists in every database from
the moment it is created, for the runs that name no project.
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


__all__ = [
    "DEFAULT_PROJECT",
    "PROJECT_NAME_PATTERN",
    "InvalidProjectNameError",
    "Project",
    "can_name_a_project",
    "check_project_name",
]
