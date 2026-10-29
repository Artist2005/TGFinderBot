from __future__ import annotations

from typing import Literal

from aiogram.filters.callback_data import CallbackData


class OnboardingCallback(CallbackData, prefix="onb"):
    """CallbackData для навігаційних кнопок онбордингу та головного меню."""

    action: Literal[
        "main_menu",
        "how_it_works",
        "my_tracking",
        "marketplaces",
        "stats",
        "refresh_all",
        "confirm_clear",
        "clear_all",
        "referral",
        "export_csv",
        "lang_menu",
        "pro_menu",
        "settings",
        "top_drops",
        "top_drops_refresh",
        "compare",
        "add_guide",
        "admin_check_now",
    ]
    page: int = 1


class SettingsCallback(CallbackData, prefix="cfg"):
    """CallbackData для інтерактивних перемикачів (Toggle Buttons) та фіч утримання."""

    action: Literal[
        "open",
        "toggle_notify",
        "toggle_dnd",
        "top_drops",
        "top_drops_refresh",
        "compare",
        "digest_preview",
    ]


class LanguageCallback(CallbackData, prefix="lang"):
    """CallbackData для перемикання мови інтерфейсу (uk / en / ru)."""

    code: Literal["uk", "en", "ru"]


class ProductActionCallback(CallbackData, prefix="prod"):
    """CallbackData для дій над карткою конкретного товару."""

    action: Literal[
        "card",
        "chart",
        "analyze",
        "ai_forecast",
        "smart_target",
        "refresh",
        "confirm_delete",
        "delete",
        "target",
        "set_target",
        "set_target_pct",
        "custom_target",
        "cancel_target",
        "compare",
    ]
    product_id: int
    value: int = 0  # ціна в центах/копійках (value / 100)
    percent: int = 0  # швидка знижка у відсотках (5, 10, 15, 20)


class ProPaymentCallback(CallbackData, prefix="pro"):
    """CallbackData для виклику інвойсу оплати Pro через Telegram Stars."""

    action: Literal["buy_stars"]
    plan_days: int = 30
