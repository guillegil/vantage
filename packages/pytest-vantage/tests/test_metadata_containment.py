"""`pytest_vantage.metadata` -- path containment for a declared metadata file
(design.md D93, ADR-0017 C3/C4). Every fixture is a real filesystem
structure built under `tmp_path`: real symlinks (`os.symlink`), a real
symlink loop, and a real FIFO (`os.mkfifo`) where the platform supports it
-- never a mock of filesystem behaviour, the same verification approach
`test_vcs.py` already uses for the plugin's other filesystem/subprocess
boundary.

This module covers `resolve_declared_path` only. Reading and parsing the
declaration itself (`read_declaration`), the byte budget and the wire
section are later slices (design.md D92, D94, D96) -- out of scope here.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from pytest_vantage import metadata


def test_an_absolute_path_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")

    assert metadata.resolve_declared_path(root, str(outside)) is None


def test_a_dotdot_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "secret.json").write_text("{}")

    assert metadata.resolve_declared_path(root, "../secret.json") is None


def test_a_symlink_escape_is_rejected_after_resolution(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside_target = tmp_path / "outside-target.json"
    outside_target.write_text("{}")
    escape_link = root / "escape.json"
    os.symlink(outside_target, escape_link)

    assert metadata.resolve_declared_path(root, "escape.json") is None


def test_a_symlink_loop_is_rejected_not_crashed(tmp_path: Path) -> None:
    # A real symlink loop -- `os.symlink`, never a mock. `Path.resolve()`
    # raises `RuntimeError` for a loop on the interpreters that predate its
    # reimplementation over `os.path.realpath` (3.10-3.12, verified this
    # session); on 3.13 `resolve()` raises nothing at all with the default
    # `strict=False` and instead returns the path lexically unresolved, so
    # the rejection there comes from `is_file()` returning `False` rather
    # than from the exception handler. Both mechanisms must reach `None` --
    # this test asserts the outcome, not the mechanism, so it holds
    # unchanged across the whole 3.10-3.13 CI matrix without branching on
    # `sys.version_info`.
    root = tmp_path / "project"
    root.mkdir()
    loop_a = root / "loop-a"
    loop_b = root / "loop-b"
    os.symlink(loop_b, loop_a)
    os.symlink(loop_a, loop_b)

    assert metadata.resolve_declared_path(root, "loop-a") is None


def test_a_directory_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "subdir").mkdir(parents=True)

    assert metadata.resolve_declared_path(root, "subdir") is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo is POSIX-only")
def test_a_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    # Bounded wall-time, not just outcome: `open()` on a FIFO with no reader
    # blocks forever, so a containment check that opened it even once would
    # hang this test -- and, for real, `pytest_sessionstart` -- rather than
    # merely fail an assertion. No reader or writer is ever attached to this
    # FIFO, so the only way this test finishes is if `resolve_declared_path`
    # never calls `open()` on it (it only ever `stat`s, via `is_file()`).
    root = tmp_path / "project"
    root.mkdir()
    fifo_path = root / "blocking.json"
    os.mkfifo(fifo_path)

    started = time.monotonic()
    result = metadata.resolve_declared_path(root, "blocking.json")
    elapsed = time.monotonic() - started

    assert result is None
    assert elapsed < 1.0


def test_a_path_equal_to_rootpath_itself_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    assert metadata.resolve_declared_path(root, ".") is None


def test_a_missing_path_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()

    assert metadata.resolve_declared_path(root, "does-not-exist.json") is None


def test_a_legitimate_nested_path_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "project"
    nested = root / "config" / "firmware.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("firmware_version: 2.1\n")

    resolved = metadata.resolve_declared_path(root, "config/firmware.yaml")

    assert resolved == nested.resolve()


def test_a_root_reached_through_a_symlink_still_accepts_its_own_children(
    tmp_path: Path,
) -> None:
    # design.md D93: both sides are resolved, or neither works -- a
    # repository checked out under a symlinked root (`/tmp` -> `/private/tmp`
    # on macOS) must not make every legitimate path look like an escape.
    real_root = tmp_path / "real-project"
    real_root.mkdir()
    nested = real_root / "config" / "firmware.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("firmware_version: 2.1\n")
    symlinked_root = tmp_path / "symlinked-project"
    os.symlink(real_root, symlinked_root)

    resolved = metadata.resolve_declared_path(symlinked_root, "config/firmware.yaml")

    assert resolved == nested.resolve()
