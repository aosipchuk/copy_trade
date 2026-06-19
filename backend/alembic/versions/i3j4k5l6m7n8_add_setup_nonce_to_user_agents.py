"""add setup_nonce to user_agents for nonce pinning on wallet approval

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-06-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "i3j4k5l6m7n8"
down_revision: str | None = "h2i3j4k5l6m7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_agents",
        sa.Column("setup_nonce", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_agents", "setup_nonce")
