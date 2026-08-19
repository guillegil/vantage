# 12. Key the test catalogue by the pytest node id

Date: 2026-08-18

## Status

Accepted

## Context

`test_case.stable_id` is `NOT NULL UNIQUE` and the schema comment says it
"supersedes `node_id` in Phase 2" (ADR-5, RQ-29 — the schema was built
complete at first use, so the column already exists and this decision picks
what fills it now). Whatever fills it *is* catalogue identity for this
change, and catalogue identity is what RQ-13 criterion 2 depends on: the same
identifier returning must reuse the same entry rather than create a second
one. RQ-9's decomposed identity columns (`file_path`, `class_name`,
`function_name`, `param_id`) exist alongside it and are candidate inputs to
an identity, not obligations to use one.

The cost of getting this wrong is not a bug fix. `stable_id` is a live
database key the moment any session is recorded against it: `result` rows
reference `test_case.id` by foreign key, and `last_seen_at`/`first_seen_at`
accumulate history keyed by whatever identity was chosen. Changing the
identity recipe later re-keys every existing row — the exact migration
ADR-5 and RQ-29 exist to prevent. That reversal cost is far more than a
sprint, which is what makes this an ADR rather than a design note (D24).

## Decision

`test_case.stable_id` stores the full pytest node id, unhashed and
untransformed — for example
`packages/vantage/tests/test_memory_store.py::TestInMemoryExecutionStore::test_first_write_creates_a_row`.
The node id says exactly what it knows: where the test lived when it was
last seen, and nothing more. A rename or a move therefore splits a test's
history, and that split stays **visible** rather than being disguised.
Visibility is the point — Phase 3 reconciliation needs to be able to find
what it must reconcile, and a lossy or opaque identity would hide that from
it.

`node_id` is the Phase 1 conflict target for the catalogue upsert (design.md
D20); `stable_id` carries the identical string in Phase 1, so its own UNIQUE
index cannot be violated by the row the upsert updates. A Phase 2 divergence
between the two columns must revisit that conflict target — that seam is
named here so a future change discovers it in this document, not in
production.

## Consequences

- A test's history splits across two catalogue entries whenever the test is
  renamed or moved, because the node id changes and a new entry is created.
  This is accepted, not merely tolerated: an incomplete history is
  preferable to a merged one that silently asserts two different tests are
  the same test.
- Phase 2 reconciliation across renames is deferred rather than solved here.
  It has a starting point — the split entries this decision produces — but
  no mechanism yet.
- Nothing about this decision constrains `file_path`, `class_name`,
  `function_name` or `param_id` — RQ-9's decomposed columns remain
  separately queryable and are stored on every result and catalogue entry
  regardless of what identity key is chosen.

## Alternatives rejected

**Hashing the four decomposed columns.** Carries identical information to
the node id and splits identically on rename, while hiding the recipe behind
a hash. Changing that recipe later re-keys every row — the same migration
this decision exists to prevent, merely less visible in a diff, because a
hash gives no clue what it was computed from.

**Identity from function name and parameters, ignoring the path.** Does
survive a file move, but makes two identically named tests in different
files collide into one catalogue entry. A merged history asserts something
false — that two distinct tests are one test — while a split history (the
chosen alternative) is only incomplete. Incomplete beats wrong.
