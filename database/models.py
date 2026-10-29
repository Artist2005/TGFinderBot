from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class User(Base):
    """Модель користувача Telegram."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str] = mapped_column(String(8), default="uk", server_default="uk", nullable=False)
    is_pro: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    pro_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    dnd_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    referred_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    referrals_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Зв'язки
    subscriptions: Mapped[list[UserProduct]] = relationship(
        "UserProduct",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def has_active_pro(self) -> bool:
        """Перевіряє, чи активна Pro-підписка станом на поточний момент UTC."""
        if not self.is_pro:
            return False
        if self.pro_until is None:
            return True
        now = datetime.now(timezone.utc)
        pro_until_utc = (
            self.pro_until
            if self.pro_until.tzinfo is not None
            else self.pro_until.replace(tzinfo=timezone.utc)
        )
        return pro_until_utc > now


class Product(Base):
    """Модель товару на маркетплейсі."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="UAH", server_default="UAH", nullable=False)
    marketplace: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    requires_verification: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Зв'язки
    subscribers: Mapped[list[UserProduct]] = relationship(
        "UserProduct",
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    price_history: Mapped[list[PriceHistory]] = relationship(
        "PriceHistory",
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PriceHistory.timestamp.asc()",
    )


class UserProduct(Base):
    """Асоціативна таблиця M:N між користувачами та товарами з підтримкою target_price."""

    __tablename__ = "user_products"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_user_products_user_product"),
        Index("ix_user_products_user_id", "user_id"),
        Index("ix_user_products_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Зв'язки
    user: Mapped[User] = relationship("User", back_populates="subscriptions")
    product: Mapped[Product] = relationship("Product", back_populates="subscribers")


class PriceHistory(Base):
    """Історія змін ціни на товар."""

    __tablename__ = "price_history"
    __table_args__ = (
        Index("ix_price_history_product_timestamp", "product_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Зв'язки
    product: Mapped[Product] = relationship("Product", back_populates="price_history")


class ProcessedPayment(Base):
    """
    Журнал ідемпотентності платежів Telegram Stars.
    Гарантує, що повторний апдейт із тим самим telegram_payment_charge_id
    ніколи не нарахує підписку двічі.
    """

    __tablename__ = "processed_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    charge_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_days: Mapped[int] = mapped_column(Integer, nullable=False)
    stars_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
