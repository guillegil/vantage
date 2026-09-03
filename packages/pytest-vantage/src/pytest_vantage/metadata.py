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

import json
from dataclasses import dataclass
from pathlib import Path, PurePath

import pytest

from pytest_vantage.boundary import _warn

DECLARATION_FILENAME = "vantage-metadata.json"
"""The declaration's one fixed, well-known name, at the test repository
root (design.md D92). Formerly a private constant of the same name and
value in `recorder.py`; that copy is gone once this module is wired in
(design.md D99's presence check and this module's own read now agree on
one spelling, by construction rather than by two literals kept in step)."""

MAX_DECLARED_FILES = 16
"""design.md D94: a bound on the declaration's *read surface* -- the
`stat`+`open` syscall count this design commits to at session start
(D99) -- independent of the byte budget a later slice adds, which already
bounds bytes."""

MAX_DECLARED_PATH_CHARS = 1024
"""design.md D94: `MAX_IDENTITY_CHARS` -- a path-shaped, client-supplied
value, the same bound D89 already argued for one of these."""

MAX_METADATA_ENTRIES = 200
"""Mirrors `vantage.core.domain.metadata.MAX_METADATA_ENTRIES` across the
RQ-24 boundary this plugin cannot import across directly -- the same shape
`pytest_vantage.budget._REPORT_BYTES_CAP` already uses to mirror
`vantage.service.errors.MAX_REPORT_BYTES`. Pinned by a test-only
cross-package import (`test_metadata_declaration.py`), never trusted to
stay in sync by convention alone."""

_ADMISSIBLE_FORMATS = frozenset({"json", "yaml"})
"""design.md D92: `format` is required and explicit, never inferred from
the file extension. `"toml"` is a one-value widening of this set later,
with no other change."""


@dataclass(frozen=True, slots=True)
class DeclaredFile:
    """One validated entry from `vantage-metadata.json` (design.md D92)."""

    path: str
    format: str
    keys: tuple[str, ...]


def _reject(config: pytest.Config, message: str) -> None:
    """Warn once, through `_warn` -- every one of `read_declaration`'s
    rejection conditions is the plugin's own file, so it may be refused
    outright (design.md D92). `-> None` is deliberate; callers use
    `_reject(...); return None` as two statements, never `return
    _reject(...)`, so mypy's `func-returns-value` check does not treat a
    `None`-returning helper's result as a value being threaded through.
    """
    _warn(config, f"vantage: {message}")


def read_declaration(config: pytest.Config, rootpath: Path) -> tuple[DeclaredFile, ...] | None:
    """Parse and validate `vantage-metadata.json` at `rootpath` (design.md
    D92).

    The declaration is the plugin's own file, so -- unlike a *declared
    document*, which never warns and never fails ingestion (D97) -- it may
    be refused outright: refusing captures nothing, exactly what the
    flag-absent path already does. Every rejection below warns **exactly
    once**, through `_warn`, and returns `None`. A valid declaration
    returns its ordered file list, which may be empty.

    Never opens or reads any file the declaration *names* -- only the
    declaration itself. Reading the files it names is `capture_metadata`'s
    job, added in a later slice.
    """
    declaration_path = rootpath / DECLARATION_FILENAME
    try:
        raw = declaration_path.read_bytes()
    except OSError:
        _reject(
            config, f"no {DECLARATION_FILENAME} found at {rootpath}, metadata will not be captured"
        )
        return None
    try:
        text = raw.decode("utf-8")
        document = json.loads(text)
    except (UnicodeDecodeError, RecursionError, json.JSONDecodeError):
        _reject(config, f"{DECLARATION_FILENAME} is not valid JSON, metadata will not be captured")
        return None
    if not isinstance(document, dict):
        _reject(
            config, f"{DECLARATION_FILENAME} must be a JSON object, metadata will not be captured"
        )
        return None
    if document.get("version") != 1:
        _reject(
            config,
            f"{DECLARATION_FILENAME} declares no supported version, metadata will not be captured",
        )
        return None
    files = document.get("files")
    if not isinstance(files, list) or len(files) > MAX_DECLARED_FILES:
        _reject(
            config,
            f'{DECLARATION_FILENAME}\'s "files" must be a list of at most '
            f"{MAX_DECLARED_FILES} entries, metadata will not be captured",
        )
        return None
    declared: list[DeclaredFile] = []
    seen_keys: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares a malformed file entry, "
                "metadata will not be captured",
            )
            return None
        path = entry.get("path")
        fmt = entry.get("format")
        keys = entry.get("keys")
        if (
            not isinstance(path, str)
            or not isinstance(fmt, str)
            or not isinstance(keys, list)
            or not all(isinstance(key, str) for key in keys)
        ):
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares a malformed file entry, "
                "metadata will not be captured",
            )
            return None
        if fmt not in _ADMISSIBLE_FORMATS:
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares an unsupported format {fmt!r}, "
                "metadata will not be captured",
            )
            return None
        if len(path) > MAX_DECLARED_PATH_CHARS:
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares a path longer than "
                f"{MAX_DECLARED_PATH_CHARS} characters, metadata will not be captured",
            )
            return None
        for key in keys:
            if key in seen_keys:
                _reject(
                    config,
                    f"{DECLARATION_FILENAME} declares the key {key!r} more than once, "
                    "metadata will not be captured",
                )
                return None
            seen_keys.add(key)
        if len(seen_keys) > MAX_METADATA_ENTRIES:
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares more than {MAX_METADATA_ENTRIES} keys "
                "in total, metadata will not be captured",
            )
            return None
        declared.append(DeclaredFile(path=path, format=fmt, keys=tuple(keys)))
    return tuple(declared)


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


__all__ = [
    "DECLARATION_FILENAME",
    "MAX_DECLARED_FILES",
    "MAX_DECLARED_PATH_CHARS",
    "MAX_METADATA_ENTRIES",
    "DeclaredFile",
    "read_declaration",
    "resolve_declared_path",
]
