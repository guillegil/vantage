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

**A third exception mechanism arrived after the first two were already
enumerated and verified** (2026-09-04, `sdd-verify`, confirmed on 3.10.21
and 3.13.15): a declared path containing a NUL byte makes `Path.resolve()`
raise `ValueError`, on every supported interpreter, not `OSError` or
`RuntimeError`. `isinstance(e, (OSError, RuntimeError))` is `False` for it
on both ends of the supported range, so the enumerated tuple this
docstring used to stop at let it through uncaught -- straight into
`pytest_configure`, with nothing on the call chain above to catch it, and
`INTERNALERROR` for the whole session. **The lesson generalises past this
one addition: this boundary must fail closed on any exception a path
operation can raise, not on a list of the ones observed so far.** A list
is a claim about which failure modes exist, and this module has now
disproved that claim twice from the standard library alone -- once across
CPython minor versions, once across argument content -- with the second
one being fatal precisely because the first one had already been "fixed"
by naming its cause instead of its shape. `except Exception` below is
deliberately broader than a tuple for that reason: the two statements
after the `try` (`is_relative_to`, `is_file`) do no I/O this function
cannot already tolerate failing, hold no resource that broad catching
could leak, and have exactly one safe outcome on any error -- return
`None`, the same rejection every other branch of this function already
returns for a hostile or malformed path. Broad catching is usually
the wrong call because it can hide a bug the caller needed to see; here
there is no caller-visible invariant left to hide, only a security
boundary whose only two exits are "resolved and contained" or "rejected,"
and every exception belongs on the rejected side.

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
from pytest_vantage.budget import _encoded_cost

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

MAX_DECLARED_KEY_CHARS = 1024
"""Mirrors `vantage.core.domain.metadata.MAX_METADATA_KEY_CHARS` across the
same RQ-24 boundary `MAX_METADATA_ENTRIES` above already mirrors, for the
same reason: a declared key is refused here, in `read_declaration`, before
any `run_metadata` row exists, so no server-side status class or schema
change is needed for it (`sdd-verify` WARNING-1) -- the whole declaration
is refused outright, exactly like `MAX_DECLARED_PATH_CHARS` beside it.
Pinned by the same test-only cross-package import as `MAX_METADATA_ENTRIES`
(`test_metadata_declaration.py`), never trusted to stay in sync by
convention alone."""

MAX_DECLARED_FILE_BYTES = 8 * 1024
"""design.md D94: the largest a declared file may be such that four of
them at full size still fit `MAX_METADATA_SECTION_BYTES` -- keeps the two
bounds coherent rather than one admitting what the other always rejects."""

MAX_METADATA_SECTION_BYTES = 32 * 1024
"""design.md D94: `4 * MAX_DECLARED_FILE_BYTES`, and `MAX_REPORT_BYTES //
32`. Spent on JSON-encoded bytes, via `budget._encoded_cost`'s exact rule
(no `ensure_ascii=False` -- see that function's own docstring for why)."""

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


@dataclass(frozen=True, slots=True)
class CapturedFile:
    """One entry of the wire `metadata.files` array (design.md D96):
    `content` is `None` whenever `status` is not `"captured"` -- the same
    "declared-but-dropped is a row, not an absence" contract D95 states
    for the storage side, kept on the wire too."""

    path: str
    format: str
    status: str
    keys: tuple[str, ...]
    content: str | None


