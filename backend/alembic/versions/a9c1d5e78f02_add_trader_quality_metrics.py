"""add trader quality metrics

Revision ID: a9c1d5e78f02
Revises: 8eae8e13c6a0
Create Date: 2026-06-15 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c1d5e78f02"
down_revision: str | None = "8eae8e13c6a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trader_stats",
        sa.Column("win_rate_pct", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("max_drawdown_usd", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("max_drawdown_pct", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("trade_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("avg_trade_duration_hrs", sa.Numeric(8, 2), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("first_trade_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trader_stats", "first_trade_at")
    op.drop_column("trader_stats", "avg_trade_duration_hrs")
    op.drop_column("trader_stats", "trade_count")
    op.drop_column("trader_stats", "max_drawdown_pct")
    op.drop_column("trader_stats", "max_drawdown_usd")
    op.drop_column("trader_stats", "win_rate_pct")
