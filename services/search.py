from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
import hashlib
import json
import logging
import random
import re
import time

from bs4 import BeautifulSoup
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import search_products_in_db
from services.scraper.base import _BROWSER_PROFILES, ScrapedProduct
from services.scraper.normalizer import normalize_url


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
    product_id: int | None = None

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


def _register_offer(offer: SearchOffer) -> SearchOffer:
    if not offer.token:
        offer.token = _make_token(offer.url)
    _OFFER_BY_TOKEN[offer.token] = offer
    _OFFER_BY_TOKEN.move_to_end(offer.token)
    while len(_OFFER_BY_TOKEN) > _MAX_OFFER_TOKENS:
        _OFFER_BY_TOKEN.popitem(last=False)
    return offer


def _cache_offer(offer: SearchOffer) -> SearchOffer:
    """Реєструє пропозицію в кеші токенів та повертає її (для Inline-режиму та кнопок)."""
    return _register_offer(offer)


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


# Таблиця перемикання розкладки клавіатури (UA/RU -> EN QWERTY)
_CYR_TO_LAT_LAYOUT = str.maketrans(
    "йцукенгшщзхїфівапролджєячсмитьбюыэъЙЦУКЕНГШЩЗХЇФІВАПРОЛДЖЄЯЧСМИТЬБЮЫЭЪ",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,.s']QWERTYUIOP[]ASDFGHJKL;'ZXCVBNM,.S']",
)

# Словник популярних мультимовних синонімів, сленгу, транслитерацій та частих помилок
_MULTIWORD_PHRASE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\bair\s+pods\b", "AirPods"),
    (r"\bear\s+pods\b", "EarPods"),
    (r"\bа[іий]р\s+подс\b", "AirPods"),
    (r"\bе[іий]р\s+подс\b", "AirPods"),
    (r"\bmac\s+book\b", "MacBook"),
    (r"\bмак\s+бук\b", "MacBook"),
    (r"\bplay\s+station\b", "PlayStation"),
    (r"\bплей\s+стейш[еи]?н\b", "PlayStation"),
    (r"\bps\s+5\b", "PlayStation 5"),
    (r"\bпс\s+5\b", "PlayStation 5"),
    (r"\bплойка\s*5?\b", "PlayStation 5"),
    (r"\beco\s+flow\b", "EcoFlow"),
    (r"\bеко\s+фло[ув]\b", "EcoFlow"),
    (r"\bэко\s+фло[ув]\b", "EcoFlow"),
    (r"\bsteam\s+deck\b", "Steam Deck"),
    (r"\bст[іи]м\s+дек\b", "Steam Deck"),
    (r"\bепл\s+вотч\b", "Apple Watch"),
    (r"\bапл\s+вотч\b", "Apple Watch"),
)

