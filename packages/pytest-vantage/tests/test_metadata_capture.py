"""`pytest_vantage.metadata.capture_metadata` (design.md D93-D97) -- tasks
7.1/7.2.

Basename note (continuing Phase 6/7a's own forward pointer): neither test
tree carries an `__init__.py`, so pytest's classic import mode needs every
basename unique workspace-wide. `packages/vantage/tests/test_metadata.py`
(PR3), `test_metadata_containment.py` (PR6, `resolve_declared_path`) and
`test_metadata_declaration.py` (PR7a, `read_declaration`) already exist --
this file is a fourth, equally unique name, for `capture_metadata` alone.

Every fixture is a real filesystem structure under `tmp_path`, matching the
other `pytest_vantage.metadata` test files' own verification style -- never
a mock of filesystem behaviour.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_vantage import metadata
from pytest_vantage.boundary import VantageWarning


def _config() -> pytest.Config:
    # `_warn` only reaches `config.pluginmanager` when `warnings.warn`
    # itself raises (an active `-W error` filter) -- never the case in
    # these tests, so a bare `SimpleNamespace` is enough, the same
    # duck-typed shape `test_opt_in.py` already passes to `Recorder`.
    return SimpleNamespace()  # type: ignore[return-value]


def _declare(root: Path, files: list[dict[str, object]]) -> None:
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": files}))


# --- capture_metadata: byte bound (task 7.1, design.md D94/D97) ------------


def test_a_file_at_the_byte_bound_is_kept(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    content = "a" * metadata.MAX_DECLARED_FILE_BYTES
    (root / "f.json").write_text(content)
    _declare(root, [{"path": "f.json", "format": "json", "keys": ["k"]}])

    section = metadata.capture_metadata(_config(), root)

    assert section is not None
    assert section.files == (
        metadata.CapturedFile(
            path="f.json", format="json", status="captured", keys=("k",), content=content
        ),
    )


def test_a_file_one_byte_over_the_bound_is_dropped_whole_and_marked_too_large(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "f.json").write_text("a" * (metadata.MAX_DECLARED_FILE_BYTES + 1))
    _declare(root, [{"path": "f.json", "format": "json", "keys": ["k"]}])

    section = metadata.capture_metadata(_config(), root)

    assert section is not None
    assert section.files == (
        metadata.CapturedFile(
            path="f.json", format="json", status="too_large", keys=("k",), content=None
        ),
    )


def test_files_past_the_section_budget_are_marked_over_budget_in_declaration_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Room for exactly one 20-byte-encoded file; the second and third,
    # despite being identical, drop -- because they come later, in the
    # order the declaration names them (design.md D97 class 6), never
    # because of any property of their own content.
    monkeypatch.setattr(metadata, "MAX_METADATA_SECTION_BYTES", 20)
    root = tmp_path / "project"
    root.mkdir()
    content = "x" * 18  # 20 encoded bytes: 18 chars + 2 quotes
    for name in ("a.json", "b.json", "c.json"):
        (root / name).write_text(content)
    _declare(
        root,
        [
            {"path": "a.json", "format": "json", "keys": ["ka"]},
            {"path": "b.json", "format": "json", "keys": ["kb"]},
            {"path": "c.json", "format": "json", "keys": ["kc"]},
        ],
    )

    section = metadata.capture_metadata(_config(), root)

    assert section is not None
    statuses = [(f.path, f.status, f.content) for f in section.files]
    assert statuses == [
        ("a.json", "captured", content),
        ("b.json", "over_budget", None),
        ("c.json", "over_budget", None),
    ]


def test_a_non_utf8_file_is_marked_not_text_before_json_encoding(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "f.json").write_bytes(b"\xff\xfe\xfa")
    _declare(root, [{"path": "f.json", "format": "json", "keys": ["k"]}])

    section = metadata.capture_metadata(_config(), root)

    assert section is not None
    assert section.files == (
        metadata.CapturedFile(
            path="f.json", format="json", status="not_text", keys=("k",), content=None
        ),
    )


@pytest.mark.skipif(
    os.geteuid() == 0 if hasattr(os, "geteuid") else True,
    reason="chmod 000 is a no-op as root; skip rather than pass vacuously",
)
def test_a_permission_denied_file_is_marked_unreadable(tmp_path: Path) -> None:
    # design.md D97 class 5: the eighth plugin-side class the proposal's
    # seven did not name -- a file the process cannot read is neither
    # missing nor oversized nor binary.
    root = tmp_path / "project"
    root.mkdir()
    restricted = root / "f.json"
    restricted.write_text('{"k": "v"}')
    os.chmod(restricted, 0o000)
    _declare(root, [{"path": "f.json", "format": "json", "keys": ["k"]}])

    try:
        section = metadata.capture_metadata(_config(), root)
    finally:
        os.chmod(restricted, 0o644)  # noqa: S103 -- restoring the fixture, not granting access

    assert section is not None
    assert section.files == (
        metadata.CapturedFile(
            path="f.json", format="json", status="unreadable", keys=("k",), content=None
        ),
    )


def test_a_missing_declared_file_is_marked_not_found(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _declare(root, [{"path": "does-not-exist.json", "format": "json", "keys": ["k"]}])

    section = metadata.capture_metadata(_config(), root)

    assert section is not None
    assert section.files == (
        metadata.CapturedFile(
            path="does-not-exist.json", format="json", status="not_found", keys=("k",), content=None
        ),
    )


def test_a_rejected_path_that_exists_is_marked_path_rejected_not_not_found(tmp_path: Path) -> None:
    # design.md D93/D97: an absolute path that happens to exist on disk
    # (outside rootpath) is still `path_rejected`, never `not_found` --
    # the two mean different things, and the plugin never opened it either
    # way (`resolve_declared_path` already refused it).
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"k": "v"}')
    _declare(root, [{"path": str(outside), "format": "json", "keys": ["k"]}])

    section = metadata.capture_metadata(_config(), root)

    assert section is not None
    assert section.files[0].status == "path_rejected"
    assert section.files[0].content is None


def test_capture_metadata_returns_none_when_the_declaration_itself_is_invalid(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    # No declaration at all -- read_declaration's own rejection, already
    # warned once; capture_metadata must not warn a second time.
    result = metadata.capture_metadata(_config(), root)

    assert result is None
    metadata_warnings = [w for w in recwarn.list if issubclass(w.category, VantageWarning)]
    assert len(metadata_warnings) == 1


def test_an_empty_declaration_captures_a_section_with_no_files(tmp_path: Path) -> None:
    # design.md D95: a MetadataSection is always returned when the
    # declaration itself validates, even with zero files declared -- the
    # empty case is still a section, not None.
    root = tmp_path / "project"
    root.mkdir()
    _declare(root, [])

    section = metadata.capture_metadata(_config(), root)

    assert section == metadata.MetadataSection(declaration=metadata.DECLARATION_FILENAME, files=())


def test_multiple_keys_on_one_captured_file_all_appear_on_its_entry(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "f.yaml").write_text("firmware_version: 2.1\nboard_revision: C\n")
    _declare(
        root,
        [{"path": "f.yaml", "format": "yaml", "keys": ["firmware_version", "board_revision"]}],
    )

    section = metadata.capture_metadata(_config(), root)

    assert section is not None
    assert section.files[0].keys == ("firmware_version", "board_revision")
    assert section.files[0].status == "captured"
