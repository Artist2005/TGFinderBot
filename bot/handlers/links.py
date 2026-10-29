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
from bot.keyboards.inline import build_pro_offer_keyboard, build_product_card_keyboard
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
    build_product_caption_with_30d_stats,
    compute_deal_score,
    render_my_tracking_text_and_kb,
    render_pro_offer_card,
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
    normalize_url,
    scraper_service,
)
from services.search import SearchOffer, _cache_offer, normalize_search_query, search_products_by_name

router = Router(name="links_router")


class TargetPriceState(StatesGroup):
    """FSM-стан для введення бажаної ціни."""

    waiting_for_price = State()


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
    await message.answer(caption, reply_markup=kb)


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

    await status_msg.edit_text(caption, reply_markup=kb)


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
    lang = db_user.language
    query = (inline_query.query or "").strip()

    bot_username = "PriceTrackerBot"
    if inline_query.bot:
        try:
            me = await inline_query.bot.get_me()
            if me.username:
                bot_username = me.username
        except Exception:
            pass

    results: list[InlineQueryResultArticle] = []

    if len(query) >= 2:
        offers = await search_products_by_name(session, query, limit=8)
    else:
        # Якщо користувач просто ввів @BotName без тексту — показуємо гарячі знижки з БД
        db_prods = await crud.search_products_in_db(session, "a", limit=6)
        if not db_prods:
            drops = await crud.get_top_price_drops(session, user_id=db_user.id, limit=5, force_live=False)
            db_prods = [d["product"] for d in drops]
        from services.search import _cache_offer, SearchOffer
        offers = [
            _cache_offer(
                SearchOffer(
                    token="",
                    title=p.title,
                    url=p.url,
                    price=Decimal(str(p.current_price)),
                    old_price=None,
                    currency=p.currency,
                    marketplace=p.marketplace,
                    is_available=getattr(p, "is_available", True),
                    image_url=p.image_url,
                    product_id=p.id,
                )
            )
            for p in db_prods[:6]
        ]

    for idx, off in enumerate(offers[:8], start=1):
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

        deep_link = (
            f"https://t.me/{bot_username}?start=track_{off.product_id}"
            if off.product_id
            else f"https://t.me/{bot_username}?start=ref_{db_user.id}"
        )
        btn_track_lbl = {
            "uk": "📉 Відстежувати ціну в боті",
            "en": "📉 Track Price in Bot",
            "ru": "📉 Отслеживать цену в боте",
        }.get(lang, "📉 Відстежувати ціну в боті")
        btn_store_lbl = {
            "uk": "🛒 Відкрити в магазині",
            "en": "🛒 Open in Store",
            "ru": "🛒 Открыть в магазине",
        }.get(lang, "🛒 Відкрити в магазині")

        msg_text = (
            f"🛍 <b>{html.escape(off.title[:85])}</b>\n"
            f"──────────────────────────────────\n"
            f"🏪 <b>Магазин:</b> {mp_label}\n"
            f"💰 <b>Ціна зараз:</b> <code>{price_fmt}</code>{old_part}\n"
            f"⚖️ <b>Deal Score:</b> <code>{score}/100</code>\n"
            f"──────────────────────────────────\n"
            f"🤖 <i>Знайдено через @{bot_username} — розумний моніторинг знижок 24/7</i>"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=btn_track_lbl, url=deep_link)],
                [InlineKeyboardButton(text=btn_store_lbl, url=off.url)],
            ]
        )

        results.append(
            InlineQueryResultArticle(
                id=f"offer_{idx}_{off.token}",
                title=f"{price_fmt} · {off.title[:48]}",
                description=f"{mp_label} · Deal Score: {score}/100 · Натисніть, щоб надіслати в чат",
                thumbnail_url=off.image_url if (off.image_url and off.image_url.startswith("http")) else None,
                input_message_content=InputTextMessageContent(
                    message_text=msg_text,
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                ),
                reply_markup=kb,
            )
        )

    await inline_query.answer(results, cache_time=60, is_personal=True)


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
    query = " ".join(raw_text.split())
    if len(query) < 2 or len(query) > 120:
        return

    searching_txt = {
        "uk": f"🔍 <b>Шукаю найнижчі ціни на «{html.escape(query[:45])}» у магазинах...</b>",
        "en": f"🔍 <b>Searching lowest prices for «{html.escape(query[:45])}»...</b>",
        "ru": f"🔍 <b>Ищу самые низкие цены на «{html.escape(query[:45])}» в магазинах...</b>",
    }.get(lang, "")

    status_msg = await message.answer(searching_txt)
    norm_q = normalize_search_query(query) or query
    offers = await search_products_by_name(session, query, limit=6)
    display_q = norm_q if norm_q.lower() != query.lower() else query
    text, kb = render_search_results_card(display_q, offers, lang=lang)
    await status_msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
