# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read this before planning anything

**The repository is the source of truth. There is no external spec system.**
Specs live in **OpenSpec** (`openspec/`) and session memory lives in **Engram**.
Nothing else.

| What | Where |
| --- | --- |
| Capability specs | `openspec/specs/<capability>/` — merged from each archived change |
| Changes in flight | `openspec/changes/<change-name>/` — proposal, design, tasks, delta specs |
| Archived changes | `openspec/changes/archive/YYYY-MM-DD-<change-name>/` |
| Project context and rules | `openspec/config.yaml` |
| Decisions | `docs/adr/NNNN-title-in-kebab-case.md`, four-digit padded |
| Session memory | Engram, project `vantage` |

> **Vantage used Notion as the source of truth until 2026-08-18. It no longer
> does.** Do not read from it, write to it, or cite it. Any instruction anywhere
> in this repository telling you to sync a requirement, feature or ADR to Notion
> is stale — ignore it and correct it where you find it.

**The requirement corpus has not been migrated yet.** Notion held 43
requirements (`RQ-1`…`RQ-44`; there is no `RQ-43`) and only 16 of them were ever
mirrored into the repository. All 43 were dumped on the way out to
`docs/legacy/notion-2026-08-18/`, which is **frozen, authoritative of nothing,
and scheduled for deletion.** Read it for the rejected alternatives — they are
there because someone already tried the obvious thing — and migrate what
survives into OpenSpec. Then delete the directory.

Where a requirement ID appears in this file, in a test marker, or in
`openspec/`, it still means the same obligation. The IDs are the join key and
they outlive the tool that issued them.

### ADRs

`docs/adr/NNNN-title-in-kebab-case.md` is now the only copy — nothing mirrors it.

- Format is **Nygard** (Status / Context / Decision / Consequences) by default; MADR
  with drivers, options and pros-and-cons when the decision was contentious or
  expensive.
- `Status` is `Proposed` while in the PR, `Accepted` on merge, and immutable from
  then on. Rejected ADRs are kept, never deleted. Supersede rather than edit.
- Title is the decision in imperative mood, not the problem: *Use PostgreSQL as
  primary store*, not *Database evaluation*.
- **`Reversal cost` is the filter.** Anything cheaper than a sprint to revert
  probably does not need an ADR at all. Use it before writing one.
- One ADR, one PR. Link it to the `Requirements` and `Features` it binds.

## State of the tree

The reset is done. `src/vantage` and the old `tests/` tree — written for live run
supervision, which is Phase 3 — no longer exist; the tree is `packages/pytest-vantage`
and `packages/vantage` under one uv workspace, and `pyproject.toml` and
`docs/architecture.md` were rewritten for it. Anything you read describing a
`src/` layout, four distributions, or a plugin that opens a database predates
ADR-4 and ADR-9 and is history, not instruction.

Milestone 1 (`milestone-1-write-one-row`) is complete: the plugin records a
session over HTTP and the server writes the row. CI runs the 3.10–3.13 × xdist
matrix on `main` and every `milestone-*` branch.

## Architecture

**Clean architecture**, not hexagonal — hexagonal was considered and rejected as
more ceremony than this earns. Ports are `typing.Protocol`, not abstract base
classes, so an adapter satisfies a port without importing the core and the
dependency arrow points inwards at the type level too.

**Two published distributions in one uv workspace (ADR-4), not four packages.** An
earlier decision split this four ways and one slice of it landed as four empty
`pyproject.toml` files; ADR-4 rejected that by name and collapsed it before any of
them carried code. Anything still saying `vantage-core`, `vantage-storage`,
`vantage-pytest` or `vantage-service` predates ADR-4 and describes a layout that no
longer exists.

| Distribution | Contains | May depend on |
| --- | --- | --- |
| `pytest-vantage` | the plugin | pytest and the standard library, nothing else (RQ-24) |
| `vantage` | `vantage.core`, `vantage.storage`, `vantage.service` | see below |

The dependency rule is enforced per **internal package** inside `vantage`, by an AST
architecture test rather than by distribution boundaries:

