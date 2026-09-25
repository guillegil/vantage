"""`pytest_vantage.metadata.read_declaration`, and the bounds it mirrors
from the server.

Every fixture is a real filesystem structure under `tmp_path`, never a mock
of filesystem behaviour.
"""

from __future__ import annotations

import codecs
import json
import os
import subprocess
import sys
import threading
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_vantage import metadata
from pytest_vantage.boundary import VantageWarning
from vantage.core.domain.metadata import FILE_STATUSES as _SERVER_FILE_STATUSES
from vantage.core.domain.metadata import MAX_METADATA_ENTRIES as _SERVER_MAX_METADATA_ENTRIES
from vantage.core.domain.metadata import MAX_METADATA_KEY_CHARS as _SERVER_MAX_METADATA_KEY_CHARS


def _config() -> pytest.Config:
    # `warn` only reaches `config.pluginmanager` when `warnings.warn`
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


def test_the_mirrored_file_statuses_match_the_server_but_malformed() -> None:
    """Each declared entry is charged to the section budget with the longest
    status in `metadata._FILE_STATUSES`, so a status the plugin gains must
    land there. The server accepts no status it does not know, and assigns
    `malformed` itself, after parsing.
    """
    assert set(metadata._FILE_STATUSES) == _SERVER_FILE_STATUSES - {"malformed"}


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
    assert f"no {metadata.DECLARATION_FILENAME} found" in str(warned[0].message)


