"""`project_vcs` and `project_failure`: the reference implementations of the
list-display bound.

Stdlib only -- both are pure functions, so nothing here needs a server, a
socket, or a temporary file.
"""

from __future__ import annotations

import dataclasses

from vantage.core.domain.execution import VcsContext
from vantage.core.domain.projection import (
    LIST_COMMIT_SUBJECT_CHARS,
    LIST_FAILURE_MESSAGE_CHARS,
    FailureProjection,
    VcsProjection,
    project_failure,
    project_vcs,
)
from vantage.core.domain.result import FailureEvidence


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
    """A 200-character subject that was NOT capture-truncated is still
    bounded and flagged by display width alone."""
    vcs = _vcs(subject="x" * 200, truncated=False)

    projection = project_vcs(vcs)

    assert projection is not None
    assert projection.commit_subject == "x" * LIST_COMMIT_SUBJECT_CHARS
    assert len(projection.commit_subject) == 120
    assert projection.commit_subject_truncated is True


def test_capture_truncation_flag_survives_even_when_short() -> None:
    """A short subject that WAS capture-truncated still reports the flag --
    the other half of the disjunction, independent of display width."""
    vcs = _vcs(subject="short subject", truncated=True)

    projection = project_vcs(vcs)

    assert projection is not None
    assert projection.commit_subject == "short subject"
    assert projection.commit_subject_truncated is True


def test_null_vcs_context_projects_to_none() -> None:
    """A non-repository execution has a null VCS context, not an omitted
    entry."""
    assert project_vcs(None) is None


def test_vcs_projection_has_no_root_field() -> None:
    """`vcs_root` never appears in a list response. The exclusion is
    structural -- a type with no field to leak -- not a runtime assertion."""
    field_names = {f.name for f in dataclasses.fields(VcsProjection)}

    assert "root" not in field_names


# --- project_failure / FailureProjection ------------------------------------


def _failure(**overrides: object) -> FailureEvidence:
    fields: dict[str, object] = {
        "failure_type": "AssertionError",
        "failure_message": "AssertionError: assert 1200 == 1320",
        "failure_message_truncated": False,
        "failure_path": "tests/helpers/pricing.py",
        "failure_lineno": 47,
        "failure_repr": "AssertionError('assert 1200 == 1320')",
        "failure_repr_truncated": False,
        "traceback": "tests/test_orders.py:19: in test_total_includes_tax\n    ...",
        "traceback_truncated": False,
        "skip_reason": None,
        "skip_reason_truncated": False,
        "xfail_reason": None,
        "xfail_reason_truncated": False,
    }
    fields.update(overrides)
    return FailureEvidence(**fields)  # type: ignore[arg-type]


def test_project_failure_bounds_message_to_200_chars_and_flags() -> None:
    """A 300-char `failure_message` with `failure_message_truncated=False` on
    the input is bounded to `LIST_FAILURE_MESSAGE_CHARS` and the flag is set
    by display width alone."""
    failure = _failure(failure_message="x" * 300, failure_message_truncated=False)

    projection = project_failure(failure)

    assert projection is not None
    assert projection.failure_message == "x" * LIST_FAILURE_MESSAGE_CHARS
    assert len(projection.failure_message) == 200
    assert projection.failure_message_truncated is True


def test_project_failure_flag_survives_a_short_capture_truncated_message() -> None:
    """A short message that WAS capture-truncated still reports the flag --
    the disjunction holds regardless of display width."""
    failure = _failure(failure_message="short message", failure_message_truncated=True)

    projection = project_failure(failure)

    assert projection is not None
    assert projection.failure_message == "short message"
    assert projection.failure_message_truncated is True


def test_failure_projection_excludes_the_heavy_fields_structurally() -> None:
    """`FailureProjection` has no field for `traceback`, `failure_repr` or
    any captured-output field -- the exclusion is structural, not a runtime
    check."""
    field_names = {f.name for f in dataclasses.fields(FailureProjection)}

    assert "traceback" not in field_names
    assert "failure_repr" not in field_names
    assert "captured_stdout" not in field_names
    assert "captured_stderr" not in field_names


def test_project_failure_of_none_is_none() -> None:
    """A result with no failure evidence projects to no failure projection."""
    assert project_failure(None) is None
