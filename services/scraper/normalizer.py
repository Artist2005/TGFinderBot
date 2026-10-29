from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from services.scraper.exceptions import InvalidUrlError, SSRFBlockedError

# Регулярний вираз для витягування першого URL із довільного тексту повідомлення
URL_EXTRACT_REGEX = re.compile(r"(https?://[^\s<>\"']+)", re.IGNORECASE)

# Заборонені внутрішні домени, сервіси метаданих хмар (AWS/GCP/Azure) та DNS-rebinding сервіси
FORBIDDEN_HOST_SUFFIXES: tuple[str, ...] = (
    "localhost",
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".arpa",
    "metadata.google.internal",
    "instance-data",
    ".nip.io",
    ".sslip.io",
    ".xip.io",
    ".localtest.me",
    ".lvh.me",
)

# Список трекінгових та реферальних GET-параметрів, які завжди видаляються
TRACKING_QUERY_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "yclid",
        "dclid",
        "msclkid",
        "twclid",
        "gad_source",
        "gbraid",
        "wbraid",
        "af_sub1",
        "af_sub2",
        "af_sub3",
        "af_sub4",
        "af_sub5",
        "aff_sub",
        "aff_uid",
        "subid",
        "partner",
        "ref",
        "ref_",
        "psc",
        "pd_rd_i",
        "pd_rd_r",
        "pd_rd_w",
        "pd_rd_wg",
        "pf_rd_i",
        "pf_rd_m",
        "pf_rd_p",
        "pf_rd_r",
        "pf_rd_s",
        "pf_rd_t",
        "qid",
        "sr",
        "dib",
        "dib_tag",
        "keywords",
        "crid",
        "sprefix",
        "th",
        "source",
        "_gl",
        "_ga",
        "srsltid",
        "sc_content",
        "spm",
        "scm",
        "pdp_npi",
        "_t",
    }
)

# Додаткові підсторінки товару Rozetka, які ведуть на ту саму картку товару
ROZETKA_SUBPAGE_SUFFIX_RE = re.compile(
    r"(/p\d+)/(?:characteristics|comments|photo|video|accessories|all-offers)/?$",
    re.IGNORECASE,
)

# Канонізація Amazon ASIN
AMAZON_ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE)

# Мапінг доменів українських та світових маркетплейсів
MARKETPLACE_DOMAINS: dict[str, str] = {
    # Україна
    "rozetka.com.ua": "rozetka",
    "rozetka.ua": "rozetka",
    "prom.ua": "prom",
    "epicentrk.ua": "epicentr",
    "27.ua": "epicentr",
    "comfy.ua": "comfy",
    "allo.ua": "allo",
    "foxtrot.com.ua": "foxtrot",
    "moyo.ua": "moyo",
    "ctrs.com.ua": "citrus",
    "citrus.ua": "citrus",
    "makeup.com.ua": "makeup",
    "makeupstore.com": "makeup",
    "makeup.pl": "makeup",
    "makeup.de": "makeup",
    "kasta.ua": "kasta",
    "olx.ua": "olx",
    "olx.pl": "olx",
    "olx.ro": "olx",
    "olx.pt": "olx",
    "olx.bg": "olx",
    "telemart.ua": "telemart",
    "brain.com.ua": "brain",
    "eva.ua": "eva",
    "maudau.com.ua": "maudau",
    "hotline.ua": "hotline",
    "ek.ua": "ekatalog",
    "answear.ua": "answear",
    "answear.pl": "answear",
    "intertop.ua": "intertop",
    "shafa.ua": "shafa",
    "stylus.ua": "stylus",
    "touch.com.ua": "touch",
    "yabko.ua": "yabko",
    # Глобальні маркетплейси (США, Європа, Азія)
    "amazon.com": "amazon",
    "amazon.de": "amazon",
    "amazon.co.uk": "amazon",
    "amazon.pl": "amazon",
    "amazon.fr": "amazon",
    "amazon.es": "amazon",
    "amazon.it": "amazon",
    "amazon.nl": "amazon",
    "amazon.ca": "amazon",
    "amazon.com.au": "amazon",
    "amazon.co.jp": "amazon",
    "ebay.com": "ebay",
    "ebay.de": "ebay",
    "ebay.co.uk": "ebay",
    "ebay.pl": "ebay",
    "ebay.fr": "ebay",
    "ebay.it": "ebay",
    "ebay.es": "ebay",
    "aliexpress.com": "aliexpress",
    "aliexpress.us": "aliexpress",
    "aliexpress.ru": "aliexpress",
    "walmart.com": "walmart",
    "bestbuy.com": "bestbuy",
    "target.com": "target",
    "newegg.com": "newegg",
    "bhphotovideo.com": "bhphoto",
    "allegro.pl": "allegro",
    "allegro.cz": "allegro",
    "ceneo.pl": "ceneo",
    "x-kom.pl": "xkom",
    "mediaexpert.pl": "mediaexpert",
    "morele.net": "morele",
    "mediamarkt.de": "mediamarkt",
    "mediamarkt.pl": "mediamarkt",
    "mediamarkt.es": "mediamarkt",
    "saturn.de": "saturn",
    "otto.de": "otto",
    "idealo.de": "idealo",
    "zalando.de": "zalando",
    "zalando.pl": "zalando",
    "zalando.co.uk": "zalando",
    "asos.com": "asos",
    "farfetch.com": "farfetch",
    "iherb.com": "iherb",
    "steampowered.com": "steam",
    "epicgames.com": "epicgames",
    "gog.com": "gog",
    "playstation.com": "playstation",
    "nike.com": "nike",
    "adidas.com": "adidas",
    "apple.com": "apple",
    "samsung.com": "samsung",
    "ikea.com": "ikea",
    "zara.com": "zara",
    "hm.com": "hm",
    "shein.com": "shein",
    "temu.com": "temu",
    "etsy.com": "etsy",
}

