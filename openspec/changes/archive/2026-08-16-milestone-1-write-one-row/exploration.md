# Exploration — Milestone 1: Write one row

> **Written against a superseded source. Read `proposal.md` instead.**
> This exploration was produced from the stale Notion narrative page, so it says
> hexagonal architecture, three packages and `REQ-1-xx` identifiers. The authoritative
> source is PROJ-1: clean architecture, four packages (`vantage-core`,
> `vantage-storage`, `vantage-pytest`, `vantage-service`) and `RQ-xx`.
> Kept as a record of how the schema mismatch was found (§3). Nothing else here is
> load-bearing.

**Change:** `milestone-1-write-one-row`
**Phase:** Phase 1 — capture and display
**Status:** complete, with one corrected finding (see *Correction* below)
**Source of truth:** the Notion Requirements page, not the committed code.

---

## 1. Ruling this exploration works under

Notion is authoritative. The committed `src/vantage` code is **relocated to the
server side, not deleted**. The plugin package `packages/pytest-vantage` is built
to the Notion contract: standard-library `sqlite3` only, zero third-party runtime
dependencies (NFR-1-01).

## 2. Inventory of the committed tree

> **Superseded verdicts.** The "Verdict" column below assumed the committed code
> would be relocated. It is now deleted outright — see §8. The inventory itself
> is still accurate as a record of what was there.

`src/vantage/` is a single hexagonal package built entirely on SQLAlchemy 2,
Pydantic v2 and Alembic:

| Area | Modules | Verdict |
| --- | --- | --- |
| Domain models | `core/domain/models.py` — frozen dataclasses, stdlib only | Pattern reference for the plugin; not directly reusable as-is |
| Event envelope | `core/domain/events.py` — Pydantic | Server side |
| Ports | `core/storage/repository.py`, `core/ingestion/sink.py` — Pydantic | Server side |
| SQLite adapter | `adapters/sqlite/{engine,repository,event_sink,tables}.py` | Server side |
| Migrations | `migrations/versions/{0001,0002}` — Alembic | Server side |
| Tests | `tests/**` (10 files) | Relocate alongside their code |

Every module in `src/vantage` depends on a third-party runtime package, so **none
of it can be imported by the zero-dependency plugin**. Nothing is dead weight —
it is simply on the other side of the boundary.

## 3. Correction — the existing schema is not the Phase 1 schema

The initial exploration concluded that `adapters/sqlite/tables.py` already holds
the complete Phase 1 + Phase 2 schema and could be hand-ported as frozen DDL.
**That conclusion is wrong.** It was reached without access to the Notion
requirements; the schema was validated against `openspec/config.yaml`, which
describes the earlier product plan.

`tables.py` is the schema of a *live run-orchestration* product — Notion's
Phase 3 — not of the capture-and-display MVP:

| Present in `tables.py`, not in Phase 1 | Purpose |
| --- | --- |
| `runs.state`, `stop_reason`, `last_heartbeat_at` | Live run supervision |
| `discovery` table | Collection hook for launching runs |
| `events` table with an SSE poll cursor (`seq`) | Live streaming to a UI |
| `runs.seed`, `seed_source`, `marker_expr`, `keyword_expr` | Run-launch selection metadata |

Conversely, requirements that Phase 1 marks **Must Have** have no column at all:

| Requirement | Needs | Present in `tables.py` |
| --- | --- | --- |
| REQ-1-10 · version-control context | commit hash, branch, first line of the commit message, dirty-tree flag | **absent** — no git columns exist |
| REQ-1-08 · failure location | file path, line number and message as queryable columns, plus the full traceback | **partial** — only `longrepr`, an opaque blob |
| REQ-1-11 · machine context | host, user, Python version, pytest version, platform, command line | **partial** — only `user`; the rest is buried in `invocation` / `env_snapshot` JSON |
| REQ-1-07 · markers | markers with their origin (test / class / module) | **absent** |
| REQ-1-13 · catalogue retention | a test catalogue with a last-observed timestamp | **absent** |
| REQ-1-09 · decomposed identity | file path, class name, function name, parameter id | **partial** — no class name column |

REQ-1-08's acceptance criterion is decisive here: *twenty tests failing at the
same source line are returned as one group when queried by failure path and
line.* A JSON blob cannot serve that query. The same applies to REQ-1-10, whose
whole point — identifying the commit where a test started failing — is the
question the product exists to answer.

**Consequence:** the plugin's schema must be derived from the Phase 1
requirements, not ported from `tables.py`. `tables.py` remains useful as a naming
and typing reference, and as the schema the relocated server package keeps for
its own (later-phase) concerns.

## 4. Target layout

uv workspace, one root lockfile, packages published independently. This matches
the recorded packaging decision and the existing `uv.lock`.

