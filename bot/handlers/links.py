from __future__ import annotations

from decimal import Decimal, InvalidOperation
import html
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.i18n import fmt_money, fmt_money_with_uah, t
from bot.keyboards.inline import (
    build_pro_offer_keyboard,
    build_product_card_keyboard,
    build_promo_input_keyboard,
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
)
from config import settings
from database import crud
from database.models import User
from services.analytics import (
    build_image_preview_options,
    build_product_caption_with_30d_stats,
    compute_deal_score,
    embed_invisible_image_link,
    render_my_tracking_text_and_kb,
    render_pro_offer_card,
    render_promo_success_card,
    render_search_results_card,
)
from services.scraper import (
    InvalidUrlError,
    ParsingStructureError,
    ProductNotFoundError,
    ProductOutOfStockError,
    RateLimitOrBlockedError,
    ScraperError,
    SSRFBlockedError,
    format_marketplace_label,
    format_marketplace_name,
    normalize_url,
    scraper_service,
)
from services.search import SearchOffer, _cache_offer, normalize_search_query, search_products_by_name

router = Router(name="links_router")
_CACHED_BOT_USERNAME: str | None = None


class TargetPriceState(StatesGroup):
    """FSM-стан для введення бажаної ціни."""

    waiting_for_price = State()


class PromoCodeState(StatesGroup):
    """FSM-стан для введення промокоду на довічний тариф ⭐️ PRO."""

    waiting_for_code = State()


@router.message(PromoCodeState.waiting_for_code, F.text)
async def handle_promo_code_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Обробляє введений користувачем промокод після натискання «🎟 Ввести промокод» або /promo."""
    lang = db_user.language
    raw_text = (message.text or "").strip()

    if (
        raw_text.lower() in {"/cancel", "скасувати", "відміна", "отмена", "cancel"}
        or "http://" in raw_text
        or "https://" in raw_text
    ):
        await state.clear()
        if "http://" not in raw_text and "https://" not in raw_text:
            await message.answer(t("cancelled", lang))
        return

    updated_user = await crud.redeem_promo_code(session, db_user.id, raw_text)
    if updated_user is not None:
        await state.clear()
        items_count = await crud.count_user_subscriptions(session, updated_user.id)
        await message.answer(
            render_promo_success_card(updated_user, raw_text, items_count=items_count),
            reply_markup=build_start_keyboard(lang, items_count=items_count),
        )
        return

    invalid_msg = {
        "uk": (
            "❌ <b>Промокод не знайдено або він недійсний.</b>\n"
            "Перевірте правильність написання та надішліть промокод ще раз або поверніться до меню:"
        ),
        "en": (
            "❌ <b>Invalid promo code.</b>\n"
            "Please check the spelling and send the code again, or return to the menu:"
        ),
        "ru": (
            "❌ <b>Промокод не найден или недействителен.</b>\n"
            "Проверьте правильность ввода и отправьте промокод ещё раз или вернитесь в меню:"
        ),
    }.get(lang, "")
    await message.answer(invalid_msg, reply_markup=build_promo_input_keyboard(lang))


@router.message(TargetPriceState.waiting_for_price, F.text)
async def handle_target_price_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Обробляє введену вручну бажану ціну («✍️ Своя ціна»)."""
    lang = db_user.language
    raw_text = (message.text or "").strip().replace(" ", "").replace(",", ".")
    if raw_text.lower() in {"/cancel", "скасувати", "відміна", "отмена", "cancel"}:
        await state.clear()
        await message.answer(t("cancelled", lang))
        return

    try:
        target_val = Decimal(raw_text).quantize(Decimal("0.01"))
        if target_val <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer(t("enter_valid_number", lang))
        return

    data = await state.get_data()
    product_id: int | None = data.get("product_id")
    await state.clear()

    if product_id is None:
        await message.answer(t("product_not_found_msg", lang))
        return

    sub = await crud.update_target_price(session, db_user.id, product_id, target_val)
    if sub is None:
        await message.answer(t("product_not_in_list", lang))
        return

    prod_with_hist = await crud.get_product_with_history(session, product_id)
    if prod_with_hist is None:
        await message.answer(t("product_not_found_msg", lang))
        return

    curr = prod_with_hist.currency or "UAH"
    cur_dec = Decimal(str(prod_with_hist.current_price))
    history_prices = (
        [Decimal(str(h.price)) for h in prod_with_hist.price_history]
        if prod_with_hist.price_history
        else [cur_dec]
    )
    formatted_target = fmt_money(target_val, curr)
    caption = build_product_caption_with_30d_stats(
        title=prod_with_hist.title,
        marketplace=prod_with_hist.marketplace,
        currency=curr,
        current_price=cur_dec,
        old_price=None,
        is_available=True,
        history_prices=history_prices,
        target_price=target_val,
        image_url=prod_with_hist.image_url,
        product_url=prod_with_hist.url,
        status_header=t("target_set_ok", lang, price=formatted_target),
        lang=lang,
    )
    kb = build_product_card_keyboard(
        product_id=prod_with_hist.id,
        clean_url=prod_with_hist.url,
        user_id=db_user.id,
        marketplace=prod_with_hist.marketplace,
        title=prod_with_hist.title,
        price_str=fmt_money(cur_dec, curr),
        lang=lang,
    )
    await message.answer(
        caption,
        reply_markup=kb,
        link_preview_options=build_image_preview_options(text=caption),
    )


