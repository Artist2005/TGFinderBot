from __future__ import annotations

import csv
from decimal import Decimal
import html
import io
import math
from typing import Any, Sequence

from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, LinkPreviewOptions
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


_ZERO_WIDTH_IMAGE_MARKER = "\u200d"  # &#8205;


def embed_invisible_image_link(text: str, image_url: str | None) -> str:
    """
    Вбудовує невидиме HTML-посилання на фото товару (CDN маркетплейсу) на початку тексту.
    Telegram завантажує та рендерить картинку напряму зі сторони своїх серверів —
    бот НЕ завантажує і НЕ зберігає жодного байта зображень на сервері (0 МБ диска/RAM).
    """
    clean_img = (image_url or "").strip()
    if not clean_img.startswith(("https://", "http://")):
        return text
    safe_url = html.escape(clean_img, quote=True)
    return f'<a href="{safe_url}">{_ZERO_WIDTH_IMAGE_MARKER}</a>{text}'


def extract_embedded_image_url(text: str) -> str | None:
    """Витягує URL картинки з невидимого тегу на початку повідомлення, якщо він є."""
    if not text.startswith('<a href="') or f">{_ZERO_WIDTH_IMAGE_MARKER}</a>" not in text[:450]:
        return None
    end_quote = text.find('">', 9)
    if end_quote == -1:
        return None
    return html.unescape(text[9:end_quote])


