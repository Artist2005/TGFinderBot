from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
import html

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.commands import (
    generate_user_csv_report,
    get_how_it_works_text,
    get_marketplaces_info_text,
    render_my_tracking_text_and_kb,
    render_user_stats_text,
)
from bot.handlers.links import TargetPriceState, build_product_caption_with_30d_stats
from bot.i18n import fmt_money, t
from bot.keyboards.callbacks import (
    LanguageCallback,
    OnboardingCallback,
    ProductActionCallback,
    SearchCallback,
    SettingsCallback,
    WishlistCallback,
)
from bot.keyboards.inline import (
    build_add_guide_keyboard,
    build_analytics_keyboard,
    build_category_picker_keyboard,
    build_clear_confirm_keyboard,
    build_delete_confirm_keyboard,
    build_language_keyboard,
    build_marketplaces_keyboard,
    build_price_alert_keyboard,
    build_pro_offer_keyboard,
    build_product_card_keyboard,
    build_referral_keyboard,
    build_settings_keyboard,
    build_start_keyboard,
    build_target_price_presets_keyboard,
    format_category_badge,
)
from bot.keyboards.reply import build_main_reply_keyboard
from config import settings
from database import crud
from database.models import User
from services.analytics import (
    build_daily_digest_card,
    build_image_preview_options,
    build_price_drop_alert_card,
    compute_optimal_target_price,
    render_add_product_guide_card,
    render_ai_price_forecast_card,
    render_clear_confirm_card,
    render_dashboard_card,
    render_delete_confirm_card,
    render_pro_offer_card,
    render_quick_compare_card,
    render_settings_card,
    render_target_price_card,
    render_top_drops_card,
)
from services.chart_engine import generate_price_chart
from services.scraper import format_marketplace_label, scraper_service
from services.search import get_cached_search_offer

router = Router(name="callbacks_router")

# Легковагий LRU-кеш Telegram file_id для графіків (зберігає лише текстові ID < 15 КБ у RAM).
_CHART_FILE_ID_CACHE: OrderedDict[tuple[object, ...], str] = OrderedDict()
_CHART_CACHE_MAX_SIZE = 256


