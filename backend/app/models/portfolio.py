from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

JsonDict = dict[str, Any]


class ModelPortfolio(Base):
    __tablename__ = "model_portfolios"
    __table_args__ = (
        CheckConstraint(
            "risk_profile IN ('conservative', 'balanced', 'aggressive')",
            name="ck_model_portfolios_risk_profile",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'retired')",
            name="ck_model_portfolios_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    risk_profile: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, default="draft", server_default="draft", nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    methodology_version: Mapped[str] = mapped_column(Text, nullable=False)
    rebalance_cadence: Mapped[str] = mapped_column(Text, nullable=False)
    min_equity_usd: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    monthly_price_usd: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    trial_days: Mapped[int] = mapped_column(
        Integer, default=7, server_default="7", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )

    versions: Mapped[list["ModelPortfolioVersion"]] = relationship(
        back_populates="portfolio", lazy="noload"
    )
    user_subscriptions: Mapped[list["UserPortfolioSubscription"]] = relationship(
        back_populates="portfolio", lazy="noload"
    )
    rebalance_events: Mapped[list["PortfolioRebalanceEvent"]] = relationship(
        back_populates="portfolio", lazy="noload"
    )


class ModelPortfolioVersion(Base):
    __tablename__ = "model_portfolio_versions"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "version_no",
            name="uq_model_portfolio_versions_portfolio_version",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired', 'rejected')",
            name="ck_model_portfolio_versions_status",
        ),
        Index(
            "uq_model_portfolio_versions_current_published",
            "portfolio_id",
            unique=True,
            postgresql_where=text("status = 'published' AND valid_to IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_portfolios.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, default="draft", server_default="draft", nullable=False
    )
    valid_from: Mapped[datetime | None] = mapped_column()
    valid_to: Mapped[datetime | None] = mapped_column()
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column()
    approval_note: Mapped[str | None] = mapped_column(Text)
    selection_started_at: Mapped[datetime | None] = mapped_column()
    selection_finished_at: Mapped[datetime | None] = mapped_column()
    facts_hash: Mapped[str | None] = mapped_column(Text)
    summary_json: Mapped[JsonDict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    portfolio: Mapped["ModelPortfolio"] = relationship(back_populates="versions")
    allocations: Mapped[list["ModelPortfolioAllocation"]] = relationship(
        back_populates="version", lazy="noload"
    )
    user_subscriptions: Mapped[list["UserPortfolioSubscription"]] = relationship(
        back_populates="active_version",
        foreign_keys="UserPortfolioSubscription.active_version_id",
        lazy="noload",
    )
    portfolio_items: Mapped[list["UserPortfolioItem"]] = relationship(
        back_populates="portfolio_version",
        foreign_keys="UserPortfolioItem.portfolio_version_id",
        lazy="noload",
    )
    backtests: Mapped[list["PortfolioBacktest"]] = relationship(
        back_populates="portfolio_version", lazy="noload"
    )


class ModelPortfolioAllocation(Base):
    __tablename__ = "model_portfolio_allocations"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "trader_id",
            name="uq_model_portfolio_allocations_version_trader",
        ),
        CheckConstraint(
            "target_weight_pct > 0 AND target_weight_pct <= 100",
            name="ck_model_portfolio_allocations_target_weight",
        ),
        CheckConstraint(
            "copy_ratio_pct > 0 AND copy_ratio_pct <= 100",
            name="ck_model_portfolio_allocations_copy_ratio",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_portfolio_versions.id"), nullable=False
    )
    trader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traders.id"), nullable=False
    )
    target_weight_pct: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    copy_ratio_pct: Mapped[float] = mapped_column(
        Numeric(5, 2), default=100, server_default="100", nullable=False
    )
    max_leverage: Mapped[float] = mapped_column(
        Numeric(5, 2), default=10, server_default="10", nullable=False
    )
    stop_loss_pct: Mapped[float] = mapped_column(
        Numeric(5, 2), default=20, server_default="20", nullable=False
    )
    sizing_mode: Mapped[str] = mapped_column(
        Text, default="fixed_ratio", server_default="fixed_ratio", nullable=False
    )
    max_per_coin_usd: Mapped[float | None] = mapped_column(Numeric(20, 2))
    allowed_coins: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    reason_code: Mapped[str | None] = mapped_column(Text)
    reason_text: Mapped[str | None] = mapped_column(Text)
    score_snapshot: Mapped[JsonDict | None] = mapped_column(JSONB)
    constraint_snapshot: Mapped[JsonDict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    version: Mapped["ModelPortfolioVersion"] = relationship(
        back_populates="allocations"
    )
    trader: Mapped["Trader"] = relationship(  # type: ignore[name-defined]
        back_populates="portfolio_allocations"
    )
    portfolio_items: Mapped[list["UserPortfolioItem"]] = relationship(
        back_populates="allocation", lazy="noload"
    )


class UserPortfolioSubscription(Base):
    __tablename__ = "user_portfolio_subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('trialing', 'active', 'past_due', 'paused', 'canceled')",
            name="ck_user_portfolio_subscriptions_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    portfolio_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_portfolios.id"), nullable=False
    )
    active_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_portfolio_versions.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text, default="trialing", server_default="trialing", nullable=False
    )
    is_demo: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    auto_rebalance: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    total_allocation_usd: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    close_removed_positions: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    billing_provider: Mapped[str | None] = mapped_column(Text)
    billing_customer_id: Mapped[str | None] = mapped_column(Text)
    billing_subscription_id: Mapped[str | None] = mapped_column(Text)
    current_period_end: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
    canceled_at: Mapped[datetime | None] = mapped_column()

    user: Mapped["User"] = relationship(  # type: ignore[name-defined]
        back_populates="portfolio_subscriptions"
    )
    portfolio: Mapped["ModelPortfolio"] = relationship(
        back_populates="user_subscriptions"
    )
    active_version: Mapped["ModelPortfolioVersion"] = relationship(
        back_populates="user_subscriptions",
        foreign_keys=[active_version_id],
    )
    items: Mapped[list["UserPortfolioItem"]] = relationship(
        back_populates="user_portfolio_subscription", lazy="noload"
    )
    rebalance_events: Mapped[list["PortfolioRebalanceEvent"]] = relationship(
        back_populates="user_portfolio_subscription", lazy="noload"
    )