MARKETPLACE_LABELS: dict[str, str] = {
    "rozetka": "🟢 Rozetka",
    "prom": "🟣 Prom.ua",
    "epicentr": "🔵 Epicentr",
    "comfy": "🟠 Comfy",
    "allo": "🔴 Allo",
    "foxtrot": "🦊 Foxtrot",
    "moyo": "🔷 MOYO",
    "citrus": "🍊 Citrus",
    "makeup": "💄 MAKEUP",
    "kasta": "🛍 Kasta",
    "olx": "📦 OLX",
    "telemart": "💻 Telemart",
    "brain": "🧠 Brain",
    "eva": "🌸 EVA",
    "maudau": "🛒 MAUDAU",
    "hotline": "🔥 Hotline",
    "ekatalog": "📑 E-Katalog",
    "answear": "👗 Answear",
    "intertop": "👟 Intertop",
    "shafa": "🧥 Shafa",
    "stylus": "📱 Stylus",
    "touch": "📲 Touch",
    "yabko": "🍏 Yabko",
    "amazon": "📦 Amazon",
    "ebay": "🏷 eBay",
    "aliexpress": "🏮 AliExpress",
    "walmart": "🌐 Walmart",
    "bestbuy": "💛 BestBuy",
    "target": "🎯 Target",
    "newegg": "🥚 Newegg",
    "bhphoto": "📷 B&H Photo",
    "allegro": "🟠 Allegro",
    "ceneo": "📊 Ceneo",
    "xkom": "💻 x-kom",
    "mediaexpert": "⚡ MediaExpert",
    "morele": "🖥 Morele",
    "mediamarkt": "🔴 MediaMarkt",
    "saturn": "🪐 Saturn",
    "otto": "🟥 OTTO",
    "idealo": "📈 Idealo",
    "zalando": "👟 Zalando",
    "asos": "🖤 ASOS",
    "farfetch": "✨ Farfetch",
    "iherb": "🌿 iHerb",
    "steam": "🎮 Steam",
    "epicgames": "🕹 Epic Games",
    "gog": "🟣 GOG",
    "playstation": "🎮 PlayStation",
    "nike": "👟 Nike",
    "adidas": "👟 Adidas",
    "apple": "🍎 Apple",
    "samsung": "🌌 Samsung",
    "ikea": "🏠 IKEA",
    "zara": "🧥 Zara",
    "hm": "👕 H&M",
    "shein": "🛍 SHEIN",
    "temu": "🧡 Temu",
    "etsy": "🧶 Etsy",
}

