# Tasks: Milestone 1 — Write one row

Test command: `uv run --extra dev pytest`. Strict TDD: every behaviour task is RED (failing test) then GREEN (make it pass), test first. Every verifying test carries `@pytest.mark.req("RQ-xx")`; non-test verification (RQ-24, RQ-27, RQ-28, RQ-29) names the ID in a comment on the block that proves it.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | A1 **359 actual** (+ 2,572 deleted, own commit) · A1-ADR **337 actual** · A2 ≈150 · B ≈380 · C ≈300 · C2 ≈290 |
| 400-line budget risk | A1: none (measured) · A1-ADR: none (measured) · A2: Low · B: Low-Medium · C: Low · C2: Low |
| Chained PRs recommended | Yes |
| Suggested split | tracker → A1 → A1-ADR → A2 → B → C → C2 |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Low-Medium

**Slice A was split into A1 and A2.** The original single-A forecast (≈480) was flagged Medium against the
400 budget, which under `ask-on-risk` requires asking regardless of the risk label — the question was
raised and the user chose the split, on the ground that A is where the CI lives, and that CI is the
mechanism proving RQ-24, RQ-27 and RQ-28 (zero runtime dependencies, the 3.10–3.13 × xdist matrix, offline
operation) — three Must-Haves, not skimmable boilerplate. Splitting keeps each PR's diff scoped to one
concern a reviewer can hold at once: A1 is the tree existing (workspace, packages, docs); A2 is the gates
that run against it. Re-forecast per-file: **A1** ≈365 lines (workspace root `pyproject.toml` ≈80, req-marker
guard test ≈30, four package `pyproject.toml` incl. the `vantage-pytest` entry-point declaration ≈65,
`README.md` ≈40, `docs/architecture.md` ≈50, `docs/adr/0003…0006` ≈70, `docs/adr/0007` ≈30). **A2** ≈150
lines (`.pre-commit-config.yaml` ≈35, `.github/workflows/ci.yml` ≈90, `.github/workflows/audit.yml` ≈25).
Both land comfortably under 400 — neither needs a further split.

