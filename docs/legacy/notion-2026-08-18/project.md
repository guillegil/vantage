# PROJ-1 Vantage — project page export

> **FROZEN. NOT A WORKING DOCUMENT. SCHEDULED FOR DELETION.**
> See `README.md` in this directory first. Snapshot taken 2026-08-18.
>
> The **Conventions** and **Engineering standards** sections of this page are
> not reproduced here — they already live in `CLAUDE.md`, which is maintained.
> The **Constraints** section has been copied into `CLAUDE.md` too, because it
> is an operative rule rather than history. What remains below is the product
> reasoning, the roadmap, the glossary, and the historical decisions table.

## What and why

A pytest run leaves nothing behind but terminal scrollback. When a test starts
failing, the questions that actually matter — when did this start, what changed
between then and now, has it done this before — are answered from memory or by
re-running the suite against older commits. Engineers end up carrying that
history in their heads, where it degrades quietly and disappears entirely when
they change team.

The alternatives today are a CI dashboard or nothing. A CI dashboard only knows
about CI, which is not where most debugging happens, and it belongs to the team
rather than to the person staring at a red test at eleven at night. Vantage
records locally, only when asked, entirely offline, and answers one question
well: **what has this test done over time, and which commit was it doing it on.**

## State at export

**Milestone 1 delivered.** Merged to `main` on 2026-08-16 as PR #19 — 97
commits, 108 tests green on Python 3.10 to 3.13 with and without xdist, twelve
CI jobs green. The plugin records a finished session and reports it over HTTP;
the server performs every write. The earlier `src/vantage` tree, written for a
live run-supervision product, was deleted rather than migrated.

**Next:** Milestone 2 — capture individual test results inside a run. Deferred
there from Milestone 1: RQ-3's result-count criteria and RQ-38's concurrency
criteria 2 and 3. **Then:** FT-2 Test capture, then the read API and the web
view that close Phase 1.

**One gap recorded and still uncovered at export:** all six fields of the run
report are required *and* extra fields are forbidden (`extra="forbid"`), so the
run object cannot gain a field without forcing `/api/v2`. Recorded in the
archived milestone; not decided.

## Roadmap

| Phase | Delivers | Done when |
| --- | --- | --- |
| **Phase 1** — Record, read, view | The plugin records sessions over HTTP and the server writes them to SQLite, a read-only HTTP API serves them, and a web view shows one test's history over time. 8 features, 43 requirements. | The RQ-19 demonstration passes: a test that passed ten times and then failed, with its first failing execution identifiable and its commit reachable from the view. |
| **Phase 2** — Deeper capture | Fixtures used, captured logs, stdout and stderr — the columns exist from day one and stay NULL until here. Incremental flush, so a killed session is not a lost session. Retention and pruning. | A failure can be diagnosed from the recorded run alone, without re-running the suite. |
| **Phase 3** — Beyond one machine | Runs shared beyond the machine that produced them. Test identity reconciled across renames and moves. A second storage adapter, which is what proves the port was ever real. | A reviewer who did not run the suite can open somebody else's run and reach the same conclusion they did. |
| **Phase 4** — Ecosystem | A query interface for agents over MCP. Extension points for other plugins — pytest-strategies vectors, pytest-verify. Aggregate metrics across a repository. | Something outside this repository reads Vantage data through a documented interface, and nobody had to change Vantage to allow it. |

## Glossary

- **Run** — one pytest invocation. The unit of history, and what a result hangs from.
- **Result** — what happened to one test inside one run. A run has many results.
- **Test** — the catalogue entry, not the execution. It survives deletion from the codebase, with the timestamp at which it was last seen. That is RQ-13, and it is the distinction most of the schema turns on.
- **Dirty working tree** — the run came from uncommitted changes and therefore cannot be reproduced by anyone, including the person who produced it.
- **Vector** — pytest-strategies vocabulary: one generated input sample. Directed vectors are named and stable and give regression; random vectors give discovery.
- **Phase / Milestone** — a phase is a released scope; a milestone is a checkpoint inside one. Phase 1 contains several milestones.

## Product positioning

