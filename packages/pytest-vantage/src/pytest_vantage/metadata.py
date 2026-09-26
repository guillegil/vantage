"""Read `vantage-metadata.json`: the keys it declares for the session to
report, and the files it names.

The declaration is read in every recorded session, for its keys; the files
it names are opened only with `--vantage-metadata`. Either way a problem
with the declaration itself refuses all of it with one warning.

Path containment is a security boundary: a declared path is rejected, never
clamped, unless it resolves strictly under `rootpath` to a regular file.
Clamping would only turn one attacker-controlled path into another.

Both sides are resolved before the (purely lexical) containment check.
Resolving only the candidate makes every path look like an escape when
`rootpath` is reached through a symlink (`/tmp` is `/private/tmp` on macOS);
resolving only the root lets a committed symlink point outside the tree.

`resolve_declared_path` fails closed on any exception, not on a list of
known ones, because path operations fail differently across versions and
inputs: a symlink loop raises `RuntimeError` on 3.10-3.12 but nothing on
3.13 (where `is_file()` rejects it instead), and a NUL byte raises
`ValueError` everywhere. An exception escaping here would cost the run all
of its metadata rather than one file its status.

Not defended against: a path component swapped for a symlink between the
resolve and the later `open()` (closing that race portably is not possible
on 3.10-3.13, and winning it needs write access to the checkout, which could
commit the secret directly), and a committer declaring a sensitive file that
genuinely lives inside the repository -- that is a code-review matter, and
every file the plugin reads is recorded on the run under its declared path.
A declared path the server would drop for its shape, together with its keys
-- absolute, holding a `..` component, or read differently on Windows --
refuses the whole declaration with one warning instead, so no declared key
ever vanishes without a word.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

import pytest

from pytest_vantage.boundary import warn
from pytest_vantage.budget import encoded_cost

DECLARATION_FILENAME = "vantage-metadata.json"
"""The declaration's fixed name, at the test repository root."""

MAX_DECLARATION_BYTES = 1024 * 1024
"""Largest declaration read, in raw bytes: far more than any declaration
whose paths and keys fit `MAX_METADATA_SECTION_BYTES` needs, and small
enough that a huge file cannot exhaust memory at session start."""

MAX_DECLARED_FILES = 16
"""Bound on the number of declared files, and so on the `stat`/`open` calls
made at session start, independent of the byte budgets below."""

MAX_DECLARED_PATH_CHARS = 1024
"""Bound on a declared path's length: the same value as the server's
`MAX_IDENTITY_CHARS`, its bound on a client-chosen, path-shaped test
identity."""

MAX_METADATA_ENTRIES = 200
"""Mirrors `vantage.core.domain.metadata.MAX_METADATA_ENTRIES`, which the
plugin cannot import because it does not depend on the `vantage`
distribution. `test_server_contract.py` pins the two values equal."""

MAX_DECLARED_KEY_CHARS = 1024
"""Mirrors `vantage.core.domain.metadata.MAX_METADATA_KEY_CHARS`, pinned the
same way. An over-long key refuses the whole declaration here, like an
over-long path."""

MAX_KEY_NAME_CHARS = 256
"""Bound on a declared key's display name."""

MAX_DECLARED_FILE_BYTES = 8 * 1024
"""Largest declared file captured, in raw bytes. A larger file is dropped
whole as `too_large`, never truncated -- and so is a file within it whose
encoded content could not fit the section even with no other file
captured (a control character costs six bytes on the wire)."""

MAX_METADATA_SECTION_BYTES = 32 * 1024
"""Budget for the declaration's part of the wire `metadata` section: 1/32
of the server's 1 MiB report cap. Spent on JSON-encoded bytes via
`budget.encoded_cost` (see there for why `ensure_ascii` stays default).
Every declared key's entry and every declared file's entry -- its path,
keys and status -- is charged first, because each reaches the wire
whatever happens to the file; content is charged from what is left, so the
section holds fewer than four files of `MAX_DECLARED_FILE_BYTES` raw bytes
each."""

_ADMISSIBLE_FORMATS = frozenset({"json", "yaml"})
"""`format` is required and explicit, never inferred from the file
extension."""

_KEY_ENTRY_FIELDS = frozenset({"name"})
"""The fields a `keys` entry may carry. Anything else refuses the
declaration, as a misspelt `name` would otherwise vanish without a word."""


