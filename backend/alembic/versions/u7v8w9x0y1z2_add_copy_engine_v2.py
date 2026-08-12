"""add copy engine v2 state and pause legacy execution

Revision ID: u7v8w9x0y1z2
Revises: t6u7v8w9x0y1
Create Date: 2026-08-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "u7v8w9x0y1z2"
down_revision: str | None = "t6u7v8w9x0y1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_execution_columns(table: str) -> None:
    op.add_column(
        table,
        sa.Column(
            "engine_version", sa.Integer(), server_default="1", nullable=False
        ),
    )
    op.add_column(
        table,
        sa.Column(
            "execution_status", sa.Text(), server_default="paused", nullable=False
        ),
    )
    op.add_column(table, sa.Column("pause_reason", sa.Text()))
    op.add_column(
        table, sa.Column("execution_status_details", postgresql.JSONB())
    )
    op.add_column(table, sa.Column("resumed_at", sa.DateTime()))
    op.add_column(table, sa.Column("blocked_at", sa.DateTime()))
    op.create_check_constraint(
        f"ck_{table}_execution_status",
        table,
        "execution_status IN ('active', 'paused', 'blocked', 'stopping', 'stopped')",
    )
    op.create_check_constraint(
        f"ck_{table}_engine_version",
        table,
        "engine_version IN (1, 2)",
    )


def _drop_execution_columns(table: str) -> None:
    op.drop_constraint(f"ck_{table}_engine_version", table, type_="check")
    op.drop_constraint(f"ck_{table}_execution_status", table, type_="check")
    for column in (
        "blocked_at",
        "resumed_at",
        "execution_status_details",
        "pause_reason",
        "execution_status",
        "engine_version",
    ):
        op.drop_column(table, column)


def upgrade() -> None:
    _add_execution_columns("subscriptions")
    _add_execution_columns("user_portfolio_subscriptions")
    _add_execution_columns("user_new_wallet_subscriptions")

    op.drop_constraint(
        "ck_user_new_wallet_items_status",
        "user_new_wallet_items",
        type_="check",
    )
    op.create_check_constraint(
        "ck_user_new_wallet_items_status",
        "user_new_wallet_items",
        "status IN ('active', 'paused', 'expired', 'failed', 'removed')",
    )

    op.add_column(
        "signals",
        sa.Column("dex", sa.Text(), server_default="", nullable=False),
    )
    op.add_column("signals", sa.Column("previous_size", sa.Numeric(30, 12)))
    op.add_column("signals", sa.Column("target_size", sa.Numeric(30, 12)))
    op.add_column("signals", sa.Column("delta_size", sa.Numeric(30, 12)))
    op.add_column("signals", sa.Column("snapshot_version", sa.BigInteger()))
    op.add_column(
        "signals",
        sa.Column("engine_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("signals", sa.Column("dedupe_key", sa.Text()))
    op.add_column(
        "signals",
        sa.Column(
            "dispatch_status", sa.Text(), server_default="pending", nullable=False
        ),
    )
    op.create_unique_constraint("uq_signals_dedupe_key", "signals", ["dedupe_key"])
    op.create_check_constraint(
        "ck_signals_engine_version",
        "signals",
        "engine_version IN (1, 2)",
    )
    op.create_check_constraint(
        "ck_signals_dispatch_status",
        "signals",
        "dispatch_status IN ("
        "'pending', 'accepted', 'dispatched', 'skipped_legacy', 'failed')",
    )

    op.create_table(
        "trader_market_scopes",
        sa.Column("trader_id", sa.BigInteger(), nullable=False),
        sa.Column("dex", sa.Text(), server_default="", nullable=False),
        sa.Column("discovery_source", sa.Text(), nullable=False),
        sa.Column("last_fill_time_ms", sa.BigInteger()),
        sa.Column("last_polled_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["trader_id"], ["traders.id"]),
        sa.PrimaryKeyConstraint("trader_id", "dex"),
    )
    op.create_table(
        "trader_position_states",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("trader_id", sa.BigInteger(), nullable=False),
        sa.Column("dex", sa.Text(), server_default="", nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("observed_size", sa.Numeric(30, 12), server_default="0", nullable=False),
        sa.Column("accepted_size", sa.Numeric(30, 12), server_default="0", nullable=False),
        sa.Column("entry_price", sa.Numeric(30, 12)),
        sa.Column("leverage", sa.Numeric(10, 4)),
        sa.Column("snapshot_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["trader_id"], ["traders.id"]),
        sa.UniqueConstraint(
            "trader_id", "dex", "coin", name="uq_trader_position_state_market"
        ),
    )
    op.create_table(
        "copy_account_execution_states",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("master_address", sa.Text()),
        sa.Column("account_address", sa.Text()),
        sa.Column("vault_address", sa.Text()),
        sa.Column("dedicated_confirmed_at", sa.DateTime()),
        sa.Column("account_mode", sa.Text()),
        sa.Column("status", sa.Text(), server_default="paused", nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("details", postgresql.JSONB()),
        sa.Column("fill_cursor_ms", sa.BigInteger()),
        sa.Column("last_preflight_at", sa.DateTime()),
        sa.Column("last_reconciled_at", sa.DateTime()),
        sa.Column("blocked_at", sa.DateTime()),
        sa.Column("cleared_at", sa.DateTime()),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'blocked')",
            name="ck_copy_account_execution_states_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_table(
        "copy_position_targets",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("dex", sa.Text(), server_default="", nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("leader_size", sa.Numeric(30, 12), nullable=False),
        sa.Column("raw_target_size", sa.Numeric(30, 12), nullable=False),
        sa.Column("target_size", sa.Numeric(30, 12), nullable=False),
        sa.Column("confirmed_allocated_size", sa.Numeric(30, 12), server_default="0", nullable=False),
        sa.Column("target_notional_usd", sa.Numeric(30, 8), server_default="0", nullable=False),
        sa.Column("price_snapshot", sa.Numeric(30, 12)),
        sa.Column("sizing_mode", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("source_signal_id", sa.BigInteger()),
        sa.Column("target_version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "state IN ('baseline_only', 'active', 'blocked', 'zero', 'stopping')",
            name="ck_copy_position_targets_state",
        ),
        sa.ForeignKeyConstraint(["source_signal_id"], ["signals.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.UniqueConstraint(
            "subscription_id", "dex", "coin", name="uq_copy_position_target_market"
        ),
    )
    op.create_table(
        "copy_account_positions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("dex", sa.Text(), server_default="", nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("aggregate_target_size", sa.Numeric(30, 12), server_default="0", nullable=False),
        sa.Column("confirmed_actual_size", sa.Numeric(30, 12), server_default="0", nullable=False),
        sa.Column("pending_explained_delta", sa.Numeric(30, 12), server_default="0", nullable=False),
        sa.Column("target_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("targets_hash", sa.Text()),
        sa.Column("status", sa.Text(), server_default="dirty", nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("last_reconciled_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('clean', 'dirty', 'pending', 'blocked', 'stalled')",
            name="ck_copy_account_positions_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", "dex", "coin", name="uq_copy_account_position_market"),
    )
    op.create_table(
        "copy_execution_orders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("dex", sa.Text(), server_default="", nullable=False),
        sa.Column("coin", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), server_default="exchange", nullable=False),
        sa.Column("cloid", sa.Text(), nullable=False),
        sa.Column("exchange_oid", sa.BigInteger()),
        sa.Column("aggregate_target_version", sa.BigInteger(), nullable=False),
        sa.Column("before_size", sa.Numeric(30, 12), nullable=False),
        sa.Column("target_size", sa.Numeric(30, 12), nullable=False),
        sa.Column("requested_delta", sa.Numeric(30, 12), nullable=False),
        sa.Column("is_buy", sa.Boolean(), nullable=False),
        sa.Column("reduce_only", sa.Boolean(), nullable=False),
        sa.Column("rounded_size", sa.Numeric(30, 12), nullable=False),
        sa.Column("reference_price", sa.Numeric(30, 12)),
        sa.Column("limit_price", sa.Numeric(30, 12)),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("filled_size", sa.Numeric(30, 12), server_default="0", nullable=False),
        sa.Column("average_price", sa.Numeric(30, 12)),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("submitted_at", sa.DateTime()),
        sa.Column("completed_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('exchange', 'internal_reallocation', 'emergency')",
            name="ck_copy_execution_orders_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'submitted', 'partial', 'filled', 'cancelled', 'failed', 'unknown')",
            name="ck_copy_execution_orders_status",
        ),
        sa.CheckConstraint(
            "cloid ~ '^0x[0-9a-f]{32}$'",
            name="ck_copy_execution_orders_cloid",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("cloid", name="uq_copy_execution_orders_cloid"),
        sa.UniqueConstraint("idempotency_key", name="uq_copy_execution_orders_idempotency"),
    )
    op.create_index(
        "uq_copy_execution_orders_active_market",
        "copy_execution_orders",
        ["user_id", "dex", "coin"],
        unique=True,
        postgresql_where=sa.text(
            "kind IN ('exchange', 'emergency') AND "
            "status IN ('pending', 'submitted', 'partial', 'unknown')"
        ),
    )
    op.create_table(
        "copy_execution_allocations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("execution_order_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_delta", sa.Numeric(30, 12), nullable=False),
        sa.Column("filled_delta", sa.Numeric(30, 12), server_default="0", nullable=False),
        sa.Column("allocation_price", sa.Numeric(30, 12)),
        sa.Column("realized_pnl", sa.Numeric(30, 8)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["execution_order_id"], ["copy_execution_orders.id"]),
        sa.ForeignKeyConstraint(["target_id"], ["copy_position_targets.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.UniqueConstraint(
            "execution_order_id",
            "target_id",
            name="uq_copy_execution_allocation_order_target",
        ),
    )

    op.alter_column("user_trades", "signal_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column(
        "user_trades",
        sa.Column("dex", sa.Text(), server_default="", nullable=False),
    )
    op.add_column("user_trades", sa.Column("execution_order_id", sa.BigInteger()))
    op.add_column("user_trades", sa.Column("execution_allocation_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_user_trades_execution_order",
        "user_trades",
        "copy_execution_orders",
        ["execution_order_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_user_trades_execution_allocation",
        "user_trades",
        "copy_execution_allocations",
        ["execution_allocation_id"],
        ["id"],
    )

    # Capture the users that had live subscriptions before pausing them.
    op.execute(
        """
        INSERT INTO copy_account_execution_states (
            user_id, master_address, account_address, status, reason
        )
        SELECT DISTINCT s.user_id, u.hl_address, u.hl_address,
               'paused', 'engine_v2_reconciliation_required'
        FROM subscriptions s
        JOIN users u ON u.id = s.user_id
        WHERE s.is_active = true AND s.is_demo = false
        ON CONFLICT (user_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE subscriptions
        SET is_active = false,
            execution_status = 'paused',
            pause_reason = 'engine_v2_reconciliation_required',
            engine_version = 1
        WHERE is_active = true AND is_demo = false
        """
    )
    op.execute(
        """
        UPDATE user_portfolio_subscriptions
        SET execution_status = 'paused',
            pause_reason = 'engine_v2_reconciliation_required',
            engine_version = 1
        WHERE is_demo = false AND status <> 'canceled'
        """
    )
    op.execute(
        """
        UPDATE user_portfolio_items i
        SET status = 'paused'
        FROM user_portfolio_subscriptions p
        WHERE p.id = i.user_portfolio_subscription_id
          AND p.is_demo = false AND i.status = 'active'
        """
    )
    op.execute(
        """
        UPDATE user_new_wallet_subscriptions
        SET execution_status = 'paused',
            pause_reason = 'engine_v2_reconciliation_required',
            engine_version = 1
        WHERE is_demo = false AND status <> 'canceled'
        """
    )
    op.execute(
        """
        UPDATE user_new_wallet_items i
        SET status = 'paused'
        FROM user_new_wallet_subscriptions p
        WHERE p.id = i.user_new_wallet_subscription_id
          AND p.is_demo = false AND i.status = 'active'
        """
    )
    op.execute(
        """
        UPDATE subscriptions
        SET is_active = false,
            execution_status = 'stopped',
            pause_reason = 'engine_v2_demo_restart_required',
            ended_reason = COALESCE(ended_reason, 'engine_v2_demo_restart_required'),
            engine_version = 1
        WHERE is_active = true AND is_demo = true
        """
    )
    op.execute(
        "UPDATE signals SET dispatch_status = 'skipped_legacy' WHERE engine_version = 1"
    )


def downgrade() -> None:
    # Safety invariant: never reactivate paused subscriptions on downgrade.
    op.drop_constraint("fk_user_trades_execution_allocation", "user_trades", type_="foreignkey")
    op.drop_constraint("fk_user_trades_execution_order", "user_trades", type_="foreignkey")
    op.drop_column("user_trades", "execution_allocation_id")
    op.drop_column("user_trades", "execution_order_id")
    op.drop_column("user_trades", "dex")
    op.alter_column("user_trades", "signal_id", existing_type=sa.BigInteger(), nullable=False)

    op.drop_table("copy_execution_allocations")
    op.drop_index("uq_copy_execution_orders_active_market", table_name="copy_execution_orders")
    op.drop_table("copy_execution_orders")
    op.drop_table("copy_account_positions")
    op.drop_table("copy_position_targets")
    op.drop_table("copy_account_execution_states")
    op.drop_table("trader_position_states")
    op.drop_table("trader_market_scopes")

    op.drop_constraint("ck_signals_dispatch_status", "signals", type_="check")
    op.drop_constraint("ck_signals_engine_version", "signals", type_="check")
    op.drop_constraint("uq_signals_dedupe_key", "signals", type_="unique")
    for column in (
        "dispatch_status",
        "dedupe_key",
        "engine_version",
        "snapshot_version",
        "delta_size",
        "target_size",
        "previous_size",
        "dex",
    ):
        op.drop_column("signals", column)

    op.drop_constraint(
        "ck_user_new_wallet_items_status",
        "user_new_wallet_items",
        type_="check",
    )
    op.create_check_constraint(
        "ck_user_new_wallet_items_status",
        "user_new_wallet_items",
        "status IN ('active', 'expired', 'failed', 'removed')",
    )
    _drop_execution_columns("user_new_wallet_subscriptions")
    _drop_execution_columns("user_portfolio_subscriptions")
    _drop_execution_columns("subscriptions")