_TOKEN_ALIASES: dict[str, str] = {
    # AirPods + помилки
    "airpods": "AirPods",
    "airpod": "AirPods",
    "airposd": "AirPods",
    "airpords": "AirPods",
    "airpds": "AirPods",
    "eirpods": "AirPods",
    "aerpods": "AirPods",
    "aripods": "AirPods",
    "aipods": "AirPods",
    "airpots": "AirPods",
    "аірподс": "AirPods",
    "айрподс": "AirPods",
    "аирподс": "AirPods",
    "еірподс": "AirPods",
    "ейрподс": "AirPods",
    "эирподс": "AirPods",
    "аірподси": "AirPods",
    "айрподси": "AirPods",
    # iPhone + помилки
    "iphone": "iPhone",
    "iphon": "iPhone",
    "iphne": "iPhone",
    "ipone": "iPhone",
    "ipohne": "iPhone",
    "ihpone": "iPhone",
    "ifon": "iPhone",
    "ifone": "iPhone",
    "айфон": "iPhone",
    "аіфон": "iPhone",
    "ифон": "iPhone",
    # iPad / MacBook / Apple
    "ipad": "iPad",
    "айпад": "iPad",
    "аіпад": "iPad",
    "macbook": "MacBook",
    "macbok": "MacBook",
    "makbook": "MacBook",
    "mcbook": "MacBook",
    "макбук": "MacBook",
    "макубк": "MacBook",
    "apple": "Apple",
    "aple": "Apple",
    "appel": "Apple",
    "епл": "Apple",
    "апл": "Apple",
    # Ігрові консолі
    "ps5": "PlayStation 5",
    "пс5": "PlayStation 5",
    "плойка": "PlayStation 5",
    "playstation": "PlayStation",
    "playstaton": "PlayStation",
    "pleystation": "PlayStation",
    "playstasion": "PlayStation",
    "плейстейшн": "PlayStation",
    "плейстейшен": "PlayStation",
    "xbox": "Xbox",
    "іксбокс": "Xbox",
    "иксбокс": "Xbox",
    "хбокс": "Xbox",
    "nintendo": "Nintendo",
    "нінтендо": "Nintendo",
    "нинтендо": "Nintendo",
    "switch": "Switch",
    "світч": "Switch",
    "свіч": "Switch",
    "стімдек": "Steam Deck",
    "стимдек": "Steam Deck",
    # Зарядні станції
    "ecoflow": "EcoFlow",
    "ekoflow": "EcoFlow",
    "ecofolv": "EcoFlow",
    "ecoflw": "EcoFlow",
    "екофлоу": "EcoFlow",
    "екофлов": "EcoFlow",
    "экофлоу": "EcoFlow",
    "bluetti": "Bluetti",
    "blueti": "Bluetti",
    "bluetty": "Bluetti",
    "блюетті": "Bluetti",
    "блюеті": "Bluetti",
    "блюєтті": "Bluetti",
    "блюэтти": "Bluetti",
    # Смартфони та техніка
    "samsung": "Samsung",
    "samung": "Samsung",
    "samsng": "Samsung",
    "samsugn": "Samsung",
    "sumsung": "Samsung",
    "самсунг": "Samsung",
    "самсуг": "Samsung",
    "galaxy": "Galaxy",
    "гелексі": "Galaxy",
    "галаксі": "Galaxy",
    "xiaomi": "Xiaomi",
    "xiomi": "Xiaomi",
    "xiami": "Xiaomi",
    "shaomi": "Xiaomi",
    "сяомі": "Xiaomi",
    "ксіомі": "Xiaomi",
    "ксяомі": "Xiaomi",
    "сяоми": "Xiaomi",
    "ксиоми": "Xiaomi",
    "redmi": "Redmi",
    "redmy": "Redmi",
    "редмі": "Redmi",
    "редми": "Redmi",
    "poco": "Poco",
    "поко": "Poco",
    "dyson": "Dyson",
    "dayson": "Dyson",
    "dison": "Dyson",
    "дайсон": "Dyson",
    "даісон": "Dyson",
    "marshall": "Marshall",
    "marshal": "Marshall",
    "маршал": "Marshall",
    "маршалл": "Marshall",
    "jbl": "JBL",
    "джейбіель": "JBL",
    "жбл": "JBL",
    "garmin": "Garmin",
    "garmn": "Garmin",
    "гармін": "Garmin",
    "гармин": "Garmin",
    "starlink": "Starlink",
    "старлінк": "Starlink",
    "старлинк": "Starlink",
    "logitech": "Logitech",
    "logitec": "Logitech",
    "лоджитек": "Logitech",
    "hyperx": "HyperX",
    "хайперікс": "HyperX",
    "razer": "Razer",
    "рейзер": "Razer",
}

_CANONICAL_BRANDS: tuple[str, ...] = (
    "AirPods",
    "iPhone",
    "iPad",
    "MacBook",
    "PlayStation",
    "Nintendo",
    "Switch",
    "Steam",
    "Samsung",
    "Galaxy",
    "Xiaomi",
    "Redmi",
    "Dyson",
    "EcoFlow",
    "Bluetti",
    "Marshall",
    "Logitech",
    "HyperX",
    "Garmin",
    "Starlink",
    "Lenovo",
)

# Ключові слова дешевих аксесуарів, які треба відсіювати, якщо користувач шукає сам гаджет
_ACCESSORY_KEYWORDS: tuple[str, ...] = (
    "чохол",
    "чехол",
    "футляр",
    "кейс для",
    "case for",
    "silicone case",
    "захисне скло",
    "защитное стекло",
    "захисна плівка",
    "защитная пленка",
    "гідрогелева плівка",
    "ремінець",
    "ремешок",
    "амбушур",
    "наклейк",
    "стікер",
    "карабін",
    "брелок",
    "шнурок для",
    "тримач для",
    "підставка для",
    "заглушк",
    "щітка для чищення",
    "геймпад",
    "джойстик",
    "dualsense",
    "dualshock",
    "кабель для",
    "адаптер для",
)


