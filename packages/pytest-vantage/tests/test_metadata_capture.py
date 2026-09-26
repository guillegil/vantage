"""`pytest_vantage.metadata.capture_metadata`.

Every fixture is a real filesystem structure under `tmp_path`, never a mock
of filesystem behaviour.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_vantage import metadata
from pytest_vantage.boundary import VantageWarning
from pytest_vantage.budget import encoded_cost


def _config() -> pytest.Config:
    # `warn` only reaches `config.pluginmanager` when `warnings.warn`
    # itself raises (an active `-W error` filter) -- never the case in
    # these tests, so a bare `SimpleNamespace` is enough, the same
    # duck-typed shape `test_opt_in.py` already passes to `Recorder`.
    return SimpleNamespace()  # type: ignore[return-value]


def _declare(root: Path, files: list[dict[str, object]]) -> None:
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": files}))


# --- capture_metadata: byte bounds and file statuses -----------------------


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


def test_a_file_past_the_remaining_budget_is_skipped_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Room for every entry plus 30 bytes of content: a.json (20 encoded
    # bytes) fits, b.json (20) no longer does, and c.json (4), later in
    # declaration order, still does.
    root = tmp_path / "project"
    root.mkdir()
    contents = {"a.json": "x" * 18, "b.json": "y" * 18, "c.json": "zz"}
    for name, content in contents.items():
        (root / name).write_text(content)
    _declare(root, [{"path": name, "format": "json", "keys": [name[0]]} for name in contents])
    declared = metadata.read_declaration(_config(), root)
    assert declared is not None
    monkeypatch.setattr(
        metadata, "MAX_METADATA_SECTION_BYTES", metadata._fixed_section_cost(declared) + 30
    )

    section = metadata.capture_metadata(_config(), root)

    assert section is not None
    statuses = [(f.path, f.status, f.content) for f in section.files]
    assert statuses == [
        ("a.json", "captured", contents["a.json"]),
        ("b.json", "over_budget", None),
        ("c.json", "captured", contents["c.json"]),
    ]


def test_a_file_whose_encoded_content_alone_exceeds_the_section_is_too_large(
    tmp_path: Path,
) -> None:
    # Within the raw cap, but a control character costs six bytes on the
    # wire, so no capture order could make it fit. It is a property of the
    # file, not the budget running out, and the next file is still read.
    root = tmp_path / "project"
    root.mkdir()
    (root / "noise.yaml").write_text("\x01" * metadata.MAX_DECLARED_FILE_BYTES)
    (root / "small.json").write_text('{"board": "rev-b"}')
    _declare(
        root,
        [
            {"path": "noise.yaml", "format": "yaml", "keys": ["n"]},
            {"path": "small.json", "format": "json", "keys": ["board"]},
        ],
    )

    section = metadata.capture_metadata(_config(), root)

    assert section is not None
    assert section.files == (
        metadata.CapturedFile(
            path="noise.yaml", format="yaml", status="too_large", keys=("n",), content=None
        ),
        metadata.CapturedFile(
            path="small.json",
            format="json",
            status="captured",
            keys=("board",),
            content='{"board": "rev-b"}',
        ),
    )


@pytest.mark.parametrize("char", ["k", "\u4e2d", "\U0001f600"], ids=["ascii", "cjk", "non_bmp"])
def test_an_accepted_section_stays_within_its_budget_on_the_wire(tmp_path: Path, char: str) -> None:
    # Paths, keys and content together, measured as the recorder serialises
    # the section. Sizes are in wire bytes, so every character set fills the
    # budget alike: one byte, six as `\uXXXX`, twelve as a surrogate pair.
    wire_bytes_per_char = encoded_cost(char) - len('""')
    root = tmp_path / "project"
    root.mkdir()
    files: list[dict[str, object]] = []
    for i in range(metadata.MAX_DECLARED_FILES):
        name = f"f{i:02d}.json"
        document = {"value": char * (500 * (i + 1) // wire_bytes_per_char)}
        (root / name).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        key = f"{i:02d}{char * (600 // wire_bytes_per_char)}"
        files.append({"path": name, "format": "json", "keys": [key]})
    _declare(root, files)

    section = metadata.capture_metadata(_config(), root)

    assert section is not None
    # The budget was actually reached, not merely never approached.
    assert {"captured", "over_budget"} <= {f.status for f in section.files}
    assert encoded_cost(metadata.wire_section(section)) <= metadata.MAX_METADATA_SECTION_BYTES


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
    # A file the process cannot read is neither missing nor oversized nor
    # binary.
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
    # A committed symlink to a file that exists outside rootpath is
    # `path_rejected`, never `not_found`, and is never opened.
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"k": "v"}')
    (root / "link.json").symlink_to(outside)
    _declare(root, [{"path": "link.json", "format": "json", "keys": ["k"]}])

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
    # A declaration that validates always yields a section, even with zero
    # files declared -- never None.
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


# --- capture_metadata: the declared keys, with and without the files --------


def test_declared_keys_are_captured_whether_or_not_the_files_are_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without `--vantage-metadata` the declaration still says which keys
    exist, including those of its files, but not one file is opened."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "f.json").write_text('{"region": "eu-west-1"}')
    (root / metadata.DECLARATION_FILENAME).write_text(
        json.dumps(
            {
                "version": 1,
                "keys": {"bench": {"name": "Test bench"}},
                "files": [{"path": "f.json", "format": "json", "keys": ["region", "zone"]}],
            }
        )
    )
    opened: list[str] = []
    real_read = metadata._read_declared_file

    def _spy(rootpath: Path, declared_path: str) -> tuple[str, str | None]:
        opened.append(declared_path)
        return real_read(rootpath, declared_path)

    monkeypatch.setattr(metadata, "_read_declared_file", _spy)

    unread = metadata.capture_metadata(_config(), root, read_files=False)
    assert opened == []
    read = metadata.capture_metadata(_config(), root)

    assert opened == ["f.json"]
    keys = (metadata.DeclaredKey("bench", "Test bench"),)
    assert unread == metadata.MetadataSection(
        declaration=metadata.DECLARATION_FILENAME,
        files=(),
        keys=keys,
        unread_file_keys=("region", "zone"),
    )
    assert read is not None
    assert read.keys == keys
    assert read.unread_file_keys == ()
    assert [entry.status for entry in read.files] == ["captured"]


def test_a_missing_declaration_is_silent_when_the_files_are_not_read(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    assert metadata.capture_metadata(_config(), root, read_files=False) is None
    assert not any(issubclass(w.category, VantageWarning) for w in recwarn.list)


# --- wire_section --------------------------------------------------------------


def _section(
    *,
    files: tuple[metadata.CapturedFile, ...] = (),
    keys: tuple[metadata.DeclaredKey, ...] = (),
    unread_file_keys: tuple[str, ...] = (),
) -> metadata.MetadataSection:
    return metadata.MetadataSection(
        declaration=metadata.DECLARATION_FILENAME,
        files=files,
        keys=keys,
        unread_file_keys=unread_file_keys,
    )


def test_a_section_that_says_nothing_is_not_sent() -> None:
    """Keys of unread files alone have nothing to record."""
    assert metadata.wire_section(None) is None
    assert metadata.wire_section(_section(unread_file_keys=("region",))) is None


def test_the_wire_section_leaves_out_empty_keys_and_values() -> None:
    """A report that declares no key and carries no value has the shape a
    server that predates both accepts."""
    captured = metadata.CapturedFile("f.json", "json", "captured", ("region",), "{}")

    wire = metadata.wire_section(_section(files=(captured,)))

    assert wire == {
        "declaration": metadata.DECLARATION_FILENAME,
        "files": [
            {
                "path": "f.json",
                "format": "json",
                "status": "captured",
                "keys": ["region"],
                "content": "{}",
            }
        ],
    }


def test_the_wire_section_carries_keys_files_and_values_in_order() -> None:
    section = _section(
        keys=(metadata.DeclaredKey("fpga.firmware", "FPGA firmware version"),),
        unread_file_keys=("region",),
    )
    values = [
        metadata.SessionValue("fpga.firmware", "1.1.0", "captured"),
        metadata.SessionValue("region", "eu-west-1", "captured"),
    ]

    wire = metadata.wire_section(section, values, named_keys=["region"])

    assert wire == {
        "declaration": metadata.DECLARATION_FILENAME,
        "keys": {
            "fpga.firmware": {"name": "FPGA firmware version"},
            "region": {"name": None},
        },
        "files": [],
        "values": [
            {"key": "fpga.firmware", "value": "1.1.0", "status": "captured"},
            {"key": "region", "value": "eu-west-1", "status": "captured"},
        ],
    }


def test_values_without_a_declaration_are_sent_with_a_null_declaration() -> None:
    values = [metadata.SessionValue("bench", "lab-3", "captured")]

    assert metadata.wire_section(None, values) == {
        "declaration": None,
        "files": [],
        "values": [{"key": "bench", "value": "lab-3", "status": "captured"}],
    }
