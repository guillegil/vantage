# 7. Store the database under the project root by default

Date: 2026-08-14

## Status

**Deprecated** on 2026-08-15, before it was ever accepted. Its context no
longer exists.

This ADR decided where **the plugin** puts its database, and anchored the
answer on **pytest's `rootdir`**. Under ADR-9 the plugin never opens a
database at all: it reports over HTTP and the server performs every write.

The server has no `rootdir`. It is not necessarily running inside a project
directory, and it may serve many projects at once, so every argument below
about worktrees, clones and CI checkouts has lost its subject.

The question *where does the server put its SQLite file* is real and still
unanswered, but it is a different question with different reasoning, and it
gets its own ADR when the server exists. What survives from this one is a
single observation: `~/.cache` is documented as safe to delete, and a run
recorded three months ago cannot be regenerated.

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