@dataclass(frozen=True, slots=True)
class DeclaredFile:
    """One validated `files` entry from `vantage-metadata.json`."""

    path: str
    format: str
    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeclaredKey:
    """One validated `keys` entry: a key the session is expected to report,
    and the name to display it under."""

    key: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Declaration:
    """A validated `vantage-metadata.json`."""

    keys: tuple[DeclaredKey, ...] = ()
    files: tuple[DeclaredFile, ...] = ()


@dataclass(frozen=True, slots=True)
class CapturedFile:
    """One entry of the wire `metadata.files` array. `content` is `None`
    whenever `status` is not `"captured"`: a dropped file still gets an
    entry, never an absence."""

    path: str
    format: str
    status: str
    keys: tuple[str, ...]
    content: str | None


@dataclass(frozen=True, slots=True)
class MetadataSection:
    """What every report says about the declaration: its name, the keys it
    declares, and the outcome for every file it named.

    `files` is empty when the files were not read; `unread_file_keys` then
    holds the keys they declare, which the server never gets a row for but
    which still count as declared.
    """

    declaration: str
    files: tuple[CapturedFile, ...]
    keys: tuple[DeclaredKey, ...] = ()
    unread_file_keys: tuple[str, ...] = ()


_FILE_STATUSES = (
    "captured",
    "not_found",
    "path_rejected",
    "unreadable",
    "too_large",
    "not_text",
    "over_budget",
)
"""Every status `capture_metadata` can give a declared file."""


def _section_document(
    declaration: str | None,
    keys: dict[str, str | None],
    files: Sequence[CapturedFile],
) -> dict[str, object]:
    """The wire `metadata` section. `keys` is left out when empty: the
    server defaults it, so a report that declares no key is also accepted by
    a server that predates it."""
    document: dict[str, object] = {"declaration": declaration}
    if keys:
        document["keys"] = {key: {"name": name} for key, name in keys.items()}
    document["files"] = [
        {
            "path": entry.path,
            "format": entry.format,
            "status": entry.status,
            "keys": list(entry.keys),
            "content": entry.content,
        }
        for entry in files
    ]
    return document


def wire_section(section: MetadataSection | None) -> dict[str, object] | None:
    """The `metadata` section a report carries, or `None` when it would say
    nothing: no declared key and no file read."""
    if section is None or not (section.keys or section.files):
        return None
    keys = {entry.key: entry.name for entry in section.keys}
    return _section_document(section.declaration, keys, section.files)


def _fixed_section_cost(declaration: Declaration) -> int:
    """What the declaration's wire section costs before any content: every
    declared key, and one entry per declared file with no content and the
    longest status it could end with, so the charge never depends on how
    the reads turn out."""
    longest_status = max(_FILE_STATUSES, key=len)
    placeholders = [
        CapturedFile(
            path=declared.path,
            format=declared.format,
            status=longest_status,
            keys=declared.keys,
            content=None,
        )
        for declared in declaration.files
    ]
    keys = {entry.key: entry.name for entry in declaration.keys}
    return encoded_cost(_section_document(DECLARATION_FILENAME, keys, placeholders))


_IGNORED = "the declaration is ignored"
"""How every refusal ends: nothing from a refused declaration is used, its
keys are not declared and its files are not read."""


def _reject(config: pytest.Config, message: str) -> None:
    """Warn once, through `warn`, that the declaration is refused. Callers
    follow it with `return None` rather than `return _reject(...)`, which
    mypy flags as `func-returns-value`.
    """
    warn(config, f"vantage: {message}, {_IGNORED}")


