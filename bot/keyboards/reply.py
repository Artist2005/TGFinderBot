from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from bot.i18n import all_btn_variants, t

# Зворотна сумісність констант (UK за замовчуванням)
BTN_MY_PRODUCTS = t("btn_my_products", "uk")
BTN_ADD_PRODUCT = t("btn_add_product", "uk")
BTN_ANALYTICS = t("btn_analytics", "uk")
BTN_MARKETPLACES = t("btn_marketplaces", "uk")
BTN_PRO_TARIFF = t("btn_pro", "uk")
BTN_SETTINGS = "⚙️ Налаштування"
BTN_LANGUAGE = t("btn_language", "uk")
BTN_HELP = t("btn_help", "uk")

# Набори всіх мовних варіантів для фільтрів F.text.in_(...) + підтримка попередніх підписів кнопок
ALL_BTN_MY_PRODUCTS = all_btn_variants("btn_my_products") | {"📦 My Products", "📦 Мої товари", "📦 My Items", "📦 Мои товары"}
ALL_BTN_ADD_PRODUCT = all_btn_variants("btn_add_product") | {
    "➕ Додати посилання",
    "➕ Add Link",
    "➕ Добавить ссылку",
    "➕ Add Product",
    "➕ Добавить товар",
    "🔍 Знайти / Додати",
    "🔍 Search / Add",
    "🔍 Найти / Добавить",
}
ALL_BTN_ANALYTICS = all_btn_variants("btn_analytics") | {
    "📊 Аналітика та економія",
    "📊 Analytics & Savings",
    "📊 Аналитика и экономия",
}
ALL_BTN_MARKETPLACES = all_btn_variants("btn_marketplaces")
ALL_BTN_PRO_TARIFF = all_btn_variants("btn_pro") | {
    "⭐️ PRO-доступ",
    "⭐️ PRO Access",
    "⭐️ PRO-доступ",
    "⭐️ Тариф PRO",
    "⭐️ PRO Plan",
}
ALL_BTN_SETTINGS = {
    "⚙️ Налаштування",
    "⚙️ Settings",
    "⚙️ Настройки",
}
ALL_BTN_LANGUAGE = all_btn_variants("btn_language") | {
    "🌐 Мова / Language",
    "🌐 Language",
    "🌐 Язык / Language",
}
ALL_BTN_HELP = all_btn_variants("btn_help") | {"ℹ️ Допомога", "ℹ️ Помощь"}


def build_main_reply_keyboard(
    lang: str = "uk",
    items_count: int | None = None,
) -> ReplyKeyboardMarkup:
    """
    Компактна постійна клавіатура головного меню (2 ряди, resize_keyboard=True, is_persistent=True):
    Ряд 1: [📦 Мої товари (4)] | [➕ Додати посилання]
    Ряд 2: [⭐️ PRO-доступ]     | [⚙️ Налаштування]
    """
    base_my_items = t("btn_my_products", lang)
    my_items_label = f"{base_my_items} ({items_count})" if items_count is not None else base_my_items

    if lang == "en":
        add_label = "🔍 Search / Add"
        pro_label = "⭐️ PRO Access"
        settings_label = "⚙️ Settings"
    elif lang == "ru":
        add_label = "🔍 Найти / Добавить"
        pro_label = "⭐️ PRO-доступ"
        settings_label = "⚙️ Настройки"
    else:
        add_label = "🔍 Знайти / Додати"
        pro_label = "⭐️ PRO-доступ"
        settings_label = "⚙️ Налаштування"

    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=my_items_label),
        KeyboardButton(text=add_label),
    )
    builder.row(
        KeyboardButton(text=pro_label),
        KeyboardButton(text=settings_label),
    )
    return builder.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=t("input_placeholder", lang),
    )