- **Licence: MIT** — chosen for adoption. A pytest plugin nobody is allowed to relicense is a pytest plugin nobody adopts.
- **Stack:** Python, SQLite, FastAPI, TypeScript, React.
- **Visibility:** public. Repository `github.com/guillegil/vantage`.

## Historical decisions table

> The page itself flagged this table as **history, not instruction**: *"read it
> for the reasoning, not for the current shape"*. The ADR files in `docs/adr/`
> superseded it. Two rows below are known to be **wrong now**: "four packages"
> was replaced by ADR-4's two distributions, and the Notion-as-source-of-truth
> row is what this whole export exists to undo.

| Date | Decision | Why | Rejected |
| --- | --- | --- | --- |
| 2026-08-13 | Clean architecture | Same dependency rule as the alternative, but the explicit use-case layer fits a system with a small number of named operations, each with one reason to change | Hexagonal — a good fit for many symmetric adapters, more ceremony than this earns |
| 2026-08-13 | Monorepo with uv workspaces | ~~Four packages~~ that release together and share a core. Independent scaling comes from packages, not repositories. Verified working end to end, including scoping a dependency to one member | Separate repositories per plugin — every change to the shared core would need coordinated releases from day one |
| 2026-08-13 | Vantage, pytest-strategies and pytest-verify stay separate plugins | Each is installable alone and useful alone | One super-plugin — forces all three on anyone who wants one |
| 2026-08-13 | SQLite from the standard library, with FTS5 | Zero dependencies, verified present in the stdlib build, and fast enough for one developer's history | An ORM — the single dependency that would break RQ-24, bought for convenience over six tables. Postgres — a server to install before you can look at your own test results |
| 2026-08-13 | Schema created complete at first use, Phase 2 columns included | Deferring a feature is free; migrating a schema under live user data is not | A migration framework in Phase 1 — having one available is exactly what makes casual schema changes feel affordable |
| 2026-08-13 | Ports as `typing.Protocol` | Structural typing lets an adapter satisfy a port without importing the core, so the dependency arrow points inwards at the type level too | Abstract base classes |
| 2026-08-13 | All project documentation in English | The audience is whoever finds the repository, not whoever wrote it | Spanish — the language the design conversations actually happen in |
| 2026-08-13 | ~~Notion is the source of truth for specs; the repository mirrors it one way~~ **Reversed 2026-08-18.** | Two-way sync between a database and files rots within weeks. `RQ-xx` and `FT-xx` are the join key | Specs living only in the repository — loses the rollups, the dashboard and the MoSCoW budget. **This is the option now chosen.** |
| 2026-08-13 | Requirement IDs appear inside the tests that verify them | Makes coverage derivable by search instead of remembered | A coverage matrix maintained by hand — accurate on the day it is written and never again |

## Decisions resolved out of the design conversation

Each already lives in an ADR in `docs/adr/`; listed for the trail.

| What | Where it went |
| --- | --- |
| The interface is a separate TypeScript client, built in CI, never committed built | ADR-8 |
| ADR-7's "merges two clones" argument was false and is removed | Corrected on ADR-7 before acceptance |
| The database's file mode is the only protection its contents have | RQ-40 |
| One distribution named `vantage` with optional dependency groups, replacing four independently installable packages | ADR-4 |
| Dependency constraints published as ranges, never exact pins; reproducibility from the lockfile CI tests against | ADR-4 |
| The service runs from a container image for real use; the `[server]` extra is a convenience the user accepts knowingly | ADR-4 |
| The built interface travels inside the service, sources in `web/`, `dist/` gitignored, assembled into the wheel by CI | ADR-8 |
| The server owns the write path; the plugin reports over HTTP and never opens a database | ADR-9 |
| The server keeps its database in the user data directory, not under the project root | ADR-10, superseding the deprecated ADR-7 |
| A session killed before it can report leaves no row, and that is a defect rather than an accepted limit | RQ-44 |

## A note on RQ-38 that was recorded as *not* a question

*"Criterion 2 counts 400 results and Milestone 1 writes none"* is a known fact
with a known date, not an open contradiction. It is covered by the rule that
`Verified` means **every** criterion, not the ones currently demonstrable.
