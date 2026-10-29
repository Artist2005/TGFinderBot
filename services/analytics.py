from __future__ import annotations

import csv
from decimal import Decimal
import html
import io
import math
from typing import Any, Sequence

from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.i18n import fmt_money, fmt_money_with_uah, normalize_lang, t
from bot.keyboards.inline import (
    ITEMS_PER_PAGE,
    WISHLIST_CATEGORIES,
    _GENERIC_PREFIX_RE,
    build_add_guide_keyboard,
    build_ai_forecast_keyboard,
    build_pro_offer_keyboard,
    build_search_results_keyboard,
    build_shared_wishlist_keyboard,
    build_start_keyboard,
    build_top_drops_keyboard,
    build_tracking_list_keyboard,
    format_category_badge,
)
from config import settings
from database import crud
from database.models import Product, User, UserProduct
from services.currency import to_uah
from services.scraper.normalizer import format_marketplace_label, format_marketplace_name


def truncate_title(title: str, max_len: int = 38) -> str:
    """Скорочує назву товару, прибираючи зайві загальні префікси, для ідеальної адаптивності на смартфонах."""
    clean = " ".join((title or "").split())
    stripped = _GENERIC_PREFIX_RE.sub("", clean).strip()
    if len(stripped) >= 4:
        clean = stripped
    if len(clean) <= max_len:
        return html.escape(clean)
    return html.escape(clean[: max_len - 1].rstrip() + "…")


from dataclasses import dataclass


@dataclass(frozen=True)
class PriceQuantMetrics:
    """
    Професійні статистичні метрики 30-денного цінового ряду (Robust Econometric Model):
    - Стійкі до викидів (Winsorized / IQR / Median / MAD)
    - Враховують експоненційне зважування свіжих цін (EWMA)
    - Включають байєсівське згладжування ймовірності та оцінку похибки моделі (SE)
    """

    current_price: Decimal
    min_30d: Decimal
    max_30d: Decimal
    avg_30d: Decimal
    median_30d: Decimal
    ewma_price: Decimal
    equilibrium_price: Decimal
    q15_support: Decimal
    q85_resistance: Decimal
    iqr: Decimal
    volatility_pct: float
    channel_position: float  # 0.0 (на дні каналу) .. 1.0 (на вершині каналу)
    z_score: float
    trend_slope_pct: float  # % зміни за тиждень за МНК (OLS)
    drop_probability_7d: int  # 5..95 (%)
    model_error_pct: int  # Похибка оцінки ±SE (%)
    deal_score: int  # 1..99
    optimal_target: Decimal


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    """Обчислює неперервний квантиль порядку q (0.0..1.0) методом лінійної інтерполяції."""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = max(0.0, min(1.0, q)) * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    weight = pos - lo
    return sorted_vals[lo] * (1.0 - weight) + sorted_vals[hi] * weight


