"""One bounded `git` read per session.

`capture(rootpath)` never raises: it is its own fail-closed boundary rather
than using `boundary.py`'s decorators, which latch after one failure and
would stop the session's later reports and heartbeats. A failed git read
records nulls; it must not stop recording.

Every `Exception` is swallowed inside `capture`. The ones expected are:

- `FileNotFoundError` -- no `git` binary, even past `shutil.which`: `which`
  and `exec` can disagree, and `PATH` can change between them.
- `subprocess.TimeoutExpired` -- the whole-capture deadline elapsed; the
  only failure the warning calls a timeout.
- `OSError` (`PermissionError`, `NotADirectoryError`, `BlockingIOError`) --
  a non-executable `git`, a deleted `cwd`, a permission refusal, a fork
  failure.
- `subprocess.CalledProcessError` -- not raised here (`returncode`, never
  `check=True`), caught anyway so a later `check=True` cannot fail open.
- `UnicodeDecodeError`, `LookupError` -- decoding stdout; `errors="replace"`
  already prevents the first, `LookupError` covers a broken codec registry.

Never `BaseException`: `KeyboardInterrupt`/`SystemExit` must still reach
pytest's `wrap_session`, the same rule `boundary._isolated` follows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# The whole capture shares this one budget: five independent 5s timeouts
# could delay session start by 25s instead of 5s.
_CAPTURE_BUDGET_SECONDS = 5.0

_MIN_TIMEOUT_SECONDS = 0.05  # floor so an expired deadline still times out positively

# Inherit the caller's environment except the variables below, and override
# only the keys that make git interactive/slow/mutating -- clearing it
# entirely drops `HOME`, so `safe.directory` is never seen and a readable
# repository owned by another uid becomes `fatal: detected dubious ownership`.
_ENV_OVERRIDES: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "LC_ALL": "C",
}

# Removed so that git discovers the repository from `cwd=rootpath`. Once
# GIT_DIR is set git skips discovery, so an exported one -- a dotfiles
# manager's, or the one git itself gives a hook in a linked worktree --
# would point every read at another repository, or make `rootpath` the top
# of the work tree. These are git's `rev-parse --local-env-vars` minus the
# config entries (they can carry `safe.directory`), plus the variables that
# scope the refs.
#
# GIT_CEILING_DIRECTORIES is kept: git never exports it, so it is always
# someone's deliberate limit on the upward search -- typically to keep a
# dotfiles repository in $HOME out of every project below it -- and a run
# must not record a repository git itself refuses to find.
_REPOSITORY_SELECTING_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_GRAFT_FILE",
    "GIT_SHALLOW_FILE",
    "GIT_REPLACE_REF_BASE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_PREFIX",
    "GIT_NAMESPACE",
)

_BRANCH_REF_PREFIX = "refs/heads/"

_TIMEOUT_WARNING = "could not read the git repository (timed out)"
_CORRUPT_WARNING = "could not read the git repository"


@dataclass(frozen=True, slots=True)
class VcsSnapshot:
    """One `capture()` result. Every field defaults to `None`, so
    `VcsSnapshot()` alone is the all-null failure snapshot."""

    commit: str | None = None
    branch: str | None = None
    commit_subject: str | None = None
    dirty: bool | None = None
    root: str | None = None
    # What to say, or None to stay silent -- returned, never emitted here,
    # so the one caller (Recorder.__init__) warns once.
    warning: str | None = None


_EMPTY = VcsSnapshot()


def _field(result: subprocess.CompletedProcess[str] | None) -> str | None:
    """`None` on any failed or swallowed invocation; the stripped stdout on
    success.
    """
    return result.stdout.strip() if result is not None and result.returncode == 0 else None


_MAX_SUBJECT_BYTES = 64 * 1024 + 1024
"""The plugin's cap, deliberately ABOVE the server's 64 KiB bound. Cutting at
or below it would deliver a long subject already short, so the server would
find nothing to truncate and record `vcs_commit_subject_truncated = 0` -- a
false zero it has no way to detect. The extra kibibyte keeps that flag
honest.
"""


def _bounded_subject(value: str | None) -> str | None:
    """One line, and never large enough to threaten the report's size cap.

    `git`'s `%s` folds a multi-line first paragraph into one line, so the
    newline cut is belt-and-braces rather than the primary mechanism -- but
    it costs nothing and this value is stored, so a newline that ever did
    escape must not be able to forge a second log line.

    The byte cap is not belt-and-braces. `%s` is unbounded: a commit message
    whose first paragraph is 200 000 characters yields a 200 001-byte
    subject, measured, and without this the session report could exceed
    `MAX_REPORT_BYTES` and be rejected as a unit -- losing every result in
    that session to a commit message. Sliced on UTF-8 bytes at a character
    boundary, never on characters.
    """
    if value is None:
        return None
    line = value.split("\n", 1)[0]
    encoded = line.encode("utf-8")
    if len(encoded) <= _MAX_SUBJECT_BYTES:
        return line
    return encoded[:_MAX_SUBJECT_BYTES].decode("utf-8", errors="ignore")


def _build_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _REPOSITORY_SELECTING_ENV}
    env.update(_ENV_OVERRIDES)
    env.pop("SSH_ASKPASS", None)  # removed, not overridden -- no ssh-safe empty value
    return env


def _run(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    # Every invocation shares this call site: shell=False always, argv a
    # list of literal constants, cwd=rootpath -- never `git -C <string>`.
    return subprocess.run(  # noqa: S603 -- argv is always a literal list, shell=False always
        argv,
        cwd=cwd,
        env=env,
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        shell=False,
    )


def _ceiling_directories() -> set[Path]:
    """The directories `GIT_CEILING_DIRECTORIES` stops git's upward search
    at, read as git reads them: absolute entries only, each resolved unless
    it follows an empty entry."""
    ceilings: set[Path] = set()
    resolve = True
    for entry in os.environ.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep):
        if not entry:
            resolve = False
        elif os.path.isabs(entry):
            ceilings.add(Path(entry).resolve() if resolve else Path(entry))
    return ceilings


def _inside_a_repository(rootpath: Path) -> bool:
    """Whether a `.git` entry exists where git looks for one: at `rootpath`,
    then in each directory above it up to, not including, the first ceiling
    directory.

    git searches upward, so a broken or hung repository whose top is above
    `rootpath` (a sub-package with its own pytest config) must still warn;
    one beyond the ceiling git never looks at must not. Resolved first
    because git walks the physical path. A failed check stays silent, as if
    there were no repository.
    """
    try:
        start = rootpath.resolve()
        ceilings = _ceiling_directories()
        for directory in (start, *start.parents):
            if directory != start and directory in ceilings:
                return False
            if (directory / ".git").exists():
                return True
        return False
    except Exception:  # `capture` never raises, and this only chooses a warning
        return False


def capture(rootpath: Path) -> VcsSnapshot:
    """One bounded git read. Never raises.

    All-null on any failure of the gate invocation (`rev-parse
    --show-toplevel`). The four after it are field-by-field, each degrading
    to `None` on its own failure rather than nulling the whole snapshot: a
    detached HEAD fails only `symbolic-ref`; a repository with no commits
    fails `rev-parse HEAD`, which also skips `git show`.
    """
    if shutil.which("git") is None:
        return _EMPTY  # no git on PATH: spawn nothing, warn nothing

    env = _build_env()
    deadline = time.monotonic() + _CAPTURE_BUDGET_SECONDS
    timed_out = False

    def remaining() -> float:
        return max(_MIN_TIMEOUT_SECONDS, deadline - time.monotonic())

    def invoke(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
        # None on any swallowed failure, timeout included. A timeout here
        # ends the capture: the budget is shared, so every later invocation
        # finds `remaining()` already at the floor and times out too.
        nonlocal timed_out
        try:
            return _run(argv, cwd=rootpath, env=env, timeout=remaining())
        except subprocess.TimeoutExpired:
            timed_out = True
            return None
        except Exception:  # the module docstring lists what is expected here
            return None

    gate = invoke(["git", "rev-parse", "--show-toplevel"])
    if gate is None or gate.returncode != 0:
        # Not-a-repo and corrupt-repo are both exit 128. The discriminator
        # is a filesystem check, never stderr text.
        if _inside_a_repository(rootpath):
            warning = _TIMEOUT_WARNING if timed_out else _CORRUPT_WARNING
            return VcsSnapshot(warning=warning)
        return _EMPTY

    root = gate.stdout.strip()

    commit = _field(invoke(["git", "rev-parse", "--verify", "--quiet", "HEAD"]))
    # The full ref, stripped here: `--short` keeps a `heads/` prefix whenever
    # a tag or another ref shares the branch's name.
    head_ref = _field(invoke(["git", "symbolic-ref", "--quiet", "HEAD"]))
    branch: str | None = None
    if head_ref is not None and head_ref.startswith(_BRANCH_REF_PREFIX):
        branch = head_ref[len(_BRANCH_REF_PREFIX) :]

    commit_subject: str | None = None
    if commit is not None:
        # `--` so that a file or directory named HEAD in rootpath cannot make
        # the revision ambiguous, which git refuses.
        subject_argv = [
            "git",
            "show",
            "--no-patch",
            "--no-show-signature",
            "--format=%s",
            "HEAD",
            "--",
        ]
        commit_subject = _bounded_subject(_field(invoke(subject_argv)))

    dirty_field = _field(invoke(["git", "status", "--porcelain", "--untracked-files=no"]))
    dirty = None if dirty_field is None else bool(dirty_field)

    return VcsSnapshot(
        commit=commit,
        branch=branch,
        commit_subject=commit_subject,
        dirty=dirty,
        root=root,
        warning=None,
    )


__all__ = ["VcsSnapshot", "capture"]
