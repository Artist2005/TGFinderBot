from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import logging
import time

import httpx

logger = logging.getLogger(__name__)

NBU_EXCHANGE_API_URL = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json"
_RATES_TTL_SECONDS = 43200.0  # 12 годин кешування у пам'яті (0 навантаження на сервер)

# Надійні базові курси НБУ (fallback, якщо API НБУ тимчасово недоступне)
_NBU_RATES: dict[str, Decimal] = {
    "UAH": Decimal("1.00"),
    "USD": Decimal("41.55"),
    "EUR": Decimal("45.10"),
    "PLN": Decimal("10.65"),
    "GBP": Decimal("53.80"),
    "CZK": Decimal("1.78"),
    "CHF": Decimal("47.50"),
    "CAD": Decimal("29.90"),
    "AUD": Decimal("27.10"),
    "TRY": Decimal("1.21"),
    "JPY": Decimal("0.27"),
    "RON": Decimal("9.05"),
    "BGN": Decimal("23.05"),
}

_LAST_FETCH_TS: float = 0.0


async def ensure_nbu_rates(force: bool = False) -> dict[str, Decimal]:
    """
    Оновлює курси валют із офіційного JSON API НБУ раз на 12 годин.
    Працює надшвидко і не навантажує оперативну пам'ять сервера.
    """
    global _LAST_FETCH_TS
    now = time.monotonic()
    if not force and _LAST_FETCH_TS > 0 and (now - _LAST_FETCH_TS) < _RATES_TTL_SECONDS:
        return _NBU_RATES

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(NBU_EXCHANGE_API_URL)
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list):
                    updated = 0
                    for item in payload:
                        if not isinstance(item, dict):
                            continue
                        cc = str(item.get("cc") or "").strip().upper()
                        rate_raw = item.get("rate")
                        if cc and rate_raw is not None:
                            try:
                                rate_val = Decimal(str(rate_raw))
                                if rate_val > Decimal("0"):
                                    _NBU_RATES[cc] = rate_val
                                    updated += 1
                            except Exception:
                                continue
                    if updated > 0:
                        _LAST_FETCH_TS = now
                        logger.debug("Оновлено курси валют НБУ (%d валют).", updated)
    except Exception as exc:
        logger.debug("Використовуємо кешовані курси НБУ (%s)", exc)
        if _LAST_FETCH_TS == 0.0:
            _LAST_FETCH_TS = now - (_RATES_TTL_SECONDS - 1800.0)

    return _NBU_RATES


_CURRENCY_ALIASES: dict[str, str] = {
    "₴": "UAH",
    "ГРН": "UAH",
    "UAH": "UAH",
    "$": "USD",
    "USD": "USD",
    "US$": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "ZŁ": "PLN",
    "ZL": "PLN",
    "PLN": "PLN",
    "£": "GBP",
    "GBP": "GBP",
    "KČ": "CZK",
    "CZK": "CZK",
}


def _normalize_curr(currency: str | None) -> str:
    raw = (currency or "UAH").strip().upper()
    return _CURRENCY_ALIASES.get(raw, raw)


def get_nbu_rate(currency: str) -> Decimal | None:
    """Повертає курс валюти до гривні (UAH) за даними НБУ."""
    curr = _normalize_curr(currency)
    if curr == "UAH":
        return Decimal("1.00")
    return _NBU_RATES.get(curr)


def to_uah(amount: Decimal | float | int, currency: str = "UAH") -> Decimal:
    """Перераховує суму з іноземної валюти ($ / € / zł / £) у гривню (₴) за курсом НБУ."""
    dec_val = Decimal(str(amount))
    curr = _normalize_curr(currency)
    if curr == "UAH" or dec_val <= Decimal("0"):
        return dec_val.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    rate = _NBU_RATES.get(curr)
    if rate is None:
        return dec_val
    return (dec_val * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def format_uah_equivalent(amount: Decimal | float | int, currency: str = "UAH") -> str:
    """
    Повертає рядок формату ' (~6 180 ₴)' для іноземних валют ($ / € / zł / £)
    або порожній рядок для UAH.
    """
    curr = _normalize_curr(currency)
    if curr == "UAH":
        return ""
    try:
        dec_val = Decimal(str(amount))
    except Exception:
        return ""
    if dec_val <= Decimal("0") or curr not in _NBU_RATES:
        return ""
    uah_val = int(to_uah(dec_val, curr))
    return f" (~{uah_val:,} ₴)".replace(",", " ")
