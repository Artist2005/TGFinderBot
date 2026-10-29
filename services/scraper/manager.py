from __future__ import annotations

import logging

import httpx

from config import settings
from services.scraper.base import BaseScraper, ScrapedProduct
from services.scraper.exceptions import (
    InvalidUrlError,
    ProductNotFoundError,
    ProductOutOfStockError,
    ScraperError,
    SSRFBlockedError,
)
from services.scraper.normalizer import normalize_url, verify_url_dns_ssrf_safe
from services.scraper.rozetka import RozetkaScraper
from services.scraper.universal import UniversalMarketplaceScraper

logger = logging.getLogger(__name__)


class ScraperService:
    """
    Фасад та реєстр парсерів для всіх підтримуваних маркетплейсів:
    - Rozetka (спеціалізований подвійний парсер: внутрішній API + SSR HTML fallback)
    - UniversalMarketplaceScraper (Amazon, eBay, AliExpress, Walmart, Allegro, Prom.ua,
      Епіцентр, Comfy, Алло, Фокстрот, MOYO, Цитрус, OLX, MAKEUP, Kasta, Steam, Shopify тощо)
    - Вбудований SSRF-захист (перевірка схеми, приватних підмереж та DNS Rebinding)
    - Long-lived httpx.AsyncClient із пулом з'єднань та чіткими таймаутами (connect/read/write/pool)
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._scrapers: dict[str, BaseScraper] = {}
        self._universal_scraper: UniversalMarketplaceScraper | None = None

    async def start(self) -> None:
        """Ініціалізує спільний асинхронний HTTP-клієнт та реєструє парсери."""
        if self._client is not None:
            return

        self._client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
            timeout=httpx.Timeout(
                connect=5.0,
                read=float(settings.scraper_timeout_seconds),
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(
                max_connections=60,
                max_keepalive_connections=25,
                keepalive_expiry=30.0,
            ),
            follow_redirects=True,
        )
        self._scrapers = {
            "rozetka": RozetkaScraper(self._client),
        }
        self._universal_scraper = UniversalMarketplaceScraper(self._client)
        logger.info(
            "ScraperService запущено (Rozetka + Universal Multi-Marketplace + SSRF Guard)."
        )

    async def close(self) -> None:
        """Коректно закриває HTTP-клієнт під час зупинки бота."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._scrapers.clear()
            self._universal_scraper = None
            logger.info("ScraperService HTTP-клієнт закрито.")

    async def fetch_product(self, raw_url: str) -> ScrapedProduct:
        """
        Приймає сире посилання, очищує його, виконує глибоку SSRF-перевірку (включно з DNS),
        визначає маркетплейс та викликає спеціалізований або універсальний скрапер.
        """
        if self._client is None or self._universal_scraper is None:
            await self.start()

        clean_url, marketplace = normalize_url(raw_url)
        await verify_url_dns_ssrf_safe(clean_url)

        scraper = self._scrapers.get(marketplace)
        if scraper is not None:
            try:
                return await scraper.fetch_product(clean_url)
            except (
                SSRFBlockedError,
                InvalidUrlError,
                ProductNotFoundError,
                ProductOutOfStockError,
            ):
                raise
            except ScraperError as exc:
                logger.warning(
                    "Спеціалізований скрапер %s не зміг обробити %s (%s), пробуємо універсальний парсер...",
                    marketplace,
                    clean_url,
                    exc,
                )
                assert self._universal_scraper is not None
                return await self._universal_scraper.fetch_product(clean_url)

        assert self._universal_scraper is not None
        return await self._universal_scraper.fetch_product(clean_url)

    async def scrape(self, raw_url: str) -> ScrapedProduct:
        """Головний метод парсингу посилання (сумісний із links.py, callbacks.py, scheduler.py)."""
        return await self.fetch_product(raw_url)

    async def scrape_url(self, raw_url: str) -> ScrapedProduct:
        """Сумісний аліас для fetch_product."""
        return await self.fetch_product(raw_url)

    async def discover_live_deals(
        self,
        *,
        search_query: str | None = None,
        limit: int = 8,
    ) -> list[ScrapedProduct]:
        """Шукає реальні живі знижки через RozetkaScraper у режимі реального часу."""
        if self._client is None:
            await self.start()
        rz = self._scrapers.get("rozetka")
        if isinstance(rz, RozetkaScraper):
            return await rz.discover_live_deals(search_query=search_query, limit=limit)
        return []


# Глобальний синглтон-екземпляр сервісу парсингу
scraper_service = ScraperService()
