# Proposal: Milestone 1 — Write one row

**Change:** `milestone-1-write-one-row` · **Phase:** 1 · **Status:** accepted, delivered and archived 2026-08-16
**Source of truth:** PROJ-1 and the `RQ` / `FT` / ADR databases in Notion.
All 40 requirements are `Draft`.

> **Second replan, 2026-08-15.** The first version of this document was written
> against a stale Notion page. The second was written against the correct
> requirements but a different architecture — the plugin writing to SQLite
> directly. **ADR-9** replaced that: the plugin reports over HTTP and the server
> performs every write. This version is written against that model.

## What changed, and what it costs

| | Before | Now |
| --- | --- | --- |
| Who writes | the plugin, to a local file | **the server**, over HTTP |
| Plugin dependencies | core + storage + pytest | **pytest and the standard library** |
| Distributions | four, then one | **two**: `pytest-vantage` and `vantage` |
| Milestone 1 spans | one component | **both**, end to end |

The milestone grew. Proving *one row* now means proving the whole path: a
plugin that reports, a transport, an endpoint that receives, a port, an
adapter and a schema. There is no smaller honest version — a plugin reporting
into a stub proves nothing.

**That is a virtue as well as a cost.** Milestone 1 now demonstrates the
architecture end to end rather than one file write, which is what a first
milestone should do. But it means the server needs a web framework here, not
in Milestone 4 as previously planned.

## Intent

A pytest run leaves nothing behind but terminal scrollback. Vantage exists to
answer *when did this start failing, and on which commit* — and nothing in the
repository can answer it yet.

Milestone 1 makes the path real and inert-by-default: **one pytest invocation
leaves one recorded run**, reported over HTTP, written by the server through a
complete schema, from a plugin that adds nothing to a user's environment.

It delivers nothing user-visible. The outcome is dogfooding readiness.

## Architecture

Settled in ADRs; not re-argued here.

| ADR | Decision |
| --- | --- |
| **ADR-3** | Clean architecture; ports as `typing.Protocol` |
| **ADR-4** | Two distributions with an HTTP boundary, released on prefixed tags |
| **ADR-5** | Complete schema at first use; no migration framework in Phase 1 |
| **ADR-6** | Standard-library `sqlite3`, hand-written SQL, no ORM |
| **ADR-9** | The plugin records over HTTP; the server owns every write |

```
pytest + pytest_vantage ──POST /api/v1/runs──> vantage.service
                                                     │
                                              vantage.core (port)
                                                     │
                                              vantage.storage ──> SQLite
```

## Requirements in scope

| Ref | Name | Priority | Verification | Component |
| --- | --- | --- | --- | --- |
| RQ-1 | Run entry | Must | Test | server |
| RQ-31 | Run timestamps | Must | Test | server |
| RQ-3 | Atomicity | Must | Test | server |
| RQ-2 | Opt-in recording | Must | Test | plugin |
| RQ-21 | Non-disruptive failure | Must | Test | plugin |
| RQ-37 | Unreachable server | Must | Test | plugin |
| RQ-24 | Zero runtime dependencies | Must | Test | plugin |
| RQ-26 | Core isolation | Must | Test | server |
| RQ-27 | Supported runtimes | Must | Test | both |
| RQ-28 | Offline operation | Must | Test | both |
| RQ-29 | Complete schema from first use | Should | **Inspection** | server |
| RQ-30 | Replaceable storage | Should | Test | server |
| RQ-38 | Concurrent sessions | Should | Test | server — **criterion 1 only** |
| RQ-40 | Owner-only store permissions | Should | Test | server |
| RQ-41 | Session report ingestion | Must | Test | server |
| RQ-42 | Malformed report rejection | Must | Test | server |

Eleven Must, five Should.

**RQ-38 is half provable here.** Its second criterion counts results from two
200-test sessions and this milestone writes no results. Criterion 1 — two
sessions leave two run entries — is provable now.

**RQ-3 is now genuinely exercised**, which it was not under the old model. A
report can be cut off in flight, so "observable in full or not at all" has a
real failure to defend against rather than being satisfied by writing one row.

Deferred: RQ-4…RQ-13 and RQ-32 (test capture, Milestones 2–3), RQ-22, RQ-23,
RQ-35, RQ-39 (Milestone 3), RQ-14…RQ-17, RQ-33, RQ-36 (the read API,
Milestone 4), RQ-18…RQ-20, RQ-34 (the interface, Milestones 5–6), RQ-25
(overhead, once there is a path worth measuring).