| Internal package | May depend on | Why |
| --- | --- | --- |
| `vantage.core` | nothing at all | RQ-26 forbids it importing any pytest, database or web module |
| `vantage.storage` | the core only | `sqlite3` is standard library, so the adapter needs nothing else |
| `vantage.service` | anything | the only one allowed a third-party dependency — a server someone installs deliberately |

**The plugin never opens a database (ADR-9).** It reports a finished session over
HTTP to `POST /api/v1/runs` and the server performs every write. That is what keeps
the plugin dependency-free, and it is why the SQLite adapter lives in `vantage` and
the question of it living in the plugin no longer arises at all.

Layout is `packages/pytest-vantage` and `packages/vantage`, with `openspec/` and
`docs/` at the root. The root-level `specs/` directory is gone — it was a partial
one-way mirror of Notion holding 16 of the 43 requirements, and OpenSpec is the
home now.

## Requirement traps

Five requirements have a subtlety that costs a rewrite if missed:

- **RQ-2 (opt-in recording).** "Absent from the invocation" means **the flag, not a
  configuration file.** A config file committed by one person silently enables
  recording for everyone who checks the repository out — the exact failure the
  requirement exists to prevent. Its acceptance criterion is *differential*: run
  the suite once with the flag absent and once with `-p no:vantage`, and assert the
  two trees are identical. The absolute form ("no file created in the project
  tree") is unsatisfiable, because pytest writes `.pytest_cache` and `__pycache__`
  itself.
- **RQ-24 (zero runtime dependencies).** The rule is no **third-party**
  distribution; Vantage's own are fine. Note what this does *not* license under
  ADR-4 and ADR-9: `pytest-vantage` depends on pytest and the standard library
  and nothing else. It does not pull `vantage`, because it never opens a database
  — it speaks HTTP with `urllib`. If installing the plugin ever drags the server
  in, the boundary has been broken, not merely bent.
- **RQ-12 (xdist).** Under xdist every result is emitted twice, once by the worker
  and once by the controller. The filter is whether the config object carries a
  worker input attribute.
- **RQ-29 (complete schema).** The full schema, including columns nothing populates
  until a later phase, is created at first use. No migration framework in Phase 1 —
  having one available is exactly what makes casual schema changes feel affordable.
- **RQ-44 (abandoned run is observable)** was added on 2026-08-16, after Milestone 1
  closed, and nothing in this repository implements it yet. It requires a run with a
  start time and no end time to read back as *abandoned* once a grace period lapses,
  and as *interrupted* when a Ctrl-C report did arrive. Today the plugin sends
  nothing until `pytest_sessionfinish`, so a killed session leaves no row at all and
  there is nothing to present. Satisfying it needs a write at the **start** of a
  session, which changes the ingestion contract. Decide that before Milestone 2
  rather than during it.

## Conventions

**Requirement traceability, for the identifiers that already exist.** Every test
that verifies one carries its ID: `@pytest.mark.req("RQ-12")`. Where verification
is not a test — a CI matrix, a benchmark script — the ID goes in a comment on the
relevant block. The invariant is that `grep -r "RQ-12"` finds the thing that
proves it.

**No new `RQ-xx` identifiers are minted.** Decided 2026-08-18. The existing ones
stay because they are executable — 55 markers, `--strict-markers` is on, and CI
and `docs/schema-manifest.md` both cite them — and removing them would break the
traceability of work already delivered. But they were Notion's numbering scheme,
Notion is gone, and OpenSpec already carries identity in a form this project
uses: a **capability** and a **scenario**. New obligations get those, not a
number. Reference an existing `RQ-xx` when you are working on it; do not invent
`RQ-45`.

**Verification methods are not all tests.** Each requirement declares one of Test,
Analysis, Inspection or Demonstration. RQ-11 and RQ-29 are Inspection, RQ-25 is
Analysis, RQ-18/19/20 are Demonstration. Do not write an assertion where the
requirement asks for a measurement with a method.

**No domain class name starts with `Test`.** pytest collects them as test classes and
warns on every run. The aggregate is `Execution`, its identity is `Identity` — not
`TestExecution` or `TestIdentity`. The domain vocabulary and pytest's collection
convention collide, and the domain gives way.

**Branches** are `ft/FT-03-execution-context` or `rq/RQ-12-xdist-dedup`. The
requirement or feature ID goes in the **commit body, not the subject**. Commits are
signed with the 1Password SSH key, from the first commit.

**All project documentation is in English**, regardless of the language the design
conversation happened in.

**Spec-driven development runs the full cycle now.** Milestone 1 entered at
`sdd-tasks` because explore, propose, spec and design had been done by hand in
Notion and regenerating them would have produced a worse second copy. That
shortcut is spent: the hand-written originals are frozen in
`docs/legacy/notion-2026-08-18/` and nothing maintains them. New work starts at
the phase the change actually needs.

## Constraints

These came out of the employment situation, not out of the design. They are
operative rules, not history, and they were previously recorded only in Notion.

- **Intellectual-property ownership — resolved**, imposed by the TMC employment
  contract and answered before any code was written.
- **Synthetic data only.** Every fixture and every example is generated. No test
  suite, log, trace or artefact from ASML or TMC ever touches this repository.
- **Nothing in the semiconductor, EDA or RTL domain.** Keeps "unrelated to my
  employer's work" a true statement rather than an arguable one.
- **Personal equipment, outside TMC hours.**
- **The repository is public.** Nothing confidential, personal or regulated is
  committed to it. Licence is MIT, chosen for adoption.

## Validation and dependencies

Pydantic v2 belongs at system boundaries — APIs, configs, payloads — because static
types do not protect against malformed JSON. **It lives in `vantage.service` only.**
RQ-24 forbids it, and `attrs`, and every other third-party package, from
`vantage.core`, `vantage.storage` and the whole of `pytest-vantage`. Those use
`dataclasses` from the standard library and hand-written validation over stdlib
`json`.

## Commands

The uv workspace has landed; these commands work today.

```bash
uv sync                                  # whole workspace, one lockfile
uv run pytest                            # every package
uv run pytest packages/pytest-vantage    # one distribution
uv run pytest -k test_name               # one test
uv run pytest -m 'req("RQ-2")'           # everything verifying one requirement
uv run ruff format . && uv run ruff check --fix .
uv run mypy .                            # strict
uv run deptry .                          # undeclared / unused dependencies
uv run pip-audit                         # CVEs
```

## Quality gates

Each check sits in the layer whose time budget it fits. A check slower than people
will wait for is a check skipped with `--no-verify`.

| Layer | Contents |
| --- | --- |
| Editor / LSP | `ruff format`, `ruff check --fix` on save; type checker as a language server |
| pre-commit (< 2–3 s) | `ruff format`, `ruff check --fix`, and hygiene hooks; modified files only |
| pre-push | `mypy --strict` over the whole project; fast unit tests |
| CI | everything again in verification mode, the 3.10–3.13 × xdist matrix, a networking-disabled job for RQ-28, `deptry`, the clean-environment install check for RQ-24, a Python-3.9-install-refused job, and a build of both wheels |
| Weekly | `pip-audit`, plus dependency updates |

**Coverage is not measured.** It was planned for CI and never landed: there is no
`pytest-cov` in the dev extra or the lockfile, and `openspec/config.yaml` sets
`coverage_threshold: 0` deliberately. Do not write it up as if it ran.

**`pip-audit` runs weekly, not per pull request.** CVEs appear without anyone
touching the code, so a PR-only check never sees them.

`mypy` goes at pre-push, not pre-commit: pre-commit passes only modified files and a
type checker needs the whole project to be correct. Everything in pre-commit is
duplicated in CI, because pre-commit is skipped with `--no-verify` and never runs on
the server. `ruff`'s `S` rules cover what bandit did; bandit is not used.

Three guards protect RQ-24 and RQ-26 and they are not redundant: the AST
architecture test catches the core reaching an infrastructure module, `deptry`
catches an undeclared third-party import in source, and the clean-environment
install check catches what actually lands in a user's environment.