```
vantage/
├── pyproject.toml                 # workspace root: [tool.uv.workspace] members = ["packages/*"]
├── packages/
│   └── pytest-vantage/
│       ├── pyproject.toml         # requires-python ">=3.10,<3.14"; dependencies = []
│       │                          # [project.entry-points.pytest11] vantage = "pytest_vantage.plugin"
│       ├── src/pytest_vantage/
│       │   ├── plugin.py          # pytest_addoption + pytest_configure only — always loaded, inert
│       │   ├── recorder.py        # registered only when recording is enabled; hooks inside the error boundary
│       │   ├── core/              # empty at Milestone 1 — the architecture test's target
│       │   └── storage/
│       │       ├── schema.py      # full DDL, created at first use (NFR-1-06)
│       │       └── sqlite_writer.py
│       └── tests/
│           ├── test_architecture.py
│           ├── test_opt_in.py
│           ├── test_write_one_row.py
│           └── test_non_disruptive_failure.py
└── (relocated server package — see open question O-1)
```

`packages/pytest-strategies/` and `packages/pytest-verify/` are named in the
project plan but are out of scope for Milestone 1.

## 5. Entry point and opt-in (CON-05 + REQ-1-02)

These two requirements pull in opposite directions: the plugin must load on every
pytest session without the user editing a conftest, yet must create and modify
nothing when the recording option is absent.

The resolution is to split the module that pytest always imports from the object
that carries the recording hooks. `plugin.py` defines only `pytest_addoption` and
`pytest_configure`. Neither touches disk. `pytest_configure` reads the recording
option and, only when it is set, instantiates and registers a separate recorder
object via `config.pluginmanager.register(...)`. Every hook that opens the
database lives on that object.

This matters because pytest fires any `pytest_*` hook implementation it finds on a
registered plugin. Leaving a `pytest_sessionfinish` at module level in an
always-loaded plugin means it runs on every session in the world, and REQ-1-02
dies quietly.

## 6. Non-disruptive failure (REQ-1-21)

The error boundary belongs around the recorder object's own hook implementations:
catch `Exception`, emit one warning, return normally. Never re-raise, never touch
`session.exitstatus`.

`pytest_runtest_logreport` is the critical one. It is not a `firstresult` hook,
and an uncaught exception inside it becomes pytest's own INTERNALERROR with exit
status 3 — the exact failure mode REQ-1-21 exists to prevent.

## 7. Architecture test (NFR-1-03)

Three candidates were compared for enforcing that the core imports no pytest,
database or web-framework module:

| Approach | Coverage | Cost | Verdict |
| --- | --- | --- | --- |
| Static AST analysis of the core package's imports (stdlib `ast`) | every module, whether or not it is exercised | low | **recommended** |
| Import-time guard | only paths actually imported at runtime | low | insufficient — the requirement is about the import graph, not one execution |
| Third-party lint rule | full, but a separate CI step | extra dev dependency | acceptable as tooling, but it is not the green pytest test the milestone asks for |

Static AST analysis wins because the requirement is stated as a static property
(*"when its imports are analysed statically"*) and because it needs no dependency
of its own. Only the runtime import graph is constrained — dev and test tooling
may use third-party packages freely.

## 8. Git history — SUPERSEDED

This section recommended relocating the server code with `git mv` so the
freeze-0001 and migration-0002 history survived, and relocating the existing
tests alongside it.

**The user has since ruled that `src/vantage/**` and `tests/**` are deleted
outright.** They were written for a different product and were already out of
date, and preserving Python 3.10 compatibility (NFR-1-04) is worth more than
salvaging them — the alternative was desugaring `StrEnum` and `datetime.UTC`
across roughly twelve sites in code destined to be rewritten. Every test imports
`vantage.*`, so none survives the deletion.

Git history retains the deleted tree. The deletion lands in its own commit at the
head of the first slice. See `proposal.md` §*Clean slate*.

## 9. Open questions for the proposal

- **O-1 · Where does the server code live. — RESOLVED.** The server ships inside
  the `pytest-vantage` distribution as its own subpackage, with its dependencies
  declared in the `[server]` optional dependency group. This keeps the documented
  install shape (`pip install pytest-vantage[server]`) and adds no fourth package
  to the plan. The boundary is enforced by the architecture test rather than by a
  distribution boundary: the plugin's runtime import graph may not reach the
  server subpackage, and a clean-environment install check in CI proves the
  server's dependencies stay behind the extra.
- **O-2 · Which Phase 2 columns.** NFR-1-06 requires the schema to be complete at
  first use, including columns no code populates yet. "Phase 2" means the Notion
  Phase 2 scope — structured logs, captured output and fixtures, artefacts by
  content hash, stable identity, flakiness — not the old plan's slices.
- **O-3 · Schema drift.** Once the plugin owns a hand-written DDL and the server
  keeps a SQLAlchemy schema, the two can diverge. A parity test is the cheap
  mitigation, but only once both describe the same tables.

## 10. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Treating `tables.py` as the Phase 1 schema | Ships an MVP that cannot answer its own acceptance question — no git context, no queryable failure location | Derive the schema from the Phase 1 requirements; see §3 |
| `requires-python = ">=3.11"` | Violates NFR-1-04 (3.10–3.13) | Settled by the baseline ruling: drop the floor to 3.10 and matrix CI 3.10–3.13, with and without pytest-xdist |
| Schema duplication between plugin and server | Silent divergence | O-3 |
| Server dependencies leaking into the plugin | Violates NFR-1-01, the requirement most likely to decide adoption | Architecture test plus a clean-environment install check in CI |

## 11. Ready for proposal

Yes, once O-1 is answered. O-2 and O-3 can be settled during design.