async def _smart_render(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Плавна Single-Window (SPA) навігація:
    редагує поточне текстове повідомлення на місці замість дублювання нових повідомлень у чаті.
    Якщо повідомлення містить вбудоване фото товару з CDN магазину — автоматично вмикає прев'ю зверху,
    а для списків і меню — вимикає його.
    """
    if callback.message is None:
        return
    preview_opts = build_image_preview_options(text=text)
    if not callback.message.photo and not callback.message.document:
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=reply_markup,
                link_preview_options=preview_opts,
            )
            return
        except Exception as exc:
            if "message is not modified" in str(exc).lower():
                return
    await callback.message.answer(
        text=text,
        reply_markup=reply_markup,
        link_preview_options=preview_opts,
    )


@router.callback_query(LanguageCallback.filter())
async def handle_language_switch(
    callback: CallbackQuery,
    callback_data: LanguageCallback,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Миттєво перемикає мову користувача (UK / EN / RU) та оновлює клавіатуру."""
    new_lang = callback_data.code
    await crud.set_user_language(session, db_user.id, new_lang)
    db_user.language = new_lang
    items_count = await crud.count_user_subscriptions(session, db_user.id)

    await callback.answer(t("lang_switched", new_lang))
    if callback.message:
        await callback.message.answer(
            t("lang_switched", new_lang),
            reply_markup=build_main_reply_keyboard(new_lang, items_count=items_count),
        )
        await _smart_render(
            callback,
            render_dashboard_card(db_user, items_count=items_count),
            reply_markup=build_start_keyboard(new_lang),
        )


@router.callback_query(SettingsCallback.filter())
async def handle_settings_callback(
    callback: CallbackQuery,
    callback_data: SettingsCallback,
    session: AsyncSession,
    db_user: User,
) -> None:
    """
    Обробник інтерактивних Toggle-кнопок налаштувань:
    - 🔔 Сповіщення: УВІМК ✅ ↔️ 🔕 Сповіщення: ВИМК ❌
    - 🌙 Нічний режим (23:00–08:00): УВІМК ✅ ↔️ 🌙 Нічний режим: ВИМК ❌
    - 🔥 Гарячі знижки дня / ⚖️ Порівняння цін / ☕️ Ранковий дайджест
    """
    action = callback_data.action
    lang = db_user.language
    if callback.message is None:
        await callback.answer()
        return

    if action == "open":
        await callback.answer()
        items_count = await crud.count_user_subscriptions(session, db_user.id)
        await _smart_render(
            callback,
            render_settings_card(db_user, items_count=items_count),
            reply_markup=build_settings_keyboard(db_user, lang),
        )
        return

    if action == "toggle_notify":
        new_state = await crud.toggle_user_notifications(session, db_user.id)
        db_user.notifications_enabled = new_state
        items_count = await crud.count_user_subscriptions(session, db_user.id)
        toast = (
            {"uk": "🔔 Сповіщення увімкнено ✅", "en": "🔔 Alerts enabled ✅", "ru": "🔔 Уведомления включены ✅"}.get(lang, "🔔 ✅")
            if new_state
            else {"uk": "🔕 Сповіщення вимкнено ❌", "en": "🔕 Alerts disabled ❌", "ru": "🔕 Уведомления выключены ❌"}.get(lang, "🔕 ❌")
        )
        await callback.answer(toast)
        await _smart_render(
            callback,
            render_settings_card(db_user, items_count=items_count),
            reply_markup=build_settings_keyboard(db_user, lang),
        )
        return

    if action == "toggle_dnd":
        new_state = await crud.toggle_user_dnd(session, db_user.id)
        db_user.dnd_enabled = new_state
        items_count = await crud.count_user_subscriptions(session, db_user.id)
        toast = (
            {
                "uk": "🌙 Нічний режим (23:00–08:00) УВІМК ✅",
                "en": "🌙 Night Mode (23:00–08:00) ON ✅",
                "ru": "🌙 Ночной режим (23:00–08:00) ВКЛ ✅",
            }.get(lang, "🌙 ✅")
            if new_state
            else {
                "uk": "🌙 Нічний режим ВИМК ❌",
                "en": "🌙 Night Mode OFF ❌",
                "ru": "🌙 Ночной режим ВЫКЛ ❌",
            }.get(lang, "🌙 ❌")
        )
        await callback.answer(toast)
        await _smart_render(
            callback,
            render_settings_card(db_user, items_count=items_count),
            reply_markup=build_settings_keyboard(db_user, lang),
        )
        return

    if action in {"top_drops", "top_drops_refresh"}:
        force_live = action == "top_drops_refresh"
        toast = (
            {
                "uk": "📡 Сканую живі знижки..." if force_live else "🔥 ТОП-5 знижок",
                "en": "📡 Scanning live deals..." if force_live else "🔥 Top-5 Deals",
                "ru": "📡 Сканирую живые скидки..." if force_live else "🔥 ТОП-5 скидок",
            }.get(lang, "🔥")
        )
        await callback.answer(toast)
        text, kb = await render_top_drops_card(session, db_user, force_live=force_live)
        await _smart_render(callback, text, reply_markup=kb)
        return

    if action == "compare":
        await callback.answer("⚖️")
        text, kb = await render_quick_compare_card(session, db_user)
        await _smart_render(callback, text, reply_markup=kb)
        return

    if action == "digest_preview":
        await callback.answer("☕️")
        subs = await crud.get_user_subscriptions_with_history(session, db_user.id)
        digest_items = []
        for s in subs[:5]:
            p = s.product
            prices = [Decimal(str(h.price)) for h in p.price_history] if p.price_history else [Decimal(str(p.current_price))]
            old_p = max(prices) if len(prices) > 1 else Decimal(str(p.current_price))
            digest_items.append(
                {
                    "title": p.title,
                    "url": p.url,
                    "marketplace": p.marketplace,
                    "currency": p.currency,
                    "old_price": old_p,
                    "new_price": Decimal(str(p.current_price)),
                }
            )
        text = build_daily_digest_card(digest_items, lang=lang)
        await _smart_render(callback, text, reply_markup=build_start_keyboard(lang))
        return


@router.callback_query(OnboardingCallback.filter())
async def handle_onboarding_callback(
    callback: CallbackQuery,
    callback_data: OnboardingCallback,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Обробляє кнопки навігації, статистики, гарячих знижок, порівняння, демо-товару та CSV-експорту."""
    action = callback_data.action
    lang = db_user.language
    if callback.message is None:
        await callback.answer()
        return

    if action in {"main_menu", "how_it_works"}:
        await callback.answer()
        items_count = await crud.count_user_subscriptions(session, db_user.id)
        await _smart_render(
            callback,
            render_dashboard_card(db_user, items_count=items_count),
            reply_markup=build_start_keyboard(lang, items_count=items_count),
        )
        return

    if action in {"add_guide", "search_prompt"}:
        await callback.answer()
        await _smart_render(
            callback,
            render_add_product_guide_card(lang),
            reply_markup=build_add_guide_keyboard(lang),
        )
        return

    if action == "settings":
        await callback.answer()
        items_count = await crud.count_user_subscriptions(session, db_user.id)
        await _smart_render(
            callback,
            render_settings_card(db_user, items_count=items_count),
            reply_markup=build_settings_keyboard(db_user, lang),
        )
        return

    if action in {"top_drops", "top_drops_refresh"}:
        force_live = action == "top_drops_refresh"
        toast = (
            {
                "uk": "📡 Сканую живі знижки..." if force_live else "🔥 ТОП-5 знижок",
                "en": "📡 Scanning live deals..." if force_live else "🔥 Top-5 Deals",
                "ru": "📡 Сканирую живые скидки..." if force_live else "🔥 ТОП-5 скидок",
            }.get(lang, "🔥")
        )
        await callback.answer(toast)
        text, kb = await render_top_drops_card(session, db_user, force_live=force_live)
        await _smart_render(callback, text, reply_markup=kb)
        return

    if action == "compare":
        await callback.answer("⚖️")
        text, kb = await render_quick_compare_card(session, db_user)
        await _smart_render(callback, text, reply_markup=kb)
        return

    if action == "pro_menu":
        await callback.answer("⭐️ PRO")
        text = await render_pro_offer_card(session, db_user)
        await _smart_render(callback, text, reply_markup=build_pro_offer_keyboard(lang))
        return

    if action == "lang_menu":
        await callback.answer()
        await _smart_render(
            callback,
            t("choose_language", lang),
            reply_markup=build_language_keyboard(lang),
        )
        return

    if action == "referral":
        await callback.answer()
        bot_username = "clipsnag_bot"
        if callback.bot:
            try:
                me = await callback.bot.get_me()
                if me.username:
                    bot_username = me.username
            except Exception:
                pass
        ref_link = f"https://t.me/{bot_username}?start=ref_{db_user.id}"
        text = t(
            "ref_program_text",
            lang,
            ref_link=ref_link,
            refs_count=db_user.referrals_count,
        )
        await _smart_render(callback, text, reply_markup=build_referral_keyboard(ref_link, lang))
        return

    if action == "export_csv":
        await callback.answer("CSV... 📥")
        csv_file = await generate_user_csv_report(session, db_user)
        if csv_file is None:
            await callback.message.answer(t("export_empty", lang))
            return
        await callback.message.answer_document(
            document=csv_file,
            caption=t("export_ready", lang),
        )
        return

    if action == "marketplaces":
        await callback.answer()
        await _smart_render(
            callback,
            get_marketplaces_info_text(lang),
            reply_markup=build_marketplaces_keyboard(lang),
        )
        return

    if action == "my_tracking":
        await callback.answer()
        page = max(1, callback_data.page or 1)
        text, kb = await render_my_tracking_text_and_kb(session, db_user, page=page)
        await _smart_render(callback, text, reply_markup=kb)
        return

    if action == "stats":
        await callback.answer("📊")
        text = await render_user_stats_text(session, db_user)
        await _smart_render(callback, text, reply_markup=build_analytics_keyboard(lang))
        return

    if action == "confirm_clear":
        items_count = await crud.count_user_subscriptions(session, db_user.id)
        if items_count == 0:
            await callback.answer(t("list_empty_toast", lang), show_alert=True)
            return
        await callback.answer()
        await _smart_render(
            callback,
            render_clear_confirm_card(items_count, lang),
            reply_markup=build_clear_confirm_keyboard(lang),
        )
        return

    if action == "clear_all":
        deleted_count = await crud.clear_all_user_subscriptions(session, db_user.id)
        await callback.answer(f"🗑 ({deleted_count})")
        text, kb = await render_my_tracking_text_and_kb(session, db_user, page=1)
        await _smart_render(callback, text, reply_markup=kb)
        return

    if action == "refresh_all":
        subscriptions = await crud.get_user_subscriptions(session, db_user.id)
        if not subscriptions:
            await callback.answer(t("list_empty_toast", lang), show_alert=True)
            return

        await callback.answer(t("refreshing_prices_toast", lang))
        status = await callback.message.answer(
            t("checking_count_msg", lang, count=len(subscriptions))
        )
        updated = 0
        for sub in subscriptions:
            try:
                scraped = await scraper_service.scrape(sub.product.url)
                await crud.record_checked_price(
                    session,
                    product=sub.product,
                    new_price=scraped.price,
                    new_title=scraped.title,
                    new_image_url=scraped.image_url,
                )
                updated += 1
            except Exception:
                continue

        text, kb = await render_my_tracking_text_and_kb(session, db_user)
        await status.edit_text(
            f"{t('updated_count_msg', lang, updated=updated, total=len(subscriptions))}\n\n{text}",
            reply_markup=kb,
        )


async def _render_product_card_view(
    session: AsyncSession,
    db_user: User,
    product_id: int,
    status_header: str | None = None,
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Формує картку товару та клавіатуру для відображення або повернення до картки."""
    lang = db_user.language
    prod_with_hist = await crud.get_product_with_history(session, product_id)
    if prod_with_hist is None:
        return None
    sub = await crud.get_user_subscription(session, db_user.id, product_id)
    cur_dec = Decimal(str(prod_with_hist.current_price))
    history_prices = (
        [Decimal(str(h.price)) for h in prod_with_hist.price_history]
        if prod_with_hist.price_history
        else [cur_dec]
    )
    max_hist = max(history_prices) if history_prices else cur_dec
    inferred_old_price = max_hist if max_hist > cur_dec else None
    default_header = (
        t("status_tracking_on", lang)
        if sub is not None
        else {
            "uk": "🔥 <b>Гаряча знижка з ТОП-5</b> (натисніть 🎯, щоб відстежувати)",
            "en": "🔥 <b>Top-5 Hot Deal</b> (tap 🎯 to start tracking)",
            "ru": "🔥 <b>Горячая скидка из ТОП-5</b> (нажмите 🎯, чтобы отслеживать)",
        }.get(lang, t("status_tracking_on", lang))
    )
    caption = build_product_caption_with_30d_stats(
        title=prod_with_hist.title,
        marketplace=prod_with_hist.marketplace,
        currency=prod_with_hist.currency,
        current_price=cur_dec,
        old_price=inferred_old_price,
        is_available=getattr(prod_with_hist, "is_available", True),
        history_prices=history_prices,
        target_price=sub.target_price if sub else None,
        category_tag=getattr(sub, "category_tag", None) if sub else None,
        image_url=prod_with_hist.image_url,
        product_url=prod_with_hist.url,
        status_header=status_header or default_header,
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
        show_back=True,
    )
    return caption, kb


@router.callback_query(SearchCallback.filter())
async def handle_search_callback(
    callback: CallbackQuery,
    callback_data: SearchCallback,
    session: AsyncSession,
    db_user: User,
) -> None:
    """
    Обробляє 1-клік додавання товару з результатів текстового пошуку за назвою («➕ Стежити»).
    """
    lang = db_user.language
    if callback.message is None:
        await callback.answer()
        return

    if callback_data.action == "prompt":
        await callback.answer()
        await _smart_render(
            callback,
            render_add_product_guide_card(lang),
            reply_markup=build_add_guide_keyboard(lang),
        )
        return

    if callback_data.action == "track":
        offer = get_cached_search_offer(callback_data.token)
        if offer is None:
            msg = {
                "uk": "⏳ Результати пошуку застаріли. Просто напишіть назву товару в чат ще раз!",
                "en": "⏳ Search results expired. Type the product name in chat again!",
                "ru": "⏳ Результаты поиска устарели. Напишите название товара в чат ещё раз!",
            }.get(lang, "")
            await callback.answer(msg, show_alert=True)
            return

        current_count = await crud.count_user_subscriptions(session, db_user.id)
        max_allowed = settings.pro_max_products if db_user.has_active_pro else settings.free_max_products
        if current_count >= max_allowed:
            await callback.answer(
                f"⚠️ Ліміт тарифу ({max_allowed}). Перейдіть на ⭐️ PRO!",
                show_alert=True,
            )
            return

        await callback.answer("➕ Додаю у відстеження...")
        product = await crud.upsert_product_with_initial_history(
            session,
            clean_url=offer.url,
            title=offer.title,
            image_url=offer.image_url,
            price=offer.price,
            currency=offer.currency,
            marketplace=offer.marketplace,
            old_price=offer.old_price,
            is_available=offer.is_available,
        )
        await crud.subscribe_user_to_product(session, user_id=db_user.id, product_id=product.id)

        rendered = await _render_product_card_view(
            session,
            db_user,
            product.id,
            status_header=t("status_tracking_on", lang),
        )
        if rendered:
            caption, kb = rendered
            await _smart_render(callback, caption, reply_markup=kb)


@router.callback_query(WishlistCallback.filter())
async def handle_wishlist_callback(
    callback: CallbackQuery,
    callback_data: WishlistCallback,
    session: AsyncSession,
    db_user: User,
) -> None:
    """
    Обробляє розумні добірки / вішлісти («💻 Збірка ПК», «🎁 Подарунки»):
    - вибір категорії для товару (pick)
    - збереження категорії (set)
    - фільтрація списку «📦 Мої товари» за категорією (filter)
    - шеринг вішліста з друзями (share)
    """
    lang = db_user.language
    action = callback_data.action
    product_id = callback_data.product_id
    tag = callback_data.tag

    if callback.message is None:
        await callback.answer()
        return

    if action == "pick":
        sub = await crud.get_user_subscription(session, db_user.id, product_id)
        if sub is None:
            await crud.subscribe_user_to_product(session, db_user.id, product_id)
            sub = await crud.get_user_subscription(session, db_user.id, product_id)
        current_tag = getattr(sub, "category_tag", None) if sub else None
        await callback.answer("📁")
        hdr = {
            "uk": (
                "📁 <b>РОЗУМНІ ДОБІРКИ · ВІШЛІСТИ</b>\n"
                "──────────────────────────────────\n"
                "Оберіть добірку для цього товару. Бот автоматично порахує загальну вартість усього списку сьогодні та скільки ви зекономите на мінімумі цін:"
            ),
            "en": (
                "📁 <b>SMART WISHLISTS & COLLECTIONS</b>\n"
                "──────────────────────────────────\n"
                "Choose a collection for this item. The bot will calculate total list value today vs 30-day lows:"
            ),
            "ru": (
                "📁 <b>УМНЫЕ ПОДБОРКИ · ВИШЛИСТЫ</b>\n"
                "──────────────────────────────────\n"
                "Выберите подборку для этого товара. Бот посчитает общую стоимость списка сегодня и экономию на минимумах:"
            ),
        }.get(lang, "")
        await _smart_render(
            callback,
            hdr,
            reply_markup=build_category_picker_keyboard(product_id, current_tag=current_tag, lang=lang),
        )
        return

    if action == "set":
        new_tag = None if tag == "none" else tag
        await crud.subscribe_user_to_product(session, db_user.id, product_id)
        await crud.update_subscription_category(session, db_user.id, product_id, new_tag)
        badge = format_category_badge(new_tag, lang) if new_tag else (
            {"uk": "Без категорії", "en": "No category", "ru": "Без категории"}.get(lang, "")
        )
        await callback.answer(f"📁 {badge}")
        rendered = await _render_product_card_view(
            session,
            db_user,
            product_id,
            status_header=f"📁 <b>Додано у добірку:</b> {badge}",
        )
        if rendered:
            caption, kb = rendered
            await _smart_render(callback, caption, reply_markup=kb)
        return

    if action == "filter":
        await callback.answer("📁")
        text, kb = await render_my_tracking_text_and_kb(
            session,
            db_user,
            page=1,
            category_tag=None if tag == "all" else tag,
        )
        await _smart_render(callback, text, reply_markup=kb)
        return

    if action == "share":
        await callback.answer("📤")
        bot_username = "PriceTrackerBot"
        if callback.bot:
            try:
                me = await callback.bot.get_me()
                if me.username:
                    bot_username = me.username
            except Exception:
                pass
        share_url = f"https://t.me/{bot_username}?start=wishlist_{db_user.id}"
        from urllib.parse import quote
        from aiogram.types import InlineKeyboardButton
        share_tg = f"https://t.me/share/url?url={quote(share_url)}&text={quote('🎁 Мій вішліст товарів зі знижками у Price Tracker:')}"
        msg = {
            "uk": (
                "📤 <b>ПОДІЛИТИСЯ ВІШЛІСТОМ З ДРУЗЯМИ</b>\n"
                "──────────────────────────────────\n"
                f"🔗 <b>Ваше посилання на вішліст:</b>\n<code>{share_url}</code>\n\n"
                "✨ Друг зможе переглянути всі ваші товари, актуальні ціни, загальну суму кошика та додати будь-який товар собі в 1 клік!"
            ),
            "en": (
                "📤 <b>SHARE WISHLIST WITH FRIENDS</b>\n"
                "──────────────────────────────────\n"
                f"🔗 <b>Your wishlist link:</b>\n<code>{share_url}</code>\n\n"
                "✨ Friends can view your items, live prices, total cart value and copy any item in 1 click!"
            ),
            "ru": (
                "📤 <b>ПОДЕЛИТЬСЯ ВИШЛИСТОМ С ДРУЗЬЯМИ</b>\n"
                "──────────────────────────────────\n"
                f"🔗 <b>Ваша ссылка на вишлист:</b>\n<code>{share_url}</code>\n\n"
                "✨ Друг увидит ваш список товаров, актуальные цены, общую сумму и сможет добавить любой товар в 1 клик!"
            ),
        }.get(lang, "")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📲 Надіслати вішліст другу", url=share_tg)],
                [
                    InlineKeyboardButton(
                        text="⬅️ До списку",
                        callback_data=OnboardingCallback(action="my_tracking", page=1).pack(),
                    )
                ],
            ]
        )
        await _smart_render(callback, msg, reply_markup=kb)
        return


