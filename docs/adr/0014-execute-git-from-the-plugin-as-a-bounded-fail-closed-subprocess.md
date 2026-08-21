# 14. Execute git from the plugin as a bounded, fail-closed subprocess

Date: 2026-08-19

## Status

Accepted

## Context

RQ-10 requires every run to record the commit it ran on, its branch, the first
line of its commit message, and whether the working tree held uncommitted
changes to a tracked file. RQ-23 and RQ-39 cover the other two cases — no
repository at all, and a repository present but unreadable — and all three
require the run to be recorded either way. The `vcs-capture` change implements
them.

Until now `pytest-vantage` has done exactly one kind of I/O: a `urllib` socket
carrying JSON to the server, inside its own process. Reading a repository is a
different kind. **It means the plugin executes a program it did not write, in
the user's test environment, on every recorded session.**

ADR-4 and RQ-24 do not answer this. Both are about what the plugin may *depend
on* — no third-party distribution, pytest and the standard library and nothing
else — and `subprocess` is standard library, so a subprocess passes both without
either having considered it. What the plugin may *execute* has never been
decided, because until now it executed nothing.

Two further facts bear on the choice.

The server cannot do this instead. ADR-9 makes the server the sole writer, which
makes it tempting to make it the sole reader too — but the server does not have
the working tree. It may be on another machine, and the dirty flag is a property
of the client's tree at the moment the session started. Only the plugin is
standing in the repository.

And RQ-39's second acceptance criterion — *"a git repository and no git
executable on PATH"* — presupposes the answer. The requirement was written
against a system that shells out; a system that parses `.git` by hand has no
such state, and the criterion becomes untestable rather than satisfied.

## Decision

**`pytest-vantage` may execute an external process, subject to four conditions
that hold together and are not separable.**

1. **Bounded.** Every invocation carries a timeout, and the timeouts share one
   budget for the whole capture rather than one each, so the worst case is a
   fixed number of seconds per session and not a multiple of the invocation
   count. `stdin` is `subprocess.DEVNULL` and the environment sets
   `GIT_TERMINAL_PROMPT=0`, so the process cannot become interactive and wait
   for a human who is not there.

2. **Fail-closed.** The function that spawns the process is itself the isolation
   boundary — it catches every exception below `BaseException` and returns an
   empty snapshot. It never propagates, and it does not delegate isolation to a
   decorator. `KeyboardInterrupt` and `SystemExit` still propagate, because
   RQ-31 depends on a real SIGINT reaching pytest's own session wrapper.

3. **Unable to break the session.** The failure path is non-latching and shares
   no flag with the reporting or liveness paths. A failure to read the
   repository must not disable result accumulation, heartbeats, or any other
   hook, and must not change the exit status pytest would otherwise have had.
   Recording nulls is the required outcome of a failure — not recording nothing.

4. **Not composed.** The argument vector is a list of literal constants, never a
   shell string, and no value derived from the repository, the environment or
   the user's configuration is ever an argv element. The working directory is
   pytest's own root path.

The process is read-only with respect to the user's repository. In particular
`GIT_OPTIONAL_LOCKS=0` is set so that reading the working-tree status does not
take the index lock: an observability tool has no business writing the index of
the repository it is observing.

This decision authorises executing `git` for version-control context. It does
not authorise executing anything else, and a later change that wants to read the
host environment some other way inherits these four conditions but not a
pre-granted answer.

## Consequences

- RQ-24 is unchanged and still holds: `subprocess` and `shutil` are standard
  library, no distribution is added, and the clean-environment install check
  keeps proving it. ADR-4's boundary is a dependency boundary, and this ADR is
  what makes explicit that it was never an execution boundary.
- The plugin acquires a surface it did not have: it executes whatever `git`
  is first on `PATH` in the user's environment. Conditions 1 and 4 bound what
  that can do — no shell, no composed arguments, no injected values, no network
  invocation, no prompt, a fixed time budget — but the surface is real and is
  named here rather than discovered later.
- A session in an environment without `git` costs nothing at all: the binary is
  looked up before anything is spawned, and its absence is a supported state
  (RQ-39.2), not a failure. It emits no warning, because a container image
  without `git` would otherwise warn on every session forever and teach people
  to ignore the warnings that matter.
- Every recorded session now pays a fixed process cost. That cost is measured
  against both of RQ-25's profiles and the numbers are committed to the
  `version-control-context` spec; the requirement's own criterion 3 exists to
  record it whether or not the 2 % budget holds. Any later change to the
  invocations or their count must re-run the measurement.
- Capture is unconditional — no opt-out flag ships with it. A flag that exists
  gets set once, on a day somebody is in a hurry, and is never unset; months
  later a project's history has no commits and nobody remembers why. If the
  measurement shows a flag is needed, it is added then and justifies itself
  with a number instead of a guess.
- The plugin remains free of any database (ADR-9) and of any third-party import
  (RQ-24, RQ-26). Nothing about the write model changes: the plugin reports, the
  server writes.

## Alternatives rejected

**Parse `.git` by hand.** Tempting, because it spawns no process at all and the
plugin already reads and writes bytes for a living. Rejected on two counts.

It makes RQ-39's second criterion untestable. "A git repository and no git
executable on PATH" is not a state a hand-written parser can be in, so the
criterion could not be exercised — and a requirement whose criterion cannot fail
is not verified, it is assumed.

And it trades one process for a compatibility surface owned permanently. The
dirty flag alone means reimplementing index-versus-worktree comparison, stat
caching and its mtime races, `.gitignore`, and `core.autocrlf`. Reading a commit
means packed refs, packed objects, and now `reftable`. Every one of those is
something git changes on its own schedule and we would have to follow. One
bounded process is a smaller, and above all a *fixed*, cost than a parser that
grows with every git release.

**Use a third-party git library.** Would be correct, maintained by people who
care about it, and a single import. Rejected outright by RQ-24: installing
`pytest-vantage` must add no third-party distribution to the user's environment.
That rule exists so the plugin can never conflict with whatever a project
already depends on, and it is not negotiable for a convenience.

**Let the server read the repository.** Consistent with ADR-9's "the server owns
every write" and would keep the plugin exactly as it is. Rejected because the
server does not have the tree. Even in the Phase 1 same-machine deployment it
has no reliable path to it, and the dirty flag must describe the tree at the
moment the session started, not whenever the report arrived. This is a fact only
the client holds.

**Do not capture version control at all.** The honest null option. Rejected
because RQ-10 is a Must Have and RQ-15 — the test-history endpoint whose own
rationale calls it the endpoint the whole product exists to serve — requires
every history entry to carry the commit it ran on. A result from a dirty tree
cannot be reproduced by anyone, including the person who produced it; without
the flag the history cannot be trusted.

Bound to: ADR-4 (two distributions, an HTTP boundary), ADR-9 (record over HTTP
and let the server own every write), RQ-10 (version-control context), RQ-23
(absent version control), RQ-24 (zero runtime dependencies), RQ-25 (runtime
overhead), RQ-39 (unreadable version control), and the
`version-control-context` capability.
