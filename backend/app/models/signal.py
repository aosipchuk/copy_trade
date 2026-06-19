from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traders.id"), nullable=False
    )
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)  # OPEN|CLOSE|UPDATE
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str | None] = mapped_column(Text)  # long|short
    size: Mapped[float | None] = mapped_column(Numeric(20, 8))
    entry_price: Mapped[float | None] = mapped_column(Numeric(20, 4))
    leverage: Mapped[float | None] = mapped_column(Numeric(5, 2))
    detected_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    trader: Mapped["Trader"] = relationship(  # type: ignore[name-defined]
        back_populates="signals"
    )
    trades: Mapped[list["UserTrade"]] = relationship(  # type: ignore[name-defined]
        back_populates="signal", lazy="noload"
    )
