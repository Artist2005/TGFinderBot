from __future__ import annotations

from decimal import Decimal
import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.i18n import fmt_money, t
from bot.keyboards.inline import (
    build_add_guide_keyboard,
    build_analytics_keyboard,
    build_language_keyboard,
    build_marketplaces_keyboard,
    build_price_alert_keyboard,
    build_pro_offer_keyboard,
    build_product_card_keyboard,
    build_referral_keyboard,
    build_settings_keyboard,
    build_start_keyboard,
)
from bot.keyboards.reply import (
    ALL_BTN_ADD_PRODUCT,
    ALL_BTN_ANALYTICS,
    ALL_BTN_HELP,
    ALL_BTN_LANGUAGE,
    ALL_BTN_MARKETPLACES,
    ALL_BTN_MY_PRODUCTS,
    ALL_BTN_PRO_TARIFF,
    ALL_BTN_SETTINGS,
    build_main_reply_keyboard,
)
from config import settings
from database import crud
from database.models import User
from services.analytics import (
    build_price_drop_alert_card,
    build_product_caption_with_30d_stats,
    generate_user_csv_report,
    render_add_product_guide_card,
    render_dashboard_card,
    render_my_tracking_text_and_kb,
    render_pro_offer_card,
    render_quick_compare_card,
    render_settings_card,
    render_shared_wishlist_card,
    render_top_drops_card,
    render_user_stats_text,
)

router = Router(name="commands_router")


def get_marketplaces_info_text(lang: str = "uk") -> str:
    """Список підтримуваних українських та світових маркетплейсів обраною мовою."""
    return t("marketplaces_info", lang)


def get_how_it_works_text(lang: str = "uk") -> str:
    """Лаконічна довідка про роботу бота обраною мовою."""
    return t("how_it_works", lang)


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Вітальне повідомлення з обробкою реферальних deep-link посилань (/start ref_12345)."""
    lang = db_user.language

    # Перевірка реферального коду (/start ref_<user_id>)
    args = (command.args or "").strip()
    if args.startswith("ref_"):
        raw_ref = args.removeprefix("ref_").strip()
        if raw_ref.isdigit():
            referrer_id = int(raw_ref)
            applied = await crud.apply_referral_bonus(
                session,
                new_user=db_user,
                referrer_id=referrer_id,
            )
            if applied:
                await message.answer(t("ref_welcome_applied", lang))
                try:
                    referrer = await session.get(User, referrer_id)
                    ref_lang = referrer.language if referrer else "uk"
                    if message.bot:
                        await message.bot.send_message(
                            chat_id=referrer_id,
                            text=t("ref_reward_notify", ref_lang),
                        )
                except Exception:
                    pass

    items_count = await crud.count_user_subscriptions(session, db_user.id)
    limit = settings.pro_max_products if db_user.has_active_pro else settings.free_max_products
    tier_badge = "⭐️ PRO" if db_user.has_active_pro else "Free"

    # Deep-link: Спільний вішліст друга (/start wishlist_<owner_id>)
    if args.startswith("wishlist_"):
        raw_owner = args.removeprefix("wishlist_").strip()
        if raw_owner.isdigit():
            owner_id = int(raw_owner)
            w_text, w_kb = await render_shared_wishlist_card(session, owner_id, viewer_lang=lang)
            await message.answer(
                t("start_welcome", lang, tier=tier_badge, limit=limit),
                reply_markup=build_main_reply_keyboard(lang, items_count=items_count),
            )
            await message.answer(w_text, reply_markup=w_kb)
            return

    # Deep-link: Підписка на товар із Inline-картки в чаті (/start track_<product_id>)
    if args.startswith("track_"):
        raw_pid = args.removeprefix("track_").strip()
        if raw_pid.isdigit():
            pid = int(raw_pid)
            prod_with_hist = await crud.get_product_with_history(session, pid)
            if prod_with_hist is not None:
                if items_count < limit:
                    sub, _ = await crud.subscribe_user_to_product(session, db_user.id, pid)
                    items_count = await crud.count_user_subscriptions(session, db_user.id)
                else:
                    sub = await crud.get_user_subscription(session, db_user.id, pid)
                cur_dec = Decimal(str(prod_with_hist.current_price))
                history_prices = (
                    [Decimal(str(h.price)) for h in prod_with_hist.price_history]
                    if prod_with_hist.price_history
                    else [cur_dec]
                )
                caption = build_product_caption_with_30d_stats(
                    title=prod_with_hist.title,
                    marketplace=prod_with_hist.marketplace,
                    currency=prod_with_hist.currency,
                    current_price=cur_dec,
                    is_available=getattr(prod_with_hist, "is_available", True),
                    history_prices=history_prices,
                    target_price=sub.target_price if sub else None,
                    category_tag=getattr(sub, "category_tag", None) if sub else None,
                    status_header=t("status_tracking_on", lang),
                    lang=lang,
                )
                kb = build_product_card_keyboard(
                    product_id=prod_with_hist.id,
                    clean_url=prod_with_hist.url,
                    user_id=db_user.id,
                    marketplace=prod_with_hist.marketplace,
                    title=prod_with_hist.title,
                    price_str=fmt_money(cur_dec, prod_with_hist.currency),
                    category_tag=getattr(sub, "category_tag", None) if sub else None,
                    lang=lang,
                )
                await message.answer(
                    t("start_welcome", lang, tier=tier_badge, limit=limit),
                    reply_markup=build_main_reply_keyboard(lang, items_count=items_count),
                )
                await message.answer(caption, reply_markup=kb)
                return

    text = t("start_welcome", lang, tier=tier_badge, limit=limit)
    await message.answer(
        text,
        reply_markup=build_main_reply_keyboard(lang, items_count=items_count),
    )
    await message.answer(
        render_dashboard_card(db_user, items_count=items_count),
        reply_markup=build_start_keyboard(lang, items_count=items_count),
    )


@router.message(Command("settings"))
@router.message(F.text.in_(ALL_BTN_SETTINGS))
async def cmd_settings(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Меню налаштувань (Сповіщення УВІМК/ВИМК, Нічний режим 23:00–08:00, Гарячі знижки, Мова)."""
    lang = db_user.language
    items_count = await crud.count_user_subscriptions(session, db_user.id)
    await message.answer(
        render_settings_card(db_user, items_count=items_count),
        reply_markup=build_settings_keyboard(db_user, lang),
    )


