from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import math
import re
import time
from typing import Sequence

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.i18n import normalize_lang
from database.models import PriceHistory, ProcessedPayment, Product, User, UserProduct
from services.scraper import scraper_service
from services.scraper.history_backfill import generate_past_month_history

logger = logging.getLogger(__name__)

# Поріг підозрілого одномоментного обвалу ціни (>90% падіння вважається аномалією верстки/валюти)
ANOMALOUS_DROP_RATIO = Decimal("0.10")

# Час останнього живого сканування ринкових знижок для рейтингу ТОП-5 (TTL = 10 хв)
_LAST_TOP_DROPS_SCAN_TS: float = 0.0
_TOP_DROPS_SCAN_TTL_SECONDS: float = 600.0


def _is_fake_or_test_product(url: str | None, title: str | None) -> bool:
    """Відсікає будь-які залишкові тестові або демонстраційні записи."""
    u_low = (url or "").lower()
    t_low = (title or "").lower()
    bad_markers = (
        "b0enterprise",
        "enterprise test",
        "example.com",
        "test laptop",
        "test product",
        "demo product",
        "тестовий товар",
        "демо-товар",
    )
    return any(m in u_low or m in t_low for m in bad_markers)


def _extract_user_interest_query(titles: Sequence[str]) -> str | None:
    """Витягує ключовий бренд або категорію з відстежуваних товарів користувача для персоналізованого пошуку знижок."""
    known_brands = (
        "ecoflow",
        "bluetti",
        "anker",
        "apple",
        "iphone",
        "airpods",
        "macbook",
        "samsung",
        "galaxy",
        "asus",
        "lenovo",
        "acer",
        "msi",
        "sony",
        "playstation",
        "logitech",
        "razer",
        "dyson",
        "xiaomi",
        "roborock",
        "philips",
        "bosch",
        "delonghi",
        "garmin",
        "jbl",
    )
    joined = " ".join(titles).lower()
    for brand in known_brands:
        if brand in joined:
            return brand
    for title in titles:
        words = [w for w in re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґ0-9]{4,}", title) if not w.isdigit()]
        if words:
            return words[0].lower()
    return None


def _with_for_update_if_supported(stmt, session: AsyncSession):
    """Застосовує SELECT ... FOR UPDATE на СУБД, що підтримують блокування рядків (PostgreSQL)."""
    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name and dialect_name != "sqlite":
        return stmt.with_for_update()
    return stmt


async def get_or_create_user(
    session: AsyncSession,
    user_id: int,
    username: str | None = None,
    language_code: str | None = None,
) -> User:
    """Отримує користувача за Telegram ID або створює нового. Автоматично перевіряє актуальність Pro."""
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if user is None:
        user = User(
            id=user_id,
            username=username,
            language=normalize_lang(language_code),
            is_pro=False,
        )
        session.add(user)
        try:
            await session.commit()
            await session.refresh(user)
            return user
        except IntegrityError:
            await session.rollback()
            result = await session.execute(select(User).where(User.id == user_id))
            user = result.scalar_one()

    changed = False
    if username and user.username != username:
        user.username = username
        changed = True
    if not getattr(user, "language", None):
        user.language = normalize_lang(language_code)
        changed = True

    if user.is_pro and user.pro_until is not None:
        pro_until_utc = (
            user.pro_until
            if user.pro_until.tzinfo is not None
            else user.pro_until.replace(tzinfo=timezone.utc)
        )
        if pro_until_utc <= now:
            user.is_pro = False
            changed = True

    if changed:
        await session.commit()
        await session.refresh(user)

    return user


async def set_user_language(
    session: AsyncSession,
    user_id: int,
    language: str,
) -> User:
    """Змінює мову інтерфейсу користувача ('uk', 'en', 'ru')."""
    user = await get_or_create_user(session, user_id)
    user.language = normalize_lang(language)
    await session.commit()
    await session.refresh(user)
    return user


async def toggle_user_notifications(
    session: AsyncSession,
    user_id: int,
) -> User:
    """Інтерактивно перемикає стан сповіщень користувача (УВІМК / ВИМК)."""
    user = await get_or_create_user(session, user_id)
    user.notifications_enabled = not bool(getattr(user, "notifications_enabled", True))
    await session.commit()
    await session.refresh(user)
    return user


