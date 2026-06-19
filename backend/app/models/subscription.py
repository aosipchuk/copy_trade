from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, Boolean, ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    trader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traders.id"), nullable=False
    )
    max_allocation_usd: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    copy_ratio_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=100)
    stop_loss_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=20)
    max_leverage: Mapped[float] = mapped_column(Numeric(5, 2), default=10)
    sizing_mode: Mapped[str] = mapped_column(
        Text, default="fixed_ratio", server_default="fixed_ratio"
    )
    max_per_coin_usd: Mapped[float | None] = mapped_column(Numeric(20, 2))
    allowed_coins: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship(  # type: ignore[name-defined]
        back_populates="subscriptions"
    )
    trader: Mapped["Trader"] = relationship(  # type: ignore[name-defined]
        back_populates="subscriptions"
    )
    trades: Mapped[list["UserTrade"]] = relationship(  # type: ignore[name-defined]
        back_populates="subscription", lazy="noload"
    )
