from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

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
from services.scraper.normalizer import (
    detect_default_currency,
    detect_marketplace,
    validate_url_ssrf_safe,
)

logger = logging.getLogger(__name__)

# Підтримує як європейський формат (14 999,00 / 1.299,99), так і американський (1,299.99 / 29.99)
PRICE_NUMBER_RE = re.compile(r"(\d[\d\s\u00a0\u202f.,]*\d|\d+)")
INLINE_JSON_PRICE_RE = re.compile(
    r'"(?:price|current_price|special_price|final_price|discounted_price|price_uah|salePrice|amount|priceAmount)"\s*:\s*"?(\d+(?:[.,]\d{1,2})?)"?',
    re.IGNORECASE,
)
INLINE_JSON_OLD_PRICE_RE = re.compile(
    r'"(?:old_price|regular_price|full_price|price_old|compare_at_price|listPrice|wasPrice|originalPrice)"\s*:\s*"?(\d+(?:[.,]\d{1,2})?)"?',
    re.IGNORECASE,
)
INLINE_JSON_CURRENCY_RE = re.compile(
    r'"(?:priceCurrency|currency|currencyCode|currency_code)"\s*:\s*"([A-Z]{3})"',
    re.IGNORECASE,
)

CURRENCY_SYMBOL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("₴", "UAH"),
    ("грн", "UAH"),
    ("uah", "UAH"),
    ("zł", "PLN"),
    ("pln", "PLN"),
    ("kč", "CZK"),
    ("czk", "CZK"),
    ("£", "GBP"),
    ("gbp", "GBP"),
    ("€", "EUR"),
    ("eur", "EUR"),
    ("$", "USD"),
    ("usd", "USD"),
    ("¥", "JPY"),
    ("₸", "KZT"),
    ("₺", "TRY"),
)