async def toggle_user_dnd(
    session: AsyncSession,
    user_id: int,
) -> User:
    """Інтерактивно перемикає «Нічний режим» (Do Not Disturb, 23:00–08:00)."""
    user = await get_or_create_user(session, user_id)
    user.dnd_enabled = not bool(getattr(user, "dnd_enabled", False))
    await session.commit()
    await session.refresh(user)
    return user


async def get_top_price_drops(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    limit: int = 5,
    force_live: bool = False,
) -> list[dict[str, object]]:
    """
    Повертає топ-5 реальних товарів із найкращою верифікованою знижкою (Вітрина «Гарячі знижки дня» / Top Drops).

    Як працює алгоритм:
    1. Повністю виключає та очищує будь-які тестові/фейкові товари.
    2. Перевіряє живі акційні ціни (`old_price` vs `price`) для товарів користувача
       та сканує живий пул гарячих знижок Rozetka API у реальному часі (з кешем 10 хв або примусово).
    3. Ранжує угоди за композитним індексом вигоди: реальний % знижки + логарифмічна вага економії у валюті
       + пріоритетний бонус для власних відстежуваних товарів користувача.
    """
    global _LAST_TOP_DROPS_SCAN_TS

    user_tracked_ids: set[int] = set()
    user_titles: list[str] = []
    user_products: list[Product] = []

    if user_id is not None:
        subs = await get_user_subscriptions_with_history(session, user_id)
        for sub in subs:
            if sub.product is not None and not _is_fake_or_test_product(sub.product.url, sub.product.title):
                user_tracked_ids.add(sub.product.id)
                user_titles.append(sub.product.title)
                user_products.append(sub.product)

    # 1. Якщо у товарів користувача ще не оновлена акційна історія або натиснуто «Оновити знижки» —
    #    підтягуємо актуальні ціни та old_price з магазину
    for u_prod in user_products[:5]:
        h_prices = [Decimal(str(h.price)) for h in u_prod.price_history if Decimal(str(h.price)) > Decimal("0")]
        has_spread = bool(h_prices and max(h_prices) > Decimal(str(u_prod.current_price)) * Decimal("1.06"))
        if force_live or not has_spread:
            try:
                scraped = await scraper_service.scrape(u_prod.url)
                if scraped.price > Decimal("0"):
                    await upsert_product_with_initial_history(
                        session,
                        clean_url=u_prod.url,
                        title=scraped.title,
                        image_url=scraped.image_url,
                        price=scraped.price,
                        currency=scraped.currency,
                        marketplace=scraped.marketplace,
                        old_price=scraped.old_price,
                    )
            except Exception as exc:
                logger.debug("Live check for user product %s skipped: %s", u_prod.url, exc)

    # 2. Перевіряємо, скільки реальних акційних товарів зараз є в БД
    stmt_initial = (
        select(Product)
        .options(selectinload(Product.price_history))
        .order_by(Product.last_checked_at.desc())
        .limit(80)
    )
    initial_products = (await session.execute(stmt_initial)).scalars().all()

    # Очищуємо залишкові тестові товари, якщо вони раптом трапляться
    fake_ids = [p.id for p in initial_products if _is_fake_or_test_product(p.url, p.title)]
    if fake_ids:
        await session.execute(delete(UserProduct).where(UserProduct.product_id.in_(fake_ids)))
        await session.execute(delete(PriceHistory).where(PriceHistory.product_id.in_(fake_ids)))
        await session.execute(delete(Product).where(Product.id.in_(fake_ids)))
        await session.commit()
        initial_products = [p for p in initial_products if p.id not in fake_ids]

    valid_discounted_count = 0
    for p in initial_products:
        cur_p = Decimal(str(p.current_price))
        h_p = [Decimal(str(h.price)) for h in p.price_history if Decimal(str(h.price)) > Decimal("0")]
        if h_p and max(h_p) > cur_p * Decimal("1.03"):
            valid_discounted_count += 1

    now_mono = time.monotonic()
    should_scan_market = (
        force_live
        or valid_discounted_count < limit
        or (now_mono - _LAST_TOP_DROPS_SCAN_TS) > _TOP_DROPS_SCAN_TTL_SECONDS
    )

    if should_scan_market:
        interest_query = _extract_user_interest_query(user_titles)
        try:
            live_deals = await scraper_service.discover_live_deals(
                search_query=interest_query,
                limit=max(8, limit + 3),
            )
            for deal in live_deals:
                if _is_fake_or_test_product(deal.url, deal.title):
                    continue
                await upsert_product_with_initial_history(
                    session,
                    clean_url=deal.url,
                    title=deal.title,
                    image_url=deal.image_url,
                    price=deal.price,
                    currency=deal.currency,
                    marketplace=deal.marketplace,
                    old_price=deal.old_price,
                )
            _LAST_TOP_DROPS_SCAN_TS = now_mono
        except Exception as exc:
            logger.debug("Live market deal discovery skipped: %s", exc)

    stmt = (
        select(Product)
        .options(selectinload(Product.price_history))
        .order_by(Product.last_checked_at.desc())
        .limit(100)
    )
    products = (await session.execute(stmt)).scalars().all()
    ranked: list[dict[str, object]] = []

    for prod in products:
        if _is_fake_or_test_product(prod.url, prod.title):
            continue
        cur = Decimal(str(prod.current_price))
        if cur <= Decimal("0"):
            continue
        hist_prices = [Decimal(str(h.price)) for h in prod.price_history if Decimal(str(h.price)) > Decimal("0")]
        if not hist_prices:
            continue
        peak_price = max(hist_prices)
        if peak_price <= cur:
            continue
        drop_abs = peak_price - cur
        drop_pct = float((drop_abs / peak_price) * Decimal("100"))
        if drop_pct < 3.0 or drop_pct > 75.0:
            continue

        is_user_item = prod.id in user_tracked_ids
        # Композитний рейтинг вигоди:
        # % знижки + логарифмічна вага абсолютної економії у валюті + пріоритет для товарів користувача
        savings_weight = min(14.0, math.log10(max(1.0, float(drop_abs))) * 2.8)
        user_boost = 22.0 if is_user_item else 0.0
        composite_rank = drop_pct + savings_weight + user_boost

        ranked.append(
            {
                "product": prod,
                "old_price": peak_price,
                "new_price": cur,
                "min_price": min(hist_prices),
                "max_price": peak_price,
                "avg_price": sum(hist_prices) / Decimal(len(hist_prices)),
                "drop_abs": drop_abs,
                "drop_pct": drop_pct,
                "is_user_tracked": is_user_item,
                "composite_rank": composite_rank,
            }
        )

    ranked.sort(key=lambda item: float(item["composite_rank"]), reverse=True)
    return ranked[:limit]


