"""add sharpe and sortino ratio to trader_stats

Revision ID: d1e2f3a4b5c6
Revises: c3d4e5f6a7b8
Create Date: 2026-06-16 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trader_stats",
        sa.Column("sharpe_ratio", sa.Numeric(10, 4), nullable=True),
    )
    op.add_column(
        "trader_stats",
        sa.Column("sortino_ratio", sa.Numeric(10, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trader_stats", "sortino_ratio")
    op.drop_column("trader_stats", "sharpe_ratio")
