"""`pytest_vantage.vcs` -- the plugin's one bounded git read per session
(design.md D43-D46). Every fixture is a real repository built with
`subprocess`/`tmp_path`, never mocked git output (spec's own verification
method). Tests that patch `subprocess.run` still spawn real processes via
`_CallRecorder` -- the patch only counts or forwards calls, never
fabricates stdout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from pytest_vantage import vcs

# Set via the environment, not `git config`, so no fixture touches or
# leaves behind a real `~/.gitconfig`.
_GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "Vantage Test",
    "GIT_AUTHOR_EMAIL": "vantage-test@example.invalid",
    "GIT_COMMITTER_NAME": "Vantage Test",
    "GIT_COMMITTER_EMAIL": "vantage-test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _fixture_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(_GIT_IDENTITY_ENV)
    return env


def _git(
    *args: str, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    # Literal argv from fixture constants, never `shell=True` -- same false
    # positive `transport.py`'s own `noqa: S310` comments document.
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env=env if env is not None else _fixture_env(),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=10,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result


def _init_repo(root: Path) -> Path:  # no commits yet
    root.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=root)
    return root


def _repo_with_one_commit(root: Path, *, filename: str = "tracked.txt") -> Path:
    _init_repo(root)
    (root / filename).write_text("first version\n")
    _git("add", filename, cwd=root)
    _git("commit", "-q", "-m", "initial commit", cwd=root)
    return root


def _independent_head(root: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=root).stdout.strip()


def _assert_all_null(snapshot: vcs.VcsSnapshot) -> None:
    assert snapshot.commit is None
    assert snapshot.branch is None
    assert snapshot.commit_subject is None
    assert snapshot.dirty is None
    assert snapshot.root is None


class _CallRecorder:
    """Wraps real `subprocess.run` to record every call's argv/kwargs while
    still letting it execute for real -- never fabricates stdout."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self._real_run = subprocess.run

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), dict(kwargs)))
        return self._real_run(argv, **kwargs)


@pytest.mark.req(id="RQ-10")
def test_dirty_tracked_file_marks_run_dirty(tmp_path: Path) -> None:
    # Arm 1: an unstaged worktree modification to a tracked file.
    worktree_repo = _repo_with_one_commit(tmp_path / "worktree-dirty")
    (worktree_repo / "tracked.txt").write_text("modified in the worktree\n")

    assert vcs.capture(worktree_repo).dirty is True

    # Arm 2: a staged-only change -- nothing left in the worktree diff.
    staged_repo = _repo_with_one_commit(tmp_path / "staged-dirty")
    (staged_repo / "tracked.txt").write_text("staged change\n")
    _git("add", "tracked.txt", cwd=staged_repo)

    assert vcs.capture(staged_repo).dirty is True

    # Triangulation: `--untracked-files=no` (design.md D44) makes an
    # untracked-only file invisible to `dirty` -- RQ-10.1 says *tracked*.
    untracked_repo = _repo_with_one_commit(tmp_path / "untracked-only")
    (untracked_repo / "untracked.txt").write_text("never added\n")

    assert vcs.capture(untracked_repo).dirty is False


@pytest.mark.req(id="RQ-10")
def test_clean_tree_matches_independent_head_read(tmp_path: Path) -> None:
    repo = _repo_with_one_commit(tmp_path / "clean")
    expected_commit = _independent_head(repo)

    snapshot = vcs.capture(repo)

    assert snapshot.commit == expected_commit and snapshot.dirty is False


@pytest.mark.req(id="RQ-10")
def test_detached_head_records_commit_null_branch(tmp_path: Path) -> None:
    repo = _repo_with_one_commit(tmp_path / "detached")
    (repo / "tracked.txt").write_text("second version\n")
    _git("commit", "-q", "-am", "second commit", cwd=repo)
    first_commit = _git("rev-parse", "HEAD~1", cwd=repo).stdout.strip()
    _git("checkout", "-q", first_commit, cwd=repo)

    snapshot = vcs.capture(repo)

    assert snapshot.commit == first_commit
    assert snapshot.branch is None


@pytest.mark.req(id="RQ-10")
def test_no_commits_yet_stores_null_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _init_repo(tmp_path / "empty-repo")
    recorder = _CallRecorder()
    monkeypatch.setattr(vcs.subprocess, "run", recorder)  # type: ignore[attr-defined]

    snapshot = vcs.capture(repo)

    assert snapshot.commit is None
    # Invocation 4 (`git show ... HEAD`) must never be spawned when
    # invocation 2 (`git rev-parse --verify --quiet HEAD`) returned null --
    # design.md D44's "skipped entirely" rule. That leaves exactly four
    # invocations: show-toplevel, rev-parse HEAD, symbolic-ref, status.
    assert len(recorder.calls) == 4
    assert not any(argv[1] == "show" for argv, _kwargs in recorder.calls)


@pytest.mark.req(id="RQ-23")
def test_not_a_repository_records_nulls_and_no_warning(tmp_path: Path) -> None:
    bare_dir = tmp_path / "not-a-repo"
    bare_dir.mkdir()

    snapshot = vcs.capture(bare_dir)

    _assert_all_null(snapshot)
    assert snapshot.warning is None