def _read_declaration_bytes(
    config: pytest.Config, rootpath: Path, *, warn_if_missing: bool
) -> bytes | None:
    """The declaration's bytes, or `None` after at most one warning.

    Read only if it is a regular file, and at most `MAX_DECLARATION_BYTES`
    of it: opening a FIFO would hang session start, and a device such as
    `/dev/zero` would be read until memory runs out. The type is checked
    before the open, the same trade the declared files make: swapping
    something else in between needs write access to the checkout.
    """
    declaration_path = rootpath / DECLARATION_FILENAME
    try:
        if not stat.S_ISREG(declaration_path.stat().st_mode):
            _reject(config, f"{DECLARATION_FILENAME} at {rootpath} is not a regular file")
            return None
        with declaration_path.open("rb") as handle:
            raw = handle.read(MAX_DECLARATION_BYTES + 1)
    except FileNotFoundError:
        if warn_if_missing:
            warn(
                config,
                f"vantage: no {DECLARATION_FILENAME} found at {rootpath}, "
                "no declared files will be read",
            )
        return None
    except OSError as exc:
        _reject(
            config,
            f"cannot read {DECLARATION_FILENAME} at {rootpath} "
            f"({exc.strerror or type(exc).__name__})",
        )
        return None
    if len(raw) > MAX_DECLARATION_BYTES:
        _reject(config, f"{DECLARATION_FILENAME} is larger than {MAX_DECLARATION_BYTES} bytes")
        return None
    return raw


class _JsonObject(dict[str, object]):
    """A decoded JSON object that remembers the names it held more than
    once, which a plain `dict` silently collapses to the last."""

    repeated: tuple[str, ...] = ()


def _json_object(pairs: list[tuple[str, object]]) -> _JsonObject:
    document = _JsonObject(pairs)
    if len(document) < len(pairs):
        seen: set[str] = set()
        repeated: list[str] = []
        for name, _value in pairs:
            if name in seen:
                repeated.append(name)
            seen.add(name)
        document.repeated = tuple(repeated)
    return document


def read_declaration(
    config: pytest.Config, rootpath: Path, *, warn_if_missing: bool = True
) -> Declaration | None:
    """Parse and validate `vantage-metadata.json` at `rootpath`.

    The declaration is the plugin's own configuration, so any problem with
    it refuses the whole declaration: warn exactly once and return `None`,
    as if there were none. (Problems with a declared *file* never warn;
    they become that file's status.) A missing declaration warns only when
    `warn_if_missing`, since declaring anything is optional unless the
    files it names were asked for. A valid declaration returns its keys and
    files in order, possibly none.

    Reads only the declaration itself, never the files it names.
    """
    raw = _read_declaration_bytes(config, rootpath, warn_if_missing=warn_if_missing)
    if raw is None:
        return None
    try:
        # `utf-8-sig` drops one leading BOM, which several Windows editors
        # write and `json.loads` refuses in a `str`.
        document = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_json_object)
    except (RecursionError, ValueError):
        # `ValueError` covers `UnicodeDecodeError`, `JSONDecodeError` and an
        # integer literal past the interpreter's digit limit, which `json`
        # raises as a plain `ValueError`.
        _reject(config, f"{DECLARATION_FILENAME} is not valid JSON")
        return None
    if not isinstance(document, dict):
        _reject(config, f"{DECLARATION_FILENAME} must be a JSON object")
        return None
    version = document.get("version")
    # By type as well as value: `true` and `1.0` both compare equal to 1.
    if type(version) is not int or version != 1:
        _reject(config, f"{DECLARATION_FILENAME} declares no supported version")
        return None
    keys = _read_declared_keys(config, document.get("keys", {}))
    if keys is None:
        return None
    files = _read_declared_files(config, document.get("files", []))
    if files is None:
        return None
    declared_keys = {entry.key for entry in keys}.union(*(declared.keys for declared in files))
    if len(declared_keys) > MAX_METADATA_ENTRIES:
        _reject(
            config,
            f"{DECLARATION_FILENAME} declares more than {MAX_METADATA_ENTRIES} keys in total",
        )
        return None
    declaration = Declaration(keys=keys, files=files)
    # Within every per-item bound, 16 paths and 200 keys can still reach
    # hundreds of kilobytes on the wire, where each entry goes whatever
    # happens to its file.
    if _fixed_section_cost(declaration) > MAX_METADATA_SECTION_BYTES:
        _reject(
            config,
            f"{DECLARATION_FILENAME}'s declared paths and keys exceed the "
            f"{MAX_METADATA_SECTION_BYTES}-byte metadata budget",
        )
        return None
    return declaration


