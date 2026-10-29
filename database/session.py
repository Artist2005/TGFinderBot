from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings
from database.base import Base
import database.models  # noqa: F401 — реєстрація всіх моделей у Base.metadata

_IS_SQLITE = settings.database_url.startswith("sqlite")

_engine_kwargs: dict[str, object] = {
    "echo": settings.db_echo,
}
if not _IS_SQLITE:
    _engine_kwargs.update(
        {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_pre_ping": True,
            "pool_recycle": 1800,
        }
    )

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    **_engine_kwargs,
)

if _IS_SQLITE:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
        """
        Оптимізує локальну базу SQLite для мінімального споживання диска та RAM на сервері:
        - foreign_keys=ON: каскадне видалення історії цін при видаленні товару
        - journal_mode=WAL: швидкий запис без блокування читання
        - synchronous=NORMAL: економія I/O диска
        - temp_store=MEMORY & cache_size=-4000: ліміт кешу SQLite до ~4 МБ RAM
        - auto_vacuum=INCREMENTAL: автоматичне повернення звільненого місця на диск
        """
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.execute("PRAGMA temp_store = MEMORY;")
        cursor.execute("PRAGMA cache_size = -4000;")
        cursor.execute("PRAGMA auto_vacuum = INCREMENTAL;")
        cursor.close()

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Асинхронний контекстний менеджер для безпечної роботи з сесією БД."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Створює таблиці та автоматично додає нові комерційні колонки без втрати даних."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _migrate_columns(sync_conn: object) -> None:
            insp = inspect(sync_conn)
            if insp.has_table("users"):
                user_cols = {col["name"] for col in insp.get_columns("users")}
                if "language" not in user_cols:
                    sync_conn.execute(
                        text("ALTER TABLE users ADD COLUMN language VARCHAR(8) DEFAULT 'uk' NOT NULL")
                    )
                if "notifications_enabled" not in user_cols:
                    sync_conn.execute(
                        text("ALTER TABLE users ADD COLUMN notifications_enabled BOOLEAN DEFAULT TRUE NOT NULL")
                    )
                if "dnd_enabled" not in user_cols:
                    sync_conn.execute(
                        text("ALTER TABLE users ADD COLUMN dnd_enabled BOOLEAN DEFAULT FALSE NOT NULL")
                    )
                if "referred_by" not in user_cols:
                    sync_conn.execute(text("ALTER TABLE users ADD COLUMN referred_by BIGINT NULL"))
                if "referrals_count" not in user_cols:
                    sync_conn.execute(
                        text("ALTER TABLE users ADD COLUMN referrals_count INTEGER DEFAULT 0 NOT NULL")
                    )

            if insp.has_table("products"):
                prod_cols = {col["name"] for col in insp.get_columns("products")}
                if "requires_verification" not in prod_cols:
                    sync_conn.execute(
                        text(
                            "ALTER TABLE products ADD COLUMN requires_verification BOOLEAN DEFAULT FALSE NOT NULL"
                        )
                    )

        await conn.run_sync(_migrate_columns)


async def close_db() -> None:
    """Коректне закриття пулу з'єднань SQLAlchemy при зупинці бота (Graceful Shutdown)."""
    await engine.dispose()
