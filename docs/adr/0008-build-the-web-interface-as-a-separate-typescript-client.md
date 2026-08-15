# 8. Build the web interface as a separate TypeScript client

Date: 2026-08-15

## Status

Proposed

## Context

RQ-14 requires a read-only HTTP API described by a machine-readable interface
document. RQ-20 requires the interface to be served entirely from assets in
the installed distribution, and to load on a machine with no Node.js
installed. RQ-28 requires every operation to complete using only local
resources, so nothing may be fetched at runtime.

A prior decision established that the interface is a separate concern from the
service: if the only consumer of the API lives inside the same package, the
contract RQ-14 demands is a function calling itself, and nothing forces it to
be honest.

FT-7 records a standing risk. Building web assets without requiring Node at
install time means the built output is committed to the repository, which is a
category of artefact that goes stale silently, and the CI check that would
prevent it is not designed.

## Decision

Build the web interface as a separate TypeScript and React source project with
its own toolchain, in `web/`, compiled in CI, and package the compiled output
into the `vantage` wheel at build time.

The built output is never committed: `web/dist/` is gitignored. The interface
reaches the server only over the HTTP API — it holds no import of any Python
module and no knowledge of the schema.

Development is `npm run dev` in `web/` against a locally running server. A
source-tree install without a prior build must fail with a message naming the
build command, not serve a blank page.

## Consequences

- The API contract becomes real. Two processes, one boundary, and no way for
  the interface to reach past it.
- FT-7's staleness risk disappears rather than being mitigated: nothing built
  enters git, so nothing can drift from its source, and the CI check it asked
  for is unnecessary.
- RQ-20 and RQ-28 both hold. The wheel carries the assets, nothing is fetched
  at runtime, and no Node is needed to install or run.
- A third party could write an alternative interface against the same
  documented API.
- A second language and a second toolchain enter a project maintained by one
  person, with their own dependency tree and their own supply-chain surface.
- Releasing the server now depends on a working Node build in CI. A broken
  frontend build blocks a backend release.
- The interface cannot be developed ahead of the endpoints it consumes, since
  there is nothing to render without the API.
- Debugging spans two runtimes and two stacks, which is slower than a template
  rendered server-side.
- The wheel grows by the size of the bundle for every user of the server,
  including any who never open a browser.

## Alternatives rejected

**Server-rendered templates inside the service.** Simpler to ship, no second
toolchain, no artefact to go stale. Rejected because it makes the separation
impossible by definition — the templates *are* the server, so the API would
have no independent consumer and RQ-14's contract would never be exercised by
anything. Live run monitoring in a later phase would also be awkward without a
client that can hold state.

**A client published separately and fetched at runtime.** Would allow the
interface to release independently of the server. Rejected outright: it
requires network access at runtime, which RQ-28 forbids.

**A client published as its own distribution, discovered by the server.**
Would allow independent releases without a runtime fetch. Rejected because it
reintroduces version skew between the interface and the API it consumes, which
is the failure the single-version packaging exists to prevent.