def _read_declared_keys(config: pytest.Config, keys: object) -> tuple[DeclaredKey, ...] | None:
    """The `keys` section, in order, or `None` after one warning."""
    if not isinstance(keys, dict):
        _reject(config, f'{DECLARATION_FILENAME}\'s "keys" must be an object')
        return None
    repeated = getattr(keys, "repeated", ())
    if repeated:
        _reject(config, f"{DECLARATION_FILENAME} declares the key {repeated[0]!r} more than once")
        return None
    # Checked before the loop, so a huge section is refused without
    # validating every entry first.
    if len(keys) > MAX_METADATA_ENTRIES:
        _reject(
            config,
            f"{DECLARATION_FILENAME} declares more than {MAX_METADATA_ENTRIES} keys in total",
        )
        return None
    declared: list[DeclaredKey] = []
    for key, entry in keys.items():
        if not key or len(key) > MAX_DECLARED_KEY_CHARS:
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares an empty key or one longer than "
                f"{MAX_DECLARED_KEY_CHARS} characters",
            )
            return None
        if not isinstance(entry, dict):
            _reject(
                config, f"{DECLARATION_FILENAME} declares the key {key!r} with a malformed entry"
            )
            return None
        unknown = sorted(set(entry) - _KEY_ENTRY_FIELDS)
        if unknown:
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares the key {key!r} with the unknown field "
                f"{unknown[0]!r}",
            )
            return None
        name = entry.get("name")
        if name is not None and (not isinstance(name, str) or len(name) > MAX_KEY_NAME_CHARS):
            _reject(
                config,
                f"{DECLARATION_FILENAME} names the key {key!r} with something other than "
                f"text of at most {MAX_KEY_NAME_CHARS} characters",
            )
            return None
        declared.append(DeclaredKey(key=key, name=name))
    return tuple(declared)


def _read_declared_files(config: pytest.Config, files: object) -> tuple[DeclaredFile, ...] | None:
    """The `files` section, in order, or `None` after one warning."""
    if not isinstance(files, list) or len(files) > MAX_DECLARED_FILES:
        _reject(
            config,
            f'{DECLARATION_FILENAME}\'s "files" must be a list of at most '
            f"{MAX_DECLARED_FILES} entries",
        )
        return None
    declared: list[DeclaredFile] = []
    seen_keys: set[str] = set()
    seen_paths: set[PurePath] = set()
    for entry in files:
        if not isinstance(entry, dict):
            _reject(config, f"{DECLARATION_FILENAME} declares a malformed file entry")
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
            _reject(config, f"{DECLARATION_FILENAME} declares a malformed file entry")
            return None
        if fmt not in _ADMISSIBLE_FORMATS:
            _reject(config, f"{DECLARATION_FILENAME} declares an unsupported format {fmt!r}")
            return None
        if len(path) > MAX_DECLARED_PATH_CHARS:
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares a path longer than "
                f"{MAX_DECLARED_PATH_CHARS} characters",
            )
            return None
        if "\x00" in path:
            # A NUL byte makes `Path.resolve()` raise `ValueError`. Refuse it
            # loudly here, like any other malformed entry, rather than rely
            # on `resolve_declared_path` alone to survive it.
            _reject(config, f"{DECLARATION_FILENAME} declares a path containing a NUL character")
            return None
        if "\\" in path or PureWindowsPath(path).drive:
            # The server cannot tell which platform declared a path, so it
            # drops any path read differently on Windows: a backslash is a
            # separator there and a name character here, and `C:` or
            # `//host/share` names a drive. Refused here, loudly, rather
            # than recorded and then silently dropped with its keys.
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares the path {path!r} with a backslash or a "
                "Windows drive",
            )
            return None
        if _leaves_the_root(path):
            # The server drops these too, keys and all; `resolve_declared_path`
            # would refuse to read them anyway.
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares the path {path!r}, which is absolute or "
                "holds a '..' component",
            )
            return None
        # The server keeps one file entry per path, so a repeat would lose
        # its status there. Compared as paths, so `./a.json` repeats `a.json`.
        if PurePath(path) in seen_paths:
            _reject(config, f"{DECLARATION_FILENAME} declares the path {path!r} more than once")
            return None
        seen_paths.add(PurePath(path))
        for key in keys:
            if key in seen_keys:
                _reject(config, f"{DECLARATION_FILENAME} declares the key {key!r} more than once")
                return None
            if len(key) > MAX_DECLARED_KEY_CHARS:
                _reject(
                    config,
                    f"{DECLARATION_FILENAME} declares a key longer than "
                    f"{MAX_DECLARED_KEY_CHARS} characters",
                )
                return None
            seen_keys.add(key)
        if len(seen_keys) > MAX_METADATA_ENTRIES:
            _reject(
                config,
                f"{DECLARATION_FILENAME} declares more than {MAX_METADATA_ENTRIES} keys in total",
            )
            return None
        declared.append(DeclaredFile(path=path, format=fmt, keys=tuple(keys)))
    return tuple(declared)


