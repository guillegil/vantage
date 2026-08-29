"""Section derivation and the per-run pass-percentage aggregate
(design.md D84, D85; spec `test-sections`).

Stdlib only -- `vantage.core.domain.sections` has no I/O, so nothing here
needs a store, a fixture, or a temporary file. No `req` marker: the three
requirements this change genuinely touches (RQ-24, RQ-26, RQ-29) are
verified elsewhere; each test below names its capability and scenario in
its own docstring instead.
"""

from __future__ import annotations

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
# normalize_prefix (spec test-sections: coercion, sibling non-bleed)
# ---------------------------------------------------------------------------


def test_normalize_prefix_coerces_a_missing_trailing_slash() -> None:
    """Scenario: A missing trailing slash is coerced on write."""
    assert normalize_prefix("tests/SectA") == "tests/SectA/"


def test_normalize_prefix_is_idempotent() -> None:
    once = normalize_prefix("tests/SectA")
    twice = normalize_prefix(once)

    assert once == twice == "tests/SectA/"


def test_normalize_prefix_strips_surrounding_whitespace_before_coercing() -> None:
    assert normalize_prefix("  tests/SectA  ") == "tests/SectA/"


def test_a_coerced_prefix_does_not_bleed_into_a_similarly_named_sibling() -> None:
    """Scenario: A prefix does not bleed into a similarly-named sibling.

    The coercion is the whole point: without it, `tests/SectA` (no trailing
    slash) would match `tests/SectAlpha/test_x.py` as a plain string prefix.
    """
    sections = [SectionDefinition(name="SectA", prefix=normalize_prefix("tests/SectA"))]

    assert derive_section("tests/SectAlpha/test_x.py", sections) == UNASSIGNED


# ---------------------------------------------------------------------------
# derive_section (spec test-sections: longest-prefix-wins)
# ---------------------------------------------------------------------------


def test_derive_section_the_longest_matching_prefix_wins() -> None:
    """Scenario: The longest matching prefix wins over a shorter one."""
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
    """Design.md D84: among equal-length matching prefixes, the
    alphabetically first name wins -- the only way a tie can occur is two
    sections sharing an identical prefix."""
    sections = [
        SectionDefinition(name="Zeta", prefix="tests/dup/"),
        SectionDefinition(name="Alpha", prefix="tests/dup/"),
    ]

    assert derive_section("tests/dup/test_x.py", sections) == "Alpha"


def test_derive_section_matching_is_case_sensitive() -> None:
    sections = [SectionDefinition(name="SectA", prefix="tests/SectA/")]

    assert derive_section("tests/secta/test_x.py", sections) == UNASSIGNED


# ---------------------------------------------------------------------------
# summarize_sections (spec test-sections: pass percentage, ordering,
# unassigned bucket)
# ---------------------------------------------------------------------------


def test_summarize_sections_the_worked_example_yields_94_4() -> None:
    """Scenario: The worked example yields 94.4%.

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
    # `total - measured` is exactly the skipped count (design.md D85).
    assert summary.items[0].total - summary.items[0].measured == 10


def test_summarize_sections_measured_zero_yields_none_never_zero_or_hundred() -> None:
    """Scenario: An empty bucket reports null."""
    sections = [SectionDefinition(name="Billing", prefix="tests/billing/")]

    summary = summarize_sections([], sections)

    assert summary.items == (
        SectionSummary(name="Billing", total=0, measured=0, passing=0, pass_percentage=None),
    )
    assert summary.unassigned == SectionSummary(
        name=UNASSIGNED, total=0, measured=0, passing=0, pass_percentage=None
    )


def test_summarize_sections_items_are_alphabetical_and_unassigned_is_excluded() -> None:
    """Scenario: Sections list alphabetically."""
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
    """Scenario: An empty unassigned bucket still appears."""
    sections = [SectionDefinition(name="Billing", prefix="tests/billing/")]
    case_outcomes = [("tests/billing/test_x.py", "passed")]

    summary = summarize_sections(case_outcomes, sections)

    assert summary.unassigned.name == UNASSIGNED
    assert summary.unassigned.total == 0


def test_summarize_sections_totals_plus_unassigned_equal_the_run_total() -> None:
    """Scenario: Section totals plus unassigned equal the run total."""
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
