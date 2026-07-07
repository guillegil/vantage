"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-07

Baseline revision -- delegates to `tables.py` (the schema authority,
design SS2) instead of hand-duplicating `op.create_table(...)` calls for
every column. This makes drift between this migration and
`metadata.create_all()` structurally impossible for the baseline; future
revisions add explicit `op.*` operations on top of this starting point.
"""

from __future__ import annotations

from alembic import op

from vantage.adapters.sqlite.tables import metadata

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    metadata.drop_all(bind=bind)
