"""add portfolio execution guards

Revision ID: q3r4s5t6u7v8
Revises: p2q3r4s5t6u7
Create Date: 2026-07-02 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "q3r4s5t6u7v8"
down_revision: str | None = "p2q3r4s5t6u7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TEMP TABLE tmp_duplicate_user_portfolio_subscriptions
        ON COMMIT DROP AS
        SELECT id
        FROM (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY user_id, portfolio_id, active_version_id, is_demo
                    ORDER BY
                        CASE
                            WHEN status IN ('active', 'trialing') THEN 0
                            ELSE 1
                        END,
                        created_at DESC,
                        id DESC
                ) AS rn
            FROM user_portfolio_subscriptions
            WHERE status <> 'canceled'
        ) ranked
        WHERE rn > 1
        """)
    op.execute("""
        UPDATE user_portfolio_items
        SET
            status = 'removed',
            removed_at = COALESCE(removed_at, timezone('utc', now()))
        WHERE status = 'active'
          AND user_portfolio_subscription_id IN (
              SELECT id FROM tmp_duplicate_user_portfolio_subscriptions
          )
        """)
    op.execute("""
        UPDATE subscriptions
        SET is_active = false
        WHERE is_active IS true
          AND source_type = 'model_portfolio'
          AND managed_by_portfolio IS true
          AND source_id IN (
              SELECT id FROM tmp_duplicate_user_portfolio_subscriptions
          )
        """)
    op.execute("""
        UPDATE user_portfolio_subscriptions
        SET
            status = 'canceled',
            canceled_at = COALESCE(canceled_at, timezone('utc', now()))
        WHERE id IN (
            SELECT id FROM tmp_duplicate_user_portfolio_subscriptions
        )
        """)
    op.add_column(
        "trader_stats",
        sa.Column(
            "daily_pnl_by_day",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "trader_stats",
        sa.Column(
            "daily_returns_pct_by_day",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_user_portfolio_subscriptions_active_version_mode",
        "user_portfolio_subscriptions",
        ["user_id", "portfolio_id", "active_version_id", "is_demo"],
        unique=True,
        postgresql_where=sa.text("status <> 'canceled'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_portfolio_subscriptions_active_version_mode",
        table_name="user_portfolio_subscriptions",
        postgresql_where=sa.text("status <> 'canceled'"),
    )
    op.drop_column("trader_stats", "daily_returns_pct_by_day")
    op.drop_column("trader_stats", "daily_pnl_by_day")
