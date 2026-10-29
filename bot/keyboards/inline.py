from __future__ import annotations

from decimal import Decimal
import math
from typing import Any, Sequence
from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.i18n import fmt_money, normalize_lang, t
from bot.keyboards.callbacks import (
    LanguageCallback,
    OnboardingCallback,
    ProductActionCallback,
    ProPaymentCallback,
    SearchCallback,
    SettingsCallback,
    WishlistCallback,
)
from config import settings
from database.models import User, UserProduct
from services.affiliate import build_affiliate_url
from services.scraper.normalizer import format_marketplace_name

ITEMS_PER_PAGE = 6

WISHLIST_CATEGORIES: dict[str, dict[str, str]] = {
    "pc": {"uk": "💻 Збірка ПК", "en": "💻 PC Build", "ru": "💻 Сборка ПК"},
    "gadgets": {"uk": "📱 Гаджети", "en": "📱 Gadgets", "ru": "📱 Гаджеты"},
    "gifts": {"uk": "🎁 Подарунки", "en": "🎁 Gifts", "ru": "🎁 Подарки"},
    "home": {"uk": "🏠 Для дому", "en": "🏠 Home", "ru": "🏠 Для дома"},
    "hunt": {"uk": "🔥 Полювання", "en": "🔥 Deal Hunt", "ru": "🔥 Охота"},
}


def format_category_badge(tag: str | None, lang: str = "uk") -> str:
    """Повертає локалізовану назву категорії вішліста (добірки) або порожній рядок."""
    if not tag:
        return ""
    lang = normalize_lang(lang)
    cat = WISHLIST_CATEGORIES.get(tag)
    if not cat:
        return ""
    return cat.get(lang, cat["uk"])


def _short_price(val: Decimal | float, currency: str = "UAH") -> str:
    return fmt_money(val, currency)


def _build_viral_share_query(
    *,
    title: str | None,
    price_str: str | None,
    old_price_str: str | None = None,
    store_name: str,
    user_id: int,
    lang: str = "uk",
) -> str:
    """Формує віральний текст для switch_inline_query з реферальним посиланням користувача."""
    ref_url = f"https://t.me/clipsnag_bot?start=ref_{user_id}"
    short_t = (title or store_name)[:48]
    p_part = f" — {price_str}" if price_str else ""
    old_part = ""
    if old_price_str and old_price_str != price_str:
        if lang == "en":
            old_part = f" (was {old_price_str})"
        elif lang == "ru":
            old_part = f" (было {old_price_str})"
        else:
            old_part = f" (було {old_price_str})"
    if lang == "en":
        return f"🔥 Great price on {store_name}: {short_t}{p_part}{old_part}! Track discounts 24/7: {ref_url}"
    if lang == "ru":
        return f"🔥 Выгодная цена в {store_name}: {short_t}{p_part}{old_part}! Мониторинг скидок 24/7: {ref_url}"
    return f"🔥 Вигідна ціна в {store_name}: {short_t}{p_part}{old_part}! Відстежуй знижки 24/7: {ref_url}"


def build_start_keyboard(lang: str = "uk", items_count: int = 0) -> InlineKeyboardMarkup:
    """
    Прогресивна інлайн-клавіатура головного дашборду (/start):
    - Для нового користувача (items_count == 0) показує лише 4 найважливіші кнопки (2 ряди),
      щоб не перевантажувати інтерфейс.
    - Коли з'являються товари (items_count > 0), автоматично відкриває кнопки кошика та вигоди.
    """
    lang = normalize_lang(lang)
    if lang == "en":
        my_items = f"📦 My Items ({items_count})" if items_count > 0 else "📦 My Items"
        search_btn = "🔍 Search by Name"
        hot_label = "🔥 Hot Deals"
        stats_label = "📊 My Savings"
        pro_label = "⭐️ PRO Plan"
        cfg_label = "⚙️ Settings"
    elif lang == "ru":
        my_items = f"📦 Мои товары ({items_count})" if items_count > 0 else "📦 Мои товары"
        search_btn = "🔍 Найти по названию"
        hot_label = "🔥 Топ скидок"
        stats_label = "📊 Моя выгода"
        pro_label = "⭐️ PRO тариф"
        cfg_label = "⚙️ Настройки"
    else:
        my_items = f"📦 Мої товари ({items_count})" if items_count > 0 else "📦 Мої товари"
        search_btn = "🔍 Знайти за назвою"
        hot_label = "🔥 Топ знижок"
        stats_label = "📊 Моя вигода"
        pro_label = "⭐️ PRO тариф"
        cfg_label = "⚙️ Опції бота"

    builder = InlineKeyboardBuilder()
    if items_count <= 0:
        # Мінімалістичний режим для нових користувачів (4 кнопки = 0 візуального шуму)
        builder.row(
            InlineKeyboardButton(
                text=search_btn,
                callback_data=OnboardingCallback(action="search_prompt").pack(),
            ),
            InlineKeyboardButton(
                text=hot_label,
                callback_data=SettingsCallback(action="top_drops").pack(),
            ),
        )
        builder.row(
            InlineKeyboardButton(
                text=pro_label,
                callback_data=OnboardingCallback(action="pro_menu").pack(),
            ),
            InlineKeyboardButton(
                text=cfg_label,
                callback_data=OnboardingCallback(action="settings").pack(),
            ),
        )
        return builder.as_markup()

    # Розширений режим для активних користувачів із товарами (3 охайні ряди)
    builder.row(
        InlineKeyboardButton(
            text=my_items,
            callback_data=OnboardingCallback(action="my_tracking", page=1).pack(),
        ),
        InlineKeyboardButton(
            text=search_btn,
            callback_data=OnboardingCallback(action="search_prompt").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=hot_label,
            callback_data=SettingsCallback(action="top_drops").pack(),
        ),
        InlineKeyboardButton(
            text=stats_label,
            callback_data=OnboardingCallback(action="stats").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=pro_label,
            callback_data=OnboardingCallback(action="pro_menu").pack(),
        ),
        InlineKeyboardButton(
            text=cfg_label,
            callback_data=OnboardingCallback(action="settings").pack(),
        ),
    )
    return builder.as_markup()


