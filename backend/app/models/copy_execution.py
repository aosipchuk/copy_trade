from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class TraderMarketScope(Base):
    __tablename__ = "trader_market_scopes"

    trader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traders.id"), primary_key=True
    )
    dex: Mapped[str] = mapped_column(Text, primary_key=True, default="")
    discovery_source: Mapped[str] = mapped_column(Text, nullable=False)
    last_fill_time_ms: Mapped[int | None] = mapped_column(BigInteger)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TraderPositionState(Base):
    __tablename__ = "trader_position_states"
    __table_args__ = (
        UniqueConstraint(
            "trader_id", "dex", "coin", name="uq_trader_position_state_market"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traders.id"), nullable=False
    )
    dex: Mapped[str] = mapped_column(Text, nullable=False, default="")
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    observed_size: Mapped[float] = mapped_column(
        Numeric(30, 12), nullable=False, default=0
    )
    accepted_size: Mapped[float] = mapped_column(
        Numeric(30, 12), nullable=False, default=0
    )
    entry_price: Mapped[float | None] = mapped_column(Numeric(30, 12))
    leverage: Mapped[float | None] = mapped_column(Numeric(10, 4))
    snapshot_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)


class CopyAccountExecutionState(Base):
    __tablename__ = "copy_account_execution_states"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'blocked')",
            name="ck_copy_account_execution_states_status",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), primary_key=True
    )
    master_address: Mapped[str | None] = mapped_column(Text)
    account_address: Mapped[str | None] = mapped_column(Text)
    vault_address: Mapped[str | None] = mapped_column(Text)
    dedicated_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime())
    account_mode: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, default="paused", server_default="paused", nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    fill_cursor_ms: Mapped[int | None] = mapped_column(BigInteger)
    last_preflight_at: Mapped[datetime | None] = mapped_column(DateTime())
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime())
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime())
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime())
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CopyPositionTarget(Base):
    __tablename__ = "copy_position_targets"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", "dex", "coin", name="uq_copy_position_target_market"
        ),
        CheckConstraint(
            "state IN ('baseline_only', 'active', 'blocked', 'zero', 'stopping')",
            name="ck_copy_position_targets_state",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id"), nullable=False
    )
    dex: Mapped[str] = mapped_column(Text, nullable=False, default="")
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    leader_size: Mapped[float] = mapped_column(Numeric(30, 12), nullable=False)
    raw_target_size: Mapped[float] = mapped_column(Numeric(30, 12), nullable=False)
    target_size: Mapped[float] = mapped_column(Numeric(30, 12), nullable=False)
    confirmed_allocated_size: Mapped[float] = mapped_column(
        Numeric(30, 12), nullable=False, default=0
    )
    target_notional_usd: Mapped[float] = mapped_column(
        Numeric(30, 8), nullable=False, default=0
    )
    price_snapshot: Mapped[float | None] = mapped_column(Numeric(30, 12))
    sizing_mode: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    source_signal_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("signals.id")
    )
    target_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CopyAccountPosition(Base):
    __tablename__ = "copy_account_positions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "dex", "coin", name="uq_copy_account_position_market"
        ),
        CheckConstraint(
            "status IN ('clean', 'dirty', 'pending', 'blocked', 'stalled')",
            name="ck_copy_account_positions_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    dex: Mapped[str] = mapped_column(Text, nullable=False, default="")
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_target_size: Mapped[float] = mapped_column(
        Numeric(30, 12), nullable=False, default=0
    )
    confirmed_actual_size: Mapped[float] = mapped_column(
        Numeric(30, 12), nullable=False, default=0
    )
    pending_explained_delta: Mapped[float] = mapped_column(
        Numeric(30, 12), nullable=False, default=0
    )
    target_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    targets_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, default="dirty", server_default="dirty", nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime())
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CopyExecutionOrder(Base):
    __tablename__ = "copy_execution_orders"
    __table_args__ = (
        UniqueConstraint("cloid", name="uq_copy_execution_orders_cloid"),
        UniqueConstraint(
            "idempotency_key", name="uq_copy_execution_orders_idempotency"
        ),
        CheckConstraint(
            "kind IN ('exchange', 'internal_reallocation', 'emergency')",
            name="ck_copy_execution_orders_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'submitted', 'partial', 'filled', 'cancelled', "
            "'failed', 'unknown')",
            name="ck_copy_execution_orders_status",
        ),
        CheckConstraint(
            "cloid ~ '^0x[0-9a-f]{32}$'",
            name="ck_copy_execution_orders_cloid",
        ),
        Index(
            "uq_copy_execution_orders_active_market",
            "user_id",
            "dex",
            "coin",
            unique=True,
            postgresql_where=text(
                "kind IN ('exchange', 'emergency') AND "
                "status IN ('pending', 'submitted', 'partial', 'unknown')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    dex: Mapped[str] = mapped_column(Text, nullable=False, default="")
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="exchange")
    cloid: Mapped[str] = mapped_column(Text, nullable=False)
    exchange_oid: Mapped[int | None] = mapped_column(BigInteger)
    aggregate_target_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before_size: Mapped[float] = mapped_column(Numeric(30, 12), nullable=False)
    target_size: Mapped[float] = mapped_column(Numeric(30, 12), nullable=False)
    requested_delta: Mapped[float] = mapped_column(Numeric(30, 12), nullable=False)
    is_buy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rounded_size: Mapped[float] = mapped_column(Numeric(30, 12), nullable=False)
    reference_price: Mapped[float | None] = mapped_column(Numeric(30, 12))
    limit_price: Mapped[float | None] = mapped_column(Numeric(30, 12))
    status: Mapped[str] = mapped_column(
        Text, default="pending", server_default="pending", nullable=False
    )
    filled_size: Mapped[float] = mapped_column(
        Numeric(30, 12), nullable=False, default=0
    )
    average_price: Mapped[float | None] = mapped_column(Numeric(30, 12))
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CopyExecutionAllocation(Base):
    __tablename__ = "copy_execution_allocations"
    __table_args__ = (
        UniqueConstraint(
            "execution_order_id",
            "target_id",
            name="uq_copy_execution_allocation_order_target",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    execution_order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("copy_execution_orders.id"), nullable=False
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("copy_position_targets.id"), nullable=False
    )
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id"), nullable=False
    )
    requested_delta: Mapped[float] = mapped_column(Numeric(30, 12), nullable=False)
    filled_delta: Mapped[float] = mapped_column(
        Numeric(30, 12), nullable=False, default=0
    )
    allocation_price: Mapped[float | None] = mapped_column(Numeric(30, 12))
    realized_pnl: Mapped[float | None] = mapped_column(Numeric(30, 8))
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
