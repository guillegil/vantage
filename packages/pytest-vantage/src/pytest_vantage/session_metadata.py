"""Run metadata the test session reports itself, through the
`vantage_metadata` fixture.

`SessionMetadata` is the mapping the fixture hands out: flat dotted keys to
text, filled by the project's own fixtures once they know what the session
runs against. The controller's `Recorder` owns the one its tests fill.
Under xdist, session fixtures run on every worker, so each worker fills its
own and hands it to the controller as its session ends (`relay`,
`unrelay`); `ReportedValues` merges them. `plan_values` then decides what
the run's last report sends, against the declaration.

A session that is not recorded gets a mapping nobody reads, so the
project's code behaves the same either way.
"""

from __future__ import annotations

import difflib
import json
import unicodedata
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

import pytest

from pytest_vantage import budget, metadata
from pytest_vantage.budget import encoded_cost
from pytest_vantage.metadata import MetadataSection, SessionValue, session_value

_SHOWN_KEY_CHARS = 80
"""How much of a key a warning about it shows: a key far past the length
bound must not flood the terminal."""

_SUGGESTED_KEY_CHARS = 128
"""Longest key a close match is looked for, or among. A typo is a slip in a
short key, and comparing two keys takes time quadratic in their length."""


def _plain(text: str) -> str:
    """`text` as a plain `str`. A subclass, such as a string enum's member,
    keeps only its text: xdist cannot carry the subclass from a worker, and
    how one formats changes across Python versions."""
    return str.__str__(text)