class UserPortfolioItem(Base):
    __tablename__ = "user_portfolio_items"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", name="uq_user_portfolio_items_subscription"
        ),
        UniqueConstraint(
            "user_portfolio_subscription_id",
            "allocation_id",
            name="uq_user_portfolio_items_subscription_allocation",
        ),
        CheckConstraint(
            "status IN ('active', 'removed', 'failed', 'paused')",
            name="ck_user_portfolio_items_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_portfolio_subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_portfolio_subscriptions.id"), nullable=False
    )
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id"), nullable=False
    )
    portfolio_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_portfolio_versions.id"), nullable=False
    )
    allocation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_portfolio_allocations.id"), nullable=False
    )
    trader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traders.id"), nullable=False
    )
    target_allocation_usd: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    target_weight_pct: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    status: Mapped[str] = mapped_column(
        Text, default="active", server_default="active", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    removed_at: Mapped[datetime | None] = mapped_column()

    user_portfolio_subscription: Mapped["UserPortfolioSubscription"] = relationship(
        back_populates="items"
    )
    subscription: Mapped["Subscription"] = relationship(  # type: ignore[name-defined]
        back_populates="portfolio_item"
    )
    portfolio_version: Mapped["ModelPortfolioVersion"] = relationship(
        back_populates="portfolio_items",
        foreign_keys=[portfolio_version_id],
    )
    allocation: Mapped["ModelPortfolioAllocation"] = relationship(
        back_populates="portfolio_items"
    )
    trader: Mapped["Trader"] = relationship(  # type: ignore[name-defined]
        back_populates="portfolio_items"
    )


class PortfolioRebalanceEvent(Base):
    __tablename__ = "portfolio_rebalance_events"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_portfolio_rebalance_events_idempotency_key",
        ),
        CheckConstraint(
            "event_type IN ('scheduled', 'emergency', 'manual', 'user_apply')",
            name="ck_portfolio_rebalance_events_event_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'pending', 'running', 'completed', 'failed', "
            "'skipped')",
            name="ck_portfolio_rebalance_events_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_portfolios.id"), nullable=False
    )
    from_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("model_portfolio_versions.id")
    )
    to_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("model_portfolio_versions.id")
    )
    user_portfolio_subscription_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user_portfolio_subscriptions.id")
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, default="draft", server_default="draft", nullable=False
    )
    diff_json: Mapped[JsonDict | None] = mapped_column(JSONB)
    error_msg: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    executed_at: Mapped[datetime | None] = mapped_column()

    portfolio: Mapped["ModelPortfolio"] = relationship(
        back_populates="rebalance_events"
    )
    from_version: Mapped["ModelPortfolioVersion | None"] = relationship(
        foreign_keys=[from_version_id]
    )
    to_version: Mapped["ModelPortfolioVersion | None"] = relationship(
        foreign_keys=[to_version_id]
    )
    user_portfolio_subscription: Mapped["UserPortfolioSubscription | None"] = (
        relationship(back_populates="rebalance_events")
    )


class PortfolioBacktest(Base):
    __tablename__ = "portfolio_backtests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    portfolio_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("model_portfolio_versions.id"), nullable=False
    )
    period_days: Mapped[int] = mapped_column(Integer, nullable=False)
    initial_equity_usd: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    total_return_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    max_drawdown_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    sharpe_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4))
    sortino_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4))
    win_rate_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    turnover_pct: Mapped[float | None] = mapped_column(Numeric(10, 4))
    fees_usd: Mapped[float | None] = mapped_column(Numeric(20, 4))
    slippage_usd: Mapped[float | None] = mapped_column(Numeric(20, 4))
    missed_trade_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    assumptions_json: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    equity_curve_json: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    portfolio_version: Mapped["ModelPortfolioVersion"] = relationship(
        back_populates="backtests"
    )