def _normal_cdf(x: float) -> float:
    """Стандартна функція нормального розподілу Φ(x) через функцію похибки Гаусса erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def analyze_price_series(
    current_price: Decimal | float,
    history_prices: Sequence[Decimal | float] | None = None,
    *,
    old_price: Decimal | float | None = None,
) -> PriceQuantMetrics:
    """
    Професійний математичний аналіз цінового ряду зі зменшеною похибкою оцінки:
    1. Фільтрація викидів (Winsorization за інтерквартильним розмахом Тукі IQR = Q75 - Q25).
    2. Оцінка рівноважної ціни через комбінацію медіани (50%), EWMA (30%) та усіченого середнього (20%).
    3. Оцінка реалізованої волатильності через комбінацію коефіцієнта варіації (CV),
       логарифмічної прибутковості (Log-Returns) та робастного абсолютного відхилення (MAD).
    4. Прогноз імовірності зниження ціни за 7 днів на основі процесу повернення до середнього
       (Ornstein-Uhlenbeck Z-score), тренду МНК (OLS) та байєсівського згладжування Beta-апріорі.
    """
    cur_f = max(0.01, float(current_price))
    raw_series = [float(p) for p in (history_prices or []) if float(p) > 0]
    if not raw_series:
        raw_series = [cur_f]
    elif abs(raw_series[-1] - cur_f) > 1e-6:
        raw_series = list(raw_series) + [cur_f]

    n = len(raw_series)
    sorted_raw = sorted(raw_series)

    # 1. Квантилі та робастне відсікання аномальних одноденних стрибків (Winsorization)
    q10 = _quantile(sorted_raw, 0.10)
    q15 = _quantile(sorted_raw, 0.15)
    q25 = _quantile(sorted_raw, 0.25)
    q50 = _quantile(sorted_raw, 0.50)
    q75 = _quantile(sorted_raw, 0.75)
    q85 = _quantile(sorted_raw, 0.85)
    q90 = _quantile(sorted_raw, 0.90)
    iqr = max(0.0, q75 - q25)

    lower_fence = q25 - 1.5 * iqr if iqr > 0 else sorted_raw[0]
    upper_fence = q75 + 1.5 * iqr if iqr > 0 else sorted_raw[-1]
    winsor_series = [min(max(x, lower_fence), upper_fence) for x in raw_series]
    sorted_winsor = sorted(winsor_series)

    mn_f = min(cur_f, sorted_raw[0])
    mx_f = max(cur_f, sorted_raw[-1])
    winsor_mean = sum(winsor_series) / n

    # 2. Експоненційно зважене середнє (EWMA) — свіжі точки мають більшу вагу
    alpha = 2.0 / min(12.0, n + 1.0)
    ewma = winsor_series[0]
    for val in winsor_series[1:]:
        ewma = alpha * val + (1.0 - alpha) * ewma

    # Робастний центр тяжіння ціни (Equilibrium Price)
    eq_price = 0.50 * q50 + 0.30 * ewma + 0.20 * winsor_mean

    # 3. Робастна волатильність: комбінація CV (Winsorized), MAD та волатильності лог-приростів
    if n > 1:
        variance = sum((x - winsor_mean) ** 2 for x in winsor_series) / (n - 1)
        std_winsor = math.sqrt(max(0.0, variance))
    else:
        std_winsor = 0.0

    abs_devs = sorted(abs(x - q50) for x in winsor_series)
    mad = _quantile(abs_devs, 0.50)
    robust_sigma = 1.4826 * mad  # Стандартний перерахунок MAD -> еквівалент σ для нормального розподілу

    log_returns_sq: list[float] = []
    down_moves = 0
    total_moves = 0
    for i in range(1, n):
        p_prev, p_curr = winsor_series[i - 1], winsor_series[i]
        if p_prev > 0 and p_curr > 0:
            lr = math.log(p_curr / p_prev)
            log_returns_sq.append(lr * lr)
            if abs(lr) > 1e-4:
                total_moves += 1
                if lr < 0:
                    down_moves += 1

    if log_returns_sq:
        step_log_vol = math.sqrt(sum(log_returns_sq) / len(log_returns_sq))
        horizon_log_vol_pct = step_log_vol * math.sqrt(min(n, 5)) * 100.0
    else:
        horizon_log_vol_pct = 0.0

    cv_pct = (std_winsor / max(0.01, eq_price)) * 100.0
    mad_pct = (robust_sigma / max(0.01, eq_price)) * 100.0
    eff_sigma = max(0.55 * std_winsor + 0.45 * robust_sigma, 0.012 * eq_price)

    # Підсумкова згладжена волатильність за 30 днів (у %)
    raw_vol_pct = 0.45 * cv_pct + 0.35 * horizon_log_vol_pct + 0.20 * mad_pct
    # Якщо передані лише агрегати (min/max/avg) без повного ряду, враховуємо піврозмах
    if n <= 3 and mx_f > mn_f:
        half_range_pct = ((mx_f - mn_f) / (2.0 * max(0.01, eq_price))) * 100.0
        raw_vol_pct = max(raw_vol_pct, 0.65 * half_range_pct)
    volatility_pct = round(max(0.0, min(95.0, raw_vol_pct)), 1)

    # 4. Позиція в робастному каналі та Z-score
    eff_low = min(cur_f, sorted_winsor[0])
    eff_high = max(cur_f, sorted_winsor[-1])
    if eff_high > eff_low:
        channel_pos = (cur_f - eff_low) / (eff_high - eff_low)
    else:
        channel_pos = 0.25
    channel_pos = max(0.0, min(1.0, channel_pos))

    z_score = (cur_f - eq_price) / max(0.01, eff_sigma)

    # 5. Лінійний тренд методом найменших квадратів (OLS slope) на останніх точках
    recent = winsor_series[-min(n, 8) :]
    m = len(recent)
    if m >= 2:
        x_mean = (m - 1) / 2.0
        y_mean = sum(recent) / m
        num = sum((i - x_mean) * (recent[i] - y_mean) for i in range(m))
        den = sum((i - x_mean) ** 2 for i in range(m))
        slope_per_step = (num / den) if den > 0 else 0.0
        trend_slope_pct = (slope_per_step * min(m, 4) / max(0.01, eq_price)) * 100.0
    else:
        trend_slope_pct = 0.0

    # 6. Професійний Deal Score (комбінація Φ(-z), позиції в каналі та емпіричного перцентиля)
    prob_cheaper_than_eq = _normal_cdf(-0.85 * z_score)  # (0..1), 0.5 при cur == eq_price
    strictly_higher = sum(1 for x in raw_series if x > cur_f + 1e-6)
    equal_pts = sum(1 for x in raw_series if abs(x - cur_f) <= 1e-6)
    empirical_rank = (strictly_higher + 0.5 * equal_pts) / n  # частка часу, коли ціна була вищою

    if cur_f <= mn_f * 1.002 and mx_f > mn_f * 1.01:
        base_score = 94.0
    else:
        signal_blend = (
            0.45 * prob_cheaper_than_eq
            + 0.35 * (1.0 - channel_pos)
            + 0.20 * empirical_rank
        )
        base_score = 32.0 + 62.0 * signal_blend

    # Захист від штучно завищених "старих цін" (Fake MRP filter):
    # враховуємо стару ціну тільки в межах розумного верхнього квантиля історії (до +8% над Q90/max)
    verified_bonus = 0.0
    if old_price is not None and float(old_price) > cur_f:
        realistic_old = min(float(old_price), max(mx_f, q90) * 1.06)
        if realistic_old > cur_f:
            real_disc_pct = ((realistic_old - cur_f) / realistic_old) * 100.0
            verified_bonus = min(7.0, real_disc_pct * 0.35)

    deal_score = int(round(max(15.0, min(99.0, base_score + verified_bonus))))

    # 7. Ймовірність знижки за 7 днів (Ornstein-Uhlenbeck Mean Reversion + Bayesian Beta Shrinkage)
    # P_reversion: чим вище ціна над рівновагою (z_score > 0), тим сильніше тяжіння вниз до медіани
    p_reversion = _normal_cdf(1.10 * z_score + 0.45 * (channel_pos - 0.5))
    # Байєсівська частота знижень з апріорним розподілом Beta(2.0, 2.0)
    p_empirical_down = (down_moves + 2.0) / (total_moves + 4.0)
    # Коефіцієнт рухливості ціни (якщо ціна місяць стоїть заморожена, ймовірність раптової зміни нижча)
    activity_factor = min(1.0, 0.42 + (volatility_pct / 8.0))
    # При сильному перегріві (z_score > 1.0) домінує сила повернення до середнього (Mean Reversion)
    rev_weight = 0.72 if z_score > 0.8 else 0.58
    momentum_adj = max(-0.05, min(0.06, -trend_slope_pct * 0.008))

    raw_prob = (rev_weight * p_reversion + (1.0 - rev_weight) * p_empirical_down + momentum_adj) * (
        0.52 + 0.48 * activity_factor
    )
    # Байєсівське стискання (Shrinkage) при малій вибірці точок N
    sample_confidence = n / (n + 2.5)
    calibrated_prob = sample_confidence * raw_prob + (1.0 - sample_confidence) * 0.46
    drop_prob_7d = int(round(max(15.0, min(91.0, calibrated_prob * 100.0))))

    # Стандартна похибка оцінки ймовірності (Standard Error SE, %) після фільтрації викидів
    p_hat = drop_prob_7d / 100.0
    se_pct = int(round(max(2.0, min(7.0, 100.0 * math.sqrt(p_hat * (1.0 - p_hat) / (n + 18.0))))))

    # 8. Оптимальна цільова ціна (Support Zone Q15 + адаптація під реальну волатильність σ)
    quant = Decimal("1") if cur_f >= 100.0 else Decimal("0.01")
    support_level = 0.62 * q15 + 0.38 * eff_low

    if support_level < cur_f * 0.982:
        # Обмежуємо максимальну глибину цілі розумним горизонтом 2.2σ (щоб ціль була досяжною)
        reachable_floor = max(support_level, cur_f - 2.25 * eff_sigma, cur_f * 0.84)
        opt_f = min(cur_f * 0.975, reachable_floor)
    else:
        # Ціна вже біля дна 30-денного каналу -> адаптуємо рекомендовану знижку до реальної волатильності товару
        adaptive_drop_ratio = max(0.03, min(0.09, 0.025 + 0.80 * (volatility_pct / 100.0)))
        opt_f = cur_f * (1.0 - adaptive_drop_ratio)

    optimal_target = max(Decimal("0.50"), Decimal(str(opt_f)).quantize(quant))

    return PriceQuantMetrics(
        current_price=Decimal(str(current_price)),
        min_30d=Decimal(str(round(mn_f, 2))),
        max_30d=Decimal(str(round(mx_f, 2))),
        avg_30d=Decimal(str(round(winsor_mean, 2))),
        median_30d=Decimal(str(round(q50, 2))),
        ewma_price=Decimal(str(round(ewma, 2))),
        equilibrium_price=Decimal(str(round(eq_price, 2))),
        q15_support=Decimal(str(round(q15, 2))),
        q85_resistance=Decimal(str(round(q85, 2))),
        iqr=Decimal(str(round(iqr, 2))),
        volatility_pct=volatility_pct,
        channel_position=channel_pos,
        z_score=round(z_score, 2),
        trend_slope_pct=round(trend_slope_pct, 2),
        drop_probability_7d=drop_prob_7d,
        model_error_pct=se_pct,
        deal_score=deal_score,
        optimal_target=optimal_target,
    )


def compute_deal_score(
    *,
    current_price: Decimal,
    min_30d: Decimal,
    max_30d: Decimal,
    avg_30d: Decimal,
    old_price: Decimal | None = None,
    history_prices: Sequence[Decimal | float] | None = None,
) -> int:
    """
    Розраховує професійний індекс вигоди покупки (Deal Score від 15 до 99)
    на основі Z-індексу відносно рівноважної ціни (Median + EWMA), квантильного каналу
    та верифікованої знижки (із захистом від фейкових закреслених цін).
    """
    series = list(history_prices) if history_prices else [min_30d, avg_30d, max_30d, current_price]
    metrics = analyze_price_series(
        current_price=current_price,
        history_prices=series,
        old_price=old_price,
    )
    return metrics.deal_score


def render_dashboard_card(db_user: User, items_count: int = 0) -> str:
    """
    Єдине зрозуміле вікно головного меню (/start та 🏠 Меню).
    Для нового користувача (0 товарів) — максимально легке, дружнє та без візуального перевантаження.
    """
    lang = normalize_lang(db_user.language)
    max_limit = settings.pro_max_products if db_user.has_active_pro else settings.free_max_products
    tier = "⭐️ PRO" if db_user.has_active_pro else "Free"

    if items_count == 0:
        if lang == "en":
            return (
                "🛍 <b>PRICE TRACKER · SMART SHOPPING</b>\n"
                "──────────────────────────────────\n"
                "👋 <b>Welcome! Adding your first item takes 3 seconds:</b>\n\n"
                "🔍 <b>Option 1 — Type a product name:</b>\n"
                "Just write e.g. <code>AirPods Pro 2</code> or <code>PlayStation 5</code> in chat — I'll find the lowest prices!\n\n"
                "🔗 <b>Option 2 — Paste a store link:</b>\n"
                "Send any URL from Rozetka, Prom, Comfy, Amazon, eBay, AliExpress, etc.\n"
                "──────────────────────────────────\n"
                "👇 <i>Type a product name or paste a link below:</i>"
            )
        if lang == "ru":
            return (
                "🛍 <b>PRICE TRACKER · УМНЫЙ ПОИСК СКИДОК</b>\n"
                "──────────────────────────────────\n"
                "👋 <b>Добро пожаловать! Начать очень просто:</b>\n\n"
                "🔍 <b>Способ 1 — Просто напишите название товара:</b>\n"
                "Например: <code>AirPods Pro 2</code> или <code>PlayStation 5</code> — я за 3 секунды сравню цены в магазинах!\n\n"
                "🔗 <b>Способ 2 — Или отправьте ссылку:</b>\n"
                "Киньте ссылку из Rozetka, Prom, Comfy, Алло, Эпицентр, OLX, Amazon и др.\n"
                "──────────────────────────────────\n"
                "👇 <i>Напишите название товара или вставьте ссылку в чат:</i>"
            )
        return (
            "🛍 <b>PRICE TRACKER · РОЗУМНИЙ МОНІТОРИНГ ЦІН</b>\n"
            "──────────────────────────────────\n"
            "👋 <b>Вітаємо! Почати дуже просто (без зайвих налаштувань):</b>\n\n"
            "🔍 <b>Спосіб 1 — Просто напишіть назву товару:</b>\n"
            "Наприклад: <code>AirPods Pro 2</code> або <code>PlayStation 5</code> — бот за 3 секунди порівняє ціни в магазинах!\n\n"
            "🔗 <b>Спосіб 2 — Або киньте посилання:</b>\n"
            "Надішліть лінк із Rozetka, Prom, Comfy, Алло, Епіцентр, MakeUp, OLX, Amazon тощо.\n"
            "──────────────────────────────────\n"
            "👇 <i>Напишіть назву товару в чат або оберіть дію нижче:</i>"
        )

    interval = "12h" if (lang == "en" and db_user.has_active_pro) else (
        "24h" if lang == "en" else (
            "12 год" if (lang == "uk" and db_user.has_active_pro) else (
                "24 год" if lang == "uk" else ("12 ч" if db_user.has_active_pro else "24 ч")
            )
        )
    )

    if lang == "en":
        return (
            "🛍 <b>PRICE TRACKER · SMART SHOPPING ASSISTANT</b>\n"
            "──────────────────────────────────\n"
            f"👤 <b>Plan:</b> <code>{tier}</code> · 📦 <b>Tracked:</b> <code>{items_count}/{max_limit}</code> · ⏱ <code>{interval}</code>\n"
            "──────────────────────────────────\n"
            "• 🔍 <b>Search by name:</b> just type e.g. <code>iPhone 16</code> in chat\n"
            "• 🔗 <b>Add by link:</b> paste 1 or up to 10 store URLs\n"
            "• 🟢 <b>Stock Sniper:</b> paste an out-of-stock link to catch restocks\n"
            "• 💬 <b>Inline mode:</b> type <code>@bot query</code> in any chat with friends\n"
            "──────────────────────────────────\n"
            "👇 <b>Type a product name, paste a link, or choose an action:</b>"
        )
    if lang == "ru":
        return (
            "🛍 <b>PRICE TRACKER · УМНЫЙ МОНИТОРИНГ ЦЕН</b>\n"
            "──────────────────────────────────\n"
            f"👤 <b>Тариф:</b> <code>{tier}</code> · 📦 <b>Товаров:</b> <code>{items_count}/{max_limit}</code> · ⏱ <code>{interval}</code>\n"
            "──────────────────────────────────\n"
            "• 🔍 <b>Поиск по названию:</b> просто напишите <code>iPhone 16</code> в чат\n"
            "• 🔗 <b>По ссылке:</b> отправьте от 1 до 10 ссылок на товары\n"
            "• 🟢 <b>Снайпер наличия:</b> ловим товары, которые раскупили\n"
            "• 📁 <b>Умные подборки:</b> группируйте товары в вишлисты и делитесь с друзьями\n"
            "──────────────────────────────────\n"
            "👇 <b>Напишите название товара, вставьте ссылку или выберите раздел:</b>"
        )
    return (
        "🛍 <b>PRICE TRACKER · РОЗУМНИЙ МОНІТОРИНГ ЦІН</b>\n"
        "──────────────────────────────────\n"
        f"👤 <b>Тариф:</b> <code>{tier}</code> · 📦 <b>Товарів:</b> <code>{items_count}/{max_limit}</code> · ⏱ <code>{interval}</code>\n"
        "──────────────────────────────────\n"
        "• 🔍 <b>Пошук за назвою:</b> просто напишіть у чат <code>AirPods Pro 2</code>\n"
        "• 🔗 <b>За посиланням:</b> надішліть від 1 до 10 лінків із будь-якого магазину\n"
        "• 🟢 <b>Снайпер наявності:</b> сповістимо, щойно розкуплений товар з'явиться!\n"
        "• 📁 <b>Добірки та Вішлісти:</b> групуйте товари («Збірка ПК») та діліться з друзями\n"
        "──────────────────────────────────\n"
        "👇 <b>Напишіть назву товару, киньте посилання або оберіть розділ:</b>"
    )


def render_add_product_guide_card(lang: str = "uk") -> str:
    """Дружня покрокова інструкція «🔍 Знайти / Додати товар» (пошук текстом + посилання + снайпер)."""
    lang = normalize_lang(lang)
    if lang == "en":
        return (
            "🔍 <b>FIND BY NAME OR ADD BY LINK</b>\n"
            "──────────────────────────────────\n"
            "1️⃣ <b>No link? Just type the product name!</b>\n"
            "Write e.g. <code>AirPods Pro 2</code>, <code>PlayStation 5 Slim</code> or <code>RTX 4070</code> right here in chat — the bot will compare stores and let you track the lowest price in 1 tap!\n\n"
            "2️⃣ <b>Have a link? Paste 1 or up to 10 URLs!</b>\n"
            "Supports Rozetka, Prom, Comfy, Allo, Epicentr, OLX, Amazon, AliExpress ($/€/zł auto-converted to ₴ at NBU rate).\n\n"
            "3️⃣ <b>🟢 Back-in-Stock Sniper:</b>\n"
            "Even if an item is sold out, send its link — we'll alert you the second it's back in stock!\n"
            "──────────────────────────────────\n"
            "👇 <i>Type a product name or paste a link below!</i>"
        )
    if lang == "ru":
        return (
            "🔍 <b>ПОИСК ПО НАЗВАНИЮ ИЛИ ССЫЛКЕ</b>\n"
            "──────────────────────────────────\n"
            "1️⃣ <b>Нет ссылки под рукой? Просто напишите название!</b>\n"
            "Напишите в чат, например: <code>AirPods Pro 2</code>, <code>PlayStation 5 Slim</code> или <code>iPhone 16</code> — бот за 3 секунды найдёт самые дешёвые цены в магазинах!\n\n"
            "2️⃣ <b>Есть ссылка? Отправьте от 1 до 10 ссылок!</b>\n"
            "Rozetka, Prom, Comfy, Алло, Эпицентр, OLX, Amazon, Allegro ($/€/zł автоматически переводятся в ₴ по курсу НБУ).\n\n"
            "3️⃣ <b>🟢 «Снайпер наличия»:</b>\n"
            "Если товар раскупили — всё равно кидайте ссылку! Бот мгновенно пришлёт тревогу, как только он появится.\n"
            "──────────────────────────────────\n"
            "👇 <i>Просто напишите название товара или вставьте ссылку в чат!</i>"
        )
    return (
        "🔍 <b>ПОШУК ЗА НАЗВОЮ АБО ЗА ПОСИЛАННЯМ</b>\n"
        "──────────────────────────────────\n"
        "1️⃣ <b>Немає посилання? Просто напишіть назву в чат!</b>\n"
        "Напишіть, наприклад: <code>AirPods Pro 2</code>, <code>PlayStation 5 Slim</code> або <code>iPhone 16</code> — бот за 3 секунди порівняє ціни в магазинах і дасть кнопку «➕ Стежити»!\n\n"
        "2️⃣ <b>Маєте посилання? Надішліть від 1 до 10 лінків!</b>\n"
        "Rozetka, Prom, Comfy, Алло, Епіцентр, MakeUp, OLX, Amazon, Allegro (валюти $/€/zł автоматично рахуються в ₴ за курсом НБУ).\n\n"
        "3️⃣ <b>🟢 «Снайпер наявності»:</b>\n"
        "Якщо товар розкупили — все одно кидайте посилання! Щойно магазин поверне його в продаж, бот миттєво пришле тривогу.\n"
        "──────────────────────────────────\n"
        "👇 <i>Просто напишіть назву товару або вставте посилання у цей чат!</i>"
    )


def build_product_caption_with_30d_stats(
    *,
    title: str,
    marketplace: str,
    currency: str,
    current_price: Decimal,
    old_price: Decimal | None = None,
    is_available: bool = True,
    history_prices: Sequence[Decimal | float] | None = None,
    target_price: Decimal | None = None,
    category_tag: str | None = None,
    status_header: str = "",
    status_line: str = "",
    lang: str = "uk",
) -> str:
    """
    UI-шаблон #1: Картка додавання / перегляду товару (Card Layout).
    Підтримує:
    - Авто-перерахунок валют у гривню за курсом НБУ ($/€/zł ➔ ~₴)
    - Режим «🟢 Снайпер наявності» (коли товар розкуплений)
    - Бейдж розумної добірки / вішлісту (📁 Категорія)
    """
    lang = normalize_lang(lang)
    cur_dec = Decimal(str(current_price))
    cur_f = float(cur_dec)

    valid_prices = [float(p) for p in (history_prices or []) if float(p) > 0]
    if not valid_prices:
        valid_prices = [cur_f] if cur_f > 0 else [0.0]

    min_30d = Decimal(str(min(valid_prices)))
    max_30d = Decimal(str(max(valid_prices)))
    avg_30d = Decimal(str(sum(valid_prices) / len(valid_prices)))
    start_30d = Decimal(str(valid_prices[0]))
    curr = currency or "UAH"

    mp_label = format_marketplace_label(marketplace)
    short_title = truncate_title(title, max_len=48)
    header_text = status_header or status_line or t("status_tracking_on", lang)
    avail_badge = t("avail_yes", lang) if is_available else t("avail_no", lang)

    cat_badge = format_category_badge(category_tag, lang)
    cat_part = f" · {cat_badge}" if cat_badge else ""

    if not is_available or cur_dec <= Decimal("0"):
        sniper_lbl = {
            "uk": "🎯 <b>Снайпер наявності:</b> <code>АКТИВНИЙ 🟢</code>\n<i>Щойно магазин поверне товар у продаж — бот миттєво надішле вам тривогу!</i>",
            "en": "🎯 <b>Back-in-Stock Sniper:</b> <code>ACTIVE 🟢</code>\n<i>The second this item returns to stock, you'll get an instant alert!</i>",
            "ru": "🎯 <b>Снайпер наличия:</b> <code>АКТИВЕН 🟢</code>\n<i>Как только магазин вернёт товар в продажу — бот мгновенно пришлёт тревогу!</i>",
        }.get(lang, "")
        last_known = (
            f"\n💰 <b>Остання відома ціна:</b> <code>{fmt_money_with_uah(cur_dec, curr)}</code>"
            if cur_dec > Decimal("0")
            else ""
        )
        return (
            f"{header_text}\n"
            f"──────────────────────────────────\n"
            f"📦 <b>{short_title}</b>\n"
            f"🏪 {mp_label} · {avail_badge}{cat_part}\n\n"
            f"{sniper_lbl}{last_known}\n"
            f"──────────────────────────────────"
        )

    # Визначаємо референсну стару ціну для бейджа динаміки
    ref_old = old_price if (old_price and old_price > cur_dec) else start_30d

    if ref_old > cur_dec:
        diff_val = ref_old - cur_dec
        pct = int(round(((float(ref_old) - cur_f) / float(ref_old)) * 100))
        lbl = {"uk": "Ціна впала:", "en": "Price dropped:", "ru": "Цена упала:"}.get(lang, "Ціна впала:")
        save_word = {"uk": "вигода", "en": "save", "ru": "выгода"}.get(lang, "вигода")
        price_badge = (
            f"🟢 <b>{lbl}</b> <code>{fmt_money_with_uah(cur_dec, curr)}</code> "
            f"<s>{fmt_money(ref_old, curr)}</s> (<code>-{max(1, pct)}%</code> · {save_word} <code>{fmt_money(diff_val, curr)}</code>)"
        )
    elif cur_dec > start_30d and start_30d > Decimal("0"):
        pct = int(round(((cur_f - float(start_30d)) / max(0.01, float(start_30d))) * 100))
        lbl = {"uk": "Ціна піднялася:", "en": "Price rose:", "ru": "Цена выросла:"}.get(lang, "Ціна піднялася:")
        price_badge = (
            f"🔴 <b>{lbl}</b> <code>{fmt_money_with_uah(cur_dec, curr)}</code> "
            f"<s>{fmt_money(start_30d, curr)}</s> (<code>+{max(1, pct)}%</code>)"
        )
    else:
        lbl = {"uk": "Поточна ціна:", "en": "Current price:", "ru": "Текущая цена:"}.get(lang, "Поточна ціна:")
        price_badge = f"💎 <b>{lbl}</b> <code>{fmt_money_with_uah(cur_dec, curr)}</code>"

    # Бейдж історичного мінімуму за 30 днів
    is_hist_low = cur_dec <= min_30d and (max_30d > min_30d or (old_price and old_price > cur_dec))
    hist_low_line = ""
    if is_hist_low:
        hist_lbl = {
            "uk": "🔥 <b>Історичний мінімум за 30 днів!</b>",
            "en": "🔥 <b>30-Day Historical Low!</b>",
            "ru": "🔥 <b>Исторический минимум за 30 дней!</b>",
        }.get(lang, "🔥 <b>Історичний мінімум за 30 днів!</b>")
        hist_low_line = f"{hist_lbl}\n"

    deal_score = compute_deal_score(
        current_price=cur_dec,
        min_30d=min_30d,
        max_30d=max_30d,
        avg_30d=avg_30d,
        old_price=ref_old,
        history_prices=valid_prices,
    )
    if deal_score >= 88:
        verdict = {
            "uk": "🔥 Дуже вигідно купувати",
            "en": "🔥 Great time to buy",
            "ru": "🔥 Отличная цена для покупки",
        }.get(lang, "🔥 Дуже вигідно купувати")
    elif deal_score >= 70:
        verdict = {
            "uk": "👍 Хороша ринкова ціна",
            "en": "👍 Good market price",
            "ru": "👍 Хорошая рыночная цена",
        }.get(lang, "👍 Хороша ринкова ціна")
    else:
        verdict = {
            "uk": "⏳ Краще почекати знижки",
            "en": "⏳ Better wait for a drop",
            "ru": "⏳ Лучше подождать скидки",
        }.get(lang, "⏳ Краще почекати знижки")

    if target_price is not None:
        tgt_dec = Decimal(str(target_price))
        if cur_dec <= tgt_dec:
            tgt_status = {
                "uk": "✅ Ціль досягнуто!",
                "en": "✅ Target reached!",
                "ru": "✅ Цель достигнута!",
            }.get(lang, "✅ Ціль досягнуто!")
        else:
            rem = cur_dec - tgt_dec
            tgt_status = {
                "uk": f"ще −{fmt_money(rem, curr)} до цілі",
                "en": f"−{fmt_money(rem, curr)} to target",
                "ru": f"ещё −{fmt_money(rem, curr)} до цели",
            }.get(lang, f"ще −{fmt_money(rem, curr)}")
        tgt_lbl = {"uk": "Ваша ціль:", "en": "Your target:", "ru": "Ваша цель:"}.get(lang, "Ваша ціль:")
        target_line = f"🎯 <b>{tgt_lbl}</b> <code>{fmt_money_with_uah(tgt_dec, curr)}</code> (<i>{tgt_status}</i>)\n"
    else:
        target_line = {
            "uk": "🎯 <b>Ваша ціль:</b> <i>не задана (сповістимо про будь-яку знижку)</i>\n",
            "en": "🎯 <b>Your target:</b> <i>not set (alerts on any price drop)</i>\n",
            "ru": "🎯 <b>Ваша цель:</b> <i>не задана (сообщим о любой скидке)</i>\n",
        }.get(lang, "")

    score_lbl = {"uk": "Вигода (Deal Score):", "en": "Deal Score:", "ru": "Выгода (Deal Score):"}.get(lang, "Вигода (Deal Score):")
    lbl_30d = {"uk": "Ціни за 30 днів:", "en": "30-Day Price Range:", "ru": "Цены за 30 дней:"}.get(lang, "Ціни за 30 днів:")
    lbl_min = {"uk": "Мін", "en": "Min", "ru": "Мин"}.get(lang, "Мін")
    lbl_avg = {"uk": "Сер", "en": "Avg", "ru": "Сред"}.get(lang, "Сер")
    lbl_max = {"uk": "Макс", "en": "Max", "ru": "Макс"}.get(lang, "Макс")
    quick_tip = {
        "uk": "💡 <i>Швидка ціль знижки: натисніть 🎯-5%, 🎯-10% або 🎯-15% нижче</i>",
        "en": "💡 <i>Quick target: tap 🎯-5%, 🎯-10% or 🎯-15% below</i>",
        "ru": "💡 <i>Быстрая цель скидки: нажмите 🎯-5%, 🎯-10% или 🎯-15% ниже</i>",
    }.get(lang, "")

    return (
        f"{header_text}\n"
        f"──────────────────────────────────\n"
        f"📦 <b>{short_title}</b>\n"
        f"🏪 {mp_label} · {avail_badge}{cat_part}\n\n"
        f"{price_badge}\n"
        f"{hist_low_line}"
        f"{target_line}"
        f"⚖️ <b>{score_lbl}</b> <code>{deal_score}/100</code> ({verdict})\n"
        f"──────────────────────────────────\n"
        f"📊 <b>{lbl_30d}</b>\n"
        f"🟢 {lbl_min}: <code>{fmt_money(min_30d, curr)}</code> · "
        f"〰️ {lbl_avg}: <code>{fmt_money(avg_30d, curr)}</code> · "
        f"🔴 {lbl_max}: <code>{fmt_money(max_30d, curr)}</code>\n"
        f"{quick_tip}"
    )


def render_target_price_card(
    *,
    title: str,
    current_price: Decimal,
    currency: str = "UAH",
    current_target: Decimal | None = None,
    lang: str = "uk",
) -> str:
    """
    Зрозумілий екран налаштування бажаної ціни (Target Price) з точним розрахунком
    суми та економії в грошах для -5%, -10%, -15% та -20%.
    """
    lang = normalize_lang(lang)
    cur = Decimal(str(current_price))
    curr = currency or "UAH"
    quant = Decimal("1") if cur >= Decimal("100") else Decimal("0.01")
    short_title = truncate_title(title, max_len=46)

    def _p(factor: str) -> tuple[str, str]:
        target_val = max(Decimal("0.50"), (cur * Decimal(factor)).quantize(quant))
        saved_val = max(Decimal("0"), cur - target_val)
        return fmt_money(target_val, curr), fmt_money(saved_val, curr)

    p5, s5 = _p("0.95")
    p10, s10 = _p("0.90")
    p15, s15 = _p("0.85")
    p20, s20 = _p("0.80")
    suggested_val = max(Decimal("0.50"), (cur * Decimal("0.90")).quantize(quant))

    if lang == "en":
        tgt_now = f"🎯 <b>Active target:</b> <code>{fmt_money(current_target, curr)}</code>\n" if current_target else ""
        return (
            "🎯 <b>SET YOUR TARGET PRICE</b>\n"
            "──────────────────────────────────\n"
            f"📦 <b>{short_title}</b>\n"
            f"💰 <b>Current price:</b> <code>{fmt_money(cur, curr)}</code>\n"
            f"{tgt_now}"
            "──────────────────────────────────\n"
            "<b>Choose a quick discount preset below:</b>\n"
            f"• <b>🎯 -5%</b>  → <code>{p5}</code> (save <code>{s5}</code>)\n"
            f"• <b>🎯 -10%</b> → <code>{p10}</code> (save <code>{s10}</code>)\n"
            f"• <b>🎯 -15%</b> → <code>{p15}</code> (save <code>{s15}</code>)\n"
            f"• <b>🎯 -20%</b> → <code>{p20}</code> (save <code>{s20}</code>)\n"
            "──────────────────────────────────\n"
            f"✍️ <i>Or simply type your desired price in chat (for example: <code>{suggested_val}</code>):</i>"
        )
    if lang == "ru":
        tgt_now = f"🎯 <b>Текущая цель:</b> <code>{fmt_money(current_target, curr)}</code>\n" if current_target else ""
        return (
            "🎯 <b>НАСТРОЙКА ЖЕЛАЕМОЙ ЦЕНЫ</b>\n"
            "──────────────────────────────────\n"
            f"📦 <b>{short_title}</b>\n"
            f"💰 <b>Сейчас в магазине:</b> <code>{fmt_money(cur, curr)}</code>\n"
            f"{tgt_now}"
            "──────────────────────────────────\n"
            "<b>Выберите готовую скидку кнопкой ниже:</b>\n"
            f"• <b>🎯 -5%</b>  → <code>{p5}</code> (экономия <code>{s5}</code>)\n"
            f"• <b>🎯 -10%</b> → <code>{p10}</code> (экономия <code>{s10}</code>)\n"
            f"• <b>🎯 -15%</b> → <code>{p15}</code> (экономия <code>{s15}</code>)\n"
            f"• <b>🎯 -20%</b> → <code>{p20}</code> (экономия <code>{s20}</code>)\n"
            "──────────────────────────────────\n"
            f"✍️ <i>Или просто напишите желаемую сумму числом в чат (например: <code>{suggested_val}</code>):</i>"
        )

    tgt_now = f"🎯 <b>Встановлена ціль:</b> <code>{fmt_money(current_target, curr)}</code>\n" if current_target else ""
    return (
        "🎯 <b>НАЛАШТУВАННЯ БАЖАНОЇ ЦІНИ</b>\n"
        "──────────────────────────────────\n"
        f"📦 <b>{short_title}</b>\n"
        f"💰 <b>Зараз у магазині:</b> <code>{fmt_money(cur, curr)}</code>\n"
        f"{tgt_now}"
        "──────────────────────────────────\n"
        "<b>Оберіть готову знижку кнопкою нижче:</b>\n"
        f"• <b>🎯 -5%</b>  → <code>{p5}</code> (економія <code>{s5}</code>)\n"
        f"• <b>🎯 -10%</b> → <code>{p10}</code> (економія <code>{s10}</code>)\n"
        f"• <b>🎯 -15%</b> → <code>{p15}</code> (економія <code>{s15}</code>)\n"
        f"• <b>🎯 -20%</b> → <code>{p20}</code> (економія <code>{s20}</code>)\n"
        "──────────────────────────────────\n"
        f"✍️ <i>Або просто напишіть свою бажану суму числом у чат (наприклад: <code>{suggested_val}</code>):</i>"
    )


def render_delete_confirm_card(title: str, lang: str = "uk") -> str:
    """Картка підтвердження видалення одного товару."""
    lang = normalize_lang(lang)
    short_t = truncate_title(title, max_len=46)
    if lang == "en":
        return (
            "🗑 <b>REMOVE ITEM FROM TRACKING?</b>\n"
            "──────────────────────────────────\n"
            f"📦 <b>{short_t}</b>\n\n"
            "Are you sure you want to stop monitoring this item?"
        )
    if lang == "ru":
        return (
            "🗑 <b>УДАЛИТЬ ТОВАР ИЗ СПИСКА?</b>\n"
            "──────────────────────────────────\n"
            f"📦 <b>{short_t}</b>\n\n"
            "Вы уверены, что хотите остановить мониторинг цен для этого товара?"
        )
    return (
        "🗑 <b>ВИДАЛИТИ ТОВАР ЗІ СПИСКУ?</b>\n"
        "──────────────────────────────────\n"
        f"📦 <b>{short_t}</b>\n\n"
        "Ви впевнені, що хочете зупинити моніторинг ціни для цього товару?"
    )


def render_clear_confirm_card(count: int, lang: str = "uk") -> str:
    """Картка підтвердження очищення всього списку відстеження."""
    lang = normalize_lang(lang)
    if lang == "en":
        return (
            "⚠️ <b>CLEAR ALL TRACKED ITEMS?</b>\n"
            "──────────────────────────────────\n"
            f"You are about to remove all <code>{count}</code> items from your tracking list.\n\n"
            "Confirm clearing your list?"
        )
    if lang == "ru":
        return (
            "⚠️ <b>ОЧИСТИТЬ ВЕСЬ СПИСОК ТОВАРОВ?</b>\n"
            "──────────────────────────────────\n"
            f"Из отслеживания будут удалены все товары (<code>{count} шт.</code>).\n\n"
            "Подтверждаете очистку списка?"
        )
    return (
        "⚠️ <b>ОЧИСТИТИ ВЕСЬ СПИСОК ТОВАРІВ?</b>\n"
        "──────────────────────────────────\n"
        f"З вашого списку буде видалено всі відстежувані товари (<code>{count} шт.</code>).\n\n"
        "Підтверджуєте очищення списку?"
    )


def build_price_drop_alert_card(
    *,
    title: str,
    marketplace: str,
    currency: str,
    old_price: Decimal,
    new_price: Decimal,
    min_30d: Decimal | None = None,
    max_30d: Decimal | None = None,
    avg_30d: Decimal | None = None,
    target_price: Decimal | None = None,
    lang: str = "uk",
) -> str:
    """
    UI-шаблон #2: Картка миттєвого сповіщення про падіння ціни (Price Drop Alert Card).
    """
    lang = normalize_lang(lang)
    curr = currency or "UAH"
    old_dec = Decimal(str(old_price))
    new_dec = Decimal(str(new_price))
    diff = max(Decimal("0"), old_dec - new_dec)
    pct = int(round((float(diff) / max(0.01, float(old_dec))) * 100))

    mn = Decimal(str(min_30d)) if min_30d is not None else new_dec
    mx = Decimal(str(max_30d)) if max_30d is not None else old_dec
    av = Decimal(str(avg_30d)) if avg_30d is not None else ((old_dec + new_dec) / Decimal("2"))

    deal_score = compute_deal_score(
        current_price=new_dec,
        min_30d=mn,
        max_30d=mx,
        avg_30d=av,
        old_price=old_dec,
    )
    if deal_score >= 88:
        verdict = {"uk": "Дуже вигідна покупка", "en": "Great time to buy", "ru": "Отличная покупка"}.get(lang, "Дуже вигідна покупка")
    elif deal_score >= 70:
        verdict = {"uk": "Хороша ціна", "en": "Good price", "ru": "Хорошая цена"}.get(lang, "Хороша ціна")
    else:
        verdict = {"uk": "Ціна знизилась", "en": "Price dropped", "ru": "Цена снизилась"}.get(lang, "Ціна знизилась")

    short_title = truncate_title(title, max_len=48)
    mp_label = format_marketplace_label(marketplace)

    header = {
        "uk": "⚡️ <b>ЦІНОВИЙ АЛЕРТ · ЗНИЖКА!</b>",
        "en": "⚡️ <b>PRICE DROP ALERT!</b>",
        "ru": "⚡️ <b>ЦЕНОВОЙ АЛЕРТ · СКИДКА!</b>",
    }.get(lang, "⚡️ <b>ЦІНОВИЙ АЛЕРТ · ЗНИЖКА!</b>")

    lbl_drop = {"uk": "Ціна впала:", "en": "Price dropped:", "ru": "Цена упала:"}.get(lang, "Ціна впала:")
    lbl_save = {"uk": "Ваша економія:", "en": "You save:", "ru": "Ваша экономия:"}.get(lang, "Ваша економія:")

    is_hist_low = new_dec <= mn
    hist_line = ""
    if is_hist_low:
        hist_lbl = {
            "uk": "🔥 <b>Історичний мінімум за 30 днів!</b>\n",
            "en": "🔥 <b>30-Day Historical Low!</b>\n",
            "ru": "🔥 <b>Исторический минимум за 30 дней!</b>\n",
        }.get(lang, "🔥 <b>Історичний мінімум за 30 днів!</b>\n")
        hist_line = hist_lbl

    target_line = ""
    if target_price is not None and new_dec <= Decimal(str(target_price)):
        tgt_lbl = {
            "uk": f"🎯 <b>Ціль досягнуто:</b> <code>{fmt_money(target_price, curr)}</code>\n",
            "en": f"🎯 <b>Target reached:</b> <code>{fmt_money(target_price, curr)}</code>\n",
            "ru": f"🎯 <b>Цель достигнута:</b> <code>{fmt_money(target_price, curr)}</code>\n",
        }.get(lang, "")
        target_line = tgt_lbl

    return (
        f"{header}\n"
        f"──────────────────────────────────\n"
        f"📦 <b>{short_title}</b>\n"
        f"🏪 {mp_label}\n\n"
        f"🟢 <b>{lbl_drop}</b> <code>{fmt_money_with_uah(new_dec, curr)}</code> "
        f"<s>{fmt_money(old_dec, curr)}</s> (<code>-{max(1, pct)}%</code>)\n"
        f"💸 <b>{lbl_save}</b> <code>-{fmt_money(diff, curr)}</code>\n"
        f"{hist_line}"
        f"{target_line}"
        f"⚖️ <b>Deal Score:</b> <code>{deal_score}/100</code> ({verdict})"
    )


def build_back_in_stock_alert_card(
    *,
    title: str,
    marketplace: str,
    currency: str,
    new_price: Decimal,
    lang: str = "uk",
) -> str:
    """
    Картка миттєвої тривоги «🚨 СНАЙПЕР НАЯВНОСТІ · ТОВАР З'ЯВИВСЯ!» (Back-in-Stock Alert).
    """
    lang = normalize_lang(lang)
    curr = currency or "UAH"
    new_dec = Decimal(str(new_price))
    short_title = truncate_title(title, max_len=48)
    mp_label = format_marketplace_label(marketplace)

    if lang == "en":
        return (
            "🚨 <b>BACK IN STOCK ALERT!</b>\n"
            "──────────────────────────────────\n"
            f"📦 <b>{short_title}</b>\n"
            f"🏪 {mp_label} · 🟢 <b>In Stock Now!</b>\n\n"
            f"💰 <b>Price:</b> <code>{fmt_money_with_uah(new_dec, curr)}</code>\n"
            "⚡️ <i>Hurry up and grab it before it sells out again!</i>"
        )
    if lang == "ru":
        return (
            "🚨 <b>СНАЙПЕР НАЛИЧИЯ · ТОВАР ПОЯВИЛСЯ!</b>\n"
            "──────────────────────────────────\n"
            f"📦 <b>{short_title}</b>\n"
            f"🏪 {mp_label} · 🟢 <b>В наличии!</b>\n\n"
            f"💰 <b>Цена:</b> <code>{fmt_money_with_uah(new_dec, curr)}</code>\n"
            "⚡️ <i>Успейте забрать товар, пока партию снова не раскупили!</i>"
        )
    return (
        "🚨 <b>СНАЙПЕР НАЯВНОСТІ · ТОВАР З'ЯВИВСЯ!</b>\n"
        "──────────────────────────────────\n"
        f"📦 <b>{short_title}</b>\n"
        f"🏪 {mp_label} · 🟢 <b>Знову в наявності!</b>\n\n"
        f"💰 <b>Ціна зараз:</b> <code>{fmt_money_with_uah(new_dec, curr)}</code>\n"
        "⚡️ <i>Встигніть замовити, поки партію знову не розкупили!</i>"
    )


def render_search_results_card(
    query: str,
    offers: Sequence[Any],
    lang: str = "uk",
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Відображає порівняльну таблицю цін за текстовим запитом (без посилання!)
    із кнопкою «➕ Відстежувати» в 1 клік для кожної пропозиції.
    """
    lang = normalize_lang(lang)
    safe_q = html.escape(query.strip()[:50])

    if not offers:
        if lang == "en":
            txt = (
                f"🔍 <b>Search: «{safe_q}»</b>\n"
                "──────────────────────────────────\n"
                "😕 Couldn't find direct offers by this name right now.\n\n"
                "💡 <b>Tip:</b> Try a shorter model name (e.g. <code>AirPods Pro 2</code>) or paste a direct link from any store!"
            )
        elif lang == "ru":
            txt = (
                f"🔍 <b>Поиск: «{safe_q}»</b>\n"
                "──────────────────────────────────\n"
                "😕 По этому запросу не удалось найти точных предложений.\n\n"
                "💡 <b>Совет:</b> Уточните модель (например, <code>AirPods Pro 2</code>) или просто отправьте прямую ссылку из магазина!"
            )
        else:
            txt = (
                f"🔍 <b>Пошук: «{safe_q}»</b>\n"
                "──────────────────────────────────\n"
                "😕 За цим запитом зараз не знайдено точних пропозицій.\n\n"
                "💡 <b>Порада:</b> Спробуйте написати коротшу назву моделі (наприклад, <code>AirPods Pro 2</code>) або просто вставте пряме посилання з магазину!"
            )
        return txt, build_add_guide_keyboard(lang)

    header = {
        "uk": f"🔍 <b>НАЙНИЖЧІ ЦІНИ: «{safe_q}»</b>",
        "en": f"🔍 <b>LOWEST PRICES: «{safe_q}»</b>",
        "ru": f"🔍 <b>ЛУЧШИЕ ЦЕНЫ: «{safe_q}»</b>",
    }.get(lang, "")

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rows: list[str] = []
    for idx, off in enumerate(offers[:6], start=1):
        mp_short = format_marketplace_name(off.marketplace)
        short_t = truncate_title(off.title, max_len=34)
        price_fmt = fmt_money_with_uah(off.price, off.currency)
        old_part = ""
        if off.old_price and off.old_price > off.price:
            pct = max(1, int(round(float((off.old_price - off.price) / off.old_price) * 100)))
            old_part = f" <s>{fmt_money(off.old_price, off.currency)}</s> (<code>-{pct}%</code>)"

        avail_icon = "🟢" if getattr(off, "is_available", True) else "🔴 Снайпер"
        medal = medals.get(idx, "🔹")
        best_tag = (
            " 🏆 <b>НАЙДЕШЕВШЕ</b>"
            if (idx == 1 and len(offers) > 1 and lang == "uk")
            else (" 🏆 <b>LOWEST</b>" if (idx == 1 and len(offers) > 1) else "")
        )
        rows.append(
            f"{medal} <b>#{idx} · <code>{price_fmt}</code></b>{old_part}{best_tag}\n"
            f"└ 🏪 <b>{mp_short}</b> {avail_icon} · <a href=\"{html.escape(off.url)}\">{short_t}</a>"
        )

    footer = {
        "uk": "👇 <i>Натисніть на товар нижче, щоб відстежувати ціну в 1 клік:</i>",
        "en": "👇 <i>Tap any offer below to start tracking in 1 click:</i>",
        "ru": "👇 <i>Нажмите на товар ниже, чтобы отслеживать цену в 1 клик:</i>",
    }.get(lang, "")

    text = (
        f"{header}\n"
        "──────────────────────\n"
        + "\n\n".join(rows)
        + f"\n──────────────────────\n{footer}"
    )
    return text, build_search_results_keyboard(offers, lang=lang)