**Deferred does not mean unconstraining**: RQ-29 builds the schema whole now,
so the columns those requirements dictate are created in this milestone even
though nothing populates them.

## The gap this replan found, now closed

Assembling the list above made a hole visible: **nothing in the set said the
server accepts session reports.** RQ-14 covers the *read-only* API; the
ingestion endpoint ADR-9 made necessary had no requirement at all. The gap did
not exist before that decision, because before it there was no endpoint.

Two requirements now cover it, both `Draft`:

| Ref | Name | Priority | Pattern |
| --- | --- | --- | --- |
| **RQ-41** | Session report ingestion | Must | Event-driven |
| **RQ-42** | Malformed report rejection | Must | Unwanted behaviour |

They are split because they are different scenarios with different outcomes,
and because the guide names the unwanted-behaviour path as the most-skipped
and the most valuable. An endpoint written without RQ-42 would have had its
rejection behaviour decided by whatever the parser happened to throw.

Two things they settle that the milestone depends on. **RQ-41 requires
idempotency** — a plugin that times out waiting for an acknowledgement must be
able to retry without inventing a duplicate run, and because the identifier is
generated by the client with `uuid4`, the server can settle that by identity
rather than by guessing. **RQ-42 requires storing nothing from a report it
cannot understand**, which is what makes RQ-3 achievable at all: *observable in
full or not at all* cannot hold if a partial parse leaves a partial row.

## Capabilities

`openspec/specs/` is empty, so every capability is new and none is a delta.
These are the names `sdd-spec` wrote files under and the names `sdd-tasks`
groups work by — do not invent a different decomposition.

| Capability | Requirements | Component |
| --- | --- | --- |
| `run-recording` | RQ-1, RQ-31, RQ-3, RQ-38 (criterion 1) | server |
| `session-ingestion` | RQ-41, RQ-42 | server |
| `recording-schema` | RQ-29 | server |
| `storage-permissions` | RQ-40 | server |
| `opt-in-activation` | RQ-2 | plugin |
| `recording-fault-tolerance` | RQ-21, RQ-37 | plugin |
| `architecture-boundaries` | RQ-24, RQ-26, RQ-30 | both |
| `runtime-support` | RQ-27, RQ-28 | both |

### Modified capabilities

None.

## Scope

### In scope

- **Plugin** (`pytest-vantage`): `pytest11` entry point; the inert
  `plugin.py` / registered `recorder.py` split; server address resolution;
  one batched report per session over `urllib`; the error boundary; the
  unreachable-server path.
- **Server** (`vantage`): `vantage.core` with the storage port and address
  resolution; `vantage.storage` with `schema.sql`, the stdlib `sqlite3`
  adapter and an in-memory one; `vantage.service` with the ingestion
  endpoint; owner-only permissions on what it creates.
- **Schema**: complete at first use (RQ-29), every Phase 1 column plus the
  Phase 2 columns, checked by inspection against `docs/schema-manifest.md`.
- **Architecture test**: static `ast` walk asserting `vantage.core` imports
  only the standard library, with the non-vacuity guard.
- **Quality gates**: pre-commit and pre-push stages, the 3.10–3.13 × xdist
  matrix, a networking-disabled job, a clean-environment install check
  asserting `pytest-vantage` adds exactly one distribution.

### Decided product rule

A run entry is written for **every** invocation with recording enabled,
including one that collects zero tests, one whose collection fails, and one
interrupted with Ctrl-C. RQ-1 carves out no exceptions, and RQ-31's second
criterion depends on it — an interrupted session is exactly the one that must
leave a start time and a null end.

### Out of scope

| Deferred | Where |
| --- | --- |
| Any result row, and everything about test capture | Milestones 2–3 |
| Populating any Phase 2 column | Phase 2 |
| The read API, the interface document, `web/` | Milestones 4–6 |
| PostgreSQL — the `[postgres]` extra is declared, no adapter written | Phase 3 |
| Authentication of the ingestion endpoint | Phase 4, with the shared server |
| A migration framework, per ADR-5 | never in Phase 1 |

## Approach

