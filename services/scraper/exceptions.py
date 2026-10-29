from __future__ import annotations


class ScraperError(Exception):
    """Базовий виняток для всіх помилок модуля парсингу."""


class InvalidUrlError(ScraperError):
    """Посилання не є валідним HTTP/HTTPS URL."""


class SSRFBlockedError(InvalidUrlError):
    """Запит заблоковано системою безпеки (SSRF-захист: локальні/приватні IP або метадані)."""


class MarketplaceNotSupportedError(ScraperError):
    """Маркетплейс за вказаним доменом ще не підтримується."""


class ProductNotFoundError(ScraperError):
    """Сторінку товару не знайдено (HTTP 404 або невірний ID товару)."""


class ProductOutOfStockError(ScraperError):
    """Товар тимчасово відсутній у продажу і ціна недоступна."""


class RateLimitOrBlockedError(ScraperError):
    """Запит заблоковано антибот-системою (403/429 Cloudflare/Captcha) після всіх спроб."""


class ParsingStructureError(ScraperError):
    """Не вдалося витягнути ціну або назву товару через зміну верстки/API."""


class AnomalousPriceError(ScraperError):
    """Виявлено аномальне відхилення ціни (>90% падіння або <= 0), потрібна верифікація."""
