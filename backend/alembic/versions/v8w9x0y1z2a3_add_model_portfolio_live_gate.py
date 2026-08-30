"""add model portfolio live activation gate

Revision ID: v8w9x0y1z2a3
Revises: u7v8w9x0y1z2
Create Date: 2026-08-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v8w9x0y1z2a3"
down_revision: str | None = "u7v8w9x0y1z2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_portfolios",
        sa.Column(
            "live_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE model_portfolios "
            "SET live_enabled = true "
            "WHERE slug = 'balanced'"
        )
    )


def downgrade() -> None:
    op.drop_column("model_portfolios", "live_enabled")
