"""`VcsProjection` and `project_vcs`: the list-display bound on `VcsContext`
(design.md D59, D60).

Stdlib only, as `architecture-boundaries` -> *Core isolation* requires --
this module has exactly one caller today
(`test_projection.py`); the two storage adapters gain their own calls to
`project_vcs` starting in Phase 2/3 of this change.

`VcsProjection` has no `root` field. That exclusion is structural, not a
runtime check: a list or history response built from this type has nothing
to leak, because the type never carries `vcs_root` in the first place --
unlike `VcsContext`, where the exclusion on the detail path is a choice that
has to be made correctly at the response-model boundary every time
(design.md D59).
"""

from __future__ import annotations

from dataclasses import dataclass

from vantage.core.domain.execution import VcsContext

LIST_COMMIT_SUBJECT_CHARS = 120
"""The list/history display width, in characters -- not bytes, so SQLite's
`substr`/`length` and Python's slicing/`len` agree by construction across
both storage adapters (design.md D57, D60)."""


@dataclass(frozen=True, slots=True)
class VcsProjection:
    """A read-only VCS projection for a list-shaped response (design.md D59).

    `commit_subject_truncated` widens its meaning relative to `VcsContext`:
    here it means "this is not the whole stored subject" -- true if the
    capture itself was truncated OR if display bounding shortened it here.
    On `VcsContext` (the detail path) the flag keeps its original,
    capture-only meaning unchanged (design.md D60).
    """

    commit: str | None
    branch: str | None
    commit_subject: str | None
    commit_subject_truncated: bool
    dirty: bool | None


def project_vcs(vcs: VcsContext | None) -> VcsProjection | None:
    """Reference implementation of the rule the SQLite adapter states in SQL
    (design.md D57, D60).

    Returns `None` for `None` -- a non-repository execution has a null VCS
    context, not an omitted list entry (history-read-api -> Test history).
    The all-null normalisation is inherited from wherever the caller's
    `VcsContext` came from (`_row_to_vcs_context`, for the SQLite adapter);
    this function restates none of it.
    """
    if vcs is None:
        return None

    subject = vcs.commit_subject
    display_truncated = subject is not None and len(subject) > LIST_COMMIT_SUBJECT_CHARS
    bounded_subject = subject if subject is None else subject[:LIST_COMMIT_SUBJECT_CHARS]

    return VcsProjection(
        commit=vcs.commit,
        branch=vcs.branch,
        commit_subject=bounded_subject,
        commit_subject_truncated=vcs.commit_subject_truncated or display_truncated,
        dirty=vcs.dirty,
    )


__all__ = ["LIST_COMMIT_SUBJECT_CHARS", "VcsProjection", "project_vcs"]