@pytest.mark.req(id="RQ-39")
def test_corrupt_git_entry_records_nulls_and_warns_once(tmp_path: Path) -> None:
    # Fixture 1: a `.git` *file* with garbage, not a `gitdir: ...` pointer.
    garbage_file_repo = tmp_path / "corrupt-git-file"
    garbage_file_repo.mkdir()
    (garbage_file_repo / ".git").write_text("not a valid gitfile pointer\n")

    file_snapshot = vcs.capture(garbage_file_repo)

    _assert_all_null(file_snapshot)
    assert file_snapshot.warning is not None

    # Fixture 2: a `.git` *directory* with a truncated `HEAD`, no objects.
    truncated_head_repo = tmp_path / "corrupt-git-dir"
    (truncated_head_repo / ".git").mkdir(parents=True)
    (truncated_head_repo / ".git" / "HEAD").write_text("ref: ")

    dir_snapshot = vcs.capture(truncated_head_repo)

    _assert_all_null(dir_snapshot)
    assert dir_snapshot.warning is not None


@pytest.mark.req(id="RQ-39")
def test_missing_git_executable_records_nulls_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Scrubs PATH for real -- never mock.patch("subprocess.run"), which
    # proves the mock, not FileNotFoundError (spec's own verification note).
    repo = _repo_with_one_commit(tmp_path / "repo-with-real-git")
    empty_path_dir = tmp_path / "empty-path"
    empty_path_dir.mkdir()
    monkeypatch.setenv("PATH", str(empty_path_dir))

    snapshot = vcs.capture(repo)

    _assert_all_null(snapshot)
    assert snapshot.warning is None


@pytest.mark.req(id="RQ-39")
@pytest.mark.skipif(
    os.geteuid() == 0 if hasattr(os, "geteuid") else True,
    reason="chmod 000 is a no-op as root; skip rather than pass vacuously",
)
def test_permissions_restricted_repository(tmp_path: Path) -> None:
    repo = _repo_with_one_commit(tmp_path / "restricted")
    git_dir = repo / ".git"
    os.chmod(git_dir, 0o000)
    try:
        snapshot = vcs.capture(repo)
    finally:
        os.chmod(git_dir, 0o755)  # noqa: S103 -- restoring the fixture, not granting access

    _assert_all_null(snapshot)


def test_monorepo_subdirectory_records_toplevel(tmp_path: Path) -> None:
    repo = _repo_with_one_commit(tmp_path / "monorepo")
    subdirectory = repo / "packages" / "some-package"
    subdirectory.mkdir(parents=True)
    (subdirectory / "module.py").write_text("# nothing\n")
    # Independent read of the same question -- robust to any symlink
    # resolution git applies (e.g. macOS's `/tmp` -> `/private/tmp`).
    expected_toplevel = _git("rev-parse", "--show-toplevel", cwd=subdirectory).stdout.strip()

    snapshot = vcs.capture(subdirectory)

    assert snapshot.root == expected_toplevel and snapshot.commit is not None


def test_argv_discipline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_one_commit(tmp_path / "argv-discipline")
    recorder = _CallRecorder()
    monkeypatch.setattr(vcs.subprocess, "run", recorder)  # type: ignore[attr-defined]

    vcs.capture(repo)

    assert recorder.calls, "no subprocess.run calls recorded -- the inspection would pass vacuously"
    repo_str = str(repo)
    for argv, kwargs in recorder.calls:
        assert argv[0] == "git"
        assert all(isinstance(element, str) for element in argv)
        assert repo_str not in argv  # no repo-derived value is ever an argv element
        assert kwargs.get("shell") is False
        assert kwargs.get("cwd") == repo
        assert kwargs.get("stdin") == subprocess.DEVNULL


def _write_sleeping_git_shim(directory: Path, *, sleep_seconds: float, forward: bool) -> None:
    # A fake `git` that sleeps, then hangs forever or forwards to the real
    # `git` via `os.execv` -- not another `subprocess` call, which would
    # leave a lingering child.
    real_git = shutil.which("git")
    assert real_git is not None, "the real git must be resolvable to build this fixture"
    shim_path = directory / "git"
    if forward:
        body = (
            f"import os, sys, time\n"
            f"time.sleep({sleep_seconds})\n"
            f"os.execv({real_git!r}, [{real_git!r}] + sys.argv[1:])\n"
        )
    else:
        body = f"import time\ntime.sleep({sleep_seconds})\n"
    shim_path.write_text(f"#!{sys.executable}\n{body}")
    shim_path.chmod(0o755)


@pytest.mark.slow
@pytest.mark.slow
def test_hung_git_bounded_at_capture_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    _write_sleeping_git_shim(shim_dir, sleep_seconds=30, forward=False)
    monkeypatch.setenv("PATH", str(shim_dir))
    repo_marker = tmp_path / "some-project"
    repo_marker.mkdir()

    started = time.monotonic()
    snapshot = vcs.capture(repo_marker)
    elapsed = time.monotonic() - started

    assert elapsed < vcs._CAPTURE_BUDGET_SECONDS + 1.0
    _assert_all_null(snapshot)


@pytest.mark.slow
@pytest.mark.slow
def test_whole_capture_budget_not_per_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A 3s-per-invocation shim that forwards to real git. Per-invocation 5s
    # timeouts would let all five succeed (15s); one shared 5s budget cannot.
    repo = _repo_with_one_commit(tmp_path / "slow-git-repo")
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    _write_sleeping_git_shim(shim_dir, sleep_seconds=3.0, forward=True)
    monkeypatch.setenv("PATH", str(shim_dir) + os.pathsep + os.environ.get("PATH", ""))

    started = time.monotonic()
    snapshot = vcs.capture(repo)
    elapsed = time.monotonic() - started

    assert elapsed < 10.0  # five independent 5s timeouts would tolerate up to 25s
    assert snapshot.root is not None  # the gate succeeds (3s < the initial 5s budget)
    # ...but not every later invocation fits inside the shared budget.
    assert not (snapshot.commit and snapshot.branch and snapshot.dirty is not None)
