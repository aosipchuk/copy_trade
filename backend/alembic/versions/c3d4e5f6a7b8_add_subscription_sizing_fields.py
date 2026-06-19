"""add subscription sizing fields

Revision ID: c3d4e5f6a7b8
Revises: a9c1d5e78f02
Create Date: 2026-06-15 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "a9c1d5e78f02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column(
            "sizing_mode",
            sa.Text(),
            nullable=False,
            server_default="fixed_ratio",
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column("max_per_coin_usd", sa.Numeric(20, 2), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("allowed_coins", sa.ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "allowed_coins")
    op.drop_column("subscriptions", "max_per_coin_usd")
    op.drop_column("subscriptions", "sizing_mode")