def build_add_guide_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    """Клавіатура інтерактивної підказки «🔍 Як знайти або додати товар»."""
    lang = normalize_lang(lang)
    if lang == "en":
        inline_btn = "💬 Try in Chat (@Bot)"
        stores_btn = "🏬 Stores"
        menu_btn = "🏠 Main Menu"
    elif lang == "ru":
        inline_btn = "💬 Поиск в чате (@Bot)"
        stores_btn = "🏬 Магазины"
        menu_btn = "🏠 В меню"
    else:
        inline_btn = "💬 Пошук у чаті (@Bot)"
        stores_btn = "🏬 Магазини"
        menu_btn = "🏠 У меню"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=inline_btn,
            switch_inline_query_current_chat="AirPods Pro 2",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=stores_btn,
            callback_data=OnboardingCallback(action="marketplaces").pack(),
        ),
        InlineKeyboardButton(
            text=menu_btn,
            callback_data=OnboardingCallback(action="main_menu").pack(),
        ),
    )
    return builder.as_markup()


def build_marketplaces_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    """Клавіатура під списком підтримуваних магазинів."""
    lang = normalize_lang(lang)
    if lang == "en":
        add_btn = "➕ Add Item"
        menu_btn = "🏠 Main Menu"
    elif lang == "ru":
        add_btn = "➕ Добавить"
        menu_btn = "🏠 В меню"
    else:
        add_btn = "➕ Як додати"
        menu_btn = "🏠 У меню"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=add_btn,
            callback_data=OnboardingCallback(action="add_guide").pack(),
        ),
        InlineKeyboardButton(
            text=menu_btn,
            callback_data=OnboardingCallback(action="main_menu").pack(),
        ),
    )
    return builder.as_markup()


def build_settings_keyboard(user: User, lang: str = "uk") -> InlineKeyboardMarkup:
    """
    Інтерактивні кнопки-перемикачі (Toggle Buttons) для меню «⚙️ Налаштування»:
    Змінюють свій стан прямо на кнопці без переходу в додаткові підменю.
    """
    lang = normalize_lang(lang)
    notify_on = bool(getattr(user, "notifications_enabled", True))
    dnd_on = bool(getattr(user, "dnd_enabled", False))

    if lang == "en":
        notify_text = "🔔 Alerts: ON ✅" if notify_on else "🔕 Alerts: OFF ❌"
        dnd_text = "🌙 Night Mode (23–08): ON ✅" if dnd_on else "🌙 Night Mode: OFF ❌"
        digest_text = "☕️ Digest"
        lang_text = "🌐 Language"
        items_text = "📦 My Items"
        menu_text = "🏠 Main Menu"
    elif lang == "ru":
        notify_text = "🔔 Уведомления: ВКЛ ✅" if notify_on else "🔕 Уведомления: ВЫКЛ ❌"
        dnd_text = "🌙 Ночной (23–08): ВКЛ ✅" if dnd_on else "🌙 Ночной режим: ВЫКЛ ❌"
        digest_text = "☕️ Дайджест"
        lang_text = "🌐 Язык"
        items_text = "📦 Товары"
        menu_text = "🏠 В меню"
    else:
        notify_text = "🔔 Сповіщення: УВІМК ✅" if notify_on else "🔕 Сповіщення: ВИМК ❌"
        dnd_text = "🌙 Нічний (23–08): УВІМК ✅" if dnd_on else "🌙 Нічний режим: ВИМК ❌"
        digest_text = "☕️ Дайджест"
        lang_text = "🌐 Мова"
        items_text = "📦 Товари"
        menu_text = "🏠 У меню"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=notify_text,
            callback_data=SettingsCallback(action="toggle_notify").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=dnd_text,
            callback_data=SettingsCallback(action="toggle_dnd").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=digest_text,
            callback_data=SettingsCallback(action="digest_preview").pack(),
        ),
        InlineKeyboardButton(
            text=lang_text,
            callback_data=OnboardingCallback(action="lang_menu").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=items_text,
            callback_data=OnboardingCallback(action="my_tracking", page=1).pack(),
        ),
        InlineKeyboardButton(
            text=menu_text,
            callback_data=OnboardingCallback(action="main_menu").pack(),
        ),
    )
    return builder.as_markup()