**The plugin stays inert until asked.** pytest fires any `pytest_*` hook it
finds on a registered plugin, so `plugin.py` declares only `pytest_addoption`
and `pytest_configure`; the recorder is registered through
`config.pluginmanager.register(...)` only when a server address is configured.
That split is what lets CON-05 and RQ-2 coexist.

**One request per session, never per test.** RQ-25's criterion makes this
observable — the request count must be independent of the test count — and it
is what keeps a network round trip out of the inner loop of somebody's suite.

**Three failure paths, not two.** RQ-37 covers an unreachable server, found in
`pytest_configure` before any test runs. RQ-21 covers an internal error while
reporting. The third exists only because the boundary is now a network: **a
server that accepts the connection and never answers.** A bounded timeout is
part of the obligation — a hang is worse than a failure, because the user
cannot tell whether the suite is slow or stuck.

**The port is designed for both.** `vantage.core` defines the storage port;
`vantage.storage` provides the sqlite adapter and an in-memory one. RQ-30's
second criterion requires the core to import neither.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| A third-party import creeps into `pytest-vantage` or `vantage.core` | Medium | Three guards: the AST architecture test, `deptry`, and the clean-environment install |
| The ingestion endpoint is built without a requirement to check it against | **High** | Write the requirement first; see *Open items* |
| Schema wrong once users hold data | Medium | Full Phase 1 + Phase 2 columns now, manifest-compared. Accepted per ADR-5 |
| A hang rather than a failure when the server misbehaves | Medium | Bounded timeout as an obligation, with an acceptance criterion of its own |
| The web framework arrives three milestones early | Medium | Accepted. It is the consequence of ADR-9 and there is no smaller honest milestone |
| RQ-38 half-verified here | Medium | Criterion 1 tested now; criterion 2 explicitly carried to Milestone 2 |

## Delivery

Not forecast yet — the shape changed enough that the previous estimate is
void. `sdd-tasks` produces the real number against concrete work items.

Chained, `feature-branch-chain`, budget 400 authored lines per PR. Already
landed: the clean slate and workspace, the ADRs, and the documentation.

The natural remaining cut follows the components: the server's core and
storage, then its ingestion endpoint, then the plugin, then the quality gates.
Whether that is four PRs or six is for the forecast to say.

## Success criteria

Each maps to a requirement's own acceptance criterion.

- [ ] **RQ-1** — one invocation leaves exactly one run entry; a second leaves another with a different identifier; zero-test and failed-collection runs each leave one.
- [ ] **RQ-31** — a completed session's entry holds start and end times; an interrupted one holds a start and a null end.
- [ ] **RQ-3** — a report truncated in flight stores none of that session, not a prefix.
- [ ] **RQ-2** — with no recording option, no connection is attempted and the project tree is unchanged.
- [ ] **RQ-21** — an injected fault yields exit 0 on a passing suite and exit 1 on a failing one; a server that accepts and never answers does not hang the suite.
- [ ] **RQ-37** — nothing listening, and an unresolvable host, each yield exit 0 and one warning naming the address.
- [ ] **RQ-24** — in an environment with pytest, installing `pytest-vantage` adds exactly one distribution; every import resolves to the standard library or pytest.
- [ ] **RQ-26** — every import in `vantage.core` resolves to the standard library, and the non-vacuity guard is green.
- [ ] **RQ-27** — CI green on 3.10–3.13, with and without xdist; a 3.9 install is refused.
- [ ] **RQ-28** — the suite passes with networking disabled.
- [ ] **RQ-29** — by inspection, every documented column exists in a fresh database.
- [ ] **RQ-30** — the core suite passes unchanged against the in-memory adapter.
- [ ] **RQ-38 (criterion 1)** — two concurrent sessions leave two run entries.
- [ ] **RQ-40** — the database is created `0600` and the store `0700` under a permissive umask.
- [x] `grep -r "RQ-1"` reaches the test that proves it, and likewise for every requirement above. (The id was written `RQ-01` here and matched nothing; requirement ids are unpadded.)

## Open items

1. **The server's own configuration is unspecified** — where its database
   lives, what address it binds. ADR-7 answered the old question and is
   deprecated; the new one has no answer and no ADR.
3. **How many plugin versions the server supports** is a commitment implied by
   ADR-4 and not yet written down.
4. **RQ-38's second criterion** is carried to Milestone 2 and noted on the
   requirement.
