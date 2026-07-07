"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-07

Frozen baseline (design SS2 ADR -- rich-test-metadata). This revision used
to delegate `upgrade()`/`downgrade()` to `tables.metadata.create_all`/
`drop_all` against the *live* `tables.py` MetaData. That only works while
`0001` is the sole migration: once a second revision (`0002`) needs to
`op.add_column(...)` a column that has since been added to `tables.py`,
the live-metadata baseline would create it too, and `0002` would then
fail with a duplicate-column error.

Frozen here as explicit, static `op.create_table` calls reproducing the
schema exactly as it existed BEFORE the rich-test-metadata columns were
added to `tables.py` (behavior-identical to the prior `create_all`
delegation for every DB created at this revision). `downgrade()` mirrors
it with explicit `op.drop_table` calls in reverse-FK order. From this
point on, `tables.py` remains the schema authority for `create_all` and
the drift guard (`tests/integration/test_sqlite_alembic_migrations.py`),
but migrations are append-only explicit deltas on top of this frozen
baseline -- never re-derived from the live `MetaData` again.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("testpath", sa.Text(), nullable=False),
        sa.Column("user", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("invocation", sa.JSON(), nullable=False),
        sa.Column("env_snapshot", sa.JSON(), nullable=False),
        sa.Column("root_dir", sa.Text(), nullable=True),
        sa.Column("totals", sa.JSON(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "test_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("phases", sa.JSON(), nullable=False),
        sa.Column("longrepr", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "discovery",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("node_ids", sa.JSON(), nullable=False),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.run_id"), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("discovery")
    op.drop_table("artifacts")
    op.drop_table("test_results")
    op.drop_table("runs")