def build_language_keyboard(current_lang: str = "uk") -> InlineKeyboardMarkup:
    """Інлайн-клавіатура вибору мови інтерфейсу (UK / EN / RU) з кнопкою повернення."""
    cur = normalize_lang(current_lang)
    builder = InlineKeyboardBuilder()
    langs = [
        ("uk", "🇺🇦 Українська"),
        ("en", "🇬🇧 English"),
        ("ru", "🇷🇺 Русский"),
    ]
    for code, label in langs:
        mark = " ✅" if code == cur else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{label}{mark}",
                callback_data=LanguageCallback(code=code).pack(),  # type: ignore[arg-type]
            )
        )
    back_cfg = "⚙️ Settings" if cur == "en" else ("⚙️ Настройки" if cur == "ru" else "⚙️ Налаштув.")
    menu_btn = "🏠 Main Menu" if cur == "en" else ("🏠 В меню" if cur == "ru" else "🏠 У меню")
    builder.row(
        InlineKeyboardButton(
            text=back_cfg,
            callback_data=OnboardingCallback(action="settings").pack(),
        ),
        InlineKeyboardButton(
            text=menu_btn,
            callback_data=OnboardingCallback(action="main_menu").pack(),
        ),
    )
    return builder.as_markup()


def build_product_card_keyboard(
    *,
    product_id: int,
    clean_url: str | None = None,
    product_url: str | None = None,
    user_id: int,
    marketplace: str = "rozetka",
    lang: str = "uk",
    show_back: bool = True,
    title: str | None = None,
    price_str: str | None = None,
    new_price_str: str | None = None,
    old_price_str: str | None = None,
    category_tag: str | None = None,
) -> InlineKeyboardMarkup:
    """
    Зрозуміла симетрична сітка кнопок для картки товару:
    Ряд 1 (1 кнопка Full-width CTA): [🛒 Купити в Store · 1 499 ₴ ↗]
    Ряд 2 (2 кнопки Medium):         [📈 Графік 30д] | [🧠 AI-Прогноз]
    Ряд 3 (4 кнопки Quick Target):   [🎯-5%] | [🎯-10%] | [🎯-15%] | [✍️ Ціль]
    Ряд 4 (3 кнопки Compact):        [🔄 Оновити] | [📁 Добірка] | [🗑 Видалити]
    Ряд 5 (2 кнопки Навігація):      [📤 Поділитися] | [⬅️ До списку]
    """
    lang = normalize_lang(lang)
    target_url = clean_url or product_url or "https://rozetka.com.ua"
    affiliate_url = build_affiliate_url(target_url, user_id=user_id)
    store_name = format_marketplace_name(marketplace)
    display_price = price_str or new_price_str
    price_suffix = f" · {display_price}" if display_price else ""

    share_query = _build_viral_share_query(
        title=title,
        price_str=display_price,
        old_price_str=old_price_str,
        store_name=store_name,
        user_id=user_id,
        lang=lang,
    )

    cat_badge = format_category_badge(category_tag, lang)

    if lang == "en":
        cta_label = f"🛒 Open {store_name}{price_suffix} ↗"
        chart_label = "📈 30d Chart"
        ai_label = "🧠 AI Advice"
        custom_lbl = "✍️ Target"
        refresh_label = "🔄 Refresh"
        cat_label = cat_badge or "📁 List"
        del_label = "🗑 Remove"
        share_short = "📤 Share"
        back_label = "⬅️ My Items"
    elif lang == "ru":
        cta_label = f"🛒 В магазин ({store_name}){price_suffix} ↗"
        chart_label = "📈 График 30д"
        ai_label = "🧠 AI-Прогноз"
        custom_lbl = "✍️ Цель"
        refresh_label = "🔄 Обновить"
        cat_label = cat_badge or "📁 Список"
        del_label = "🗑 Удалить"
        share_short = "📤 Поделиться"
        back_label = "⬅️ К списку"
    else:
        cta_label = f"🛒 У магазин ({store_name}){price_suffix} ↗"
        chart_label = "📈 Графік 30д"
        ai_label = "🧠 AI-Прогноз"
        custom_lbl = "✍️ Ціль"
        refresh_label = "🔄 Оновити"
        cat_label = cat_badge or "📁 Добірка"
        del_label = "🗑 Видалити"
        share_short = "📤 Поділитися"
        back_label = "⬅️ До списку"

    builder = InlineKeyboardBuilder()

    # Ряд 1 (1 кнопка Full-width CTA з ціною)
    builder.row(
        InlineKeyboardButton(
            text=cta_label,
            url=affiliate_url,
        )
    )
    # Ряд 2 (2 кнопки однакової ширини: Графік 30д | AI-Прогноз)
    builder.row(
        InlineKeyboardButton(
            text=chart_label,
            callback_data=ProductActionCallback(action="chart", product_id=product_id).pack(),
        ),
        InlineKeyboardButton(
            text=ai_label,
            callback_data=ProductActionCallback(action="ai_forecast", product_id=product_id).pack(),
        ),
    )
    # Ряд 3 (4 кнопки Compact: швидка ціль знижки 🎯-5% / 🎯-10% / 🎯-15% / ✍️ Ціль)
    builder.row(
        InlineKeyboardButton(
            text="🎯-5%",
            callback_data=ProductActionCallback(
                action="set_target_pct", product_id=product_id, percent=5
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🎯-10%",
            callback_data=ProductActionCallback(
                action="set_target_pct", product_id=product_id, percent=10
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🎯-15%",
            callback_data=ProductActionCallback(
                action="set_target_pct", product_id=product_id, percent=15
            ).pack(),
        ),
        InlineKeyboardButton(
            text=custom_lbl,
            callback_data=ProductActionCallback(
                action="custom_target", product_id=product_id
            ).pack(),
        ),
    )
    # Ряд 4 (3 кнопки: Оновити | Добірка / Вішліст | Видалити)
    builder.row(
        InlineKeyboardButton(
            text=refresh_label,
            callback_data=ProductActionCallback(action="refresh", product_id=product_id).pack(),
        ),
        InlineKeyboardButton(
            text=cat_label,
            callback_data=WishlistCallback(action="pick", product_id=product_id).pack(),
        ),
        InlineKeyboardButton(
            text=del_label,
            callback_data=ProductActionCallback(action="confirm_delete", product_id=product_id).pack(),
        ),
    )
    # Ряд 5 (2 кнопки: Поділитися | До списку товарів)
    builder.row(
        InlineKeyboardButton(
            text=share_short,
            switch_inline_query=share_query,
        ),
        InlineKeyboardButton(
            text=back_label,
            callback_data=OnboardingCallback(action="my_tracking", page=1).pack(),
        ),
    )
    return builder.as_markup()


def build_category_picker_keyboard(
    product_id: int,
    current_tag: str | None = None,
    lang: str = "uk",
) -> InlineKeyboardMarkup:
    """Клавіатура вибору розумної добірки / вішліста для товару («💻 Збірка ПК», «🎁 Подарунки» тощо)."""
    lang = normalize_lang(lang)
    builder = InlineKeyboardBuilder()

    tags_order = ["pc", "gadgets", "gifts", "home", "hunt"]
    btns: list[InlineKeyboardButton] = []
    for tag in tags_order:
        label = format_category_badge(tag, lang)
        mark = " ✅" if current_tag == tag else ""
        btns.append(
            InlineKeyboardButton(
                text=f"{label}{mark}",
                callback_data=WishlistCallback(action="set", product_id=product_id, tag=tag).pack(),
            )
        )

    builder.row(btns[0], btns[1])
    builder.row(btns[2], btns[3])
    builder.row(btns[4])

    clear_lbl = (
        "🧹 No Category"
        if lang == "en"
        else ("🧹 Без подборки" if lang == "ru" else "🧹 Без добірки")
    )
    back_lbl = (
        "⬅️ To Card"
        if lang == "en"
        else ("⬅️ К товару" if lang == "ru" else "⬅️ До товару")
    )
    builder.row(
        InlineKeyboardButton(
            text=clear_lbl,
            callback_data=WishlistCallback(action="set", product_id=product_id, tag="none").pack(),
        ),
        InlineKeyboardButton(
            text=back_lbl,
            callback_data=ProductActionCallback(action="card", product_id=product_id).pack(),
        ),
    )
    return builder.as_markup()


def build_delete_confirm_keyboard(product_id: int, lang: str = "uk") -> InlineKeyboardMarkup:
    """Клавіатура безпечного підтвердження видалення одного товару."""
    lang = normalize_lang(lang)
    if lang == "en":
        yes_btn = "✅ Yes, Delete"
        no_btn = "⬅️ Keep Item"
    elif lang == "ru":
        yes_btn = "✅ Да, удалить"
        no_btn = "⬅️ Оставить"
    else:
        yes_btn = "✅ Так, видалити"
        no_btn = "⬅️ Залишити"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=yes_btn,
            callback_data=ProductActionCallback(action="delete", product_id=product_id).pack(),
        ),
        InlineKeyboardButton(
            text=no_btn,
            callback_data=ProductActionCallback(action="card", product_id=product_id).pack(),
        ),
    )
    return builder.as_markup()


