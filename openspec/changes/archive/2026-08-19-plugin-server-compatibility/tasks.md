# Tasks: Plugin/server compatibility

**Change:** `plugin-server-compatibility` · Strict TDD — every behavioural task
names its failing test first. Test command: `uv run --extra dev pytest`.

One slice, ~250 lines against the 500 budget. Baseline is **255 passed, zero
warnings**.

## Design decisions, settled here

**D38 — The server advertises one capability, not a version number.**
`GET /api/v1/capabilities` answers `{"session_lifecycle": true}`. Not a semver
string: there is exactly one thing a client needs to know today, and inventing a
version scheme before there is a second capability to name would be guessing at
a shape nothing yet constrains. A second capability adds a key, which an older
client ignores.

**D39 — An older server's `404` is the answer, not a failure.** Nothing has to
change on the old side, which is the only reason this can work at all: the
servers that need detecting are already published. A `404` means "predates the
lifecycle" and is handled, not warned about as a transport error.

**D40 — It fails closed.** Only an explicit positive answer enables the
lifecycle. Unreachable, malformed, non-JSON, wrong type, timed out — all mean
degrade. A capability check that fails open is worse than none, because it
promises a guarantee it does not hold.

**D41 — Degrading means the previous release, not a new half-state.** No
start-write, no beats, and `pytest_sessionfinish` sends exactly what it sent
before the lifecycle existed. This mirrors `session-lifecycle`'s own rule for a
failed start-write, and it is what makes degrading a decision rather than an
accident.

**D42 — Bounded by the liveness timeout.** The capability request uses
`resolve_liveness_timeout`, never the report timeout. A preflight that blocks
for the report timeout puts that cost in front of every session.

## Phase 1: Capability advertisement and the plugin's refusal to half-record

- [x] 1.1 RED `packages/vantage/tests/test_ingestion.py`: `GET
      /api/v1/capabilities` answers `200 {"session_lifecycle": true}`.
- [x] 1.2 RED, same file: it is mounted under `/api/v1` and nowhere else —
      `GET /capabilities` is `404`, matching the absence rule `app.py`'s
      docstring already states for the run routes.
- [x] 1.3 RED `packages/pytest-vantage/tests/test_failure_paths.py`: a server
      that answers the capability probe `404` (an older `vantage`) records the
      session with **no start-write and no heartbeat**, and its finish report
      is byte-identical to what the plugin sent before the lifecycle existed.
      Assert the request count, not just the outcome — the point is that
      nothing extra was sent.
- [x] 1.4 RED, same file: that degradation warns **once**, on the liveness
      latch, naming the address. It must not disable result recording.
- [x] 1.5 RED, same file, **the fail-closed table**: a capability response that
      is malformed JSON, valid JSON of the wrong type, `{"session_lifecycle":
      false}`, an empty body, a `500`, or a connection that hangs past the
      liveness timeout — **every one degrades**. Table-driven; each row is a way
      a check could quietly fail open.
- [x] 1.6 RED: the capability probe is bounded by `resolve_liveness_timeout`,
      not the report timeout, when the two differ (D42).
- [x] 1.7 GREEN `packages/vantage/src/vantage/service/routes/capabilities.py`
      (new) and `app.py`: the route and its mount.
- [x] 1.8 GREEN `packages/pytest-vantage/src/pytest_vantage/transport.py`:
      `fetch_capabilities(address, *, timeout) -> bool` — returns True **only**
      for an explicit positive answer. Every other outcome is False, including
      every exception it can raise; this function does not propagate.
- [x] 1.9 GREEN `packages/pytest-vantage/src/pytest_vantage/plugin.py`: call it
      after `_preflight_reachable` succeeds, and pass the result to `Recorder`.
      **Correct `_preflight_reachable`'s docstring** — it claims the preflight
      sends no bytes, and after this change that is no longer true of the
      preflight as a whole.
- [x] 1.10 GREEN `packages/pytest-vantage/src/pytest_vantage/recorder.py`:
      `pytest_sessionstart` and `_maybe_beat` both return immediately when the
      lifecycle is unavailable. `pytest_sessionfinish` is **unchanged** — that
      is what makes the degraded path the previous release's behaviour.
- [x] 1.11 Confirm, **by reading**, that the xdist controller guard in
      `plugin.py` still precedes every one of these calls, so no worker probes
      capabilities either. No edit expected; record what you saw.
- [x] 1.12 Prove each new test by **mutation**: make `fetch_capabilities`
      return True unconditionally, and confirm the fail-closed table goes red.
      A check that cannot fail closed is the defect this change exists to
      prevent, so a test that would not catch it is not worth having. Revert
      and prove the tree is clean.
- [x] 1.13 GREEN gate: `uv run --extra dev pytest` with **zero warnings**,
      `uv run mypy .`, `uv run ruff check .`, `uv run deptry .`. Confirm
      `pytest-vantage` still imports pytest and the standard library only.
