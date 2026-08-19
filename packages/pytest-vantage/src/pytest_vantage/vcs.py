"""One bounded `git` read per session (design.md D43-D46, RQ-10, RQ-23,
RQ-39). `capture(rootpath)` never raises: it is its own fail-closed boundary
(follows `transport.fetch_capabilities`, D40) rather than either of
`boundary.py`'s two decorators -- both `fault_isolated` and
`liveness_isolated` **latch**, which would let one failed session silently
stop reporting results or heartbeats, turning RQ-39's "record nulls" into
"record nothing". `boundary.py` is unchanged, deliberately.

Every exception this module can encounter is swallowed inside `capture`,
named exhaustively rather than a bare `except Exception`:

- `FileNotFoundError` -- no `git` binary, even past `shutil.which`: `which`
  and `exec` can disagree (RQ-39.2), and `PATH` can change between them.
- `subprocess.TimeoutExpired` -- the whole-capture deadline (D44) elapsed.
- `OSError` (`PermissionError`, `NotADirectoryError`, `BlockingIOError`) --
  a non-executable `git`, a deleted `cwd`, a permission refusal, a fork
  failure.
- `subprocess.CalledProcessError` -- not raised here (`returncode`, never
  `check=True`), caught anyway so a later `check=True` cannot fail open.
- `UnicodeDecodeError`, `LookupError` -- decoding stdout; `errors="replace"`
  already prevents the first, `LookupError` covers a broken codec registry.
- Anything else -- `Exception`, the outer net. Never `BaseException`:
  `KeyboardInterrupt`/`SystemExit` must still reach pytest's `wrap_session`
  (RQ-31), the same rule `boundary._isolated` states.

Stdlib only (RQ-24): `subprocess`, `shutil`, `os`, `time`, `dataclasses`,
`pathlib`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# The whole capture shares this one budget (design.md D44): five
# independent 5s timeouts would cost a 25s session, against a spec
# bounding the session at *timeout + 5s* (RQ-21 criterion 4).
_CAPTURE_BUDGET_SECONDS = 5.0

_MIN_TIMEOUT_SECONDS = 0.05  # floor so an expired deadline still times out positively

# design.md D46: inherit the caller's environment, override only the keys
# that make git interactive/slow/mutating -- clearing it entirely drops
# `HOME`, so `safe.directory` is never seen and a readable repository owned
# by another uid becomes `fatal: detected dubious ownership`.
_ENV_OVERRIDES: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "LC_ALL": "C",
}

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
    # so the one caller (Recorder.__init__, design.md D51) warns once.
    warning: str | None = None


_EMPTY = VcsSnapshot()

# Named exhaustively -- see the module docstring for what raises each one.
_SWALLOWED_EXCEPTIONS: tuple[type[BaseException], ...] = (
    FileNotFoundError,
    subprocess.TimeoutExpired,
    OSError,
    subprocess.CalledProcessError,
    UnicodeDecodeError,
    LookupError,
    Exception,
)


def _field(result: subprocess.CompletedProcess[str] | None) -> str | None:
    """`None` on any failed or swallowed invocation; the stripped stdout on
    success -- shared by every field-by-field invocation (design.md D44).
    """
    return result.stdout.strip() if result is not None and result.returncode == 0 else None


def _build_env() -> dict[str, str]:
    env = dict(os.environ)
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


def capture(rootpath: Path) -> VcsSnapshot:
    """One bounded git read. Never raises. All-null on any failure of the
    gate invocation (design.md D44); the four after it are field-by-field,
    each degrading to `None` on its own failure rather than nulling the
    whole snapshot (design.md D45: a detached HEAD is invocation 3 failing
    while 2, 4, 5 succeed; no commits is invocation 2 failing while 3, 5
    succeed).
    """
    if shutil.which("git") is None:
        return _EMPTY  # RQ-39.2: zero processes spawned, silent

    env = _build_env()
    deadline = time.monotonic() + _CAPTURE_BUDGET_SECONDS

    def remaining() -> float:
        return max(_MIN_TIMEOUT_SECONDS, deadline - time.monotonic())

    def invoke(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
        # None on any swallowed failure, timeout included. A timeout here
        # ends the capture: every later invocation finds `remaining()`
        # already at the floor and times out too -- the shared, not
        # per-invocation, budget (design.md D44).
        try:
            return _run(argv, cwd=rootpath, env=env, timeout=remaining())
        except _SWALLOWED_EXCEPTIONS:
            return None

    gate = invoke(["git", "rev-parse", "--show-toplevel"])
    if gate is None or gate.returncode != 0:
        # design.md D45: not-a-repo and corrupt-repo are both exit 128.
        # The discriminator is a filesystem check, never stderr text.
        if (rootpath / ".git").exists():
            warning = _TIMEOUT_WARNING if gate is None else _CORRUPT_WARNING
            return VcsSnapshot(warning=warning)
        return _EMPTY

    root = gate.stdout.strip()

    commit = _field(invoke(["git", "rev-parse", "--verify", "--quiet", "HEAD"]))
    branch = _field(invoke(["git", "symbolic-ref", "--quiet", "--short", "HEAD"]))

    commit_subject: str | None = None
    if commit is not None:
        # design.md D44: skipped entirely when invocation 2 returned null.
        subject_argv = ["git", "show", "--no-patch", "--no-show-signature", "--format=%s", "HEAD"]
        commit_subject = _field(invoke(subject_argv))

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