def build_clear_confirm_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    """Клавіатура безпечного підтвердження очищення всього списку товарів."""
    lang = normalize_lang(lang)
    if lang == "en":
        yes_btn = "🗑 Clear All"
        no_btn = "⬅️ Go Back"
    elif lang == "ru":
        yes_btn = "🗑 Да, очистить"
        no_btn = "⬅️ Назад"
    else:
        yes_btn = "🗑 Так, очистити"
        no_btn = "⬅️ Назад"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=yes_btn,
            callback_data=OnboardingCallback(action="clear_all").pack(),
        ),
        InlineKeyboardButton(
            text=no_btn,
            callback_data=OnboardingCallback(action="my_tracking", page=1).pack(),
        ),
    )
    return builder.as_markup()


def _calc_preset_price(cur: Decimal, factor: str) -> Decimal:
    raw = cur * Decimal(factor)
    if cur >= Decimal("100"):
        return max(Decimal("1"), raw.quantize(Decimal("1")))
    return max(Decimal("0.50"), raw.quantize(Decimal("0.01")))


def build_target_price_presets_keyboard(
    *,
    product_id: int,
    current_price: Decimal,
    currency: str = "UAH",
    lang: str = "uk",
) -> InlineKeyboardMarkup:
    """
    Зрозуміла клавіатура вибору бажаної знижки (Target Price):
    Ряд 1 (4 кнопки Compact): [🎯-5%] | [🎯-10%] | [🎯-15%] | [🎯-20%]
    Ряд 2 (1 кнопка Smart):   [🧠 Авто-ціль AI (Оптимальна ціна)]
    Ряд 3 (2 кнопки Medium):  [🧹 Скинути] | [⬅️ До товару]
    """
    lang = normalize_lang(lang)
    builder = InlineKeyboardBuilder()
    cur = Decimal(str(current_price))

    p5 = _calc_preset_price(cur, "0.95")
    p10 = _calc_preset_price(cur, "0.90")
    p15 = _calc_preset_price(cur, "0.85")
    p20 = _calc_preset_price(cur, "0.80")

    smart_lbl = (
        "🧠 Auto AI Target (Optimal Price)"
        if lang == "en"
        else ("🧠 Авто-цель AI (Оптимальная цена)" if lang == "ru" else "🧠 Авто-ціль AI (Оптимальна ціна)")
    )
    back_card = "⬅️ To Card" if lang == "en" else ("⬅️ К товару" if lang == "ru" else "⬅️ До товару")

    builder.row(
        InlineKeyboardButton(
            text="🎯-5%",
            callback_data=ProductActionCallback(
                action="set_target_pct", product_id=product_id, percent=5, value=int(p5 * 100)
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🎯-10%",
            callback_data=ProductActionCallback(
                action="set_target_pct", product_id=product_id, percent=10, value=int(p10 * 100)
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🎯-15%",
            callback_data=ProductActionCallback(
                action="set_target_pct", product_id=product_id, percent=15, value=int(p15 * 100)
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🎯-20%",
            callback_data=ProductActionCallback(
                action="set_target_pct", product_id=product_id, percent=20, value=int(p20 * 100)
            ).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=smart_lbl,
            callback_data=ProductActionCallback(
                action="smart_target", product_id=product_id
            ).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t("ibtn_reset_target", lang),
            callback_data=ProductActionCallback(
                action="set_target", product_id=product_id, value=0
            ).pack(),
        ),
        InlineKeyboardButton(
            text=back_card,
            callback_data=ProductActionCallback(
                action="cancel_target", product_id=product_id
            ).pack(),
        ),
    )
    return builder.as_markup()


def build_ai_forecast_keyboard(
    *,
    product_id: int,
    optimal_price: Decimal,
    currency: str = "UAH",
    lang: str = "uk",
) -> InlineKeyboardMarkup:
    """Клавіатура під AI-прогнозом ціни з 1-клік кнопкою встановлення оптимальної цілі."""
    lang = normalize_lang(lang)
    opt_str = fmt_money(optimal_price, currency)
    if lang == "en":
        set_opt = f"🎯 Set Optimal Target: {opt_str}"
        chart_lbl = "📈 30d Chart"
        back_lbl = "⬅️ To Card"
    elif lang == "ru":
        set_opt = f"🎯 Задать AI-цель: {opt_str}"
        chart_lbl = "📈 График 30д"
        back_lbl = "⬅️ К товару"
    else:
        set_opt = f"🎯 Задати AI-ціль: {opt_str}"
        chart_lbl = "📈 Графік 30д"
        back_lbl = "⬅️ До товару"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=set_opt,
            callback_data=ProductActionCallback(
                action="smart_target",
                product_id=product_id,
                value=int(optimal_price * 100),
            ).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=chart_lbl,
            callback_data=ProductActionCallback(action="chart", product_id=product_id).pack(),
        ),
        InlineKeyboardButton(
            text=back_lbl,
            callback_data=ProductActionCallback(action="card", product_id=product_id).pack(),
        ),
    )
    return builder.as_markup()


def build_price_alert_keyboard(
    *,
    product_id: int,
    clean_url: str | None = None,
    product_url: str | None = None,
    user_id: int,
    marketplace: str = "rozetka",
    lang: str = "uk",
    title: str | None = None,
    price_str: str | None = None,
    new_price_str: str | None = None,
    old_price_str: str | None = None,
) -> InlineKeyboardMarkup:
    """
    Клавіатура для сповіщення про зниження ціни (Alert Card):
    Ряд 1 (1 кнопка Full-width CTA): [🛒 Купити зі знижкою ↗]
    Ряд 2 (2 кнопки Medium):         [📈 Графік 30д] | [🎯 Моя ціль]
    Ряд 3 (2 кнопки Medium):         [📤 Поділитися] | [📦 Мої товари]
    """
    lang = normalize_lang(lang)
    target_url = clean_url or product_url or "https://rozetka.com.ua"
    affiliate_url = build_affiliate_url(target_url, user_id=user_id)
    store_name = format_marketplace_name(marketplace)
    display_price = new_price_str or price_str
    price_part = f" · {display_price}" if display_price else ""

    share_query = _build_viral_share_query(
        title=title,
        price_str=display_price,
        old_price_str=old_price_str,
        store_name=store_name,
        user_id=user_id,
        lang=lang,
    )

    if lang == "en":
        buy_label = f"🛒 Buy on {store_name}{price_part} ↗"
        chart_label = "📈 30d Chart"
        target_label = "🎯 My Target"
        share_label = "📤 Share"
        list_label = "📦 My Items"
    elif lang == "ru":
        buy_label = f"🛒 Купить ({store_name}){price_part} ↗"
        chart_label = "📈 График 30д"
        target_label = "🎯 Моя цель"
        share_label = "📤 Поделиться"
        list_label = "📦 К списку"
    else:
        buy_label = f"🛒 Купити ({store_name}){price_part} ↗"
        chart_label = "📈 Графік 30д"
        target_label = "🎯 Моя ціль"
        share_label = "📤 Поділитися"
        list_label = "📦 До списку"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=buy_label,
            url=affiliate_url,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=chart_label,
            callback_data=ProductActionCallback(action="chart", product_id=product_id).pack(),
        ),
        InlineKeyboardButton(
            text=target_label,
            callback_data=ProductActionCallback(action="target", product_id=product_id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=share_label,
            switch_inline_query=share_query,
        ),
        InlineKeyboardButton(
            text=list_label,
            callback_data=OnboardingCallback(action="my_tracking", page=1).pack(),
        ),
    )
    return builder.as_markup()


