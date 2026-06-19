"""add portfolio_stop_loss_pct to users

Revision ID: f0g1h2i3j4k5
Revises: e5f6g7h8i9j0
Create Date: 2026-06-16 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f0g1h2i3j4k5"
down_revision: str | None = "e5f6g7h8i9j0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "portfolio_stop_loss_pct",
            sa.Numeric(5, 2),
            nullable=True,
            server_default="20.0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "portfolio_stop_loss_pct")