class UniversalMarketplaceScraper(BaseScraper):
    """
    Універсальний багатоплатформний скрапер для світових та українських маркетплейсів:
    Amazon, eBay, AliExpress, Walmart, BestBuy, Target, Newegg, Allegro, Ceneo, x-kom,
    MediaMarkt, Otto, Zalando, ASOS, iHerb, Steam, Prom.ua, Епіцентр, Comfy, Алло,
    Фокстрот, MOYO, Цитрус, MAKEUP, Kasta, OLX, а також будь-яких магазинів на
    Shopify, WooCommerce, Magento, OpenCart тощо.

    Каскад витягування даних:
      1. Shopify JSON API (`/products/<handle>.json`) для миттєвого отримання даних з Shopify-магазинів.
      2. Schema.org JSON-LD (`Product`, `ProductGroup`, `Offer`, `AggregateOffer`).
      3. Мікродані (`itemprop="price"`) та OpenGraph (`product:price:amount`, `og:price:amount`).
      4. Специфічні CSS-селектори світових та українських маркетплейсів (Amazon, eBay, Allegro, Walmart, Prom, Epicentr, Comfy, Allo, Foxtrot, OLX, Steam тощо).
      5. Вбудовані SPA-стейти (`__NEXT_DATA__`, `__INITIAL_STATE__`, inline JSON).
    """

    marketplace_name = "universal"

    # Спеціалізовані селектори ціни для популярних світових та українських маркетплейсів
    PRICE_SELECTORS: tuple[str, ...] = (
        # Amazon (всі регіони)
        "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
        "#corePrice_feature_div .a-price .a-offscreen",
        "#apex_desktop .a-price .a-offscreen",
        ".a-price.a-text-price.a-size-medium.apexPriceToPay .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        ".a-price .a-offscreen",
        # eBay
        ".x-price-primary span.ux-textspans",
        "#prcIsum",
        "[itemprop='price']",
        # Walmart / BestBuy / Target / Newegg
        "[data-automation-id='product-price'] span",
        ".priceView-hero-price span[aria-hidden='true']",
        "[data-test='product-price']",
        ".price-current",
        # Allegro / Ceneo / x-kom / MediaMarkt
        "[data-box-name='price'] [aria-label]",
        "meta[itemprop='price']",
        "[data-name='productPrice']",
        "[data-test='product-price']",
        # AliExpress
        ".product-price-value",
        "[class*='price--current'] span",
        # Steam
        ".game_purchase_action .discount_final_price",
        ".game_purchase_action .game_purchase_price",
        # Prom.ua
        "[data-qaid='product_price']",
        "[data-qaid='price']",
        # Епіцентр (epicentrk.ua)
        ".p-price__main",
        "[data-product-price]",
        # Comfy
        ".price__current",
        ".gen-tab__price-current",
        # Алло (allo.ua)
        ".p-trade-price__current .sum",
        ".v-price-box__cur .sum",
        # Фокстрот (foxtrot.com.ua)
        ".card-price",
        ".product-box__main_price",
        # MOYO
        ".product_price_current",
        ".price_value",
        # Цитрус (ctrs.com.ua)
        ".price",
        "[data-price]",
        # OLX
        "[data-testid='ad-price-container'] h3",
        # Shopify / WooCommerce / MAKEUP / Kasta / Brain / Telemart / Універсальні
        ".price-item--sale",
        ".price-item--regular",
        ".woocommerce-Price-amount.amount",
        ".price_item",
        ".product-price__big",
        ".product-price__current",
        ".product-price",
        ".current-price",
        ".special-price .price",
        ".sale-price",
        "[data-testid='current-price']",
        "[data-auto-id='product-price']",
    )

    OLD_PRICE_SELECTORS: tuple[str, ...] = (
        # Amazon
        ".basisPrice .a-offscreen",
        ".a-price.a-text-price .a-offscreen",
        # eBay / Global
        ".x-additional-info .ux-textspans--STRIKETHROUGH",
        ".discount_original_price",
        ".price-item--regular s",
        # Українські маркетплейси
        "[data-qaid='old_price']",
        ".p-price__old",
        ".price__old",
        ".p-trade-price__old .sum",
        ".v-price-box__old .sum",
        ".card-price__old",
        ".product-price__small",
        ".old-price",
        "del .woocommerce-Price-amount",
        "del.price",
    )

    TITLE_SELECTORS: tuple[str, ...] = (
        "#productTitle",
        "h1.x-item-title__mainTitle",
        "#main-title",
        ".apphub_AppName",
        "[data-qaid='product_name']",
        "[data-testid='ad_title']",
        "h1.p-header__title",
        "h1.product__title",
        "h1.p-view__header-title",
        "h1.page-title",
        "h1[itemprop='name']",
        "h1",
    )

    IMAGE_SELECTORS: tuple[str, ...] = (
        "#landingImage",
        "#imgBlkFront",
        ".ux-image-carousel-item.active img",
        ".game_header_image_full",
        "[data-qaid='image_preview'] img",
        "img[itemprop='image']",
        ".picture-container__picture",
        ".product-gallery img",
        ".main-slider__item img",
        "meta[property='og:image']",
    )

    @staticmethod
    def _parse_decimal_price(raw_value: Any) -> Decimal | None:
        """
        Витягує числовий Decimal із довільного рядка ціни у будь-якому міжнародному форматі:
        - '14 999,00 ₴' -> 14999.00
        - '$1,299.99'   -> 1299.99
        - '1.299,99 €'  -> 1299.99
        - '49,99 zł'    -> 49.99
        """
        if raw_value is None:
            return None
        if isinstance(raw_value, (int, float, Decimal)):
            try:
                val = Decimal(str(raw_value)).quantize(Decimal("0.01"))
                return val if val > 0 else None
            except InvalidOperation:
                return None

        text = str(raw_value).strip()
        if not text:
            return None

        match = PRICE_NUMBER_RE.search(text)
        if not match:
            return None

        num_str = (
            match.group(1)
            .replace(" ", "")
            .replace("\u00a0", "")
            .replace("\u202f", "")
            .strip(".,")
        )
        if not num_str:
            return None

        # Розрізняємо роздільник тисяч і десяткову частину
        if "," in num_str and "." in num_str:
            if num_str.rfind(",") > num_str.rfind("."):
                # Європейський формат: 1.299,99 -> 1299.99
                num_str = num_str.replace(".", "").replace(",", ".")
            else:
                # Американський/Британський формат: 1,299.99 -> 1299.99
                num_str = num_str.replace(",", "")
        elif "," in num_str:
            parts = num_str.split(",")
            if len(parts[-1]) in (1, 2):
                # Десяткова кома: 49,99 -> 49.99
                num_str = "".join(parts[:-1]) + "." + parts[-1]
            else:
                # Роздільник тисяч: 1,299 -> 1299
                num_str = "".join(parts)
        elif "." in num_str:
            parts = num_str.split(".")
            if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
                # Роздільник тисяч крапкою: 1.299 -> 1299
                num_str = "".join(parts)

        try:
            val = Decimal(num_str).quantize(Decimal("0.01"))
            return val if val > 0 else None
        except InvalidOperation:
            return None

    def _detect_currency(
        self,
        soup: BeautifulSoup,
        html_text: str,
        url: str,
        marketplace: str,
        price_hint_text: str = "",
    ) -> str:
        """Автоматично визначає валюту товару (UAH, USD, EUR, PLN, GBP тощо)."""
        # 1. Метатеги валюти
        for attr_name in ("product:price:currency", "og:price:currency", "priceCurrency"):
            el = (
                soup.find("meta", attrs={"property": attr_name})
                or soup.find("meta", attrs={"itemprop": attr_name})
                or soup.find(attrs={"itemprop": attr_name})
            )
            if el:
                val = (el.get("content") or el.get_text(strip=True) or "").strip().upper()
                if len(val) == 3 and val.isalpha():
                    return val

        # 2. Символ валюти безпосередньо біля ціни
        hint_lower = price_hint_text.lower()
        if hint_lower:
            for token, curr_code in CURRENCY_SYMBOL_PATTERNS:
                if token in hint_lower:
                    return curr_code

        # 3. Вбудований JSON у сторінці
        json_curr = INLINE_JSON_CURRENCY_RE.search(html_text)
        if json_curr:
            return json_curr.group(1).upper()

        # 4. Дефолтна валюта за доменом / регіоном магазину
        return detect_default_currency(url, marketplace)

    async def _try_shopify_json(self, url: str, marketplace: str) -> ScrapedProduct | None:
        """
        Швидкий шлях для сотень тисяч міжнародних магазинів на базі Shopify
        (посилання вигляду `/products/<handle>`).
        """
        parsed = urlparse(url)
        if "/products/" not in parsed.path:
            return None

        base_path = parsed.path.rstrip("/")
        if base_path.endswith(".json"):
            json_url = url
        else:
            json_url = f"{parsed.scheme}://{parsed.netloc}{base_path}.json"

        try:
            resp = await self._client.get(json_url, headers=self.build_headers(accept_json=True), timeout=6.0)
            if resp.status_code != 200 or "application/json" not in resp.headers.get("content-type", ""):
                return None
            payload = resp.json()
            product_data = payload.get("product")
            if not isinstance(product_data, dict):
                return None

            title = str(product_data.get("title") or "").strip()
            variants = product_data.get("variants") or []
            if not title or not isinstance(variants, list) or not variants:
                return None

            first_var = variants[0]
            price = self._parse_decimal_price(first_var.get("price"))
            old_price = self._parse_decimal_price(first_var.get("compare_at_price"))
            if price is None:
                return None

            images = product_data.get("images") or []
            image_url = None
            if isinstance(images, list) and images and isinstance(images[0], dict):
                image_url = images[0].get("src")
            elif isinstance(product_data.get("image"), dict):
                image_url = product_data["image"].get("src")

            currency = detect_default_currency(url, marketplace)
            return ScrapedProduct(
                url=url,
                title=title,
                price=price,
                currency=currency,
                image_url=image_url,
                marketplace=marketplace,
                is_available=True,
                old_price=old_price if (old_price and old_price > price) else None,
            )
        except Exception:
            return None

    async def _request_html(self, url: str) -> str:
        """Виконує асинхронний запит із ротацією заголовків, антибот-детекцією та Exponential Backoff + Jitter."""
        validate_url_ssrf_safe(url)
        max_retries = max(1, settings.scraper_max_retries)
        last_exc: Exception | None = None

        for attempt in range(1, max_retries + 1):
            headers = self.build_headers(accept_json=False)
            try:
                response = await self._client.get(url, headers=headers, follow_redirects=True)
                validate_url_ssrf_safe(str(response.url))
                if response.status_code == 404:
                    raise ProductNotFoundError(f"Сторінку товару не знайдено (404): {url}")
                if response.status_code in (403, 429, 503) or is_antibot_challenge(
                    response.text, response.status_code, response.headers
                ):
                    if attempt == max_retries:
                        raise RateLimitOrBlockedError(
                            f"Магазин тимчасово обмежив доступ антибот-захистом (HTTP {response.status_code})."
                        )
                    await asyncio.sleep(compute_backoff_with_jitter(attempt))
                    continue
                response.raise_for_status()
                return response.text
            except (ProductNotFoundError, RateLimitOrBlockedError):
                raise
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(compute_backoff_with_jitter(attempt))

        raise ParsingStructureError(f"Помилка з'єднання з магазином: {last_exc}")

    def _extract_from_json_ld(
        self,
        soup: BeautifulSoup,
        url: str,
        marketplace: str,
    ) -> ScrapedProduct | None:
        """Парсить Schema.org JSON-LD для будь-якого інтернет-магазину."""
        scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
        for script in scripts:
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            nodes: list[dict[str, Any]] = []
            if isinstance(data, dict):
                if isinstance(data.get("@graph"), list):
                    nodes.extend(n for n in data["@graph"] if isinstance(n, dict))
                else:
                    nodes.append(data)
            elif isinstance(data, list):
                nodes.extend(n for n in data if isinstance(n, dict))

            for node in nodes:
                node_type = str(node.get("@type") or "").lower()
                if not any(t in node_type for t in ("product", "individualproduct", "productgroup")):
                    continue

                title = str(node.get("name") or "").strip()
                image_raw = node.get("image")
                image_url: str | None = None
                if isinstance(image_raw, str):
                    image_url = image_raw
                elif isinstance(image_raw, list) and image_raw:
                    first = image_raw[0]
                    image_url = (
                        first
                        if isinstance(first, str)
                        else (first.get("url") or first.get("contentUrl") if isinstance(first, dict) else None)
                    )
                elif isinstance(image_raw, dict):
                    image_url = image_raw.get("url") or image_raw.get("contentUrl")

                offers = node.get("offers")
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if not isinstance(offers, dict):
                    continue

                price = self._parse_decimal_price(
                    offers.get("price")
                    or offers.get("lowPrice")
                    or (offers.get("priceSpecification", {}) or {}).get("price")
                )
                high_price = self._parse_decimal_price(offers.get("highPrice"))
                default_curr = detect_default_currency(url, marketplace)
                currency = str(
                    offers.get("priceCurrency")
                    or (offers.get("priceSpecification", {}) or {}).get("priceCurrency")
                    or default_curr
                ).upper()
                availability = str(offers.get("availability") or "").lower()
                is_available = "outofstock" not in availability and "soldout" not in availability

                if title and price is not None:
                    return ScrapedProduct(
                        url=url,
                        title=title,
                        price=price,
                        currency=currency,
                        image_url=image_url,
                        marketplace=marketplace,
                        is_available=is_available,
                        old_price=high_price if (high_price and high_price > price) else None,
                    )
        return None

    def _extract_from_dom_and_meta(
        self,
        soup: BeautifulSoup,
        html_text: str,
        url: str,
        marketplace: str,
    ) -> ScrapedProduct:
        """Витягує назву, фото, ціну, валюту та стару ціну з метатегів, CSS-селекторів або inline JSON."""
        # 1. Назва товару
        title: str | None = None
        for selector in self.TITLE_SELECTORS:
            el = soup.select_one(selector)
            if el and el.get_text(strip=True):
                title = el.get_text(strip=True)
                break

        if not title:
            og_title = soup.find("meta", attrs={"property": "og:title"}) or soup.find(
                "meta", attrs={"name": "title"}
            )
            if og_title and og_title.get("content"):
                title = str(og_title["content"]).strip()

        if not title and soup.title and soup.title.string:
            title = soup.title.string.strip()

        # 2. Зображення
        image_url: str | None = None
        og_img = soup.find("meta", attrs={"property": "og:image"})
        if og_img and og_img.get("content"):
            image_url = str(og_img["content"]).strip()

        if not image_url:
            for selector in self.IMAGE_SELECTORS:
                img_el = soup.select_one(selector)
                if img_el:
                    src = (
                        img_el.get("data-old-hires")
                        or img_el.get("src")
                        or img_el.get("data-src")
                        or img_el.get("content")
                    )
                    if src and str(src).startswith("http"):
                        image_url = str(src).strip()
                        break

        # 3. Ціна (метатеги -> селектори -> inline JSON)
        price: Decimal | None = None
        price_hint_text = ""

        for meta_prop in ("product:price:amount", "og:price:amount", "price"):
            meta_el = soup.find("meta", attrs={"property": meta_prop}) or soup.find(
                "meta", attrs={"itemprop": meta_prop}
            )
            if meta_el and meta_el.get("content"):
                price = self._parse_decimal_price(meta_el["content"])
                if price is not None:
                    price_hint_text = str(meta_el["content"])
                    break

        if price is None:
            for selector in self.PRICE_SELECTORS:
                el = soup.select_one(selector)
                if el:
                    raw_attr = (
                        el.get("content")
                        or el.get("data-price")
                        or el.get("data-product-price")
                        or el.get("aria-label")
                    )
                    if raw_attr:
                        price = self._parse_decimal_price(raw_attr)
                        price_hint_text = str(raw_attr)
                    else:
                        # Ігноруємо вкладені закреслені теги старої ціни всередині контейнера
                        cloned = BeautifulSoup(str(el), "html.parser")
                        for old_node in cloned.select(
                            "del, s, strike, [class*='old'], [class*='prev'], [data-qaid='old_price']"
                        ):
                            old_node.decompose()
                        raw_txt = cloned.get_text(" ", strip=True)
                        price = self._parse_decimal_price(raw_txt)
                        price_hint_text = raw_txt
                    if price is not None:
                        break

        if price is None:
            inline_match = INLINE_JSON_PRICE_RE.search(html_text)
            if inline_match:
                price = self._parse_decimal_price(inline_match.group(1))

        # 4. Стара ціна (до знижки)
        old_price: Decimal | None = None
        for selector in self.OLD_PRICE_SELECTORS:
            old_el = soup.select_one(selector)
            if old_el:
                old_price = self._parse_decimal_price(old_el.get_text(" ", strip=True))
                if old_price is not None:
                    break

        if old_price is None:
            old_match = INLINE_JSON_OLD_PRICE_RE.search(html_text)
            if old_match:
                old_price = self._parse_decimal_price(old_match.group(1))

        # Якщо метатег повернув базову ціну без акції, а в селекторі акційної ціни є нижча ціна — коригуємо
        if price is not None and old_price is not None and old_price > 0:
            if old_price < price:
                price, old_price = old_price, price
            elif old_price == price:
                old_price = None

        if not title or price is None:
            raise ParsingStructureError(
                f"Не вдалося визначити ціну товару на сторінці ({marketplace}). "
                "Переконайтеся, що посилання веде безпосередньо на картку товару."
            )

        currency = self._detect_currency(soup, html_text, url, marketplace, price_hint_text)

        return ScrapedProduct(
            url=url,
            title=title,
            price=price,
            currency=currency,
            image_url=image_url,
            marketplace=marketplace,
            is_available=True,
            old_price=old_price,
        )

    async def fetch_product(self, url: str) -> ScrapedProduct:
        """Витягує дані про товар із будь-якого підтримуваного світового або українського маркетплейсу."""
        marketplace = detect_marketplace(url)

        # 1. Швидка перевірка Shopify JSON API (якщо магазин працює на Shopify)
        shopify_product = await self._try_shopify_json(url, marketplace)
        if shopify_product is not None:
            return shopify_product

        # 2. Завантаження HTML та каскадний парсинг (JSON-LD -> OpenGraph/Microdata -> CSS/SPA)
        html_text = await self._request_html(url)

        try:
            soup = BeautifulSoup(html_text, "lxml")
        except Exception:
            soup = BeautifulSoup(html_text, "html.parser")

        json_ld_product = self._extract_from_json_ld(soup, url, marketplace)
        if json_ld_product is not None:
            if json_ld_product.old_price is None:
                try:
                    dom_prod = self._extract_from_dom_and_meta(soup, html_text, url, marketplace)
                    if dom_prod.old_price and dom_prod.old_price > json_ld_product.price:
                        return ScrapedProduct(
                            url=json_ld_product.url,
                            title=json_ld_product.title,
                            price=json_ld_product.price,
                            currency=json_ld_product.currency,
                            image_url=json_ld_product.image_url or dom_prod.image_url,
                            marketplace=marketplace,
                            is_available=json_ld_product.is_available,
                            old_price=dom_prod.old_price,
                        )
                except Exception:
                    pass
            return json_ld_product

        return self._extract_from_dom_and_meta(soup, html_text, url, marketplace)
