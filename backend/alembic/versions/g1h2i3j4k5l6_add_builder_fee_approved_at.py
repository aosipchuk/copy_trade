"""add builder_fee_approved_at

Revision ID: g1h2i3j4k5l6
Revises: f0g1h2i3j4k5
Create Date: 2026-06-16 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "g1h2i3j4k5l6"
down_revision: str | None = "f0g1h2i3j4k5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("builder_fee_approved_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "builder_fee_approved_at")
