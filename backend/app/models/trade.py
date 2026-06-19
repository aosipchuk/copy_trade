from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class UserTrade(Base):
    __tablename__ = "user_trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("subscriptions.id"), nullable=False
    )
    signal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("signals.id"), nullable=False
    )
    hl_order_id: Mapped[int | None] = mapped_column(BigInteger)
    coin: Mapped[str | None] = mapped_column(Text)
    side: Mapped[str | None] = mapped_column(Text)  # long|short
    size: Mapped[float | None] = mapped_column(Numeric(20, 8))
    price: Mapped[float | None] = mapped_column(Numeric(20, 4))
    trade_type: Mapped[str | None] = mapped_column(Text)  # 'open' | 'close'
    realized_pnl: Mapped[float | None] = mapped_column(Numeric(20, 4))
    # pending|filled|failed|cancelled
    status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    error_msg: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(server_default=func.now())

    subscription: Mapped["Subscription"] = relationship(  # type: ignore[name-defined]
        back_populates="trades"
    )
    signal: Mapped["Signal"] = relationship(  # type: ignore[name-defined]
        back_populates="trades"
    )
