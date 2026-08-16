# 4. Publish two distributions with an HTTP boundary between them

Date: 2026-08-15

## Status

Accepted on 2026-08-16, when PR #19 merged Milestone 1 into `main`.

Third revision, still before acceptance. This ADR first decided four
independently installable distributions, then one distribution with optional
dependency groups. Both are replaced. Four distributions meant four version
numbers one maintainer cannot keep in step; one distribution meant the base
install had to be the plugin, which reads oddly for a product that will
eventually span languages. ADR-9 then settled the write model and the
packaging fell out of it.

## Context

ADR-9 decided that `pytest-vantage` reports over HTTP and the server performs
every write. That removes the constraint which forced the earlier answers: the
plugin no longer imports core or storage code, so it no longer has to ship
with them.

Two facts then decide the packaging.

**Not all version skew is equal.** The earlier revisions treated it as one
risk. It is two. Across shared *imports* — core, storage and service — skew is
fatal and at worst silent. Across a versioned *HTTP* boundary — plugin to
server — it is ordinary, and is the everyday case in any networked system.

**The product will not stay Python.** If Vantage ever records `vitest` runs,
that plugin is JavaScript published to npm; a pip extra cannot install it.
Language plugins belong in their language's registry, which means the product
name should belong to the component that is language-agnostic: the server.

## Decision

Publish two distributions from one repository.

```
pip install pytest-vantage      the plugin. pytest and the standard library.
pip install vantage             the server: core, storage, service.
pip install vantage[postgres]   + the PostgreSQL driver.
npm install @vantage/vitest     later, in its own registry.
```

They release independently through prefixed tags — `pytest-vantage-v1.2.0`,
`vantage-v1.4.0` — with CI building only the package a tag names.

The contract between them is the versioned HTTP API, not their version
numbers. `POST /api/v1/...` is what both sides agree on; how many plugin
versions the server promises to keep serving is a support commitment to be
written down, not an accident of release timing.

The `[postgres]` extra names the wire protocol rather than a vendor: Supabase,
Neon, RDS and CockroachDB all speak PostgreSQL and all arrive through it.
Which adapter runs is chosen by a connection URL in configuration; the extra
only decides whether the driver it needs is installed. Selection is
architecture, installation is packaging, and no port makes a missing package
appear.

## Consequences

- `pytest-vantage` has no dependencies beyond the runner it extends, so RQ-24
  cannot be violated even by accident, and it sits beside `pytest-cov` and
  `pytest-xdist` under a name every pytest user already recognises.
- The product name stays with the language-agnostic component, leaving room
  for plugins outside Python without renaming anything.
- Two things must be installed and both must be running; the product does
  nothing with only one of them. That is a real adoption cost, paid in
  exchange for a plugin that cannot conflict with anything.
- Two version numbers appear in changelogs, release notes and bug reports,
  and users will report the wrong one.
- The versioned API becomes an obligation: every breaking change needs a new
  version and a deprecation window. A single distribution would not have owed
  that.
- CI must exercise the plugin against several released server versions, not
  only against `HEAD`, or the compatibility promise is untested.
- Prefixed tags are easy to mistype, and a wrong tag publishes the wrong
  package.

## Alternatives rejected

**Four distributions, one per internal package.** Enforces each dependency
rule by installation rather than by a test, which is genuinely stronger, and
would let a second storage adapter ship on its own cadence. Rejected because
four version numbers that import each other will drift under one maintainer,
and because ADR-9 removed the imports that justified the split.

**One distribution with optional dependency groups.** One version cannot drift
from itself. Rejected because extras only add: the base install must be the
smallest thing, which forces the product name onto the Python plugin, and
nothing can subtract the server from a plugin install.

**Two repositories.** Fully independent release and issue tracking. Rejected
because the contract test that exercises both sides of the HTTP boundary would
have nowhere to live — and that boundary is the thing most likely to break.