def build_image_preview_options(
    image_url: str | None = None,
    *,
    fallback_url: str | None = None,
    text: str | None = None,
) -> LinkPreviewOptions:
    """
    Формує параметри прев'ю для Telegram Bot API:
    якщо є пряме посилання на фото товару (або воно вбудоване в text через embed_invisible_image_link),
    вмикає велике фото зверху картки без збереження файлів на сервері.
    Інакше вимикає прев'ю, щоб у списках не з'являлися випадкові прев'ю сайтів.
    """
    target_url = (image_url or "").strip()
    if not target_url and text:
        target_url = extract_embedded_image_url(text) or ""
    if not target_url and fallback_url:
        target_url = fallback_url.strip()

    if target_url.startswith(("https://", "http://")):
        return LinkPreviewOptions(
            is_disabled=False,
            url=target_url,
            prefer_large_media=True,
            show_above_text=True,
        )
    return LinkPreviewOptions(is_disabled=True)


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
    Компактне, чисте, без горизонтальних ліній та зайвих емодзі.
    """
    lang = normalize_lang(db_user.language)
    max_limit = settings.pro_max_products if db_user.has_active_pro else settings.free_max_products
    tier = (
        "PRO (∞)"
        if (db_user.has_active_pro and db_user.pro_until is None)
        else ("PRO" if db_user.has_active_pro else "Free")
    )

    if items_count == 0:
        if lang == "en":
            return (
                "<b>Price Tracker</b>\n"
                "Automatic price monitoring across online stores.\n\n"
                "<b>How to add an item:</b>\n"
                "• Write a product name in chat (e.g. <code>AirPods Pro 2</code>)\n"
                "• Or paste a direct link from a store"
            )
        if lang == "ru":
            return (
                "<b>Price Tracker</b>\n"
                "Автоматический мониторинг цен и скидок.\n\n"
                "<b>Как добавить товар:</b>\n"
                "• Напишите название в чат (например, <code>AirPods Pro 2</code>)\n"
                "• Или отправьте ссылку из магазина"
            )
        return (
            "<b>Price Tracker</b>\n"
            "Автоматичний моніторинг цін та знижок.\n\n"
            "<b>Як додати товар:</b>\n"
            "• Напишіть назву в чат (наприклад, <code>AirPods Pro 2</code>)\n"
            "• Або надішліть посилання з магазину"
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
            "<b>Price Tracker</b>\n\n"
            f"Plan: <code>{tier}</code>\n"
            f"Tracked items: <code>{items_count}/{max_limit}</code>\n"
            f"Check interval: <code>every {interval}</code>\n\n"
            "Send a product name or store link to add an item."
        )
    if lang == "ru":
        return (
            "<b>Price Tracker</b>\n\n"
            f"Тариф: <code>{tier}</code>\n"
            f"Отслеживается: <code>{items_count}/{max_limit}</code>\n"
            f"Проверка цен: <code>каждые {interval}</code>\n\n"
            "Напишите название товара или отправьте ссылку в чат."
        )
    return (
        "<b>Price Tracker</b>\n\n"
        f"Тариф: <code>{tier}</code>\n"
        f"Відстежується: <code>{items_count}/{max_limit}</code>\n"
        f"Перевірка цін: <code>кожні {interval}</code>\n\n"
        "Напишіть назву товару або надішліть посилання в чат."
    )


def render_add_product_guide_card(lang: str = "uk") -> str:
    """Коротка та чиста інструкція додавання товару."""
    lang = normalize_lang(lang)
    if lang == "en":
        return (
            "<b>Add a Product</b>\n\n"
            "<b>1. By name</b>\n"
            "Write a model in chat, e.g. <code>AirPods Pro 2</code> or <code>PlayStation 5</code>.\n\n"
            "<b>2. By link</b>\n"
            "Send 1 to 10 links from Rozetka, Prom, Comfy, Allo, Epicentr, OLX, Amazon, etc.\n\n"
            "<b>3. Out-of-stock tracking</b>\n"
            "Send a link to a sold-out item to get an alert when it returns."
        )
    if lang == "ru":
        return (
            "<b>Добавление товара</b>\n\n"
            "<b>1. По названию</b>\n"
            "Напишите модель в чат, например: <code>AirPods Pro 2</code> или <code>PlayStation 5</code>.\n\n"
            "<b>2. По ссылке</b>\n"
            "Отправьте от 1 до 10 ссылок из Rozetka, Prom, Comfy, Алло, Эпицентр, OLX, Amazon и др.\n\n"
            "<b>3. Контроль наличия</b>\n"
            "Отправьте ссылку на раскупленный товар — бот сообщит, когда он появится."
        )
    return (
        "<b>Додавання товару</b>\n\n"
        "<b>1. За назвою</b>\n"
        "Напишіть модель у чат, наприклад: <code>AirPods Pro 2</code> або <code>PlayStation 5</code>.\n\n"
        "<b>2. За посиланням</b>\n"
        "Надішліть від 1 до 10 посилань із Rozetka, Prom, Comfy, Алло, Епіцентр, OLX, Amazon тощо.\n\n"
        "<b>3. Контроль наявності</b>\n"
        "Надішліть посилання на розкуплений товар — сповістимо, коли він з'явиться."
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
    image_url: str | None = None,
    product_url: str | None = None,
    status_header: str = "",
    status_line: str = "",
    lang: str = "uk",
) -> str:
    """
    Чиста та структурована картка товару без зайвих емодзі й без розривів рядків.
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

    mp_name = format_marketplace_name(marketplace)
    short_title = truncate_title(title, max_len=58)
    header_text = status_header or status_line or t("status_tracking_on", lang)
    avail_text = {
        "uk": "В наявності" if is_available else "Немає в наявності",
        "en": "In stock" if is_available else "Out of stock",
        "ru": "В наличии" if is_available else "Нет в наличии",
    }.get(lang, "В наявності" if is_available else "Немає в наявності")

    cat_badge = format_category_badge(category_tag, lang)
    cat_part = f" · {cat_badge}" if cat_badge else ""
    preview_target = image_url or product_url

    if not is_available or cur_dec <= Decimal("0"):
        sniper_lbl = {
            "uk": "Статус: очікуємо появу в наявності\nСповістимо, щойно товар повернеться у продаж.",
            "en": "Status: waiting for restock\nWe'll notify you as soon as it's back in stock.",
            "ru": "Статус: ожидаем появление в наличии\nСообщим, как только товар вернётся в продажу.",
        }.get(lang, "")
        last_known = (
            f"\nОстання ціна: <code>{fmt_money_with_uah(cur_dec, curr)}</code>"
            if cur_dec > Decimal("0")
            else ""
        )
        sniper_card = (
            f"{header_text}\n\n"
            f"<b>{short_title}</b>\n"
            f"{mp_name} · {avail_text}{cat_part}\n\n"
            f"{sniper_lbl}{last_known}"
        )
        return embed_invisible_image_link(sniper_card, preview_target)

    ref_old = old_price if (old_price and old_price > cur_dec) else start_30d

    if ref_old > cur_dec:
        diff_val = ref_old - cur_dec
        pct = int(round(((float(ref_old) - cur_f) / float(ref_old)) * 100))
        lbl = {"uk": "Ціна:", "en": "Price:", "ru": "Цена:"}.get(lang, "Ціна:")
        save_lbl = {"uk": "Економія:", "en": "Save:", "ru": "Экономия:"}.get(lang, "Економія:")
        price_block = (
            f"{lbl} <b><code>{fmt_money_with_uah(cur_dec, curr)}</code></b> "
            f"<s>{fmt_money(ref_old, curr)}</s> (<code>-{max(1, pct)}%</code>)\n"
            f"{save_lbl} <code>{fmt_money(diff_val, curr)}</code>"
        )
    elif cur_dec > start_30d and start_30d > Decimal("0"):
        pct = int(round(((cur_f - float(start_30d)) / max(0.01, float(start_30d))) * 100))
        lbl = {"uk": "Ціна:", "en": "Price:", "ru": "Цена:"}.get(lang, "Ціна:")
        price_block = (
            f"{lbl} <b><code>{fmt_money_with_uah(cur_dec, curr)}</code></b> "
            f"<s>{fmt_money(start_30d, curr)}</s> (<code>+{max(1, pct)}%</code>)"
        )
    else:
        lbl = {"uk": "Ціна:", "en": "Price:", "ru": "Цена:"}.get(lang, "Ціна:")
        price_block = f"{lbl} <b><code>{fmt_money_with_uah(cur_dec, curr)}</code></b>"

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
            "uk": "дуже вигідно",
            "en": "great deal",
            "ru": "очень выгодно",
        }.get(lang, "дуже вигідно")
    elif deal_score >= 70:
        verdict = {
            "uk": "хороша ціна",
            "en": "good price",
            "ru": "хорошая цена",
        }.get(lang, "хороша ціна")
    else:
        verdict = {
            "uk": "краще зачекати",
            "en": "better wait",
            "ru": "лучше подождать",
        }.get(lang, "краще зачекати")

    if target_price is not None:
        tgt_dec = Decimal(str(target_price))
        if cur_dec <= tgt_dec:
            tgt_status = {
                "uk": "досягнуто",
                "en": "reached",
                "ru": "достигнута",
            }.get(lang, "досягнуто")
        else:
            rem = cur_dec - tgt_dec
            tgt_status = {
                "uk": f"ще −{fmt_money(rem, curr)}",
                "en": f"−{fmt_money(rem, curr)} left",
                "ru": f"ещё −{fmt_money(rem, curr)}",
            }.get(lang, f"ще −{fmt_money(rem, curr)}")
        tgt_lbl = {"uk": "Ваша ціль:", "en": "Target:", "ru": "Ваша цель:"}.get(lang, "Ваша ціль:")
        target_line = f"{tgt_lbl} <code>{fmt_money_with_uah(tgt_dec, curr)}</code> ({tgt_status})\n"
    else:
        target_line = {
            "uk": "Ваша ціль: не задана\n",
            "en": "Target: not set\n",
            "ru": "Ваша цель: не задана\n",
        }.get(lang, "")

    lbl_30d = {"uk": "За 30 днів:", "en": "30-day stats:", "ru": "За 30 дней:"}.get(lang, "За 30 днів:")
    lbl_min = {"uk": "Мін", "en": "Min", "ru": "Мин"}.get(lang, "Мін")
    lbl_avg = {"uk": "Сер", "en": "Avg", "ru": "Сред"}.get(lang, "Сер")
    lbl_max = {"uk": "Макс", "en": "Max", "ru": "Макс"}.get(lang, "Макс")

    card_body = (
        f"{header_text}\n\n"
        f"<b>{short_title}</b>\n"
        f"{mp_name} · {avail_text}{cat_part}\n\n"
        f"{price_block}\n"
        f"{target_line}"
        f"Deal Score: <code>{deal_score}/100</code> ({verdict})\n\n"
        f"<b>{lbl_30d}</b>\n"
        f"{lbl_min}: <code>{fmt_money(min_30d, curr)}</code>\n"
        f"{lbl_avg}: <code>{fmt_money(avg_30d, curr)}</code>\n"
        f"{lbl_max}: <code>{fmt_money(max_30d, curr)}</code>"
    )
    return embed_invisible_image_link(card_body, preview_target)