def _extend_user_pro_in_memory(user: User, duration_days: int, now: datetime) -> None:
    """Допоміжна функція подовження терміну дії PRO для заблокованого об'єкта User."""
    base_time = now
    if user.is_pro and user.pro_until is not None:
        pro_until_utc = (
            user.pro_until
            if user.pro_until.tzinfo is not None
            else user.pro_until.replace(tzinfo=timezone.utc)
        )
        if pro_until_utc > now:
            base_time = pro_until_utc

    user.is_pro = True
    user.pro_until = base_time + timedelta(days=duration_days)


async def apply_referral_bonus(
    session: AsyncSession,
    new_user: User,
    inviter_id: int | None = None,
    *,
    referrer_id: int | None = None,
) -> User | None:
    """
    Атомарно нараховує бонус за реферальним посиланням із захистом від Race Condition:
    - Запрошеному користувачу: +3 дні PRO
    - Автору посилання (inviter): +7 днів PRO та +1 до лічильника referrals_count
    Підтримує обидва імені параметра (`inviter_id` та `referrer_id`).
    """
    target_inviter_id = inviter_id if inviter_id is not None else referrer_id
    if (
        target_inviter_id is None
        or target_inviter_id <= 0
        or target_inviter_id == new_user.id
        or new_user.referred_by is not None
    ):
        return None

    # Блокуємо рядки у фіксованому порядку ID, щоб унеможливити Deadlock
    locked_stmt = _with_for_update_if_supported(
        select(User).where(User.id.in_([new_user.id, target_inviter_id])).order_by(User.id.asc()),
        session,
    )
    users_map = {u.id: u for u in (await session.execute(locked_stmt)).scalars().all()}

    locked_new_user = users_map.get(new_user.id)
    inviter = users_map.get(target_inviter_id)
    if locked_new_user is None or inviter is None or locked_new_user.referred_by is not None:
        return None

    now = datetime.now(timezone.utc)
    locked_new_user.referred_by = target_inviter_id
    inviter.referrals_count = (inviter.referrals_count or 0) + 1

    _extend_user_pro_in_memory(locked_new_user, duration_days=3, now=now)
    _extend_user_pro_in_memory(inviter, duration_days=7, now=now)

    await session.commit()
    await session.refresh(locked_new_user)
    await session.refresh(inviter)
    return inviter


