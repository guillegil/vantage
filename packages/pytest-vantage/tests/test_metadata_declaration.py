"""`pytest_vantage.metadata.read_declaration`, and the bounds it mirrors
from the server.

Every fixture is a real filesystem structure under `tmp_path`, never a mock
of filesystem behaviour.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_vantage import metadata
from pytest_vantage.boundary import VantageWarning
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES as _SERVER_MAX_METADATA_ENTRIES
from vantage.core.domain.metadata import MAX_METADATA_KEY_CHARS as _SERVER_MAX_METADATA_KEY_CHARS


def _config() -> pytest.Config:
    # `_warn` only reaches `config.pluginmanager` when `warnings.warn`
    # itself raises (an active `-W error` filter) -- never the case in
    # these tests, so a bare `SimpleNamespace` is enough, the same
    # duck-typed shape `test_opt_in.py` already passes to `Recorder`.
    return SimpleNamespace()  # type: ignore[return-value]


def _metadata_warnings(recwarn: pytest.WarningsRecorder) -> list[warnings.WarningMessage]:
    return [w for w in recwarn.list if issubclass(w.category, VantageWarning)]


# --- mirrored constants -----------------------------------------------------


def test_the_mirrored_entry_bound_matches_the_server() -> None:
    """`metadata.MAX_METADATA_ENTRIES` mirrors the server's
    `vantage.core.domain.metadata.MAX_METADATA_ENTRIES`, which the plugin
    cannot import; this test is what keeps the two in step.
    """
    assert metadata.MAX_METADATA_ENTRIES == _SERVER_MAX_METADATA_ENTRIES


def test_the_mirrored_key_char_bound_matches_the_server() -> None:
    """`metadata.MAX_DECLARED_KEY_CHARS` mirrors the server's
    `vantage.core.domain.metadata.MAX_METADATA_KEY_CHARS`, kept in step the
    same way.
    """
    assert metadata.MAX_DECLARED_KEY_CHARS == _SERVER_MAX_METADATA_KEY_CHARS


# --- read_declaration: rejection conditions ---------------------------------


def test_an_absent_declaration_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    result = metadata.read_declaration(_config(), root)

    assert result is None
    warned = _metadata_warnings(recwarn)
    assert len(warned) == 1
    assert metadata.DECLARATION_FILENAME in str(warned[0].message)


def test_a_non_json_declaration_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / metadata.DECLARATION_FILENAME).write_text("{not json")

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


@pytest.mark.parametrize("document", ["[]", '"a string"', "42"])
def test_a_non_object_declaration_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder, document: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / metadata.DECLARATION_FILENAME).write_text(document)

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


@pytest.mark.parametrize("document", [{"files": []}, {"version": 2, "files": []}])
def test_an_unsupported_version_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder, document: dict[str, object]
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps(document))

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


@pytest.mark.parametrize(
    "files_value",
    [
        "not a list",
        [{"path": f"f{i}.json", "format": "json", "keys": []} for i in range(17)],
    ],
    ids=["not_a_list", "over_max_declared_files"],
)
def test_an_invalid_files_field_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder, files_value: object
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / metadata.DECLARATION_FILENAME).write_text(
        json.dumps({"version": 1, "files": files_value})
    )

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


@pytest.mark.parametrize(
    "entry",
    [
        {"format": "json", "keys": ["k"]},  # missing path
        {"path": "f.json", "keys": ["k"]},  # missing format
        {"path": "f.json", "format": "json"},  # missing keys
    ],
    ids=["missing_path", "missing_format", "missing_keys"],
)
def test_an_entry_missing_a_required_field_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder, entry: dict[str, object]
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": [entry]}))

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


def test_an_unknown_format_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    entry = {"path": "f.toml", "format": "toml", "keys": ["k"]}
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": [entry]}))

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


def test_a_duplicate_stored_key_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    # The key space is flat and unique per run -- detected from the
    # declaration alone, before any file is opened.
    root = tmp_path / "project"
    root.mkdir()
    files = [
        {"path": "a.json", "format": "json", "keys": ["firmware_version"]},
        {"path": "b.json", "format": "json", "keys": ["firmware_version"]},
    ]
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": files}))

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


def test_a_path_longer_than_the_bound_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    long_path = "a" * (metadata.MAX_DECLARED_PATH_CHARS + 1) + ".json"
    entry = {"path": long_path, "format": "json", "keys": ["k"]}
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": [entry]}))

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


def test_a_path_containing_a_nul_byte_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    # Refused loudly here, before `resolve_declared_path` ever sees it --
    # `Path.resolve()` raises `ValueError` on a NUL byte.
    root = tmp_path / "project"
    root.mkdir()
    entry = {"path": "config\x00.json", "format": "json", "keys": ["k"]}
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": [entry]}))

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


def test_a_key_longer_than_the_bound_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    # The whole declaration is refused, exactly like an over-long path.
    root = tmp_path / "project"
    root.mkdir()
    long_key = "k" * (metadata.MAX_DECLARED_KEY_CHARS + 1)
    entry = {"path": "a.json", "format": "json", "keys": [long_key]}
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": [entry]}))

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


def test_more_than_the_total_key_bound_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metadata, "MAX_METADATA_ENTRIES", 2)
    root = tmp_path / "project"
    root.mkdir()
    entry = {"path": "a.json", "format": "json", "keys": ["k1", "k2", "k3"]}
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": [entry]}))

    result = metadata.read_declaration(_config(), root)

    assert result is None
    assert len(_metadata_warnings(recwarn)) == 1


# --- read_declaration: valid declarations -----------------------------------


def test_a_well_formed_declaration_is_read_with_no_warning(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    files = [
        {
            "path": "config/firmware.yaml",
            "format": "yaml",
            "keys": ["firmware_version", "board_revision"],
        }
    ]
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": files}))

    result = metadata.read_declaration(_config(), root)

    assert result == (
        metadata.DeclaredFile(
            path="config/firmware.yaml", format="yaml", keys=("firmware_version", "board_revision")
        ),
    )
    assert len(_metadata_warnings(recwarn)) == 0


def test_an_empty_files_list_is_accepted_with_no_warning(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": []}))

    result = metadata.read_declaration(_config(), root)

    assert result == ()
    assert len(_metadata_warnings(recwarn)) == 0