@router.message(F.text.regexp(r"https?://\S+"))
async def handle_product_link(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """
    Обробляє одне або декілька посилань на товари (до 10 URL одночасно — пакетне додавання PRO),
    а також автоматично очищує посилання із пересланих повідомлень і рекламних постів.
    """
    lang = db_user.language
    raw_text = message.text or ""
    raw_urls = re.findall(r"https?://[^\s<>\"']+", raw_text)
    if not raw_urls:
        return

    # Очищуємо пунктуацію в кінці URL та видаляємо дублікати зі збереженням порядку
    extracted_urls: list[str] = []
    for u in raw_urls:
        cleaned_u = u.rstrip(").,;!?>]\"'")
        if cleaned_u and cleaned_u not in extracted_urls:
            extracted_urls.append(cleaned_u)

    # Пакетний імпорт, якщо надіслано 2+ посилань в одному повідомленні
    if len(extracted_urls) > 1:
        batch_urls = extracted_urls[:10]
        status_msg = await message.answer(
            f"📥 <b>Пакетний імпорт:</b> обробляю <code>{len(batch_urls)}</code> посилань..."
        )
        added_count = 0
        skipped_limit = False

        for url_candidate in batch_urls:
            try:
                clean_url, _ = normalize_url(url_candidate)
            except (InvalidUrlError, SSRFBlockedError):
                continue

            existing_product = await crud.get_product_by_url(session, clean_url)
            already_subscribed = False
            if existing_product is not None:
                existing_sub = await crud.get_user_subscription(session, db_user.id, existing_product.id)
                already_subscribed = existing_sub is not None

            if not already_subscribed:
                current_count = await crud.count_user_subscriptions(session, db_user.id)
                max_allowed = settings.pro_max_products if db_user.has_active_pro else settings.free_max_products
                if current_count >= max_allowed:
                    skipped_limit = True
                    break

            try:
                scraped = await scraper_service.scrape(clean_url)
                product = await crud.upsert_product_with_initial_history(
                    session,
                    clean_url=scraped.url,
                    title=scraped.title,
                    image_url=scraped.image_url,
                    price=scraped.price,
                    currency=scraped.currency,
                    marketplace=scraped.marketplace,
                    old_price=scraped.old_price,
                )
                await crud.subscribe_user_to_product(
                    session,
                    user_id=db_user.id,
                    product_id=product.id,
                )
                added_count += 1
            except Exception:
                continue

        text, kb = await render_my_tracking_text_and_kb(session, db_user, page=1)
        limit_note = (
            f"\n⚠️ <i>Досягнуто ліміт тарифу. Активуйте ⭐️ PRO (до {settings.pro_max_products} товарів)!</i>"
            if skipped_limit
            else ""
        )
        await status_msg.edit_text(
            f"✅ <b>Пакетний імпорт завершено:</b> додано <code>{added_count}/{len(batch_urls)}</code> товарів.{limit_note}\n\n{text}",
            reply_markup=kb,
        )
        return

    status_msg = await message.answer(t("loading_product", lang))

    try:
        clean_url, marketplace = normalize_url(extracted_urls[0])
    except (InvalidUrlError, SSRFBlockedError):
        await status_msg.edit_text(t("invalid_url", lang))
        return

    existing_product = await crud.get_product_by_url(session, clean_url)
    already_subscribed = False
    if existing_product is not None:
        existing_sub = await crud.get_user_subscription(session, db_user.id, existing_product.id)
        already_subscribed = existing_sub is not None

    if not already_subscribed:
        current_count = await crud.count_user_subscriptions(session, db_user.id)
        max_allowed = settings.pro_max_products if db_user.has_active_pro else settings.free_max_products

        if current_count >= max_allowed:
            if not db_user.has_active_pro:
                pro_card = await render_pro_offer_card(session, db_user)
                await status_msg.edit_text(
                    f"⚠️ <b>Досягнуто ліміт Free-тарифу ({settings.free_max_products}/{settings.free_max_products} товарів)</b>\n\n{pro_card}",
                    reply_markup=build_pro_offer_keyboard(lang),
                )
            else:
                await status_msg.edit_text(
                    t("limit_pro_reached", lang, pro_max=settings.pro_max_products)
                )
            return

    try:
        scraped = await scraper_service.scrape(clean_url)
    except (InvalidUrlError, SSRFBlockedError):
        await status_msg.edit_text(t("invalid_url", lang))
        return
    except ProductNotFoundError:
        await status_msg.edit_text(t("err_404", lang))
        return
    except ProductOutOfStockError as exc:
        await status_msg.edit_text(f"⚠️ {html.escape(str(exc))}")
        return
    except RateLimitOrBlockedError:
        await status_msg.edit_text(t("err_rate_limit", lang))
        return
    except (ParsingStructureError, ScraperError):
        await status_msg.edit_text(
            t("err_parse", lang, store=format_marketplace_label(marketplace))
        )
        return

    product = await crud.upsert_product_with_initial_history(
        session,
        clean_url=scraped.url,
        title=scraped.title,
        image_url=scraped.image_url,
        price=scraped.price,
        currency=scraped.currency,
        marketplace=scraped.marketplace,
        old_price=scraped.old_price,
        is_available=scraped.is_available,
    )
    sub, is_new_sub = await crud.subscribe_user_to_product(
        session,
        user_id=db_user.id,
        product_id=product.id,
    )

    prod_with_hist = await crud.get_product_with_history(session, product.id)
    history_prices = (
        [Decimal(str(h.price)) for h in prod_with_hist.price_history]
        if prod_with_hist and prod_with_hist.price_history
        else [Decimal(str(product.current_price))]
    )

    cur_dec = Decimal(str(product.current_price))
    if not scraped.is_available or cur_dec <= Decimal("0"):
        sub_status = {
            "uk": "🎯 <b>Режим «Снайпер наявності» увімкнено!</b>",
            "en": "🎯 <b>Back-in-Stock Sniper Activated!</b>",
            "ru": "🎯 <b>Режим «Снайпер наличия» включён!</b>",
        }.get(lang, t("status_tracking_on", lang))
    else:
        sub_status = t("status_tracking_on", lang) if is_new_sub else t("status_tracking_updated", lang)

    caption = build_product_caption_with_30d_stats(
        title=product.title,
        marketplace=product.marketplace,
        currency=product.currency,
        current_price=cur_dec,
        old_price=scraped.old_price,
        is_available=scraped.is_available,
        history_prices=history_prices,
        target_price=sub.target_price,
        category_tag=getattr(sub, "category_tag", None),
        image_url=product.image_url or scraped.image_url,
        product_url=product.url,
        status_header=sub_status,
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
        category_tag=getattr(sub, "category_tag", None),
        lang=lang,
    )

    await status_msg.edit_text(
        caption,
        reply_markup=kb,
        link_preview_options=build_image_preview_options(text=caption),
    )


# Множина всіх відомих текстів кнопок меню, які не повинні запускати пошук товарів
_ALL_MENU_BUTTON_TEXTS = (
    ALL_BTN_ADD_PRODUCT
    | ALL_BTN_ANALYTICS
    | ALL_BTN_HELP
    | ALL_BTN_LANGUAGE
    | ALL_BTN_MARKETPLACES
    | ALL_BTN_MY_PRODUCTS
    | ALL_BTN_PRO_TARIFF
    | ALL_BTN_SETTINGS
    | {"🏪 Маркетплейси", "📊 Статистика та знижки", "ℹ️ Як це працює", "⭐️ Тариф PRO"}
)


@router.inline_query()
async def handle_inline_query(
    inline_query: InlineQuery,
    session: AsyncSession,
    db_user: User,
) -> None:
    """
    💬 КІЛЕР-ФІЧА #4: Вірусний Inline-режим (@BotName запит) для будь-яких чатів та груп.
    Дозволяє миттєво знайти товар і відправити в чат красиву картку з ціною, Deal Score
    та прямою кнопкою «📉 Відстежувати ціну в боті».
    """
    global _CACHED_BOT_USERNAME
    lang = db_user.language
    query = (inline_query.query or "").strip()

    if not _CACHED_BOT_USERNAME and inline_query.bot:
        try:
            me = await inline_query.bot.get_me()
            if me.username:
                _CACHED_BOT_USERNAME = me.username
        except Exception:
            pass
    bot_username = _CACHED_BOT_USERNAME or "clipsnag_bot"

    results: list[InlineQueryResultArticle] = []

    # Якщо користувач натиснув кнопку «📤 Поділитися знахідкою» (де вставляється готовий текст із 🔥 або посиланням)
    if query.startswith("🔥") or "t.me/" in query:
        ref_link = f"https://t.me/{bot_username}?start=ref_{db_user.id}"
        btn_open_lbl = {
            "uk": "📉 Відстежувати знижки в боті (+3д PRO)",
            "en": "📉 Track Deals in Bot (+3d PRO)",
            "ru": "📉 Отслеживать скидки в боте (+3д PRO)",
        }.get(lang, "📉 Відстежувати знижки в боті (+3д PRO)")
        results.append(
            InlineQueryResultArticle(
                id="viral_share_card",
                title="📤 Надіслати рекомендацію в цей чат",
                description=query[:90],
                input_message_content=InputTextMessageContent(
                    message_text=html.escape(query),
                    parse_mode="HTML",
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text=btn_open_lbl, url=ref_link)]]
                ),
            )
        )

    offers: list[SearchOffer] = []
    if len(query) >= 2 and not query.startswith("🔥") and "t.me/" not in query:
        offers = await search_products_by_name(session, query, limit=8)
    else:
        # Якщо користувач щойно набрав @clipsnag_bot (ще без тексту) — показуємо його товари + популярні хіти
        user_subs = await crud.get_user_subscriptions(session, db_user.id)
        for s in user_subs[:4]:
            p = s.product
            offers.append(
                _cache_offer(
                    SearchOffer(
                        token="",
                        title=p.title,
                        url=p.url,
                        marketplace=p.marketplace,
                        price=Decimal(str(p.current_price)),
                        old_price=None,
                        currency=p.currency,
                        is_available=getattr(p, "is_available", True),
                        image_url=p.image_url,
                        product_id=p.id,
                    )
                )
            )
        if len(offers) < 5:
            default_hits = await search_products_by_name(session, "AirPods", limit=6)
            seen_u = {o.url for o in offers}
            for h in default_hits:
                if h.url not in seen_u:
                    seen_u.add(h.url)
                    offers.append(h)

    for idx, off in enumerate(offers[:8], start=1):
        mp_short = format_marketplace_name(off.marketplace)
        mp_label = format_marketplace_label(off.marketplace)
        price_fmt = fmt_money_with_uah(off.price, off.currency)
        old_part = ""
        if off.old_price and off.old_price > off.price:
            pct = max(1, int(round(float((off.old_price - off.price) / off.old_price) * 100)))
            old_part = f" <s>{fmt_money(off.old_price, off.currency)}</s> (<code>-{pct}%</code>)"

        score = compute_deal_score(
            current_price=off.price,
            min_30d=off.price * Decimal("0.95"),
            max_30d=off.old_price or (off.price * Decimal("1.10")),
            avg_30d=off.price,
            old_price=off.old_price,
        )

        if off.product_id:
            deep_link = f"https://t.me/{bot_username}?start=track_{off.product_id}"
        elif off.token:
            deep_link = f"https://t.me/{bot_username}?start=tok_{off.token}"
        else:
            deep_link = f"https://t.me/{bot_username}?start=ref_{db_user.id}"

        btn_track_lbl = {
            "uk": "📉 Відстежувати ціну в боті",
            "en": "📉 Track Price in Bot",
            "ru": "📉 Отслеживать цену в боте",
        }.get(lang, "📉 Відстежувати ціну в боті")
        btn_store_lbl = {
            "uk": "🛒 Відкрити в магазині",
            "en": "🛒 Open in Store",
            "ru": "🛒 Открыть в магазине",
        }.get(lang, "🛒 Відкрити в магазине")

        msg_text = embed_invisible_image_link(
            (
                f"<b>{html.escape(off.title[:85])}</b>\n"
                f"Магазин: <b>{mp_short}</b>\n\n"
                f"Ціна: <b><code>{price_fmt}</code></b>{old_part}\n"
                f"Оцінка вигоди (Deal Score): <code>{score}/100</code>\n\n"
                f"<i>Знайдено через @{bot_username}</i>"
            ),
            off.image_url or off.url,
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=btn_track_lbl, url=deep_link)],
                [InlineKeyboardButton(text=btn_store_lbl, url=off.url)],
            ]
        )

        thumb = off.image_url if (off.image_url and off.image_url.startswith("https://")) else None

        results.append(
            InlineQueryResultArticle(
                id=f"offer_{idx}_{off.token or idx}",
                title=f"{price_fmt} · {mp_short} · {off.title[:42]}",
                description=f"Deal Score: {score}/100 · Натисніть, щоб відправити в чат",
                thumbnail_url=thumb,
                input_message_content=InputTextMessageContent(
                    message_text=msg_text,
                    parse_mode="HTML",
                    link_preview_options=build_image_preview_options(off.image_url, fallback_url=off.url),
                ),
                reply_markup=kb,
            )
        )

    if not results:
        ref_link = f"https://t.me/{bot_username}?start=ref_{db_user.id}"
        results.append(
            InlineQueryResultArticle(
                id="fallback_open_bot",
                title=f"Знайти «{query[:35]}» у @{bot_username}",
                description="Натисніть, щоб відкрити бота та знайти найнижчу ціну",
                input_message_content=InputTextMessageContent(
                    message_text=(
                        f"Пошук цін на <b>{html.escape(query[:60])}</b> у @{bot_username}\n"
                        f"<a href=\"{ref_link}\">Перевірити ціни</a>"
                    ),
                    parse_mode="HTML",
                ),
            )
        )

    try:
        await inline_query.answer(results, cache_time=5, is_personal=True)
    except Exception:
        # Якщо Telegram відхилив якусь іконку чи URL — відправляємо без thumbnail_url
        for r in results:
            r.thumbnail_url = None
        await inline_query.answer(results, cache_time=5, is_personal=True)


