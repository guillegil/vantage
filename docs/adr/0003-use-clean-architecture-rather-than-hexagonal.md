# 3. Use Clean Architecture rather than Hexagonal

Date: 2026-08-14

## Status

Proposed

## Context

Vantage needs its domain logic -- the run/result model, the storage
contract, option resolution -- isolated from the frameworks that surround
it: pytest on one side, SQLite (and later a web API) on the other. Both
Hexagonal Architecture (Ports and Adapters) and Clean Architecture solve
this with the same underlying idea: the domain defines the interfaces it
needs, and outer layers implement them, so the dependency arrow always
points inward.

An earlier design pass (superseded, see the repository reset in
`CLAUDE.md`) specified Hexagonal Architecture by name, with abstract base
classes standing in for ports. That pass never reached working code, so
this decision is made without a prior implementation to migrate.

Hexagonal Architecture centers the domain as a hexagon with symmetrical
ports on every side, each with one or more adapters. It carries ceremony
this project does not need: a formal ports/adapters vocabulary, and
frequently abstract base classes to define the ports, which forces every
adapter to inherit from a Vantage-owned base class to satisfy the
contract.

## Decision

Use Clean Architecture. Ports are `typing.Protocol`, not abstract base
classes: a `SqliteExecutionStore` or `InMemoryExecutionStore` satisfies
`ExecutionStore` by having the right shape, with no import of the core
package required to do it. That is what makes `vantage.storage` a
dependency of nothing on the core's side while still being swappable
(RQ-30).

Concretely: `vantage.core` defines the domain model and the storage port;
`vantage.storage` and (later) other adapters satisfy that port structurally;
`pytest-vantage` and `vantage.service` are the outermost layer, each
depending inward and never the reverse.

The layer names above are subpackage boundaries, not distribution
boundaries. This ADR was written when the plan was four distributions;
ADR-4 settled on two (`pytest-vantage`, and `vantage` carrying `core`,
`storage` and `service` as subpackages). Nothing in the decision changes
— the dependency arrow is the same one — but the names are corrected here
so this file and ADR-4 do not describe different trees.

## Consequences

- A `Protocol`-based port gives up compile-time proof that an adapter
  registered itself against the contract; a class can drift out of shape
  and the failure surfaces at first use (a `mypy --strict` run against a
  concrete usage site) rather than at class definition.
- The vocabulary is less immediately searchable than Hexagonal's "ports and
  adapters": a new contributor familiar with the hexagon terminology has to
  re-map it onto Clean Architecture's layer names (entities, use cases,
  interface adapters) to find their way around.
- Because nothing forces adapters to subclass a common base, nothing stops
  a future adapter from silently implementing only part of the `Protocol`
  and failing at runtime on the unimplemented method, rather than at
  import time.
- Reversal cost is non-trivial once `vantage.storage` and `pytest-vantage`
  exist against this boundary: switching to Hexagonal later means
  reintroducing an abstract base class and updating every adapter to
  inherit from it, not just a rename.