def build_top_drops_keyboard(
    drops: Sequence[dict[str, object]],
    user_id: int,
    lang: str = "uk",
) -> InlineKeyboardMarkup:
    """
    Клавіатура для вітрини «🔥 Гарячі знижки дня» (Top Drops):
    Натискання на товар відкриває його детальну картку в боті, а кнопка оновлення запускає живе сканування!
    """
    lang = normalize_lang(lang)
    builder = InlineKeyboardBuilder()
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    for idx, item in enumerate(drops[:5], start=1):
        prod = item["product"]
        pct = int(round(float(item["drop_pct"])))
        prod_id = int(getattr(prod, "id", 0))
        is_tracked = bool(item.get("is_user_tracked", False))
        cur_price = _short_price(getattr(prod, "current_price", 0), getattr(prod, "currency", "UAH"))
        short_t = str(getattr(prod, "title", ""))[:20].strip()
        prefix = medals.get(idx, "🔥")
        star = "⭐️ " if is_tracked else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{prefix} #{idx} (−{pct}%) · {star}{short_t}… — {cur_price}",
                callback_data=ProductActionCallback(action="card", product_id=prod_id).pack(),
            )
        )

    scan_label = {
        "uk": "🔄 Знайти свіжі знижки (Live)",
        "en": "🔄 Scan Fresh Live Deals",
        "ru": "🔄 Найти свежие скидки (Live)",
    }.get(lang, "🔄 Знайти свіжі знижки (Live)")

    builder.row(
        InlineKeyboardButton(
            text=scan_label,
            callback_data=SettingsCallback(action="top_drops_refresh").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t("ibtn_my_products", lang),
            callback_data=OnboardingCallback(action="my_tracking", page=1).pack(),
        ),
        InlineKeyboardButton(
            text=t("ibtn_main_menu", lang),
            callback_data=OnboardingCallback(action="main_menu").pack(),
        ),
    )
    return builder.as_markup()


