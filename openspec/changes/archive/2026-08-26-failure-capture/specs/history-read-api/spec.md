# Delta for History Read API

## MODIFIED Requirements

### Requirement: Lean list projections

List responses (run list, and any results list) MUST carry only
bounded-size fields, excluding traceback and captured output; the full
record MUST remain reachable via the corresponding single-item endpoint. A
run's commit subject in a list response MUST be limited to a fixed,
documented display width smaller than the stored value, with the full
stored subject reachable on the run's detail endpoint. The truncation flag
MUST travel with the subject wherever the subject appears, never surfaced on
its own. `vcs_root` MUST appear in no response, list or detail.
(Previously: the traceback/captured-output exclusion half was verified only
by Inspection, because `result.traceback` had no writer and the exclusion
could not fail. `failure-evidence` now populates it, so the exclusion is
Test.)

**Verification: Test**, over both halves — the VCS-projection half (subject
width, truncation flag, `vcs_root` exclusion) and the traceback/captured-
output exclusion half, now that `failure-evidence` populates `traceback`,
`captured_stdout` and `captured_stderr`.

#### Scenario: List responses exclude traceback and captured output
- GIVEN a result whose traceback and captured stdout/stderr are recorded
- WHEN that result appears in a list response
- THEN its entry excludes the traceback and captured-output fields
- AND the full record remains reachable via that result's single-item endpoint

#### Scenario: The commit subject is bounded in list responses
- GIVEN a run whose recorded commit subject exceeds the list projection's display width
- WHEN that run appears in a list response
- THEN the commit subject in that entry is limited to a fixed, documented width smaller than the stored value, and the full stored subject remains reachable via that run's detail endpoint

#### Scenario: The truncation flag never surfaces independently of its subject
- GIVEN a run whose stored commit subject was truncated at capture time
- WHEN that run's commit subject appears in any response, list or detail
- THEN the truncation flag is present alongside it in that same response

#### Scenario: `vcs_root` appears in no run list or run detail response
- GIVEN a run recorded from a repository with a known `vcs_root`
- WHEN that run appears in a list response and is separately requested by its detail endpoint
- THEN the recorded `vcs_root` value does not appear in either response body

## ADDED Requirements

### Requirement: Single result detail

A single result MUST be reachable via a documented single-item endpoint
that returns its full record, including every field a list response
excludes — traceback, captured stdout, captured stderr, failure type,
message, path, line number and representation, and their truncation flags.
Requesting an identifier for a result that does not exist MUST be answered
without altering stored data.

**Verification: Test.**

#### Scenario: The full record is reachable for a given result
- GIVEN a failed result whose traceback and captured output were recorded
- WHEN that result is requested by its single-item endpoint
- THEN the response carries its traceback, captured stdout, captured stderr, failure type, message, path, line number and representation

#### Scenario: A bounded field's truncation flag travels with it on the single-item endpoint
- GIVEN a result whose traceback was truncated at capture time
- WHEN that result is requested by its single-item endpoint
- THEN the truncation flag is present alongside the traceback in that response

#### Scenario: An unknown result identifier leaves stored data unchanged
- GIVEN a result identifier that does not exist
- WHEN it is requested by the single-item endpoint
- THEN the request is answered without creating, altering or removing any row
