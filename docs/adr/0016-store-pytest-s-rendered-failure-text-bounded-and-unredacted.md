# 16. Store pytest's rendered failure text, bounded and unredacted

Date: 2026-08-24

## Status

Proposed

## Context

Vantage records that a test failed and nothing about why.
`vantage.core.domain.result.Result` carries an identity, an outcome, timings and
a worker id — sixteen values reach the wire, and not one of them says what went
wrong. pytest builds a failure representation during the run and it dies with
the process.

The product exists to answer *"this test failed four times this month — is it the
same failure or four different ones?"*. Today that question cannot be asked of
the database at all.

**The columns are not the new thing.** `storage/schema.sql` has declared
`failure_type`, `failure_message`, `failure_path`, `failure_lineno`,
`failure_repr`, `traceback`, `captured_stdout`, `captured_stderr` and their
truncation flags since Milestone 1, because ADR-5 and RQ-29 create the schema
complete at first use; `idx_result_failure_path_lineno` has been sitting there
waiting for a writer. Nothing populates any of it. This decision does not add a
column, and `meta.schema_version` does not move.

**The new thing is what those columns will hold.** Text produced by the user's
own test process, copied verbatim into a durable store.
`assert response == expected` fails by printing both values. If the response
carried a token, the token is now in the database. A traceback prints local
variables, an HTTP client prints headers, and a fixture that logs a connection
string logs it into the record.

RQ-35's existing mechanism does not transfer. It redacts by matching *option
names* on a command line — a grammar exists, so a match is decidable. A
traceback is free-form text with no grammar at all, and captured output is
whatever a program chose to print.

Two facts bound the harm without excusing it. The store is local and owner-only
(`storage-permissions`), and nothing leaves the machine (RQ-28), so the reader is
the same person whose terminal already showed the secret. **What genuinely
changes is lifetime.** A transient scrollback becomes a file with no expiry that
someone may later copy to another machine, include in a backup, or attach to a
bug report.

And the decision is expensive to unmake. Under ADR-0013, dropping a populated
column means a `schema_version` bump, and a version bump means every existing
database is **refused rather than migrated** — recorded history is lost. That is
well beyond a sprint to revert, which is the filter `openspec/config.yaml` and
CLAUDE.md set for an ADR.

## Decision

**Vantage stores pytest's rendered failure evidence — the traceback, the
failure's type, message, location and representation, and the captured stdout and
stderr — verbatim and unredacted, under four conditions that hold together and
are not separable.**

1. **Rendered independently of display flags.** The stored traceback is produced
   by the plugin's own `item.repr_failure(excinfo, style="long")`, not by reading
   back whatever pytest happened to render for the terminal. A record whose
   completeness depends on a `--tb` display flag is not a record: `--tb=no`
   would store nothing, the database would look healthy, and nobody would find
   out until the day they needed it.

2. **Bounded twice, at two different layers, because one bound is not enough.**
   Each text field is cut to 64 KiB of UTF-8 at a character boundary by the
   server, with an out-of-band `_truncated` flag beside it — `MAX_TEXT_FIELD_BYTES`
   and `truncate()`, adopted unchanged. Above that, the *plugin* spends a
   per-report budget on JSON-encoded bytes before the request is built, because
   the server's 1 MiB report cap rejects the **whole session, run included**, and
   per-field truncation runs after the point at which it could have prevented
   that. A field dropped for the budget sets the same flag, so absent evidence is
   marked absent rather than merely missing.

3. **Unredacted, and disclosed rather than claimed safe.** No redactor ships. The
   capability spec, the README and OQ-11 state plainly that stored failure text
   may contain credentials. Redaction is deferred, not refused forever; until it
   arrives, the honest position is written down where a user reads it.

4. **Refusable.** A session-level opt-out disables failure-text capture, by
   invocation flag. Consistent with RQ-2, a committed configuration file may
   **narrow** what an already-activated session records and may never **enable**
   capture or clear the opt-out — there is no configuration syntax that turns
   this on.

This authorises storing text the *test process* produced. It does not authorise
storing the host environment, the values on the recorded command line, log
records, or test artefacts. Each of those is its own decision and inherits these
four conditions rather than a pre-granted answer.

## Consequences

- The question the product exists to answer becomes a query rather than an
  eyeballing exercise: twenty tests failing at one source line group as one,
  through an index that has existed since Milestone 1 and has never been used.
- **Vantage will store credentials, on some machine, at some point.** That is
  stated, not mitigated. The exposure that is new is lifetime, not visibility —
  and it is new enough to be worth a user's deliberate choice, which is what
  condition 4 exists to give them.
- Stored paths are whatever pytest reports, which for a frame inside
  `site-packages` is an absolute path that can carry a username. Not redacted,
  for the same reason as everything else here.