def test_a_directory_named_like_the_declaration_is_not_reported_as_absent(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    root = tmp_path / "project"
    (root / metadata.DECLARATION_FILENAME).mkdir(parents=True)

    result = metadata.read_declaration(_config(), root)

    assert result is None
    warned = _metadata_warnings(recwarn)
    assert len(warned) == 1
    assert "not a regular file" in str(warned[0].message)


def test_a_declaration_in_a_symlink_loop_is_not_reported_as_absent(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    # Unreadable even as root, unlike the permission case below.
    root = tmp_path / "project"
    root.mkdir()
    os.symlink(metadata.DECLARATION_FILENAME, root / metadata.DECLARATION_FILENAME)

    result = metadata.read_declaration(_config(), root)

    assert result is None
    warned = _metadata_warnings(recwarn)
    assert len(warned) == 1
    assert "cannot read" in str(warned[0].message)


@pytest.mark.skipif(
    os.geteuid() == 0 if hasattr(os, "geteuid") else True,
    reason="chmod 000 is a no-op as root; skip rather than pass vacuously",
)
def test_an_unreadable_declaration_is_not_reported_as_absent(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    declaration = root / metadata.DECLARATION_FILENAME
    declaration.write_text(json.dumps({"version": 1, "files": []}))
    os.chmod(declaration, 0o000)

    try:
        result = metadata.read_declaration(_config(), root)
    finally:
        os.chmod(declaration, 0o644)  # noqa: S103 -- restoring the fixture, not granting access

    assert result is None
    warned = _metadata_warnings(recwarn)
    assert len(warned) == 1
    assert "cannot read" in str(warned[0].message)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo is POSIX-only")
def test_a_fifo_declaration_is_refused_without_blocking(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    # Opening a FIFO with no writer blocks, which would hang session start.
    # Run in a thread so a regression fails the test rather than hanging it.
    root = tmp_path / "project"
    root.mkdir()
    fifo = root / metadata.DECLARATION_FILENAME
    os.mkfifo(fifo)
    outcome: list[object] = []
    worker = threading.Thread(
        target=lambda: outcome.append(metadata.read_declaration(_config(), root)), daemon=True
    )

    worker.start()
    worker.join(timeout=5)
    blocked = worker.is_alive()
    if blocked:  # release it: opening the write end unblocks the reader
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(timeout=5)

    assert not blocked, "read_declaration blocked opening a FIFO"
    assert outcome == [None]
    assert len(_metadata_warnings(recwarn)) == 1


@pytest.mark.skipif(not Path(os.devnull).exists(), reason="needs a null device")
def test_a_declaration_linked_to_a_device_is_refused_unread(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    # The null device stands in for `/dev/zero`, which the same check stops
    # before a read that would never end.
    root = tmp_path / "project"
    root.mkdir()
    os.symlink(os.devnull, root / metadata.DECLARATION_FILENAME)

    result = metadata.read_declaration(_config(), root)

    assert result is None
    warned = _metadata_warnings(recwarn)
    assert len(warned) == 1
    assert "not a regular file" in str(warned[0].message)


@pytest.mark.parametrize(
    ("padding", "accepted"), [(0, True), (1, False)], ids=["at_the_bound", "one_byte_over"]
)
def test_the_declaration_is_read_only_up_to_its_byte_bound(
    tmp_path: Path, recwarn: pytest.WarningsRecorder, padding: int, accepted: bool
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    document = json.dumps({"version": 1, "files": []})
    # Trailing whitespace is valid JSON, so only the size decides.
    size = metadata.MAX_DECLARATION_BYTES + padding
    (root / metadata.DECLARATION_FILENAME).write_text(document.ljust(size))

    result = metadata.read_declaration(_config(), root)

    assert (result == ()) is accepted
    assert len(_metadata_warnings(recwarn)) == (0 if accepted else 1)


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is enforced on Linux")
def test_a_huge_declaration_is_refused_without_being_read_whole(tmp_path: Path) -> None:
    # A sparse file far larger than the child's capped address space: reading
    # it whole raises MemoryError there instead of exhausting the host.
    root = tmp_path / "project"
    root.mkdir()
    with (root / metadata.DECLARATION_FILENAME).open("wb") as handle:
        handle.truncate(4 * 1024**3)
    script = (
        "import resource, sys, warnings\n"
        "from pathlib import Path\n"
        "from types import SimpleNamespace\n"
        "from pytest_vantage import metadata\n"
        "resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    result = metadata.read_declaration(SimpleNamespace(), Path(sys.argv[1]))\n"
        "print(result, len(caught))\n"
    )

    completed = subprocess.run(  # noqa: S603 -- this interpreter, a literal script
        [sys.executable, "-c", script, str(root)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["None", "1"]


@pytest.mark.parametrize(
    "raw",
    [
        b"{not json",
        # Past the interpreter's integer-string digit limit, which `json`
        # raises as a plain `ValueError`. Without a limit it parses to an
        # unsupported version, refused all the same.
        b'{"version": ' + b"1" * 4301 + b', "files": []}',
        b"[" * 100_000 + b"]" * 100_000,
        b"\xff{}",
    ],
    ids=["syntax_error", "overlong_integer", "nested_too_deep", "not_utf8"],
)
def test_a_non_json_declaration_captures_nothing_and_warns_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder, raw: bytes
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / metadata.DECLARATION_FILENAME).write_bytes(raw)

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


@pytest.mark.parametrize(
    "document",
    [
        {"files": []},
        {"version": 2, "files": []},
        # Equal to 1 in Python, but not the integer 1.
        {"version": True, "files": []},
        {"version": 1.0, "files": []},
        {"version": "1", "files": []},
    ],
    ids=["missing", "two", "true", "float", "string"],
)
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


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"path": "g.json", "format": "json"}, {"path": "g.json", "format": "json"}),
        ({"path": "./g.json", "format": "json"}, {"path": "g.json", "format": "json"}),
        ({"path": "g.json", "format": "json"}, {"path": "g.json", "format": "yaml"}),
    ],
    ids=["same_path", "same_file_spelled_differently", "different_format"],
)
def test_a_path_declared_twice_captures_nothing_and_warns_once(
    tmp_path: Path,
    recwarn: pytest.WarningsRecorder,
    first: dict[str, object],
    second: dict[str, object],
) -> None:
    # The server keeps one file entry per path and would silently drop the
    # second one's status; the plugin would read and charge the file twice.
    root = tmp_path / "project"
    root.mkdir()
    files = [{**first, "keys": ["a"]}, {**second, "keys": ["b"]}]
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


@pytest.mark.parametrize(
    "path",
    [
        "config\\app.json",
        "\\\\server\\share\\app.json",
        "C:/config/app.json",
        "C:app.json",
        "//server/share/app.json",
    ],
    ids=["backslash", "unc-backslashes", "drive-absolute", "drive-relative", "unc-slashes"],
)
def test_a_windows_shaped_path_captures_nothing_and_warns_naming_it(
    tmp_path: Path, recwarn: pytest.WarningsRecorder, path: str
) -> None:
    """The server drops a path that reads differently on Windows, keys and
    all, so the plugin refuses the declaration instead of sending an entry
    that would vanish. `C:/config/app.json` would even resolve here, to a
    directory named `C:`, and be captured for nothing.
    """
    root = tmp_path / "project"
    (root / "C:" / "config").mkdir(parents=True)
    (root / "C:" / "config" / "app.json").write_text("{}")
    entry = {"path": path, "format": "json", "keys": ["k"]}
    (root / metadata.DECLARATION_FILENAME).write_text(json.dumps({"version": 1, "files": [entry]}))

    result = metadata.read_declaration(_config(), root)

    assert result is None
    (warned,) = _metadata_warnings(recwarn)
    assert repr(path) in str(warned.message)


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


@pytest.mark.parametrize("char", ["k", "\u4e2d", "\U0001f600"], ids=["ascii", "cjk", "non_bmp"])
def test_paths_and_keys_beyond_the_section_budget_capture_nothing_and_warn_once(
    tmp_path: Path, recwarn: pytest.WarningsRecorder, char: str
) -> None:
    # Every entry reaches the wire whatever happens to its file, so a
    # declaration within every per-item bound can still outgrow the section.
    root = tmp_path / "project"
    root.mkdir()
    keys = [
        f"{i:03d}{char * (metadata.MAX_DECLARED_KEY_CHARS - 3)}"
        for i in range(metadata.MAX_METADATA_ENTRIES)
    ]
    files = [
        {
            "path": f"{i:02d}{char * (metadata.MAX_DECLARED_PATH_CHARS - 7)}.json",
            "format": "json",
            "keys": keys[i :: metadata.MAX_DECLARED_FILES],
        }
        for i in range(metadata.MAX_DECLARED_FILES)
    ]
    document = json.dumps({"version": 1, "files": files}, ensure_ascii=False)
    (root / metadata.DECLARATION_FILENAME).write_text(document, encoding="utf-8")

    result = metadata.read_declaration(_config(), root)

    assert result is None
    warned = _metadata_warnings(recwarn)
    assert len(warned) == 1
    assert "budget" in str(warned[0].message)


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


def test_a_declaration_saved_with_a_utf8_bom_is_accepted(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    # Several Windows editors and PowerShell write one by default.
    root = tmp_path / "project"
    root.mkdir()
    files = [{"path": "f.json", "format": "json", "keys": ["k"]}]
    document = json.dumps({"version": 1, "files": files}).encode()
    (root / metadata.DECLARATION_FILENAME).write_bytes(codecs.BOM_UTF8 + document)

    result = metadata.read_declaration(_config(), root)

    assert result == (metadata.DeclaredFile(path="f.json", format="json", keys=("k",)),)
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
