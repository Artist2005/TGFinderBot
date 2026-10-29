from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Глобальна конфігурація застосунку через змінні оточення (.env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Telegram Bot ---
    bot_token: SecretStr = Field(..., alias="BOT_TOKEN", description="Токен Telegram-бота від @BotFather")

    # --- Database (SQLite by default or PostgreSQL Async) ---
    database_url: str = Field(
        default="sqlite+aiosqlite:///./price_tracker.db",
        alias="DATABASE_URL",
        description="DSN підключення до БД (sqlite+aiosqlite:///./price_tracker.db або postgresql+asyncpg://...)",
    )
    db_echo: bool = Field(default=False, alias="DB_ECHO")
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")

    # --- Партнерська мережа (CPA / Affiliate) ---
    affiliate_subid_key: str = Field(
        default="",
        alias="AFFILIATE_SUBID_KEY",
        description="Ключ або ідентифікатор партнера (SalesDoubler / Admitad)",
    )
    affiliate_deeplink_base: str | None = Field(
        default=None,
        alias="AFFILIATE_DEEPLINK_BASE",
        description="Базовий URL CPA-мережі (наприклад, https://rdr.salesdoubler.com.ua/in/offer/1234/)",
    )

    # --- Промокоди для активації довічного тарифу PRO ---
    lifetime_promo_codes: str = Field(
        default="ROMAN_PRO,CLIPSNAG_VIP,VIP2026,FOREVER_PRO",
        alias="LIFETIME_PROMO_CODES",
        description="Список промокодів через кому, які відкривають довічний план ⭐️ PRO",
    )

    # --- Бізнес-логіка та ліміти Freemium / Pro ---
    free_max_products: int = Field(default=3, alias="FREE_MAX_PRODUCTS")
    pro_max_products: int = Field(default=50, alias="PRO_MAX_PRODUCTS")
    pro_price_stars: int = Field(default=40, alias="PRO_PRICE_STARS")
    pro_duration_days: int = Field(default=30, alias="PRO_DURATION_DAYS")

    @property
    def valid_promo_codes(self) -> set[str]:
        """Повертає множину активних промокодів у верхньому регістрі для відкриття довічного PRO."""
        result: set[str] = set()
        for part in (self.lifetime_promo_codes or "").replace(";", ",").split(","):
            clean = part.strip().upper()
            if clean:
                result.add(clean)
        return result

    # --- Інтервали перевірки цін (Free: раз на добу = 24 год, PRO: кожні 12 год = 720 хв) ---
    free_check_interval_hours: int = Field(default=24, alias="FREE_CHECK_INTERVAL_HOURS")
    pro_check_interval_minutes: int = Field(default=720, alias="PRO_CHECK_INTERVAL_MINUTES")

    # --- Налаштування HTTP-скрапера ---
    scraper_timeout_seconds: float = Field(default=15.0, alias="SCRAPER_TIMEOUT_SECONDS")
    scraper_max_retries: int = Field(default=3, alias="SCRAPER_MAX_RETRIES")
    scraper_concurrency_limit: int = Field(default=5, alias="SCRAPER_CONCURRENCY_LIMIT")


settings = Settings()
