"""Path containment for a declared metadata file (design.md D93, ADR-0017
C3/C4). Standard library only (RQ-24) -- `pathlib`, nothing else.

This is a security boundary, not a convenience check: a declared path is
REJECTED, never clamped, the moment it does not resolve strictly under
`rootpath`. Clamping turns an attacker-controlled path into a different
attacker-controlled path, which is why every failure mode below returns
`None` rather than a "closest safe" substitute.

**Both sides are resolved, or neither works.** Resolving only the
candidate makes every legitimate path look like an escape the moment
`rootpath` is itself reached through a symlink -- `/tmp` is `/private/tmp`
on macOS, and the same shape recurs anywhere a checkout sits under a
symlinked home. Resolving only the root would let a committed symlink
inside the tree point at, say, `~/.ssh/id_rsa` and sail through a lexical
check. Both sides are resolved here, in that order, before anything is
compared.

**Symlinks are resolved BEFORE containment is checked**, never after: once
`Path.resolve()` has run, `is_relative_to()` is a purely lexical comparison
that touches no filesystem, so nothing on disk can change the answer
between the resolve and the containment check.

**Cross-version trap, verified rather than assumed** (2026-09-02, this
session, against real `os.symlink` loops on cpython 3.10.21, 3.11.16,
3.12.14 and 3.13.15 -- this project's floor and ceiling, and the two
versions between): a symlink loop does NOT fail the same way on every
supported interpreter. On 3.10-3.12, `Path.resolve()`'s pure-Python loop
detection raises `RuntimeError`. On 3.13, `resolve()` was reimplemented
over `os.path.realpath()`; with the default `strict=False` it raises
NOTHING for a loop at all -- it silently returns the path lexically
unresolved, and the rejection then comes from `is_file()` below returning
`False` (stat-ing through an unresolvable loop fails, and `Path.is_file()`
swallows that `OSError` per its own contract, rather than propagating it).
Both `OSError` and `RuntimeError` are caught here so that whichever
mechanism a given interpreter uses, the result is the same `None` --
catching only one of the two would leave the interpreters that use the
other one able to crash `pytest_sessionstart` on a committed symlink loop.

**TOCTOU between this resolve and a later `open()` is accepted, not
closed.** A path component can be replaced by a symlink after this function
returns and before a caller opens the file it named. Closing that window
needs `openat2(RESOLVE_BENEATH)` or per-component `O_NOFOLLOW`, neither of
which is portable across the supported 3.10-3.13 range. It is accepted
because the threat model this boundary answers is a hostile *server*
directing which files get read, and a co-worker's *committed* declaration
naming a path it should not -- not a concurrent local attacker who already
holds write access to the checkout and could simply commit the secret
directly instead of racing this check.

**What this does and does not protect against, stated plainly.** This
function protects against a declaration naming a path outside the test
repository. It does NOT protect against a colleague with commit rights
editing the committed `vantage-metadata.json` itself to name a sensitive
file that genuinely lives inside `rootpath` -- that is a code-review
problem, not a filesystem problem, and it is what ADR-0017's C4 (bounded,
drop-whole, never truncated) and C5 (the read surface is auditable on the
run afterwards) answer, not this function.
"""

from __future__ import annotations

from pathlib import Path, PurePath


def resolve_declared_path(rootpath: Path, declared: str) -> Path | None:
    """Return the resolved target for `declared`, or `None` if rejected.

    Rejected, never clamped (ADR-0017 C4): an absolute path, a path
    containing a `..` component, a path that does not resolve strictly
    under `rootpath` once every symlink in either side is followed, a path
    equal to `rootpath` itself, or anything that is not a regular file once
    resolved -- a directory, a socket, a device node, a FIFO. None of those
    is ever opened here: every check below is a `stat` (`is_file()`), never
    an `open()`, so a FIFO with no reader cannot block this function.
    """
    candidate = PurePath(declared)
    if candidate.is_absolute() or candidate.drive or candidate.anchor:
        return None
    if ".." in candidate.parts:
        return None
    try:
        root = rootpath.resolve()  # the ANCHOR, resolved first
        target = (root / candidate).resolve()  # strict=False; follows symlinks
        if not target.is_relative_to(root):  # 3.9+; pure, lexical, no I/O
            return None
        if target == root or not target.is_file():
            return None
    except (OSError, RuntimeError):
        return None
    return target


__all__ = ["resolve_declared_path"]
