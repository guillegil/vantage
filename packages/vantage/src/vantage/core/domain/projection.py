"""List-display projections of `VcsContext` and `FailureEvidence`.

`project_vcs` and `project_failure` are the one statement of the list rule
both storage adapters apply: a list entry carries a projection exactly when
the full record carries the value it projects, bounded to the display width.
An adapter that bounds text in SQL still hands the bounded value to these
functions rather than restating the rule.

`VcsProjection` has no `root` field. That exclusion is structural, not a
runtime check: a list or history response built from this type has nothing
to leak, because the type never carries `vcs_root` in the first place --
unlike `VcsContext`, where the exclusion on the detail path is a choice that
has to be made correctly at the response-model boundary every time.
"""

from __future__ import annotations

from dataclasses import dataclass

from vantage.core.domain.execution import VcsContext
from vantage.core.domain.result import FailureEvidence

LIST_COMMIT_SUBJECT_CHARS = 120
"""The list/history display width, in characters -- not bytes, so SQLite's
`substr`/`length` and Python's slicing/`len` agree by construction across
both storage adapters."""

LIST_FAILURE_MESSAGE_CHARS = 200
"""The list/history display width for `failure_message`, in characters.
Derived from `excinfo.exconly()`'s shape (`ExceptionType: message`) -- a
qualified exception name routinely spends 20-40 characters before the
discriminating content starts, so 200 keeps the type plus a usable head."""


@dataclass(frozen=True, slots=True)
class VcsProjection:
    """A read-only VCS projection for a list-shaped response.

    `commit_subject_truncated` widens its meaning relative to `VcsContext`:
    here it means "this is not the whole stored subject" -- true if the
    capture itself was truncated OR if display bounding shortened it here.
    On `VcsContext` (the detail path) the flag keeps its original,
    capture-only meaning unchanged.
    """

    commit: str | None
    branch: str | None
    commit_subject: str | None
    commit_subject_truncated: bool
    dirty: bool | None


def project_vcs(vcs: VcsContext | None) -> VcsProjection | None:
    """The list projection of a run's VCS context.

    `None` exactly when the context is `None` or empty -- a non-repository
    run lists with a null VCS context, not an omitted entry. A context known
    only by its `root` still projects, with every field null: the run was
    inside a repository, as its detail says.

    `commit_subject` only needs its first `LIST_COMMIT_SUBJECT_CHARS + 1`
    characters: one past the display width is enough to know it was longer.
    """
    if vcs is None or vcs.is_empty():
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


@dataclass(frozen=True, slots=True)
class FailureProjection:
    """A read-only failure projection for a list-shaped response. No field
    carries `traceback`, `failure_repr` or captured output -- the same
    structural exclusion `VcsProjection` applies to `vcs_root`.

    `failure_message_truncated` widens its meaning relative to
    `FailureEvidence`: here it means "this is not the whole stored message"
    -- true if the capture itself was truncated, if the per-report budget
    dropped it, OR if display bounding shortened it here.
    """

    failure_type: str | None
    failure_message: str | None
    failure_message_truncated: bool
    failure_path: str | None
    failure_lineno: int | None
    skip_reason: str | None
    xfail_reason: str | None


def project_failure(failure: FailureEvidence | None) -> FailureProjection | None:
    """The list projection of a result's failure evidence, by the same rule
    as `project_vcs`.

    `None` exactly when the evidence is `None` or empty. Evidence whose only
    content lies outside the lean fields -- a traceback alone -- projects
    with every field null, so the list still shows that the detail has
    evidence. Only the emptiness of `failure_repr` and `traceback` matters
    here, and `failure_message` only needs its first
    `LIST_FAILURE_MESSAGE_CHARS + 1` characters.
    """
    if failure is None or failure.is_empty():
        return None

    message = failure.failure_message
    display_truncated = message is not None and len(message) > LIST_FAILURE_MESSAGE_CHARS
    bounded_message = message if message is None else message[:LIST_FAILURE_MESSAGE_CHARS]

    return FailureProjection(
        failure_type=failure.failure_type,
        failure_message=bounded_message,
        failure_message_truncated=failure.failure_message_truncated or display_truncated,
        failure_path=failure.failure_path,
        failure_lineno=failure.failure_lineno,
        skip_reason=failure.skip_reason,
        xfail_reason=failure.xfail_reason,
    )


__all__ = [
    "LIST_COMMIT_SUBJECT_CHARS",
    "LIST_FAILURE_MESSAGE_CHARS",
    "FailureProjection",
    "VcsProjection",
    "project_failure",
    "project_vcs",
]
