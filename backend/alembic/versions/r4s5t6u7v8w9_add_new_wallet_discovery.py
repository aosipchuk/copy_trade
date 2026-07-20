"""add new wallet discovery

Revision ID: r4s5t6u7v8w9
Revises: q3r4s5t6u7v8
Create Date: 2026-07-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "r4s5t6u7v8w9"
down_revision: str | None = "q3r4s5t6u7v8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_subscriptions_source_type",
        "subscriptions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_subscriptions_source_type",
        "subscriptions",
        "source_type IN ('manual', 'model_portfolio', 'new_wallet')",
    )
    op.add_column("subscriptions", sa.Column("expires_at", sa.DateTime()))
    op.add_column("subscriptions", sa.Column("ended_reason", sa.Text()))
    op.create_index(
        "ix_subscriptions_new_wallet_active_expires",
        "subscriptions",
        ["source_type", "expires_at"],
        postgresql_where=sa.text("is_active IS true"),
    )

    op.create_table(
        "new_wallet_candidates",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("trader_id", sa.BigInteger(), nullable=True),
        sa.Column("hl_address", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("funded_at", sa.DateTime(), nullable=True),
        sa.Column("qualified_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("chain_depth", sa.Integer(), nullable=True),
        sa.Column("chain_total_balance_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("threshold_usd_snapshot", sa.Numeric(20, 2), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("first_seen_tx_hash", sa.Text(), nullable=True),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'qualified', 'rejected', 'subscribed', "
            "'expired', 'disabled')",
            name="ck_new_wallet_candidates_status",
        ),
        sa.ForeignKeyConstraint(["trader_id"], ["traders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hl_address"),
    )
    op.create_index(
        "ix_new_wallet_candidates_status_detected",
        "new_wallet_candidates",
        ["status", "detected_at"],
    )
    op.create_index(
        "ix_new_wallet_candidates_hl_address",
        "new_wallet_candidates",
        ["hl_address"],
    )

    op.create_table(
        "new_wallet_funding_links",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("wallet_address", sa.Text(), nullable=False),
        sa.Column("funded_by_address", sa.Text(), nullable=True),
        sa.Column("amount_usdc", sa.Numeric(20, 6), nullable=True),
        sa.Column("event_time", sa.DateTime(), nullable=True),
        sa.Column("tx_hash", sa.Text(), nullable=True),
        sa.Column("balance_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("balance_source", sa.Text(), nullable=True),
        sa.Column(
            "raw_event_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["new_wallet_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_new_wallet_links_candidate_depth",
        "new_wallet_funding_links",
        ["candidate_id", "depth"],
    )

    op.create_table(
        "user_new_wallet_subscriptions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "is_demo",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("total_allocation_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("max_active_wallets", sa.Integer(), nullable=False),
        sa.Column("max_per_wallet_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column(
            "copy_ratio_pct", sa.Numeric(5, 2), server_default="100", nullable=False
        ),
        sa.Column(
            "stop_loss_pct", sa.Numeric(5, 2), server_default="20", nullable=False
        ),
        sa.Column(
            "max_leverage", sa.Numeric(5, 2), server_default="10", nullable=False
        ),
        sa.Column(
            "sizing_mode", sa.Text(), server_default="fixed_ratio", nullable=False
        ),
        sa.Column("allowed_coins", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column(
            "close_positions_on_expire",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'canceled')",
            name="ck_user_new_wallet_subscriptions_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_new_wallet_subscriptions_user_status_mode",
        "user_new_wallet_subscriptions",
        ["user_id", "status", "is_demo"],
    )

    op.create_table(
        "user_new_wallet_items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_new_wallet_subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("trader_id", sa.BigInteger(), nullable=False),
        sa.Column("target_allocation_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'expired', 'failed', 'removed')",
            name="ck_user_new_wallet_items_status",
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["new_wallet_candidates.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.ForeignKeyConstraint(["trader_id"], ["traders.id"]),
        sa.ForeignKeyConstraint(
            ["user_new_wallet_subscription_id"],
            ["user_new_wallet_subscriptions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_id",
            name="uq_user_new_wallet_items_subscription",
        ),
    )
    op.create_index(
        "ix_user_new_wallet_items_subscription",
        "user_new_wallet_items",
        ["subscription_id"],
    )
    op.create_index(
        "ix_user_new_wallet_items_active_expires",
        "user_new_wallet_items",
        ["expires_at"],
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_user_new_wallet_items_active_parent_candidate",
        "user_new_wallet_items",
        ["user_new_wallet_subscription_id", "candidate_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_new_wallet_items_active_parent_candidate",
        table_name="user_new_wallet_items",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index(
        "ix_user_new_wallet_items_active_expires",
        table_name="user_new_wallet_items",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_index(
        "ix_user_new_wallet_items_subscription",
        table_name="user_new_wallet_items",
    )
    op.drop_table("user_new_wallet_items")
    op.drop_index(
        "ix_user_new_wallet_subscriptions_user_status_mode",
        table_name="user_new_wallet_subscriptions",
    )
    op.drop_table("user_new_wallet_subscriptions")
    op.drop_index(
        "ix_new_wallet_links_candidate_depth",
        table_name="new_wallet_funding_links",
    )
    op.drop_table("new_wallet_funding_links")
    op.drop_index(
        "ix_new_wallet_candidates_hl_address",
        table_name="new_wallet_candidates",
    )
    op.drop_index(
        "ix_new_wallet_candidates_status_detected",
        table_name="new_wallet_candidates",
    )
    op.drop_table("new_wallet_candidates")

    op.drop_index(
        "ix_subscriptions_new_wallet_active_expires",
        table_name="subscriptions",
        postgresql_where=sa.text("is_active IS true"),
    )
    op.drop_column("subscriptions", "ended_reason")
    op.drop_column("subscriptions", "expires_at")
    op.drop_constraint(
        "ck_subscriptions_source_type",
        "subscriptions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_subscriptions_source_type",
        "subscriptions",
        "source_type IN ('manual', 'model_portfolio')",
    )
