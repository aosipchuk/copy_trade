from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Trader(Base):
    __tablename__ = "traders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    hl_address: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    human_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NULL = not yet evaluated; False = no perp fills (prediction-market/spot-only
    # trader, nothing for the copy engine to mirror → excluded from the listing).
    has_perp_activity: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    stats: Mapped[list["TraderStat"]] = relationship(
        back_populates="trader", lazy="noload"
    )
    signals: Mapped[list["Signal"]] = relationship(  # type: ignore[name-defined]
        back_populates="trader", lazy="noload"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(  # type: ignore[name-defined]
        back_populates="trader", lazy="noload"
    )
    portfolio_allocations: Mapped[list["ModelPortfolioAllocation"]] = relationship(  # type: ignore[name-defined]
        back_populates="trader", lazy="noload"
    )
    portfolio_items: Mapped[list["UserPortfolioItem"]] = relationship(  # type: ignore[name-defined]
        back_populates="trader", lazy="noload"
    )
    new_wallet_candidates: Mapped[list["NewWalletCandidate"]] = relationship(
        back_populates="trader", lazy="noload"
    )


class TraderStat(Base):
    __tablename__ = "trader_stats"

    trader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traders.id"), primary_key=True
    )
    period: Mapped[str] = mapped_column(Text, primary_key=True)  # day|week|month|all
    pnl_usd: Mapped[float | None] = mapped_column(Numeric(20, 4))
    roi_pct: Mapped[float | None] = mapped_column(Numeric(10, 6))
    volume_usd: Mapped[float | None] = mapped_column(Numeric(20, 2))
    win_rate_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    max_drawdown_usd: Mapped[float | None] = mapped_column(Numeric(20, 4))
    max_drawdown_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    trade_count: Mapped[int | None] = mapped_column()
    avg_trade_duration_hrs: Mapped[float | None] = mapped_column(Numeric(8, 2))
    first_trade_at: Mapped[datetime | None] = mapped_column()
    sharpe_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4))
    sortino_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4))
    profit_factor: Mapped[float | None] = mapped_column(Numeric(10, 4))
    avg_pnl_per_trade: Mapped[float | None] = mapped_column(Numeric(20, 4))
    max_losing_streak: Mapped[int | None] = mapped_column(Integer)
    profitable_days_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    avg_trades_per_day: Mapped[float | None] = mapped_column(Numeric(8, 4))
    daily_pnl_std_dev: Mapped[float | None] = mapped_column(Numeric(20, 4))
    daily_pnl_by_day: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    daily_returns_pct_by_day: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    long_ratio_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    avg_position_size_usd: Mapped[float | None] = mapped_column(Numeric(20, 2))
    fees_paid_usd: Mapped[float | None] = mapped_column(Numeric(20, 4))
    calmar_ratio: Mapped[float | None] = mapped_column(Numeric(10, 4))
    composite_score: Mapped[float | None] = mapped_column(Numeric(6, 2))
    max_drawdown_duration_days: Mapped[float | None] = mapped_column(Numeric(8, 2))
    active_trading_days: Mapped[int | None] = mapped_column(Integer)
    avg_leverage: Mapped[float | None] = mapped_column(Numeric(6, 2))
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    trader: Mapped["Trader"] = relationship(back_populates="stats")
