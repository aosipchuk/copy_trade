from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, LargeBinary, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str | None] = mapped_column(Text)
    hl_address: Mapped[str | None] = mapped_column(Text)
    portfolio_stop_loss_pct: Mapped[float | None] = mapped_column(
        Numeric(5, 2), server_default="20.0"
    )
    builder_fee_approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    agents: Mapped[list["UserAgent"]] = relationship(
        back_populates="user", lazy="noload"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(  # type: ignore[name-defined]
        back_populates="user", lazy="noload"
    )


class UserAgent(Base):
    __tablename__ = "user_agents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    agent_address: Mapped[str] = mapped_column(Text, nullable=False)
    agent_key_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    setup_nonce: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="agents")
