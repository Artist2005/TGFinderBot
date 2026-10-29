from __future__ import annotations

from decimal import Decimal, InvalidOperation
import html
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.i18n import fmt_money, t
from bot.keyboards.inline import build_pro_offer_keyboard, build_product_card_keyboard
from config import settings
from database import crud
from database.models import User
from services.analytics import (
    build_product_caption_with_30d_stats,
    compute_deal_score,
    render_my_tracking_text_and_kb,
    render_pro_offer_card,
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

    sub_status = t("status_tracking_on", lang) if is_new_sub else t("status_tracking_updated", lang)
    cur_dec = Decimal(str(product.current_price))

    caption = build_product_caption_with_30d_stats(
        title=product.title,
        marketplace=product.marketplace,
        currency=product.currency,
        current_price=cur_dec,
        old_price=scraped.old_price,
        is_available=scraped.is_available,
        history_prices=history_prices,
        target_price=sub.target_price,
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
        lang=lang,
    )

    await status_msg.edit_text(caption, reply_markup=kb)