def _text(value: object) -> str | None:
    """`value` as the text recorded for it, or `None` for no value.

    `bool` before `int`, which it subclasses. Numbers go through `str`, so
    `1.10` becomes `"1.1"`: a project that cares about the spelling passes
    a string. Lists and tuples become compact JSON; anything else goes
    through `str`, so a version object records as it prints. Raises when
    the value cannot be turned into text.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return _plain(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, (list, tuple)):
        text = json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    return _plain(text)


def _key_problem(key: str, *, leaf: bool) -> str | None:
    """Why `key` cannot be recorded, or `None`. Only a leaf can be empty: a
    mapping under an empty key still yields `.name` keys."""
    if leaf and not key:
        return "a key must not be empty"
    if len(key) > metadata.MAX_DECLARED_KEY_CHARS:
        return f"a key must be at most {metadata.MAX_DECLARED_KEY_CHARS} characters"
    # A newline, a NUL or an escape sequence cannot be shown in a listing or
    # typed into a filter as what it is.
    if any(unicodedata.category(char) == "Cc" for char in key):
        return "a key must not contain a control character"
    return None


def _shown(key: str) -> str:
    return repr(key if len(key) <= _SHOWN_KEY_CHARS else key[:_SHOWN_KEY_CHARS] + "...")


def _not_a_string(prefix: str | None, key: object) -> str:
    """How a warning names a key that is not a string, under `prefix`."""
    try:
        part = repr(key)
    except Exception:
        part = f"<{type(key).__name__}>"
    return part if prefix is None else f"{prefix}.{part}"


class SessionMetadata(MutableMapping[str, "str | None"]):
    """Run metadata the session reports: flat dotted keys to text, or to
    `None` for a key reported without a value.

    Setting a mapping flattens it under its key with `.`, depth first in the
    mapping's own order, and sets each leaf as if it were set on its own: a
    leaf set again is replaced, and keys the mapping does not mention are
    left alone. A value is turned into text as it is set (`_text`).

    Setting never raises into the caller: a key that cannot be recorded, or
    a value that cannot be turned into text, is skipped with one warning
    naming it. Reading a key that was never set raises `KeyError`, as for
    any mapping.
    """

    def __init__(self, warn: Callable[[str], None] | None = None) -> None:
        """`warn` receives each warning. Without it problems are skipped
        silently, as befits a session that records nothing."""
        self._values: dict[str, str | None] = {}
        self._warn = warn

    def __getitem__(self, key: str) -> str | None:
        return self._values[key]

    def __delitem__(self, key: str) -> None:
        del self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._values!r})"

    def __setitem__(self, key: str, value: object) -> None:
        # Iterative rather than recursive, so a structure nested past the
        # interpreter's recursion limit cannot raise into the caller either.
        # `walking` holds the `id` of every mapping being walked, outermost
        # first, one per frame of `walks` after the first.
        walks: list[tuple[str | None, Iterator[tuple[object, object]]]] = [
            (None, iter([(key, value)]))
        ]
        walking: list[int] = []
        while walks:
            prefix, items = walks[-1]
            try:
                sub_key, sub_value = next(items)
            except StopIteration:
                walks.pop()
                if walks:
                    walking.pop()
                continue
            if not isinstance(sub_key, str):
                self._skip(_not_a_string(prefix, sub_key), "a key must be a string")
                continue
            flat_key = _plain(sub_key) if prefix is None else f"{prefix}.{_plain(sub_key)}"
            try:
                if isinstance(sub_value, Mapping):
                    problem = _key_problem(flat_key, leaf=False)
                    if problem is None and id(sub_value) in walking:
                        problem = "its value contains itself"
                    if problem is None:
                        children = list(sub_value.items())
                        walking.append(id(sub_value))
                        walks.append((flat_key, iter(children)))
                else:
                    problem = _key_problem(flat_key, leaf=True)
                    if problem is None:
                        self._values[flat_key] = _text(sub_value)
            except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
                problem = f"its value cannot be turned into text ({type(exc).__name__})"
            if problem is not None:
                self._skip(flat_key, problem)

    def update(self, other: object = (), /, **kwargs: object) -> None:
        """Set every pair of `other` (a mapping, or an iterable of pairs) and
        then of `kwargs`, as `[]=` would. Typed to take nested mappings and
        any value, and never raises: an `other` that cannot be read as
        pairs is skipped with one warning."""
        source: Any = other
        try:
            if isinstance(source, Mapping):
                pairs = list(source.items())
            elif hasattr(source, "keys"):
                pairs = [(key, source[key]) for key in source.keys()]
            else:
                pairs = [(key, value) for key, value in source]
        except Exception as exc:  # never BaseException: Ctrl-C must still stop the run
            pairs = []
            if self._warn is not None:
                self._warn(
                    "vantage: ignoring a metadata update that is neither a mapping nor "
                    f"pairs ({type(exc).__name__})"
                )
        for key, value in [*pairs, *kwargs.items()]:
            self[key] = value

    def entries(self) -> list[SessionValue]:
        """Every key set, in the order first set, as the wire carries it."""
        return [session_value(key, text) for key, text in self._values.items()]

    def _skip(self, key: str, reason: str) -> None:
        if self._warn is not None:
            self._warn(f"vantage: ignoring the metadata key {_shown(key)}: {reason}")


SESSION_METADATA: pytest.StashKey[SessionMetadata] = pytest.StashKey()
"""Where the `vantage_metadata` fixture finds the mapping of a recorded
session, on the controller or on an xdist worker."""


def relay(entries: Iterable[SessionValue]) -> str:
    """`entries` as the JSON text an xdist worker hands its controller.

    Text, ASCII-escaped, rather than lists of strings: xdist refuses to
    carry a string that does not encode as UTF-8, and a lone surrogate
    (text decoded with `surrogateescape`) would crash the worker as its
    session ends. The escapes carry it through unchanged.
    """
    return json.dumps([[entry.key, entry.value, entry.status] for entry in entries])


def unrelay(relayed: object) -> list[SessionValue]:
    """The entries `relay` wrote, skipping anything of another shape."""
    if not isinstance(relayed, str):
        return []
    try:
        items = json.loads(relayed)
    except (RecursionError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return [
        SessionValue(item[0], item[1], item[2])
        for item in items
        if isinstance(item, list)
        and len(item) == 3
        and isinstance(item[0], str)
        and (item[1] is None or isinstance(item[1], str))
        and item[2] in metadata.SESSION_VALUE_STATUSES
    ]


class ReportedValues:
    """The values every part of a session reported, merged in the order
    received. The first value received for a key is kept; a key received
    again with a different one is listed in `conflicting`."""

    def __init__(self) -> None:
        self._values: dict[str, SessionValue] = {}
        self._conflicting: dict[str, None] = {}

    def add(self, entries: Iterable[SessionValue]) -> None:
        for entry in entries:
            if self._values.setdefault(entry.key, entry) != entry:
                self._conflicting[entry.key] = None

    def entries(self) -> list[SessionValue]:
        return list(self._values.values())

    @property
    def conflicting(self) -> list[str]:
        return list(self._conflicting)


@dataclass(frozen=True, slots=True)
class ValuesPlan:
    """What the run's last report sends of the reported values, and what
    there is to warn about.

    - `values`: the wire `values`, in order.
    - `named_keys`: keys of unread declared files among them, which the
      report declares in `keys` (see `metadata.wire_section`).
    - `shadowed`: keys the session reported that a declared file also
      supplies; the file's row is kept.
    - `undeclared`: keys sent that the declaration does not declare.
    - `capped`, `over_budget`: how many were left out past the entry cap,
      and past `budget.MAX_METADATA_VALUES_BYTES`.
    """

    values: tuple[SessionValue, ...] = ()
    named_keys: tuple[str, ...] = ()
    shadowed: tuple[str, ...] = ()
    undeclared: tuple[str, ...] = ()
    declared: frozenset[str] = frozenset()
    capped: int = 0
    over_budget: int = 0


def plan_values(reported: Iterable[SessionValue], section: MetadataSection | None) -> ValuesPlan:
    """Decide what the last report sends of `reported`, against the
    declaration `section` describes (`None` for none).

    A run holds at most `metadata.MAX_METADATA_ENTRIES` rows: the keys of
    the files read first, as the server stores them from the start report,
    then the session's keys in the order first set, then an `absent` row
    for every key the declaration's `keys` section names that nothing
    supplied. A key a read file also supplies is not sent: the file's row
    is kept. Everything is computed here; the server derives no row.
    """
    section_keys = () if section is None else tuple(entry.key for entry in section.keys)
    read_file_keys = frozenset(
        () if section is None else (key for entry in section.files for key in entry.keys)
    )
    unread_file_keys = frozenset(() if section is None else section.unread_file_keys)
    declared = frozenset(section_keys) | read_file_keys | unread_file_keys

    reported = list(reported)
    reported_keys = {entry.key for entry in reported}
    shadowed = tuple(entry.key for entry in reported if entry.key in read_file_keys)
    candidates = [entry for entry in reported if entry.key not in read_file_keys]
    candidates += [
        session_value(key, None)
        for key in section_keys
        if key not in reported_keys and key not in read_file_keys
    ]
    within_cap = candidates[: max(metadata.MAX_METADATA_ENTRIES - len(read_file_keys), 0)]

    room = budget.MAX_METADATA_VALUES_BYTES
    values: list[SessionValue] = []
    named_keys: list[str] = []
    for entry in within_cap:
        named = entry.key in unread_file_keys and entry.key not in section_keys
        cost = encoded_cost({"key": entry.key, "value": entry.value, "status": entry.status})
        cost += len(", ") + (encoded_cost({entry.key: {"name": None}}) if named else 0)
        if cost > room:
            break
        room -= cost
        values.append(entry)
        if named:
            named_keys.append(entry.key)

    return ValuesPlan(
        values=tuple(values),
        named_keys=tuple(named_keys),
        shadowed=shadowed,
        undeclared=tuple(entry.key for entry in values if declared and entry.key not in declared),
        declared=declared,
        capped=len(candidates) - len(within_cap),
        over_budget=len(within_cap) - len(values),
    )


def _listed(keys: Iterable[str]) -> str:
    return ", ".join(repr(key) for key in keys)


def _undeclared_warning(undeclared: Iterable[str], declared: Collection[str]) -> str:
    """One line naming every undeclared key, with the closest declared key
    where there is one."""
    candidates = sorted(key for key in declared if len(key) <= _SUGGESTED_KEY_CHARS)
    named: list[str] = []
    for key in undeclared:
        close = (
            difflib.get_close_matches(key, candidates, n=1)
            if len(key) <= _SUGGESTED_KEY_CHARS
            else []
        )
        named.append(f"{key!r} (did you mean {close[0]!r}?)" if close else repr(key))
    return f"vantage: undeclared metadata keys: {', '.join(named)}"


def plan_warnings(plan: ValuesPlan, conflicting: Collection[str] = ()) -> list[str]:
    """The warnings `plan` and the workers' `conflicting` keys call for, at
    most one of each kind."""
    warnings: list[str] = []
    if conflicting:
        warnings.append(
            f"vantage: xdist workers reported different values for the metadata keys "
            f"{_listed(conflicting)}, the first value received is kept"
        )
    if plan.shadowed:
        warnings.append(
            f"vantage: the metadata keys {_listed(plan.shadowed)} are read from declared "
            "files and also reported by the session, the file values are kept"
        )
    if plan.capped:
        warnings.append(
            f"vantage: {plan.capped} metadata key(s) left out: a run records at most "
            f"{metadata.MAX_METADATA_ENTRIES}, those read from declared files first"
        )
    if plan.over_budget:
        warnings.append(
            f"vantage: {plan.over_budget} metadata key(s) left out: the values reported "
            f"exceed the {budget.MAX_METADATA_VALUES_BYTES} bytes a run's last report holds"
        )
    if plan.undeclared:
        warnings.append(_undeclared_warning(plan.undeclared, plan.declared))
    return warnings


__all__ = [
    "SESSION_METADATA",
    "ReportedValues",
    "SessionMetadata",
    "ValuesPlan",
    "plan_values",
    "plan_warnings",
    "relay",
    "unrelay",
]
