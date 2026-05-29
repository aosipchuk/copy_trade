from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    trader_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("traders.id"), nullable=False)
    max_allocation_usd: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    copy_ratio_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=100)
    stop_loss_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=20)
    max_leverage: Mapped[float] = mapped_column(Numeric(5, 2), default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="subscriptions")  # type: ignore[name-defined]
    trader: Mapped["Trader"] = relationship(back_populates="subscriptions")  # type: ignore[name-defined]
    trades: Mapped[list["UserTrade"]] = relationship(back_populates="subscription", lazy="noload")  # type: ignore[name-defined]
