"""Validation of a session report's `vcs` and `metadata` sections.

`VcsReport.commit` accepts a SHA-256 (64 hex chars), never only a 40-hex
pattern -- git is migrating away from SHA-1.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from vantage.service.schemas import (
    MetadataFileReport,
    MetadataReport,
    VcsReport,
)


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
    `vcs` means the two sides disagree about what a VCS snapshot is, unlike
    `ResultReport`'s deliberately different `extra="allow"`."""
    with pytest.raises(ValidationError, match="extra"):
        VcsReport.model_validate(_well_formed_vcs(tag="v1.2.3"))


def test_vcs_report_rejects_a_missing_required_field() -> None:
    incomplete = _well_formed_vcs()
    del incomplete["root"]

    with pytest.raises(ValidationError, match="root"):
        VcsReport.model_validate(incomplete)


def _well_formed_metadata_file(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "path": "config/firmware.yaml",
        "format": "yaml",
        "status": "captured",
        "keys": ["firmware_version", "board_revision"],
        "content": 'firmware_version: "2.1"\nboard_revision: C\n',
    }
    payload.update(overrides)
    return payload


def test_metadata_file_report_accepts_a_declared_value_of_arbitrary_length_and_content() -> None:
    """No `max_length`, `pattern` or other constraint may appear on a
    metadata field: a failed constraint is a `422` that rejects the WHOLE
    session report. Bounds belong to the normalizer, which drops rather than
    rejects. A value bigger and stranger than any real declared file could
    hold must still validate."""
    huge_content = "\N{SNOWMAN}" * 100_000 + "\x00" * 1_000 + "a" * 500_000

    report = MetadataFileReport.model_validate(
        _well_formed_metadata_file(
            path="p" * 10_000,
            keys=["k" * 10_000],
            content=huge_content,
        )
    )

    assert report.content == huge_content
    assert report.path == "p" * 10_000
    assert report.keys == ["k" * 10_000]


def test_metadata_file_report_accepts_a_null_content_for_a_non_captured_status() -> None:
    """`content` is `None` whenever `status` is not `"captured"`: a declared
    file that was dropped is still reported, not omitted."""
    report = MetadataFileReport.model_validate(
        _well_formed_metadata_file(status="too_large", content=None)
    )

    assert report.status == "too_large"
    assert report.content is None


def test_metadata_file_report_rejects_an_unknown_field() -> None:
    """`extra="forbid"`, matching `VcsReport`."""
    with pytest.raises(ValidationError, match="extra"):
        MetadataFileReport.model_validate(_well_formed_metadata_file(size=8192))


def test_metadata_report_accepts_a_well_formed_section() -> None:
    report = MetadataReport.model_validate(
        {
            "declaration": "vantage-metadata.json",
            "files": [_well_formed_metadata_file(), _well_formed_metadata_file(status="not_found")],
        }
    )

    assert report.declaration == "vantage-metadata.json"
    assert len(report.files) == 2
    assert report.files[0].content is not None
    assert report.files[1].status == "not_found"


def test_metadata_report_accepts_an_arbitrary_length_declaration_name() -> None:
    """No constraint of any kind on `declaration` either."""
    report = MetadataReport.model_validate({"declaration": "d" * 10_000, "files": []})

    assert report.declaration == "d" * 10_000


def test_metadata_report_rejects_an_unknown_field() -> None:
    """`extra="forbid"`, matching `VcsReport`."""
    with pytest.raises(ValidationError, match="extra"):
        MetadataReport.model_validate(
            {"declaration": "vantage-metadata.json", "files": [], "extra_field": 1}
        )
