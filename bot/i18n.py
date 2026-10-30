from __future__ import annotations

from decimal import Decimal
import string

SUPPORTED_LANGUAGES: dict[str, str] = {
    "uk": "🇺🇦 Українська",
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
}

CURRENCY_SYMBOLS: dict[str, str] = {
    "UAH": "₴",
    "USD": "$",
    "EUR": "€",
    "PLN": "zł",
    "GBP": "£",
    "CZK": "Kč",
    "TRY": "₺",
    "KZT": "₸",
    "JPY": "¥",
    "CAD": "CA$",
    "AUD": "A$",
    "CHF": "CHF",
    "RON": "lei",
    "BGN": "лв",
}


def fmt_money(value: Decimal | float | int, currency: str = "UAH") -> str:
    """Форматує грошову суму з валютним символом (напр. 14 999 ₴ або $199.99)."""
    curr = (currency or "UAH").upper()
    sym = CURRENCY_SYMBOLS.get(curr, curr)
    val = float(value)

    if curr in ("USD", "EUR", "GBP", "CAD", "AUD", "CHF", "PLN"):
        if val.is_integer():
            num_str = f"{val:,.0f}".replace(",", " ")
        else:
            num_str = f"{val:,.2f}".replace(",", " ")
        if curr in ("USD", "GBP", "CAD", "AUD"):
            return f"{sym}{num_str}"
        return f"{num_str} {sym}"

    if val.is_integer():
        num_str = f"{val:,.0f}".replace(",", " ")
    else:
        num_str = f"{val:,.2f}".replace(",", " ")
    return f"{num_str} {sym}"


def fmt_money_with_uah(value: Decimal | float | int, currency: str = "UAH") -> str:
    """
    Форматує грошову суму та для іноземних валют ($ / € / zł / £) автоматично додає
    еквівалент у гривні за курсом НБУ: напр. '$149.00 (~6 190 ₴)'.
    """
    base = fmt_money(value, currency)
    curr = (currency or "UAH").strip().upper()
    if curr == "UAH":
        return base
    from services.currency import format_uah_equivalent

    return f"{base}{format_uah_equivalent(value, curr)}"


