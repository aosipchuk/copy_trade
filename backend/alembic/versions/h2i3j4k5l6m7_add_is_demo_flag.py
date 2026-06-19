"""add is_demo flag to subscriptions and user_trades

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-06-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h2i3j4k5l6m7"
down_revision: str | None = "g1h2i3j4k5l6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "user_trades",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "ix_subscriptions_user_id_is_demo",
        "subscriptions",
        ["user_id", "is_demo"],
    )
    op.create_index(
        "ix_user_trades_is_demo",
        "user_trades",
        ["is_demo"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_trades_is_demo", table_name="user_trades")
    op.drop_index("ix_subscriptions_user_id_is_demo", table_name="subscriptions")
    op.drop_column("user_trades", "is_demo")
    op.drop_column("subscriptions", "is_demo")
