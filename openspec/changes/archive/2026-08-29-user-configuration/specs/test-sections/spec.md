# Test Sections Specification

## Purpose

Defines section definitions as the first tenant of `user-configuration`
(namespace `test_sections`), longest-prefix-wins derivation performed at
read time, the always-present `unassigned` bucket, and the per-run
pass-percentage aggregate.

**Component:** `vantage` — pure derivation and counting live in
`vantage.core` (stdlib only, RQ-26); persistence reuses
`user-configuration`'s store in `vantage.storage`; the HTTP surface and
value validation live in `vantage.service`. `pytest-vantage` is untouched
(RQ-24): sections are read-side only.

## Requirements

### Requirement: Section definitions stored under `test_sections`

The system MUST store each section as one row under namespace `test_sections`, keyed by section name, holding its prefix. A trailing `/` MUST be coerced onto the prefix at write time.

#### Scenario: A missing trailing slash is coerced on write
- GIVEN a prefix "tests/SectA" with no trailing slash
- WHEN a section is written with that prefix
- THEN the stored prefix is "tests/SectA/"

### Requirement: Section name constraints

A section name MUST be non-empty after stripping leading and trailing whitespace, MUST NOT exceed a configured maximum length, and MUST be rejected if it equals "unassigned" under any casing.

#### Scenario: An empty or whitespace-only name is rejected
- GIVEN a name that is empty after stripping whitespace
- WHEN a section write is attempted with that name
- THEN it is rejected

#### Scenario: "unassigned" is reserved regardless of casing
- GIVEN a section write named "Unassigned" or "UNASSIGNED"
- WHEN the write is attempted
- THEN it is rejected, matched case-insensitively against the reserved name

### Requirement: Longest-prefix-wins derivation at read time

A test's section MUST be derived at read time, never stored on a run or result row, by selecting the section whose prefix is the longest byte-exact, case-sensitive match against the test's file path. A test matching no section's prefix MUST derive as `unassigned`.

#### Scenario: The longest matching prefix wins over a shorter one
- GIVEN sections "tests/" and "tests/SectA/" both defined
- WHEN a test's file path is "tests/SectA/test_x.py"
- THEN it derives into "tests/SectA/", not "tests/"

#### Scenario: A prefix does not bleed into a similarly-named sibling
- GIVEN a section "tests/SectA/" and a test file path "tests/SectAlpha/test_x.py"
- WHEN that test's section is derived
- THEN it does not match "tests/SectA/"

#### Scenario: Renaming a section re-groups history with no backfill
- GIVEN historical results already classified under a section's old name
- WHEN the section is renamed
- THEN the next read groups those same results under the new name, with no write to any run or result row

### Requirement: Deleting a section is immediate and silent

Deleting a section MUST take effect on the server side immediately, with no confirmation step. Its previously matching results MUST derive as `unassigned` on the next read, with no historical rewrite.

#### Scenario: A deleted section's tests fall back to unassigned
- GIVEN a section with matching historical results
- WHEN the section is deleted
- THEN the next read of those results' section groups them under `unassigned`

### Requirement: `unassigned` bucket is always present and reconciles

A run's section summary MUST always include an `unassigned` bucket, in its own field outside the section list, even when it is empty. The sum of every section's total plus the `unassigned` total MUST equal the run's total result count.

#### Scenario: An empty unassigned bucket still appears
- GIVEN a run whose every result matches some defined section
- WHEN its section summary is read
- THEN `unassigned` is present with a total of zero

#### Scenario: Section totals plus unassigned equal the run total
- GIVEN a run with N total results
- WHEN its section summary is read
- THEN the sum of every section's total plus the `unassigned` total equals N

### Requirement: Sections are ordered alphabetically

The section list in both the listing and summary responses MUST be sorted alphabetically by name. `unassigned` MUST NOT appear inside that list.

#### Scenario: Sections list alphabetically
- GIVEN sections named "Zeta", "Alpha", and "Mid"
- WHEN they are listed or summarized
- THEN they appear in the order "Alpha", "Mid", "Zeta"

### Requirement: Pass percentage

`pass_percentage` for each bucket MUST be computed as `(passed + xfailed) / (passed + failed + error + xfailed + xpassed) * 100`, excluding `skipped` from the denominator entirely. When that denominator is zero, `pass_percentage` MUST be `null` on the wire, never `0.0` or `100.0`.

#### Scenario: The worked example yields 94.4%
- GIVEN a bucket with 80 passed, 5 xfailed, 2 xpassed, 3 failed, 10 skipped
- WHEN its pass percentage is computed
- THEN it is 94.4% (85/90)

#### Scenario: An empty bucket reports null
- GIVEN a bucket with zero results across passed, failed, error, xfailed and xpassed
- WHEN its pass percentage is computed
- THEN it is `null` on the wire, not `0.0` and not `100.0`

### Requirement: Section definitions are readable, upsertable, and deletable via the service API

The system MUST expose a way, through `vantage.service`, to list section definitions, upsert a section's prefix, and delete a section.

#### Scenario: An upserted section is listed
- GIVEN a section written via the service API
- WHEN section definitions are listed
- THEN it appears with its stored prefix

### Requirement: Run section-summary endpoint

The system MUST expose `GET /api/v1/runs/{run_id}/sections`, returning every defined section's summary and the `unassigned` bucket for that run.

#### Scenario: A run's summary reflects its sections
- GIVEN a run with results spread across defined sections and unassigned
- WHEN `GET /api/v1/runs/{run_id}/sections` is called for it
- THEN the response includes each section's summary and the `unassigned` bucket