def _try_fix_wrong_keyboard_layout(word: str) -> str:
    """
    Виправляє слово, набране в українській/російській розкладці замість англійської
    (наприклад, 'фшкзщвы' -> 'airpods', 'шзрщту' -> 'iphone').
    """
    if not re.search(r"[а-яіїєґыэъА-ЯІЇЄҐЫЭЪ]", word):
        return word
    converted = word.translate(_CYR_TO_LAT_LAYOUT)
    conv_low = converted.lower()
    if conv_low in _TOKEN_ALIASES:
        return _TOKEN_ALIASES[conv_low]
    for brand in _CANONICAL_BRANDS:
        if SequenceMatcher(None, conv_low, brand.lower()).ratio() >= 0.80:
            return brand
    return word


def normalize_search_query(raw_query: str) -> str:
    """
    Нормалізує пошуковий запит:
    - виправляє помилкову розкладку клавіатури ('фшкзщвы' -> 'AirPods');
    - замінює сленг, кириличну транслитерацію та типові одруківки ('айрподс', 'airposd', 'плойка');
    - виконує нечітке (fuzzy) виправлення назв брендів за алгоритмом SequenceMatcher.
    """
    q = re.sub(r"\s+", " ", (raw_query or "").strip())
    if not q:
        return ""

    for pattern, repl in _MULTIWORD_PHRASE_REPLACEMENTS:
        q = re.sub(pattern, repl, q, flags=re.IGNORECASE)

    tokens = q.split(" ")
    fixed_tokens: list[str] = []

    for tok in tokens:
        clean_tok = tok.strip()
        if not clean_tok:
            continue

        # 1. Перевірка на помилкову розкладку клавіатури
        layout_fixed = _try_fix_wrong_keyboard_layout(clean_tok)
        if layout_fixed != clean_tok:
            fixed_tokens.append(layout_fixed)
            continue

        low = clean_tok.lower()

        # 2. Точний збіг зі словником транслитерацій / сленгу / частих помилок
        if low in _TOKEN_ALIASES:
            fixed_tokens.append(_TOKEN_ALIASES[low])
            continue

        # 3. Якщо слово злиплось із цифрою (наприклад 'айфон16', 'airpods4', 'ps5')
        m_num = re.match(r"^([a-zA-Zа-яА-ЯіїєґІЇЄҐ]{3,})(\d+)$", low)
        if m_num:
            prefix, digits = m_num.group(1), m_num.group(2)
            if prefix in _TOKEN_ALIASES:
                fixed_tokens.append(f"{_TOKEN_ALIASES[prefix]} {digits}")
                continue

        # 4. Нечіткий (fuzzy) пошук для латинських слів із помилками (напр. 'airpds', 'ipohne', 'samsugn')
        if len(low) >= 5 and re.match(r"^[a-z]+$", low):
            best_brand: str | None = None
            best_score = 0.0
            for brand in _CANONICAL_BRANDS:
                score = SequenceMatcher(None, low, brand.lower()).ratio()
                if score > best_score:
                    best_score = score
                    best_brand = brand
            if best_brand is not None and best_score >= 0.76:
                fixed_tokens.append(best_brand)
                continue

        fixed_tokens.append(clean_tok)

    return re.sub(r"\s+", " ", " ".join(fixed_tokens)).strip()


def _is_unwanted_accessory(title: str, query: str) -> bool:
    """
    Повертає True, якщо товар є дешевим аксесуаром (чохол, скло, плівка, амбушури),
    хоча користувач шукав сам основний пристрій (наприклад, 'Airpods' або 'iPhone').
    """
    t_low = title.lower()
    q_low = query.lower()

    # Якщо користувач сам явно шукає чохол / скло / ремінець — не фільтруємо
    for kw in _ACCESSORY_KEYWORDS:
        if kw in q_low:
            return False

    for kw in _ACCESSORY_KEYWORDS:
        if kw in t_low:
            return True

    # Відсікаємо дротові EarPods, коли користувач шукає бездротові AirPods
    if "airpods" in q_low and "earpods" in t_low and "airpods" not in t_low:
        return True

    return False


