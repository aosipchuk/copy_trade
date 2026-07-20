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


class NewWalletCandidate(Base):
    __tablename__ = "new_wallet_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'qualified', 'rejected', 'subscribed', "
            "'expired', 'disabled')",
            name="ck_new_wallet_candidates_status",
        ),
        Index("ix_new_wallet_candidates_status_detected", "status", "detected_at"),
        Index("ix_new_wallet_candidates_hl_address", "hl_address"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trader_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("traders.id"))
    hl_address: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, default="pending", server_default="pending", nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    funded_at: Mapped[datetime | None] = mapped_column()
    qualified_at: Mapped[datetime | None] = mapped_column()
    last_checked_at: Mapped[datetime | None] = mapped_column()
    chain_depth: Mapped[int | None] = mapped_column(Integer)
    chain_total_balance_usd: Mapped[float | None] = mapped_column(Numeric(20, 2))
    threshold_usd_snapshot: Mapped[float | None] = mapped_column(Numeric(20, 2))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    first_seen_tx_hash: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[JsonDict | None] = mapped_column(JSONB)

    trader: Mapped["Trader | None"] = relationship(  # type: ignore[name-defined]
        back_populates="new_wallet_candidates",
        lazy="noload",
    )
    links: Mapped[list["NewWalletFundingLink"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        lazy="noload",
    )
    items: Mapped[list["UserNewWalletItem"]] = relationship(
        back_populates="candidate",
        lazy="noload",
    )


class NewWalletFundingLink(Base):
    __tablename__ = "new_wallet_funding_links"
    __table_args__ = (
        Index("ix_new_wallet_links_candidate_depth", "candidate_id", "depth"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("new_wallet_candidates.id"), nullable=False
    )
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    wallet_address: Mapped[str] = mapped_column(Text, nullable=False)
    funded_by_address: Mapped[str | None] = mapped_column(Text)
    amount_usdc: Mapped[float | None] = mapped_column(Numeric(20, 6))
    event_time: Mapped[datetime | None] = mapped_column()
    tx_hash: Mapped[str | None] = mapped_column(Text)
    balance_usd: Mapped[float | None] = mapped_column(Numeric(20, 2))
    balance_source: Mapped[str | None] = mapped_column(Text)
    raw_event_json: Mapped[JsonDict | None] = mapped_column(JSONB)

    candidate: Mapped["NewWalletCandidate"] = relationship(back_populates="links")


class UserNewWalletSubscription(Base):
    __tablename__ = "user_new_wallet_subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'canceled')",
            name="ck_user_new_wallet_subscriptions_status",
        ),
        Index(
            "ix_user_new_wallet_subscriptions_user_status_mode",
            "user_id",
            "status",
            "is_demo",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text, default="active", server_default="active", nullable=False
    )
    is_demo: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    total_allocation_usd: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    max_active_wallets: Mapped[int] = mapped_column(Integer, nullable=False)
    max_per_wallet_usd: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    copy_ratio_pct: Mapped[float] = mapped_column(
        Numeric(5, 2), default=100, server_default="100", nullable=False
    )
    stop_loss_pct: Mapped[float] = mapped_column(
        Numeric(5, 2), default=20, server_default="20", nullable=False
    )
    max_leverage: Mapped[float] = mapped_column(
        Numeric(5, 2), default=10, server_default="10", nullable=False
    )
    sizing_mode: Mapped[str] = mapped_column(
        Text, default="fixed_ratio", server_default="fixed_ratio", nullable=False
    )
    allowed_coins: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    close_positions_on_expire: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
    canceled_at: Mapped[datetime | None] = mapped_column()

    user: Mapped["User"] = relationship(  # type: ignore[name-defined]
        back_populates="new_wallet_subscriptions"
    )
    items: Mapped[list["UserNewWalletItem"]] = relationship(
        back_populates="user_new_wallet_subscription",
        lazy="noload",
    )


class UserNewWalletItem(Base):
    __tablename__ = "user_new_wallet_items"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            name="uq_user_new_wallet_items_subscription",
        ),
        CheckConstraint(
            "status IN ('active', 'expired', 'failed', 'removed')",
            name="ck_user_new_wallet_items_status",
        ),
        Index("ix_user_new_wallet_items_subscription", "subscription_id"),
        Index(
            "ix_user_new_wallet_items_active_expires",
            "expires_at",
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_user_new_wallet_items_active_parent_candidate",
            "user_new_wallet_subscription_id",
            "candidate_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_new_wallet_subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_new_wallet_subscriptions.id"), nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("new_wallet_candidates.id"), nullable=False
    )
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id"), nullable=False
    )
    trader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traders.id"), nullable=False
    )
    target_allocation_usd: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    status: Mapped[str] = mapped_column(
        Text, default="active", server_default="active", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column()
    error_msg: Mapped[str | None] = mapped_column(Text)

    user_new_wallet_subscription: Mapped["UserNewWalletSubscription"] = relationship(
        back_populates="items"
    )
    candidate: Mapped["NewWalletCandidate"] = relationship(back_populates="items")
    subscription: Mapped["Subscription"] = relationship(  # type: ignore[name-defined]
        back_populates="new_wallet_item"
    )
    trader: Mapped["Trader"] = relationship(lazy="noload")  # type: ignore[name-defined]
