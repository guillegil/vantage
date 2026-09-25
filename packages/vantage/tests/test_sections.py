"""Section derivation and the per-run pass-percentage aggregate.

Stdlib only -- `vantage.core.domain.sections` has no I/O, so nothing here
needs a store, a fixture, or a temporary file.
"""

from __future__ import annotations

import pytest
from vantage.core.domain.sections import (
    UNASSIGNED,
    RunSectionSummary,
    SectionDefinition,
    SectionSummary,
    derive_section,
    normalize_prefix,
    summarize_sections,
)

# ---------------------------------------------------------------------------
# SectionDefinition: the reserved name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [UNASSIGNED, "Unassigned", "UNASSIGNED"])
def test_a_section_cannot_be_named_unassigned(name: str) -> None:
    """`derive_section` answers `UNASSIGNED` for a result matching no
    section, so a section by that name would share the unassigned bucket and
    `summarize_sections` would report its results twice."""
    with pytest.raises(ValueError, match="reserved"):
        SectionDefinition(name=name, prefix="tests/a/")


def test_a_name_merely_containing_unassigned_is_an_ordinary_section() -> None:
    sections = [SectionDefinition(name="unassigned-legacy", prefix="tests/a/")]

    summary = summarize_sections([("tests/a/test_x.py", "passed")], sections)

    assert [item.name for item in summary.items] == ["unassigned-legacy"]
    assert summary.unassigned.total == 0


# ---------------------------------------------------------------------------
# normalize_prefix: coercion, sibling non-bleed
# ---------------------------------------------------------------------------


def test_normalize_prefix_coerces_a_missing_trailing_slash() -> None:
    """A missing trailing slash is coerced on write."""
    assert normalize_prefix("tests/SectA") == "tests/SectA/"


def test_normalize_prefix_is_idempotent() -> None:
    once = normalize_prefix("tests/SectA")
    twice = normalize_prefix(once)

    assert once == twice == "tests/SectA/"


def test_normalize_prefix_strips_surrounding_whitespace_before_coercing() -> None:
    assert normalize_prefix("  tests/SectA  ") == "tests/SectA/"


def test_a_coerced_prefix_does_not_bleed_into_a_similarly_named_sibling() -> None:
    """A prefix does not bleed into a similarly-named sibling.

    The coercion is the whole point: without it, `tests/SectA` (no trailing
    slash) would match `tests/SectAlpha/test_x.py` as a plain string prefix.
    """
    sections = [SectionDefinition(name="SectA", prefix=normalize_prefix("tests/SectA"))]

    assert derive_section("tests/SectAlpha/test_x.py", sections) == UNASSIGNED


# ---------------------------------------------------------------------------
# derive_section: longest prefix wins
# ---------------------------------------------------------------------------


def test_derive_section_the_longest_matching_prefix_wins() -> None:
    """The longest matching prefix wins over a shorter one."""
    sections = [
        SectionDefinition(name="Tests", prefix="tests/"),
        SectionDefinition(name="SectA", prefix="tests/SectA/"),
    ]

    assert derive_section("tests/SectA/test_x.py", sections) == "SectA"


def test_derive_section_no_match_derives_as_unassigned() -> None:
    sections = [SectionDefinition(name="Billing", prefix="tests/billing/")]

    assert derive_section("tests/checkout/test_x.py", sections) == UNASSIGNED
    assert derive_section("tests/checkout/test_x.py", []) == UNASSIGNED


def test_derive_section_an_equal_length_tie_is_broken_alphabetically_by_name() -> None:
    """Among equal-length matching prefixes, the alphabetically first name
    wins -- the only way a tie can occur is two sections sharing an
    identical prefix."""
    sections = [
        SectionDefinition(name="Zeta", prefix="tests/dup/"),
        SectionDefinition(name="Alpha", prefix="tests/dup/"),
    ]

    assert derive_section("tests/dup/test_x.py", sections) == "Alpha"


def test_derive_section_matching_is_case_sensitive() -> None:
    sections = [SectionDefinition(name="SectA", prefix="tests/SectA/")]

    assert derive_section("tests/secta/test_x.py", sections) == UNASSIGNED


# ---------------------------------------------------------------------------
# summarize_sections: pass percentage, ordering, unassigned bucket
# ---------------------------------------------------------------------------


