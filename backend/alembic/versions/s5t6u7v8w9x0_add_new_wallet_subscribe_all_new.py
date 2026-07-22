"""add new wallet subscribe all new flag

Revision ID: s5t6u7v8w9x0
Revises: r4s5t6u7v8w9
Create Date: 2026-07-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "s5t6u7v8w9x0"
down_revision: str | None = "r4s5t6u7v8w9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_new_wallet_subscriptions",
        sa.Column(
            "subscribe_all_new",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("user_new_wallet_subscriptions", "subscribe_all_new")