def build_pro_offer_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    """Клавіатура оформлення PRO-підписки (30 днів / 90 днів ХІТ -20% / 365 днів VIP -40% / рефералка)."""
    lang = normalize_lang(lang)
    stars_30 = settings.pro_price_stars
    stars_90 = max(1, int(round(stars_30 * 2.4)))
    stars_365 = max(1, int(round(stars_30 * 7.2)))

    if lang == "en":
        btn_30 = f"⚡️ PRO 30 Days — {stars_30} ⭐️"
        btn_90 = f"🔥 PRO 90 Days (-20% BEST) — {stars_90} ⭐️"
        btn_365 = f"👑 PRO 365 Days (-40% VIP) — {stars_365} ⭐️"
        btn_ref = "🎁 Get +7 Days PRO per Friend"
    elif lang == "ru":
        btn_30 = f"⚡️ PRO на 30 дней — {stars_30} ⭐️"
        btn_90 = f"🔥 PRO на 90 дней (-20% ХИТ) — {stars_90} ⭐️"
        btn_365 = f"👑 PRO на 365 дней (-40% VIP) — {stars_365} ⭐️"
        btn_ref = "🎁 Получить +7 дней PRO за друга"
    else:
        btn_30 = f"⚡️ PRO на 30 днів — {stars_30} ⭐️"
        btn_90 = f"🔥 PRO на 90 днів (-20% ХІТ) — {stars_90} ⭐️"
        btn_365 = f"👑 PRO на 365 днів (-40% VIP) — {stars_365} ⭐️"
        btn_ref = "🎁 Отримати +7 днів PRO за друга"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=btn_30,
            callback_data=ProPaymentCallback(action="buy_stars", plan_days=30).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=btn_90,
            callback_data=ProPaymentCallback(action="buy_stars", plan_days=90).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=btn_365,
            callback_data=ProPaymentCallback(action="buy_stars", plan_days=365).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=btn_ref,
            callback_data=OnboardingCallback(action="referral").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t("ibtn_my_products", lang),
            callback_data=OnboardingCallback(action="my_tracking", page=1).pack(),
        ),
        InlineKeyboardButton(
            text=t("ibtn_main_menu", lang),
            callback_data=OnboardingCallback(action="main_menu").pack(),
        ),
    )
    return builder.as_markup()


