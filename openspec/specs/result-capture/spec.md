# Result Capture Specification

## Purpose

Defines what the system records for each individual test a session executed:
an outcome that reflects every execution phase, the duration of each phase
separately, and an identity decomposed into separately queryable values.
What a failed result additionally records — its traceback, failure type,
message and location, and its captured stdout and stderr — is defined in
`failure-evidence`.

Throughout this spec, **null means "did not happen"** and is never
interchangeable with zero or with the empty string. That distinction is the
substance of RQ-5 criterion 2 and RQ-9 criteria 2 and 3, not a stylistic
preference.

**Component:** both — `pytest-vantage` observes the phases and reports them
inside the single session report it already sends; `vantage` persists them
through `vantage.core`'s storage port. The plugin opens no database (ADR-9)
and sends no additional request per test (RQ-25 criterion 2).

## ADDED Requirements

### Requirement: Outcome across phases (RQ-4)

The system MUST record for each test an outcome that reflects every execution
phase that test produced, not the call phase alone.

The recorded outcome MUST be one of exactly six values — `passed`, `failed`,
`error`, `skipped`, `xfailed`, `xpassed`. This vocabulary is closed: it is the
existing `result.outcome` CHECK constraint, and the accepted proposal-round
decision confirms there is no seventh outcome to invent.

#### Scenario: Fixture raising before the body yields error (RQ-4.1)
- GIVEN a test whose fixture raises before the test body runs
- WHEN the session is recorded
- THEN its recorded outcome is `error`

#### Scenario: Skip marker yields skipped (RQ-4.2)
- GIVEN a test decorated with `@pytest.mark.skip`
- WHEN the session is recorded
- THEN its recorded outcome is `skipped`

#### Scenario: Failing xfail yields xfailed (RQ-4.3)
- GIVEN a test decorated with `@pytest.mark.xfail` that fails
- WHEN the session is recorded
- THEN its recorded outcome is `xfailed`

#### Scenario: Passing xfail yields xpassed (RQ-4.4)
- GIVEN a test decorated with `@pytest.mark.xfail` that passes
- WHEN the session is recorded
- THEN its recorded outcome is `xpassed`

#### Scenario: Teardown error after a passing call is not passed (RQ-4.5)
- GIVEN a test that passes but whose teardown raises
- WHEN the session is recorded
- THEN its recorded outcome is not `passed`
- AND, per the accepted outcome-vocabulary decision, it is `error`

### Requirement: Per-phase duration (RQ-5)

The system SHOULD record the duration of the setup, call and teardown phases
separately. A phase that never ran MUST record a null duration, never zero.

#### Scenario: Setup dominates a slow fixture (RQ-5.1)
- GIVEN a test whose fixture sleeps 8 seconds and whose body sleeps 0.1 seconds
- WHEN the result is recorded
- THEN its setup duration is at least 8 seconds
- AND its call duration is below 1 second

#### Scenario: A phase that never ran is null, not zero (RQ-5.2)
- GIVEN a test that fails during setup and therefore produces no call phase
- WHEN the result is recorded
- THEN its call duration is null rather than zero

### Requirement: Decomposed identity (RQ-9)

The system MUST record each test's file path, class name, function name and
parameter identifier as separately queryable values. An absent class name or
an absent parameter identifier MUST be recorded as null, never as an empty
string.

#### Scenario: Filtering by file path alone (RQ-9.1)
- GIVEN a suite spanning several files and classes
- WHEN results are filtered by file path alone
- THEN every test defined in that file is returned

#### Scenario: Module-level test has a null class name (RQ-9.2)
- GIVEN a test defined at module level rather than inside a class
- WHEN the result is recorded
- THEN its class name is null rather than an empty string

#### Scenario: Unparametrised test has a null parameter identifier (RQ-9.3)
- GIVEN a test that is not parametrised
- WHEN the result is recorded
- THEN its parameter identifier is null

#### Scenario: An empty parameter identifier stays distinct from an absent one (extension)

**This scenario extends RQ-9 beyond its literal criteria.** The requirement's
own examples do not cover it. It applies criterion 2's absent-versus-empty
principle — stated there for `class_name` — to `param_id`, because this
project's own suite contains
`packages/vantage/tests/test_execution.py::test_identity_rejects_anything_but_32_lowercase_hex_characters[]`,
whose parameter identifier is the empty string.

- GIVEN a parametrised test whose parameter identifier pytest renders as the empty string
- WHEN the result is recorded
- THEN its parameter identifier is the empty string and is not null
- AND a query selecting tests whose parameter identifier is null does not return it