- **Cumulative growth is unbounded and nothing here prunes it.** A CI machine
  recording every failing run accumulates up to ~320 KiB per failed test,
  indefinitely. Retention, pruning and vacuum are named as a separate future
  change; no policy is invented in this one.
- Recording now costs a second rendering per failed test, charged against
  RQ-25's overhead budget. `version-control-context` has already spent part of
  that budget, so the number is measured against current figures across a
  failure-density axis, and recorded whether or not the 2% holds.
- **The opt-out is a lever people will pull for the wrong reason and never push
  back**, which is precisely the argument ADR-0014 used to refuse a flag for VCS
  capture. The difference is what refusal costs: there, a project silently loses
  its commit history to save 6 ms. Here, the alternative to a lever is that
  someone who cannot accept unredacted storage must stop using the plugin
  entirely, which makes the disclosure an announcement rather than a choice.
- **`schema.sql` is byte-unchanged and no existing database is refused.** This is
  the payoff RQ-29 and ADR-5 were written to buy, collected for the first time,
  and this change owes a test that proves it rather than a paragraph that
  asserts it.
- Reversal is by supersession, never by edit, and OQ-11 reopens with it. Erasing
  already-recorded text is a `DELETE`/`VACUUM`, not a revert, and no tool ships
  for it here.

## Alternatives rejected

**Store nothing; keep the outcome only.** The honest null option, and it costs
nothing to keep. Rejected because it leaves the product unable to answer its own
stated question. A failure list that says twenty tests failed and cannot say
whether it is one bug or twenty is a list nobody can act on, and the columns
would go on sitting in the schema as a promise nothing keeps.

**Store a redacted traceback.** The obviously desirable option. Rejected on
tractability, not on cost: content-scanning arbitrary free-form text for secrets
is an unbounded problem, and RQ-35's option-name matching does not transfer to
text with no grammar. Worse, **a redactor that misses once is more dangerous than
none**, because it converts a known hazard into a claimed guarantee — a user who
believes the store is scrubbed will treat it as safe to copy, back up and
attach. Deferred to OQ-11 as an open question, not foreclosed.

**Store a hash or a normalised fingerprint of the failure instead of the text.**
Genuinely tempting: it answers the grouping question — *is this the same
failure?* — while storing nothing a credential could hide in. Rejected on two
counts. It answers only that question: a reader who learns four failures are
identical still cannot see what they are, and must re-run the suite to find out,
which is exactly the state this product exists to remove. And a hash over text
containing absolute paths, memory addresses and timestamps groups nothing, so it
would need normalisation first — a parser of free-form text, which is the same
unbounded problem redaction has, arrived at from the other side.

**Store structured frames instead of one text blob.** A `result_frame` table with
a row per stack frame, queryable by file and function, is a better long-term
shape and would make phase and frame attribution exact. Rejected for now on cost
and uncertainty: it multiplies row count per failure, needs its own bound and its
own truncation story, and pytest's `ReprEntry` is a rendering type rather than a
published data contract, so the schema would be chasing a private shape.
Out of scope by the proposal; not foreclosed by this decision.

**Store what pytest already rendered, via `report.longreprtext`.** Free — the
text is already on the report object that crosses the xdist wire. Rejected
because the stored value would then be whatever the user's `--tb` flag produced:
nothing under `--tb=no`, one line under `--tb=line`, all three frames under the
default. The failure mode is silent, and it is the worst kind — the database
looks healthy and is empty of evidence. Condition 1 exists to make the record
independent of how the run happened to be displayed.

**Capture unconditionally, with no opt-out** — the answer ADR-0014 reached for
version-control capture, and consistency argues for repeating it. Rejected
because the two flags refuse different things. There, the flag would let someone
trade a project's commit history for 6 ms on a day they were in a hurry, and the
loss would be discovered months later by someone who did not set it. Here, the
flag is the only alternative to storing values a person may be contractually or
legally unable to store, and its cost when set is that failures stop carrying
their evidence — visible immediately, to the person who set it, in the first
failure they open.

Bound to: ADR-5 (schema created complete at first use), ADR-8 (the web interface
owns output encoding), ADR-9 (record over HTTP and let the server own every
write), ADR-0013 (schema-version refusal rather than migration), ADR-0014 (the
plugin's execution boundary, and its argument against an opt-out flag), RQ-2
(opt-in recording), RQ-22 (bounded stored text), RQ-24 (zero runtime
dependencies), RQ-25 (runtime overhead), RQ-28 (nothing leaves the machine),
RQ-29 (complete schema), OQ-11, and the `failure-evidence`, `session-ingestion`
and `history-read-api` capabilities.
