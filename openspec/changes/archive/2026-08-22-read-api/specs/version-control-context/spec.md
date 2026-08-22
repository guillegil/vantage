# Delta for Version Control Context

## MODIFIED Requirements

### Requirement: Absent repository (RQ-23)

> **Heading note.** This heading carries the merged capability spec's exact
> text, trailing identifier included, because the archive merge matches a
> `MODIFIED` block to its target **by heading text**. A heading that does not
> match appends a duplicate requirement instead of replacing the existing one.
> The identifier is a join key here, not vocabulary: this change writes none
> of its own, and this suffix disappears when the merged spec is renumbered
> in a change of its own.


Where the project directory is not a git repository, the server MUST record
the run with all six vcs fields null and MUST NOT emit any warning for that
absence — complaining about something optional being missing trains people
to ignore warnings, including the ones that matter. A run recorded this way
MUST appear in the run list alongside runs from repositories, with all six
VCS fields null rather than the run being omitted.
(Previously: the run-list criterion was verified only by Inspection at the
storage level, standing in with the scenario "Absent repository's run is
retrievable in storage, pending a run list," and explicitly not claimed as
met, deferred until `read-api` supplied a run list. That stand-in scenario
and its deferral paragraph are retired now that `history-read-api` exists.)

**Verification: Test**, for every scenario including the run-list criterion,
now that `history-read-api` supplies a real run list to demonstrate it
through.

#### Scenario: Not a git repository records nulls
- GIVEN a directory that is not a git repository
- WHEN a session is recorded
- THEN the run is stored and its commit hash, branch, commit subject and dirty flag are all null

#### Scenario: Absent repository emits no warning
- GIVEN a directory that is not a git repository
- WHEN a session is recorded
- THEN no warning is emitted

#### Scenario: Absent repository's run appears in the run list
- GIVEN a run recorded from a directory that is not a git repository, alongside runs recorded from repositories
- WHEN the run list is requested
- THEN the absent-repository run is present in the list with all six VCS fields null, in no way distinguished in position or omission from any other run