async def render_shared_wishlist_card(
    session: AsyncSession,
    owner_id: int,
    viewer_lang: str = "uk",
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Відображає спільний вішліст друга за deep-link посиланням (/start wishlist_<user_id>).
    Показує всі товари у списку, сумарну вартість у ₴ та кнопки додавання собі в 1 клік.
    """
    lang = normalize_lang(viewer_lang)
    subs = await crud.get_user_subscriptions_with_history(session, owner_id)
    if not subs:
        msg = {
            "uk": "📭 Цей вішліст наразі порожній або власник видалив товари.",
            "en": "📭 This shared wishlist is currently empty.",
            "ru": "📭 Этот вишлист сейчас пуст.",
        }.get(lang, "")
        return msg, build_start_keyboard(lang)

    owner = await session.get(User, owner_id)
    owner_name = (
        html.escape((getattr(owner, "first_name", None) or (f"@{owner.username}" if owner.username else f"User #{owner_id}"))[:32])
        if owner
        else f"#{owner_id}"
    )

    total_now_uah = Decimal("0")
    total_min_uah = Decimal("0")
    rows: list[str] = []

    for idx, sub in enumerate(subs[:10], start=1):
        p = sub.product
        cur = Decimal(str(p.current_price))
        prices = [Decimal(str(h.price)) for h in p.price_history if Decimal(str(h.price)) > 0] if p.price_history else ([cur] if cur > 0 else [])
        mn = min(prices) if prices else cur
        total_now_uah += to_uah(cur, p.currency)
        total_min_uah += to_uah(mn, p.currency)

        mp_short = format_marketplace_name(p.marketplace)
        short_t = truncate_title(p.title, max_len=34)
        cat_badge = format_category_badge(getattr(sub, "category_tag", None), lang)
        cat_str = f" · {cat_badge}" if cat_badge else ""
        price_str = fmt_money_with_uah(cur, p.currency) if cur > 0 else "🟢 Снайпер наявності"

        rows.append(
            f"<b>#{idx} · <code>{price_str}</code></b> · 🏪 <b>{mp_short}</b>{cat_str}\n"
            f"└ <a href=\"{html.escape(p.url)}\">{short_t}</a>"
        )

    potential_save_uah = max(Decimal("0"), total_now_uah - total_min_uah)
    header = {
        "uk": f"🎁 <b>СПІЛЬНИЙ ВІШЛІСТ · {owner_name}</b>",
        "en": f"🎁 <b>SHARED WISHLIST · {owner_name}</b>",
        "ru": f"🎁 <b>ОБЩИЙ ВИШЛИСТ · {owner_name}</b>",
    }.get(lang, "")

    summary = {
        "uk": (
            f"🛒 <b>Вартість списку сьогодні:</b> <code>~{fmt_money(total_now_uah, 'UAH')}</code>\n"
            f"📉 <b>На мінімумі 30д:</b> <code>~{fmt_money(total_min_uah, 'UAH')}</code> "
            f"(потенціал економії <code>{fmt_money(potential_save_uah, 'UAH')}</code>)"
        ),
        "en": (
            f"🛒 <b>Total value today:</b> <code>~{fmt_money(total_now_uah, 'UAH')}</code>\n"
            f"📉 <b>At 30d lows:</b> <code>~{fmt_money(total_min_uah, 'UAH')}</code> "
            f"(potential save <code>{fmt_money(potential_save_uah, 'UAH')}</code>)"
        ),
        "ru": (
            f"🛒 <b>Стоимость списка сегодня:</b> <code>~{fmt_money(total_now_uah, 'UAH')}</code>\n"
            f"📉 <b>На минимуме 30д:</b> <code>~{fmt_money(total_min_uah, 'UAH')}</code> "
            f"(потенциал экономии <code>{fmt_money(potential_save_uah, 'UAH')}</code>)"
        ),
    }.get(lang, "")

    hint = {
        "uk": "👇 <i>Натисніть на будь-який товар нижче, щоб додати його у своє відстеження:</i>",
        "en": "👇 <i>Tap any item below to copy it to your own tracking list:</i>",
        "ru": "👇 <i>Нажмите на любой товар ниже, чтобы добавить его себе в отслеживание:</i>",
    }.get(lang, "")

    text = (
        f"{header}\n"
        f"{summary}\n"
        "──────────────────────────────────\n"
        + "\n\n".join(rows)
        + f"\n──────────────────────────────────\n{hint}"
    )
    return text, build_shared_wishlist_keyboard(subs, lang=lang)


def build_daily_digest_card(
    items: Sequence[dict[str, Any]],
    lang: str = "uk",
) -> str:
    """
    UI-шаблон #3: Щоденний ранковий дайджест цін (Daily Morning Digest Card),
    який агрегує нічні падіння цін (23:00–08:00 при увімкненому DND) або підсумки доби.
    """
    lang = normalize_lang(lang)
    header = {
        "uk": "☕️ <b>РАНКОВИЙ ДАЙДЖЕСТ ЗНИЖОК</b>",
        "en": "☕️ <b>MORNING PRICE DROP DIGEST</b>",
        "ru": "☕️ <b>УТРЕННИЙ ДАЙДЖЕСТ СКИДОК</b>",
    }.get(lang, "☕️ <b>РАНКОВИЙ ДАЙДЖЕСТ ЗНИЖОК</b>")

    sub_header = {
        "uk": "🌙 <i>Зібрано під час Нічного режиму (23:00–08:00)</i>",
        "en": "🌙 <i>Collected during Night Mode (23:00–08:00)</i>",
        "ru": "🌙 <i>Собрано во время Ночного режима (23:00–08:00)</i>",
    }.get(lang, "🌙 <i>Зібрано під час Нічного режиму (23:00–08:00)</i>")

    if not items:
        empty_msg = {
            "uk": "За минулу ніч ціни залишалися стабільними. Ми продовжуємо моніторинг 24/7!",
            "en": "Prices remained stable overnight. We continue monitoring 24/7!",
            "ru": "За прошлую ночь цены оставались стабильными. Продолжаем мониторинг 24/7!",
        }.get(lang, "")
        return f"{header}\n{sub_header}\n──────────────────────────────────\n✨ {empty_msg}"

    rows: list[str] = []
    savings_by_curr: dict[str, Decimal] = {}

    for idx, item in enumerate(items[:8], start=1):
        title = truncate_title(str(item.get("title", "Товар")), max_len=44)
        url = html.escape(str(item.get("url", "")))
        mp_label = format_marketplace_label(str(item.get("marketplace", "store")))
        curr = str(item.get("currency", "UAH"))
        old_p = Decimal(str(item.get("old_price", 0)))
        new_p = Decimal(str(item.get("new_price", 0)))
        diff = max(Decimal("0"), old_p - new_p)
        pct = int(round((float(diff) / max(0.01, float(old_p))) * 100)) if old_p > new_p else 0

        if diff > 0:
            savings_by_curr[curr] = savings_by_curr.get(curr, Decimal("0")) + diff

        title_link = f"<a href=\"{url}\">{title}</a>" if url else f"<b>{title}</b>"
        if old_p > new_p:
            price_part = (
                f"🟢 <code>{fmt_money(new_p, curr)}</code> "
                f"<s>{fmt_money(old_p, curr)}</s> (<code>-{max(1, pct)}%</code>)"
            )
        else:
            price_part = f"💎 <code>{fmt_money(new_p, curr)}</code>"

        rows.append(
            f"<b>{idx}.</b> {mp_label} · {title_link}\n"
            f"   {price_part}"
        )

    total_savings_str = (
        " + ".join(f"<code>{fmt_money(val, c)}</code>" for c, val in savings_by_curr.items())
        if savings_by_curr
        else "<code>0 ₴</code>"
    )
    footer_lbl = {
        "uk": "💰 <b>Сумарна вигода за ніч:</b>",
        "en": "💰 <b>Total overnight savings:</b>",
        "ru": "💰 <b>Суммарная выгода за ночь:</b>",
    }.get(lang, "💰 <b>Сумарна вигода за ніч:</b>")

    return (
        f"{header}\n"
        f"{sub_header}\n"
        f"──────────────────────────────────\n"
        + "\n\n".join(rows)
        + f"\n──────────────────────────────────\n"
        f"{footer_lbl} {total_savings_str}"
    )


async def render_top_drops_card(
    session: AsyncSession,
    db_user: User,
    *,
    force_live: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Вітрина «🔥 Гарячі знижки дня» (Top-5 реальних верифікованих знижок із живим скануванням магазинів).
    """
    lang = normalize_lang(db_user.language)
    drops = await crud.get_top_price_drops(
        session,
        user_id=db_user.id,
        limit=5,
        force_live=force_live,
    )

    header = {
        "uk": "🔥 <b>ГАРЯЧІ ЗНИЖКИ ДНЯ · LIVE ТОП-5</b>",
        "en": "🔥 <b>LIVE HOT DEALS OF THE DAY · TOP 5</b>",
        "ru": "🔥 <b>ГОРЯЧИЕ СКИДКИ ДНЯ · LIVE ТОП-5</b>",
    }.get(lang, "🔥 <b>ГАРЯЧІ ЗНИЖКИ ДНЯ · LIVE ТОП-5</b>")

    sub_hdr = {
        "uk": "📡 <i>Перевірено по живих акціях магазинів та ваших відстеженнях</i>",
        "en": "📡 <i>Verified across live store discounts & your tracked items</i>",
        "ru": "📡 <i>Проверено по живым акциям магазинов и вашим товарам</i>",
    }.get(lang, "")

    if not drops:
        empty = {
            "uk": "Надішліть посилання на товар у чат або натисніть «🔄 Оновити знижки», щоб просканувати акції!",
            "en": "Send a product link in chat or tap '🔄 Refresh Deals' to scan live store discounts!",
            "ru": "Отправьте ссылку на товар в чат или нажмите «🔄 Обновить скидки» для сканирования акций!",
        }.get(lang, "")
        return f"{header}\n{sub_hdr}\n──────────────────────────────────\n{empty}", build_start_keyboard(lang)

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    tracked_tag_map = {
        "uk": " · ⭐️ <b>Ваш товар</b>",
        "en": " · ⭐️ <b>Tracked</b>",
        "ru": " · ⭐️ <b>Ваш товар</b>",
    }

    rows: list[str] = []
    for idx, item in enumerate(drops, start=1):
        p = item["product"]
        old_p = Decimal(str(item["old_price"]))
        new_p = Decimal(str(item["new_price"]))
        drop_abs = Decimal(str(item["drop_abs"]))
        pct = float(item["drop_pct"])
        is_user_tracked = bool(item.get("is_user_tracked", False))

        hist_list = (
            [Decimal(str(h.price)) for h in p.price_history if Decimal(str(h.price)) > Decimal("0")]
            if getattr(p, "price_history", None)
            else [old_p, new_p]
        )
        quant = analyze_price_series(
            current_price=new_p,
            history_prices=hist_list,
            old_price=old_p,
        )
        score = quant.deal_score

        rank_prefix = f"{medals.get(idx, '🔥')} <b>#{idx}</b>"
        mp_short = format_marketplace_name(p.marketplace)
        user_tag = tracked_tag_map.get(lang, "") if is_user_tracked else ""
        short_title = truncate_title(p.title, max_len=34)

        badge = (
            f"<b><code>{fmt_money(new_p, p.currency)}</code></b> "
            f"<s>{fmt_money(old_p, p.currency)}</s> "
            f"(<code>−{pct:.0f}%</code>)"
        )

        rows.append(
            f"{rank_prefix} · {badge} · ⚖️ <code>{score}/100</code>\n"
            f"└ 🏪 <b>{mp_short}</b>{user_tag} · <a href=\"{html.escape(p.url)}\">{short_title}</a>"
        )

    hint = {
        "uk": "👆 <i>Натисніть на будь-яку знижку нижче, щоб відкрити картку, 30д графік та AI-прогноз:</i>",
        "en": "👆 <i>Tap any deal below to open its full card, 30d chart & AI forecast:</i>",
        "ru": "👆 <i>Нажмите на любую скидку ниже, чтобы открыть карточку, 30д график и AI-прогноз:</i>",
    }.get(lang, "")

    text = (
        f"{header}\n"
        f"{sub_hdr}\n"
        f"──────────────────────────────────\n"
        + "\n\n".join(rows)
        + f"\n──────────────────────────────────\n{hint}"
    )
    return text, build_top_drops_keyboard(drops, user_id=db_user.id, lang=lang)


async def render_quick_compare_card(
    session: AsyncSession,
    db_user: User,
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Швидке порівняння цін («⚖️ Порівняння цін») між відстежуваними магазинами користувача.
    Виділяє найвигіднішу пропозицію та ранжує товари за Deal Score і ціною.
    """
    lang = normalize_lang(db_user.language)
    subs = await crud.get_user_subscriptions_with_history(session, db_user.id)

    header = {
        "uk": "⚖️ <b>ПОРІВНЯННЯ ЦІН ТА ВИГОДИ</b>",
        "en": "⚖️ <b>PRICE & VALUE COMPARISON</b>",
        "ru": "⚖️ <b>СРАВНЕНИЕ ЦЕН И ВЫГОДЫ</b>",
    }.get(lang, "⚖️ <b>ПОРІВНЯННЯ ЦІН ТА ВИГОДИ</b>")

    if not subs:
        return t("empty_tracking", lang), build_start_keyboard(lang)

    # Сортуємо за валютою та зростанням ціни, щоб найкраща пропозиція була першою
    sorted_subs = sorted(subs, key=lambda s: (s.product.currency, float(s.product.current_price)))
    best_sub = sorted_subs[0]

    rows: list[str] = []
    for idx, sub in enumerate(sorted_subs[:8], start=1):
        p = sub.product
        prices = [Decimal(str(h.price)) for h in p.price_history] if p.price_history else [Decimal(str(p.current_price))]
        cur = Decimal(str(p.current_price))
        mn, mx = min(prices), max(prices)
        av = sum(prices) / Decimal(len(prices))
        score = compute_deal_score(current_price=cur, min_30d=mn, max_30d=mx, avg_30d=av, old_price=mx)

        mp_short = format_marketplace_name(p.marketplace)
        short_title = truncate_title(p.title, max_len=34)
        crown = " 🏆 <b>НАЙКРАЩА ЦІНА</b>" if (lang == "uk" and sub.id == best_sub.id and len(sorted_subs) > 1) else (
            " 🏆 <b>BEST PRICE</b>" if sub.id == best_sub.id and len(sorted_subs) > 1 else ""
        )
        rows.append(
            f"<b>#{idx} · <code>{fmt_money_with_uah(cur, p.currency)}</code></b> · ⚖️ <code>{score}/100</code>{crown}\n"
            f"└ 🏪 <b>{mp_short}</b> · <a href=\"{html.escape(p.url)}\">{short_title}</a>"
        )

    text = f"{header}\n──────────────────────────────────\n" + "\n\n".join(rows)
    return text, build_tracking_list_keyboard(subs, user_id=db_user.id, lang=lang)


def render_settings_card(db_user: User, items_count: int = 0) -> str:
    """Формує стильну картку налаштувань користувача з індикаторами сповіщень та нічного режиму."""
    lang = normalize_lang(db_user.language)
    max_limit = settings.pro_max_products if db_user.has_active_pro else settings.free_max_products
    tier = "⭐️ PRO" if db_user.has_active_pro else "Free"
    notify_on = getattr(db_user, "notifications_enabled", True)
    dnd_on = getattr(db_user, "dnd_enabled", False)

    if lang == "en":
        return (
            "⚙️ <b>SETTINGS & PREFERENCES</b>\n"
            "──────────────────────────────────\n"
            f"👤 <b>Plan:</b> <code>{tier}</code> (<code>{items_count}/{max_limit}</code> items)\n"
            f"🔔 <b>Instant Alerts:</b> <code>{'ON ✅' if notify_on else 'OFF ❌'}</code>\n"
            f"🌙 <b>Night Mode (23:00–08:00):</b> <code>{'ON ✅' if dnd_on else 'OFF ❌'}</code>\n"
            "──────────────────────────────────\n"
            "💡 <i>Tap any switch below to toggle it on/off. In Night Mode, overnight drops arrive in a morning digest at 08:30.</i>"
        )
    if lang == "ru":
        return (
            "⚙️ <b>НАСТРОЙКИ И УВЕДОМЛЕНИЯ</b>\n"
            "──────────────────────────────────\n"
            f"👤 <b>Тариф:</b> <code>{tier}</code> (<code>{items_count}/{max_limit}</code> товаров)\n"
            f"🔔 <b>Уведомления:</b> <code>{'ВКЛ ✅' if notify_on else 'ВЫКЛ ❌'}</code>\n"
            f"🌙 <b>Ночной режим (23:00–08:00):</b> <code>{'ВКЛ ✅' if dnd_on else 'ВЫКЛ ❌'}</code>\n"
            "──────────────────────────────────\n"
            "💡 <i>Нажмите на переключатель ниже для изменения. В Ночном режиме все ночные скидки приходят в дайджесте на 08:30.</i>"
        )
    return (
        "⚙️ <b>НАЛАШТУВАННЯ ТА СПОВІЩЕННЯ</b>\n"
        "──────────────────────────────────\n"
        f"👤 <b>Тариф:</b> <code>{tier}</code> (<code>{items_count}/{max_limit}</code> товарів)\n"
        f"🔔 <b>Миттєві сповіщення:</b> <code>{'УВІМК ✅' if notify_on else 'ВИМК ❌'}</code>\n"
        f"🌙 <b>Нічний режим (23:00–08:00):</b> <code>{'УВІМК ✅' if dnd_on else 'ВИМК ❌'}</code>\n"
        "──────────────────────────────────\n"
        "💡 <i>Натискайте на перемикачі нижче. У Нічному режимі бот не турбує вас уночі, а надсилає всі знижки в ранковому дайджесті о 08:30.</i>"
    )


async def render_my_tracking_text_and_kb(
    session: AsyncSession,
    db_user: User,
    page: int = 1,
    category_tag: str | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Формує зрозумілий список відстежуваних товарів («📦 Мої товари») зі статусами знижок,
    фільтрацією за розумними добірками (📁 Вішлісти), авто-перерахунком валют за курсом НБУ
    та розрахунком сумарної вартості списку сьогодні vs на мінімумі.
    """
    lang = normalize_lang(db_user.language)
    all_subscriptions = await crud.get_user_subscriptions_with_history(session, db_user.id)
    max_limit = settings.pro_max_products if db_user.has_active_pro else settings.free_max_products

    if not all_subscriptions:
        return t("empty_tracking", lang), build_start_keyboard(lang=lang, items_count=0)

    active_tag = category_tag if category_tag and category_tag != "all" else None
    if active_tag:
        subscriptions = [s for s in all_subscriptions if getattr(s, "category_tag", None) == active_tag]
        if not subscriptions:
            subscriptions = all_subscriptions
            active_tag = None
    else:
        subscriptions = all_subscriptions

    total_items = len(subscriptions)
    total_pages = max(1, math.ceil(total_items / ITEMS_PER_PAGE))
    cur_page = max(1, min(page, total_pages))
    start_idx = (cur_page - 1) * ITEMS_PER_PAGE
    page_subs = subscriptions[start_idx : start_idx + ITEMS_PER_PAGE]

    discounted_count = 0
    targets_count = 0
    sniper_count = 0
    list_total_uah = Decimal("0")
    list_min_uah = Decimal("0")

    for s in subscriptions:
        if s.target_price is not None:
            targets_count += 1
        p = s.product
        cur_p = Decimal(str(p.current_price))
        if not getattr(p, "is_available", True) or cur_p <= Decimal("0"):
            sniper_count += 1
            continue
        hist = [Decimal(str(h.price)) for h in p.price_history if Decimal(str(h.price)) > 0] if p.price_history else [cur_p]
        mx_p = max(hist) if hist else cur_p
        mn_p = min(hist) if hist else cur_p
        if cur_p < mx_p:
            discounted_count += 1
        list_total_uah += to_uah(cur_p, p.currency)
        list_min_uah += to_uah(mn_p, p.currency)

    rows: list[str] = []
    for idx, sub in enumerate(page_subs, start=start_idx + 1):
        p = sub.product
        mp_short = format_marketplace_name(p.marketplace)
        title_escaped = truncate_title(p.title, max_len=34)
        cur = Decimal(str(p.current_price))
        is_avail = getattr(p, "is_available", True) and cur > Decimal("0")
        cat_badge = format_category_badge(getattr(sub, "category_tag", None), lang)
        cat_part = f" · {cat_badge}" if cat_badge and not active_tag else ""

        if not is_avail:
            sniper_str = {
                "uk": "🟢 <b>Снайпер наявності</b> (чекаємо поставку)",
                "en": "🟢 <b>Stock Sniper</b> (waiting for restock)",
                "ru": "🟢 <b>Снайпер наличия</b> (ждём поставку)",
            }.get(lang, "")
            rows.append(
                f"<b>#{idx} ·</b> {sniper_str}\n"
                f"└ 🏪 <b>{mp_short}</b>{cat_part} · <a href=\"{html.escape(p.url)}\">{title_escaped}</a>"
            )
            continue

        prices = [Decimal(str(h.price)) for h in p.price_history if Decimal(str(h.price)) > 0] if p.price_history else [cur]
        mx = max(prices) if prices else cur

        if mx > cur:
            pct = max(1, int(round((float(mx - cur) / max(0.01, float(mx))) * 100)))
            price_str = f"🟢 <b><code>{fmt_money_with_uah(cur, p.currency)}</code></b> <s>{fmt_money(mx, p.currency)}</s> (<code>-{pct}%</code>)"
        else:
            price_str = f"💰 <b><code>{fmt_money_with_uah(cur, p.currency)}</code></b>"

        target_badge = ""
        if sub.target_price is not None:
            tgt_dec = Decimal(str(sub.target_price))
            reached = " ✅" if cur <= tgt_dec else ""
            target_badge = f" · 🎯 <code>{fmt_money(tgt_dec, p.currency)}</code>{reached}"

        rows.append(
            f"<b>#{idx} ·</b> {price_str}{target_badge}\n"
            f"└ 🏪 <b>{mp_short}</b>{cat_part} · <a href=\"{html.escape(p.url)}\">{title_escaped}</a>"
        )

    sniper_part = f" · 🟢 Снайпер: <code>{sniper_count}</code>" if sniper_count > 0 else ""
    summary_line = {
        "uk": f"🟢 Зі знижкою: <code>{discounted_count}</code> · 🎯 Цілей: <code>{targets_count}</code>{sniper_part}\n",
        "en": f"🟢 On sale: <code>{discounted_count}</code> · 🎯 Targets: <code>{targets_count}</code>{sniper_part}\n",
        "ru": f"🟢 Со скидкой: <code>{discounted_count}</code> · 🎯 Целей: <code>{targets_count}</code>{sniper_part}\n",
    }.get(lang, "")

    wishlist_calc_line = ""
    if len(subscriptions) > 1 and list_total_uah > Decimal("0"):
        pot_save = max(Decimal("0"), list_total_uah - list_min_uah)
        cat_title = format_category_badge(active_tag, lang) if active_tag else (
            {"uk": "Весь список", "en": "Full list", "ru": "Весь список"}.get(lang, "Весь список")
        )
        wishlist_calc_line = {
            "uk": (
                f"📁 <b>{cat_title} сьогодні:</b> <code>~{fmt_money(list_total_uah, 'UAH')}</code> "
                f"(на мінімумах 30д: <code>~{fmt_money(list_min_uah, 'UAH')}</code>, вигода до <code>{fmt_money(pot_save, 'UAH')}</code>)\n"
            ),
            "en": (
                f"📁 <b>{cat_title} today:</b> <code>~{fmt_money(list_total_uah, 'UAH')}</code> "
                f"(at 30d lows: <code>~{fmt_money(list_min_uah, 'UAH')}</code>, save up to <code>{fmt_money(pot_save, 'UAH')}</code>)\n"
            ),
            "ru": (
                f"📁 <b>{cat_title} сегодня:</b> <code>~{fmt_money(list_total_uah, 'UAH')}</code> "
                f"(на минимумах 30д: <code>~{fmt_money(list_min_uah, 'UAH')}</code>, выгода до <code>{fmt_money(pot_save, 'UAH')}</code>)\n"
            ),
        }.get(lang, "")

    rows_block = summary_line + wishlist_calc_line + "\n" + "\n\n".join(rows)

    text = t(
        "my_products_header",
        lang,
        used=len(all_subscriptions),
        max_limit=max_limit,
        rows=rows_block,
    )
    return text, build_tracking_list_keyboard(
        subscriptions,
        user_id=db_user.id,
        lang=lang,
        page=cur_page,
        active_tag=active_tag,
        all_subscriptions=all_subscriptions,
    )


def compute_optimal_target_price(
    current_price: Decimal,
    history_prices: Sequence[Decimal | float] | None = None,
) -> Decimal:
    """
    Розраховує розумну рекомендовану ціль (AI Optimal Target Price) на основі зони підтримки Q15,
    відсікання викидів (Winsorization) та фактичної реалізованої волатильності σ.
    """
    return analyze_price_series(
        current_price=current_price,
        history_prices=history_prices,
    ).optimal_target


async def render_ai_price_forecast_card(
    session: AsyncSession,
    db_user: User,
    product_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    """
    AI-Прогноз ціни та Радник покупця («Купувати зараз чи чекати?»):
    Використовує робастну економетричну модель (Median/EWMA, Winsorized IQR, MAD-волатильність,
    Ornstein-Uhlenbeck Z-score + Байєсівське згладжування Beta-апріорі з оцінкою похибки ±SE%).
    """
    lang = normalize_lang(db_user.language)
    product = await crud.get_product_with_history(session, product_id)
    if product is None:
        return t("product_not_found_msg", lang), build_start_keyboard(lang)

    cur = Decimal(str(product.current_price))
    curr = product.currency or "UAH"
    prices = [Decimal(str(h.price)) for h in product.price_history] if product.price_history else [cur]
    mx = max(prices)

    qm = analyze_price_series(
        current_price=cur,
        history_prices=prices,
        old_price=mx,
    )

    optimal_target = qm.optimal_target
    potential_save = max(Decimal("0"), cur - optimal_target)
    vol_str = f"{qm.volatility_pct:.1f}".rstrip("0").rstrip(".")
    drop_prob = qm.drop_probability_7d
    err_pct = qm.model_error_pct
    score = qm.deal_score
    z_str = f"{qm.z_score:+.2f}σ"

    # Позиція в робастному каналі (10 зелених = біля мінімуму, 1 зелений = на піку)
    pos_steps = max(1, min(10, int(round((1.0 - qm.channel_position) * 10))))
    channel_bar = "🟩" * pos_steps + "⬜" * (10 - pos_steps)

    short_title = truncate_title(product.title, max_len=46)
    mp_label = format_marketplace_label(product.marketplace)

    if qm.trend_slope_pct <= -1.0:
        trend_badge = {
            "uk": f"📉 Спадний (<code>{qm.trend_slope_pct:+.1f}%/тиж</code>)",
            "en": f"📉 Downward (<code>{qm.trend_slope_pct:+.1f}%/wk</code>)",
            "ru": f"📉 Нисходящий (<code>{qm.trend_slope_pct:+.1f}%/нед</code>)",
        }.get(lang, "")
    elif qm.trend_slope_pct >= 1.0:
        trend_badge = {
            "uk": f"📈 Висхідний (<code>{qm.trend_slope_pct:+.1f}%/тиж</code>)",
            "en": f"📈 Upward (<code>{qm.trend_slope_pct:+.1f}%/wk</code>)",
            "ru": f"📈 Восходящий (<code>{qm.trend_slope_pct:+.1f}%/нед</code>)",
        }.get(lang, "")
    else:
        trend_badge = {
            "uk": "⚖️ Стабільний коридор",
            "en": "⚖️ Sideways channel",
            "ru": "⚖️ Стабильный коридор",
        }.get(lang, "")

    if lang == "en":
        if score >= 86:
            verdict = "🟢 <b>BUY NOW</b> — Price is in the lower support zone (Z-score below equilibrium)!"
        elif score >= 70:
            verdict = "🟡 <b>FAIR MARKET PRICE</b> — Near equilibrium. Good to buy or set the AI target."
        else:
            verdict = "🔴 <b>WAIT FOR REVERSION</b> — Price is overheated above equilibrium. High chance of a pullback!"

        text = (
            "🧠 <b>AI QUANT PRICE FORECAST (7 DAYS)</b>\n"
            "──────────────────────────────────\n"
            f"📦 <b>{short_title}</b>\n"
            f"🏪 {mp_label} · 💰 <b>Now:</b> <code>{fmt_money(cur, curr)}</code> (<code>{z_str}</code>)\n"
            "──────────────────────────────────\n"
            f"📊 <b>Value Index:</b> {channel_bar} <code>{score}/100</code>\n"
            f"⚖️ <b>Equilibrium (EWMA/Med):</b> <code>{fmt_money(qm.equilibrium_price, curr)}</code>\n"
            f"🛡 <b>Support / Resistance:</b> <code>{fmt_money(qm.q15_support, curr)}</code> — <code>{fmt_money(qm.q85_resistance, curr)}</code>\n"
            f"📉 <b>Realized Volatility (σ):</b> <code>±{vol_str}%</code> · {trend_badge}\n"
            f"🔮 <b>7-Day Drop Probability:</b> <code>{drop_prob}%</code> (margin <code>±{err_pct}%</code>)\n"
            f"🎯 <b>Optimal Buy Zone:</b> <code>{fmt_money(optimal_target, curr)}</code> "
            f"(save <code>{fmt_money(potential_save, curr)}</code>)\n"
            "──────────────────────────────────\n"
            f"💡 <b>AI Quant Verdict:</b>\n{verdict}"
        )
    elif lang == "ru":
        if score >= 86:
            verdict = "🟢 <b>ПОКУПАТЬ СЕЙЧАС</b> — Цена в нижней зоне поддержки (ниже равновесной)!"
        elif score >= 70:
            verdict = "🟡 <b>СПРАВЕДЛИВАЯ ЦЕНА</b> — Около равновесия. Можно брать или задать AI-цель."
        else:
            verdict = "🔴 <b>ЛУЧШЕ ПОДОЖДАТЬ</b> — Цена перегрета выше равновесной, вероятен откат к медиане!"

        text = (
            "🧠 <b>AI-ПРОГНОЗ ЦЕНЫ И КВАНТ-АНАЛИЗ (7 ДН.)</b>\n"
            "──────────────────────────────────\n"
            f"📦 <b>{short_title}</b>\n"
            f"🏪 {mp_label} · 💰 <b>Сейчас:</b> <code>{fmt_money(cur, curr)}</code> (<code>{z_str}</code>)\n"
            "──────────────────────────────────\n"
            f"📊 <b>Индекс выгоды:</b> {channel_bar} <code>{score}/100</code>\n"
            f"⚖️ <b>Равновесная (EWMA/Мед):</b> <code>{fmt_money(qm.equilibrium_price, curr)}</code>\n"
            f"🛡 <b>Коридор (Q15–Q85):</b> <code>{fmt_money(qm.q15_support, curr)}</code> — <code>{fmt_money(qm.q85_resistance, curr)}</code>\n"
            f"📉 <b>Волатильность (σ):</b> <code>±{vol_str}%</code> · {trend_badge}\n"
            f"🔮 <b>Вероятность скидки (7 дн.):</b> <code>{drop_prob}%</code> (погрешность <code>±{err_pct}%</code>)\n"
            f"🎯 <b>Оптимальная цена входа:</b> <code>{fmt_money(optimal_target, curr)}</code> "
            f"(выгода <code>{fmt_money(potential_save, curr)}</code>)\n"
            "──────────────────────────────────\n"
            f"💡 <b>Вердикт AI-Советника:</b>\n{verdict}"
        )
    else:
        if score >= 86:
            verdict = "🟢 <b>КУПУВАТИ ЗАРАЗ</b> — Ціна у нижній зоні підтримки (нижче рівноважної медіани)!"
        elif score >= 70:
            verdict = "🟡 <b>СПРАВЕДЛИВА ЦІНА</b> — Біля ринкової рівноваги. Вигідно брати або задати AI-ціль."
        else:
            verdict = "🔴 <b>КРАЩЕ ЗАЧЕКАТИ ЗНИЖКИ</b> — Ціна перегріта вище рівноваги, очікується повернення до медіани!"

        text = (
            "🧠 <b>AI-ПРОГНОЗ ЦІНИ ТА КВАНТ-АНАЛІЗ (7 ДН.)</b>\n"
            "──────────────────────────────────\n"
            f"📦 <b>{short_title}</b>\n"
            f"🏪 {mp_label} · 💰 <b>Зараз:</b> <code>{fmt_money(cur, curr)}</code> (відхилення <code>{z_str}</code>)\n"
            "──────────────────────────────────\n"
            f"📊 <b>Індекс вигоди:</b> {channel_bar} <code>{score}/100</code>\n"
            f"⚖️ <b>Рівноважна (EWMA/Мед):</b> <code>{fmt_money(qm.equilibrium_price, curr)}</code>\n"
            f"🛡 <b>Коридор норми (Q15–Q85):</b> <code>{fmt_money(qm.q15_support, curr)}</code> — <code>{fmt_money(qm.q85_resistance, curr)}</code>\n"
            f"📉 <b>Волатильність (σ):</b> <code>±{vol_str}%</code> · {trend_badge}\n"
            f"🔮 <b>Ймовірність знижки (7 дн.):</b> <code>{drop_prob}%</code> (похибка <code>±{err_pct}%</code>)\n"
            f"🎯 <b>Оптимальна ціна покупки:</b> <code>{fmt_money(optimal_target, curr)}</code> "
            f"(економія <code>{fmt_money(potential_save, curr)}</code>)\n"
            "──────────────────────────────────\n"
            f"💡 <b>Вердикт AI-Радника:</b>\n{verdict}"
        )

    kb = build_ai_forecast_keyboard(
        product_id=product.id,
        optimal_price=optimal_target,
        currency=curr,
        lang=lang,
    )
    return text, kb


async def render_pro_offer_card(
    session: AsyncSession,
    db_user: User,
) -> str:
    """
    Преміальна вітрина тарифу ⭐️ PRO із персональним калькулятором окупності (ROI),
    порівнянням Free vs PRO та 3 гнучкими планами (30 / 90 / 365 днів).
    """
    lang = normalize_lang(db_user.language)
    subs = await crud.get_user_subscriptions_with_history(session, db_user.id)
    used_count = len(subs)
    free_max = settings.free_max_products
    pro_max = settings.pro_max_products
    stars_30 = settings.pro_price_stars
    stars_90 = max(1, int(round(stars_30 * 2.4)))
    stars_365 = max(1, int(round(stars_30 * 7.2)))

    # Підраховуємо вже знайдену або потенційну економію користувача
    savings_uah = Decimal("0")
    for s in subs:
        p = s.product
        prices = [Decimal(str(h.price)) for h in p.price_history] if p.price_history else [Decimal(str(p.current_price))]
        cur = Decimal(str(p.current_price))
        mx = max(prices)
        diff = max(cur * Decimal("0.12"), mx - cur)
        if p.currency == "UAH":
            savings_uah += diff

    if savings_uah <= Decimal("0"):
        savings_uah = Decimal("1850")
    savings_str = fmt_money(savings_uah.quantize(Decimal("1")), "UAH")

    if db_user.has_active_pro and db_user.pro_until is not None:
        until_str = db_user.pro_until.strftime("%d.%m.%Y")
        status_line = {
            "uk": f"✅ <b>Ваш статус: ⭐️ PRO активний до <code>{until_str}</code></b>",
            "en": f"✅ <b>Your status: ⭐️ PRO active until <code>{until_str}</code></b>",
            "ru": f"✅ <b>Ваш статус: ⭐️ PRO активен до <code>{until_str}</code></b>",
        }.get(lang, "")
    else:
        status_line = {
            "uk": f"🔓 <b>Ваш тариф: Free (<code>{used_count}/{free_max}</code> товарів)</b>",
            "en": f"🔓 <b>Your plan: Free (<code>{used_count}/{free_max}</code> items)</b>",
            "ru": f"🔓 <b>Ваш тариф: Free (<code>{used_count}/{free_max}</code> товаров)</b>",
        }.get(lang, "")

    if lang == "en":
        text = (
            "👑 <b>PRICE TRACKER ⭐️ PRO · MAXIMUM SAVINGS</b>\n"
            "──────────────────────────────────\n"
            f"{status_line}\n"
            f"💰 <b>Estimated savings with PRO:</b> <code>~{savings_str}/mo</code>\n"
            "<i>One caught discount pays for a full year of PRO 10× over!</i>\n"
            "──────────────────────────────────\n"
            "<b>Why power shoppers choose ⭐️ PRO:</b>\n"
            f"📦 <b>Up to {pro_max} products</b> (vs {free_max} in Free — <b>16× more!</b>)\n"
            "⚡️ <b>2× Faster Checks (every 12h)</b> — catch flash sales before items sell out\n"
            "🧠 <b>AI Price Advisor & 1-Tap Optimal Target</b> — know whether to buy now or wait\n"
            "📥 <b>Bulk Link Import</b> — send up to 10 links in a single message\n"
            "📊 <b>Full 30-Day Portfolio Analytics & Excel/CSV Reports</b>\n"
            "──────────────────────────────────\n"
            "💎 <b>Choose your plan (less than 1 ⭐️ / day):</b>\n"
            f"• <b>30 Days:</b> <code>{stars_30} ⭐️</code>\n"
            f"• <b>90 Days (-20% BEST):</b> <code>{stars_90} ⭐️</code>\n"
            f"• <b>365 Days (-40% VIP Year):</b> <code>{stars_365} ⭐️</code>\n"
            "──────────────────────────────────\n"
            "🎁 <i>Want PRO for free? Invite a friend and get <b>+7 days of PRO</b>!</i>"
        )
    elif lang == "ru":
        text = (
            "👑 <b>ТАРИФ ⭐️ PRO · МАКСИМАЛЬНАЯ ВЫГОДА</b>\n"
            "──────────────────────────────────\n"
            f"{status_line}\n"
            f"💰 <b>Потенциал вашей экономии:</b> <code>~{savings_str}/мес</code>\n"
            "<i>Даже 1 пойманная скидка окупает год подписки PRO в 10 раз!</i>\n"
            "──────────────────────────────────\n"
            "<b>Что открывает подписка ⭐️ PRO:</b>\n"
            f"📦 <b>До {pro_max} товаров</b> (вместо {free_max} в Free — <b>в 16× больше!</b>)\n"
            "⚡️ <b>Проверка каждые 12 ч (2× быстрее)</b> — перехватывайте закрытые акции первыми\n"
            "🧠 <b>AI-Прогноз цены и Авто-цель в 1 клик</b> — точный совет «покупать или ждать»\n"
            "📥 <b>Пакетное добавление</b> — до 10 ссылок в одном сообщении\n"
            "📊 <b>VIP-Аналитика портфеля за 30 дней и экспорт в Excel/CSV</b>\n"
            "──────────────────────────────────\n"
            "💎 <b>Выберите выгодный план (меньше 1 ⭐️ в день):</b>\n"
            f"• <b>30 дней:</b> <code>{stars_30} ⭐️</code>\n"
            f"• <b>90 дней (-20% ХИТ):</b> <code>{stars_90} ⭐️</code>\n"
            f"• <b>365 дней (-40% VIP Год):</b> <code>{stars_365} ⭐️</code>\n"
            "──────────────────────────────────\n"
            "🎁 <i>Хотите PRO бесплатно? Пригласите друга и получите <b>+7 дней PRO</b>!</i>"
        )
    else:
        text = (
            "👑 <b>ТАРИФ ⭐️ PRO · МАКСИМАЛЬНА ВИГОДА</b>\n"
            "──────────────────────────────────\n"
            f"{status_line}\n"
            f"💰 <b>Ваш потенціал економії:</b> <code>~{savings_str}/міс</code>\n"
            "<i>Навіть 1 спіймана знижка окупає річний PRO у 10 разів!</i>\n"
            "──────────────────────────────────\n"
            "<b>Чому варто перейти на ⭐️ PRO:</b>\n"
            f"📦 <b>До {pro_max} товарів</b> (замість {free_max} у Free — <b>у 16× більше!</b>)\n"
            "⚡️ <b>Перевірка кожні 12 год (2× швидше)</b> — встигайте купити за акційною ціною до розпродажу складу\n"
            "🧠 <b>AI-Прогноз ціни та Авто-ціль в 1 клік</b> — точний вердикт «купувати зараз чи чекати»\n"
            "📥 <b>Пакетне додавання</b> — надсилайте до 10 посилань одним повідомленням\n"
            "📊 <b>Повна 30-денна аналітика кошика та експорт у Excel/CSV</b>\n"
            "──────────────────────────────────\n"
            "💎 <b>Оберіть свій план (менше 1 ⭐️ на день):</b>\n"
            f"• <b>30 днів:</b> <code>{stars_30} ⭐️</code>\n"
            f"• <b>90 днів (-20% ХІТ):</b> <code>{stars_90} ⭐️</code>\n"
            f"• <b>365 днів (-40% VIP Рік):</b> <code>{stars_365} ⭐️</code>\n"
            "──────────────────────────────────\n"
            "🎁 <i>Хочете отримати PRO безкоштовно? Запросіть друга та отримайте <b>+7 днів PRO</b>!</i>"
        )

    return text


async def render_user_stats_text(session: AsyncSession, db_user: User) -> str:
    """
    Формує зведену 30-денну портфельну аналітику:
    - Сумарна вартість кошика зараз vs 30-денний пік (включно з єдиним перерахунком у ₴ за курсом НБУ)
    - Загальна економія по кожній валюті та по вішлістах (📁 Добірках)
    - Середній індекс вигоди (Deal Score) та кількість досягнутих цілей
    """
    lang = normalize_lang(db_user.language)
    subscriptions = await crud.get_user_subscriptions_with_history(session, db_user.id)
    if not subscriptions:
        return t("empty_analytics", lang)

    savings_by_currency: dict[str, Decimal] = {}
    cart_by_currency: dict[str, Decimal] = {}
    total_cart_uah = Decimal("0")
    total_savings_uah = Decimal("0")
    category_totals_uah: dict[str, Decimal] = {}
    has_foreign_currency = False
    best_deals_lines: list[str] = []
    scores: list[int] = []
    reached_targets = 0

    for sub in subscriptions:
        prod = sub.product
        cur = Decimal(str(prod.current_price))
        if cur <= Decimal("0") or not getattr(prod, "is_available", True):
            continue

        history = prod.price_history if prod else []
        prices = [Decimal(str(h.price)) for h in history if Decimal(str(h.price)) > 0] if history else [cur]
        if not prices:
            prices = [cur]

        mn = min(prices)
        mx = max(prices)
        av = sum(prices) / Decimal(len(prices))
        curr = prod.currency or "UAH"
        if curr.upper() != "UAH":
            has_foreign_currency = True

        cart_by_currency[curr] = cart_by_currency.get(curr, Decimal("0")) + cur
        cur_uah = to_uah(cur, curr)
        total_cart_uah += cur_uah

        cat_tag = getattr(sub, "category_tag", None)
        if cat_tag:
            category_totals_uah[cat_tag] = category_totals_uah.get(cat_tag, Decimal("0")) + cur_uah

        saved = max(Decimal("0"), mx - cur)
        if saved > 0:
            savings_by_currency[curr] = savings_by_currency.get(curr, Decimal("0")) + saved
            total_savings_uah += to_uah(saved, curr)

        score = compute_deal_score(current_price=cur, min_30d=mn, max_30d=mx, avg_30d=av, old_price=mx)
        scores.append(score)

        if sub.target_price is not None and cur <= Decimal(str(sub.target_price)):
            reached_targets += 1

        mp_label = format_marketplace_label(prod.marketplace)
        short_title = truncate_title(prod.title, max_len=44)
        if cur <= mn and mx > mn:
            save_badge = f"🔥 МІН 30Д (<code>-{fmt_money(saved, curr)}</code>)" if lang != "en" else f"🔥 30D LOW (<code>-{fmt_money(saved, curr)}</code>)"
        elif saved > 0:
            save_badge = f"🟢 <code>-{fmt_money(saved, curr)}</code>"
        else:
            save_badge = f"⚖️ <code>{score}/100</code>"

        best_deals_lines.append(
            t(
                "analytics_item_row",
                lang,
                mp_label=mp_label,
                title=short_title,
                cur=fmt_money_with_uah(cur, curr),
                mn=fmt_money(mn, curr),
                mx=fmt_money(mx, curr),
                save_badge=save_badge,
            )
        )

    if savings_by_currency:
        savings_str = " + ".join(fmt_money(val, curr) for curr, val in savings_by_currency.items())
        if has_foreign_currency:
            savings_str += f" (~{fmt_money(total_savings_uah, 'UAH')} НБУ)"
    else:
        savings_str = "0 ₴"

    cart_str = " + ".join(fmt_money(val, curr) for curr, val in cart_by_currency.items()) or "0 ₴"
    if has_foreign_currency and total_cart_uah > 0:
        cart_str += f" (<code>~{fmt_money(total_cart_uah, 'UAH')}</code> за курсом НБУ)"

    avg_score = int(round(sum(scores) / max(1, len(scores)))) if scores else 75

    cat_summary_line = ""
    if category_totals_uah:
        parts = [
            f"{format_category_badge(tag, lang)}: <code>~{fmt_money(val, 'UAH')}</code>"
            for tag, val in category_totals_uah.items()
        ]
        cat_summary_line = "📁 " + " · ".join(parts) + "\n"

    portfolio_meta = {
        "uk": (
            f"🛒 <b>Вартість кошика зараз:</b> <code>{cart_str}</code>\n"
            f"{cat_summary_line}"
            f"⚖️ <b>Середній Deal Score:</b> <code>{avg_score}/100</code> · "
            f"🎯 <b>Цілей досягнуто:</b> <code>{reached_targets}</code>\n"
        ),
        "en": (
            f"🛒 <b>Current cart value:</b> <code>{cart_str}</code>\n"
            f"{cat_summary_line}"
            f"⚖️ <b>Average Deal Score:</b> <code>{avg_score}/100</code> · "
            f"🎯 <b>Targets reached:</b> <code>{reached_targets}</code>\n"
        ),
        "ru": (
            f"🛒 <b>Стоимость корзины сейчас:</b> <code>{cart_str}</code>\n"
            f"{cat_summary_line}"
            f"⚖️ <b>Средний Deal Score:</b> <code>{avg_score}/100</code> · "
            f"🎯 <b>Целей достигнуто:</b> <code>{reached_targets}</code>\n"
        ),
    }.get(lang, "")

    base_text = t(
        "analytics_header",
        lang,
        count=len(subscriptions),
        savings=savings_str,
        items=portfolio_meta + "\n" + "\n".join(best_deals_lines),
    )
    return base_text


async def generate_user_csv_report(
    session: AsyncSession,
    db_user: User,
) -> BufferedInputFile | None:
    """
    Генерує UTF-8 BOM CSV-файл для Excel / Google Sheets з усіма товарами користувача та 30-денною статистикою.
    Виконує лише 1 SQL-запит із selectinload замість N+1 та повертає готовий BufferedInputFile.
    """
    subscriptions = await crud.get_user_subscriptions_with_history(session, db_user.id)
    if not subscriptions:
        return None

    output = io.StringIO()
    output.write("\ufeff")  # UTF-8 BOM для коректного відкриття кирилиці в Microsoft Excel
    writer = csv.writer(output, delimiter=";")
    writer.writerow(
        [
            "ID",
            "Marketplace",
            "Product Title",
            "Current Price",
            "Currency",
            "Target Price",
            "30d Min Price",
            "30d Max Price",
            "30d Avg Price",
            "Savings vs 30d High",
            "Last Checked (UTC)",
            "Product URL",
        ]
    )

    for idx, sub in enumerate(subscriptions, start=1):
        p = sub.product
        history = p.price_history if p else []
        prices = [float(h.price) for h in history] if history else [float(p.current_price)]
        cur = float(p.current_price)
        mn = min(prices)
        mx = max(prices)
        avg = sum(prices) / len(prices)
        savings = max(0.0, mx - cur)

        writer.writerow(
            [
                idx,
                p.marketplace.upper(),
                p.title,
                f"{cur:.2f}",
                p.currency,
                f"{float(sub.target_price):.2f}" if sub.target_price is not None else "",
                f"{mn:.2f}",
                f"{mx:.2f}",
                f"{avg:.2f}",
                f"{savings:.2f}",
                p.last_checked_at.strftime("%Y-%m-%d %H:%M") if p.last_checked_at else "",
                p.url,
            ]
        )

    csv_bytes = output.getvalue().encode("utf-8")
    return BufferedInputFile(
        csv_bytes,
        filename=f"price_tracker_report_{db_user.id}.csv",
    )
