"""add has_perp_activity to traders

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-06-25 20:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "n8o9p0q1r2s3"
down_revision: str | None = "m7n8o9p0q1r2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL = not yet evaluated (passes quality gates); False = no perp activity
    # (prediction-market / spot-only trader, not copyable → hidden from listing).
    op.add_column(
        "traders",
        sa.Column("has_perp_activity", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("traders", "has_perp_activity")