**Slice C stays split into C and C2** (decided in the prior forecast): the original single-C scope
(entry point, plugin, recorder, boundary, and all of RQ-1/RQ-2/RQ-21/RQ-30/RQ-31/RQ-37/RQ-38's tests)
lands ≈560–580 authored lines, driven by six `pytester`/subprocess-based test files. C ≈300 (happy path);
C2 ≈290 (both failure paths + concurrency). Both under 400.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| A1 | Delete stale tree; workspace root, 4 package skeletons + entry point, `specs/`, README/architecture docs, ADRs | PR A1 → tracker | `uv run --extra dev pytest` (collects nothing yet) | N/A — no runnable process yet; `pytest --collect-only` confirms `--strict-markers` doesn't fail | `git revert` restores `src/vantage`/`tests` and every config/doc file intact |
| A1-ADR | The five ADRs, one commit each | PR A1-ADR → A1 | N/A — prose | N/A | Deleting `docs/adr/` leaves A1's tree untouched |
| A2 | Quality gates: pre-commit, CI matrix + offline job + clean-env install, weekly audit | PR A2 → A1-ADR | N/A — CI executes remotely; `yamllint .github/workflows/*.yml` locally | `pre-commit run --all-files`; CI dry-run via `act` or a draft-PR trigger | Removing the two workflow files and `.pre-commit-config.yaml` reverts to no gates, no effect on A1's tree |
| B | Core port + option resolution, architecture test, schema + manifest, in-memory + sqlite adapters | PR B → A2 | `uv run --extra dev pytest packages/vantage-core packages/vantage-storage` | N/A — no process to launch yet; contract suite is the harness | New files under `vantage-core`/`vantage-storage` only, no plugin wiring yet |
| C | Inert `plugin.py`, `recorder.py`, RQ-1/RQ-2/RQ-30/RQ-31 (entry point already declared in A1) | PR C → B | `uv run --extra dev pytest packages/vantage-pytest -k "opt_in or run_entry"` | `pytester.runpytest_subprocess` scenarios in `test_run_entry.py`/`test_opt_in.py` | New files under `vantage-pytest`; entry point already exists from A1 and stays inert until `plugin.py` reads it |
| C2 | `boundary.py`, RQ-21, RQ-37, RQ-38 (criterion 1) | PR C2 → C | `uv run --extra dev pytest packages/vantage-pytest -k "failure_paths or concurrency"` | two-process `subprocess` harness in `test_concurrency.py`; fault-injection harness in `test_failure_paths.py` | `boundary.py` + failure/concurrency tests removable without touching the C happy path |

---

## Phase A1: Clean slate and workspace

- [x] A1.1 Delete `src/vantage/**` (19 files) and `tests/**` (10 files) in one standalone commit, head of slice A1, nothing else in it.
- [x] A1.2 Rewrite root `pyproject.toml`: uv workspace root, shared `ruff`/`mypy --strict`/`deptry` config, the **only** `[tool.pytest.ini_options]` (`markers = ["req(id): ..."]`, `--strict-markers`, `pythonpath`).
- [x] A1.3 Create `packages/vantage-core/pyproject.toml` (`dependencies = []`, `requires-python = ">=3.10,<3.14"`), `vantage-storage/pyproject.toml` (depends on core only), `vantage-pytest/pyproject.toml` **including** `[project.entry-points.pytest11] vantage = "vantage_pytest.plugin"`, `vantage-service/pyproject.toml` (skeleton) + `specs/.gitkeep`.
- [x] A1.4 RED: guard test scanning each package `pyproject.toml` for the literal string `[tool.pytest.ini_options]`, failing if found (D9). **It must also assert it found and read four files** — without that it passes having scanned nothing, which is the same vacuity failure `test_core_package_is_not_empty` exists to prevent in B.1. Make it RED first by temporarily adding the section to one package file.
- [x] A1.5 GREEN: remove the temporary section; the guard passes over four genuinely scanned files.
- [x] A1.6 Rewrite `README.md` and `docs/architecture.md` to describe the four-package clean-architecture layout, not the deleted live-run-supervision plan.
## Phase A1-ADR: Architecture decision records

*Split out of A1 after measurement. A1 with the ADRs came to 696 authored lines against a
400 budget; without them it is 359, dead on the original forecast, and the ADRs are 337.
The forecast bucket for them (~70 lines for four) was the defect — it did not account for
CLAUDE.md's own rule of three negative consequences minimum per ADR, plus MADR options and
pros-and-cons for 0004. That convention produces roughly 67 lines per ADR.*

*Reviewing five architecture decisions is also a different activity from reviewing a
package skeleton, and the ADR template's own rule is "one ADR, one PR" — so one commit per
ADR inside one PR is the closest reachable shape while they describe decisions implemented
across later slices.*

- [x] ADR.1 `docs/adr/0003-use-clean-architecture-rather-than-hexagonal.md` — binds RQ-26, RQ-30. Nygard.
- [x] ADR.2 `docs/adr/0004-split-the-monorepo-into-four-packages.md` — binds RQ-24, RQ-26, RQ-30. MADR, three real options.
- [x] ADR.3 `docs/adr/0005-complete-schema-at-first-use-no-migrations-in-phase-1.md` — binds RQ-29. Nygard.
- [x] ADR.4 `docs/adr/0006-use-stdlib-sqlite3-and-no-orm.md` — binds RQ-24, RQ-28. Nygard.
- [x] ADR.5 `docs/adr/0007-store-the-database-under-the-project-root.md` — binds RQ-2, RQ-10. Nygard.

*All five `Status: Proposed`. Filenames are fixed: the Notion rows' `Repo path` fields
already point at these exact paths, so renaming one silently breaks the mirror.*

## Phase A2: Quality gates

*Depends on A1-ADR, which depends on A1 — the workflows below reference package paths
(`packages/vantage-core`, `packages/vantage-storage`, `packages/vantage-pytest`,
`packages/vantage-service`) that A1 creates.*

- [ ] A2.1 Create `.pre-commit-config.yaml`: pre-commit stage (`ruff format`, `ruff check --fix`, hygiene hooks, modified files only) and pre-push stage (`mypy --strict`, fast unit tests) per CLAUDE.md's gate table.
- [ ] A2.2 Create `.github/workflows/ci.yml` with three requirement-bearing jobs, each carrying its ID in a comment since these are Inspection/comment-verified, not marked tests: 3.10–3.13 × {with, without} xdist matrix (comment `# RQ-27`); networking-disabled job (comment `# RQ-28`); clean-environment install check asserting every added distribution is Vantage's own (comment `# RQ-24`). Plus verification-mode ruff, `mypy --strict`, `deptry`, `pip-audit`, and the package build steps.
- [ ] A2.3 Create `.github/workflows/audit.yml`: weekly scheduled `pip-audit`.

## Phase B: Core, boundary, schema

- [ ] B.1 RED: `packages/vantage-core/tests/test_architecture.py` + `importwalk.py` — static `ast` walk asserting every import in `vantage_core` resolves to stdlib or `vantage_core`; `test_core_package_is_not_empty` (≥3 modules visited, ≥1 import, `ports/storage.py` and `config/resolution.py` visited). Written before any core module exists (RQ-26).
- [ ] B.2 GREEN: create `vantage_core/domain/execution.py` (`Execution` — not `TestExecution`), `vantage_core/domain/text.py` (`MAX_TEXT_BYTES`, `TRUNCATION_MARKER`), `vantage_core/ports/storage.py` (`ExecutionStore` Protocol), `vantage_core/config/resolution.py` stubs — walk turns green.
- [ ] B.3 RED: `packages/vantage-core/tests/test_resolution.py` — precedence table (cli > env > ini > default), `~` expansion, relative-path-anchors-on-rootdir-not-cwd, empty-string-as-absent, `is_activated` truth table. No pytest session involved.
- [ ] B.4 GREEN: implement `resolve_database_path`, `is_activated`, `DatabaseLocation`, `PathSource(str, Enum)` (not `StrEnum` — 3.10 floor) in `config/resolution.py`.
- [ ] B.5 Create `packages/vantage-storage/src/vantage_storage/schema.sql` (ten tables, twelve indexes, per the design's column manifest) and `docs/schema-manifest.md` (the RQ-29 inspection artifact, same content in prose form).
- [ ] B.6 RQ-29 Inspection task (not a test): create a fresh database from `schema.sql`, run `PRAGMA table_info` per table, and record the comparison against `docs/schema-manifest.md` confirming every documented column exists — comment `# RQ-29` on the manifest header.
- [ ] B.7 RED: `packages/vantage-core/tests/vantage_port_contract.py` — `ExecutionStoreContract` exercising `start_execution`/`finish_execution`/`close` purely through the `ExecutionStore` Protocol, plus `test_memory_store.py`/`test_sqlite_store.py` stub subclasses with a `store` fixture, before either adapter exists (RQ-30).
- [ ] B.8 GREEN: implement `vantage_storage/memory.py` (`InMemoryExecutionStore`) — contract suite green against it.
- [ ] B.9 GREEN: implement `vantage_storage/connection.py` (mkdir, `.gitignore` via `open(path, "x")`, PRAGMAs, WAL-with-fallback, `BEGIN IMMEDIATE` DDL from `schema.sql`) and `vantage_storage/sqlite_store.py` (`SqliteExecutionStore`) — contract suite green against it too.
- [ ] B.10 RED+GREEN: `test_schema_manifest.py` — mechanised `PRAGMA table_info` ↔ manifest rot-detector (comment `# RQ-29`, supporting the Inspection task in B.6, not the record of verification itself).

## Phase C: Plugin, opt-in, one row (happy path)

*The `pytest11` entry point was already declared in A1.5 — this phase only writes the module it points to.*

- [ ] C.1 RED: `packages/vantage-pytest/tests/conftest.py` (`pytest_plugins = ["pytester"]`) + `test_opt_in.py` — differential tree-hash comparison (bare run vs. `-p no:vantage`) and no-database-file assertion (`@pytest.mark.req("RQ-2")`).
- [ ] C.2 GREEN: create `vantage_pytest/plugin.py` — **only** `pytest_addoption` (`--vantage`, `--vantage-db`, `vantage_db` ini key) and `pytest_configure` (calls `core.is_activated`; returns if `False`); opt-in tests green.
- [ ] C.3 RED: `test_run_entry.py` — first invocation, second invocation (distinct id), zero-test collection, failed-collection, completed-session timestamps, interrupted-session (SIGINT) null end time; fold in an "every field present" assertion for RQ-3 (`@pytest.mark.req("RQ-1")`, `@pytest.mark.req("RQ-31")`).
- [ ] C.4 GREEN: create `vantage_pytest/recorder.py` (`Recorder` with `pytest_sessionstart` INSERT, `pytest_sessionfinish` writing `finished_at` iff `exitstatus not in {2, 3}` (D3), `pytest_report_header`, `pytest_unconfigure`); wire `plugin.pytest_configure` to open the database (via `vantage_storage.connection`) and `pluginmanager.register(recorder, name="vantage-recorder")` on activation only. `test_run_entry.py` and `test_opt_in.py` both green.
- [ ] C.5 Confirm the RQ-30 contract suite (B.7) still passes with the sqlite adapter now reachable through the plugin path — no new test, cross-check only.

## Phase C2: Fault tolerance and concurrency

- [ ] C2.1 RED: `test_failure_paths.py` — RQ-21 trio: patched-to-raise store on a passing suite (exit 0, one warning), on a failing suite (exit 1, one warning), and on every recorder hook (exit 0, no internal error) (`@pytest.mark.req("RQ-21")`). Threat matrix: Process integration.
- [ ] C2.2 GREEN: create `vantage_pytest/boundary.py` (decorator catching `Exception` only, never `BaseException`; latches `self._disabled` on first failure; `_warn(config, message)` emitting `VantageWarning(UserWarning)` with terminal-reporter/`stderr` fallback); wrap every `Recorder` hook. RQ-21 tests green.
- [ ] C2.3 RED: `test_failure_paths.py` — RQ-37 trio: read-only directory, missing directory, corrupt file, each asserting exit 0 and one warning naming the path (`@pytest.mark.req("RQ-37")`). Threat matrix: Path authority.
- [ ] C2.4 GREEN: wrap the full resolve → mkdir → connect → PRAGMAs → `schema.sql` → construct-recorder sequence in `plugin.pytest_configure` in one `try/except Exception`; on failure, warn naming the path, do not register, return. RQ-37 tests green.
- [ ] C2.5 RED: `test_resolution.py` addition — relative-path-anchors-on-rootdir test, if not already covered by B.3 (Path authority coverage check).
- [ ] C2.6 RED: `test_concurrency.py` — two `subprocess`-launched pytest sessions started within the same second against one database, asserting two run entries (`@pytest.mark.req("RQ-38")`, criterion 1 only — no 400-result criterion). Threat matrix: Concurrent writers.
- [ ] C2.7 RED: a lock-timeout test asserting degradation into the RQ-37 path when `database is locked` survives the 5 s busy timeout during `pytest_configure` (threat matrix's second "Concurrent writers" RED test).
- [ ] C2.8 GREEN: implement D8 in `vantage_storage/connection.py` — `PRAGMA journal_mode=WAL` with fallback to `delete` mode (no warning) if it fails or reads back non-`wal`; `sqlite3.connect(path, timeout=5.0)`; `isolation_level=None` + explicit `BEGIN IMMEDIATE`; `uuid4().hex` identity; every DDL statement `IF NOT EXISTS` inside one transaction. C2.6 and C2.7 both green.

---

## Traceability

`grep -r "RQ-xx"` must reach the proving artifact for every requirement in scope: RQ-1/RQ-31 → C.3; RQ-2 → C.1; RQ-3 → folded into C.3; RQ-21 → C2.1; RQ-24 → A2.2 comment + clean-env CI job; RQ-26 → B.1; RQ-27 → A2.2 comment; RQ-28 → A2.2 comment; RQ-29 → B.6 comment (Inspection) + B.10 (rot-detector); RQ-30 → B.7; RQ-37 → C2.3; RQ-38 (criterion 1 only) → C2.6.
