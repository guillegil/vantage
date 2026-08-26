# Delta for Result Capture

> **Archiver directive — Purpose text only.** No requirement changes
> accompany this delta: `result-capture`'s `## Requirements` (RQ-4, RQ-5,
> RQ-9 and their scenarios) are unchanged and MUST be left exactly as they
> stand in the main spec. This delta carries no `ADDED`/`MODIFIED`/`REMOVED`/
> `RENAMED` requirement block for that reason — there is nothing for such a
> block to replace. The only change is the `## Purpose` section below: when
> merging this change, replace the main spec's existing `## Purpose` section
> with the text that follows, verbatim.

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
