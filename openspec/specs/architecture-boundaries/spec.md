# Architecture Boundaries Specification

## Purpose

Defines the structural guarantees that keep the plugin free of any
third-party runtime dependency, keep the server's core free of third-party
and storage-specific imports, and keep the storage adapter replaceable
without touching the core.

**Component:** `pytest-vantage` for zero-dependency (RQ-24); `vantage`
(`vantage.core` / `vantage.storage`) for core isolation (RQ-26) and
replaceable storage (RQ-30).

## Requirements

### Requirement: Zero runtime dependencies (RQ-24)

Where pytest is already present in the environment, installing the plugin
package MUST add no distribution other than the plugin package itself.

#### Scenario: Clean install adds exactly one distribution
- GIVEN a virtual environment with pytest installed
- WHEN `pytest-vantage` is installed
- THEN exactly one distribution is added to the environment

#### Scenario: Every import resolves to stdlib or pytest
- GIVEN the plugin package's source
- WHEN its imports are analysed
- THEN every one resolves either to the Python standard library or to pytest

#### Scenario: Declared dependencies name only pytest
- GIVEN the plugin package's metadata
- WHEN its declared dependencies are read
- THEN pytest is the only one

### Requirement: Core isolation (RQ-26)

The core package MUST import only modules from the Python standard library.

#### Scenario: Every core import resolves to the standard library
- GIVEN the core package
- WHEN every import statement in it is resolved by static analysis
- THEN each one resolves to a Python standard-library module

#### Scenario: The analysis is not vacuous
- GIVEN the static analysis of the previous scenario
- WHEN it runs
- THEN it reports having examined at least one module

### Requirement: Replaceable storage (RQ-30)

The server SHOULD support replacing the storage adapter by providing an
alternative implementation of the storage port, without modifying the core
package.

#### Scenario: Core suite passes against an in-memory adapter
- GIVEN an in-memory implementation of the storage port
- WHEN the core test suite is run against it
- THEN it passes unchanged

#### Scenario: Core contains no storage-implementation import
- GIVEN the in-memory implementation of the previous scenario
- WHEN the core package is inspected
- THEN it contains no import of either storage implementation
