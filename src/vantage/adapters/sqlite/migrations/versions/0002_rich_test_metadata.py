"""add rich test metadata columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07

Additive delta on top of the frozen `0001` baseline (see that revision's
docstring for the freeze rationale). Adds nullable run-level selection
metadata columns (`seed`, `seed_source`, `marker_expr`, `keyword_expr`) and
nullable test-level structured-identity/parameter columns
(`base_test_id`, `relpath`, `lineno`, `originalname`, `parameters`,
`fixture_names`) plus the `ix_test_results_base_test_id` index (spec
Domain 3 -- Additive-Only Migration). No existing column is altered or
dropped; pre-existing rows land with NULL for every new column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("seed", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("seed_source", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("marker_expr", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("keyword_expr", sa.Text(), nullable=True))

    op.add_column("test_results", sa.Column("base_test_id", sa.String(), nullable=True))
    op.add_column("test_results", sa.Column("relpath", sa.Text(), nullable=True))
    op.add_column("test_results", sa.Column("lineno", sa.Integer(), nullable=True))
    op.add_column("test_results", sa.Column("originalname", sa.Text(), nullable=True))
    op.add_column("test_results", sa.Column("parameters", sa.JSON(), nullable=True))
    op.add_column("test_results", sa.Column("fixture_names", sa.JSON(), nullable=True))

    op.create_index("ix_test_results_base_test_id", "test_results", ["base_test_id"])


def downgrade() -> None:
    op.drop_index("ix_test_results_base_test_id", table_name="test_results")

    with op.batch_alter_table("test_results") as batch_op:
        batch_op.drop_column("fixture_names")
        batch_op.drop_column("parameters")
        batch_op.drop_column("originalname")
        batch_op.drop_column("lineno")
        batch_op.drop_column("relpath")
        batch_op.drop_column("base_test_id")

    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("keyword_expr")
        batch_op.drop_column("marker_expr")
        batch_op.drop_column("seed_source")
        batch_op.drop_column("seed")
