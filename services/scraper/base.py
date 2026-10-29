from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
import random

from fake_useragent import UserAgent
import httpx

# Реалістичні профілі браузерів (User-Agent + узгоджені Client Hints)
_BROWSER_PROFILES: list[dict[str, str]] = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Linux"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) "
            "Gecko/20100101 Firefox/132.0"
        ),
        "Sec-Ch-Ua": "",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    },
]

_ACCEPT_LANGUAGES: list[str] = [
    "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-US,en;q=0.9,uk-UA;q=0.8,uk;q=0.7",
    "en-GB,en;q=0.9,en-US;q=0.8,de;q=0.7",
    "uk,en-US;q=0.9,en;q=0.8,pl;q=0.7",
]

_ANTIBOT_MARKERS: tuple[str, ...] = (
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
    "just a moment...",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "access denied |",
    "ddos-guard",
    "verify you are human",
)


def compute_backoff_with_jitter(
    attempt: int,
    base_delay: float = 0.85,
    max_delay: float = 12.0,
) -> float:
    """
    Обчислює затримку за алгоритмом Exponential Backoff + Full Jitter (AWS Architecture pattern),
    щоб уникнути синхронних повторних запитів (Thundering Herd) при 429/503.
    """
    exp_cap = min(max_delay, base_delay * (2 ** max(0, attempt - 1)))
    return round(random.uniform(base_delay * 0.5, max(base_delay, exp_cap)), 3)


def is_antibot_challenge(
    response_text: str,
    status_code: int,
    headers: dict[str, str] | httpx.Headers | None = None,
) -> bool:
    """
    Визначає, чи сторінка є антибот-заглушкою (Cloudflare, DDoS-Guard, Akamai, Captcha),
    навіть якщо сервер повернув HTTP 200 або 403/503.
    """
    if headers is not None:
        lower_headers = {str(k).lower(): str(v).lower() for k, v in headers.items()}
        if "cf-mitigated" in lower_headers:
            return True

    if status_code in (403, 429, 503):
        sample = (response_text or "")[:8192].lower()
        if any(marker in sample for marker in _ANTIBOT_MARKERS):
            return True

    sample_head = (response_text or "")[:4096].lower()
    if "<title>just a moment..." in sample_head or "cf_chl_opt" in sample_head:
        return True
    return False


@dataclass(slots=True, frozen=True)
class ScrapedProduct:
    """Уніфікована DTO-модель результату парсингу товару з будь-якого маркетплейсу."""

    url: str
    title: str
    price: Decimal
    currency: str = "UAH"
    image_url: str | None = None
    marketplace: str = "rozetka"
    is_available: bool = True
    old_price: Decimal | None = None

    @property
    def current_price(self) -> Decimal:
        """Сумісний аліас для доступу до поточної ціни."""
        return self.price

    @property
    def clean_url(self) -> str:
        """Сумісний аліас для очищеного URL."""
        return self.url


class BaseScraper(ABC):
    """Абстрактний базовий клас для асинхронних парсерів маркетплейсів."""

    marketplace_name: str = "unknown"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        try:
            self._ua = UserAgent(browsers=["chrome", "firefox", "edge", "safari"])
        except Exception:
            self._ua = None

    def build_headers(
        self,
        *,
        accept_json: bool = False,
        referer: str | None = None,
    ) -> dict[str, str]:
        """
        Генерує реалістичні HTTP-заголовки з ротацією User-Agent, Accept-Language
        та консистентними Client Hints (Sec-Ch-Ua*).
        """
        profile = random.choice(_BROWSER_PROFILES)
        user_agent = profile["User-Agent"]
        if self._ua and random.random() < 0.35:
            try:
                user_agent = self._ua.random
            except Exception:
                pass

        headers: dict[str, str] = {
            "User-Agent": user_agent,
            "Accept": (
                "application/json, text/plain, */*"
                if accept_json
                else "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "empty" if accept_json else "document",
            "Sec-Fetch-Mode": "cors" if accept_json else "navigate",
            "Sec-Fetch-Site": "same-origin" if accept_json else "none",
            "Upgrade-Insecure-Requests": "1",
        }
        if profile.get("Sec-Ch-Ua"):
            headers["Sec-Ch-Ua"] = profile["Sec-Ch-Ua"]
            headers["Sec-Ch-Ua-Mobile"] = profile["Sec-Ch-Ua-Mobile"]
            headers["Sec-Ch-Ua-Platform"] = profile["Sec-Ch-Ua-Platform"]
        if referer:
            headers["Referer"] = referer
        return headers

    @abstractmethod
    async def fetch_product(self, url: str) -> ScrapedProduct:
        """Витягує метадані та актуальну ціну товару за очищеним URL."""
        raise NotImplementedError
