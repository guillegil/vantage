# Delta for History Read API

## ADDED Requirements

### Requirement: Exact key=value equality filter

The runs list surface MUST support an exact `key=value` equality filter over
`run_metadata`, using the `(key, value)` index. A run recorded before the
filtered key was declared MUST be excluded from the match — it has no value
for a key that did not yet exist — and the response MUST additionally report
how many runs predate that key, so the horizon of the excluded history is
stated rather than implied.

**Verification: Test.**

#### Scenario: A key=value filter returns matching runs
- GIVEN runs recorded with `firmware_version=2.1` and others with a different value
- WHEN the runs list is requested filtered by `firmware_version=2.1`
- THEN only the matching runs are returned

#### Scenario: Runs predating the key are excluded and counted
- GIVEN some runs recorded before `firmware_version` was ever declared, and others recorded after with `firmware_version=2.1`
- WHEN the runs list is requested filtered by `firmware_version=2.1`
- THEN the pre-declaration runs are excluded from the match, and the response reports how many runs predate that key

#### Scenario: An unknown key or value yields an empty match, not an error
- GIVEN no run has ever recorded a given key
- WHEN the runs list is requested filtered by that key and any value
- THEN the response reports zero matching runs rather than an error