def _is_relevant_title(title: str, query: str, normalized_query: str | None = None) -> bool:
    """
    Перевіряє релевантність назви товару з підтримкою нечіткого збігу (fuzzy matching),
    щоб запити з помилками ('airposd', 'айрподс') або з 1 слова ('Airpods') знаходили потрібні товари.
    """
    if _is_unwanted_accessory(title, normalized_query or query):
        return False

    t_low = title.lower()
    t_words = re.findall(r"[a-zа-яіїєґ0-9]{2,}", t_low)

    check_queries = [q for q in (normalized_query, query) if q]
    for q_candidate in check_queries:
        q_words = [w.lower() for w in re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґ0-9]{2,}", q_candidate)]
        if not q_words:
            return True

        matched = 0
        for qw in q_words:
            if qw in t_low:
                matched += 1
                continue
            # Fuzzy-перевірка окремого слова для стійкості до одруківок
            if len(qw) >= 4 and any(SequenceMatcher(None, qw, tw).ratio() >= 0.74 for tw in t_words):
                matched += 1

        required = 1 if len(q_words) == 1 else max(1, (len(q_words) + 1) // 2)
        if matched >= required:
            return True

    return False


async def _fetch_rozetka_goods_list(
    client: httpx.AsyncClient,
    search_text: str,
    headers: dict[str, str],
) -> list[dict[str, object]]:
    """Виконує запит до Rozetka Search API v6 і повертає сирий список goods."""
    resp = await client.get(
        "https://search.rozetka.com.ua/ua/search/api/v6/",
        params={
            "front-type": "xl",
            "country": "UA",
            "lang": "ua",
            "text": search_text.strip(),
            "redirected": "1",
        },
        headers=headers,
        timeout=2.8,
    )
    if resp.status_code != 200:
        return []
    payload = resp.json()
    goods = ((payload.get("data") or {}) if isinstance(payload, dict) else {}).get("goods") or []
    return goods if isinstance(goods, list) else []


async def _search_rozetka_api(
    client: httpx.AsyncClient,
    query: str,
    limit: int = 5,
) -> list[SearchOffer]:
    """
    Швидкий пошук через офіційний Search API v6 + Autocomplete Spellcheck + Product API Rozetka.
    Автоматично виправляє помилки в запитах через автодоповнення Rozetka, якщо прямий пошук порожній.
    """
    headers = {
        "User-Agent": get_random_user_agent(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
        "Referer": "https://rozetka.com.ua/ua/",
    }
    try:
        goods = await _fetch_rozetka_goods_list(client, query, headers)

        # Якщо через одруківку нічого не знайдено — звертаємось до швидкого Autocomplete API Rozetka
        if not goods:
            ac_resp = await client.get(
                "https://search.rozetka.com.ua/ua/search/api/v6/autocomplete/",
                params={
                    "front-type": "xl",
                    "country": "UA",
                    "lang": "ua",
                    "text": query.strip(),
                },
                headers=headers,
                timeout=2.2,
            )
            if ac_resp.status_code == 200:
                ac_content = ((ac_resp.json() or {}).get("data") or {}).get("content") or {}
                records = ac_content.get("records") or {}
                ac_goods = records.get("goods") or []
                if isinstance(ac_goods, list) and ac_goods:
                    goods = ac_goods
                else:
                    changed_text = str(ac_content.get("changedText") or "").strip()
                    additions = records.get("words_additions") or []
                    suggested_query = ""
                    if changed_text and changed_text.lower() != query.strip().lower():
                        suggested_query = changed_text
                    elif isinstance(additions, list) and additions and isinstance(additions[0], dict):
                        suggested_query = str(additions[0].get("name") or "").strip()

                    if suggested_query:
                        goods = await _fetch_rozetka_goods_list(client, suggested_query, headers)

        if not goods:
            return []

        candidate_ids: list[str] = []
        fallback_items: list[dict[str, object]] = []
        for g in goods[:10]:
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
            if _is_unwanted_accessory(title, query):
                return None
            clean_u, _ = normalize_url(href)
            raw_img = (
                src.get("image_main")
                or raw_item.get("image_main")
                or src.get("preview")
                or raw_item.get("preview")
                or raw_item.get("image")
            )
            img = str(raw_img).strip() if isinstance(raw_img, str) and str(raw_img).strip().startswith("http") else None
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
            *[_hydrate(gid, raw) for gid, raw in zip(candidate_ids[: limit + 3], fallback_items[: limit + 3])]
        )
        valid = [item for item in hydrated if item is not None]
        return valid[:limit]
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
                    for elem in (node.get("itemListElement") or [])[:8]:
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
        for card in soup.select(".product-item, .b-i-product")[:6]:
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
            img_el = card.select_one("img")
            raw_img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
            img_url = str(raw_img).strip() if str(raw_img).strip().startswith("http") else None
            offers.append(
                SearchOffer(
                    token=tok,
                    url=clean_u,
                    title=title,
                    price=price,
                    old_price=None,
                    currency="UAH",
                    marketplace="telemart",
                    image_url=img_url,
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
    first_arg: AsyncSession | str,
    second_arg: str | None = None,
    *,
    session: AsyncSession | None = None,
    limit: int = 6,
) -> list[SearchOffer]:
    """
    Паралельно шукає товар за назвою у базі бота та маркетплейсах (Rozetka, Prom, Telemart тощо)
    із підтримкою однослівних запитів («Airpods»), виправленням помилок («airposd», «eirpods»),
    неправильної розкладки клавіатури («фшкзщвы») та кириличної транслитерації («айрподс», «плойка»).

    Підтримує обидва варіанти виклику:
    - `await search_products_by_name(session, query, limit=6)`
    - `await search_products_by_name(query, session=session, limit=6)`
    """
    if isinstance(first_arg, str):
        raw_query = first_arg
        db_session = session
    else:
        db_session = first_arg
        raw_query = second_arg or ""

    clean_query = re.sub(r"\s+", " ", (raw_query or "").strip())
    if len(clean_query) < 2:
        return []

    norm_query = normalize_search_query(clean_query)
    effective_query = norm_query or clean_query

    cache_key = effective_query.lower()
    now = time.monotonic()
    cached = _QUERY_CACHE.get(cache_key)
    if cached is not None and (now - cached[0]) < _SEARCH_CACHE_TTL:
        for off in cached[1]:
            _register_offer(off)
        return cached[1][:limit]

    db_offers: list[SearchOffer] = []
    if db_session is not None:
        try:
            db_products = list(await search_products_in_db(db_session, effective_query, limit=4))
            if not db_products and effective_query.lower() != clean_query.lower():
                db_products = list(await search_products_in_db(db_session, clean_query, limit=4))
            for p in db_products:
                cur_p = Decimal(str(p.current_price))
                if cur_p <= Decimal("0") or _is_unwanted_accessory(p.title, effective_query):
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
                        product_id=p.id,
                    )
                )
        except Exception as exc:
            logger.debug("DB search skipped: %s", exc)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        rz_task = _search_rozetka_api(client, effective_query, limit=5)
        prom_task = _search_prom_ua(client, effective_query, limit=3)
        tm_task = _search_telemart_or_moyo(client, effective_query, limit=2)
        rz_res, prom_res, tm_res = await asyncio.gather(rz_task, prom_task, tm_task)

    combined: list[SearchOffer] = []
    seen_urls: set[str] = set()

    for off in [*db_offers, *rz_res, *prom_res, *tm_res]:
        if off.url in seen_urls or off.price <= Decimal("0"):
            continue
        if _is_unwanted_accessory(off.title, effective_query):
            continue
        seen_urls.add(off.url)
        _register_offer(off)
        combined.append(off)

    # Відсікаємо підозріло дешеві випадкові товари (наприклад, кабель за 150 грн, коли основна маса коштує 8 000+ грн)
    if len(combined) >= 2:
        max_price = max(float(o.price) for o in combined)
        if max_price >= 2500.0:
            filtered_by_floor = [o for o in combined if float(o.price) >= max_price * 0.12]
            if filtered_by_floor:
                combined = filtered_by_floor

    # Сортуємо: спочатку релевантні за назвою і від найнижчої ціни до вищої
    combined.sort(
        key=lambda item: (
            0 if _is_relevant_title(item.title, clean_query, effective_query) else 1,
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
