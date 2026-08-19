# Distributed Execution Specification

## Purpose

Defines what a session records when its tests are spread across `pytest-xdist`
workers: each result exactly once, and one run entry — with the same suite run
without xdist as the control that catches a de-duplication filter that is too
aggressive.

**Component:** both — the plugin decides what leaves the process, the server
records what arrives. Neither may achieve the count by de-duplicating on read;
that would make the correct count a property of the query rather than of the
data.

## ADDED Requirements

### Requirement: Distributed execution (RQ-12)

While `pytest-xdist` is active, the system MUST record each test result exactly
once, and MUST record exactly one run entry for the session.

The recorded count MUST NOT depend on whether xdist is active: the same suite
executed with and without xdist MUST record the same number of results.

#### Scenario: Six tests across two workers record six results (RQ-12.1)
- GIVEN a suite of six tests run with two xdist workers
- WHEN the session is recorded
- THEN the result count is six

#### Scenario: The same six tests without xdist also record six (RQ-12.2)
- GIVEN the same suite of six tests run without xdist installed
- WHEN the session is recorded
- THEN the result count is also six

#### Scenario: Six tests across two workers record one run entry (RQ-12.3)
- GIVEN a suite of six tests run with two xdist workers
- WHEN the session is recorded
- THEN exactly one run entry exists