def build_referral_keyboard(ref_link: str, lang: str = "uk") -> InlineKeyboardMarkup:
    """Клавіатура для миттєвого поширення реферального посилання друзям у Telegram."""
    lang = normalize_lang(lang)
    share_url = f"https://t.me/share/url?url={quote(ref_link)}"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("ibtn_share_ref", lang),
            url=share_url,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=t("ibtn_pro_plan", lang),
            callback_data=OnboardingCallback(action="pro_menu").pack(),
        ),
        InlineKeyboardButton(
            text=t("ibtn_main_menu", lang),
            callback_data=OnboardingCallback(action="main_menu").pack(),
        ),
    )
    return builder.as_markup()


def build_analytics_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    """Інлайн-клавіатура для розділу аналітики портфеля та вигоди."""
    lang = normalize_lang(lang)
    cmp_label = "⚖️ Compare" if lang == "en" else ("⚖️ Сравнить" if lang == "ru" else "⚖️ Порівняти")
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("ibtn_my_products", lang),
            callback_data=OnboardingCallback(action="my_tracking", page=1).pack(),
        ),
        InlineKeyboardButton(
            text=cmp_label,
            callback_data=SettingsCallback(action="compare").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t("ibtn_export_csv", lang),
            callback_data=OnboardingCallback(action="export_csv").pack(),
        ),
        InlineKeyboardButton(
            text=t("ibtn_main_menu", lang),
            callback_data=OnboardingCallback(action="main_menu").pack(),
        ),
    )
    return builder.as_markup()


def build_tracking_list_keyboard(
    subscriptions: Sequence[UserProduct],
    user_id: int,
    lang: str = "uk",
    page: int = 1,
    active_tag: str | None = None,
    all_subscriptions: Sequence[UserProduct] | None = None,
) -> InlineKeyboardMarkup:
    """
    Дружня та зрозуміла клавіатура для списку активних підписок («📦 Мої товари»):
    - Кожен товар — це 1 охайна кнопка на всю ширину поста:
      [📦 1. Rozetka · Назва... — 1 499 ₴ 🎯] -> миттєво відкриває картку товару!
    - Якщо користувач створив добірки (💻 Збірка ПК, 🎁 Подарунки), зверху з'являються вкладки фільтрації.
    - Підтримується пагінація (по 6 товарів на сторінку) та кнопка спільного доступу до вішліста.
    """
    lang = normalize_lang(lang)
    builder = InlineKeyboardBuilder()

    full_subs = list(all_subscriptions) if all_subscriptions is not None else list(subscriptions)
    used_tags: list[str] = []
    for s in full_subs:
        tag_val = getattr(s, "category_tag", None)
        if tag_val and tag_val in WISHLIST_CATEGORIES and tag_val not in used_tags:
            used_tags.append(tag_val)

    # Вкладки розумних добірок показуються прогресивно (тільки якщо користувач призначив хоча б 1 категорію)
    if used_tags:
        all_lbl = "All" if lang == "en" else ("Все" if lang == "ru" else "Всі")
        all_mark = "✅ " if not active_tag else ""
        filter_btns: list[InlineKeyboardButton] = [
            InlineKeyboardButton(
                text=f"{all_mark}📂 {all_lbl} ({len(full_subs)})",
                callback_data=WishlistCallback(action="filter", tag="all").pack(),
            )
        ]
        for t_code in used_tags[:3]:
            cnt = sum(1 for s in full_subs if getattr(s, "category_tag", None) == t_code)
            badge = format_category_badge(t_code, lang)
            mark = "✅ " if active_tag == t_code else ""
            filter_btns.append(
                InlineKeyboardButton(
                    text=f"{mark}{badge} ({cnt})",
                    callback_data=WishlistCallback(action="filter", tag=t_code).pack(),
                )
            )
        for i in range(0, len(filter_btns), 2):
            builder.row(*filter_btns[i : i + 2])

    total_items = len(subscriptions)
    total_pages = max(1, math.ceil(total_items / ITEMS_PER_PAGE))
    cur_page = max(1, min(page, total_pages))
    start_idx = (cur_page - 1) * ITEMS_PER_PAGE
    page_subs = subscriptions[start_idx : start_idx + ITEMS_PER_PAGE]

    for idx, sub in enumerate(page_subs, start=start_idx + 1):
        product = sub.product
        store_short = format_marketplace_name(product.marketplace)[:10]
        short_title = product.title[:16] + ("…" if len(product.title) > 16 else "")
        is_avail = bool(getattr(product, "is_available", True))
        if not is_avail or Decimal(str(product.current_price)) <= Decimal("0"):
            price_str = "🟢 Снайпер" if lang != "en" else "🟢 Sniper"
        else:
            price_str = _short_price(product.current_price, product.currency)
        target_mark = " 🎯" if sub.target_price is not None else ""
        cat_icon = ""
        sub_tag = getattr(sub, "category_tag", None)
        if sub_tag and sub_tag in WISHLIST_CATEGORIES and not active_tag:
            cat_icon = format_category_badge(sub_tag, lang).split(" ")[0] + " "

        builder.row(
            InlineKeyboardButton(
                text=f"📦 {idx}. {cat_icon}{store_short} · {short_title} — {price_str}{target_mark}",
                callback_data=ProductActionCallback(action="card", product_id=product.id).pack(),
            )
        )

    # Пагінація, якщо товарів більше ніж 6
    if total_pages > 1:
        prev_page = cur_page - 1 if cur_page > 1 else total_pages
        next_page = cur_page + 1 if cur_page < total_pages else 1
        page_lbl = f"📄 {cur_page}/{total_pages}"
        builder.row(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=OnboardingCallback(action="my_tracking", page=prev_page).pack(),
            ),
            InlineKeyboardButton(
                text=page_lbl,
                callback_data=OnboardingCallback(action="my_tracking", page=cur_page).pack(),
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=OnboardingCallback(action="my_tracking", page=next_page).pack(),
            ),
        )

    if lang == "en":
        refresh_lbl = "🔄 Check All"
        stats_lbl = "📊 My Savings"
        share_wl_lbl = "📤 Share Wishlist"
        csv_lbl = "📥 Excel/CSV"
        add_lbl = "🔍 Search / Add"
        clear_lbl = "🧹 Clear All"
    elif lang == "ru":
        refresh_lbl = "🔄 Обновить"
        stats_lbl = "📊 Экономия"
        share_wl_lbl = "📤 Поделиться списком"
        csv_lbl = "📥 Отчёт CSV"
        add_lbl = "🔍 Найти ещё"
        clear_lbl = "🧹 Очистить"
    else:
        refresh_lbl = "🔄 Оновити всі"
        stats_lbl = "📊 Моя вигода"
        share_wl_lbl = "📤 Поділитися вішлістом"
        csv_lbl = "📥 Звіт Excel"
        add_lbl = "🔍 Знайти ще"
        clear_lbl = "🧹 Очистити"

    builder.row(
        InlineKeyboardButton(
            text=refresh_lbl,
            callback_data=OnboardingCallback(action="refresh_all").pack(),
        ),
        InlineKeyboardButton(
            text=stats_lbl,
            callback_data=OnboardingCallback(action="stats").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=share_wl_lbl,
            callback_data=WishlistCallback(action="share", tag=active_tag or "all").pack(),
        ),
        InlineKeyboardButton(
            text=csv_lbl,
            callback_data=OnboardingCallback(action="export_csv").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=add_lbl,
            callback_data=OnboardingCallback(action="search_prompt").pack(),
        ),
        InlineKeyboardButton(
            text=clear_lbl,
            callback_data=OnboardingCallback(action="confirm_clear").pack(),
        ),
    )
    return builder.as_markup()


