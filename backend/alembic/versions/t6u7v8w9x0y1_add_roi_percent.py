"""add canonical ROI percent shadow column

Revision ID: t6u7v8w9x0y1
Revises: s5t6u7v8w9x0
Create Date: 2026-08-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "t6u7v8w9x0y1"
down_revision: str | None = "s5t6u7v8w9x0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trader_stats",
        sa.Column("roi_percent", sa.Numeric(precision=20, scale=8), nullable=True),
    )
    op.execute(
        """
        UPDATE trader_stats
        SET roi_percent = roi_pct * 100
        WHERE roi_percent IS NULL AND roi_pct IS NOT NULL
        """
    )
    op.create_check_constraint(
        "ck_trader_stats_roi_percent_finite_range",
        "trader_stats",
        "roi_percent IS NULL OR ("
        "roi_percent::text NOT IN ('NaN', 'Infinity', '-Infinity') "
        "AND roi_percent BETWEEN -1000000 AND 1000000)",
    )


def downgrade() -> None:
    # Deliberately do not mutate legacy roi_pct. It remains a raw HL ratio.
    op.drop_constraint(
        "ck_trader_stats_roi_percent_finite_range",
        "trader_stats",
        type_="check",
    )
    op.drop_column("trader_stats", "roi_percent")
