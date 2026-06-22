"""add avg_leverage to trader_stats

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "l6m7n8o9p0q1"
down_revision: str | None = "k5l6m7n8o9p0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trader_stats",
        sa.Column("avg_leverage", sa.Numeric(6, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trader_stats", "avg_leverage")
