"""`project_vcs`: the reference implementation of the list-display bound
(design.md D59, D60).

Stdlib only -- `project_vcs` is a pure function with no caller yet (the
adapters call it starting in Phase 2/3 of this change); nothing here needs a
server, a socket, or a temporary file. New tests carry no `req` marker --
this change mints no numeric requirement identifiers (CLAUDE.md).
"""

from __future__ import annotations

import dataclasses

from vantage.core.domain.execution import VcsContext
from vantage.core.domain.projection import (
    LIST_COMMIT_SUBJECT_CHARS,
    VcsProjection,
    project_vcs,
)


def _vcs(*, subject: str | None, truncated: bool) -> VcsContext:
    return VcsContext(
        commit="a" * 40,
        branch="main",
        commit_subject=subject,
        commit_subject_truncated=truncated,
        dirty=False,
        root="/repo",
    )


def test_subject_bounded_at_120_chars_sets_flag() -> None:
    """history-read-api -> Lean list projections -> The commit subject is
    bounded in list responses (D60). A 200-character subject that was NOT
    capture-truncated is still bounded and flagged by display width alone."""
    vcs = _vcs(subject="x" * 200, truncated=False)

    projection = project_vcs(vcs)

    assert projection is not None
    assert projection.commit_subject == "x" * LIST_COMMIT_SUBJECT_CHARS
    assert len(projection.commit_subject) == 120
    assert projection.commit_subject_truncated is True


def test_capture_truncation_flag_survives_even_when_short() -> None:
    """Lean list projections -> The truncation flag never surfaces
    independently of its subject (D60). A short subject that WAS
    capture-truncated still reports the flag -- the other half of the
    disjunction, independent of display width."""
    vcs = _vcs(subject="short subject", truncated=True)

    projection = project_vcs(vcs)

    assert projection is not None
    assert projection.commit_subject == "short subject"
    assert projection.commit_subject_truncated is True


def test_null_vcs_context_projects_to_none() -> None:
    """history-read-api -> Test history -> A non-repository execution has a
    null VCS context, not an omitted entry."""
    assert project_vcs(None) is None


def test_vcs_projection_has_no_root_field() -> None:
    """Lean list projections -> `vcs_root` appears in no run list or run
    detail response (D59). The exclusion is structural -- a type with no
    field to leak -- not a runtime assertion."""
    field_names = {f.name for f in dataclasses.fields(VcsProjection)}

    assert "root" not in field_names
