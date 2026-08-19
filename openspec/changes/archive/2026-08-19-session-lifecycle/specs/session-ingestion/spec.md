# Delta for Session Report Ingestion

RQ-42.3 asserted that a truncated report leaves the run table empty. That was
true only because every report arrived in one POST. With a start-write, a
truncated *finish* report can now be rejected while a run entry created by an
earlier accepted start-write legitimately remains.

## MODIFIED Requirements

### Requirement: Malformed report rejection (RQ-42)

If a submitted session report cannot be understood, then the server MUST
reject it and MUST store nothing from it. The rejection response MUST name
the offending field or condition without exposing internal identifiers or a
traceback.

A `results` section that cannot be understood MUST reject the **entire**
report, including its run and every other result it carried. The server MUST
NOT store the parseable subset: RQ-3 requires a session to be observable in
full or not at all, and a session stored minus the entries that failed to parse
is a partial write wearing the appearance of a whole one.

Rejecting a report never removes or alters a run entry created by an earlier
*accepted* report for the same session. A rejected report stores nothing
**from that report**; it does not undo what a prior accepted report already
wrote.
(Previously: RQ-42.3 asserted a truncated report leaves the run table empty
in every case, which assumed no prior report for the session could exist.)

#### Scenario: Missing required field (RQ-42.1)
- GIVEN an empty database
- WHEN a report with a missing required field is submitted
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Invalid JSON (RQ-42.2)
- GIVEN an empty database
- WHEN a payload that is not valid JSON is submitted
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Body truncated midway, no prior report (RQ-42.3)
- GIVEN an empty database with no prior report for this session
- WHEN a report is submitted whose body is truncated midway
- THEN the response reports the rejection and the run table stays empty

#### Scenario: Finish report truncated after an accepted start-write (RQ-42.3, RQ-3.2)
- GIVEN a session whose start-write has already been accepted, leaving a run entry with a null `finished_at`
- WHEN that session's finish report is submitted with its body truncated midway
- THEN the response reports the rejection
- AND the run entry is left exactly as the start-write wrote it — a start time, a null `finished_at`, and no result rows — rather than the run table being emptied or the finish report's data being applied

#### Scenario: One malformed result rejects the whole report (RQ-42.1, with RQ-3.2)
- GIVEN an empty database
- WHEN a report carrying 500 results is submitted whose single result at index 250 is malformed
- THEN the response reports the rejection
- AND the run table stays empty and no result row is stored, rather than the 499 parseable entries being kept

#### Scenario: Rejection names the cause, safely (RQ-42.4)
- GIVEN a rejected report
- WHEN the response is read
- THEN it names which field or condition caused the rejection, without exposing internal identifiers or a traceback
- AND where the cause is an entry in the `results` section, it names that entry and its offending field