def render_target_price_card(
    *,
    title: str,
    current_price: Decimal,
    currency: str = "UAH",
    current_target: Decimal | None = None,
    lang: str = "uk",
) -> str:
    """Компактний екран налаштування бажаної ціни."""
    lang = normalize_lang(lang)
    cur = Decimal(str(current_price))
    curr = currency or "UAH"
    quant = Decimal("1") if cur >= Decimal("100") else Decimal("0.01")
    short_title = truncate_title(title, max_len=52)

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
        tgt_now = f"Active target: <code>{fmt_money(current_target, curr)}</code>\n" if current_target else ""
        return (
            "<b>Target Price</b>\n\n"
            f"<b>{short_title}</b>\n"
            f"Current price: <code>{fmt_money(cur, curr)}</code>\n"
            f"{tgt_now}\n"
            f"• -5% → <code>{p5}</code> (−{s5})\n"
            f"• -10% → <code>{p10}</code> (−{s10})\n"
            f"• -15% → <code>{p15}</code> (−{s15})\n"
            f"• -20% → <code>{p20}</code> (−{s20})\n\n"
            f"Choose a button below or type your price in chat (e.g. <code>{suggested_val}</code>):"
        )
    if lang == "ru":
        tgt_now = f"Текущая цель: <code>{fmt_money(current_target, curr)}</code>\n" if current_target else ""
        return (
            "<b>Желаемая цена</b>\n\n"
            f"<b>{short_title}</b>\n"
            f"Сейчас: <code>{fmt_money(cur, curr)}</code>\n"
            f"{tgt_now}\n"
            f"• -5% → <code>{p5}</code> (−{s5})\n"
            f"• -10% → <code>{p10}</code> (−{s10})\n"
            f"• -15% → <code>{p15}</code> (−{s15})\n"
            f"• -20% → <code>{p20}</code> (−{s20})\n\n"
            f"Выберите кнопку ниже или напишите сумму в чат (например, <code>{suggested_val}</code>):"
        )

    tgt_now = f"Встановлена ціль: <code>{fmt_money(current_target, curr)}</code>\n" if current_target else ""
    return (
        "<b>Бажана ціна</b>\n\n"
        f"<b>{short_title}</b>\n"
        f"Зараз: <code>{fmt_money(cur, curr)}</code>\n"
        f"{tgt_now}\n"
        f"• -5% → <code>{p5}</code> (−{s5})\n"
        f"• -10% → <code>{p10}</code> (−{s10})\n"
        f"• -15% → <code>{p15}</code> (−{s15})\n"
        f"• -20% → <code>{p20}</code> (−{s20})\n\n"
        f"Оберіть кнопку нижче або напишіть суму в чат (наприклад, <code>{suggested_val}</code>):"
    )


def render_delete_confirm_card(title: str, lang: str = "uk") -> str:
    """Картка підтвердження видалення одного товару."""
    lang = normalize_lang(lang)
    short_t = truncate_title(title, max_len=52)
    if lang == "en":
        return (
            "<b>Remove item from tracking?</b>\n\n"
            f"{short_t}"
        )
    if lang == "ru":
        return (
            "<b>Удалить товар из отслеживания?</b>\n\n"
            f"{short_t}"
        )
    return (
        "<b>Видалити товар із відстеження?</b>\n\n"
        f"{short_t}"
    )


