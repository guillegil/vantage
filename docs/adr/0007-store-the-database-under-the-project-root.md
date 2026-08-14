# 7. Store the database under the project root by default

Date: 2026-08-14

## Status

Proposed

## Context

Once `--vantage` (or `--vantage-db`) activates recording, some default
database location is needed when no explicit path is given -- `--vantage`
alone has to mean something. Three locations were considered: under the
project root (`<rootdir>/.vantage/vantage.db`), a user-level cache
directory (`~/.cache/vantage/...`), and inside pytest's own
`.pytest_cache`.

RQ-10 records the commit, branch and dirty flag alongside every run; that
record is only meaningful relative to the checkout it came from. A
machine-global location merges the histories of every clone, worktree and
CI checkout of the same repository into one database and needs
disambiguation columns to undo the merge it caused. `~/.cache` is
documented, by convention, as safe to delete -- storing longitudinal
history there contradicts the product's own claim that the history is
worth keeping. `.pytest_cache` is owned by pytest and is cleared by
`--cache-clear`, which would silently destroy a user's recorded history as
a side effect of an unrelated pytest flag.

## Decision

Default to `<rootdir>/.vantage/vantage.db`, anchored on pytest's `rootdir`
rather than `os.getcwd()`, so the same project resolves the same database
whether pytest is invoked from the repository root or a subdirectory. The
`.vantage/` directory is created only after activation (never as a side
effect of resolution alone, which stays pure), and a `.gitignore` containing
`*` is written into it at creation time with `open(path, "x")`
(`FileExistsError` ignored) so the database itself is never accidentally
committed. The project's own `.gitignore` is never touched.

## Consequences

- The database lives inside the project directory, so `rm -rf` of a
  checkout deletes its recorded history along with the code -- unlike a
  user-cache location, which would have survived that specific case at the
  cost of merging unrelated checkouts together.
- Every checkout, worktree and CI runner accumulates its own
  `.vantage/vantage.db` with no shared history between them; comparing
  runs across two developers' machines means comparing two separate
  databases, not one.
- A user who does not read `.gitignore` carefully, or who force-adds files,
  can still commit `.vantage/vantage.db` to the repository despite the
  written safeguard, since a generated `.gitignore` entry is a convention,
  not an enforced constraint.
- Reversal cost is high once users hold data: changing the default after
  people have accumulated `.vantage/` history means either leaving old
  databases orphaned at the old path or writing a one-time migration this
  project has otherwise deliberately avoided (ADR-5).
