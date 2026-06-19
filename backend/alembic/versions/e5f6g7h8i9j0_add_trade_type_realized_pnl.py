"""add trade_type and realized_pnl to user_trades

Revision ID: e5f6g7h8i9j0
Revises: d1e2f3a4b5c6
Create Date: 2026-06-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6g7h8i9j0"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_trades",
        sa.Column("trade_type", sa.Text(), nullable=True),
    )
    op.add_column(
        "user_trades",
        sa.Column("realized_pnl", sa.Numeric(20, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_trades", "realized_pnl")
    op.drop_column("user_trades", "trade_type")