def render_clear_confirm_card(count: int, lang: str = "uk") -> str:
    """Картка підтвердження очищення всього списку відстеження."""
    lang = normalize_lang(lang)
    if lang == "en":
        return (
            "<b>Clear all tracked items?</b>\n\n"
            f"All <code>{count}</code> items will be removed from your list."
        )
    if lang == "ru":
        return (
            "<b>Очистить весь список товаров?</b>\n\n"
            f"Будут удалены все товары (<code>{count} шт.</code>)."
        )
    return (
        "<b>Очистити весь список товарів?</b>\n\n"
        f"Буде видалено всі відстежувані товари (<code>{count} шт.</code>)."
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
    Чиста картка сповіщення про зниження ціни.
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

    short_title = truncate_title(title, max_len=56)
    mp_name = format_marketplace_name(marketplace)

    header = {
        "uk": "📉 <b>Ціна знизилась</b>",
        "en": "📉 <b>Price Drop</b>",
        "ru": "📉 <b>Цена снизилась</b>",
    }.get(lang, "📉 <b>Ціна знизилась</b>")

    lbl_now = {"uk": "Нова ціна:", "en": "New price:", "ru": "Новая цена:"}.get(lang, "Нова ціна:")
    lbl_save = {"uk": "Економія:", "en": "You save:", "ru": "Экономия:"}.get(lang, "Економія:")

    target_line = ""
    if target_price is not None and new_dec <= Decimal(str(target_price)):
        target_line = {
            "uk": f"Ціль досягнуто: <code>{fmt_money(target_price, curr)}</code>\n",
            "en": f"Target reached: <code>{fmt_money(target_price, curr)}</code>\n",
            "ru": f"Цель достигнута: <code>{fmt_money(target_price, curr)}</code>\n",
        }.get(lang, "")

    return (
        f"{header}\n\n"
        f"<b>{short_title}</b>\n"
        f"{mp_name}\n\n"
        f"{lbl_now} <b><code>{fmt_money_with_uah(new_dec, curr)}</code></b> "
        f"<s>{fmt_money(old_dec, curr)}</s> (<code>-{max(1, pct)}%</code>)\n"
        f"{lbl_save} <code>{fmt_money(diff, curr)}</code>\n"
        f"{target_line}"
        f"Deal Score: <code>{deal_score}/100</code>"
    )


def build_back_in_stock_alert_card(
    *,
    title: str,
    marketplace: str,
    currency: str,
    new_price: Decimal,
    lang: str = "uk",
) -> str:
    """Картка сповіщення про появу товару в наявності."""
    lang = normalize_lang(lang)
    curr = currency or "UAH"
    new_dec = Decimal(str(new_price))
    short_title = truncate_title(title, max_len=56)
    mp_name = format_marketplace_name(marketplace)

    if lang == "en":
        return (
            "🔔 <b>Back in Stock</b>\n\n"
            f"<b>{short_title}</b>\n"
            f"{mp_name}\n\n"
            f"Price: <b><code>{fmt_money_with_uah(new_dec, curr)}</code></b>"
        )
    if lang == "ru":
        return (
            "🔔 <b>Товар снова в наличии</b>\n\n"
            f"<b>{short_title}</b>\n"
            f"{mp_name}\n\n"
            f"Цена: <b><code>{fmt_money_with_uah(new_dec, curr)}</code></b>"
        )
    return (
        "🔔 <b>Товар знову в наявності</b>\n\n"
        f"<b>{short_title}</b>\n"
        f"{mp_name}\n\n"
        f"Ціна: <b><code>{fmt_money_with_uah(new_dec, curr)}</code></b>"
    )


def render_search_results_card(
    query: str,
    offers: Sequence[Any],
    lang: str = "uk",
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Чистий список результатів пошуку за назвою без візуального шуму.
    """
    lang = normalize_lang(lang)
    safe_q = html.escape(query.strip()[:50])

    if not offers:
        if lang == "en":
            txt = (
                f"<b>Search: {safe_q}</b>\n\n"
                "No direct offers found.\n"
                "Try a shorter model name (e.g. <code>AirPods Pro 2</code>) or send a direct link from a store."
            )
        elif lang == "ru":
            txt = (
                f"<b>Поиск: {safe_q}</b>\n\n"
                "Точных предложений не найдено.\n"
                "Попробуйте написать короче (например, <code>AirPods Pro 2</code>) или отправьте прямую ссылку."
            )
        else:
            txt = (
                f"<b>Пошук: {safe_q}</b>\n\n"
                "Точних пропозицій не знайдено.\n"
                "Спробуйте коротшу назву (наприклад, <code>AirPods Pro 2</code>) або надішліть пряме посилання."
            )
        return txt, build_add_guide_keyboard(lang)

    header = {
        "uk": f"<b>Результати пошуку: {safe_q}</b>",
        "en": f"<b>Search results: {safe_q}</b>",
        "ru": f"<b>Результаты поиска: {safe_q}</b>",
    }.get(lang, "")

    rows: list[str] = []
    for idx, off in enumerate(offers[:6], start=1):
        mp_short = format_marketplace_name(off.marketplace)
        short_t = truncate_title(off.title, max_len=44)
        price_fmt = fmt_money_with_uah(off.price, off.currency)
        old_part = ""
        if off.old_price and off.old_price > off.price:
            pct = max(1, int(round(float((off.old_price - off.price) / off.old_price) * 100)))
            old_part = f" <s>{fmt_money(off.old_price, off.currency)}</s> (-{pct}%)"

        rows.append(
            f"<b>{idx}.</b> <a href=\"{html.escape(off.url)}\">{short_t}</a>\n"
            f"{mp_short} · <b><code>{price_fmt}</code></b>{old_part}"
        )

    footer = {
        "uk": "<i>Оберіть товар кнопкою нижче для відстеження:</i>",
        "en": "<i>Select an item below to track its price:</i>",
        "ru": "<i>Выберите товар кнопкой ниже для отслеживания:</i>",
    }.get(lang, "")

    text = f"{header}\n\n" + "\n\n".join(rows) + f"\n\n{footer}"
    best_img = next(
        (
            str(o.image_url).strip()
            for o in offers
            if getattr(o, "image_url", None) and str(o.image_url).strip().startswith(("https://", "http://"))
        ),
        None,
    )
    if best_img:
        text = embed_invisible_image_link(text, best_img)
    return text, build_search_results_keyboard(offers, lang=lang)


async def render_shared_wishlist_card(
    session: AsyncSession,
    owner_id: int,
    viewer_lang: str = "uk",
) -> tuple[str, InlineKeyboardMarkup]:
    """Відображає спільний вішліст за посиланням."""
    lang = normalize_lang(viewer_lang)
    subs = await crud.get_user_subscriptions_with_history(session, owner_id)
    if not subs:
        msg = {
            "uk": "Цей список наразі порожній.",
            "en": "This shared list is currently empty.",
            "ru": "Этот список сейчас пуст.",
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
        short_t = truncate_title(p.title, max_len=44)
        price_str = fmt_money_with_uah(cur, p.currency) if cur > 0 else "очікується поставка"

        rows.append(
            f"<b>{idx}.</b> <a href=\"{html.escape(p.url)}\">{short_t}</a>\n"
            f"{mp_short} · <b><code>{price_str}</code></b>"
        )

    header = {
        "uk": f"<b>Список бажань · {owner_name}</b>",
        "en": f"<b>Wishlist · {owner_name}</b>",
        "ru": f"<b>Список желаний · {owner_name}</b>",
    }.get(lang, "")

    summary = {
        "uk": (
            f"Сума зараз: <code>~{fmt_money(total_now_uah, 'UAH')}</code>\n"
            f"На мінімумі 30д: <code>~{fmt_money(total_min_uah, 'UAH')}</code>"
        ),
        "en": (
            f"Total today: <code>~{fmt_money(total_now_uah, 'UAH')}</code>\n"
            f"30d low total: <code>~{fmt_money(total_min_uah, 'UAH')}</code>"
        ),
        "ru": (
            f"Сумма сейчас: <code>~{fmt_money(total_now_uah, 'UAH')}</code>\n"
            f"На минимуме 30д: <code>~{fmt_money(total_min_uah, 'UAH')}</code>"
        ),
    }.get(lang, "")

    text = f"{header}\n{summary}\n\n" + "\n\n".join(rows)
    return text, build_shared_wishlist_keyboard(subs, lang=lang)


def build_daily_digest_card(
    items: Sequence[dict[str, Any]],
    lang: str = "uk",
) -> str:
    """Ранковий дайджест знижок."""
    lang = normalize_lang(lang)
    header = {
        "uk": "<b>Ранковий дайджест знижок</b>",
        "en": "<b>Morning Price Digest</b>",
        "ru": "<b>Утренний дайджест скидок</b>",
    }.get(lang, "<b>Ранковий дайджест знижок</b>")

    if not items:
        empty_msg = {
            "uk": "За минулу ніч ціни не змінювалися.",
            "en": "Prices remained stable overnight.",
            "ru": "За прошлую ночь цены не менялись.",
        }.get(lang, "")
        return f"{header}\n\n{empty_msg}"

    rows: list[str] = []
    savings_by_curr: dict[str, Decimal] = {}

    for idx, item in enumerate(items[:8], start=1):
        title = truncate_title(str(item.get("title", "Товар")), max_len=44)
        url = html.escape(str(item.get("url", "")))
        mp_short = format_marketplace_name(str(item.get("marketplace", "store")))
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
                f"<b><code>{fmt_money(new_p, curr)}</code></b> "
                f"<s>{fmt_money(old_p, curr)}</s> (<code>-{max(1, pct)}%</code>)"
            )
        else:
            price_part = f"<b><code>{fmt_money(new_p, curr)}</code></b>"

        rows.append(
            f"<b>{idx}.</b> {title_link}\n"
            f"{mp_short} · {price_part}"
        )

    total_savings_str = (
        " + ".join(f"<code>{fmt_money(val, c)}</code>" for c, val in savings_by_curr.items())
        if savings_by_curr
        else "<code>0 ₴</code>"
    )
    footer_lbl = {
        "uk": "Загальна знижка:",
        "en": "Total savings:",
        "ru": "Общая скидка:",
    }.get(lang, "Загальна знижка:")

    return f"{header}\n\n" + "\n\n".join(rows) + f"\n\n{footer_lbl} {total_savings_str}"


async def render_top_drops_card(
    session: AsyncSession,
    db_user: User,
    *,
    force_live: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    """Топ-5 знижок дня."""
    lang = normalize_lang(db_user.language)
    drops = await crud.get_top_price_drops(
        session,
        user_id=db_user.id,
        limit=5,
        force_live=force_live,
    )

    header = {
        "uk": "<b>Гарячі знижки дня</b>",
        "en": "<b>Hot Deals Today</b>",
        "ru": "<b>Горячие скидки дня</b>",
    }.get(lang, "<b>Гарячі знижки дня</b>")

    if not drops:
        empty = {
            "uk": "Зараз активних знижок немає. Натисніть «Оновити знижки» або додайте посилання на товар.",
            "en": "No active discounts right now. Tap 'Refresh Deals' or send a product link.",
            "ru": "Сейчас активных скидок нет. Нажмите «Обновить скидки» или отправьте ссылку на товар.",
        }.get(lang, "")
        return f"{header}\n\n{empty}", build_start_keyboard(lang)

    rows: list[str] = []
    for idx, item in enumerate(drops, start=1):
        p = item["product"]
        old_p = Decimal(str(item["old_price"]))
        new_p = Decimal(str(item["new_price"]))
        pct = float(item["drop_pct"])

        mp_short = format_marketplace_name(p.marketplace)
        short_title = truncate_title(p.title, max_len=44)

        rows.append(
            f"<b>{idx}.</b> <a href=\"{html.escape(p.url)}\">{short_title}</a>\n"
            f"{mp_short} · <b><code>{fmt_money(new_p, p.currency)}</code></b> "
            f"<s>{fmt_money(old_p, p.currency)}</s> (<code>−{pct:.0f}%</code>)"
        )

    text = f"{header}\n\n" + "\n\n".join(rows)
    return text, build_top_drops_keyboard(drops, user_id=db_user.id, lang=lang)


async def render_quick_compare_card(
    session: AsyncSession,
    db_user: User,
) -> tuple[str, InlineKeyboardMarkup]:
    """Швидке порівняння цін між відстежуваними товарами користувача."""
    lang = normalize_lang(db_user.language)
    subs = await crud.get_user_subscriptions_with_history(session, db_user.id)

    header = {
        "uk": "<b>Порівняння цін</b>",
        "en": "<b>Price Comparison</b>",
        "ru": "<b>Сравнение цен</b>",
    }.get(lang, "<b>Порівняння цін</b>")

    if not subs:
        return t("empty_tracking", lang), build_start_keyboard(lang)

    sorted_subs = sorted(subs, key=lambda s: (s.product.currency, float(s.product.current_price)))

    rows: list[str] = []
    for idx, sub in enumerate(sorted_subs[:8], start=1):
        p = sub.product
        prices = [Decimal(str(h.price)) for h in p.price_history] if p.price_history else [Decimal(str(p.current_price))]
        cur = Decimal(str(p.current_price))
        mn, mx = min(prices), max(prices)
        av = sum(prices) / Decimal(len(prices))
        score = compute_deal_score(current_price=cur, min_30d=mn, max_30d=mx, avg_30d=av, old_price=mx)

        mp_short = format_marketplace_name(p.marketplace)
        short_title = truncate_title(p.title, max_len=44)
        rows.append(
            f"<b>{idx}.</b> <a href=\"{html.escape(p.url)}\">{short_title}</a>\n"
            f"{mp_short} · <b><code>{fmt_money_with_uah(cur, p.currency)}</code></b> · Deal Score: <code>{score}/100</code>"
        )

    text = f"{header}\n\n" + "\n\n".join(rows)
    return text, build_tracking_list_keyboard(subs, user_id=db_user.id, lang=lang)


def render_settings_card(db_user: User, items_count: int = 0) -> str:
    """Компактна картка налаштувань користувача."""
    lang = normalize_lang(db_user.language)
    max_limit = settings.pro_max_products if db_user.has_active_pro else settings.free_max_products
    tier = (
        "PRO (∞)"
        if (db_user.has_active_pro and db_user.pro_until is None)
        else ("PRO" if db_user.has_active_pro else "Free")
    )
    notify_on = getattr(db_user, "notifications_enabled", True)
    dnd_on = getattr(db_user, "dnd_enabled", False)

    if lang == "en":
        return (
            "<b>Settings</b>\n\n"
            f"Plan: <code>{tier}</code> ({items_count}/{max_limit})\n"
            f"Instant alerts: <code>{'ON' if notify_on else 'OFF'}</code>\n"
            f"Night mode (23:00–08:00): <code>{'ON' if dnd_on else 'OFF'}</code>\n\n"
            "<i>In Night mode, overnight discounts arrive in a morning digest at 08:30.</i>"
        )
    if lang == "ru":
        return (
            "<b>Настройки</b>\n\n"
            f"Тариф: <code>{tier}</code> ({items_count}/{max_limit})\n"
            f"Уведомления: <code>{'ВКЛ' if notify_on else 'ВЫКЛ'}</code>\n"
            f"Ночной режим (23:00–08:00): <code>{'ВКЛ' if dnd_on else 'ВЫКЛ'}</code>\n\n"
            "<i>В Ночном режиме ночные скидки приходят утром в 08:30.</i>"
        )
    return (
        "<b>Налаштування</b>\n\n"
        f"Тариф: <code>{tier}</code> ({items_count}/{max_limit})\n"
        f"Миттєві сповіщення: <code>{'УВІМК' if notify_on else 'ВИМК'}</code>\n"
        f"Нічний режим (23:00–08:00): <code>{'УВІМК' if dnd_on else 'ВИМК'}</code>\n\n"
        "<i>У Нічному режимі нічні знижки надходять зранку о 08:30.</i>"
    )


async def render_my_tracking_text_and_kb(
    session: AsyncSession,
    db_user: User,
    page: int = 1,
    category_tag: str | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Чистий список відстежуваних товарів («Мої товари») без перевантаження та розривів.
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

    list_total_uah = Decimal("0")

    for s in subscriptions:
        p = s.product
        cur_p = Decimal(str(p.current_price))
        if not getattr(p, "is_available", True) or cur_p <= Decimal("0"):
            continue
        list_total_uah += to_uah(cur_p, p.currency)

    rows: list[str] = []
    for idx, sub in enumerate(page_subs, start=start_idx + 1):
        p = sub.product
        mp_short = format_marketplace_name(p.marketplace)
        title_escaped = truncate_title(p.title, max_len=44)
        cur = Decimal(str(p.current_price))
        is_avail = getattr(p, "is_available", True) and cur > Decimal("0")

        if not is_avail:
            sniper_str = {
                "uk": "очікується в наявності",
                "en": "waiting for restock",
                "ru": "ожидается в наличии",
            }.get(lang, "")
            rows.append(
                f"<b>{idx}.</b> <a href=\"{html.escape(p.url)}\">{title_escaped}</a>\n"
                f"{mp_short} · <i>{sniper_str}</i>"
            )
            continue

        prices = [Decimal(str(h.price)) for h in p.price_history if Decimal(str(h.price)) > 0] if p.price_history else [cur]
        mx = max(prices) if prices else cur

        if mx > cur:
            pct = max(1, int(round((float(mx - cur) / max(0.01, float(mx))) * 100)))
            price_str = f"<b><code>{fmt_money_with_uah(cur, p.currency)}</code></b> <s>{fmt_money(mx, p.currency)}</s> (-{pct}%)"
        else:
            price_str = f"<b><code>{fmt_money_with_uah(cur, p.currency)}</code></b>"

        target_badge = ""
        if sub.target_price is not None:
            tgt_dec = Decimal(str(sub.target_price))
            target_lbl = {"uk": "ціль", "en": "target", "ru": "цель"}.get(lang, "ціль")
            target_badge = f" · {target_lbl}: <code>{fmt_money(tgt_dec, p.currency)}</code>"

        rows.append(
            f"<b>{idx}.</b> <a href=\"{html.escape(p.url)}\">{title_escaped}</a>\n"
            f"{mp_short} · {price_str}{target_badge}"
        )

    wishlist_calc_line = ""
    if len(subscriptions) > 1 and list_total_uah > Decimal("0"):
        wishlist_calc_line = {
            "uk": f"Сума списку: <code>~{fmt_money(list_total_uah, 'UAH')}</code>\n\n",
            "en": f"List total: <code>~{fmt_money(list_total_uah, 'UAH')}</code>\n\n",
            "ru": f"Сумма списка: <code>~{fmt_money(list_total_uah, 'UAH')}</code>\n\n",
        }.get(lang, "")

    rows_block = wishlist_calc_line + "\n\n".join(rows)

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
    Зрозумілий прогноз ціни на 7 днів без зайвого статистичного жаргону та емодзі-шкал.
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
    drop_prob = qm.drop_probability_7d
    score = qm.deal_score

    short_title = truncate_title(product.title, max_len=54)
    mp_name = format_marketplace_name(product.marketplace)

    if lang == "en":
        if score >= 86:
            verdict = "Good time to buy — price is near its 30-day low."
        elif score >= 70:
            verdict = "Fair market price — you can buy now or set the recommended target."
        else:
            verdict = "Better wait — price is above normal, a discount is likely."

        text = (
            "<b>Price Forecast (7 Days)</b>\n\n"
            f"<b>{short_title}</b>\n"
            f"{mp_name}\n\n"
            f"Current price: <b><code>{fmt_money(cur, curr)}</code></b>\n"
            f"Fair price: <code>{fmt_money(qm.equilibrium_price, curr)}</code>\n"
            f"Normal range: <code>{fmt_money(qm.q15_support, curr)}</code> – <code>{fmt_money(qm.q85_resistance, curr)}</code>\n"
            f"Deal Score: <code>{score}/100</code>\n"
            f"7-day drop chance: <code>{drop_prob}%</code>\n\n"
            f"Recommended target: <b><code>{fmt_money(optimal_target, curr)}</code></b> (−{fmt_money(potential_save, curr)})\n\n"
            f"<b>Verdict:</b> {verdict}"
        )
    elif lang == "ru":
        if score >= 86:
            verdict = "Выгодно покупать сейчас — цена у 30-дневного минимума."
        elif score >= 70:
            verdict = "Рыночная цена в норме — можно покупать или задать рекомендованную цель."
        else:
            verdict = "Лучше подождать — цена выше обычной, вероятно снижение."

        text = (
            "<b>Прогноз цены (7 дней)</b>\n\n"
            f"<b>{short_title}</b>\n"
            f"{mp_name}\n\n"
            f"Сейчас: <b><code>{fmt_money(cur, curr)}</code></b>\n"
            f"Справедливая цена: <code>{fmt_money(qm.equilibrium_price, curr)}</code>\n"
            f"Типичный диапазон: <code>{fmt_money(qm.q15_support, curr)}</code> – <code>{fmt_money(qm.q85_resistance, curr)}</code>\n"
            f"Deal Score: <code>{score}/100</code>\n"
            f"Вероятность скидки: <code>{drop_prob}%</code>\n\n"
            f"Рекомендованная цель: <b><code>{fmt_money(optimal_target, curr)}</code></b> (−{fmt_money(potential_save, curr)})\n\n"
            f"<b>Вывод:</b> {verdict}"
        )
    else:
        if score >= 86:
            verdict = "Вигідно купувати зараз — ціна біля мінімуму за 30 днів."
        elif score >= 70:
            verdict = "Ринкова ціна в нормі — можна купувати або задати рекомендовану ціль."
        else:
            verdict = "Краще зачекати — ціна вища за звичайну, ймовірне зниження."

        text = (
            "<b>Прогноз ціни (7 днів)</b>\n\n"
            f"<b>{short_title}</b>\n"
            f"{mp_name}\n\n"
            f"Зараз: <b><code>{fmt_money(cur, curr)}</code></b>\n"
            f"Справедлива ціна: <code>{fmt_money(qm.equilibrium_price, curr)}</code>\n"
            f"Типовий діапазон: <code>{fmt_money(qm.q15_support, curr)}</code> – <code>{fmt_money(qm.q85_resistance, curr)}</code>\n"
            f"Deal Score: <code>{score}/100</code>\n"
            f"Ймовірність знижки: <code>{drop_prob}%</code>\n\n"
            f"Рекомендована ціль: <b><code>{fmt_money(optimal_target, curr)}</code></b> (−{fmt_money(potential_save, curr)})\n\n"
            f"<b>Висновок:</b> {verdict}"
        )

    kb = build_ai_forecast_keyboard(
        product_id=product.id,
        optimal_price=optimal_target,
        currency=curr,
        lang=lang,
    )
    return embed_invisible_image_link(text, product.image_url or product.url), kb


async def render_pro_offer_card(
    session: AsyncSession,
    db_user: User,
) -> str:
    """Лаконічна картка тарифу PRO."""
    lang = normalize_lang(db_user.language)
    subs = await crud.get_user_subscriptions_with_history(session, db_user.id)
    used_count = len(subs)
    free_max = settings.free_max_products
    pro_max = settings.pro_max_products
    stars_30 = settings.pro_price_stars
    stars_90 = max(1, int(round(stars_30 * 2.4)))
    stars_365 = max(1, int(round(stars_30 * 7.2)))

    if db_user.has_active_pro and db_user.pro_until is None:
        status_line = {
            "uk": f"Ваш тариф: <b>PRO (Довічний)</b> · <code>{used_count}/{pro_max}</code>",
            "en": f"Your plan: <b>PRO (Lifetime)</b> · <code>{used_count}/{pro_max}</code>",
            "ru": f"Ваш тариф: <b>PRO (Пожизненный)</b> · <code>{used_count}/{pro_max}</code>",
        }.get(lang, "")
    elif db_user.has_active_pro and db_user.pro_until is not None:
        until_str = db_user.pro_until.strftime("%d.%m.%Y")
        status_line = {
            "uk": f"Ваш тариф: <b>PRO до {until_str}</b> · <code>{used_count}/{pro_max}</code>",
            "en": f"Your plan: <b>PRO until {until_str}</b> · <code>{used_count}/{pro_max}</code>",
            "ru": f"Ваш тариф: <b>PRO до {until_str}</b> · <code>{used_count}/{pro_max}</code>",
        }.get(lang, "")
    else:
        status_line = {
            "uk": f"Ваш тариф: <b>Free</b> · <code>{used_count}/{free_max}</code>",
            "en": f"Your plan: <b>Free</b> · <code>{used_count}/{free_max}</code>",
            "ru": f"Ваш тариф: <b>Free</b> · <code>{used_count}/{free_max}</code>",
        }.get(lang, "")

    if lang == "en":
        return (
            "<b>Price Tracker PRO</b>\n"
            f"{status_line}\n\n"
            "<b>Features:</b>\n"
            f"• Up to <b>{pro_max} products</b> (vs {free_max} in Free)\n"
            "• Price checks every <b>12 hours</b>\n"
            "• 7-day price forecast & optimal target\n"
            "• Bulk link import (up to 10 links)\n"
            "• 30-day analytics & CSV export\n\n"
            "<b>Plans:</b>\n"
            f"• 30 days — <code>{stars_30} ⭐️</code>\n"
            f"• 90 days (-20%) — <code>{stars_90} ⭐️</code>\n"
            f"• 365 days (-40%) — <code>{stars_365} ⭐️</code>"
        )
    if lang == "ru":
        return (
            "<b>Тариф PRO</b>\n"
            f"{status_line}\n\n"
            "<b>Возможности:</b>\n"
            f"• До <b>{pro_max} товаров</b> (вместо {free_max} в Free)\n"
            "• Проверка цен каждые <b>12 часов</b>\n"
            "• Прогноз цены и авто-цель в 1 клик\n"
            "• Пакетное добавление (до 10 ссылок)\n"
            "• Аналитика за 30 дней и экспорт в CSV\n\n"
            "<b>Тарифы:</b>\n"
            f"• 30 дней — <code>{stars_30} ⭐️</code>\n"
            f"• 90 дней (-20%) — <code>{stars_90} ⭐️</code>\n"
            f"• 365 дней (-40%) — <code>{stars_365} ⭐️</code>"
        )
    return (
        "<b>Тариф PRO</b>\n"
        f"{status_line}\n\n"
        "<b>Можливості:</b>\n"
        f"• До <b>{pro_max} товарів</b> (замість {free_max} у Free)\n"
        "• Перевірка цін кожні <b>12 годин</b>\n"
        "• Прогноз ціни та авто-ціль в 1 клік\n"
        "• Пакетне додавання (до 10 посилань)\n"
        "• Аналітика за 30 днів та експорт у CSV\n\n"
        "<b>Плани:</b>\n"
        f"• 30 днів — <code>{stars_30} ⭐️</code>\n"
        f"• 90 днів (-20%) — <code>{stars_90} ⭐️</code>\n"
        f"• 365 днів (-40%) — <code>{stars_365} ⭐️</code>"
    )


def render_promo_prompt_card(lang: str = "uk") -> str:
    """Картка запрошення ввести промокод для активації довічного плану PRO."""
    lang = normalize_lang(lang)
    if lang == "en":
        return (
            "<b>Activate Promo Code</b>\n\n"
            "Send your promo code in a message right now\n"
            "(or use <code>/promo CODE</code>)."
        )
    if lang == "ru":
        return (
            "<b>Активация промокода</b>\n\n"
            "Отправьте ваш промокод сообщением в чат\n"
            "(или введите <code>/promo КОД</code>)."
        )
    return (
        "<b>Активація промокоду</b>\n\n"
        "Надішліть ваш промокод повідомленням у чат\n"
        "(або введіть <code>/promo КОД</code>)."
    )


def render_promo_success_card(db_user: User, code: str, items_count: int = 0) -> str:
    """Картка успішної активації промокоду на довічний тариф PRO."""
    lang = normalize_lang(db_user.language)
    clean_code = html.escape(code.strip().upper())
    pro_max = settings.pro_max_products
    if lang == "en":
        return (
            "<b>Promo Code Activated</b>\n\n"
            f"Code: <code>{clean_code}</code>\n"
            "Plan: <code>PRO · Lifetime (∞)</code>\n"
            f"Limit: <code>{items_count}/{pro_max}</code> items\n"
            "Price checks: <code>every 12h</code>"
        )
    if lang == "ru":
        return (
            "<b>Промокод активирован</b>\n\n"
            f"Код: <code>{clean_code}</code>\n"
            "Тариф: <code>PRO · Пожизненный (∞)</code>\n"
            f"Лимит: <code>{items_count}/{pro_max}</code> товаров\n"
            "Проверка цен: <code>каждые 12 ч</code>"
        )
    return (
        "<b>Промокод активовано</b>\n\n"
        f"Код: <code>{clean_code}</code>\n"
        "Тариф: <code>PRO · Довічний (∞)</code>\n"
        f"Ліміт: <code>{items_count}/{pro_max}</code> товарів\n"
        "Перевірка цін: <code>кожні 12 год</code>"
    )


async def render_user_stats_text(session: AsyncSession, db_user: User) -> str:
    """Зведена 30-денна аналітика списку користувача без перевантаження."""
    lang = normalize_lang(db_user.language)
    subscriptions = await crud.get_user_subscriptions_with_history(session, db_user.id)
    if not subscriptions:
        return t("empty_analytics", lang)

    savings_by_currency: dict[str, Decimal] = {}
    cart_by_currency: dict[str, Decimal] = {}
    total_cart_uah = Decimal("0")
    total_savings_uah = Decimal("0")
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

        saved = max(Decimal("0"), mx - cur)
        if saved > 0:
            savings_by_currency[curr] = savings_by_currency.get(curr, Decimal("0")) + saved
            total_savings_uah += to_uah(saved, curr)

        score = compute_deal_score(current_price=cur, min_30d=mn, max_30d=mx, avg_30d=av, old_price=mx)
        scores.append(score)

        if sub.target_price is not None and cur <= Decimal(str(sub.target_price)):
            reached_targets += 1

        mp_name = format_marketplace_name(prod.marketplace)
        short_title = truncate_title(prod.title, max_len=44)
        save_badge = f"(−{fmt_money(saved, curr)})" if saved > 0 else ""

        best_deals_lines.append(
            t(
                "analytics_item_row",
                lang,
                mp_label=mp_name,
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
            savings_str += f" (~{fmt_money(total_savings_uah, 'UAH')})"
    else:
        savings_str = "0 ₴"

    cart_str = " + ".join(fmt_money(val, curr) for curr, val in cart_by_currency.items()) or "0 ₴"
    if has_foreign_currency and total_cart_uah > 0:
        cart_str += f" (~{fmt_money(total_cart_uah, 'UAH')})"

    avg_score = int(round(sum(scores) / max(1, len(scores)))) if scores else 75

    portfolio_meta = {
        "uk": (
            f"Сума кошика: <code>{cart_str}</code>\n"
            f"Середній Deal Score: <code>{avg_score}/100</code>\n"
            f"Цілей досягнуто: <code>{reached_targets}</code>\n"
        ),
        "en": (
            f"Cart value: <code>{cart_str}</code>\n"
            f"Average Deal Score: <code>{avg_score}/100</code>\n"
            f"Targets reached: <code>{reached_targets}</code>\n"
        ),
        "ru": (
            f"Сумма корзины: <code>{cart_str}</code>\n"
            f"Средний Deal Score: <code>{avg_score}/100</code>\n"
            f"Целей достигнуто: <code>{reached_targets}</code>\n"
        ),
    }.get(lang, "")

    base_text = t(
        "analytics_header",
        lang,
        count=len(subscriptions),
        savings=savings_str,
        items=portfolio_meta + "\n" + "\n\n".join(best_deals_lines),
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
