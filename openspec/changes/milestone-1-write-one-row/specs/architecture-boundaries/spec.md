# Architecture Boundaries Specification

## Purpose

Defines the structural guarantees that keep the core free of third-party and
pytest/storage-specific dependencies, keep the plugin free of any
third-party runtime dependency, and keep the storage adapter replaceable
without touching the core.

## Requirements

### Requirement: Zero runtime dependencies

Installing the plugin package MUST add to the environment only distributions
published by this project.

#### Scenario: Clean install adds only this project's distributions

- GIVEN a clean virtual environment
- WHEN the plugin package is installed
- THEN every distribution added to the environment is one published by this project

#### Scenario: No undeclared third-party import

- GIVEN the plugin package's source
- WHEN its declared dependencies are analysed
- THEN no third-party distribution is declared and no third-party module is imported without being declared

### Requirement: Core isolation

The core package MUST import only modules from the Python standard library.

#### Scenario: Every core import resolves to the standard library

- GIVEN the core package
- WHEN every import statement in it is resolved by static analysis
- THEN each one resolves to a Python standard-library module

#### Scenario: The analysis is not vacuous

- GIVEN the static analysis of the previous scenario
- WHEN it runs
- THEN it reports having examined at least one module

### Requirement: Replaceable storage

The system SHOULD support replacing the storage adapter by providing an
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