def _leaves_the_root(path: str) -> bool:
    """Whether `path`, read as a POSIX or as a Windows path, is absolute or
    holds a `..` component: the shape check the server applies, which cannot
    know the declaring platform."""
    return any(
        candidate.is_absolute() or bool(candidate.anchor) or ".." in candidate.parts
        for candidate in (PurePosixPath(path), PureWindowsPath(path))
    )


def _rejected_file_status(rootpath: Path, declared_path: str) -> str:
    """Best-effort label for why `resolve_declared_path` rejected
    `declared_path`: `not_found` when nothing exists there, `path_rejected`
    for anything else (absolute, `..`, escape, not a regular file). Advisory
    only; the security decision was already made.
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
    """Read one declared file, bounded to `MAX_DECLARED_FILE_BYTES` bytes.
    Never opens a path `resolve_declared_path` rejects, and never raises:
    every failure returns a status and `None`.
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


def capture_metadata(
    config: pytest.Config, rootpath: Path, *, read_files: bool = True
) -> MetadataSection | None:
    """Read `vantage-metadata.json` and, if `read_files`, read and bound
    every file it declares.

    `None` when there is no declaration, or it is refused
    (`read_declaration` has already warned). Otherwise always a
    `MetadataSection` with the declared keys and, when the files were read,
    one entry per declared file, even when every file was dropped. When
    they were not, no file is opened and their keys are only listed.

    Every entry is charged to `MAX_METADATA_SECTION_BYTES` up front, and
    content from what is left, in declaration order. A file whose content
    does not fit the remainder is `over_budget` on its own: every file is
    read, and a later one that still fits is captured. A file whose content
    could not fit even with no other file captured is `too_large`.
    """
    declaration = read_declaration(config, rootpath, warn_if_missing=read_files)
    if declaration is None:
        return None
    if not read_files:
        return MetadataSection(
            declaration=DECLARATION_FILENAME,
            files=(),
            keys=declaration.keys,
            unread_file_keys=tuple(key for declared in declaration.files for key in declared.keys),
        )
    content_budget = MAX_METADATA_SECTION_BYTES - _fixed_section_cost(declaration)
    remaining_budget = content_budget
    captured: list[CapturedFile] = []
    for declared in declaration.files:
        status, content = _read_declared_file(rootpath, declared.path)
        if status == "captured" and content is not None:
            cost = encoded_cost(content)
            if cost > content_budget:
                status, content = "too_large", None
            elif cost > remaining_budget:
                status, content = "over_budget", None
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
    return MetadataSection(
        declaration=DECLARATION_FILENAME, files=tuple(captured), keys=declaration.keys
    )


def resolve_declared_path(rootpath: Path, declared: str) -> Path | None:
    """Return the resolved target for `declared`, or `None` if rejected.

    Rejected, never clamped: an absolute path, a path containing a `..`
    component, a path that does not resolve strictly under `rootpath` once
    every symlink on either side is followed, `rootpath` itself, or anything
    that is not a regular file once resolved (a directory, socket, device
    node or FIFO). Nothing is opened here, only `stat`ed (`is_file()`), so a
    FIFO cannot block this function.
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
        # Fail closed on any exception, not an enumerated list -- see the
        # module docstring.
        return None
    return target


__all__ = [
    "DECLARATION_FILENAME",
    "MAX_DECLARATION_BYTES",
    "MAX_DECLARED_FILE_BYTES",
    "MAX_DECLARED_FILES",
    "MAX_DECLARED_KEY_CHARS",
    "MAX_DECLARED_PATH_CHARS",
    "MAX_KEY_NAME_CHARS",
    "MAX_METADATA_ENTRIES",
    "MAX_METADATA_SECTION_BYTES",
    "CapturedFile",
    "Declaration",
    "DeclaredFile",
    "DeclaredKey",
    "MetadataSection",
    "capture_metadata",
    "read_declaration",
    "resolve_declared_path",
    "wire_section",
]