Ukrainian_MARKETPLACES: frozenset[str] = frozenset(
    {
        "rozetka",
        "prom",
        "epicentr",
        "comfy",
        "allo",
        "foxtrot",
        "moyo",
        "citrus",
        "kasta",
        "telemart",
        "brain",
        "eva",
        "maudau",
        "hotline",
        "ekatalog",
        "intertop",
        "shafa",
        "stylus",
        "touch",
        "yabko",
    }
)

POLISH_MARKETPLACES: frozenset[str] = frozenset(
    {"allegro", "ceneo", "xkom", "mediaexpert", "morele"}
)


def _check_ip_obj_safe(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address, raw_url: str) -> None:
    """Перевіряє, чи не належить IP-адреса до приватних, loopback, link-local або зарезервованих діапазонів."""
    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ):
        raise SSRFBlockedError(
            f"Запит до внутрішньої або зарезервованої IP-адреси ({ip_obj}) заблоковано (SSRF Protection): {raw_url}"
        )


def validate_url_ssrf_safe(url: str) -> None:
    """
    Синхронна перевірка URL на SSRF-вразливості:
    - Дозволяє виключно схеми http / https та стандартні веб-порти (80, 443).
    - Забороняє авторизаційні дані в URL (user:pass@host).
    - Блокує localhost, внутрішні домени (.local, .internal, nip.io) та прямі IP-адреси.
    - Блокує приватні мережі (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, 169.254.0.0/16, ::1).
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise InvalidUrlError(f"Непідтримувана схема URL: {parsed.scheme}")

    if parsed.username is not None or parsed.password is not None:
        raise SSRFBlockedError("URL із вбудованими обліковими даними (user:pass@) заборонені.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise InvalidUrlError("Некоректний порт у URL.") from exc

    if port is not None and port not in (80, 443):
        raise SSRFBlockedError(f"Нестандартний порт ({port}) заблоковано політикою безпеки SSRF.")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise InvalidUrlError("У посиланні відсутній хост.")

    if host in FORBIDDEN_HOST_SUFFIXES or any(host.endswith(suffix) for suffix in FORBIDDEN_HOST_SUFFIXES):
        raise SSRFBlockedError(f"Внутрішній або локальний хост '{host}' заблоковано (SSRF Protection).")

    # Перевірка на числове/IP представлення хоста (включно з IPv4, IPv6, десятковим/шістнадцятковим форматом)
    try:
        ip_obj = ipaddress.ip_address(host)
        _check_ip_obj_safe(ip_obj, url)
        # Навіть публічні прямі IP без доменного імені забороняємо для товарних карток
        raise SSRFBlockedError("Прямі IP-адреси без доменного імені не дозволені.")
    except ValueError:
        pass

    # Захист від цілочисельного або восьмеричного кодування IPv4 (наприклад, http://2130706433 або http://0177.0.0.1)
    if host.replace(".", "").isdigit():
        raise SSRFBlockedError("Числові IP-адреси заборонені політикою безпеки SSRF.")

    if "." not in host:
        raise InvalidUrlError(f"Некоректне доменне ім'я: {host}")


async def verify_url_dns_ssrf_safe(url: str) -> None:
    """
    Асинхронна глибока перевірка DNS-записів хоста перед виконанням HTTP-запиту.
    Захищає від DNS Rebinding атак, коли зовнішній домен резолвиться у 127.0.0.1 або 169.254.169.254.
    """
    validate_url_ssrf_safe(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    host_clean = host.removeprefix("www.").removeprefix("m.")

    # Довірені офіційні маркетплейси зі білого списку проходять миттєво після базової перевірки
    for trusted_domain in MARKETPLACE_DOMAINS:
        if host_clean == trusted_domain or host_clean.endswith(f".{trusted_domain}"):
            return

    loop = asyncio.get_running_loop()
    try:
        addr_info = await loop.getaddrinfo(
            host,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        # Якщо домен тимчасово не резолвиться — мережевий рівень httpx коректно обробить помилку
        return

    for family, _, _, _, sockaddr in addr_info:
        raw_ip = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        _check_ip_obj_safe(ip_obj, url)


def format_marketplace_label(marketplace: str) -> str:
    """Повертає красиву назву маркетплейсу з іконкою."""
    return MARKETPLACE_LABELS.get(marketplace.lower(), f"🌐 {marketplace.capitalize()}")


def format_marketplace_name(marketplace: str) -> str:
    """Повертає чисту назву маркетплейсу без початкового емодзі (для компактних кнопок)."""
    label = format_marketplace_label(marketplace)
    parts = label.split(" ", 1)
    if len(parts) == 2 and not parts[0].isalnum():
        return parts[1][:18]
    return label[:18]


get_marketplace_label = format_marketplace_label


def detect_default_currency(url: str, marketplace: str = "") -> str:
    """Визначає базову валюту магазину за доменом або маркетплейсом (fallback)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    mp = marketplace.lower()

    if host.endswith(".ua") or mp in Ukrainian_MARKETPLACES:
        return "UAH"
    if host.endswith(".pl") or mp in POLISH_MARKETPLACES:
        return "PLN"
    if host.endswith(".co.uk") or host.endswith(".uk"):
        return "GBP"
    if host.endswith((".de", ".fr", ".es", ".it", ".nl", ".be", ".at", ".pt", ".fi", ".ie", ".eu")) or mp in {
        "otto",
        "saturn",
        "idealo",
    }:
        return "EUR"
    if host.endswith(".cz"):
        return "CZK"
    if host.endswith(".ro"):
        return "RON"
    if host.endswith(".bg"):
        return "BGN"
    if host.endswith(".ca"):
        return "CAD"
    if host.endswith(".com.au") or host.endswith(".au"):
        return "AUD"
    if host.endswith(".co.jp") or host.endswith(".jp"):
        return "JPY"
    if host.endswith(".kz"):
        return "KZT"
    if host.endswith(".com.tr") or host.endswith(".tr"):
        return "TRY"
    if host.endswith(".ch"):
        return "CHF"
    return "USD"