@dataclass(frozen=True, slots=True)
class MetadataSection:
    """The wire `metadata` section (design.md D96): the declaration's own
    name, and the outcome recorded for every file it named."""

    declaration: str
    files: tuple[CapturedFile, ...]


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
        if "\x00" in path:
            # ADR-0017 C4: rejected outright, at the declaration boundary,
            # rather than left to crash `resolve_declared_path` -- a NUL
            # byte makes `Path.resolve()` raise `ValueError` on every
            # supported interpreter (sdd-verify CRITICAL-1). Refusing it
            # here, loudly, is the "declaration is the plugin's own file"
            # branch this module's docstring already claims for every
            # other malformed entry; silently dropping it would leave the
            # plugin dependent on `resolve_declared_path`'s own defence
            # alone to avoid a crash.
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares a path containing a NUL character, "
                "metadata will not be captured",
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
            if len(key) > MAX_DECLARED_KEY_CHARS:
                _reject(
                    config,
                    f"{DECLARATION_FILENAME} declares a key longer than "
                    f"{MAX_DECLARED_KEY_CHARS} characters, metadata will not be captured",
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


def _rejected_file_status(rootpath: Path, declared_path: str) -> str:
    """Best-effort, non-security label for *why* `resolve_declared_path`
    rejected `declared_path` (design.md D97 classes 1 and 2): `not_found`
    when nothing exists at that path at all, `path_rejected` for every
    other reason (absolute, `..`, escape, not a regular file). Advisory
    only -- the security decision was already made by `resolve_declared_
    path` returning `None`; this only recovers a friendlier reason for the
    wire and the reader's three-state contract (D95).
    """
    candidate = PurePath(declared_path)
    if candidate.is_absolute() or candidate.drive or candidate.anchor:
        return "path_rejected"
    if ".." in candidate.parts:
        return "path_rejected"
    try:
        exists = (rootpath / candidate).exists()
    except OSError:
        return "path_rejected"
    return "not_found" if not exists else "path_rejected"


def _read_declared_file(rootpath: Path, declared_path: str) -> tuple[str, str | None]:
    """Read one declared file, bounded to `MAX_DECLARED_FILE_BYTES` bytes
    (design.md D97 classes 1-5). Never opens a path `resolve_declared_path`
    rejects, and never raises: every failure returns a status and `None`.
    """
    target = resolve_declared_path(rootpath, declared_path)
    if target is None:
        return _rejected_file_status(rootpath, declared_path), None
    try:
        with target.open("rb") as handle:
            raw = handle.read(MAX_DECLARED_FILE_BYTES + 1)
    except OSError:
        return "unreadable", None
    if len(raw) > MAX_DECLARED_FILE_BYTES:
        return "too_large", None
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "not_text", None
    return "captured", content


def capture_metadata(config: pytest.Config, rootpath: Path) -> MetadataSection | None:
    """Read, bound and ship every file `vantage-metadata.json` declares
    (design.md D93-D97, D94's section budget). `None` only when the
    declaration itself could not be read or validated -- `read_declaration`
    has already warned exactly once for that case. Otherwise always a
    `MetadataSection`, even when every file it names ends up dropped:
    D95's "every declared thing gets a row" contract holds whether or not
    capture succeeded.

    The section budget is spent in declaration order (D97 class 6): once
    one file's encoded cost does not fit the remainder, every file from
    that point on -- not just the one that overflowed -- is dropped whole
    and marked `over_budget`, without being opened.
    """
    declared_files = read_declaration(config, rootpath)
    if declared_files is None:
        return None
    remaining_budget = MAX_METADATA_SECTION_BYTES
    budget_exhausted = False
    captured: list[CapturedFile] = []
    for declared in declared_files:
        if budget_exhausted:
            captured.append(
                CapturedFile(
                    path=declared.path,
                    format=declared.format,
                    status="over_budget",
                    keys=declared.keys,
                    content=None,
                )
            )
            continue
        status, content = _read_declared_file(rootpath, declared.path)
        if status == "captured" and content is not None:
            cost = _encoded_cost(content)
            if cost > remaining_budget:
                status, content = "over_budget", None
                budget_exhausted = True
            else:
                remaining_budget -= cost
        captured.append(
            CapturedFile(
                path=declared.path,
                format=declared.format,
                status=status,
                keys=declared.keys,
                content=content,
            )
        )
    return MetadataSection(declaration=DECLARATION_FILENAME, files=tuple(captured))


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
    except Exception:
        # Fail closed on ANY exception a path operation can raise, not on
        # an enumerated list -- see the module docstring for why a tuple
        # of exception types was already proven incomplete once.
        return None
    return target


__all__ = [
    "DECLARATION_FILENAME",
    "MAX_DECLARED_FILE_BYTES",
    "MAX_DECLARED_FILES",
    "MAX_DECLARED_KEY_CHARS",
    "MAX_DECLARED_PATH_CHARS",
    "MAX_METADATA_ENTRIES",
    "MAX_METADATA_SECTION_BYTES",
    "CapturedFile",
    "DeclaredFile",
    "MetadataSection",
    "capture_metadata",
    "read_declaration",
    "resolve_declared_path",
]
