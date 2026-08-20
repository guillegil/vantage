"""`VcsReport`'s wire shape (design.md D47): `extra="forbid"`, and a
`commit` field that accepts a SHA-256 (64 hex chars), never a pattern
anchored at 40 -- git is migrating away from SHA-1, and a 40-hex pattern
would `422` a report from a SHA-256 repository over a field nothing reads
yet.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from vantage.service.schemas import SessionReport, VcsReport


def _well_formed_vcs(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "commit": "a" * 40,
        "branch": "main",
        "commit_subject": "Fix the thing",
        "dirty": False,
        "root": "/repo",
    }
    payload.update(overrides)
    return payload


def test_vcs_report_accepts_a_well_formed_section() -> None:
    report = VcsReport.model_validate(_well_formed_vcs())

    assert report.commit == "a" * 40
    assert report.branch == "main"


def test_vcs_report_accepts_a_sha256_commit_sixty_four_hex_characters() -> None:
    report = VcsReport.model_validate(_well_formed_vcs(commit="f" * 64))

    assert report.commit == "f" * 64


def test_vcs_report_rejects_a_commit_longer_than_sixty_four_characters() -> None:
    with pytest.raises(ValidationError, match="commit"):
        VcsReport.model_validate(_well_formed_vcs(commit="a" * 65))


def test_vcs_report_accepts_all_five_fields_null() -> None:
    report = VcsReport.model_validate(
        _well_formed_vcs(commit=None, branch=None, commit_subject=None, dirty=None, root=None)
    )

    assert report.commit is None
    assert report.branch is None
    assert report.commit_subject is None
    assert report.dirty is None
    assert report.root is None


def test_vcs_report_rejects_an_unknown_field_inside_the_section() -> None:
    """`extra="forbid"`, matching `RunReport` -- an unknown field inside
    `vcs` means the two sides disagree about what a VCS snapshot is
    (design.md D47), unlike `ResultReport`'s deliberately different
    `extra="allow"`."""
    with pytest.raises(ValidationError, match="extra"):
        VcsReport.model_validate(_well_formed_vcs(tag="v1.2.3"))


def test_vcs_report_rejects_a_missing_required_field() -> None:
    incomplete = _well_formed_vcs()
    del incomplete["root"]

    with pytest.raises(ValidationError, match="root"):
        VcsReport.model_validate(incomplete)


def test_session_report_vcs_defaults_to_none_when_the_section_is_absent() -> None:
    """An older plugin's report shape -- no `vcs` key at all -- still
    validates; `SessionReport.vcs` defaults to `None` (design.md D47)."""
    report = SessionReport.model_validate(
        {
            "run": {
                "id": "a" * 32,
                "started_at": "2026-08-15T09:14:02.481930+00:00",
                "finished_at": None,
                "exit_status": None,
                "interrupted": False,
                "interrupt_reason": None,
            }
        }
    )

    assert report.vcs is None


def test_session_report_carries_a_well_formed_vcs_section() -> None:
    report = SessionReport.model_validate(
        {
            "run": {
                "id": "a" * 32,
                "started_at": "2026-08-15T09:14:02.481930+00:00",
                "finished_at": None,
                "exit_status": None,
                "interrupted": False,
                "interrupt_reason": None,
            },
            "vcs": _well_formed_vcs(),
        }
    )

    assert report.vcs is not None
    assert report.vcs.commit == "a" * 40