def test_summarize_sections_mixed_outcomes_yield_94_4() -> None:
    """xfailed counts as passing and skipped is excluded from the denominator.

    80 passed, 5 xfailed, 2 xpassed, 3 failed, 10 skipped -> passing=85,
    measured=90, pass_percentage=94.4 (85/90).
    """
    sections = [SectionDefinition(name="Billing", prefix="tests/billing/")]
    case_outcomes = (
        [("tests/billing/test_x.py", "passed")] * 80
        + [("tests/billing/test_x.py", "xfailed")] * 5
        + [("tests/billing/test_x.py", "xpassed")] * 2
        + [("tests/billing/test_x.py", "failed")] * 3
        + [("tests/billing/test_x.py", "skipped")] * 10
    )

    summary = summarize_sections(case_outcomes, sections)

    assert summary.items == (
        SectionSummary(name="Billing", total=100, measured=90, passing=85, pass_percentage=94.4),
    )
    # `total - measured` is exactly the skipped count.
    assert summary.items[0].total - summary.items[0].measured == 10


def _single_bucket(passed: int, failed: int) -> SectionSummary:
    sections = [SectionDefinition(name="Billing", prefix="tests/billing/")]
    case_outcomes = [("tests/billing/test_x.py", "passed")] * passed + [
        ("tests/billing/test_x.py", "failed")
    ] * failed
    return summarize_sections(case_outcomes, sections).items[0]


@pytest.mark.parametrize(
    ("passed", "failed", "expected"),
    [
        (1999, 1, 99.9),
        (19999, 1, 99.9),
        (1, 2000, 0.1),
        (1, 19999, 0.1),
        (2000, 0, 100.0),
        (0, 2000, 0.0),
    ],
)
def test_pass_percentage_reads_100_or_0_only_when_exactly_true(
    passed: int, failed: int, expected: float
) -> None:
    """One failure among 2000 rounds to 100.0 and one pass among 2001 to
    0.0 -- the values a fully green or fully red bucket reports. Only a
    bucket that really is all-passing or none-passing reads as either."""
    assert _single_bucket(passed, failed).pass_percentage == expected


def test_pass_percentage_satisfies_the_published_client_check() -> None:
    """The check `summarize_sections` documents holds for every bucket of
    up to 100 measured results -- the exact float equality
    `passing / measured == pass_percentage / 100` would not, since the
    percentage is rounded (85/90 publishes 94.4)."""
    for measured in range(1, 101):
        for passing in range(measured + 1):
            percentage = _single_bucket(passing, measured - passing).pass_percentage
            assert percentage is not None
            assert abs(100 * passing / measured - percentage) < 0.1
            assert (percentage == 100.0) == (passing == measured)
            assert (percentage == 0.0) == (passing == 0)


def test_summarize_sections_measured_zero_yields_none_never_zero_or_hundred() -> None:
    """An empty bucket reports null."""
    sections = [SectionDefinition(name="Billing", prefix="tests/billing/")]

    summary = summarize_sections([], sections)

    assert summary.items == (
        SectionSummary(name="Billing", total=0, measured=0, passing=0, pass_percentage=None),
    )
    assert summary.unassigned == SectionSummary(
        name=UNASSIGNED, total=0, measured=0, passing=0, pass_percentage=None
    )


def test_summarize_sections_items_are_alphabetical_and_unassigned_is_excluded() -> None:
    """Sections list alphabetically."""
    sections = [
        SectionDefinition(name="Zeta", prefix="tests/zeta/"),
        SectionDefinition(name="Alpha", prefix="tests/alpha/"),
        SectionDefinition(name="Mid", prefix="tests/mid/"),
    ]
    case_outcomes = [
        ("tests/zeta/test_x.py", "passed"),
        ("tests/alpha/test_x.py", "passed"),
        ("tests/mid/test_x.py", "passed"),
    ]

    summary = summarize_sections(case_outcomes, sections)

    assert [item.name for item in summary.items] == ["Alpha", "Mid", "Zeta"]
    assert UNASSIGNED not in [item.name for item in summary.items]


def test_summarize_sections_unassigned_is_present_even_when_empty() -> None:
    """An empty unassigned bucket still appears."""
    sections = [SectionDefinition(name="Billing", prefix="tests/billing/")]
    case_outcomes = [("tests/billing/test_x.py", "passed")]

    summary = summarize_sections(case_outcomes, sections)

    assert summary.unassigned.name == UNASSIGNED
    assert summary.unassigned.total == 0


def test_summarize_sections_totals_plus_unassigned_equal_the_run_total() -> None:
    """Section totals plus unassigned equal the run total."""
    sections = [SectionDefinition(name="Billing", prefix="tests/billing/")]
    case_outcomes = [
        ("tests/billing/test_x.py", "passed"),
        ("tests/billing/test_x.py", "failed"),
        ("tests/other/test_y.py", "passed"),
        ("tests/other/test_y.py", "skipped"),
    ]

    summary = summarize_sections(case_outcomes, sections)

    run_total = len(case_outcomes)
    assert sum(item.total for item in summary.items) + summary.unassigned.total == run_total


def test_summarize_sections_returns_a_run_section_summary() -> None:
    summary = summarize_sections([], [])

    assert isinstance(summary, RunSectionSummary)
    assert summary.items == ()