class _SafeFormatDict(dict[str, object]):
    """Безпечний словник форматування, який не викликає KeyError на відсутніх плейсхолдерах."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


TRANSLATIONS: dict[str, dict[str, str]] = {
    # =========================================================================
    # УКРАЇНСЬКА (UK)
    # =========================================================================
    "uk": {
        # Reply-кнопки головного меню (компактні, без обрізання на екранах телефонів)
        "btn_my_products": "📦 Мої товари",
        "btn_add_product": "➕ Додати товар",
        "btn_analytics": "📊 Аналітика",
        "btn_pro": "⭐️ PRO",
        "btn_marketplaces": "🏬 Магазини",
        "btn_help": "ℹ️ Довідка",
        "btn_language": "🌐 Мова",
        "btn_lang": "🌐 Мова",
        "btn_referral": "🎁 Бонуси",
        "btn_export_csv": "📥 Експорт CSV",

        # Inline-кнопки (вивірена довжина для 1-, 2- та 4-колонкових рядків)
        "ibtn_my_products": "📦 Мої товари",
        "ibtn_my_list": "📦 Мої товари",
        "ibtn_marketplaces": "🏬 Магазини",
        "ibtn_pro_plan": "⭐️ PRO",
        "ibtn_pro": "⭐️ PRO",
        "ibtn_how_it_works": "📖 Довідка",
        "ibtn_demo_drop": "🧪 Демо",
        "ibtn_referral": "🎁 Бонус",
        "ibtn_language": "🌐 Мова",
        "ibtn_lang": "🌐 Мова",
        "ibtn_buy_store": "🛒 Відкрити в {store}{mp} ↗",
        "ibtn_chart_30d": "📈 Графік 30д",
        "ibtn_refresh_price": "🔄 Оновити",
        "ibtn_change_target": "🎯 Ціль ({price})",
        "ibtn_set_target_pro": "🎯 Цільова ціна ⭐️",
        "ibtn_stop_tracking": "🗑 Видалити",
        "ibtn_back_to_list": "⬅️ Мої товари",
        "ibtn_main_menu": "🏠 Меню",
        "ibtn_refresh_all": "🔄 Оновити",
        "ibtn_export_csv": "📥 CSV-звіт",
        "ibtn_add_more": "➕ Додати",
        "ibtn_clear_list": "🧹 Очистити",
        "ibtn_buy_pro_30d": "💫 PRO на 30 днів — {stars} ⭐️",
        "ibtn_buy_pro_90d": "🔥 PRO на 90 днів (-20%) — {stars}{stars90} ⭐️",
        "ibtn_test_activate_pro": "🧪 Тест PRO",
        "ibtn_share_friend": "📤 Поділитися з другом",
        "ibtn_buy_with_discount": "🔥 Купити в {store}{mp} зі знижкою ↗",
        "ibtn_Chart_30d": "📈 Графік 30д",
        "ibtn_reset_target": "🧹 Скинути",
        "ibtn_cancel": "✖️ Скасувати",

        # Вітання та довідка
        "welcome_title": "👋 <b>Global Price Tracker Bot</b>",
        "welcome_body": (
            "Моніторинг цін 24/7 у <b>50+ маркетплейсах світу та України</b> "
            "(Rozetka, Prom, Епіцентр, Comfy, Алло, Amazon, eBay, AliExpress, Allegro, Steam та ін.).\n\n"
            "✨ <b>Можливості:</b>\n"
            "• 📈 <b>Графік за 30 днів</b> одразу при додаванні\n"
            "• 💎 <b>Deal Score (0–100)</b> — оцінка реальності знижки\n"
            "• 🔔 <b>Миттєві сповіщення</b> при падінні ціни\n"
            "• 📥 <b>Експорт у CSV/Excel</b> одним кліком\n\n"
            "📋 <b>Профіль:</b> {tier}  •  <b>{used}/{max_limit}</b> товарів  •  раз на <b>{interval}</b>\n\n"
            "👇 <b>Надішліть посилання на товар у чат:</b>"
        ),
        "marketplaces_text": (
            "🌍 <b>Підтримувані магазини (Україна + Світ)</b>\n\n"
            "🇺🇦 <b>Україна:</b>\n"
            "Rozetka • Prom.ua • Епіцентр • Comfy • Алло • Фокстрот • MOYO • Цитрус • MAKEUP • EVA • Kasta • Maudau • Telemart • Brain • Ябко • OLX\n\n"
            "🌎 <b>США, Європа та Азія:</b>\n"
            "Amazon • eBay • AliExpress • Temu • SHEIN • Allegro • Ceneo • x-kom • MediaMarkt • Walmart • BestBuy • Target • Zalando • ASOS • iHerb • Steam\n\n"
            "🌐 <b>+ Універсальний парсер:</b>\n"
            "Підтримує <b>95%+ інтернет-магазинів світу</b> (Shopify, WooCommerce, OpenGraph) з автовизначенням валюти (₴, $, €, zł, £)."
        ),
        "how_it_works_text": (
            "📖 <b>Як користуватися ботом</b>\n\n"
            "1️⃣ <b>Надішліть посилання</b> на товар з будь-якого магазину.\n"
            "2️⃣ <b>Аналіз за 30 днів:</b> бот миттєво покаже поточну вигоду (<b>Deal Score</b>) та графік цін за місяць.\n"
            "3️⃣ <b>Сповіщення:</b> щойно ціна знизиться або досягне вашої <b>🎯 Цільової ціни</b>, бот надішле алерт."
        ),
        "add_product_prompt": (
            "➕ <b>Додавання товару</b>\n\n"
            "Надішліть посилання на товар із будь-якого магазину:\n"
            "• <code>https://rozetka.com.ua/...</code>\n"
            "• <code>https://comfy.ua/...</code>\n"
            "• <code>https://www.amazon.com/dp/...</code>"
        ),

        # Мої товари / Аналітика / CSV / Адмінка / Реферали
        "empty_tracking": (
            "📭 <b>Список порожній</b>\n\n"
            "Надішліть посилання або назву товару, щоб почати відстеження цін."
        ),
        "my_products_header": (
            "📦 <b>Мої товари ({used}/{max_limit})</b>\n\n"
            "{rows}\n\n"
            "<i>Натисніть на товар нижче для деталей:</i>"
        ),
        "empty_analytics": (
            "📊 <b>Аналітика (30 днів)</b>\n\n"
            "Список порожній. Додайте перший товар за посиланням."
        ),
        "analytics_item_row": (
            "• <b>{title}</b> ({mp_label})\n"
            "  <code>{cur}</code>  ·  мін <code>{mn}</code>  ·  макс <code>{mx}</code>"
        ),
        "analytics_header": (
            "📊 <b>Аналітика (30 днів)</b>\n\n"
            "Товарів: <b>{count}</b>\n"
            "Економія: <b><code>{savings}</code></b>\n\n"
            "{items}"
        ),
        "ref_welcome_applied": (
            "🎁 <b>Бонус активовано</b>\n"
            "Вам нараховано <b>+3 дні PRO</b> за запрошенням."
        ),
        "ref_reward_notify": (
            "🎁 <b>Новий друг за вашим посиланням</b>\n"
            "Вам нараховано <b>+7 днів PRO</b>."
        ),
        "choose_language": (
            "🌐 <b>Мова інтерфейсу</b>\n\n"
            "Оберіть мову для меню та сповіщень:"
        ),
        "ref_program_text": (
            "🎁 <b>Бонусна програма</b>\n\n"
            "• Вам: <b>+7 днів PRO</b> за друга\n"
            "• Другу: <b>+3 дні PRO</b> при старті\n\n"
            "Запрошено: <b>{count}</b>\n\n"
            "Ваше посилання:\n"
            "<code>{link}</code>"
        ),
        "export_empty": "Список порожній. Додайте товар для експорту.",
        "export_ready": (
            "📥 <b>CSV-звіт готовий</b>\n"
            "Ціни, мінімуми за 30 днів та посилання (Excel / Google Sheets)."
        ),
        "admin_stats": (
            "<b>Панель адміністратора</b>\n\n"
            "Користувачів: <code>{total_users}</code> (PRO: <code>{pro_users}</code>)\n"
            "Товарів у базі: <code>{total_products}</code>\n"
            "Підписок: <code>{total_subs}</code>\n"
            "Точок історії: <code>{total_points}</code>"
        ),
        "pro_active_until": "Активний до <b>{date}</b>",
        "pro_offer_title": "⭐️ <b>Тариф PRO</b>",
        "pro_offer_body": (
            "{status}\n\n"
            "• До <b>{pro_max} товарів</b> (замість {free_max})\n"
            "• Перевірка <b>кожні 12 год</b>\n"
            "• Персональна <b>цільова ціна</b>\n"
            "• Експорт у CSV"
        ),
        "test_drop_need_item": "Спочатку додайте хоча б один товар.",
        "alert_body": (
            "📉 <b>Ціна знизилась</b>\n\n"
            "<b>{title}</b>\n"
            "Магазин: {mp_label}\n\n"
            "Було: <s>{old_p}</s>\n"
            "Зараз: <b>{new_p}</b> (-{diff}, -{pct:.1f}%)"
        ),
        "pro_activated_msg": (
            "⭐️ <b>Тариф PRO активовано</b>\n\n"
            "Діє до: <b>{until}</b>\n"
            "Ліміт: до <b>{max_p} товарів</b>"
        ),

        # Картка товару та аналітика 30 днів
        "avail_yes": "В наявності",
        "avail_no": "Немає в наявності",
        "target_line": "Ціль: <b>{target}</b>\n",
        "verdict_best": "Мінімум за 30 днів — вигідно купувати",
        "verdict_good": "Нижче середньої ціни",
        "verdict_wait": "Вище середньої — варто зачекати",
        "stats_30d_box": (
            "<b>За 30 днів:</b>\n"
            "• Мін: <b>{min}</b>\n"
            "• Сер: <b>{avg}</b>\n"
            "• Макс: <b>{max}</b>\n"
            "• Deal Score: <b>{score}/100</b>"
        ),
        "card_Scanning": "Перевіряю ціну в магазині...",
        "err_invalid_url": (
            "<b>Некоректне посилання</b>\n"
            "Надішліть пряме посилання (<code>https://...</code>) на сторінку товару."
        ),
        "err_out_of_stock": "<b>Товар тимчасово відсутній у продажу.</b>",
        "err_not_found": "<b>Сторінку товару не знайдено (404).</b>",
        "err_rate_limit": "<b>Магазин обмежив запити.</b> Спробуйте через 15 секунд.",
        "err_parse": (
            "<b>Не вдалося зчитати ціну</b>\n"
            "Переконайтеся, що це пряме посилання на картку товару."
        ),
        "err_scraper": "Помилка отримання даних: {err}",
        "limit_reached_title": "<b>Ліміт тарифу Free ({used}/{limit})</b>",
        "limit_reached_body": (
            "Активуйте <b>PRO</b> (до <b>{pro_max} товарів</b>) або введіть промокод."
        ),
        "status_added": "Додано у відстеження ({used}/{max_limit})",
        "status_updated": "Дані оновлено ({used}/{max_limit})",
        "enter_valid_number": "Введіть число (наприклад: <code>12500</code> або <code>49.99</code>).",
        "price_range_error": "Ціна має бути від 0.01 до 10 000 000.",
        "product_not_found_msg": "Товар не знайдено.",
        "product_not_in_list": "Товар не знайдено у вашому списку.",
        "target_saved_msg": (
            "🎯 <b>Ціль збережено: {target}</b>\n"
            "{title}\n"
            "Зараз: <b>{current}</b>"
        ),

        # Платежі, Мова та Callback-повідомлення
        "invoice_title": "Price Tracker PRO ({days} дн.)",
        "invoice_desc": (
            "До {pro_max} товарів, перевірка кожні 12 год, цільова ціна та CSV-аналітика на {days} днів."
        ),
        "payment_success": (
            "⭐️ <b>PRO активовано</b>\n\n"
            "Діє до: <b>{until}</b>\n"
            "Ліміт: до <b>{pro_max} товарів</b>"
        ),
        "lang_switched": "Мову змінено на <b>Українську</b>",
        "list_cleared_msg": "Список очищено (видалено: <b>{count}</b>).",
        "pro_activated_toast": "Тариф PRO активовано",
        "list_empty_toast": "Список порожній.",
        "refreshing_prices_toast": "Оновлюю ціни...",
        "checking_count_msg": "Перевіряю ціни ({count} шт.)...",
        "updated_count_msg": "Оновлено: <b>{ok}/{total}</b> товарів.",
        "item_deleted_toast": "Товар видалено.",
        "item_deleted_msg": "<b>Товар видалено з відстеження.</b>",
        "already_deleted_toast": "Вже видалено.",
        "pro_only_target_alert": "Цільова ціна доступна у тарифі PRO.",
        "target_reset_toast": "Ціль скинуто.",
        "target_reset_msg": "<b>Цільову ціну скинуто.</b> Сповіщення надходитимуть при будь-якому зниженні.",
        "target_prompt_title": (
            "🎯 <b>Цільова ціна</b>\n\n"
            "Зараз: <code>{current}</code>\n"
            "Оберіть знижку кнопкою або напишіть суму в чат:"
        ),
        "action_cancelled_toast": "Скасовано.",
        "action_cancelled_msg": "Введення цільової ціни скасовано.",
        "building_chart_toast": "Будую графік...",
        "chart_caption": (
            "📈 <b>Графік за 30 днів</b>\n"
            "<b>{title}</b> ({mp_label})\n\n"
            "Зараз: <b>{price}</b>\n"
            "Мін: <code>{min_p}</code>\n"
            "Сер: <code>{avg_p}</code>\n"
            "Макс: <code>{max_p}</code>"
        ),
        "refreshing_one_toast": "Перевіряю ціну...",
        "price_unchanged_toast": "Актуально: {price}",
        "price_changed_toast": "Нова ціна: {old} → {new}",
        "refresh_failed_msg": "Не вдалося оновити ціну зараз.",
        "input_placeholder": "Назва товару або посилання...",

        # Додаткові ключі інлайн-кнопок, графіків та хендлерів
        "ibtn_target_price": "🎯 Цільова ціна",
        "ibtn_refresh": "🔄 Оновити",
        "ibtn_delete": "🗑 Видалити",
        "ibtn_buy_on": "🛒 Відкрити в {store} ↗",
        "ibtn_buy_discount": "🛒 Купити в {store} ↗",
        "ibtn_pay_stars_30": "PRO на 30 днів — {stars} ⭐️",
        "ibtn_pay_stars_90": "PRO на 90 днів (-20%) — {stars} ⭐️",
        "ibtn_try_pro_free": "Тест PRO",
        "ibtn_share_ref": "📤 Поділитися посиланням",
        "ibtn_chart_short": "📈 30д",
        "ibtn_target_short": "🎯 Ціль",
        "ibtn_cart_report": "📊 Аналітика",
        "start_welcome": (
            "<b>Price Tracker</b> · Тариф: <b>{tier}</b> (до {limit} товарів)"
        ),
        "how_it_works": (
            "ℹ️ <b>Як працює бот</b>\n\n"
            "1. Надішліть <b>посилання</b> або <b>назву товару</b>.\n"
            "2. Переглядайте <b>історію цін за 30 днів</b> та графік.\n"
            "3. Задайте <b>цільову ціну</b> — бот сповістить, коли ціна впаде."
        ),
        "marketplaces_info": (
            "🏬 <b>Підтримувані магазини</b>\n\n"
            "<b>Україна:</b>\n"
            "Rozetka, Prom.ua, Епіцентр, Comfy, Алло, Фокстрот, MOYO, Цитрус, MAKEUP, EVA, Kasta, Maudau, OLX\n\n"
            "<b>Світ:</b>\n"
            "Amazon, eBay, AliExpress, Temu, SHEIN, Allegro, Ceneo, x-kom, MediaMarkt, Walmart, BestBuy, Steam\n\n"
            "Також підтримується більшість інших інтернет-магазинів за прямим посиланням (₴, $, €, zł, £)."
        ),
        "alert_price_drop": "Зниження ціни в магазині",
        "loading_product": "Перевіряю ціну та історію за 30 днів...",
        "invalid_url": (
            "<b>Некоректне посилання</b>\n"
            "Надішліть пряме посилання (<code>https://...</code>) на сторінку товару."
        ),
        "limit_free_reached": (
            "<b>Досягнуто ліміт тарифу Free ({free_max} товарів)</b>\n\n"
            "Перейдіть на <b>PRO</b> (до {pro_max} товарів) або активуйте промокод."
        ),
        "limit_pro_reached": "<b>Досягнуто ліміт тарифу PRO ({pro_max} товарів).</b>",
        "err_404": "<b>Сторінку товару не знайдено (404).</b>",
        "status_tracking_on": "✅ Відстежується",
        "status_tracking_updated": "🔄 Ціну оновлено",
        "target_set_ok": "🎯 Ціль збережено: <b>{price}</b>",
        "cancelled": "Скасовано.",
        "chart_avg": "Середня (30д)",
        "chart_target": "Ваша ціль",
        "chart_min_badge": "МІН",
        "chart_max_badge": "МАКС",
        "chart_now": "Зараз",
        "chart_min_30d": "Мін. 30д",
    },

    # =========================================================================
    # ENGLISH (EN)
    # =========================================================================
    "en": {
        # Reply buttons (compact: max 14 chars for 2-col, max 9 chars for 3-col)
        "btn_my_products": "📦 My Items",
        "btn_add_product": "➕ Add Item",
        "btn_analytics": "📊 Analytics",
        "btn_pro": "⭐️ PRO",
        "btn_marketplaces": "🏬 Stores",
        "btn_help": "ℹ️ Help",
        "btn_language": "🌐 Lang",
        "btn_lang": "🌐 Lang",
        "btn_referral": "🎁 Bonus",
        "btn_export_csv": "📥 CSV",

        # Inline buttons (strictly sized so they never truncate with '...')
        "ibtn_my_products": "📦 My Items",
        "ibtn_my_list": "📦 My Items",
        "ibtn_marketplaces": "🏬 Stores",
        "ibtn_pro_plan": "⭐️ PRO",
        "ibtn_pro": "⭐️ PRO",
        "ibtn_how_it_works": "ℹ️ Help",
        "ibtn_demo_drop": "🧪 Demo",
        "ibtn_referral": "🎁 Bonus",
        "ibtn_language": "🌐 Lang",
        "ibtn_lang": "🌐 Lang",
        "ibtn_main_menu": "🏠 Menu",
        "ibtn_buy_store": "🛒 Open on {store}{mp} ↗",
        "ibtn_chart_30d": "📈 Chart",
        "ibtn_refresh_price": "🔄 Refresh",
        "ibtn_change_target": "🎯 Target: {price}",
        "ibtn_set_target_pro": "🎯 Target Price",
        "ibtn_stop_tracking": "🗑 Delete",
        "ibtn_back_to_list": "⬅️ My Items",
        "ibtn_refresh_all": "🔄 Refresh",
        "ibtn_export_csv": "📥 CSV",
        "ibtn_add_more": "➕ Add",
        "ibtn_clear_list": "🧹 Clear",
        "ibtn_buy_pro_30d": "💫 PRO 30 Days — {stars} ⭐️",
        "ibtn_buy_pro_90d": "🔥 PRO 90 Days (-20%) — {stars}{stars90} ⭐️",
        "ibtn_test_activate_pro": "🧪 Try PRO",
        "ibtn_share_friend": "📤 Share Link",
        "ibtn_buy_with_discount": "🔥 Buy on {store}{mp} ↗",
        "ibtn_Chart_30d": "📈 Chart",
        "ibtn_reset_target": "🧹 Reset",
        "ibtn_cancel": "✖️ Cancel",

        # Welcome & Info
        "welcome_title": "👋 <b>Global Price Tracker Bot</b>",
        "welcome_body": (
            "24/7 price monitoring across <b>50+ global & local marketplaces</b> "
            "(Amazon, eBay, AliExpress, Walmart, Allegro, Rozetka, Prom, Comfy & any online shop).\n\n"
            "✨ <b>Key Features:</b>\n"
            "• 🕒 <b>30-Day Price History</b> right when you add a link\n"
            "• 💎 <b>Deal Score (0–100)</b> — verify if a discount is genuine\n"
            "• 🔔 <b>Instant Alerts</b> as soon as the price drops\n\n"
            "📋 <b>Your Plan:</b> <b>{tier}</b> • Items: <b>{used}/{max_limit}</b> • Checks: <b>{interval}</b>\n\n"
            "👇 <b>Paste and send any product link into this chat!</b>"
        ),
        "marketplaces_text": (
            "🌍 <b>Supported Marketplaces (Global + UA)</b>\n\n"
            "🌎 <b>Global, US, EU & Asia:</b>\n"
            "Amazon • eBay • AliExpress • Temu • SHEIN • Walmart • BestBuy • Target • Newegg • Allegro • Ceneo • x-kom • MediaMarkt • Zalando • ASOS • iHerb • Steam\n\n"
            "🇺🇦 <b>Ukraine:</b>\n"
            "Rozetka • Prom.ua • Epicentr • Comfy • Allo • Foxtrot • MOYO • Citrus • MAKEUP • EVA • Kasta • Maudau • OLX\n\n"
            "🌐 <b>+ Universal Smart Parser:</b>\n"
            "Works with <b>95%+ of online stores worldwide</b> (Shopify, WooCommerce, OpenGraph) in any currency ($, €, £, zł, ₴)."
        ),
        "how_it_works_text": (
            "ℹ️ <b>How Price Tracker Works</b>\n\n"
            "1️⃣ <b>Send a link</b> to any product in this chat.\n"
            "2️⃣ <b>Instant 30-Day Audit:</b> Get a 30-day price chart, min/max stats & <b>Deal Score</b>.\n"
            "3️⃣ <b>24/7 Watch:</b> Receive an instant alert as soon as the price drops or hits your <b>🎯 Target</b>."
        ),
        "add_product_prompt": (
            "➕ <b>Add a Product</b>\n\n"
            "Send a direct link to any product page:\n"
            "• <code>https://www.amazon.com/dp/...</code>\n"
            "• <code>https://rozetka.com.ua/...</code>"
        ),

        # My products / Analytics / CSV / Admin / Referral
        "empty_tracking": (
            "📭 <b>Your list is empty</b>\n\n"
            "Send a product link or name to start price tracking."
        ),
        "my_products_header": (
            "📦 <b>My Items ({used}/{max_limit})</b>\n\n"
            "{rows}\n\n"
            "<i>Tap any product below for details:</i>"
        ),
        "empty_analytics": (
            "📊 <b>Analytics (30 Days)</b>\n\n"
            "Add your first product via link to see 30-day stats."
        ),
        "analytics_item_row": (
            "• <b>{title}</b> ({mp_label})\n"
            "  <code>{cur}</code>  ·  low <code>{mn}</code>  ·  high <code>{mx}</code>"
        ),
        "analytics_header": (
            "📊 <b>Analytics (30 Days)</b>\n\n"
            "Items: <b>{count}</b>\n"
            "Savings: <b><code>{savings}</code></b>\n\n"
            "{items}"
        ),
        "ref_welcome_applied": (
            "🎁 <b>Bonus activated</b>\n"
            "You received <b>+3 days of PRO</b>."
        ),
        "ref_reward_notify": (
            "🎁 <b>New friend joined via your link</b>\n"
            "You earned <b>+7 days of PRO</b>."
        ),
        "choose_language": (
            "🌐 <b>Language</b>\n\n"
            "Select your language for menus and alerts:"
        ),
        "ref_program_text": (
            "🎁 <b>Referral Program</b>\n\n"
            "• You get: <b>+7 days of PRO</b> per friend\n"
            "• Friend gets: <b>+3 days of PRO</b> on start\n\n"
            "Invited: <b>{count}</b>\n\n"
            "Your link:\n"
            "<code>{link}</code>"
        ),
        "export_empty": "No tracked products to export yet.",
        "export_ready": (
            "📥 <b>CSV Report ready</b>\n"
            "Compatible with Excel & Google Sheets."
        ),
        "admin_stats": (
            "<b>Admin Dashboard</b>\n\n"
            "Users: <code>{total_users}</code> (PRO: <code>{pro_users}</code>)\n"
            "Products: <code>{total_products}</code>\n"
            "Subscriptions: <code>{total_subs}</code>\n"
            "Price points: <code>{total_points}</code>"
        ),
        "pro_active_until": "Active until <b>{date}</b>",
        "pro_offer_title": "⭐️ <b>PRO Plan</b>",
        "pro_offer_body": (
            "Status: {status}\n\n"
            "• Up to <b>{pro_max} products</b> (instead of {free_max})\n"
            "• Checks <b>every 12h</b>\n"
            "• Custom <b>Target Price</b> alerts\n"
            "• CSV export"
        ),
        "test_drop_need_item": "Add at least one product first.",
        "alert_body": (
            "📉 <b>Price Drop</b>\n\n"
            "<b>{title}</b>\n"
            "Store: {mp_label}\n\n"
            "Was: <s>{old_p}</s>\n"
            "Now: <b>{new_p}</b> (-{diff}, -{pct:.1f}%)"
        ),
        "pro_activated_msg": (
            "⭐️ <b>PRO Plan Activated</b>\n\n"
            "Valid until: <b>{until}</b>\n"
            "Limit: <b>{max_p} products</b>"
        ),

        # Product card & 30d stats
        "avail_yes": "In Stock",
        "avail_no": "Out of Stock",
        "target_line": "Target: <b>{target}</b>\n",
        "verdict_best": "30-day low — best time to buy",
        "verdict_good": "Below 30-day average",
        "verdict_wait": "Above average — wait for a discount",
        "stats_30d_box": (
            "<b>30-Day Stats:</b>\n"
            "• Low: <b>{min}</b>\n"
            "• Avg: <b>{avg}</b>\n"
            "• High: <b>{max}</b>\n"
            "• Deal Score: <b>{score}/100</b>"
        ),
        "card_Scanning": "Checking store price...",
        "err_invalid_url": (
            "<b>Invalid Link</b>\n"
            "Please send a direct product URL (<code>https://...</code>)."
        ),
        "err_out_of_stock": "<b>Out of Stock</b>",
        "err_not_found": "<b>Product page not found (404).</b>",
        "err_rate_limit": "<b>Store rate limit reached.</b> Try again in 15 seconds.",
        "err_parse": (
            "<b>Could not read price</b>\n"
            "Make sure the link points directly to a product page."
        ),
        "err_scraper": "Error: {err}",
        "limit_reached_title": "<b>Free Limit Reached ({used}/{limit})</b>",
        "limit_reached_body": (
            "Upgrade to <b>PRO</b> (up to <b>{pro_max} products</b>) or redeem a promo code."
        ),
        "status_added": "Added to tracking ({used}/{max_limit})",
        "status_updated": "Updated ({used}/{max_limit})",
        "enter_valid_number": "Enter a valid number (e.g. <code>199.99</code>).",
        "price_range_error": "Price must be between 1 and 10,000,000.",
        "product_not_found_msg": "Product not found.",
        "product_not_in_list": "Product is no longer in your list.",
        "target_saved_msg": (
            "🎯 <b>Target Saved: {target}</b>\n"
            "{title}\n"
            "Current: <b>{current}</b>"
        ),

        # Payments & Callbacks
        "invoice_title": "Price Tracker PRO ({days} Days)",
        "invoice_desc": (
            "Up to {pro_max} products, checks every 12h, Target Price alerts & CSV export ({days} days)."
        ),
        "payment_success": (
            "⭐️ <b>PRO Activated</b>\n\n"
            "Valid until: <b>{until}</b>\n"
            "Limit: <b>up to {pro_max} products</b>"
        ),
        "lang_switched": "Language switched to <b>English</b>",
        "list_cleared_msg": "Tracking list cleared (<b>{count}</b> items).",
        "pro_activated_toast": "PRO Plan activated",
        "list_empty_toast": "Your list is empty.",
        "refreshing_prices_toast": "Refreshing prices...",
        "checking_count_msg": "Checking prices ({count})...",
        "updated_count_msg": "Updated: <b>{ok}/{total}</b> items.",
        "item_deleted_toast": "Removed.",
        "item_deleted_msg": "<b>Product removed from tracking.</b>",
        "already_deleted_toast": "Already removed.",
        "pro_only_target_alert": "Target Price is a PRO feature.",
        "target_reset_toast": "Target cleared.",
        "target_reset_msg": "<b>Target price cleared.</b>",
        "target_prompt_title": (
            "🎯 <b>Target Price</b>\n\n"
            "Current: <code>{current}</code>\n"
            "Choose a discount button or type your price:"
        ),
        "action_cancelled_toast": "Cancelled.",
        "action_cancelled_msg": "Cancelled.",
        "building_chart_toast": "Building chart...",
        "chart_caption": (
            "📈 <b>30-Day Price Chart</b>\n"
            "<b>{title}</b> ({mp_label})\n\n"
            "Current: <b>{price}</b>\n"
            "Low: <code>{min_p}</code>\n"
            "Avg: <code>{avg_p}</code>\n"
            "High: <code>{max_p}</code>"
        ),
        "refreshing_one_toast": "Checking store price...",
        "price_unchanged_toast": "Up to date: {price}",
        "price_changed_toast": "New price: {old} → {new}",
        "refresh_failed_msg": "Could not refresh price right now.",
        "input_placeholder": "Product name or link...",

        # Additional keys for inline buttons, charts, and handlers
        "ibtn_target_price": "🎯 Target Price",
        "ibtn_refresh": "🔄 Refresh",
        "ibtn_delete": "🗑 Delete",
        "ibtn_buy_on": "🛒 Open on {store} ↗",
        "ibtn_buy_discount": "🛒 Buy on {store} ↗",
        "ibtn_pay_stars_30": "PRO 30 Days — {stars} ⭐️",
        "ibtn_pay_stars_90": "PRO 90 Days (-20%) — {stars} ⭐️",
        "ibtn_try_pro_free": "Try PRO",
        "ibtn_share_ref": "📤 Share Link",
        "ibtn_chart_short": "📈 30d",
        "ibtn_target_short": "🎯 Goal",
        "ibtn_cart_report": "📊 Analytics",
        "start_welcome": (
            "<b>Price Tracker</b> · Plan: <b>{tier}</b> (up to {limit} items)"
        ),
        "how_it_works": (
            "ℹ️ <b>How It Works</b>\n\n"
            "1. Send a <b>link</b> or <b>product name</b>.\n"
            "2. View <b>30-day price history</b> and chart.\n"
            "3. Set a <b>target price</b> to get instant drop alerts."
        ),
        "marketplaces_info": (
            "🏬 <b>Supported Stores</b>\n\n"
            "<b>Global:</b>\n"
            "Amazon, eBay, AliExpress, Temu, SHEIN, Walmart, BestBuy, Allegro, Ceneo, x-kom, MediaMarkt, Steam\n\n"
            "<b>Ukraine:</b>\n"
            "Rozetka, Prom.ua, Epicentr, Comfy, Allo, Foxtrot, MOYO, Citrus, MAKEUP, EVA, Kasta, Maudau, OLX\n\n"
            "Also supports most online stores via direct link ($, €, £, zł, ₴)."
        ),
        "alert_price_drop": "Store price dropped",
        "loading_product": "Checking price & 30-day history...",
        "invalid_url": (
            "<b>Invalid Link</b>\n"
            "Please send a direct link (<code>https://...</code>) to a product page."
        ),
        "limit_free_reached": (
            "<b>Free Plan Limit Reached ({free_max} items)</b>\n\n"
            "Upgrade to <b>PRO</b> (up to {pro_max} items) or redeem a promo code."
        ),
        "limit_pro_reached": "<b>Maximum PRO plan limit reached ({pro_max} items).</b>",
        "err_404": "<b>Product page not found (404).</b>",
        "status_tracking_on": "✅ Tracking active",
        "status_tracking_updated": "🔄 Price updated",
        "target_set_ok": "🎯 Target saved: <b>{price}</b>",
        "cancelled": "Cancelled.",
        "chart_avg": "30d Avg",
        "chart_target": "Target",
        "chart_min_badge": "LOW",
        "chart_max_badge": "HIGH",
        "chart_now": "Now",
        "chart_min_30d": "30d Low",
    },

    # =========================================================================
    # РУССКИЙ (RU)
    # =========================================================================
    "ru": {
        # Reply-кнопки (компактные: до 14 символов для 2 колонок, до 9 для 3 колонок)
        "btn_my_products": "📦 Мои товары",
        "btn_add_product": "➕ Добавить",
        "btn_analytics": "📊 Аналитика",
        "btn_pro": "⭐️ PRO",
        "btn_marketplaces": "🏬 Магазины",
        "btn_help": "ℹ️ Справка",
        "btn_language": "🌐 Язык",
        "btn_lang": "🌐 Язык",
        "btn_referral": "🎁 Бонусы",
        "btn_export_csv": "📥 CSV",

        # Inline-кнопки (без обрезания текста '...')
        "ibtn_my_products": "📦 Мои товары",
        "ibtn_my_list": "📦 Мои товары",
        "ibtn_marketplaces": "🏬 Магазины",
        "ibtn_pro_plan": "⭐️ PRO",
        "ibtn_pro": "⭐️ PRO",
        "ibtn_how_it_works": "ℹ️ Справка",
        "ibtn_demo_drop": "🧪 Демо",
        "ibtn_referral": "🎁 Бонус",
        "ibtn_language": "🌐 Язык",
        "ibtn_lang": "🌐 Язык",
        "ibtn_main_menu": "🏠 Меню",
        "ibtn_buy_store": "🛒 Открыть в {store}{mp} ↗",
        "ibtn_chart_30d": "📈 График",
        "ibtn_refresh_price": "🔄 Обновить",
        "ibtn_change_target": "🎯 Цель: {price}",
        "ibtn_set_target_pro": "🎯 Целевая цена",
        "ibtn_stop_tracking": "🗑 Удалить",
        "ibtn_back_to_list": "⬅️ К списку",
        "ibtn_refresh_all": "🔄 Обновить",
        "ibtn_export_csv": "📥 CSV-отчёт",
        "ibtn_add_more": "➕ Добавить",
        "ibtn_clear_list": "🧹 Очистить",
        "ibtn_buy_pro_30d": "💫 PRO на 30 дней — {stars} ⭐️",
        "ibtn_buy_pro_90d": "🔥 PRO на 90 дней (-20%) — {stars}{stars90} ⭐️",
        "ibtn_test_activate_pro": "🧪 Тест PRO",
        "ibtn_share_friend": "📤 Поделиться",
        "ibtn_buy_with_discount": "🔥 Купить в {store}{mp} ↗",
        "ibtn_Chart_30d": "📈 График",
        "ibtn_reset_target": "🧹 Сбросить",
        "ibtn_cancel": "✖️ Отмена",

        # Приветствие и справка
        "welcome_title": "👋 <b>Global Price Tracker Bot</b>",
        "welcome_body": (
            "Мониторинг цен 24/7 в <b>50+ магазинах Украины и мира</b> "
            "(Rozetka, Prom, Эпицентр, Comfy, Алло, Amazon, eBay, AliExpress, Allegro и др.).\n\n"
            "✨ <b>Возможности:</b>\n"
            "• 🕒 <b>История за 30 дней</b> сразу при добавлении товара\n"
            "• 💎 <b>Deal Score (0–100)</b> — проверка реальности скидки\n"
            "• 🔔 <b>Мгновенные уведомления</b> при падении цены\n\n"
            "📋 <b>Профиль:</b> тариф <b>{tier}</b> • Товаров: <b>{used}/{max_limit}</b> • Проверка: <b>{interval}</b>\n\n"
            "👇 <b>Просто отправьте ссылку на товар в этот чат!</b>"
        ),
        "marketplaces_text": (
            "🌍 <b>Поддерживаемые магазины (Украина + Мир)</b>\n\n"
            "🇺🇦 <b>Украина:</b>\n"
            "Rozetka • Prom.ua • Эпицентр • Comfy • Алло • Фокстрот • MOYO • Цитрус • MAKEUP • EVA • Kasta • Maudau • OLX\n\n"
            "🌎 <b>США, Европа и Азия:</b>\n"
            "Amazon • eBay • AliExpress • Temu • SHEIN • Allegro • Ceneo • x-kom • MediaMarkt • Walmart • BestBuy • Steam\n\n"
            "🌐 <b>+ Универсальный парсер:</b>\n"
            "Работает с <b>95%+ интернет-магазинов мира</b> (Shopify, WooCommerce, OpenGraph) в любой валюте (₴, $, €, zł, £)."
        ),
        "how_it_works_text": (
            "ℹ️ <b>Как пользоваться ботом</b>\n\n"
            "1️⃣ <b>Отправьте ссылку</b> на товар в этот чат.\n"
            "2️⃣ <b>Аудит за 30 дней:</b> Получите график цен, мин/макс за месяц и <b>Deal Score</b>.\n"
            "3️⃣ <b>Авто-мониторинг:</b> Как только цена упадёт или достигнет вашей <b>🎯 Цели</b> — бот пришлёт уведомление."
        ),
        "add_product_prompt": (
            "➕ <b>Добавление товара</b>\n\n"
            "Отправьте ссылку на страницу товара из любого магазина:\n"
            "• <code>https://rozetka.com.ua/...</code>\n"
            "• <code>https://www.amazon.com/dp/...</code>"
        ),

        # Мои товары / Аналитика / CSV / Админка / Рефералы
        "empty_tracking": (
            "📭 <b>Список пуст</b>\n\n"
            "Отправьте ссылку или название товара, чтобы начать отслеживание цен."
        ),
        "my_products_header": (
            "📦 <b>Мои товары ({used}/{max_limit})</b>\n\n"
            "{rows}\n\n"
            "<i>Нажмите на товар ниже для деталей:</i>"
        ),
        "empty_analytics": (
            "📊 <b>Аналитика (30 дней)</b>\n\n"
            "Список пуст. Добавьте первый товар по ссылке."
        ),
        "analytics_item_row": (
            "• <b>{title}</b> ({mp_label})\n"
            "  <code>{cur}</code>  ·  мин <code>{mn}</code>  ·  макс <code>{mx}</code>"
        ),
        "analytics_header": (
            "📊 <b>Аналитика (30 дней)</b>\n\n"
            "Товаров: <b>{count}</b>\n"
            "Экономия: <b><code>{savings}</code></b>\n\n"
            "{items}"
        ),
        "ref_welcome_applied": (
            "🎁 <b>Бонус активирован</b>\n"
            "Вам начислено <b>+3 дня PRO</b> по приглашению."
        ),
        "ref_reward_notify": (
            "🎁 <b>Новый друг по вашей ссылке</b>\n"
            "Вам начислено <b>+7 дней PRO</b>."
        ),
        "choose_language": (
            "🌐 <b>Язык интерфейса</b>\n\n"
            "Выберите язык для меню и уведомлений:"
        ),
        "ref_program_text": (
            "🎁 <b>Бонусная программа</b>\n\n"
            "• Вам: <b>+7 дней PRO</b> за друга\n"
            "• Другу: <b>+3 дня PRO</b> на старте\n\n"
            "Приглашено: <b>{count}</b>\n\n"
            "Ваша ссылка:\n"
            "<code>{link}</code>"
        ),
        "export_empty": "Нет товаров для экспорта.",
        "export_ready": (
            "📥 <b>CSV-отчёт готов</b>\n"
            "Таблица открывается в Excel и Google Sheets."
        ),
        "admin_stats": (
            "<b>Панель администратора</b>\n\n"
            "Пользователей: <code>{total_users}</code> (PRO: <code>{pro_users}</code>)\n"
            "Товаров: <code>{total_products}</code>\n"
            "Подписок: <code>{total_subs}</code>\n"
            "Точек истории: <code>{total_points}</code>"
        ),
        "pro_active_until": "Активен до <b>{date}</b>",
        "pro_offer_title": "⭐️ <b>Тариф PRO</b>",
        "pro_offer_body": (
            "Статус: {status}\n\n"
            "• До <b>{pro_max} товаров</b> (вместо {free_max})\n"
            "• Проверка <b>каждые 12 ч</b>\n"
            "• <b>Целевая цена</b> для каждого товара\n"
            "• Экспорт в CSV/Excel"
        ),
        "test_drop_need_item": "Сначала добавьте хотя бы один товар.",
        "alert_body": (
            "📉 <b>Цена снизилась</b>\n\n"
            "<b>{title}</b>\n"
            "Магазин: {mp_label}\n\n"
            "Было: <s>{old_p}</s>\n"
            "Сейчас: <b>{new_p}</b> (-{diff}, -{pct:.1f}%)"
        ),
        "pro_activated_msg": (
            "⭐️ <b>Тариф PRO активирован</b>\n\n"
            "Действует до: <b>{until}</b>\n"
            "Лимит: <b>до {max_p} товаров</b>"
        ),

        # Карточка товара и аналитика 30 дней
        "avail_yes": "В наличии",
        "avail_no": "Нет в наличии",
        "target_line": "Цель: <b>{target}</b>\n",
        "verdict_best": "Минимум за 30 дней — выгодно покупать",
        "verdict_good": "Ниже средней за месяц",
        "verdict_wait": "Выше средней — стоит подождать",
        "stats_30d_box": (
            "<b>За 30 дней:</b>\n"
            "• Мин: <b>{min}</b>\n"
            "• Средняя: <b>{avg}</b>\n"
            "• Макс: <b>{max}</b>\n"
            "• Deal Score: <b>{score}/100</b>"
        ),
        "card_Scanning": "Проверяю цену в магазине...",
        "err_invalid_url": (
            "<b>Некорректная ссылка</b>\n"
            "Отправьте прямую ссылку (<code>https://...</code>) на страницу товара."
        ),
        "err_out_of_stock": "<b>Товар отсутствует в продаже.</b>",
        "err_not_found": "<b>Страница товара не найдена (404).</b>",
        "err_rate_limit": "<b>Магазин временно ограничил запросы.</b> Повторите через 15 сек.",
        "err_parse": (
            "<b>Не удалось считать цену</b>\n"
            "Убедитесь, что ссылка ведёт на страницу товара."
        ),
        "err_scraper": "Ошибка: {err}",
        "limit_reached_title": "<b>Лимит тарифа Free ({used}/{limit})</b>",
        "limit_reached_body": (
            "Активируйте <b>PRO</b> (до <b>{pro_max} товаров</b>) или введите промокод."
        ),
        "status_added": "Добавлено в отслеживание ({used}/{max_limit})",
        "status_updated": "Обновлено ({used}/{max_limit})",
        "enter_valid_number": "Введите число (например: <code>4500</code>).",
        "price_range_error": "Цена должна быть от 1 до 10 000 000.",
        "product_not_found_msg": "Товар не найден.",
        "product_not_in_list": "Товар больше не отслеживается.",
        "target_saved_msg": (
            "🎯 <b>Цель сохранена: {target}</b>\n"
            "{title}\n"
            "Сейчас: <b>{current}</b>"
        ),

        # Платежи и Callback-уведомления
        "invoice_title": "Price Tracker PRO ({days} дн.)",
        "invoice_desc": (
            "До {pro_max} товаров, проверка каждые 12 ч, целевая цена и CSV-аналитика ({days} дн.)."
        ),
        "payment_success": (
            "⭐️ <b>Тариф PRO активирован</b>\n\n"
            "Действует до: <b>{until}</b>\n"
            "Лимит: <b>до {pro_max} товаров</b>"
        ),
        "lang_switched": "Язык изменён на <b>Русский</b>",
        "list_cleared_msg": "Список очищен (<b>{count}</b> товаров).",
        "pro_activated_toast": "Тариф PRO активирован",
        "list_empty_toast": "Ваш список пуст.",
        "refreshing_prices_toast": "Обновляю цены...",
        "checking_count_msg": "Проверяю цены ({count})...",
        "updated_count_msg": "Обновлено: <b>{ok}/{total}</b> товаров.",
        "item_deleted_toast": "Удалено.",
        "item_deleted_msg": "<b>Отслеживание товара прекращено.</b>",
        "already_deleted_toast": "Уже удалено.",
        "pro_only_target_alert": "Целевая цена доступна в тарифе PRO.",
        "target_reset_toast": "Цель сброшена.",
        "target_reset_msg": "<b>Целевая цена сброшена.</b>",
        "target_prompt_title": (
            "🎯 <b>Целевая цена</b>\n\n"
            "Сейчас: <code>{current}</code>\n"
            "Выберите скидку кнопкой или введите сумму в чат:"
        ),
        "action_cancelled_toast": "Отменено.",
        "action_cancelled_msg": "Отменено.",
        "building_chart_toast": "Строю график...",
        "chart_caption": (
            "📈 <b>График за 30 дней</b>\n"
            "<b>{title}</b> ({mp_label})\n\n"
            "Сейчас: <b>{price}</b>\n"
            "Мин: <code>{min_p}</code>\n"
            "Средняя: <code>{avg_p}</code>\n"
            "Макс: <code>{max_p}</code>"
        ),
        "refreshing_one_toast": "Проверяю цену...",
        "price_unchanged_toast": "Цена актуальна: {price}",
        "price_changed_toast": "Новая цена: {old} → {new}",
        "refresh_failed_msg": "Не удалось обновить цену сейчас.",
        "input_placeholder": "Название товара или ссылка...",

        # Дополнительные ключи инлайн-кнопок, графиков и хендлеров
        "ibtn_target_price": "🎯 Целевая цена",
        "ibtn_refresh": "🔄 Обновить",
        "ibtn_delete": "🗑 Удалить",
        "ibtn_buy_on": "🛒 Открыть в {store} ↗",
        "ibtn_buy_discount": "🛒 Купить в {store} ↗",
        "ibtn_pay_stars_30": "PRO на 30 дней — {stars} ⭐️",
        "ibtn_pay_stars_90": "PRO на 90 дней (-20%) — {stars} ⭐️",
        "ibtn_try_pro_free": "Тест PRO",
        "ibtn_share_ref": "📤 Поделиться ссылкой",
        "ibtn_chart_short": "📈 30д",
        "ibtn_target_short": "🎯 Цель",
        "ibtn_cart_report": "📊 Аналитика",
        "start_welcome": (
            "<b>Price Tracker</b> · Тариф: <b>{tier}</b> (до {limit} товаров)"
        ),
        "how_it_works": (
            "ℹ️ <b>Как работает бот</b>\n\n"
            "1. Отправьте <b>ссылку</b> или <b>название товара</b>.\n"
            "2. Смотрите <b>историю цен за 30 дней</b> и график.\n"
            "3. Задайте <b>целевую цену</b> — бот сообщит, когда цена снизится."
        ),
        "marketplaces_info": (
            "🏬 <b>Поддерживаемые магазины</b>\n\n"
            "<b>Украина:</b>\n"
            "Rozetka, Prom.ua, Эпицентр, Comfy, Алло, Фокстрот, MOYO, Цитрус, MAKEUP, EVA, Kasta, Maudau, OLX\n\n"
            "<b>Мир:</b>\n"
            "Amazon, eBay, AliExpress, Temu, SHEIN, Allegro, Ceneo, x-kom, MediaMarkt, Walmart, BestBuy, Steam\n\n"
            "Также поддерживается большинство интернет-магазинов по прямой ссылке (₴, $, €, zł, £)."
        ),
        "alert_price_drop": "Снижение цены в магазине",
        "loading_product": "Проверяю цену и историю за 30 дней...",
        "invalid_url": (
            "<b>Некорректная ссылка</b>\n"
            "Отправьте прямую ссылку (<code>https://...</code>) на страницу товара."
        ),
        "limit_free_reached": (
            "<b>Достигнут лимит тарифа Free ({free_max} товаров)</b>\n\n"
            "Перейдите на <b>PRO</b> (до {pro_max} товаров) или активируйте промокод."
        ),
        "limit_pro_reached": "<b>Достигнут лимит тарифа PRO ({pro_max} товаров).</b>",
        "err_404": "<b>Страница товара не найдена (404).</b>",
        "status_tracking_on": "✅ Отслеживается",
        "status_tracking_updated": "🔄 Цена обновлена",
        "target_set_ok": "🎯 Цель сохранена: <b>{price}</b>",
        "cancelled": "Отменено.",
        "chart_avg": "Средняя (30д)",
        "chart_target": "Ваша цель",
        "chart_min_badge": "МИН",
        "chart_max_badge": "МАКС",
        "chart_now": "Сейчас",
        "chart_min_30d": "Мин. 30д",
    },
}


def normalize_lang(lang_code: str | None) -> str:
    """Нормалізує код мови Telegram до 'uk', 'en' або 'ru'."""
    if not lang_code:
        return "uk"
    code = lang_code.lower().strip()[:2]
    if code in ("uk", "ua"):
        return "uk"
    if code in ("ru", "be", "kk"):
        return "ru"
    if code in ("en",):
        return "en"
    return "uk"


def t(key: str, lang: str = "uk", **kwargs: object) -> str:
    """Повертає локалізований рядок за ключем та мовою з безпечним підставленням параметрів."""
    norm = normalize_lang(lang)
    lang_dict = TRANSLATIONS.get(norm, TRANSLATIONS["uk"])
    template = lang_dict.get(key) or TRANSLATIONS["uk"].get(key, key)
    if kwargs:
        safe_kwargs = _SafeFormatDict(kwargs)
        # Синоніми плейсхолдерів між різними модулями
        if "store" in safe_kwargs and "mp_label" not in safe_kwargs:
            safe_kwargs["mp_label"] = safe_kwargs["store"]
        if "mp_label" in safe_kwargs and "store" not in safe_kwargs:
            safe_kwargs["store"] = safe_kwargs["mp_label"]
        if "store" in safe_kwargs and "mp" not in safe_kwargs:
            safe_kwargs["mp"] = ""
        elif "mp" in safe_kwargs and "store" not in safe_kwargs:
            safe_kwargs["store"] = ""

        if "stars" in safe_kwargs and "stars90" not in safe_kwargs:
            safe_kwargs["stars90"] = ""
        elif "stars90" in safe_kwargs and "stars" not in safe_kwargs:
            safe_kwargs["stars"] = ""

        alias_pairs = [
            ("ref_link", "link"),
            ("refs_count", "count"),
            ("header", "status"),
            ("header", "reason"),
            ("users", "total_users"),
            ("products", "total_products"),
            ("subs", "total_subs"),
            ("points", "total_points"),
            ("date", "until"),
            ("pro_max", "max_p"),
            ("updated", "ok"),
            ("cur", "current"),
            ("cur", "price"),
        ]
        for a, b in alias_pairs:
            if a in safe_kwargs and b not in safe_kwargs:
                safe_kwargs[b] = safe_kwargs[a]
            elif b in safe_kwargs and a not in safe_kwargs:
                safe_kwargs[a] = safe_kwargs[b]

        safe_kwargs.setdefault("mp_lines", "—")
        safe_kwargs.setdefault("mins", 60)
        safe_kwargs.setdefault("mp_label", "Store")
        safe_kwargs.setdefault("title", "—")
        safe_kwargs.setdefault("suggested", "999")
        safe_kwargs.setdefault("err", "")

        if "pct" in safe_kwargs:
            try:
                safe_kwargs["pct"] = float(str(safe_kwargs["pct"]))
            except (ValueError, TypeError):
                safe_kwargs["pct"] = 0.0

        return string.Formatter().vformat(template, (), safe_kwargs)
    return template


def all_translations(key: str) -> set[str]:
    """Повертає множину перекладів ключа всіма мовами (для фільтрів F.text.in_(...))."""
    return {
        TRANSLATIONS[lang][key]
        for lang in TRANSLATIONS
        if key in TRANSLATIONS[lang]
    }


# Зворотна сумісність із bot.keyboards.reply
all_btn_variants = all_translations
