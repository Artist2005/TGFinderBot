from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.i18n import t
from bot.keyboards.callbacks import ProPaymentCallback
from config import settings
from database import crud
from database.models import User

router = Router(name="payments_router")

PRO_INVOICE_PREFIX = "pro_subscription_days_"


@router.callback_query(ProPaymentCallback.filter(F.action == "buy_stars"))
async def send_pro_stars_invoice(
    callback: CallbackQuery,
    callback_data: ProPaymentCallback,
    db_user: User,
) -> None:
    """Відправляє користувачу рахунок (sendInvoice) для оплати PRO (30 або 90 днів) через Telegram Stars (XTR)."""
    await callback.answer()
    if callback.message is None:
        return

    lang = db_user.language
    plan_days = callback_data.plan_days if callback_data.plan_days in (30, 90, 365) else 30
    if plan_days == 30:
        amount_stars = settings.pro_price_stars
    elif plan_days == 90:
        amount_stars = max(1, int(round(settings.pro_price_stars * 2.4)))
    else:
        amount_stars = max(1, int(round(settings.pro_price_stars * 7.2)))

    prices = [
        LabeledPrice(
            label=f"PRO ({plan_days}d)",
            amount=amount_stars,
        )
    ]

    await callback.message.answer_invoice(
        title=t("invoice_title", lang, days=plan_days),
        description=t("invoice_desc", lang, pro_max=settings.pro_max_products, days=plan_days),
        payload=f"{PRO_INVOICE_PREFIX}{plan_days}",
        provider_token="",  # Для цифрових товарів у Telegram Stars provider_token завжди порожній рядок
        currency="XTR",
        prices=prices,
    )


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery) -> None:
    """Підтвердження готовності прийняти платіж Telegram Stars (pre_checkout_query)."""
    payload = pre_checkout_query.invoice_payload or ""
    if not payload.startswith(PRO_INVOICE_PREFIX) and payload != "pro_subscription_30_days":
        await pre_checkout_query.answer(
            ok=False,
            error_message="Invalid invoice payload. Please request a new invoice via /pro.",
        )
        return

    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(
    message: Message,
    session: AsyncSession,
    db_user: User,
) -> None:
    """
    Ідемпотентно обробляє успішну оплату Telegram Stars (`telegram_payment_charge_id`)
    та активує статус `is_pro` на обраний період без подвійного нарахування.
    """
    payment = message.successful_payment
    if payment is None or message.from_user is None:
        return

    lang = db_user.language
    payload = payment.invoice_payload or ""
    plan_days = settings.pro_duration_days
    if payload.startswith(PRO_INVOICE_PREFIX):
        raw_days = payload.removeprefix(PRO_INVOICE_PREFIX)
        if raw_days.isdigit():
            plan_days = int(raw_days)

    charge_id = (
        payment.telegram_payment_charge_id
        or payment.provider_payment_charge_id
        or f"fallback_{message.from_user.id}_{message.message_id}"
    )

    user, _is_new_charge = await crud.process_stars_payment_idempotent(
        session=session,
        user_id=message.from_user.id,
        charge_id=charge_id,
        duration_days=plan_days,
        stars_amount=payment.total_amount,
        username=message.from_user.username,
        language_code=message.from_user.language_code,
    )

    until_str = (
        user.pro_until.strftime("%d.%m.%Y %H:%M UTC")
        if user.pro_until is not None
        else f"{plan_days}d"
    )

    await message.answer(
        t("payment_success", lang, date=until_str, pro_max=settings.pro_max_products)
    )
