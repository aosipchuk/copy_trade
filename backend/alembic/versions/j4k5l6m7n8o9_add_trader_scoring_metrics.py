"""add trader scoring metrics to trader_stats

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-06-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "j4k5l6m7n8o9"
down_revision: str | None = "i3j4k5l6m7n8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trader_stats",
        sa.Column("profit_factor", sa.Numeric(10, 4), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("avg_pnl_per_trade", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("max_losing_streak", sa.Integer(), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("profitable_days_pct", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("avg_trades_per_day", sa.Numeric(8, 4), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("daily_pnl_std_dev", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("long_ratio_pct", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("avg_position_size_usd", sa.Numeric(20, 2), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("fees_paid_usd", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("calmar_ratio", sa.Numeric(10, 4), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("composite_score", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("max_drawdown_duration_days", sa.Numeric(8, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trader_stats", "max_drawdown_duration_days")
    op.drop_column("trader_stats", "composite_score")
    op.drop_column("trader_stats", "calmar_ratio")
    op.drop_column("trader_stats", "fees_paid_usd")
    op.drop_column("trader_stats", "avg_position_size_usd")
    op.drop_column("trader_stats", "long_ratio_pct")
    op.drop_column("trader_stats", "daily_pnl_std_dev")
    op.drop_column("trader_stats", "avg_trades_per_day")
    op.drop_column("trader_stats", "profitable_days_pct")
    op.drop_column("trader_stats", "max_losing_streak")
    op.drop_column("trader_stats", "avg_pnl_per_trade")
    op.drop_column("trader_stats", "profit_factor")
