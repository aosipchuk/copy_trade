"""add model portfolio tables

Revision ID: o1p2q3r4s5t6
Revises: n8o9p0q1r2s3
Create Date: 2026-07-02 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "o1p2q3r4s5t6"
down_revision: str | None = "n8o9p0q1r2s3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column(
            "source_type",
            sa.Text(),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column("source_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("source_version_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "managed_by_portfolio",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        "ck_subscriptions_source_type",
        "subscriptions",
        "source_type IN ('manual', 'model_portfolio')",
    )

    op.create_table(
        "model_portfolios",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("risk_profile", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("methodology_version", sa.Text(), nullable=False),
        sa.Column("rebalance_cadence", sa.Text(), nullable=False),
        sa.Column("min_equity_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("monthly_price_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("trial_days", sa.Integer(), server_default="7", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "risk_profile IN ('conservative', 'balanced', 'aggressive')",
            name="ck_model_portfolios_risk_profile",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'retired')",
            name="ck_model_portfolios_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "model_portfolio_versions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("portfolio_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=True),
        sa.Column("valid_to", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("approval_note", sa.Text(), nullable=True),
        sa.Column("selection_started_at", sa.DateTime(), nullable=True),
        sa.Column("selection_finished_at", sa.DateTime(), nullable=True),
        sa.Column("facts_hash", sa.Text(), nullable=True),
        sa.Column(
            "summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'retired', 'rejected')",
            name="ck_model_portfolio_versions_status",
        ),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["portfolio_id"], ["model_portfolios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "portfolio_id",
            "version_no",
            name="uq_model_portfolio_versions_portfolio_version",
        ),
    )
    op.create_index(
        "uq_model_portfolio_versions_current_published",
        "model_portfolio_versions",
        ["portfolio_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published' AND valid_to IS NULL"),
    )

    op.create_table(
        "model_portfolio_allocations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("trader_id", sa.BigInteger(), nullable=False),
        sa.Column("target_weight_pct", sa.Numeric(6, 3), nullable=False),
        sa.Column(
            "copy_ratio_pct", sa.Numeric(5, 2), server_default="100", nullable=False
        ),
        sa.Column(
            "max_leverage", sa.Numeric(5, 2), server_default="10", nullable=False
        ),
        sa.Column(
            "stop_loss_pct", sa.Numeric(5, 2), server_default="20", nullable=False
        ),
        sa.Column(
            "sizing_mode", sa.Text(), server_default="fixed_ratio", nullable=False
        ),
        sa.Column("max_per_coin_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("allowed_coins", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column(
            "score_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "constraint_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "copy_ratio_pct > 0 AND copy_ratio_pct <= 100",
            name="ck_model_portfolio_allocations_copy_ratio",
        ),
        sa.CheckConstraint(
            "target_weight_pct > 0 AND target_weight_pct <= 100",
            name="ck_model_portfolio_allocations_target_weight",
        ),
        sa.ForeignKeyConstraint(["trader_id"], ["traders.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["model_portfolio_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "version_id",
            "trader_id",
            name="uq_model_portfolio_allocations_version_trader",
        ),
    )

    op.create_table(
        "user_portfolio_subscriptions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("portfolio_id", sa.BigInteger(), nullable=False),
        sa.Column("active_version_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), server_default="trialing", nullable=False),
        sa.Column(
            "is_demo", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "auto_rebalance",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("total_allocation_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column(
            "close_removed_positions",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("billing_provider", sa.Text(), nullable=True),
        sa.Column("billing_customer_id", sa.Text(), nullable=True),
        sa.Column("billing_subscription_id", sa.Text(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('trialing', 'active', 'past_due', 'paused', 'canceled')",
            name="ck_user_portfolio_subscriptions_status",
        ),
        sa.ForeignKeyConstraint(["active_version_id"], ["model_portfolio_versions.id"]),
        sa.ForeignKeyConstraint(["portfolio_id"], ["model_portfolios.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_portfolio_items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_portfolio_subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("portfolio_version_id", sa.BigInteger(), nullable=False),
        sa.Column("allocation_id", sa.BigInteger(), nullable=False),
        sa.Column("trader_id", sa.BigInteger(), nullable=False),
        sa.Column("target_allocation_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("target_weight_pct", sa.Numeric(6, 3), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'removed', 'failed', 'paused')",
            name="ck_user_portfolio_items_status",
        ),
        sa.ForeignKeyConstraint(["allocation_id"], ["model_portfolio_allocations.id"]),
        sa.ForeignKeyConstraint(
            ["portfolio_version_id"], ["model_portfolio_versions.id"]
        ),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.ForeignKeyConstraint(["trader_id"], ["traders.id"]),
        sa.ForeignKeyConstraint(
            ["user_portfolio_subscription_id"], ["user_portfolio_subscriptions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_id",
            name="uq_user_portfolio_items_subscription",
        ),
        sa.UniqueConstraint(
            "user_portfolio_subscription_id",
            "allocation_id",
            name="uq_user_portfolio_items_subscription_allocation",
        ),
    )

    op.create_table(
        "portfolio_rebalance_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("portfolio_id", sa.BigInteger(), nullable=False),
        sa.Column("from_version_id", sa.BigInteger(), nullable=True),
        sa.Column("to_version_id", sa.BigInteger(), nullable=True),
        sa.Column("user_portfolio_subscription_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column(
            "diff_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "event_type IN ('scheduled', 'emergency', 'manual', 'user_apply')",
            name="ck_portfolio_rebalance_events_event_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending', 'running', 'completed', 'failed', "
            "'skipped')",
            name="ck_portfolio_rebalance_events_status",
        ),
        sa.ForeignKeyConstraint(["from_version_id"], ["model_portfolio_versions.id"]),
        sa.ForeignKeyConstraint(["portfolio_id"], ["model_portfolios.id"]),
        sa.ForeignKeyConstraint(["to_version_id"], ["model_portfolio_versions.id"]),
        sa.ForeignKeyConstraint(
            ["user_portfolio_subscription_id"], ["user_portfolio_subscriptions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_portfolio_rebalance_events_idempotency_key",
        ),
    )

    op.create_table(
        "portfolio_backtests",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("portfolio_version_id", sa.BigInteger(), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("initial_equity_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("total_return_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("max_drawdown_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("sharpe_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("sortino_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("win_rate_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("turnover_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("fees_usd", sa.Numeric(20, 4), nullable=True),
        sa.Column("slippage_usd", sa.Numeric(20, 4), nullable=True),
        sa.Column(
            "missed_trade_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "assumptions_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "equity_curve_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_version_id"], ["model_portfolio_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("portfolio_backtests")
    op.drop_table("portfolio_rebalance_events")
    op.drop_table("user_portfolio_items")
    op.drop_table("user_portfolio_subscriptions")
    op.drop_table("model_portfolio_allocations")
    op.drop_index(
        "uq_model_portfolio_versions_current_published",
        table_name="model_portfolio_versions",
    )
    op.drop_table("model_portfolio_versions")
    op.drop_table("model_portfolios")

    op.drop_constraint("ck_subscriptions_source_type", "subscriptions", type_="check")
    op.drop_column("subscriptions", "managed_by_portfolio")
    op.drop_column("subscriptions", "source_version_id")
    op.drop_column("subscriptions", "source_id")
    op.drop_column("subscriptions", "source_type")
