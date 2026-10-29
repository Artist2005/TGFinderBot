from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup
import httpx

from config import settings
from services.scraper.base import (
    BaseScraper,
    ScrapedProduct,
    compute_backoff_with_jitter,
    is_antibot_challenge,
)
from services.scraper.exceptions import (
    ParsingStructureError,
    ProductNotFoundError,
    ProductOutOfStockError,
    RateLimitOrBlockedError,
)
from services.scraper.normalizer import normalize_url, validate_url_ssrf_safe

logger = logging.getLogger(__name__)

ROZETKA_GOODS_ID_RE = re.compile(r"/p(\d+)(?:/|$)", re.IGNORECASE)
PRICE_CLEAN_RE = re.compile(r"[^\d.,]")

# Пул реальних популярних товарів електроніки/живлення на Rozetka для живого сканування акцій
LIVE_RADAR_GOODS_IDS: tuple[str, ...] = (
    "399232959",  # Зарядна станція EcoFlow DELTA 2 1024Wh | 1800W (ZMR330-EU)
    "528525849",  # Зарядна станція EcoFlow DELTA 3 Plus
    "451615640",  # Зарядна станція EcoFlow DELTA 2 Max (2016 Вт·год)
    "362525085",  # Зарядна станція EcoFlow DELTA 2 (1024 Вт·год)
    "549794964",  # Зарядна станція EcoFlow DELTA 3 / 1800 Вт
    "472783869",  # Зарядна станція EcoFlow DELTA 3 1500 LiFePO4
    "578357452",  # Зарядна станція EcoFlow DELTA 3 Classic
    "460418169",  # Навушники Google Pixel Buds Pro Charcoal
    "588452716",  # Ноутбук ASUS TUF Gaming F16
    "575379307",  # Навушники Samsung Galaxy Buds4 Black
    "582390196",  # Мобільний телефон Samsung Galaxy A57 5G 8/256GB
    "582390049",  # Мобільний телефон Samsung Galaxy A37 5G 8/256GB
)


