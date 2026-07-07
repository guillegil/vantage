"""SQLAlchemy 2.0 Core table metadata -- the single schema authority
(design SS2). Alembic's initial migration (`migrations/versions/`) and
`metadata.create_all()` (used for test/in-memory DBs, design SS2) both
derive from this module, so they cannot drift.

`schedules` and `users` are deferred -- TODO: add when slice 5
(scheduling) and slice 7 (users & attribution) land (design SS2 handoff
notes).
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

runs = Table(
    "runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("state", String, nullable=False),
    Column("testpath", Text, nullable=False),
    Column("user", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("started_at", DateTime, nullable=True),
    Column("finished_at", DateTime, nullable=True),
    Column("exit_code", Integer, nullable=True),
    Column("stop_reason", Text, nullable=True),
    Column("invocation", JSON, nullable=False),
    Column("env_snapshot", JSON, nullable=False),
    Column("root_dir", Text, nullable=True),
    Column("totals", JSON, nullable=False),
    Column("last_heartbeat_at", DateTime, nullable=True),
)

test_results = Table(
    "test_results",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("node_id", Text, nullable=False),
    Column("outcome", String, nullable=False),
    Column("duration", Float, nullable=False),
    Column("phases", JSON, nullable=False),
    Column("longrepr", Text, nullable=True),
    Column("started_at", DateTime, nullable=True),
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("kind", String, nullable=False),
    Column("path", Text, nullable=False),
    Column("size", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
)

# Populated by the Vantage-owned discovery hook (design SS4, slice 4). Only
# the schema lands in this slice -- no `DiscoveryRepository` Protocol/impl
# yet (spec Domain 4 is surfaced but not consumed until slice 4).
discovery = Table(
    "discovery",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("node_ids", JSON, nullable=False),
    Column("collected_at", DateTime, nullable=False),
)

# Append-only; `seq` is the SSE poll cursor (design SS5).
events = Table(
    "events",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.run_id"), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("event_type", String, nullable=False),
    Column("timestamp", DateTime, nullable=False),
    Column("payload", JSON, nullable=False),
)