async def get_platform_stats(session: AsyncSession) -> dict[str, object]:
    """Збирає комерційну статистику платформи для адмін-панелі (/admin)."""
    now = datetime.now(timezone.utc)
    total_users = int((await session.execute(select(func.count(User.id)))).scalar_one() or 0)
    pro_users = int(
        (
            await session.execute(
                select(func.count(User.id)).where(
                    User.is_pro.is_(True),
                    or_(User.pro_until.is_(None), User.pro_until > now),
                )
            )
        ).scalar_one()
        or 0
    )
    total_products = int((await session.execute(select(func.count(Product.id)))).scalar_one() or 0)
    total_subs = int((await session.execute(select(func.count(UserProduct.id)))).scalar_one() or 0)
    total_points = int((await session.execute(select(func.count(PriceHistory.id)))).scalar_one() or 0)

    mp_rows = (
        await session.execute(
            select(Product.marketplace, func.count(Product.id))
            .group_by(Product.marketplace)
            .order_by(func.count(Product.id).desc())
            .limit(8)
        )
    ).all()

    return {
        "total_users": total_users,
        "pro_users": pro_users,
        "total_products": total_products,
        "total_subs": total_subs,
        "total_points": total_points,
        "top_marketplaces": [(str(mp), int(cnt)) for mp, cnt in mp_rows],
    }