@router.message(Command("top", "hot", "deals"))
async def cmd_top_drops(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Вітрина «🔥 Гарячі знижки дня» (Топ-5 товарів із найбільшим % падіння ціни)."""
    text, kb = await render_top_drops_card(session, db_user)
    await message.answer(text, reply_markup=kb)


@router.message(Command("compare"))
async def cmd_quick_compare(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Швидке порівняння цін по відстежуваних магазинах."""
    text, kb = await render_quick_compare_card(session, db_user)
    await message.answer(text, reply_markup=kb)


@router.message(Command("lang", "language"))
@router.message(F.text.in_(ALL_BTN_LANGUAGE))
async def cmd_language(message: Message, db_user: User) -> None:
    """Меню вибору мови інтерфейсу (UK / EN / RU)."""
    lang = db_user.language
    await message.answer(
        t("choose_language", lang),
        reply_markup=build_language_keyboard(lang),
    )


@router.message(Command("ref", "referral"))
async def cmd_referral(message: Message, db_user: User) -> None:
    """Показує реферальну програму та персональне посилання користувача."""
    lang = db_user.language
    bot_Username = "clipsnag_bot"
    if message.bot:
        try:
            me = await message.bot.get_me()
            if me.username:
                bot_Username = me.username
        except Exception:
            pass
    ref_link = f"https://t.me/{bot_Username}?start=ref_{db_user.id}"
    text = t(
        "ref_program_text",
        lang,
        ref_link=ref_link,
        refs_count=db_user.referrals_count,
    )
    await message.answer(text, reply_markup=build_referral_keyboard(ref_link, lang))


@router.message(Command("export"))
async def cmd_export_csv(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Експортує всі товари користувача та 30-денну аналітику у CSV/Excel."""
    lang = db_user.language
    csv_file = await generate_user_csv_report(session, db_user)
    if csv_file is None:
        await message.answer(t("export_empty", lang))
        return
    await message.answer_document(
        document=csv_file,
        caption=t("export_ready", lang),
    )


@router.message(Command("admin"))
async def cmd_admin_dashboard(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Комерційна панель адміністратора (/admin) зі статистикою платформи."""
    lang = db_user.language
    stats = await crud.get_platform_stats(session)
    await message.answer(
        t(
            "admin_stats",
            lang,
            users=stats["total_users"],
            pro_users=stats["pro_users"],
            products=stats["total_products"],
            subs=stats["total_subs"],
            points=stats["total_points"],
        )
    )


@router.message(Command("my_tracking"))
@router.message(F.text.in_(ALL_BTN_MY_PRODUCTS))
@router.message(F.text.startswith(("📦 Мої товари", "📦 My Items", "📦 Мои товары")))
async def cmd_my_tracking(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Виводить список активних підписок користувача."""
    lang = db_user.language
    items_count = await crud.count_user_subscriptions(session, db_user.id)
    text, kb = await render_my_tracking_text_and_kb(session, db_user)
    await message.answer(
        text,
        reply_markup=kb or build_main_reply_keyboard(lang, items_count=items_count),
    )


@router.message(F.text.in_(ALL_BTN_ADD_PRODUCT))
async def btn_add_product_prompt(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Покрокова інструкція додавання товару з кнопкою миттєвого демо-товару."""
    lang = db_user.language
    await message.answer(
        render_add_product_guide_card(lang),
        reply_markup=build_add_guide_keyboard(lang),
    )


@router.message(F.text.in_(ALL_BTN_MARKETPLACES | {"🏪 Маркетплейси"}))
async def btn_marketplaces_info(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Показує список підтримуваних світових та українських маркетплейсів."""
    lang = db_user.language
    await message.answer(
        get_marketplaces_info_text(lang),
        reply_markup=build_marketplaces_keyboard(lang),
    )


@router.message(F.text.in_(ALL_BTN_ANALYTICS | {"📊 Статистика та знижки"}))
async def btn_analytics_report(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Показує зведену статистику за 30 днів із кнопками швидких дій."""
    lang = db_user.language
    text = await render_user_stats_text(session, db_user)
    await message.answer(text, reply_markup=build_analytics_keyboard(lang))


@router.message(F.text.in_(ALL_BTN_HELP | {"ℹ️ Як це працює"}))
async def btn_help_info(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Показує головне меню та коротку інструкцію."""
    lang = db_user.language
    items_count = await crud.count_user_subscriptions(session, db_user.id)
    await message.answer(
        render_dashboard_card(db_user, items_count=items_count),
        reply_markup=build_start_keyboard(lang, items_count=items_count),
    )


@router.message(Command("pro"))
@router.message(F.text.in_(ALL_BTN_PRO_TARIFF | {"⭐️ Тариф PRO"}))
async def cmd_pro(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Преміальна вітрина тарифу PRO з розрахунком окупності (ROI) та тарифами 30/90/365 днів."""
    lang = db_user.language
    text = await render_pro_offer_card(session, db_user)
    await message.answer(text, reply_markup=build_pro_offer_keyboard(lang))

