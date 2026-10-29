from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import logging
import re
import time

from bs4 import BeautifulSoup
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import search_products_in_db
from services.scraper.base import _BROWSER_PROFILES, ScrapedProduct
from services.scraper.normalizer import normalize_url
import random


def get_random_user_agent() -> str:
    return random.choice(_BROWSER_PROFILES)["User-Agent"]

logger = logging.getLogger(__name__)

_SEARCH_CACHE_TTL = 900.0  # 15 хвилин кешування пошукових запитів
_MAX_OFFER_TOKENS = 300


@dataclass(slots=True)
class SearchOffer:
    """Знайдена пропозиція товару з маркетплейсу за текстовим запитом."""

    token: str
    url: str
    title: str
    price: Decimal
    old_price: Decimal | None
    currency: str
    marketplace: str
    image_url: str | None
    is_available: bool = True

    def to_scraped_product(self) -> ScrapedProduct:
        return ScrapedProduct(
            url=self.url,
            title=self.title,
            price=self.price,
            currency=self.currency,
            image_url=self.image_url,
            marketplace=self.marketplace,
            is_available=self.is_available,
            old_price=self.old_price,
        )


_OFFER_BY_TOKEN: OrderedDict[str, SearchOffer] = OrderedDict()
_QUERY_CACHE: OrderedDict[str, tuple[float, list[SearchOffer]]] = OrderedDict()


def _make_token(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]


def _register_offer(offer: SearchOffer) -> None:
    _OFFER_BY_TOKEN[offer.token] = offer
    _OFFER_BY_TOKEN.move_to_end(offer.token)
    while len(_OFFER_BY_TOKEN) > _MAX_OFFER_TOKENS:
        _OFFER_BY_TOKEN.popitem(last=False)


def get_cached_search_offer(token: str) -> SearchOffer | None:
    """Повертає збережену пропозицію за коротким токеном кнопки."""
    return _OFFER_BY_TOKEN.get(token)


def _parse_dec(val: object) -> Decimal | None:
    if val is None:
        return None
    s = re.sub(r"[^\d.,]", "", str(val)).replace(",", ".")
    if not s:
        return None
    try:
        d = Decimal(s)
        return d if d > Decimal("0") else None
    except Exception:
        return None


