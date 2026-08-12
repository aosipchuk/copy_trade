from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint(
            "engine_version IN (1, 2)",
            name="ck_signals_engine_version",
        ),
        CheckConstraint(
            "dispatch_status IN ('pending', 'accepted', 'dispatched', "
            "'skipped_legacy', 'failed')",
            name="ck_signals_dispatch_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("traders.id"), nullable=False
    )
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)  # OPEN|CLOSE|UPDATE
    coin: Mapped[str] = mapped_column(Text, nullable=False)
    dex: Mapped[str] = mapped_column(Text, default="", server_default="")
    side: Mapped[str | None] = mapped_column(Text)  # long|short
    size: Mapped[float | None] = mapped_column(Numeric(20, 8))
    entry_price: Mapped[float | None] = mapped_column(Numeric(20, 4))
    leverage: Mapped[float | None] = mapped_column(Numeric(5, 2))
    previous_size: Mapped[float | None] = mapped_column(Numeric(30, 12))
    target_size: Mapped[float | None] = mapped_column(Numeric(30, 12))
    delta_size: Mapped[float | None] = mapped_column(Numeric(30, 12))
    snapshot_version: Mapped[int | None] = mapped_column(BigInteger)
    engine_version: Mapped[int] = mapped_column(default=1, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True)
    dispatch_status: Mapped[str] = mapped_column(
        Text, default="pending", server_default="pending", nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    trader: Mapped["Trader"] = relationship(  # type: ignore[name-defined]
        back_populates="signals"
    )
    trades: Mapped[list["UserTrade"]] = relationship(  # type: ignore[name-defined]
        back_populates="signal", lazy="noload"
    )
