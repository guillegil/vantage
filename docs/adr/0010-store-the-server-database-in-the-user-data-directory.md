# 10. Store the server database in the user data directory

Date: 2026-08-15

## Status

Proposed

## Context

ADR-9 decided the plugin reports over HTTP and the server performs every
write; this ADR does not revisit that. What it decides is where the server
puts the file it writes to.

ADR-7 answered a related-looking question — where the *plugin's* database
goes — and is now `Deprecated`, before it was ever accepted: it anchored its
answer on pytest's `rootdir`, and under ADR-9 the plugin never opens a
database at all, so `rootdir` has nothing left to anchor to. The server is a
different subject: a long-lived process with no `rootdir` of its own, not
necessarily started inside a project directory, serving many projects
through one HTTP boundary instead of one process per checkout. This ADR is
the one ADR-7 named as still owed once the server existed. What survives
from ADR-7 is one observation: `~/.cache` is documented, by convention, as
safe to delete, and a run recorded three months ago cannot be regenerated.

Some default has to exist, because `vantage serve` with no `--database` has
to mean something — the server-side counterpart of `--vantage` alone
meaning something on the plugin side.

## Decision

The server's database defaults to `$XDG_DATA_HOME/vantage/vantage.db`,
falling back to `~/.local/share/vantage/vantage.db` when `$XDG_DATA_HOME`
is unset. Precedence is `--database PATH` > `VANTAGE_DATABASE` > that
default — an explicit flag beats an environment variable beats the XDG
default.

Environment configuration is permitted here even though RQ-2 forbids it on
the plugin, and the difference is the threat, not the mechanism. RQ-2
exists to stop a value committed by one person from silently enabling
recording in someone else's checkout; the server is installed and started
deliberately by whoever runs it, and an environment variable is the
ordinary way a container is configured.

**Resolution is a pure function that creates nothing.** Deciding where the
database goes and creating it there are separate acts:
`packages/vantage/src/vantage/core/config/resolution.py` performs no
filesystem access at all — no `stat`, no `mkdir`, not even an `exists()`
check on its own default branch — so asking the question never has a side
effect on disk. `packages/vantage/tests/test_path_authority.py` is the test
that proves it, alongside a read-only-parent check that fails loudly at
startup rather than silently at the first write.

## Consequences

- One server, one database, every project's runs mixed together. There is
  no project column, and none is invented here: `run.root_dir` (RQ-11,
  Milestone 3) is the seam that will eventually separate them; no read path
  needs the separation in Milestone 1 because there is no read path yet.
- **The XDG base-directory specification is a Linux convention.** Windows
  (`%LOCALAPPDATA%`) and macOS (`~/Library/Application Support`) each have
  their own, and this decision does not yet follow them: on both, the
  fallback still resolves to `~/.local/share/vantage/vantage.db`, which
  works but is not idiomatic for either platform. Recorded as a known
  consequence rather than fixed here — the platform question is open, and
  a later ADR is where it gets decided.
- Reversal cost is high once users hold data: changing the default after
  people have accumulated `~/.local/share/vantage/` history means either
  leaving old databases orphaned at the old path or writing a one-time
  migration ADR-5 otherwise deliberately avoids. That cost, not the choice
  itself, is what earns this a numbered ADR rather than a design note.

## Alternatives rejected

**`$XDG_STATE_HOME` (`~/.local/state/vantage/…`).** The specification's own
examples — logs, history — describe exactly what a table of recorded runs
resembles. Rejected because the specification frames STATE as data the
user does not care enough about to back up, and three months of test
history is data a user does care about.

**`~/.cache/vantage/…`.** Survives `rm -rf` of a checkout, which a
project-rooted location would not. Rejected because `~/.cache` is
documented as safe to delete — the same argument ADR-7 already made, now
pointed at the server's database instead of the plugin's.

**`./vantage.db` (the current working directory).** Trivial to explain.
Rejected because a long-lived server process is typically started by a
supervisor whose working directory is `/`, not a project checkout — the
database would land somewhere no one meant it to.