def _is_relevant_title(title: str, query: str) -> bool:
    """Перевіряє, чи містить назва товару ключові слова запиту."""
    t_low = title.lower()
    words = [w.lower() for w in re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґ0-9]{2,}", query)]
    if not words:
        return True
    matched = sum(1 for w in words if w in t_low)
    return matched >= max(1, (len(words) + 1) // 2)


async def _search_rozetka_api(client: httpx.AsyncClient, query: str, limit: int = 5) -> list[SearchOffer]:
    """Швидкий пошук через офіційний Search API v6 + Product API Rozetka."""
    headers = {
        "User-Agent": get_random_user_agent(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
        "Referer": "https://rozetka.com.ua/ua/",
    }
    try:
        resp = await client.get(
            "https://search.rozetka.com.ua/ua/search/api/v6/",
            params={
                "front-type": "xl",
                "country": "UA",
                "lang": "ua",
                "text": query.strip(),
                "redirected": "1",
            },
            headers=headers,
            timeout=2.8,
        )
        if resp.status_code != 200:
            return []
        payload = resp.json()
        goods = ((payload.get("data") or {}) if isinstance(payload, dict) else {}).get("goods") or []
        if not isinstance(goods, list) or not goods:
            return []

        candidate_ids: list[str] = []
        fallback_items: list[dict[str, object]] = []
        for g in goods[:8]:
            if not isinstance(g, dict):
                continue
            gid = str(g.get("id") or "").strip()
            if gid.isdigit():
                candidate_ids.append(gid)
                fallback_items.append(g)

        async def _hydrate(gid: str, raw_item: dict[str, object]) -> SearchOffer | None:
            try:
                p_resp = await client.get(
                    f"https://product-api.rozetka.com.ua/v4/goods/get-main?front-type=xl&country=UA&lang=ua&goodsId={gid}",
                    headers=headers,
                    timeout=2.5,
                )
                data = (p_resp.json() or {}).get("data") if p_resp.status_code == 200 else None
            except Exception:
                data = None

            src = data if isinstance(data, dict) else raw_item
            title = str(src.get("title") or "").strip()
            price = _parse_dec(src.get("price"))
            old_price = _parse_dec(src.get("old_price"))
            href = str(src.get("href") or f"https://rozetka.com.ua/ua/p{gid}/").strip()
            sell_status = str(src.get("sell_status") or "available").lower()
            is_avail = sell_status in {"available", "limited", "active"}
            if not title or price is None or not is_avail:
                return None
            clean_u, _ = normalize_url(href)
            img = src.get("image_main") if isinstance(src.get("image_main"), str) else None
            tok = _make_token(clean_u)
            if old_price is not None and old_price <= price:
                old_price = None
            return SearchOffer(
                token=tok,
                url=clean_u,
                title=title,
                price=price,
                old_price=old_price,
                currency="UAH",
                marketplace="rozetka",
                image_url=img,
                is_available=True,
            )

        hydrated = await asyncio.gather(
            *[_hydrate(gid, raw) for gid, raw in zip(candidate_ids[:limit], fallback_items[:limit])]
        )
        return [item for item in hydrated if item is not None]
    except Exception as exc:
        logger.debug("Rozetka search skipped for '%s': %s", query, exc)
        return []


async def _search_prom_ua(client: httpx.AsyncClient, query: str, limit: int = 3) -> list[SearchOffer]:
    """Швидкий пошук пропозицій на Prom.ua за JSON-LD / мікророзміткою."""
    headers = {
        "User-Agent": get_random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "uk-UA,uk;q=0.9",
    }
    try:
        resp = await client.get(
            "https://prom.ua/ua/search",
            params={"search_term": query.strip()},
            headers=headers,
            timeout=2.8,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text[:160000], "html.parser")
        offers: list[SearchOffer] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            items = parsed if isinstance(parsed, list) else [parsed]
            for node in items:
                if not isinstance(node, dict):
                    continue
                if "itemlist" in str(node.get("@type") or "").lower():
                    for elem in (node.get("itemListElement") or [])[:6]:
                        item_obj = elem.get("item") if isinstance(elem, dict) else None
                        if isinstance(item_obj, dict):
                            items.append(item_obj)
                if "product" not in str(node.get("@type") or "").lower():
                    continue
                title = str(node.get("name") or "").strip()
                off = node.get("offers")
                if isinstance(off, list) and off:
                    off = off[0]
                if not isinstance(off, dict):
                    continue
                price = _parse_dec(off.get("price") or off.get("lowPrice"))
                url = str(off.get("url") or node.get("url") or "").strip()
                if not title or price is None or not url.startswith("http"):
                    continue
                if not _is_relevant_title(title, query):
                    continue
                clean_u, _ = normalize_url(url)
                tok = _make_token(clean_u)
                img = node.get("image")
                if isinstance(img, list) and img:
                    img = img[0]
                offers.append(
                    SearchOffer(
                        token=tok,
                        url=clean_u,
                        title=title,
                        price=price,
                        old_price=None,
                        currency=str(off.get("priceCurrency") or "UAH").upper(),
                        marketplace="prom",
                        image_url=img if isinstance(img, str) else None,
                        is_available=True,
                    )
                )
                if len(offers) >= limit:
                    return offers
        return offers
    except Exception as exc:
        logger.debug("Prom search skipped for '%s': %s", query, exc)
        return []


async def _search_telemart_or_moyo(client: httpx.AsyncClient, query: str, limit: int = 2) -> list[SearchOffer]:
    """Швидкий пошук на Telemart.ua / Moyo.ua для розширення мультимагазинного порівняння."""
    headers = {
        "User-Agent": get_random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "uk-UA,uk;q=0.9",
    }
    try:
        resp = await client.get(
            "https://telemart.ua/ua/search/",
            params={"search_que": query.strip()},
            headers=headers,
            timeout=2.6,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text[:140000], "html.parser")
        offers: list[SearchOffer] = []
        for card in soup.select(".product-item, .b-i-product")[:5]:
            title_el = card.select_one(".product-item__title a, .b-i-product-name a, a[title]")
            price_el = card.select_one(".product-cost, .b-i-product-price, [data-price]")
            if not title_el or not price_el:
                continue
            title = title_el.get_text(" ", strip=True) or str(title_el.get("title") or "").strip()
            href = str(title_el.get("href") or "").strip()
            raw_p = price_el.get("data-price") or price_el.get_text(" ", strip=True)
            price = _parse_dec(raw_p)
            if not title or not href.startswith("http") or price is None:
                continue
            if not _is_relevant_title(title, query):
                continue
            clean_u, _ = normalize_url(href)
            tok = _make_token(clean_u)
            offers.append(
                SearchOffer(
                    token=tok,
                    url=clean_u,
                    title=title,
                    price=price,
                    old_price=None,
                    currency="UAH",
                    marketplace="telemart",
                    image_url=None,
                    is_available=True,
                )
            )
            if len(offers) >= limit:
                break
        return offers
    except Exception as exc:
        logger.debug("Telemart search skipped for '%s': %s", query, exc)
        return []


async def search_products_by_name(
    query: str,
    *,
    session: AsyncSession | None = None,
    limit: int = 6,
) -> list[SearchOffer]:
    """
    Паралельно шукає товар за назвою у базі бота та маркетплейсах (Rozetka, Prom, Telemart тощо),
    сортує за найнижчою ціною і кешує результати на 15 хвилин для миттєвої відповіді.
    """
    clean_query = re.sub(r"\s+", " ", (query or "").strip())
    if len(clean_query) < 2:
        return []

    cache_key = clean_query.lower()
    now = time.monotonic()
    cached = _QUERY_CACHE.get(cache_key)
    if cached is not None and (now - cached[0]) < _SEARCH_CACHE_TTL:
        for off in cached[1]:
            _register_offer(off)
        return cached[1][:limit]

    db_offers: list[SearchOffer] = []
    if session is not None:
        try:
            db_products = await search_products_in_db(session, clean_query, limit=4)
            for p in db_products:
                cur_p = Decimal(str(p.current_price))
                if cur_p <= Decimal("0"):
                    continue
                hist_p = [Decimal(str(h.price)) for h in p.price_history if Decimal(str(h.price)) > Decimal("0")]
                peak_p = max(hist_p) if hist_p else None
                tok = _make_token(p.url)
                db_offers.append(
                    SearchOffer(
                        token=tok,
                        url=p.url,
                        title=p.title,
                        price=cur_p,
                        old_price=peak_p if (peak_p and peak_p > cur_p) else None,
                        currency=p.currency,
                        marketplace=p.marketplace,
                        image_url=p.image_url,
                        is_available=bool(getattr(p, "is_available", True)),
                    )
                )
        except Exception as exc:
            logger.debug("DB search skipped: %s", exc)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        rz_task = _search_rozetka_api(client, clean_query, limit=5)
        prom_task = _search_prom_ua(client, clean_query, limit=3)
        tm_task = _search_telemart_or_moyo(client, clean_query, limit=2)
        rz_res, prom_res, tm_res = await asyncio.gather(rz_task, prom_task, tm_task)

    combined: list[SearchOffer] = []
    seen_urls: set[str] = set()

    for off in [*db_offers, *rz_res, *prom_res, *tm_res]:
        if off.url in seen_urls or off.price <= Decimal("0"):
            continue
        seen_urls.add(off.url)
        _register_offer(off)
        combined.append(off)

    # Сортуємо: спочатку релевантні за назвою і від найнижчої ціни до вищої
    combined.sort(
        key=lambda item: (
            0 if _is_relevant_title(item.title, clean_query) else 1,
            float(item.price),
        )
    )

    final_list = combined[:limit]
    if final_list:
        _QUERY_CACHE[cache_key] = (now, final_list)
        _QUERY_CACHE.move_to_end(cache_key)
        while len(_QUERY_CACHE) > 60:
            _QUERY_CACHE.popitem(last=False)

    return final_list
