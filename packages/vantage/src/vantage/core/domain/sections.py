"""Section definitions, longest-prefix-wins derivation, and the per-run
pass-percentage aggregate.

Stdlib only. ``UNASSIGNED`` is a module-level plain ``str``, never an
``Enum`` and never a one-member class, for the ``__format__`` reason
recorded in ``liveness.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

UNASSIGNED = "unassigned"
"""The reserved bucket name every result matching no section falls into."""

SECTION_NAME_MAX_CHARS = 120
"""Bound on a section name -- `LIST_COMMIT_SUBJECT_CHARS`'s display width,
the same class of value: a label read in a list."""

SECTION_PREFIX_MAX_CHARS = 1024
"""Bound on a section prefix -- `MAX_IDENTITY_CHARS`, already the bound on
a path-shaped client value elsewhere in this codebase."""

MAX_SECTIONS = 200
"""Bound on stored sections -- `MAX_PAGE_ITEMS`. The run summary is
unpaginated, so this cap on stored sections is also the bound on that
response."""


@dataclass(frozen=True, slots=True)
class SectionDefinition:
    """One section's name and its normalized prefix."""

    name: str
    prefix: str


def normalize_prefix(prefix: str) -> str:
    """Strip surrounding whitespace and coerce exactly one trailing `/`.

    Idempotent: normalizing an already-normalized prefix returns it
    unchanged. This coercion is what keeps a prefix from bleeding into a
    similarly-named sibling directory -- `tests/SectA` is stored as
    `tests/SectA/`, so it can never match `tests/SectAlpha/test_x.py`.
    """
    stripped = prefix.strip()
    return stripped if stripped.endswith("/") else f"{stripped}/"


def derive_section(file_path: str, sections: Sequence[SectionDefinition]) -> str:
    """Return the name of the section whose prefix is the longest
    byte-exact, case-sensitive match against `file_path`.

    Matching is `str.startswith`, which agrees with `schema.sql`'s default
    BINARY collation by construction (no `COLLATE` clause anywhere). No
    write-time overlap validation exists here: `tests/` and `tests/SectA/`
    coexisting is the broad-then-narrow editing workflow, resolved at read
    time by preferring the longer prefix. Among equal-length matches --
    which can only occur when two sections share an identical prefix -- the
    alphabetically first name wins, so the answer never depends on row
    order in either adapter. A `file_path` matching no section derives as
    `UNASSIGNED`.
    """
    matches = [section for section in sections if file_path.startswith(section.prefix)]
    if not matches:
        return UNASSIGNED
    best = min(matches, key=lambda section: (-len(section.prefix), section.name))
    return best.name


@dataclass(frozen=True, slots=True)
class SectionSummary:
    """One bucket's four published numbers.

    `total` counts every result classified into this bucket, skipped
    included. `measured` excludes `skipped` from the pass-percentage
    denominator, so `total - measured` is exactly the skipped count.
    `passing` is the numerator. `pass_percentage` is `None`, never `0.0` or
    `100.0`, when `measured == 0` -- an empty bucket and a fully-skipped
    bucket report the identical wire value. Otherwise it reads `100.0` only
    when every measured result passed and `0.0` only when none did.
    """

    name: str
    total: int
    measured: int
    passing: int
    pass_percentage: float | None


@dataclass(frozen=True, slots=True)
class RunSectionSummary:
    """A run's whole section summary: every defined section, alphabetical
    by name, plus the always-present `unassigned` bucket in its own field,
    never inside `items`."""

    items: tuple[SectionSummary, ...]
    unassigned: SectionSummary


def _pass_percentage(passing: int, measured: int) -> float | None:
    if not measured:
        return None
    percentage = round(100 * passing / measured, 1)
    # One failure among 2000 rounds to 100.0, and one pass among 2001 to
    # 0.0 -- what a fully green or fully red bucket reports. Pin a ratio
    # that only rounds to an end one step inside it, so both ends stay exact.
    if percentage == 100.0 and passing < measured:
        return 99.9
    if percentage == 0.0 and passing > 0:
        return 0.1
    return percentage


def _summarize_bucket(name: str, outcomes: Sequence[str]) -> SectionSummary:
    total = len(outcomes)
    passed = outcomes.count("passed")
    failed = outcomes.count("failed")
    error = outcomes.count("error")
    xfailed = outcomes.count("xfailed")
    xpassed = outcomes.count("xpassed")
    passing = passed + xfailed
    measured = passed + failed + error + xfailed + xpassed
    pass_percentage = _pass_percentage(passing, measured)
    return SectionSummary(
        name=name,
        total=total,
        measured=measured,
        passing=passing,
        pass_percentage=pass_percentage,
    )


def summarize_sections(
    case_outcomes: Iterable[tuple[str, str]],
    sections: Sequence[SectionDefinition],
) -> RunSectionSummary:
    """Classify every `(file_path, outcome)` pair into its derived section
    and compute each bucket's four numbers.

    Published identities a client can check without trusting the server:
    `sum(item.total for item in items) + unassigned.total` equals the run's
    result count, and `abs(100 * item.passing / item.measured -
    item.pass_percentage) < 0.1` for every bucket with `measured > 0`.
    Rounding happens once, here, to one decimal; `100.0` still means every
    measured result passed and `0.0` that none did.
    """
    buckets: dict[str, list[str]] = {section.name: [] for section in sections}
    buckets[UNASSIGNED] = []
    for file_path, outcome in case_outcomes:
        buckets[derive_section(file_path, sections)].append(outcome)

    items = tuple(
        _summarize_bucket(section.name, buckets[section.name])
        for section in sorted(sections, key=lambda section: section.name)
    )
    unassigned = _summarize_bucket(UNASSIGNED, buckets[UNASSIGNED])
    return RunSectionSummary(items=items, unassigned=unassigned)


__all__ = [
    "MAX_SECTIONS",
    "SECTION_NAME_MAX_CHARS",
    "SECTION_PREFIX_MAX_CHARS",
    "UNASSIGNED",
    "RunSectionSummary",
    "SectionDefinition",
    "SectionSummary",
    "derive_section",
    "normalize_prefix",
    "summarize_sections",
]
