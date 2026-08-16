# 11. Serve the ingestion API with FastAPI on uvicorn

Date: 2026-08-15

## Status

Proposed

## Context

ADR-9 decided the plugin reports over HTTP and the server performs every
write; ADR-4 decided that boundary is what `vantage` publishes. Neither
picked what serves it, and ADR-9 already flagged the risk: a framework
chosen in Milestone 1 for one write endpoint has to still be right in
Milestone 5, when the read API and the browser interface (ADR-8) sit on the
same server — reversing that late is well past a sprint. `vantage.service`
is the one package RQ-24 does not constrain (ADR-4), so a dependency here
costs nothing the plugin or the core pay for. Concrete requirements now:
RQ-41's versioned-path 404, RQ-42's structured rejection body without a
hand-rolled router, and RQ-38's concurrent sessions, ruling out a
single-threaded blocking server.

## Decision

`vantage` serves `POST /api/v1/runs` with FastAPI on uvicorn. FastAPI's
request model already speaks Pydantic v2 — the project's own
boundary-validation standard (CLAUDE.md) — so the ingestion schema, the
`/api/v1` prefix, the 422 path and OpenAPI generation come from one
dependency instead of being wired by hand. Uvicorn gives ASGI concurrency,
which the later live-monitoring milestone needs a streaming transport for
regardless.

`fastapi` and `uvicorn` become runtime dependencies of `vantage` only.
`pytest-vantage` gains nothing and cannot — RQ-24 still holds it to stdlib
and `pytest` alone, because it never imports `vantage.service`.

## Consequences

- The framework choice lands three milestones before the read API needs it,
  paid once rather than migrated later under a live schema.
- `vantage`'s dependency footprint grows by two packages plus their own
  transitive ones on `pip install vantage`; RQ-24 does not apply here, but
  the install is no longer zero-dependency in fact.
- FastAPI's default 422 body has to be actively overridden — `errors.py`
  exists because it leaks input values RQ-42.4 forbids exposing — and
  OpenAPI generation is free but unreviewed against `docs/api/v1-ingestion.md`.

## Alternatives rejected

**Starlette alone.** One fewer layer. Rejected — hand-wiring Pydantic for
the surface FastAPI already provides, saving no dependency, since uvicorn
is needed either way.

**Flask.** Familiar. Rejected — WSGI, not ASGI, so live monitoring would
need a second server rather than reusing this one.

**stdlib `http.server`.** Zero dependencies. Rejected — hand-rolled routing,
no production concurrency story, against a package RQ-24 does not
constrain: paying nothing here buys nothing.
