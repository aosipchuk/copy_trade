from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Trader(Base):
    __tablename__ = "traders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    hl_address: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    stats: Mapped[list["TraderStat"]] = relationship(back_populates="trader", lazy="noload")
    signals: Mapped[list["Signal"]] = relationship(back_populates="trader", lazy="noload")  # type: ignore[name-defined]
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="trader", lazy="noload")  # type: ignore[name-defined]


class TraderStat(Base):
    __tablename__ = "trader_stats"

    trader_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("traders.id"), primary_key=True)
    period: Mapped[str] = mapped_column(Text, primary_key=True)  # day|week|month|allTime
    pnl_usd: Mapped[float | None] = mapped_column(Numeric(20, 4))
    roi_pct: Mapped[float | None] = mapped_column(Numeric(10, 6))
    volume_usd: Mapped[float | None] = mapped_column(Numeric(20, 2))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    trader: Mapped["Trader"] = relationship(back_populates="stats")
