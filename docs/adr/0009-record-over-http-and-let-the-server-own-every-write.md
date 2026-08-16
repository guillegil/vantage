# 9. Record over HTTP and let the server own every write

Date: 2026-08-15

## Status

Proposed

## Context

The plugin runs inside the virtualenv of the project under test, beside that
project's real dependencies. RQ-24 exists because every package it brings
competes with theirs for a single resolution — and the conflict arrives
*because* the plugin pins correctly, not despite it.

Four separate pressures converge on the same answer, and no two of them alone
would have been enough.

**A database driver cannot live in the plugin.** Supporting PostgreSQL from
the plugin means `psycopg` in someone else's test environment, which is
precisely the conflict RQ-24 names.

**A plugin for another language cannot import Python.** If Vantage ever
records `vitest` or `jest` runs, that plugin is JavaScript in `node_modules`.
It can share no storage code with the Python one. The only boundary both can
speak is a network protocol.

**SQLite serialises writers.** With several sessions writing, the previous
design was carrying WAL, `BEGIN IMMEDIATE`, a five-second busy timeout and a
network-filesystem fallback — all of it engineering around one limitation.

**Live run monitoring needs a reader that sees the run as it happens.**
Against a shared database file that means polling a table with a cursor;
against a process that is already being told, it is free.

## Decision

`pytest-vantage` reports finished sessions to the server over HTTP, and the
server performs every write to the database.

The plugin depends on the standard library only — `urllib` for transport,
`json` for encoding — and holds no database code, no schema knowledge and no
storage port. Its whole contract is a versioned HTTP API, `POST /api/v1/...`.

## Consequences

- RQ-24 stops being a rule to police and becomes impossible to violate: there
  is nothing for the plugin to depend on.
- A plugin in any language is now possible, because the boundary is a protocol
  rather than an import.
- One writer. WAL, `BEGIN IMMEDIATE`, busy timeouts and the concurrent-session
  requirement all cease to be plugin concerns and move behind the server's
  storage port.
- Which database is behind the server — SQLite, PostgreSQL, anything later —
  becomes invisible to the plugin.
- Live monitoring is natural rather than simulated.
- **Recording now requires a running server.** The project's strongest
  adoption argument — install a plugin, add a flag, done — is gone.
- **Every CI job needs a sidecar**, which is real friction in the place test
  results matter most.
- The failure surface multiplies: connection refused, timeout, a server dying
  mid-session, DNS, a half-sent report. Each must degrade without breaking the
  suite, and RQ-21 grew from three acceptance criteria to five because of it.
- A server that accepts a connection and never answers turns a fault-tolerance
  requirement into a hang, which is worse than a failure — the user cannot
  tell whether the suite is slow or stuck. A bounded timeout is part of the
  obligation, not an implementation detail.
- Data loss changes shape. Before: the suite ran, the file has it. Now: the
  suite ran, the server was down, it is gone.
- Per-session HTTP has a cost RQ-25's two percent must absorb, which forces
  the payload to be batched rather than streamed. RQ-25 now carries a
  criterion that the request count is independent of the test count.
- Two components must be running and version-compatible for the product to
  work at all.
- Localhost HTTP is still local, so RQ-28's offline guarantee is unaffected.

## Alternatives rejected

**The plugin writes directly to the database.** Keeps `pip install` plus a
flag as the entire setup, and a run recorded is a run kept, with no second
process to be down. Rejected because PostgreSQL support would put a driver in
the user's virtualenv, and because no non-Python plugin could ever share it —
both of which the roadmap requires.

**A port with two implementations: local file by default, HTTP when a server
is configured.** Preserves the frictionless install and genuinely defers the
choice. Rejected because it means two write paths to build, test and support
forever, and because the local one still requires the plugin to import storage
code and therefore ship with it.

This decision was taken knowing it costs the adoption argument. The exchange is
a plugin that can never break a user's environment, and a boundary that a
plugin in any language can speak.
