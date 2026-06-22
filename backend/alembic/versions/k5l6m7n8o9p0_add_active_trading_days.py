"""add active_trading_days to trader_stats

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k5l6m7n8o9p0"
down_revision: str | None = "j4k5l6m7n8o9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trader_stats",
        sa.Column("active_trading_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trader_stats", "active_trading_days")