class RozetkaScraper(BaseScraper):
    """
    Каскадний асинхронний парсер для маркетплейсу Rozetka.

    Порядок витягування даних (Multi-tier Fallback Strategy):
      1. Внутрішні JSON API Rozetka (`product-api` та `common-api/v2/goods/get-price`).
      2. Мікророзмітка Schema.org (`<script type="application/ld+json">`) на сторінці товару.
      3. Метатеги OpenGraph (`og:price:amount`, `product:price:amount`, `og:title`, `og:image`).
      4. Прямий парсинг DOM-дерева через CSS-селектори (`p.product-price__big`, `h1.product__title`).
    """

    marketplace_name = "rozetka"

    PRODUCT_API_URL = (
        "https://product-api.rozetka.com.ua/v4/goods/get-main"
        "?front-type=xl&country=UA&lang=ua&goodsId={goods_id}"
    )
    PRICE_API_URL = "https://common-api.rozetka.com.ua/v2/goods/get-price/?id={goods_id}&lang=ua"

    @staticmethod
    def extract_goods_id(url: str) -> str | None:
        """Витягує числовий ID товару Rozetka з URL (наприклад, /p326094910/ -> '326094910')."""
        match = ROZETKA_GOODS_ID_RE.search(url)
        return match.group(1) if match else None

    @staticmethod
    def _parse_decimal_price(raw_value: Any) -> Decimal | None:
        """Безпечно конвертує рядок або число у Decimal."""
        if raw_value is None:
            return None
        if isinstance(raw_value, (int, float, Decimal)):
            val = Decimal(str(raw_value))
            return val.quantize(Decimal("0.01")) if val > 0 else None

        cleaned = PRICE_CLEAN_RE.sub("", str(raw_value)).replace(",", ".")
        if not cleaned:
            return None
        try:
            val = Decimal(cleaned)
            return val.quantize(Decimal("0.01")) if val > 0 else None
        except InvalidOperation:
            return None

    async def _request_with_retries(
        self,
        url: str,
        *,
        accept_json: bool = False,
        referer: str | None = None,
    ) -> httpx.Response:
        """Виконує HTTP GET-запит із ротацією заголовків, антибот-детекцією та Exponential Backoff + Jitter."""
        validate_url_ssrf_safe(url)
        max_retries = max(1, settings.scraper_max_retries)
        last_exc: Exception | None = None

        for attempt in range(1, max_retries + 1):
            headers = self.build_headers(accept_json=accept_json, referer=referer)
            try:
                response = await self._client.get(url, headers=headers, follow_redirects=True)
                validate_url_ssrf_safe(str(response.url))
                if response.status_code == 404:
                    raise ProductNotFoundError(f"Товар за посиланням {url} не знайдено (404).")
                if response.status_code in (403, 429, 503) or is_antibot_challenge(
                    response.text, response.status_code, response.headers
                ):
                    logger.warning(
                        "Rozetka відповіла антибот-статусом %s для %s (спроба %d/%d)",
                        response.status_code,
                        url,
                        attempt,
                        max_retries,
                    )
                    if attempt == max_retries:
                        raise RateLimitOrBlockedError(
                            f"Запит заблоковано антибот-захистом (HTTP {response.status_code})."
                        )
                    await asyncio.sleep(compute_backoff_with_jitter(attempt))
                    continue

                response.raise_for_status()
                return response
            except (ProductNotFoundError, RateLimitOrBlockedError):
                raise
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "Помилка мережі при зверненні до %s (спроба %d/%d): %s",
                    url,
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(compute_backoff_with_jitter(attempt))

        raise ParsingStructureError(f"Не вдалося отримати відповідь від Rozetka: {last_exc}")

    async def _try_internal_api(self, goods_id: str, canonical_url: str) -> ScrapedProduct | None:
        """
        Рівень 1: Спроба отримати повні дані через внутрішній JSON API Rozetka.
        """
        api_url = self.PRODUCT_API_URL.format(goods_id=goods_id)
        try:
            resp = await self._request_with_retries(
                api_url,
                accept_json=True,
                referer=canonical_url,
            )
            payload = resp.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                return None

            title = str(data.get("title") or "").strip()
            price = self._parse_decimal_price(data.get("price"))
            old_price = self._parse_decimal_price(data.get("old_price"))
            sell_status = str(data.get("sell_status") or "").lower()
            is_available = sell_status in {"available", "limited"}

            image_url: str | None = None
            images = data.get("images")
            if isinstance(images, list) and images:
                first_img = images[0]
                if isinstance(first_img, dict):
                    orig = first_img.get("original") or first_img.get("base_action") or first_img.get("preview")
                    if isinstance(orig, dict):
                        image_url = orig.get("url")
            if not image_url and isinstance(data.get("image_main"), str):
                image_url = data["image_main"]

            # Якщо в get-main немає ціни, додатково запитуємо швидкий ендпоінт get-price
            if price is None:
                price_resp = await self._request_with_retries(
                    self.PRICE_API_URL.format(goods_id=goods_id),
                    accept_json=True,
                    referer=canonical_url,
                )
                price_data = price_resp.json()
                if isinstance(price_data, dict):
                    price = self._parse_decimal_price(price_data.get("price"))
                    old_price = old_price or self._parse_decimal_price(price_data.get("old_price"))
                    status = str(price_data.get("sell_status") or price_data.get("status") or "").lower()
                    if status:
                        is_available = status in {"available", "limited", "active"}

            if title and price is not None:
                if old_price is not None and old_price > 0:
                    if old_price < price:
                        price, old_price = old_price, price
                    elif old_price == price:
                        old_price = None
                return ScrapedProduct(
                    url=canonical_url,
                    title=title,
                    price=price,
                    currency="UAH",
                    image_url=image_url,
                    marketplace=self.marketplace_name,
                    is_available=is_available,
                    old_price=old_price,
                )
        except ProductNotFoundError:
            raise
        except Exception as exc:
            logger.debug("Внутрішній API Rozetka не повернув повних даних для ID %s: %s", goods_id, exc)

        return None

    def _parse_json_ld(self, soup: BeautifulSoup, canonical_url: str) -> ScrapedProduct | None:
        """
        Рівень 2: Парсинг структурованих даних Schema.org (JSON-LD `<script type="application/ld+json">`).
        """
        scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
        for script in scripts:
            raw_text = (script.string or script.get_text() or "").strip()
            if not raw_text:
                continue
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                continue

            candidates: list[dict[str, Any]] = []
            if isinstance(parsed, dict):
                if isinstance(parsed.get("@graph"), list):
                    candidates.extend(item for item in parsed["@graph"] if isinstance(item, dict))
                else:
                    candidates.append(parsed)
            elif isinstance(parsed, list):
                candidates.extend(item for item in parsed if isinstance(item, dict))

            for item in candidates:
                item_type = str(item.get("@type") or "").lower()
                if "product" not in item_type:
                    continue

                title = str(item.get("name") or "").strip()
                image_raw = item.get("image")
                image_url: str | None = None
                if isinstance(image_raw, str):
                    image_url = image_raw
                elif isinstance(image_raw, list) and image_raw:
                    first = image_raw[0]
                    image_url = first if isinstance(first, str) else (first.get("url") if isinstance(first, dict) else None)
                elif isinstance(image_raw, dict):
                    image_url = image_raw.get("url")

                offers = item.get("offers")
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if not isinstance(offers, dict):
                    continue

                price = self._parse_decimal_price(offers.get("price") or offers.get("lowPrice"))
                currency = str(offers.get("priceCurrency") or "UAH").upper()
                availability = str(offers.get("availability") or "").lower()
                is_available = "outofstock" not in availability and "discontinued" not in availability

                if title and price is not None:
                    old_price: Decimal | None = None
                    old_el = soup.select_one("p.product-price__small, .product-prices__small")
                    if old_el:
                        old_price = self._parse_decimal_price(old_el.get_text(strip=True))
                    big_el = soup.select_one("p.product-price__big, .product-prices__big")
                    if big_el:
                        dom_price = self._parse_decimal_price(big_el.get_text(strip=True))
                        if dom_price and dom_price != price:
                            old_price = max(price, dom_price)
                            price = min(price, dom_price)
                    if old_price is not None and old_price <= price:
                        old_price = None

                    return ScrapedProduct(
                        url=canonical_url,
                        title=title,
                        price=price,
                        currency=currency,
                        image_url=image_url,
                        marketplace=self.marketplace_name,
                        is_available=is_available,
                        old_price=old_price,
                    )
        return None

    def _parse_opengraph_and_dom(self, soup: BeautifulSoup, canonical_url: str) -> ScrapedProduct:
        """
        Рівень 3 та 4: Комбінований парсинг OpenGraph метатегів та HTML CSS-селекторів.
        """
        # 1. Назва товару
        title: str | None = None
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            title = str(og_title["content"]).strip()

        if not title:
            for selector in ("h1.product__title", "h1.title__font", "h1.product-about__title", "h1"):
                el = soup.select_one(selector)
                if el and el.get_text(strip=True):
                    title = el.get_text(strip=True)
                    break

        # 2. Зображення товару
        image_url: str | None = None
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image and og_image.get("content"):
            image_url = str(og_image["content"]).strip()

        if not image_url:
            for selector in ("img.picture-container__picture", ".main-slider__item img", "rz-gallery-main-content-image img"):
                img_el = soup.select_one(selector)
                if img_el and img_el.get("src"):
                    image_url = str(img_el["src"]).strip()
                    break

        # 3. Ціна товару (OpenGraph / Microdata / CSS)
        price: Decimal | None = None
        for meta_prop in ("product:price:amount", "og:price:amount"):
            meta_price = soup.find("meta", attrs={"property": meta_prop})
            if meta_price and meta_price.get("content"):
                price = self._parse_decimal_price(meta_price["content"])
                if price is not None:
                    break

        if price is None:
            itemprop_price = soup.find(attrs={"itemprop": "price"})
            if itemprop_price:
                raw = itemprop_price.get("content") or itemprop_price.get_text(strip=True)
                price = self._parse_decimal_price(raw)

        old_price: Decimal | None = None
        if price is None:
            for selector in (
                "p.product-price__big",
                ".product-prices__big",
                "[data-testid='product-price']",
                ".product-price__wrap p",
            ):
                price_el = soup.select_one(selector)
                if price_el:
                    price = self._parse_decimal_price(price_el.get_text(strip=True))
                    if price is not None:
                        break

        old_price_el = soup.select_one("p.product-price__small, .product-prices__small")
        if old_price_el:
            old_price = self._parse_decimal_price(old_price_el.get_text(strip=True))

        # 4. Перевірка статусу наявності
        status_el = soup.select_one(".product-statuses__item, .status-label")
        status_text = status_el.get_text(strip=True).lower() if status_el else ""
        out_of_stock_markers = ("немає в наявності", "закінчився", "знято з виробництва", "нет в наличии")
        is_available = not any(marker in status_text for marker in out_of_stock_markers)

        if not is_available and price is None:
            raise ProductOutOfStockError(f"Товар «{title or canonical_url}» відсутній у наявності, ціна недоступна.")

        if not title or price is None:
            raise ParsingStructureError(
                f"Не вдалося витягнути назву або ціну зі сторінки Rozetka: {canonical_url}"
            )

        return ScrapedProduct(
            url=canonical_url,
            title=title,
            price=price,
            currency="UAH",
            image_url=image_url,
            marketplace=self.marketplace_name,
            is_available=is_available,
            old_price=old_price,
        )

    async def fetch_product(self, url: str) -> ScrapedProduct:
        """
        Головний публічний метод скрапера Rozetka.
        Послідовно застосовує рівні витягування даних для максимальної відмовостійкості.
        """
        goods_id = self.extract_goods_id(url)

        # Крок 1: Внутрішній API Rozetka (найшвидший і найточніший спосіб)
        if goods_id:
            api_product = await self._try_internal_api(goods_id, url)
            if api_product is not None:
                if not api_product.is_available and api_product.price <= 0:
                    raise ProductOutOfStockError(f"Товар «{api_product.title}» немає в наявності.")
                return api_product

        # Крок 2: Завантаження HTML-сторінки товару
        response = await self._request_with_retries(url, accept_json=False)
        html = response.text

        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # Крок 3: Schema.org JSON-LD
        json_ld_product = self._parse_json_ld(soup, url)
        if json_ld_product is not None:
            return json_ld_product

        # Крок 4: OpenGraph + CSS-селектори
        return self._parse_opengraph_and_dom(soup, url)

    async def discover_live_deals(
        self,
        *,
        search_query: str | None = None,
        limit: int = 8,
    ) -> list[ScrapedProduct]:
        """
        Шукає реальні живі знижки (де `old_price > price` і товар є в наявності)
        безпосередньо через API Rozetka в режимі реального часу.
        """
        candidate_ids: list[str] = list(LIVE_RADAR_GOODS_IDS)
        headers = self.build_headers(accept_json=True, referer="https://rozetka.com.ua/ua/")

        if search_query and len(search_query.strip()) >= 2:
            try:
                search_resp = await self._client.get(
                    "https://search.rozetka.com.ua/ua/search/api/v6/",
                    params={
                        "front-type": "xl",
                        "country": "UA",
                        "lang": "ua",
                        "text": search_query.strip(),
                        "redirected": "1",
                    },
                    headers=headers,
                    timeout=2.5,
                )
                if search_resp.status_code == 200:
                    goods = (search_resp.json().get("data") or {}).get("goods") or []
                    for g in goods[:10]:
                        gid = str(g.get("id") or "").strip()
                        if gid.isdigit() and gid not in candidate_ids:
                            candidate_ids.insert(0, gid)
            except Exception as exc:
                logger.debug("Live search query '%s' skipped: %s", search_query, exc)

        async def _fetch_one(gid: str) -> ScrapedProduct | None:
            try:
                resp = await self._client.get(
                    self.PRODUCT_API_URL.format(goods_id=gid),
                    headers=headers,
                    timeout=3.5,
                )
                if resp.status_code != 200:
                    return None
                data = (resp.json() or {}).get("data")
                if not isinstance(data, dict):
                    return None
                sell_status = str(data.get("sell_status") or "").lower()
                if sell_status not in {"available", "limited"}:
                    return None
                price = self._parse_decimal_price(data.get("price"))
                old_price = self._parse_decimal_price(data.get("old_price"))
                title = str(data.get("title") or "").strip()
                raw_href = str(data.get("href") or f"https://rozetka.com.ua/ua/p{gid}/").strip()
                if not title or price is None or old_price is None:
                    return None
                if old_price <= price or price < Decimal("300"):
                    return None
                drop_pct = float((old_price - price) / old_price * Decimal("100"))
                if drop_pct < 4.0 or drop_pct > 65.0:
                    return None
                clean_u, _ = normalize_url(raw_href)
                img_url: str | None = None
                if isinstance(data.get("image_main"), str):
                    img_url = data["image_main"]
                return ScrapedProduct(
                    url=clean_u,
                    title=title,
                    price=price,
                    currency="UAH",
                    image_url=img_url,
                    marketplace=self.marketplace_name,
                    is_available=True,
                    old_price=old_price,
                )
            except Exception:
                return None

        results = await asyncio.gather(*[_fetch_one(gid) for gid in candidate_ids[:18]])
        valid_deals: list[ScrapedProduct] = [item for item in results if item is not None]
        valid_deals.sort(
            key=lambda d: float(((d.old_price or d.price) - d.price) / (d.old_price or d.price)),
            reverse=True,
        )
        return valid_deals[:limit]