def build_search_results_keyboard(
    offers: Sequence[Any],
    lang: str = "uk",
) -> InlineKeyboardMarkup:
    """
    Клавіатура результатів швидкого пошуку за назвою товару:
    Кожна кнопка в 1 клік додає обрану пропозицію у відстеження!
    """
    lang = normalize_lang(lang)
    builder = InlineKeyboardBuilder()
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    for idx, offer in enumerate(offers[:6], start=1):
        store_short = format_marketplace_name(getattr(offer, "marketplace", "store"))[:10]
        title_str = str(getattr(offer, "title", ""))
        short_title = title_str[:16] + ("…" if len(title_str) > 16 else "")
        is_avail = bool(getattr(offer, "is_available", True))
        price_val = getattr(offer, "price", Decimal("0"))
        curr = str(getattr(offer, "currency", "UAH"))
        token = str(getattr(offer, "token", ""))

        if not is_avail or Decimal(str(price_val)) <= Decimal("0"):
            price_badge = "🟢 Снайпер" if lang != "en" else "🟢 Sniper"
        else:
            price_badge = _short_price(price_val, curr)

        prefix = medals.get(idx, "➕")
        track_verb = "Track" if lang == "en" else ("Следить" if lang == "ru" else "Стежити")
        builder.row(
            InlineKeyboardButton(
                text=f"{prefix} #{idx} {track_verb}: {store_short} · {short_title} — {price_badge}",
                callback_data=SearchCallback(action="track", token=token).pack(),
            )
        )

    inline_lbl = (
        "💬 Share in Chat (@Bot)"
        if lang == "en"
        else ("💬 Поделиться в чате" if lang == "ru" else "💬 Надіслати в чат (@Bot)")
    )
    menu_lbl = t("ibtn_main_menu", lang)
    builder.row(
        InlineKeyboardButton(
            text=inline_lbl,
            switch_inline_query=str(getattr(offers[0], "title", ""))[:32] if offers else "",
        ),
        InlineKeyboardButton(
            text=menu_lbl,
            callback_data=OnboardingCallback(action="main_menu").pack(),
        ),
    )
    return builder.as_markup()


def build_shared_wishlist_keyboard(
    subscriptions: Sequence[UserProduct],
    lang: str = "uk",
) -> InlineKeyboardMarkup:
    """Клавіатура спільного вішліста друга: дозволяє в 1 клік відкрити або додати будь-який товар собі."""
    lang = normalize_lang(lang)
    builder = InlineKeyboardBuilder()

    for idx, sub in enumerate(subscriptions[:8], start=1):
        p = sub.product
        store_short = format_marketplace_name(p.marketplace)[:10]
        short_title = p.title[:18] + ("…" if len(p.title) > 18 else "")
        price_str = _short_price(p.current_price, p.currency)
        builder.row(
            InlineKeyboardButton(
                text=f"➕ #{idx} {store_short} · {short_title} — {price_str}",
                callback_data=ProductActionCallback(action="card", product_id=p.id).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=t("ibtn_main_menu", lang),
            callback_data=OnboardingCallback(action="main_menu").pack(),
        )
    )
    return builder.as_markup()