@router.callback_query(ProductActionCallback.filter())
async def handle_product_action(
    callback: CallbackQuery,
    callback_data: ProductActionCallback,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
) -> None:
    """Обробляє інлайн-кнопки картки товару, швидкі пресети бажаної ціни (-5%, -10%, -15%) та графіки."""
    product_id = callback_data.product_id
    action = callback_data.action
    lang = db_user.language

    if action == "cancel_target":
        await state.clear()
        await callback.answer(t("action_cancelled_toast", lang))
        rendered = await _render_product_card_view(session, db_user, product_id)
        if rendered:
            caption, kb = rendered
            await _smart_render(callback, caption, reply_markup=kb)
        elif callback.message:
            await _smart_render(callback, t("action_cancelled_msg", lang))
        return

    if action == "compare":
        await callback.answer("⚖️")
        if callback.message:
            text, kb = await render_quick_compare_card(session, db_user)
            await _smart_render(callback, text, reply_markup=kb)
        return

    if action == "ai_forecast":
        await callback.answer("🧠 AI-Прогноз")
        text, kb = await render_ai_price_forecast_card(session, db_user, product_id)
        await _smart_render(callback, text, reply_markup=kb)
        return

    if action == "smart_target":
        await state.clear()
        prod_with_hist = await crud.get_product_with_history(session, product_id)
        if prod_with_hist is None:
            await callback.answer(t("product_not_found_msg", lang), show_alert=True)
            return

        await crud.subscribe_user_to_product(session, db_user.id, product_id)
        cur_dec = Decimal(str(prod_with_hist.current_price))
        history_prices = (
            [Decimal(str(h.price)) for h in prod_with_hist.price_history]
            if prod_with_hist.price_history
            else [cur_dec]
        )
        optimal_target = compute_optimal_target_price(cur_dec, history_prices)
        await crud.update_target_price(session, db_user.id, product_id, optimal_target)
        formatted_target = fmt_money(optimal_target, prod_with_hist.currency)
        await callback.answer(f"🧠 AI-ціль → {formatted_target}")

        if callback.message:
            rendered = await _render_product_card_view(
                session,
                db_user,
                product_id,
                status_header=f"🧠 <b>AI-ціль встановлено:</b> <code>{formatted_target}</code>",
            )
            if rendered:
                caption, kb = rendered
                try:
                    if callback.message.photo:
                        await callback.message.edit_caption(caption=caption, reply_markup=kb)
                    else:
                        await callback.message.edit_text(
                            text=caption,
                            reply_markup=kb,
                            link_preview_options=build_image_preview_options(text=caption),
                        )
                except Exception:
                    await callback.message.answer(
                        caption,
                        reply_markup=kb,
                        link_preview_options=build_image_preview_options(text=caption),
                    )
        return

    # Швидкий вибір відсотка знижки (-5%, -10%, -15%) одним кліком з картки товару
    if action == "set_target_pct":
        await state.clear()
        pct = callback_data.percent
        prod_with_hist = await crud.get_product_with_history(session, product_id)
        if prod_with_hist is None:
            await callback.answer(t("product_not_found_msg", lang), show_alert=True)
            return

        # Автоматично підписуємо користувача, якщо він клікнув із вітрини гарячих знижок
        await crud.subscribe_user_to_product(session, db_user.id, product_id)

        cur_dec = Decimal(str(prod_with_hist.current_price))
        quant = Decimal("1") if cur_dec >= Decimal("100") else Decimal("0.01")
        factor = Decimal(str(max(1, 100 - pct))) / Decimal("100")
        new_target = max(Decimal("0.01"), (cur_dec * factor).quantize(quant))

        await crud.update_target_price(session, db_user.id, product_id, new_target)
        formatted_target = fmt_money(new_target, prod_with_hist.currency)
        await callback.answer(f"🎯 -{pct}% → {formatted_target}")

        if callback.message:
            rendered = await _render_product_card_view(
                session,
                db_user,
                product_id,
                status_header=t("target_set_ok", lang, price=formatted_target),
            )
            if rendered:
                caption, kb = rendered
                try:
                    if callback.message.photo:
                        await callback.message.edit_caption(caption=caption, reply_markup=kb)
                    else:
                        await callback.message.edit_text(
                            text=caption,
                            reply_markup=kb,
                            link_preview_options=build_image_preview_options(text=caption),
                        )
                except Exception:
                    await callback.message.answer(
                        caption,
                        reply_markup=kb,
                        link_preview_options=build_image_preview_options(text=caption),
                    )
        return

    if action == "card":
        rendered = await _render_product_card_view(session, db_user, product_id)
        if rendered is None or callback.message is None:
            await callback.answer(t("product_not_found_msg", lang), show_alert=True)
            return

        await callback.answer()
        caption, kb = rendered
        await _smart_render(callback, caption, reply_markup=kb)
        return

    if action == "confirm_delete":
        product = await crud.get_product_by_id(session, product_id)
        if product is None or callback.message is None:
            await callback.answer(t("product_not_found_msg", lang), show_alert=True)
            return
        await callback.answer()
        await _smart_render(
            callback,
            render_delete_confirm_card(product.title, lang),
            reply_markup=build_delete_confirm_keyboard(product.id, lang),
        )
        return

    if action == "delete":
        deleted = await crud.unsubscribe_user_from_product(session, db_user.id, product_id)
        if deleted:
            await callback.answer(t("item_deleted_toast", lang))
            text, kb = await render_my_tracking_text_and_kb(session, db_user, page=1)
            await _smart_render(callback, text, reply_markup=kb)
        else:
            await callback.answer(t("already_deleted_toast", lang), show_alert=True)
        return

    if action == "set_target":
        await state.clear()
        val_cents = callback_data.value
        new_target = (Decimal(str(val_cents)) / Decimal("100")).quantize(Decimal("0.01")) if val_cents > 0 else None
        await crud.subscribe_user_to_product(session, db_user.id, product_id)
        sub = await crud.update_target_price(session, db_user.id, product_id, new_target)
        if sub is None:
            await callback.answer(t("product_not_in_list", lang), show_alert=True)
            return

        product = await crud.get_product_by_id(session, product_id)
        curr = product.currency if product else "UAH"

        if new_target is None:
            await callback.answer(t("target_reset_toast", lang))
            header = t("target_reset_msg", lang)
        else:
            formatted_target = fmt_money(new_target, curr)
            await callback.answer(f"🎯 {formatted_target}")
            header = t("target_set_ok", lang, price=formatted_target)

        rendered = await _render_product_card_view(session, db_user, product_id, status_header=header)
        if rendered:
            caption, kb = rendered
            await _smart_render(callback, caption, reply_markup=kb)
        elif callback.message:
            await _smart_render(callback, header)
        return

    if action in {"target", "custom_target"}:
        product = await crud.get_product_by_id(session, product_id)
        if product is None:
            await callback.answer(t("product_not_found_msg", lang), show_alert=True)
            return

        sub = await crud.get_user_subscription(session, db_user.id, product_id)
        await state.set_state(TargetPriceState.waiting_for_price)
        await state.update_data(product_id=product_id)
        await callback.answer()
        if callback.message:
            cur_dec = Decimal(str(product.current_price))
            kb = build_target_price_presets_keyboard(
                product_id=product.id,
                current_price=cur_dec,
                currency=product.currency,
                lang=lang,
            )
            card_text = render_target_price_card(
                title=product.title,
                currency=product.currency,
                current_price=cur_dec,
                current_target=sub.target_price if sub else None,
                lang=lang,
            )
            await _smart_render(callback, card_text, reply_markup=kb)
        return

    if action == "analyze":
        prod_with_hist = await crud.get_product_with_history(session, product_id)
        if prod_with_hist is None or callback.message is None:
            await callback.answer(t("product_not_found_msg", lang), show_alert=True)
            return

        await callback.answer()
        prices = [Decimal(str(h.price)) for h in prod_with_hist.price_history] or [
            Decimal(str(prod_with_hist.current_price))
        ]
        cur = Decimal(str(prod_with_hist.current_price))
        min_p = min(prices)
        max_p = max(prices)
        avg_quant = Decimal("1") if cur >= Decimal("100") else Decimal("0.01")
        avg_p = (sum(prices) / Decimal(len(prices))).quantize(avg_quant)

        await callback.message.answer(
            t(
                "chart_caption",
                lang,
                mp_label=format_marketplace_label(prod_with_hist.marketplace),
                title=html.escape(prod_with_hist.title[:50]),
                cur=fmt_money(cur, prod_with_hist.currency),
                min_p=fmt_money(min_p, prod_with_hist.currency),
                avg_p=fmt_money(avg_p, prod_with_hist.currency),
                max_p=fmt_money(max_p, prod_with_hist.currency),
            )
        )
        return

    if action == "refresh":
        product = await crud.get_product_by_id(session, product_id)
        if product is None or callback.message is None:
            await callback.answer(t("product_not_found_msg", lang), show_alert=True)
            return

        await callback.answer(t("refreshing_prices_toast", lang))
        try:
            scraped = await scraper_service.scrape(product.url)
        except Exception:
            await callback.message.answer(t("refresh_failed_msg", lang))
            return

        await crud.record_checked_price(
            session,
            product=product,
            new_price=scraped.price,
            new_title=scraped.title,
            new_image_url=scraped.image_url,
        )
        prod_with_hist = await crud.get_product_with_history(session, product.id)
        sub = await crud.get_user_subscription(session, db_user.id, product.id)
        history_prices = (
            [Decimal(str(h.price)) for h in prod_with_hist.price_history]
            if prod_with_hist and prod_with_hist.price_history
            else [scraped.price]
        )
        cur_dec = Decimal(str(product.current_price))

        caption = build_product_caption_with_30d_stats(
            title=product.title,
            marketplace=product.marketplace,
            currency=product.currency,
            current_price=cur_dec,
            old_price=scraped.old_price,
            is_available=scraped.is_available,
            history_prices=history_prices,
            target_price=sub.target_price if sub else None,
            image_url=product.image_url or scraped.image_url,
            product_url=product.url,
            status_header=t("status_tracking_updated", lang),
            lang=lang,
        )
        kb = build_product_card_keyboard(
            product_id=product.id,
            clean_url=product.url,
            user_id=db_user.id,
            marketplace=product.marketplace,
            title=product.title,
            price_str=fmt_money(cur_dec, product.currency),
            old_price_str=fmt_money(scraped.old_price, product.currency) if scraped.old_price else "",
            lang=lang,
        )
        await _smart_render(callback, caption, reply_markup=kb)
        return

    if action == "chart":
        await callback.answer(t("building_chart_toast", lang))
        sub = await crud.get_user_subscription(session, db_user.id, product_id)
        target_price = sub.target_price if sub else None

        product = await crud.get_product_with_history(session, product_id)
        if product is None or callback.message is None:
            if callback.message:
                await callback.message.answer(t("product_not_found_msg", lang))
            return

        prices = [Decimal(str(entry.price)) for entry in product.price_history] or [
            Decimal(str(product.current_price))
        ]
        cur = Decimal(str(product.current_price))
        min_p = min(prices)
        max_p = max(prices)
        avg_quant = Decimal("1") if cur >= Decimal("100") else Decimal("0.01")
        avg_p = (sum(prices) / Decimal(len(prices))).quantize(avg_quant)

        short_title = html.escape(product.title if len(product.title) <= 48 else f"{product.title[:47]}…")
        caption = t(
            "chart_caption",
            lang,
            mp_label=format_marketplace_label(product.marketplace),
            title=short_title,
            cur=fmt_money(cur, product.currency),
            min_p=fmt_money(min_p, product.currency),
            avg_p=fmt_money(avg_p, product.currency),
            max_p=fmt_money(max_p, product.currency),
        )
        kb = build_product_card_keyboard(
            product_id=product.id,
            clean_url=product.url,
            user_id=db_user.id,
            marketplace=product.marketplace,
            title=product.title,
            price_str=fmt_money(cur, product.currency),
            lang=lang,
        )

        cache_key = (
            product.id,
            str(cur),
            len(product.price_history),
            str(target_price) if target_price is not None else "",
            lang,
        )
        cached_file_id = _CHART_FILE_ID_CACHE.get(cache_key)
        if cached_file_id:
            _CHART_FILE_ID_CACHE.move_to_end(cache_key)
            try:
                await callback.message.answer_photo(photo=cached_file_id, caption=caption, reply_markup=kb)
                return
            except Exception:
                _CHART_FILE_ID_CACHE.pop(cache_key, None)

        chart_buf = await generate_price_chart(
            session=session,
            product_id=product_id,
            target_price=target_price,
            lang=lang,
            product=product,
        )
        if chart_buf is None:
            await callback.message.answer(t("product_not_found_msg", lang))
            return

        try:
            photo_file = BufferedInputFile(
                chart_buf.getvalue(),
                filename=f"chart_30d_{product_id}.png",
            )
            sent_msg = await callback.message.answer_photo(photo=photo_file, caption=caption, reply_markup=kb)
            if sent_msg.photo:
                _CHART_FILE_ID_CACHE[cache_key] = sent_msg.photo[-1].file_id
                if len(_CHART_FILE_ID_CACHE) > _CHART_CACHE_MAX_SIZE:
                    _CHART_FILE_ID_CACHE.popitem(last=False)
        finally:
            chart_buf.close()