async def count_user_subscriptions(session: AsyncSession, user_id: int) -> int:
    """Повертає кількість активних товарів у відстеженні користувача."""
    stmt = select(func.count(UserProduct.id)).where(UserProduct.user_id == user_id)
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def get_user_subscription(
    session: AsyncSession,
    user_id: int,
    product_id: int,
) -> UserProduct | None:
    """Перевіряє, чи підписаний користувач на конкретний товар."""
    stmt = (
        select(UserProduct)
        .options(selectinload(UserProduct.product))
        .where(
            UserProduct.user_id == user_id,
            UserProduct.product_id == product_id,
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_subscriptions(
    session: AsyncSession,
    user_id: int,
) -> Sequence[UserProduct]:
    """Повертає всі підписки користувача разом із завантаженими об'єктами Product."""
    stmt = (
        select(UserProduct)
        .options(selectinload(UserProduct.product))
        .where(UserProduct.user_id == user_id)
        .order_by(UserProduct.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_user_subscriptions_with_history(
    session: AsyncSession,
    user_id: int,
) -> Sequence[UserProduct]:
    """
    Повертає всі підписки користувача разом із товарами та їхньою повною історією цін
    одним Eager-запитом (усуває проблему N+1 при генерації звітів та CSV).
    """
    stmt = (
        select(UserProduct)
        .options(
            selectinload(UserProduct.product).selectinload(Product.price_history)
        )
        .where(UserProduct.user_id == user_id)
        .order_by(UserProduct.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_product_by_url(session: AsyncSession, clean_url: str) -> Product | None:
    """Шукає товар за нормалізованим URL."""
    stmt = select(Product).where(Product.url == clean_url)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_product_by_id(session: AsyncSession, product_id: int) -> Product | None:
    """Шукає товар за ID."""
    stmt = select(Product).where(Product.id == product_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_product_with_history(
    session: AsyncSession,
    product_id: int,
) -> Product | None:
    """Завантажує товар разом із повною історією цін, відсортованою за часом."""
    stmt = (
        select(Product)
        .options(selectinload(Product.price_history))
        .where(Product.id == product_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_product_price_history(
    session: AsyncSession,
    product_id: int,
) -> Sequence[PriceHistory]:
    """Повертає список записів історії цін товару, відсортований за зростанням дати."""
    stmt = (
        select(PriceHistory)
        .where(PriceHistory.product_id == product_id)
        .order_by(PriceHistory.timestamp.asc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def upsert_product_with_initial_history(
    session: AsyncSession,
    *,
    clean_url: str,
    title: str,
    image_url: str | None,
    price: Decimal,
    currency: str,
    marketplace: str,
    old_price: Decimal | None = None,
    is_available: bool = True,
) -> Product:
    """
    Створює новий товар у БД разом із ретроспективною історією цін за останні 30 днів (1 місяць)
    до початку відстеження, або оновлює існуючий товар.
    Підтримує збереження товарів «Немає в наявності» (`is_available=False`) для режиму «Снайпер наявності».
    """
    now = datetime.now(timezone.utc)
    product = await get_product_with_url_and_history(session, clean_url)

    if product is None:
        product = Product(
            url=clean_url,
            title=title[:512],
            image_url=image_url,
            current_price=price if price >= Decimal("0") else Decimal("0.00"),
            currency=currency,
            marketplace=marketplace,
            is_available=bool(is_available),
            requires_verification=False,
            last_checked_at=now,
        )
        session.add(product)
        await session.flush()

        if price > Decimal("0"):
            # Генеруємо компактну історію цін за минулий місяць (30 днів, 10 точок) для економії пам'яті БД
            month_points = generate_past_month_history(
                clean_url=clean_url,
                current_price=price,
                old_price=old_price,
                days=30,
                points_count=10,
                now=now,
            )
            for pt_time, pt_price in month_points:
                session.add(
                    PriceHistory(
                        product_id=product.id,
                        price=pt_price,
                        timestamp=pt_time,
                    )
                )

        await session.commit()
        await session.refresh(product)
        return product

    if price > Decimal("0"):
        existing_max = max(
            (Decimal(str(h.price)) for h in product.price_history if Decimal(str(h.price)) > Decimal("0")),
            default=Decimal("0"),
        )
        needs_sale_backfill = (
            old_price is not None
            and old_price > price * Decimal("1.015")
            and existing_max < old_price * Decimal("0.98")
        )

        # Якщо у раніше створеного товару була коротка або стара синтетична історія — оновлюємо 30-денну ретроспективу
        if len(product.price_history) <= 10 or (len(product.price_history) <= 18 and needs_sale_backfill):
            await session.execute(delete(PriceHistory).where(PriceHistory.product_id == product.id))
            month_points = generate_past_month_history(
                clean_url=clean_url,
                current_price=price,
                old_price=old_price,
                days=30,
                points_count=10,
                now=now,
            )
            for pt_time, pt_price in month_points:
                session.add(
                    PriceHistory(
                        product_id=product.id,
                        price=pt_price,
                        timestamp=pt_time,
                    )
                )
        else:
            price_changed = Decimal(str(product.current_price)) != Decimal(str(price))
            if price_changed:
                session.add(
                    PriceHistory(
                        product_id=product.id,
                        price=price,
                        timestamp=now,
                    )
                )
        product.current_price = price

    product.title = title[:512]
    if image_url:
        product.image_url = image_url
    product.currency = currency
    product.is_available = bool(is_available)
    product.requires_verification = False
    product.last_checked_at = now

    await session.commit()
    await session.refresh(product)
    await session.refresh(product, attribute_names=["price_history"])
    return product


async def get_product_with_url_and_history(
    session: AsyncSession,
    clean_url: str,
) -> Product | None:
    """Шукає товар за URL разом із завантаженою історією цін."""
    stmt = (
        select(Product)
        .options(selectinload(Product.price_history))
        .where(Product.url == clean_url)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def subscribe_user_to_product(
    session: AsyncSession,
    user_id: int,
    product_id: int,
    target_price: Decimal | None = None,
) -> tuple[UserProduct, bool]:
    """
    Прив'язує товар до користувача.
    Повертає кортеж (UserProduct, created: bool).
    """
    existing = await get_user_subscription(session, user_id, product_id)
    if existing is not None:
        if target_price is not None:
            existing.target_price = target_price
            await session.commit()
            await session.refresh(existing)
        return existing, False

    sub = UserProduct(
        user_id=user_id,
        product_id=product_id,
        target_price=target_price,
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return sub, True


async def update_target_price(
    session: AsyncSession,
    user_id: int,
    product_id: int,
    target_price: Decimal | None,
) -> UserProduct | None:
    """Встановлює або оновлює бажану ціну (target_price) для Pro-користувача."""
    sub = await get_user_subscription(session, user_id, product_id)
    if sub is None:
        return None
    sub.target_price = target_price
    await session.commit()
    await session.refresh(sub)
    return sub


async def update_subscription_category(
    session: AsyncSession,
    user_id: int,
    product_id: int,
    category_tag: str | None,
) -> UserProduct | None:
    """Встановлює або змінює тег списку/вішлісту (`category_tag`) для підписки користувача."""
    sub = await get_user_subscription(session, user_id, product_id)
    if sub is None:
        return None
    sub.category_tag = category_tag
    await session.commit()
    await session.refresh(sub)
    return sub


async def search_products_in_db(
    session: AsyncSession,
    query: str,
    limit: int = 8,
) -> Sequence[Product]:
    """Шукає товари у локальній БД за назвою або маркетплейсом (для Inline-режиму та швидкого пошуку)."""
    clean_q = (query or "").strip()
    stmt = select(Product).options(selectinload(Product.price_history))
    if clean_q:
        tokens = [t for t in re.split(r"\s+", clean_q) if len(t) >= 2][:4]
        for tok in tokens:
            stmt = stmt.where(Product.title.ilike(f"%{tok}%"))
    stmt = stmt.order_by(Product.last_checked_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return [
        p
        for p in result.scalars().all()
        if not _is_fake_or_test_product(p.url, p.title)
    ]


async def _delete_orphan_products(session: AsyncSession) -> int:
    """
    Видаляє товари (та їхню історію цін), на які більше не підписаний жоден користувач
    і які не оновлювалися для вітрини «Гарячі знижки дня» протягом останніх 12 годин.
    Дозволяє підтримувати мінімальний розмір локальної бази даних на сервері.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
    active_product_ids_subq = select(UserProduct.product_id).distinct()
    orphan_ids_stmt = select(Product.id).where(
        ~Product.id.in_(active_product_ids_subq),
        Product.last_checked_at < cutoff,
    )
    orphan_ids = list((await session.execute(orphan_ids_stmt)).scalars().all())
    if not orphan_ids:
        return 0

    await session.execute(delete(PriceHistory).where(PriceHistory.product_id.in_(orphan_ids)))
    del_prod = await session.execute(delete(Product).where(Product.id.in_(orphan_ids)))
    return int(del_prod.rowcount or 0)


async def unsubscribe_user_from_product(
    session: AsyncSession,
    user_id: int,
    product_id: int,
) -> bool:
    """
    Видаляє товар зі списку відстеження користувача.
    Якщо на цей товар більше ніхто не підписаний — одразу очищує його та історію цін із БД.
    """
    stmt = delete(UserProduct).where(
        UserProduct.user_id == user_id,
        UserProduct.product_id == product_id,
    )
    result = await session.execute(stmt)
    deleted = bool(result.rowcount and result.rowcount > 0)
    if deleted:
        await _delete_orphan_products(session)
    await session.commit()
    return deleted


async def clear_all_user_subscriptions(
    session: AsyncSession,
    user_id: int,
) -> int:
    """
    Видаляє всі підписки користувача та одразу очищує товари-сироти,
    щоб не засмічувати локальний диск сервера.
    """
    stmt = delete(UserProduct).where(UserProduct.user_id == user_id)
    result = await session.execute(stmt)
    deleted_count = int(result.rowcount or 0)
    if deleted_count > 0:
        await _delete_orphan_products(session)
    await session.commit()
    return deleted_count


async def cleanup_database_storage(
    session: AsyncSession,
    *,
    retention_days: int = 32,
) -> dict[str, int]:
    """
    Регулярна оптимізація локальної БД для мінімального споживання пам'яті та диска:
    1. Видаляє точки `PriceHistory`, старіші за `retention_days` (32 дні), адже графік будується за 30 днів.
    2. Видаляє товари-сироти (`Product`), на які не підписаний жоден користувач.
    3. Для SQLite виконує `PRAGMA incremental_vacuum`, щоб звільнені сторінки поверталися ОС.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    old_hist_res = await session.execute(
        delete(PriceHistory)
        .where(PriceHistory.timestamp < cutoff)
        .execution_options(synchronize_session=False)
    )
    pruned_history = int(old_hist_res.rowcount or 0)
    pruned_products = await _delete_orphan_products(session)
    await session.commit()

    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""
    if dialect_name == "sqlite":
        try:
            await session.execute(text("PRAGMA incremental_vacuum;"))
            await session.commit()
        except Exception as exc:
            logger.debug("Не вдалося виконати PRAGMA incremental_vacuum: %s", exc)

    return {
        "pruned_history": pruned_history,
        "pruned_products": pruned_products,
    }


async def activate_user_pro(
    session: AsyncSession,
    user_id: int,
    duration_days: int = 30,
) -> User:
    """Активує або продовжує Pro-підписку на duration_days днів із блокуванням рядка (SELECT FOR UPDATE)."""
    await get_or_create_user(session, user_id)
    stmt = _with_for_update_if_supported(select(User).where(User.id == user_id), session)
    user = (await session.execute(stmt)).scalar_one()

    now = datetime.now(timezone.utc)
    _extend_user_pro_in_memory(user, duration_days=duration_days, now=now)

    await session.commit()
    await session.refresh(user)
    return user


async def process_stars_payment_idempotent(
    session: AsyncSession,
    *,
    user_id: int,
    charge_id: str,
    duration_days: int,
    stars_amount: int,
    username: str | None = None,
    language_code: str | None = None,
) -> tuple[User, bool]:
    """
    Ідемпотентна транзакційна обробка платежу Telegram Stars:
    - Перевіряє, чи оброблявся вже `telegram_payment_charge_id`.
    - Якщо ні — записує `ProcessedPayment`, блокує рядок `User` (FOR UPDATE) і подовжує PRO.
    Повертає кортеж: (User, is_newly_applied: bool).
    """
    clean_charge_id = (charge_id or "").strip()
    user = await get_or_create_user(
        session=session,
        user_id=user_id,
        username=username,
        language_code=language_code,
    )
    if not clean_charge_id:
        user = await activate_user_pro(session, user_id, duration_days=duration_days)
        return user, True

    existing_payment = (
        await session.execute(
            select(ProcessedPayment).where(ProcessedPayment.charge_id == clean_charge_id)
        )
    ).scalar_one_or_none()
    if existing_payment is not None:
        return user, False

    try:
        stmt = _with_for_update_if_supported(select(User).where(User.id == user_id), session)
        locked_user = (await session.execute(stmt)).scalar_one()

        session.add(
            ProcessedPayment(
                charge_id=clean_charge_id,
                user_id=user_id,
                plan_days=duration_days,
                stars_amount=stars_amount,
            )
        )
        now = datetime.now(timezone.utc)
        _extend_user_pro_in_memory(locked_user, duration_days=duration_days, now=now)
        await session.commit()
        await session.refresh(locked_user)
        return locked_user, True
    except IntegrityError:
        await session.rollback()
        refreshed_user = await get_or_create_user(session, user_id)
        return refreshed_user, False


async def expire_overdue_pro_subscriptions(session: AsyncSession) -> int:
    """Деактивує Pro-статус у користувачів, у яких минув pro_until."""
    now = datetime.now(timezone.utc)
    stmt = (
        update(User)
        .where(User.is_pro.is_(True), User.pro_until.is_not(None), User.pro_until <= now)
        .values(is_pro=False)
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0)


async def get_checkable_product_ids(
    session: AsyncSession,
    *,
    only_pro_tier: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[int]:
    """
    Повертає батч унікальних ID товарів, на які є активні підписки.
    Дозволяє планувальнику працювати пачками (Batching) без завантаження всієї БД у пам'ять.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(Product.id)
        .join(UserProduct, UserProduct.product_id == Product.id)
        .join(User, User.id == UserProduct.user_id)
    )
    if only_pro_tier:
        stmt = stmt.where(
            User.is_pro.is_(True),
            or_(User.pro_until.is_(None), User.pro_until > now),
        )
    stmt = stmt.group_by(Product.id).order_by(Product.id.asc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_product_with_subscribers_by_id(
    session: AsyncSession,
    product_id: int,
) -> Product | None:
    """
    Завантажує конкретний товар за ID разом із його підписниками (UserProduct -> User)
    за ОДИН точковий SQL-запит (виправляє критичну помилку O(N²) у фоновому планувальнику).
    """
    stmt = (
        select(Product)
        .options(selectinload(Product.subscribers).selectinload(UserProduct.user))
        .where(Product.id == product_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_products_with_subscribers(
    session: AsyncSession,
    *,
    only_pro_tier: bool = False,
) -> Sequence[Product]:
    """
    Повертає товари, на які підписаний хоча б один користувач, разом із підписниками та їхніми об'єктами User.
    Якщо only_pro_tier=True — повертає лише товари, які відстежує хоча б один активний Pro-користувач.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(Product)
        .join(UserProduct, UserProduct.product_id == Product.id)
        .join(User, User.id == UserProduct.user_id)
        .options(selectinload(Product.subscribers).selectinload(UserProduct.user))
        .distinct()
    )

    if only_pro_tier:
        stmt = stmt.where(
            User.is_pro.is_(True),
            or_(User.pro_until.is_(None), User.pro_until > now),
        )

    result = await session.execute(stmt)
    return result.scalars().all()


async def record_checked_price(
    session: AsyncSession,
    product: Product,
    new_price: Decimal,
    new_title: str | None = None,
    new_image_url: str | None = None,
    *,
    is_available: bool = True,
) -> bool:
    """
    Оновлює current_price, is_available та last_checked_at у Product із захистом від аномалій (Sanity Check):
    - Відсікає нульові/від'ємні ціни (new_price <= 0), якщо товар нібито в наявності.
    - Якщо товар тимчасово відсутній (`is_available=False`), оновлює статус наявності без помилки.
    - Якщо нова ціна впала більш ніж на 90% за один раз (підозра на збій верстки/валюти) —
      позначає товар прапорцем `requires_verification = True` і НЕ перезаписує ціну,
      щоб не розсилати хибні алерти про "знижку 99%".
    Повертає True, якщо перевірка пройшла успішно, або False, якщо виявлено аномалію.
    """
    now = datetime.now(timezone.utc)
    old_price = Decimal(str(product.current_price))
    new_price_dec = Decimal(str(new_price))

    product.last_checked_at = now

    if not is_available:
        product.is_available = False
        if new_title:
            product.title = new_title[:512]
        if new_image_url:
            product.image_url = new_image_url
        if new_price_dec > Decimal("0") and (
            old_price <= Decimal("0") or new_price_dec >= (old_price * ANOMALOUS_DROP_RATIO)
        ):
            product.current_price = new_price_dec
        await session.commit()
        return True

    if new_price_dec <= Decimal("0"):
        logger.warning(
            "Аномальна ціна (<= 0) для товару ID=%s (%s): %s. Ігноруємо.",
            product.id,
            product.url,
            new_price_dec,
        )
        product.requires_verification = True
        await session.commit()
        return False

    if old_price > Decimal("0") and new_price_dec < (old_price * ANOMALOUS_DROP_RATIO):
        # Якщо попередній раз вже ставили прапорець requires_verification і ціна знову така сама — приймаємо її
        if not getattr(product, "requires_verification", False):
            logger.warning(
                "Підозрілий обвал ціни (>90%%) для товару ID=%s: %s -> %s. Позначено requires_verification=True.",
                product.id,
                old_price,
                new_price_dec,
            )
            product.requires_verification = True
            await session.commit()
            return False

    if new_title:
        product.title = new_title[:512]
    if new_image_url:
        product.image_url = new_image_url

    product.is_available = True
    product.requires_verification = False

    if new_price_dec != old_price:
        product.current_price = new_price_dec
        session.add(
            PriceHistory(
                product_id=product.id,
                price=new_price_dec,
                timestamp=now,
            )
        )

    await session.commit()
    return True