def extract_first_url(text: str) -> str:
    """Витягує перше HTTP/HTTPS посилання з тексту користувача."""
    match = URL_EXTRACT_REGEX.search(text.strip())
    if not match:
        raise InvalidUrlError("У повідомленні не знайдено коректного посилання (http:// або https://).")
    return match.group(1).rstrip(".,;!)>\"'")


def detect_marketplace(url: str) -> str:
    """
    Визначає назву маркетплейсу за доменом посилання.
    Виконує SSRF-валідацію перед розбором.
    """
    validate_url_ssrf_safe(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")

    if not host or "." not in host or parsed.scheme not in {"http", "https"}:
        raise InvalidUrlError(f"Некоректний URL: {url}")

    for domain, mp_key in MARKETPLACE_DOMAINS.items():
        if host == domain or host.endswith(f".{domain}"):
            return mp_key

    parts = host.split(".")
    if len(parts) >= 2:
        second_level_tlds = {"com", "co", "net", "org", "gov", "edu", "in", "shop", "store"}
        if len(parts) >= 3 and parts[-2] in second_level_tlds:
            return parts[-3]
        return parts[-2]
    return host


def normalize_url(raw_text_or_url: str) -> tuple[str, str]:
    """
    Очищує вхідний URL від UTM-міток, трекінгових параметрів і якорів,
    перевіряє його на SSRF-безпеку та визначає маркетплейс.
    Повертає кортеж: (clean_url, marketplace).
    """
    extracted_url = extract_first_url(raw_text_or_url)
    validate_url_ssrf_safe(extracted_url)
    marketplace = detect_marketplace(extracted_url)
    parsed = urlparse(extracted_url)

    scheme = "https"
    netloc = (parsed.hostname or "").lower()
    if netloc in {"www.rozetka.com.ua", "rozetka.ua", "m.rozetka.com.ua"}:
        netloc = "rozetka.com.ua"

    path = parsed.path or "/"

    if marketplace == "rozetka":
        path = ROZETKA_SUBPAGE_SUFFIX_RE.sub(r"\1/", path)
        if not path.endswith("/"):
            path = f"{path}/"
        clean_query = ""
    elif marketplace == "amazon":
        asin_match = AMAZON_ASIN_RE.search(path)
        if asin_match:
            path = f"/dp/{asin_match.group(1)}"
        clean_query = ""
    else:
        filtered_params = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if k.lower() not in TRACKING_QUERY_PARAMS and not k.lower().startswith("utm_")
        ]
        clean_query = urlencode(filtered_params, doseq=True)

    clean_url = urlunparse((scheme, netloc, path, "", clean_query, ""))
    return clean_url, marketplace