@router.message(F.text)
async def handle_text_product_search(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """
    🔍 КІЛЕР-ФІЧА #1: Пошук найнижчої ціни просто за назвою товару (без посилання!).
    Користувач пише у чат текст (наприклад, «AirPods Pro 2» або «PlayStation 5 Slim»),
    бот паралельно шукає пропозиції в магазинах і видає порівняльну таблицю з кнопками «➕ Стежити».
    """
    raw_text = (message.text or "").strip()
    if not raw_text or raw_text.startswith("/"):
        return
    if raw_text in _ALL_MENU_BUTTON_TEXTS or raw_text.startswith(("📦 Мої товари", "📦 My Items", "📦 Мои товары")):
        return
    if "http://" in raw_text or "https://" in raw_text:
        return

    lang = db_user.language
    if raw_text.upper() in settings.valid_promo_codes:
        updated_user = await crud.redeem_promo_code(session, db_user.id, raw_text)
        if updated_user is not None:
            items_count = await crud.count_user_subscriptions(session, updated_user.id)
            await message.answer(
                render_promo_success_card(updated_user, raw_text, items_count=items_count),
                reply_markup=build_start_keyboard(lang, items_count=items_count),
            )
            return

    query = " ".join(raw_text.split())
    if len(query) < 2 or len(query) > 120:
        return

    searching_txt = {
        "uk": f"🔎 Шукаю «<b>{html.escape(query[:45])}</b>»...",
        "en": f"🔎 Searching «<b>{html.escape(query[:45])}</b>»...",
        "ru": f"🔎 Ищу «<b>{html.escape(query[:45])}</b>»...",
    }.get(lang, "")

    status_msg = await message.answer(searching_txt)
    norm_q = normalize_search_query(query) or query
    offers = await search_products_by_name(session, query, limit=6)
    display_q = norm_q if norm_q.lower() != query.lower() else query
    text, kb = render_search_results_card(display_q, offers, lang=lang)
    await status_msg.edit_text(
        text,
        reply_markup=kb,
        link_preview_options=build_image_preview_options(text=text),
    )
