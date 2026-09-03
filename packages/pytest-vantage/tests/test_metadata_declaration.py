"""`pytest_vantage.metadata.read_declaration` (design.md D92, D94) -- tasks
6.3/6.4, deferred from PR6 and landed here.

Basename note (PR6's own forward pointer, `tasks.md` Phase 6): neither test
tree carries an `__init__.py`, so pytest's classic import mode needs every
basename unique workspace-wide. `packages/vantage/tests/test_metadata.py`
(PR3, vocabulary) and `packages/pytest-vantage/tests/test_metadata_containment.py`
(PR6, `resolve_declared_path`) already exist -- this file is the second,
equally unique name Phase 6's note asked for. `capture_metadata` (tasks
7.1/7.2) is a later slice, in its own file, for the same reason.

Every fixture is a real filesystem structure under `tmp_path`, matching
`test_metadata_containment.py` and `test_vcs.py`'s own verification style --
never a mock of filesystem behaviour.
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


def _config() -> pytest.Config:
    # `_warn` only reaches `config.pluginmanager` when `warnings.warn`
    # itself raises (an active `-W error` filter) -- never the case in
    # these tests, so a bare `SimpleNamespace` is enough, the same
    # duck-typed shape `test_opt_in.py` already passes to `Recorder`.
    return SimpleNamespace()  # type: ignore[return-value]


def _metadata_warnings(recwarn: pytest.WarningsRecorder) -> list[warnings.WarningMessage]:
    return [w for w in recwarn.list if issubclass(w.category, VantageWarning)]


# --- mirrored constant (design.md D94) --------------------------------------


def test_the_mirrored_entry_bound_matches_the_server() -> None:
    """design.md D94: `metadata.MAX_METADATA_ENTRIES` mirrors
    `vantage.core.domain.metadata.MAX_METADATA_ENTRIES` across the RQ-24
    boundary this plugin cannot import across directly -- the same shape
    `pytest_vantage.budget._REPORT_BYTES_CAP` already uses for its own
    server mirror (`test_report_budget.py::test_the_mirrored_cap_matches_
    the_server`). A divergence here would let the plugin admit a
    declaration the server's own bound would later reject wholesale, which
    is a correctness bug, not a cosmetic one -- pinned by a test-only
    cross-package import, never trusted to stay in sync by convention.
    """
    assert metadata.MAX_METADATA_ENTRIES == _SERVER_MAX_METADATA_ENTRIES


# --- read_declaration: rejection conditions (task 6.3, design.md D92) -------


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
    # design.md D92: the key space is flat and globally unique per run --
    # detected purely from the declaration, before any file is opened.
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


# --- read_declaration: acceptance (task 6.3/6.4) ----------------------------


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
