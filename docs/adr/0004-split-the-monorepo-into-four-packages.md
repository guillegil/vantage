# 4. Split the monorepo into four packages

Date: 2026-08-14

## Status

Proposed

## Context and Problem Statement

Vantage has code with genuinely different dependency rules: domain logic
that RQ-26 requires to import nothing but the standard library, a storage
adapter that must stay swappable without the domain depending on it
(RQ-30), a pytest plugin that must add no third-party runtime dependency to
someone else's test environment (RQ-24), and a future API/web service that
is the one place a third-party dependency (FastAPI, Pydantic) is
acceptable. How should this code be packaged so those rules are enforced by
the dependency graph itself, not by convention alone?

An earlier design pass (superseded, see the repository reset in
`CLAUDE.md`) specified one package with internal module boundaries instead.
That pass never reached working code.

## Decision Drivers

- RQ-24: installing `vantage-pytest` must add only distributions this
  project itself publishes.
- RQ-26: the core must import only the standard library, and this must be
  checkable by static analysis, not only convention.
- RQ-30: the storage adapter must be replaceable without modifying the
  core, and the core must not import any storage implementation.
- Cost of enforcement: a rule that can be violated by one `import` inside a
  large package is weaker than a rule a dependency graph enforces at
  install time.

## Considered Options

1. One package, with internal modules (`core/`, `storage/`, `pytest_plugin/`)
   and a lint rule against crossing them.
2. Two packages: `vantage-core` (domain + storage, since storage is small)
   and `vantage-pytest` (the plugin), with the service deferred entirely.
3. Four packages, one per dependency tier: `vantage-core`,
   `vantage-storage`, `vantage-pytest`, `vantage-service`.

## Decision Outcome

Chosen option: **4 -- four packages**, because it is the only option where
`pip install vantage-pytest` cannot pull in a third-party dependency by
construction: the dependency graph enforces RQ-24 at install time, not only
at review time. `deptry`, the RQ-26 architecture test and the
clean-environment install check (CLAUDE.md's three non-redundant guards)
each still apply, but they are now checking a boundary the packaging
already makes hard to cross by accident, rather than the only thing
stopping the violation.

### Consequences

- Four packages before three of them have meaningful content (`vantage-core`
  and `vantage-storage` start small, `vantage-service` starts empty) means
  more scaffolding files -- four `pyproject.toml`s, four `src/` layouts --
  for less initial payoff than a single-package start would have offered.
- A change spanning two packages (for example, a new port method in
  `vantage-core` consumed by `vantage-storage`) now touches two
  independently versioned packages and two dependency declarations,
  rather than one internal refactor.
- The workspace needs a shared lockfile and shared tool configuration
  (`ruff`, `mypy --strict`, the `pytest` ini) hoisted to the workspace
  root (D9) to avoid four packages drifting into four different lint or
  type-check configurations.
- A contributor working only on the plugin must still understand that
  `vantage-pytest` depends on `vantage-core` and `vantage-storage` as
  separate installable units, not as sibling modules one import away --
  a steeper first-orientation cost than a single package would have had.

## Pros and Cons of the Options

### Option 1: one package, internal modules

- Good, because it is the least scaffolding: one `pyproject.toml`, one
  `src/` tree.
- Good, because refactoring across the internal boundaries is a normal
  same-package code move.
- Bad, because nothing stops `pytest_plugin/` from importing a third-party
  package that then ships inside `vantage-core`'s own distribution --
  RQ-24 and RQ-26 would depend entirely on the AST architecture test and
  code review, never on `pip install` itself.
- Bad, because RQ-30 (storage replaceable without touching the core) has
  no dependency-graph proof; a "core imports storage" mistake is a
  same-package import that costs nothing to write.

### Option 2: two packages (core+storage merged, service deferred)

- Good, because it is fewer packages to scaffold than four while still
  separating the plugin.
- Good, because it defers `vantage-service` scaffolding to the milestone
  that actually needs it.
- Bad, because `vantage-core` importing `vantage-storage` internals is
  still a same-package concern, so RQ-30's "core imports no storage
  implementation" scenario has the same enforcement gap as Option 1 has
  for RQ-24.
- Bad, because a future storage adapter (a second database engine, say)
  has no existing sibling package pattern to copy from -- it would arrive
  as the first split of an already-merged package rather than the second
  instance of an established pattern.

### Option 3: four packages (chosen)

- Good, because RQ-24 and RQ-30 are proven by the dependency graph itself,
  not only by tests that could pass today and be defeated by tomorrow's
  import.
- Good, because `vantage-service`'s eventual third-party dependencies
  (FastAPI, Pydantic) are structurally confined to the one package
  allowed to hold them, from the moment that package exists.
- Bad, because it is the most scaffolding of the three options before any
  of it does real work (see Consequences above).
- Bad, because cross-package changes require touching more than one
  dependency declaration, as noted above.
