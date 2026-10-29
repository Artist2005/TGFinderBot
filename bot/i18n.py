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
            "📭 <b>СПИСОК ВІДСТЕЖЕННЯ ПОРОЖНІЙ</b>\n"
            "──────────────────────────────────\n"
            "Надішліть посилання на товар (Rozetka, Prom, Comfy, Алло, Amazon, Allegro тощо) — "
            "я одразу покажу <b>30-денну аналітику</b> та почну моніторинг!"
        ),
        "my_products_header": (
            "📦 <b>МОЇ ТОВАРИ У ВІДСТЕЖЕННІ ({used}/{max_limit})</b>\n"
            "──────────────────────────────────\n"
            "{rows}\n"
            "──────────────────────────────────\n"
            "👆 <i>Натисніть на товар нижче, щоб відкрити картку, графік або задати ціль:</i>"
        ),
        "empty_analytics": (
            "📊 <b>АНАЛІТИКА КОШИКА (30 ДНІВ)</b>\n"
            "──────────────────────────────────\n"
            "Список порожній. Додайте перший товар за посиланням, щоб бачити статистику економії за 30 днів!"
        ),
        "analytics_item_row": (
            "• {mp_label} <b>{title}</b>\n"
            "  💰 <code>{cur}</code> (мін: <code>{mn}</code> · макс: <code>{mx}</code>) {save_badge}"
        ),
        "analytics_header": (
            "📊 <b>АНАЛІТИКА КОШИКА (ЗА 30 ДНІВ)</b>\n"
            "──────────────────────────────────\n"
            "📦 Товарів: <code>{count}</code> · 💎 Економія: <code>{savings}</code>\n"
            "──────────────────────────────────\n"
            "{items}"
        ),
        "ref_welcome_applied": (
            "🎁 <b>Бонус активовано!</b>\n"
            "Ви отримали <b>+3 дні тарифу PRO безкоштовно</b> за запрошенням друга!"
        ),
        "ref_reward_notify": (
            "🎉 <b>Новий друг за вашим посиланням!</b>\n"
            "Вам нараховано <b>+7 днів тарифу PRO</b> безкоштовно!"
        ),
        "choose_language": (
            "🌐 <b>МОВА ІНТЕРФЕЙСУ · LANGUAGE</b>\n"
            "──────────────────────────────────\n"
            "Оберіть зручну мову для меню, графіків та сповіщень:"
        ),
        "ref_program_text": (
            "🎁 <b>БЕЗКОШТОВНИЙ PRO ЗА ДРУЗІВ</b>\n"
            "──────────────────────────────────\n"
            "• Вам: <b>+7 днів PRO</b> за кожного друга\n"
            "• Другу: <b>+3 дні PRO</b> одразу при старті\n\n"
            "👥 Запрошено друзів: <code>{count}</code>\n"
            "🔗 <b>Ваше реферальне посилання:</b>\n"
            "<code>{link}</code>"
        ),
        "export_empty": "📭 Список порожній. Додайте хоча б один товар для експорту!",
        "export_ready": (
            "📥 <b>CSV-звіт готовий!</b>\n"
            "Актуальні ціни, мінімуми/максимуми за 30 днів та посилання (Excel / Google Sheets)."
        ),
        "admin_stats": (
            "🛡 <b>ПАНЕЛЬ АДМІНІСТРАТОРА</b>\n"
            "──────────────────────────────────\n"
            "👥 Користувачів: <code>{total_users}</code> (PRO: <code>{pro_users}</code>)\n"
            "📦 Товарів у базі: <code>{total_products}</code>\n"
            "🔔 Активних підписок: <code>{total_subs}</code>\n"
            "📈 Точок історії цін: <code>{total_points}</code>\n\n"
            "🏬 <b>Топ магазинів:</b>\n"
            "{mp_lines}"
        ),
        "pro_active_until": "✅ Активний до <b>{date}</b>",
        "pro_offer_title": "⭐️ <b>ТАРИФ PRICE TRACKER PRO</b>",
        "pro_offer_body": (
            "{status}\n"
            "──────────────────────────────────\n"
            "💎 <b>Переваги PRO:</b>\n"
            "• 📦 До <b>{pro_max} товарів</b> (замість {free_max})\n"
            "• ⚡️ Перевірка цін <b>кожні 12 год</b> (замість 24 год)\n"
            "• 🎯 Персональна <b>Цільова ціна</b> на кожен товар\n"
            "• 📊 Повний <b>Deal Score</b> та CSV-експорт\n"
            "──────────────────────────────────\n"
            "💳 Від <b>{stars} ⭐️ Telegram Stars</b> або безкоштовно за друзів:"
        ),
        "test_drop_need_item": "Спочатку додайте хоча б один товар у відстеження!",
        "alert_body": (
            "🔥 <b>ЦІНА ВПАЛА! {reason}</b>\n\n"
            "{mp_label}\n"
            "📦 <b>{title}</b>\n\n"
            "📉 Було: <s>{old_p}</s>  ➔  🟢 <b>Стало: {new_p}</b>\n"
            "💰 <b>Економія: -{diff} (-{pct:.1f}%)</b>"
        ),
        "pro_activated_msg": (
            "🎉 <b>Тариф PRO активовано!</b>\n\n"
            "• Діє до: <b>{until}</b>\n"
            "• Ліміт: до <b>{max_p} товарів</b>\n"
            "• Перевірка цін: <b>кожні 12 год</b>"
        ),

        # Картка товару та аналітика 30 днів
        "avail_yes": "🟢 В наявності",
        "avail_no": "🔴 Немає в наявності",
        "target_line": "🎯 Ваша ціль: <b>{target}</b>\n",
        "verdict_best": "🔥 Мінімум за 30 днів — купуйте зараз!",
        "verdict_good": "✅ Вигідна ціна (нижче середньої)",
        "verdict_wait": "⏳ Вище середньої — варто зачекати",
        "stats_30d_box": (
            "📊 <b>Аналітика за 30 днів:</b>\n"
            "├ 30 дн. тому: <b>{start}</b>  ({trend})\n"
            "├ Мін: <b>{min}</b>  •  Макс: <b>{max}</b>\n"
            "├ Середня: <b>{avg}</b>\n"
            "├ Вигода (Deal Score): <b>{score}/100</b>\n"
            "└ <i>{verdict}</i>"
        ),
        "card_Scanning": "🔍 <b>Аналізую товар та ціни за 30 днів...</b> ⏳",
        "err_invalid_url": (
            "❌ <b>Некоректне посилання</b>\n"
            "Надішліть пряме посилання (<code>https://...</code>) на сторінку товару."
        ),
        "err_out_of_stock": "📦 <b>Товар тимчасово відсутній у продажу.</b>",
        "err_not_found": "🔎 <b>Сторінку товару не знайдено (404).</b>",
        "err_rate_limit": "⏳ <b>Магазин обмежив запити.</b> Спробуйте через 15 секунд.",
        "err_parse": (
            "⚠️ <b>Не вдалося зчитати ціну</b>\n"
            "Переконайтеся, що це пряме посилання саме на картку товару."
        ),
        "err_scraper": "❌ Помилка отримання даних: {err}",
        "limit_reached_title": "🔒 <b>Ліміт безкоштовного тарифу ({used}/{limit})</b>",
        "limit_reached_body": (
            "Активуйте <b>PRO</b> (до <b>{pro_max} товарів</b> та перевірка кожні 12 год) "
            "або запросіть друга безкоштовно!"
        ),
        "status_added": "✅ <b>Товар додано!</b> ({used}/{max_limit})",
        "status_updated": "ℹ️ <b>Дані товару оновлено!</b> ({used}/{max_limit})",
        "enter_valid_number": "❌ Введіть число (наприклад: <code>12500</code> або <code>49.99</code>).",
        "price_range_error": "❌ Ціна має бути від 0.01 до 10 000 000.",
        "product_not_found_msg": "❌ Товар не знайдено.",
        "product_not_in_list": "❌ Товар не знайдено у вашому списку.",
        "target_saved_msg": (
            "🎯 <b>Цільову ціну збережено: {target}</b>\n"
            "📦 {title}\n"
            "💰 Зараз: <b>{current}</b>"
        ),

        # Платежі, Мова та Callback-повідомлення
        "invoice_title": "Price Tracker PRO ({days} дн.)",
        "invoice_desc": (
            "До {pro_max} товарів у 50+ магазинах світу, "
            "перевірка кожні 12 год, цільова ціна та CSV-аналітика на {days} днів."
        ),
        "payment_success": (
            "🎉 <b>Оплата успішна! PRO активовано!</b>\n\n"
            "• Діє до: <b>{until}</b>\n"
            "• Ліміт: <b>до {pro_max} товарів</b>\n"
            "• Перевірка: <b>кожні 12 год</b>"
        ),
        "lang_switched": "✅ Мову змінено на <b>Українську 🇺🇦</b>",
        "list_cleared_msg": "🧹 Список очищено (видалено: <b>{count}</b>).",
        "pro_activated_toast": "🎉 Тариф PRO активовано!",
        "list_empty_toast": "Список порожній.",
        "refreshing_prices_toast": "🔄 Оновлюю ціни...",
        "checking_count_msg": "🔄 <b>Перевіряю ціни ({count} шт.)...</b>",
        "updated_count_msg": "✅ Оновлено: <b>{ok}/{total}</b> товарів.",
        "item_deleted_toast": "🗑 Товар видалено.",
        "item_deleted_msg": "🗑 <b>Товар видалено з відстеження.</b>",
        "already_deleted_toast": "Вже видалено.",
        "pro_only_target_alert": "🔒 Цільова ціна доступна у тарифі PRO!",
        "target_reset_toast": "🧹 Ціль скинуто.",
        "target_reset_msg": "🧹 <b>Цільову ціну скинуто.</b> Сповіщення надходитимуть при будь-якому зниженні ціни.",
        "target_prompt_title": (
            "🎯 <b>БАЖАНА ЦІНА (TARGET PRICE)</b>\n"
            "──────────────────────────────────\n"
            "💰 Поточна ціна: <code>{current}</code>\n"
            "Оберіть швидку знижку кнопкою або напишіть суму числом у чат:"
        ),
        "action_cancelled_toast": "Скасовано.",
        "action_cancelled_msg": "✖️ Введення цільової ціни скасовано.",
        "building_chart_toast": "📈 Будую графік за 30 днів...",
        "chart_caption": (
            "📈 <b>Динаміка ціни за 30 днів</b>\n"
            "{mp_label} • <b>{title}</b>\n\n"
            "💰 Зараз: <b>{price}</b>\n"
            "📉 Мін: <b>{min_p}</b>  •  📊 Сер: <b>{avg_p}</b>  •  📈 Макс: <b>{max_p}</b>"
        ),
        "refreshing_one_toast": "🔄 Перевіряю ціну в магазині...",
        "price_unchanged_toast": "✅ Актуально: {price} (без змін)",
        "price_changed_toast": "🔔 Нова ціна: {old} ➔ {new}!",
        "refresh_failed_msg": "⚠️ Не вдалося оновити ціну зараз.",
        "input_placeholder": "🔗 Вставте посилання на товар...",

        # Додаткові ключі інлайн-кнопок, графіків та хендлерів
        "ibtn_target_price": "🎯 Цільова ціна",
        "ibtn_refresh": "🔄 Оновити",
        "ibtn_delete": "🗑 Видалити",
        "ibtn_buy_on": "🛒 Відкрити в {store} ↗",
        "ibtn_buy_discount": "🔥 Купити в {store} ↗",
        "ibtn_pay_stars_30": "💫 PRO на 30 днів — {stars} ⭐️",
        "ibtn_pay_stars_90": "🔥 PRO на 90 днів (-20%) — {stars} ⭐️",
        "ibtn_try_pro_free": "🧪 Тест PRO",
        "ibtn_share_ref": "📤 Поділитися посиланням",
        "ibtn_chart_short": "📈 30д",
        "ibtn_target_short": "🎯 Ціль",
        "ibtn_cart_report": "📊 Аналітика",
        "start_welcome": (
            "👋 <b>Вітаємо у Global Price Tracker Bot!</b>\n"
            "📋 Тариф: <b>{tier}</b> (до <b>{limit}</b> товарів)"
        ),
        "how_it_works": (
            "🌍 <b>МОНІТОРИНГ ЦІН 24/7 (УКРАЇНА + СВІТ)</b>\n"
            "──────────────────────────────────\n"
            "• 🕒 <b>Історія за 30 днів</b> одразу при додаванні\n"
            "• 💎 <b>Deal Score (0–100)</b> — перевірка реальності знижки\n"
            "• 🔔 <b>Миттєві сповіщення</b> при падінні ціни\n"
            "──────────────────────────────────\n"
            "👇 <b>Просто надішліть посилання на товар у цей чат!</b>"
        ),
        "marketplaces_info": (
            "🏬 <b>ПІДТРИМУВАНІ МАГАЗИНИ (УКРАЇНА + СВІТ)</b>\n"
            "──────────────────────────────────\n"
            "🇺🇦 <b>Україна:</b>\n"
            "Rozetka · Prom.ua · Епіцентр · Comfy · Алло · Фокстрот · MOYO · Цитрус · MAKEUP · EVA · Kasta · Maudau · OLX\n\n"
            "🌎 <b>США, Європа та Азія:</b>\n"
            "Amazon · eBay · AliExpress · Temu · SHEIN · Allegro · Ceneo · x-kom · MediaMarkt · Walmart · BestBuy · Steam\n\n"
            "🌐 <b>+ Універсальний парсер:</b>\n"
            "Працює з <b>95%+ інтернет-магазинів світу</b> (Shopify, WooCommerce, OpenGraph) у будь-якій валюті (₴, $, €, zł, £)."
        ),
        "alert_price_drop": "📉 Зниження ціни в магазині!",
        "loading_product": "🔍 <b>Аналізую товар та ціни за 30 днів...</b> ⏳",
        "invalid_url": (
            "❌ <b>Некоректне посилання</b>\n"
            "Надішліть пряме посилання (<code>https://...</code>) на сторінку товару."
        ),
        "limit_free_reached": (
            "🔒 <b>Ліміт безкоштовного тарифу ({free_max} товарів)</b>\n\n"
            "Активуйте <b>PRO</b> (до <b>{pro_max} товарів</b>, перевірка кожні 12 год та цільова ціна) "
            "або запросіть друга безкоштовно!"
        ),
        "limit_pro_reached": "🔒 <b>Досягнуто ліміту тарифу PRO ({pro_max} товарів).</b>",
        "err_404": "🔎 <b>Сторінку товару не знайдено (404).</b>",
        "status_tracking_on": "✅ <b>Товар у відстеженні!</b>",
        "status_tracking_updated": "ℹ️ <b>Дані та графік оновлено!</b>",
        "target_set_ok": "🎯 <b>Ціль збережено: {price}</b>\nСповістимо, щойно ціна впаде до цього рівня!",
        "cancelled": "✖️ Скасовано.",
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
            "📭 <b>YOUR TRACKING LIST IS EMPTY</b>\n"
            "──────────────────────────────────\n"
            "Send a product link from any store — and I will build a <b>30-day price chart</b> and start monitoring!"
        ),
        "my_products_header": (
            "📦 <b>YOUR TRACKED ITEMS ({used}/{max_limit})</b>\n"
            "──────────────────────────────────\n"
            "{rows}\n"
            "──────────────────────────────────\n"
            "👆 <i>Tap any product below to open its card, 30d chart, or set a target price:</i>"
        ),
        "empty_analytics": (
            "📊 <b>PORTFOLIO ANALYTICS (30 DAYS)</b>\n"
            "──────────────────────────────────\n"
            "Add your first product via link to see your 30-day savings statistics!"
        ),
        "analytics_item_row": (
            "• {mp_label} <b>{title}</b>\n"
            "  💰 <code>{cur}</code> (low: <code>{mn}</code> · high: <code>{mx}</code>) {save_badge}"
        ),
        "analytics_header": (
            "📊 <b>PORTFOLIO ANALYTICS (30 DAYS)</b>\n"
            "──────────────────────────────────\n"
            "📦 Items: <code>{count}</code> · 💎 Savings: <code>{savings}</code>\n"
            "──────────────────────────────────\n"
            "{items}"
        ),
        "ref_welcome_applied": (
            "🎁 <b>Referral Bonus Activated!</b>\n"
            "You received <b>+3 days of PRO for free</b>!"
        ),
        "ref_reward_notify": (
            "🎉 <b>New friend joined via your link!</b>\n"
            "You earned <b>+7 days of PRO</b> for free!"
        ),
        "choose_language": (
            "🌐 <b>INTERFACE LANGUAGE</b>\n"
            "──────────────────────────────────\n"
            "Select your preferred language for menus, charts, and alerts:"
        ),
        "ref_program_text": (
            "🎁 <b>FREE PRO FOR INVITING FRIENDS</b>\n"
            "──────────────────────────────────\n"
            "• You get: <b>+7 days of PRO</b> per friend\n"
            "• Friend gets: <b>+3 days of PRO</b> on start\n\n"
            "👥 Friends invited: <code>{count}</code>\n"
            "🔗 <b>Your invite link:</b>\n"
            "<code>{link}</code>"
        ),
        "export_empty": "📭 No tracked products to export yet.",
        "export_ready": (
            "📥 <b>Your CSV Report is ready!</b>\n"
            "Compatible with Excel & Google Sheets."
        ),
        "admin_stats": (
            "🛡 <b>SAAS ADMIN DASHBOARD</b>\n"
            "──────────────────────────────────\n"
            "👥 Users: <code>{total_users}</code> (PRO: <code>{pro_users}</code>)\n"
            "📦 Products: <code>{total_products}</code> | Subscriptions: <code>{total_subs}</code>\n"
            "📈 Price data points: <code>{total_points}</code>\n\n"
            "🏬 <b>Top Stores:</b>\n"
            "{mp_lines}"
        ),
        "pro_active_until": "✅ Active until <b>{date}</b>",
        "pro_offer_title": "⭐️ <b>PRICE TRACKER PRO</b>",
        "pro_offer_body": (
            "Status: {status}\n"
            "──────────────────────────────────\n"
            "💎 <b>PRO Benefits:</b>\n"
            "• 📦 Up to <b>{pro_max} products</b> (instead of {free_max})\n"
            "• ⚡️ <b>Price checks every 12h</b> (instead of 24h)\n"
            "• 🎯 <b>Custom Target Price</b> alerts\n"
            "• 📊 <b>Deal Score</b> & CSV export\n"
            "──────────────────────────────────\n"
            "💳 From <b>{stars} ⭐️ Telegram Stars</b> (or free via referral link)"
        ),
        "test_drop_need_item": "Add at least one product first to test a price drop alert!",
        "alert_body": (
            "🔥 <b>PRICE DROP! {reason}</b>\n\n"
            "🏬 {mp_label}\n"
            "📦 <b>{title}</b>\n\n"
            "📉 Was: <s>{old_p}</s>  ➔  🟢 <b>Now: {new_p}</b>\n"
            "💰 <b>Save: -{diff} (-{pct:.1f}%)</b>"
        ),
        "pro_activated_msg": (
            "🎉 <b>PRO Plan Activated!</b>\n\n"
            "📅 Valid until: <b>{until}</b>\n"
            "📦 Limit: <b>{max_p} products</b> • Checks: <b>every 12h</b>"
        ),

        # Product card & 30d stats
        "avail_yes": "🟢 In Stock",
        "avail_no": "🔴 Out of Stock",
        "target_line": "🎯 Target Price: <b>{target}</b>\n",
        "verdict_best": "🔥 30-day low — best time to buy!",
        "verdict_good": "✅ Below 30-day average",
        "verdict_wait": "⏳ Above average — wait for a discount",
        "stats_30d_box": (
            "📊 <b>30-Day Analytics:</b>\n"
            "├ 30d Ago: <b>{start}</b>  |  Avg: <b>{avg}</b>\n"
            "├ Low: <b>{min}</b>  |  High: <b>{max}</b>\n"
            "├ Trend: {trend}\n"
            "└ Deal Score: <b>{score}/100</b> — <i>{verdict}</i>"
        ),
        "card_Scanning": "🔍 <b>Analyzing product & 30-day price history...</b> ⏳",
        "err_invalid_url": (
            "❌ <b>Invalid Link</b>\n"
            "Please send a public product URL (<code>https://...</code>)."
        ),
        "err_out_of_stock": (
            "📦 <b>Out of Stock</b>\n"
            "The store does not display an active price right now."
        ),
        "err_not_found": "🔎 <b>Product page not found (404).</b>",
        "err_rate_limit": "⏳ <b>Store rate limit reached.</b> Try again in 10 seconds.",
        "err_parse": (
            "⚠️ <b>Could not read price from this page</b>\n"
            "Make sure the link points directly to a single product page."
        ),
        "err_scraper": "❌ Error: {err}",
        "limit_reached_title": "🔒 <b>Free Limit Reached ({used}/{limit})</b>",
        "limit_reached_body": (
            "Upgrade to <b>PRO</b> (up to <b>{pro_max} products</b>, checks every 12h & target price) "
            "or invite a friend for free!"
        ),
        "status_added": "✅ <b>Added to tracking</b> ({used}/{max_limit})",
        "status_updated": "ℹ️ <b>Price & chart updated</b> ({used}/{max_limit})",
        "enter_valid_number": "❌ Enter a valid number (e.g. <code>199.99</code>).",
        "price_range_error": "❌ Price must be between 1 and 10,000,000.",
        "product_not_found_msg": "❌ Product not found.",
        "product_not_in_list": "❌ Product is no longer in your list.",
        "target_saved_msg": (
            "🎯 <b>Target Saved: {target}</b>\n\n"
            "📦 <b>{title}</b>\n"
            "💰 Current: <b>{current}</b>\n\n"
            "🔔 <i>We'll alert you as soon as the price drops to {target} or lower!</i>"
        ),

        # Payments & Callbacks
        "invoice_title": "Price Tracker PRO ({days} Days)",
        "invoice_desc": (
            "Up to {pro_max} products, checks every 12h, Target Price alerts & CSV export ({days} days)."
        ),
        "payment_success": (
            "🎉 <b>Payment Successful! PRO Activated!</b>\n\n"
            "📅 Valid until: <b>{until}</b>\n"
            "📦 Limit: <b>up to {pro_max} products</b> (checks every 12h)"
        ),
        "lang_switched": "✅ Language switched to <b>English 🇬🇧</b>!",
        "list_cleared_msg": "🧹 Tracking list cleared (<b>{count}</b> items).",
        "pro_activated_toast": "🎉 PRO Plan activated!",
        "list_empty_toast": "Your list is empty.",
        "refreshing_prices_toast": "🔄 Refreshing prices...",
        "checking_count_msg": "🔄 <b>Checking prices for {count} items...</b>",
        "updated_count_msg": "✅ Updated prices for <b>{ok}/{total}</b> items.",
        "item_deleted_toast": "🗑 Removed from tracking.",
        "item_deleted_msg": "🗑 <b>Product removed from tracking.</b>",
        "already_deleted_toast": "Already removed.",
        "pro_only_target_alert": (
            "🔒 Target Price is a PRO feature!\n\n"
            "Open '⭐️ PRO' or invite a friend for free access."
        ),
        "target_reset_toast": "🧹 Target cleared.",
        "target_reset_msg": "🧹 <b>Target price cleared.</b>",
        "target_prompt_title": (
            "🎯 <b>Target Price</b>\n\n"
            "📦 <b>{title}</b>\n"
            "💰 Current: <b>{current}</b>\n\n"
            "👇 Tap a quick discount button below or type your price (e.g. <code>{suggested}</code>):"
        ),
        "action_cancelled_toast": "Cancelled.",
        "action_cancelled_msg": "✖️ Cancelled.",
        "building_chart_toast": "📈 Building 30d chart...",
        "chart_caption": (
            "📈 <b>30-Day Price History</b>\n"
            "{mp_label} • <b>{title}</b>\n"
            "💰 Current: <b>{price}</b>"
        ),
        "refreshing_one_toast": "🔄 Checking store price...",
        "price_unchanged_toast": "✅ Up to date: {price}",
        "price_changed_toast": "🔔 New price: {old} ➔ {new}!",
        "refresh_failed_msg": "⚠️ Could not refresh price right now.",
        "input_placeholder": "🔗 Paste a product link...",

        # Additional keys for inline buttons, charts, and handlers
        "ibtn_target_price": "🎯 Target Price",
        "ibtn_refresh": "🔄 Refresh",
        "ibtn_delete": "🗑 Delete",
        "ibtn_buy_on": "🛒 Open on {store} ↗",
        "ibtn_buy_discount": "🔥 Buy on {store} ↗",
        "ibtn_pay_stars_30": "💫 PRO 30 Days — {stars} ⭐️",
        "ibtn_pay_stars_90": "🔥 PRO 90 Days (-20%) — {stars} ⭐️",
        "ibtn_try_pro_free": "🧪 Try PRO",
        "ibtn_share_ref": "📤 Share Invite Link",
        "ibtn_chart_short": "📈 30d",
        "ibtn_target_short": "🎯 Goal",
        "ibtn_cart_report": "📊 Analytics",
        "start_welcome": (
            "👋 <b>Welcome to Global Price Tracker Bot!</b>\n"
            "📋 Plan: <b>{tier}</b> (up to <b>{limit}</b> items)"
        ),
        "how_it_works": (
            "🌍 <b>24/7 Price Monitoring (Global + Local)</b>\n\n"
            "• 🕒 <b>30-Day Price History</b> right when you add a link\n"
            "• 💎 <b>Deal Score (0–100)</b> — verify genuine discounts\n"
            "• 🔔 <b>Instant Alerts</b> on every price drop\n\n"
            "👇 <b>Paste and send any product link into this chat!</b>"
        ),
        "marketplaces_info": (
            "🏬 <b>SUPPORTED MARKETPLACES (GLOBAL + UA)</b>\n"
            "──────────────────────────────────\n"
            "🌎 <b>Global, US, EU & Asia:</b>\n"
            "Amazon · eBay · AliExpress · Temu · SHEIN · Walmart · BestBuy · Allegro · Ceneo · x-kom · MediaMarkt · Steam\n\n"
            "🇺🇦 <b>Ukraine:</b>\n"
            "Rozetka · Prom.ua · Epicentr · Comfy · Allo · Foxtrot · MOYO · Citrus · MAKEUP · EVA · Kasta · Maudau · OLX\n\n"
            "🌐 <b>+ Universal Smart Parser:</b>\n"
            "Works with <b>95%+ of online stores worldwide</b> (Shopify, WooCommerce, OpenGraph) in any currency ($, €, £, zł, ₴)."
        ),
        "alert_price_drop": "📉 Store price dropped!",
        "loading_product": "🔍 <b>Analyzing product & 30-day prices...</b> ⏳",
        "invalid_url": (
            "❌ <b>Invalid Link</b>\n"
            "Please send a direct link (<code>https://...</code>) to a product page."
        ),
        "limit_free_reached": (
            "🔒 <b>Free Plan Limit Reached ({free_max} items)</b>\n\n"
            "Upgrade to <b>PRO</b> (up to <b>{pro_max} items</b>, checks every 12h & target price) "
            "or invite a friend for free!"
        ),
        "limit_pro_reached": "🔒 <b>Maximum PRO plan limit reached ({pro_max} items).</b>",
        "err_404": "🔎 <b>Product page not found (404).</b>",
        "status_tracking_on": "✅ <b>Added to tracking!</b>",
        "status_tracking_updated": "ℹ️ <b>Price & chart updated!</b>",
        "target_set_ok": "🎯 <b>Target saved: {price}</b>\nWe'll notify you as soon as the price drops to this level!",
        "cancelled": "✖️ Cancelled.",
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
            "📭 <b>СПИСОК ОТСЛЕЖИВАНИЯ ПУСТ</b>\n"
            "──────────────────────────────────\n"
            "Отправьте ссылку на товар из любого магазина — я построю <b>график цен за 30 дней</b> и начну мониторинг!"
        ),
        "my_products_header": (
            "📦 <b>МОИ ТОВАРЫ В ОТСЛЕЖИВАНИИ ({used}/{max_limit})</b>\n"
            "──────────────────────────────────\n"
            "{rows}\n"
            "──────────────────────────────────\n"
            "👆 <i>Нажмите на товар ниже, чтобы открыть карточку, график или задать цель:</i>"
        ),
        "empty_analytics": (
            "📊 <b>АНАЛИТИКА КОРЗИНЫ (30 ДНЕЙ)</b>\n"
            "──────────────────────────────────\n"
            "Добавьте первый товар по ссылке, чтобы видеть статистику экономии за 30 дней!"
        ),
        "analytics_item_row": (
            "• {mp_label} <b>{title}</b>\n"
            "  💰 <code>{cur}</code> (мин: <code>{mn}</code> · макс: <code>{mx}</code>) {save_badge}"
        ),
        "analytics_header": (
            "📊 <b>АНАЛИТИКА КОРЗИНЫ (30 ДНЕЙ)</b>\n"
            "──────────────────────────────────\n"
            "📦 Товаров: <code>{count}</code> · 💎 Экономия: <code>{savings}</code>\n"
            "──────────────────────────────────\n"
            "{items}"
        ),
        "ref_welcome_applied": (
            "🎁 <b>Бонус активирован!</b>\n"
            "Вы получили <b>+3 дня PRO бесплатно</b> по приглашению друга!"
        ),
        "ref_reward_notify": (
            "🎉 <b>Новый друг по вашей ссылке!</b>\n"
            "Вам начислено <b>+7 дней PRO</b> бесплатно!"
        ),
        "choose_language": (
            "🌐 <b>ЯЗЫК ИНТЕРФЕЙСА · LANGUAGE</b>\n"
            "──────────────────────────────────\n"
            "Выберите удобный язык для меню, графиков и уведомлений:"
        ),
        "ref_program_text": (
            "🎁 <b>БЕСПЛАТНЫЙ PRO ЗА ДРУЗЕЙ</b>\n"
            "──────────────────────────────────\n"
            "• Вам: <b>+7 дней PRO</b> за каждого друга\n"
            "• Другу: <b>+3 дня PRO</b> сразу на старте\n\n"
            "👥 Приглашено друзей: <code>{count}</code>\n"
            "🔗 <b>Ваша ссылка:</b>\n"
            "<code>{link}</code>"
        ),
        "export_empty": "📭 Нет товаров для экспорта.",
        "export_ready": (
            "📥 <b>Ваш CSV-отчёт готов!</b>\n"
            "Таблица открывается в Excel и Google Sheets."
        ),
        "admin_stats": (
            "🛡 <b>SAAS ADMIN DASHBOARD</b>\n"
            "──────────────────────────────────\n"
            "👥 Пользователей: <code>{total_users}</code> (PRO: <code>{pro_users}</code>)\n"
            "📦 Товаров: <code>{total_products}</code> | Подписок: <code>{total_subs}</code>\n"
            "📈 Точек истории цен: <code>{total_points}</code>\n\n"
            "🏬 <b>Топ магазинов:</b>\n"
            "{mp_lines}"
        ),
        "pro_active_until": "✅ Активен до <b>{date}</b>",
        "pro_offer_title": "⭐️ <b>ПОДПИСКА PRICE TRACKER PRO</b>",
        "pro_offer_body": (
            "Статус: {status}\n"
            "──────────────────────────────────\n"
            "💎 <b>Преимущества PRO:</b>\n"
            "• 📦 До <b>{pro_max} товаров</b> (вместо {free_max})\n"
            "• ⚡️ <b>Проверка каждые 12 ч</b> (вместо раз в сутки)\n"
            "• 🎯 <b>Целевая цена</b> для каждого товара\n"
            "• 📊 <b>Deal Score</b> и экспорт в CSV/Excel\n"
            "──────────────────────────────────\n"
            "💳 От <b>{stars} ⭐️ Telegram Stars</b> (или бесплатно за друзей)"
        ),
        "test_drop_need_item": "Сначала добавьте хотя бы один товар, чтобы протестировать уведомление!",
        "alert_body": (
            "🔥 <b>ЦЕНА УПАЛА! {reason}</b>\n\n"
            "🏬 {mp_label}\n"
            "📦 <b>{title}</b>\n\n"
            "📉 Было: <s>{old_p}</s>  ➔  🟢 <b>Стало: {new_p}</b>\n"
            "💰 <b>Экономия: -{diff} (-{pct:.1f}%)</b>"
        ),
        "pro_activated_msg": (
            "🎉 <b>Тариф PRO активирован!</b>\n\n"
            "📅 Действует до: <b>{until}</b>\n"
            "📦 Лимит: <b>до {max_p} товаров</b> • Проверка: <b>каждые 12 ч</b>"
        ),

        # Карточка товара и аналитика 30 дней
        "avail_yes": "🟢 В наличии",
        "avail_no": "🔴 Нет в наличии",
        "target_line": "🎯 Целевая цена: <b>{target}</b>\n",
        "verdict_best": "🔥 Минимум за 30 дней — выгодно покупать!",
        "verdict_good": "✅ Цена ниже средней за месяц",
        "verdict_wait": "⏳ Выше средней — стоит подождать скидку",
        "stats_30d_box": (
            "📊 <b>Аналитика за 30 дней:</b>\n"
            "├ 30 дн. назад: <b>{start}</b>  |  Средняя: <b>{avg}</b>\n"
            "├ Мин: <b>{min}</b>  |  Макс: <b>{max}</b>\n"
            "├ Динамика: {trend}\n"
            "└ Deal Score: <b>{score}/100</b> — <i>{verdict}</i>"
        ),
        "card_Scanning": "🔍 <b>Анализирую товар и историю цен за 30 дней...</b> ⏳",
        "err_invalid_url": (
            "❌ <b>Некорректная ссылка</b>\n"
            "Отправьте прямую ссылку (<code>https://...</code>) на страницу товара."
        ),
        "err_out_of_stock": (
            "📦 <b>Товар отсутствует в продаже</b>\n"
            "Магазин сейчас не указывает актуальную цену."
        ),
        "err_not_found": "🔎 <b>Страница товара не найдена (404).</b>",
        "err_rate_limit": "⏳ <b>Магазин временно ограничил запросы.</b> Повторите через 10 сек.",
        "err_parse": (
            "⚠️ <b>Не удалось считать цену</b>\n"
            "Убедитесь, что ссылка ведёт на конкретный товар."
        ),
        "err_scraper": "❌ Ошибка: {err}",
        "limit_reached_title": "🔒 <b>Лимит бесплатного тарифа ({used}/{limit})</b>",
        "limit_reached_body": (
            "Активируйте <b>PRO</b> (до <b>{pro_max} товаров</b>, проверка каждые 12 ч и целевая цена) "
            "или пригласите друга бесплатно!"
        ),
        "status_added": "✅ <b>Добавлено в мониторинг</b> ({used}/{max_limit})",
        "status_updated": "ℹ️ <b>Данные и график обновлены</b> ({used}/{max_limit})",
        "enter_valid_number": "❌ Введите корректное число (например: <code>4500</code>).",
        "price_range_error": "❌ Цена должна быть от 1 до 10 000 000.",
        "product_not_found_msg": "❌ Товар не найден.",
        "product_not_in_list": "❌ Товар больше не отслеживается.",
        "target_saved_msg": (
            "🎯 <b>Цель сохранена: {target}</b>\n\n"
            "📦 <b>{title}</b>\n"
            "💰 Текущая цена: <b>{current}</b>\n\n"
            "🔔 <i>Сообщим, как только цена опустится до {target} или ниже!</i>"
        ),

        # Платежи и Callback-уведомления
        "invoice_title": "Price Tracker PRO ({days} дн.)",
        "invoice_desc": (
            "До {pro_max} товаров, проверка каждые 12 ч, целевая цена и CSV-аналитика ({days} дн.)."
        ),
        "payment_success": (
            "🎉 <b>Оплата успешна! Тариф PRO активирован!</b>\n\n"
            "📅 Действует до: <b>{until}</b>\n"
            "📦 Лимит: <b>до {pro_max} товаров</b> (проверка каждые 12 ч)"
        ),
        "lang_switched": "✅ Язык изменён на <b>Русский 🇷🇺</b>!",
        "list_cleared_msg": "🧹 Список очищен (<b>{count}</b> товаров).",
        "pro_activated_toast": "🎉 Тариф PRO активирован!",
        "list_empty_toast": "Ваш список пуст.",
        "refreshing_prices_toast": "🔄 Обновляю цены...",
        "checking_count_msg": "🔄 <b>Проверяю цены для {count} товаров...</b>",
        "updated_count_msg": "✅ Обновлены цены для <b>{ok}/{total}</b> товаров.",
        "item_deleted_toast": "🗑 Удалено из отслеживания.",
        "item_deleted_msg": "🗑 <b>Отслеживание товара прекращено.</b>",
        "already_deleted_toast": "Уже удалено.",
        "pro_only_target_alert": (
            "🔒 Целевая цена доступна в тарифе PRO!\n\n"
            "Откройте «⭐️ PRO» или пригласите друга бесплатно."
        ),
        "target_reset_toast": "🧹 Цель сброшена.",
        "target_reset_msg": "🧹 <b>Целевая цена сброшена.</b>",
        "target_prompt_title": (
            "🎯 <b>Целевая цена</b>\n\n"
            "📦 <b>{title}</b>\n"
            "💰 Сейчас: <b>{current}</b>\n\n"
            "👇 Выберите быструю скидку кнопкой ниже или введите сумму в чат (например: <code>{suggested}</code>):"
        ),
        "action_cancelled_toast": "Отменено.",
        "action_cancelled_msg": "✖️ Отменено.",
        "building_chart_toast": "📈 Строю график за 30д...",
        "chart_caption": (
            "📈 <b>Динамика цены за 30 дней</b>\n"
            "{mp_label} • <b>{title}</b>\n"
            "💰 Сейчас: <b>{price}</b>"
        ),
        "refreshing_one_toast": "🔄 Проверяю цену в магазине...",
        "price_unchanged_toast": "✅ Цена актуальна: {price}",
        "price_changed_toast": "🔔 Новая цена: {old} ➔ {new}!",
        "refresh_failed_msg": "⚠️ Не удалось обновить цену сейчас.",
        "input_placeholder": "🔗 Вставьте ссылку на товар...",

        # Дополнительные ключи инлайн-кнопок, графиков и хендлеров
        "ibtn_target_price": "🎯 Целевая цена",
        "ibtn_refresh": "🔄 Обновить",
        "ibtn_delete": "🗑 Удалить",
        "ibtn_buy_on": "🛒 Открыть в {store} ↗",
        "ibtn_buy_discount": "🔥 Купить в {store} ↗",
        "ibtn_pay_stars_30": "💫 PRO на 30 дней — {stars} ⭐️",
        "ibtn_pay_stars_90": "🔥 PRO на 90 дней (-20%) — {stars} ⭐️",
        "ibtn_try_pro_free": "🧪 Тест PRO",
        "ibtn_share_ref": "📤 Поделиться ссылкой",
        "ibtn_chart_short": "📈 30д",
        "ibtn_target_short": "🎯 Цель",
        "ibtn_cart_report": "📊 Аналитика",
        "start_welcome": (
            "👋 <b>Добро пожаловать в Global Price Tracker Bot!</b>\n"
            "📋 Тариф: <b>{tier}</b> (до <b>{limit}</b> товаров)"
        ),
        "how_it_works": (
            "🌍 <b>Мониторинг цен 24/7 (Украина + Мир)</b>\n\n"
            "• 🕒 <b>История за 30 дней</b> сразу при добавлении\n"
            "• 💎 <b>Deal Score (0–100)</b> — проверка реальности скидки\n"
            "• 🔔 <b>Мгновенные уведомления</b> при падении цены\n\n"
            "👇 <b>Просто отправьте ссылку на товар в этот чат!</b>"
        ),
        "marketplaces_info": (
            "🏬 <b>ПОДДЕРЖИВАЕМЫЕ МАГАЗИНЫ (УКРАИНА + МИР)</b>\n"
            "──────────────────────────────────\n"
            "🇺🇦 <b>Украина:</b>\n"
            "Rozetka · Prom.ua · Эпицентр · Comfy · Алло · Фокстрот · MOYO · Цитрус · MAKEUP · EVA · Kasta · Maudau · OLX\n\n"
            "🌎 <b>США, Европа и Азия:</b>\n"
            "Amazon · eBay · AliExpress · Temu · SHEIN · Allegro · Ceneo · x-kom · MediaMarkt · Walmart · BestBuy · Steam\n\n"
            "🌐 <b>+ Универсальный парсер:</b>\n"
            "Работает с <b>95%+ интернет-магазинов мира</b> (Shopify, WooCommerce, OpenGraph) в любой валюте (₴, $, €, zł, £)."
        ),
        "alert_price_drop": "📉 Снижение цены в магазине!",
        "loading_product": "🔍 <b>Анализирую товар и цены за 30 дней...</b> ⏳",
        "invalid_url": (
            "❌ <b>Некорректная ссылка</b>\n"
            "Отправьте прямую ссылку (<code>https://...</code>) на страницу товара."
        ),
        "limit_free_reached": (
            "🔒 <b>Лимит бесплатного тарифа ({free_max} товаров)</b>\n\n"
            "Активируйте <b>PRO</b> (до <b>{pro_max} товаров</b>, проверка каждые 12 ч и целевая цена) "
            "или пригласите друга бесплатно!"
        ),
        "limit_pro_reached": "🔒 <b>Достигнут лимит тарифа PRO ({pro_max} товаров).</b>",
        "err_404": "🔎 <b>Страница товара не найдена (404).</b>",
        "status_tracking_on": "✅ <b>Товар в отслеживании!</b>",
        "status_tracking_updated": "ℹ️ <b>Данные и график обновлены!</b>",
        "target_set_ok": "🎯 <b>Цель сохранена: {price}</b>\nСообщим, как только цена опустится до этого уровня!",
        "cancelled": "✖️ Отменено.",
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
