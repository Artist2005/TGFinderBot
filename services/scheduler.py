from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import time
from typing import Any
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.i18n import fmt_money, normalize_lang
from bot.keyboards.inline import build_price_alert_keyboard, build_start_keyboard
from config import settings
from database import crud
from database.models import Product, User
from database.session import get_db_session
from services.analytics import build_daily_digest_card, build_price_drop_alert_card
from services.scraper.exceptions import ScraperError
from services.scraper.manager import scraper_service

logger = logging.getLogger(__name__)

# Розмір одного батчу при обході БД (запобігає OOM на тисячах товарів)
SCHEDULER_BATCH_SIZE = 50

# Ліміт паралельних запитів на один домен маркетплейсу (запобігає миттєвому 429/бану)
PER_DOMAIN_CONCURRENCY = 2

# Безпечний ліміт розсилки сповіщень Telegram Bot API (~25 повідомлень/сек при ліміті 30/сек)
TELEGRAM_BROADCAST_RATE_PER_SEC = 25.0


def _is_night_hours_kyiv() -> bool:
    """Перевіряє, чи зараз нічний час (23:00–08:00 за київським часом UTC+2/+3)."""
    kyiv_now = datetime.now(timezone.utc) + timedelta(hours=2)
    return kyiv_now.hour >= 23 or kyiv_now.hour < 8


class _AsyncRateLimiter:
    """Асинхронний Token-Bucket Rate Limiter для дотримання лімітів Telegram Bot API (Flood Control)."""

    def __init__(self, rate_per_sec: float = TELEGRAM_BROADCAST_RATE_PER_SEC) -> None:
        self._min_interval = 1.0 / max(1.0, rate_per_sec)
        self._lock = asyncio.Lock()
        self._last_sent_at = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_sent_at
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_sent_at = time.monotonic()


class PriceCheckScheduler:
    """
    Високонавантажений сервіс фонового моніторингу цін та розсилки сповіщень (APScheduler):
    - Batching: вибірка товарів пачками по 50 ID (0 OOM та 0 N+1 / O(N²) запитів).
    - Per-Domain Rate Limiting: окремий asyncio.Semaphore(2) для кожного домену магазину.
    - Night Mode (Do Not Disturb, 23:00–08:00): буферизує нічні знижки й надсилає ранковий дайджест о 08:30.
    - Telegram Flood Control: контроль частоти відправки повідомлень + обробка TelegramRetryAfter.
    """

    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._global_semaphore = asyncio.Semaphore(settings.scraper_concurrency_limit)
        self._domain_semaphores: dict[str, asyncio.Semaphore] = {}
        self._tg_limiter = _AsyncRateLimiter(TELEGRAM_BROADCAST_RATE_PER_SEC)
        # Оперативна черга нічних знижок для користувачів з увімкненим DND: user_id -> list[dict]
        self._night_digest_queue: dict[int, list[dict[str, Any]]] = defaultdict(list)

    def _get_domain_semaphore(self, url: str) -> asyncio.Semaphore:
        """Повертає або створює семафор конкурентності для конкретного домену маркетплейсу."""
        host = (urlparse(url).hostname or "default").lower().removeprefix("www.")
        sem = self._domain_semaphores.get(host)
        if sem is None:
            sem = asyncio.Semaphore(PER_DOMAIN_CONCURRENCY)
            self._domain_semaphores[host] = sem
        return sem

    def start(self) -> None:
        """Реєструє періодичні задачі та запускає планувальник."""
        if self._scheduler.running:
            return

        # 1. Перевірка товарів Pro-користувачів (кожні 12 год / 720 хв)
        self._scheduler.add_job(
            self.run_price_checks,
            trigger="interval",
            minutes=settings.pro_check_interval_minutes,
            kwargs={"only_pro_tier": True},
            id="check_pro_products",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # 2. Періодична перевірка товарів усіх користувачів (раз на добу / 24 год)
        self._scheduler.add_job(
            self.run_price_checks,
            trigger="interval",
            hours=settings.free_check_interval_hours,
            kwargs={"only_pro_tier": False},
            id="check_all_products",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # 3. Очищення прострочених Pro-підписок та оптимізація локальної БД (старі точки >32 дн, сироти, vacuum)
        self._scheduler.add_job(
            self.cleanup_expired_pro,
            trigger="interval",
            hours=12,
            id="cleanup_expired_pro",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        # 4. Ранковий дайджест нічних знижок (о 06:30 UTC / 08:30 за Києвом)
        self._scheduler.add_job(
            self.send_morning_digests,
            trigger="cron",
            hour=6,
            minute=30,
            id="send_morning_digests",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        self._scheduler.start()
        pro_hours_str = (
            f"{settings.pro_check_interval_minutes // 60} год"
            if settings.pro_check_interval_minutes % 60 == 0
            else f"{settings.pro_check_interval_minutes} хв"
        )
        logger.info(
            "Планувальник APScheduler запущено (PRO: кожні %s, Free: кожні %d год / раз на добу, BatchSize=%d).",
            pro_hours_str,
            settings.free_check_interval_hours,
            SCHEDULER_BATCH_SIZE,
        )

    def shutdown(self, wait: bool = True) -> None:
        """Коректно зупиняє планувальник, очікуючи завершення активних задач."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("Планувальник APScheduler зупинено (wait=%s).", wait)

    async def send_morning_digests(self) -> None:
        """Розсилає ранковий дайджест знижок користувачам, у яких вночі спрацював Нічний режим (DND)."""
        if not self._night_digest_queue:
            return

        queued_users = list(self._night_digest_queue.keys())
        for user_id in queued_users:
            items = self._night_digest_queue.pop(user_id, [])
            if not items:
                continue
            try:
                async with get_db_session() as session:
                    user = await session.get(User, user_id)
                    lang = normalize_lang(user.language if user else "uk")
                text = build_daily_digest_card(items, lang=lang)
                await self._send_safe_alert(
                    user_id=user_id,
                    text=text,
                    image_url=None,
                    reply_markup=build_start_keyboard(lang),
                )
            except Exception as exc:
                logger.warning("Не вдалося надіслати ранковий дайджест користувачу %d: %s", user_id, exc)

    async def cleanup_expired_pro(self) -> None:
        """Деактивує прострочені Pro-підписки та очищує застарілі дані в локальній БД."""
        try:
            async with get_db_session() as session:
                expired_count = await crud.expire_overdue_pro_subscriptions(session)
                if expired_count > 0:
                    logger.info("Деактивовано %d прострочених Pro-підписок.", expired_count)

                storage_stats = await crud.cleanup_database_storage(session, retention_days=32)
                if storage_stats["pruned_history"] > 0 or storage_stats["pruned_products"] > 0:
                    logger.info(
                        "Оптимізація БД: видалено %d старих точок історії (>32 дн.) та %d товарів без підписок.",
                        storage_stats["pruned_history"],
                        storage_stats["pruned_products"],
                    )
        except Exception as exc:
            logger.exception("Помилка під час очищення прострочених Pro-підписок та оптимізації БД: %s", exc)

    async def run_price_checks(self, *, only_pro_tier: bool = False) -> None:
        """
        Пакетно (Batching по 50 товарів) отримує список ID товарів із БД і паралельно перевіряє їх.
        Повністю усуває проблему O(N²) запитів та переповнення пам'яті.
        """
        tier_label = "PRO" if only_pro_tier else "ALL (Free + Pro)"
        offset = 0
        total_checked = 0

        try:
            while True:
                async with get_db_session() as session:
                    batch_ids = await crud.get_checkable_product_ids(
                        session,
                        only_pro_tier=only_pro_tier,
                        limit=SCHEDULER_BATCH_SIZE,
                        offset=offset,
                    )

                if not batch_ids:
                    break

                logger.info(
                    "Пакетна перевірка цін [%s]: батч із %d товарів (offset=%d)...",
                    tier_label,
                    len(batch_ids),
                    offset,
                )

                tasks = [
                    self._check_single_product(product_id=pid, only_pro_tier=only_pro_tier)
                    for pid in batch_ids
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

                total_checked += len(batch_ids)
                if len(batch_ids) < SCHEDULER_BATCH_SIZE:
                    break
                offset += SCHEDULER_BATCH_SIZE

            if total_checked > 0:
                logger.info("Перевірку цін [%s] завершено: опрацьовано %d товарів.", tier_label, total_checked)
        except Exception as exc:
            logger.exception("Критична помилка в циклі run_price_checks [%s]: %s", tier_label, exc)

    async def _check_single_product(self, product_id: int, *, only_pro_tier: bool) -> None:
        """
        Перевіряє один товар із дотриманням глобального та доменного семафорів,
        оновлює історію в БД та розсилає алерти про знижки.
        """
        async with get_db_session() as session:
            product = await crud.get_product_with_subscribers_by_id(session, product_id)
            if product is None or not product.subscribers:
                return
            product_url = product.url

        domain_sem = self._get_domain_semaphore(product_url)
        async with self._global_semaphore, domain_sem:
            try:
                scraped = await scraper_service.fetch_product(product_url)
            except ScraperError as exc:
                logger.warning("Не вдалося оновити товар ID=%d (%s): %s", product_id, product_url, exc)
                return
            except Exception as exc:
                logger.exception("Неочікувана помилка скрапера для ID=%d: %s", product_id, exc)
                return

            if not scraped.is_available or scraped.price <= Decimal("0"):
                logger.debug(
                    "Товар ID=%d тимчасово відсутній або ціна некоректна (%s), пропускаємо алерт.",
                    product_id,
                    scraped.price,
                )
                return

            async with get_db_session() as session:
                product = await crud.get_product_with_subscribers_by_id(session, product_id)
                if product is None:
                    return

                old_price = Decimal(str(product.current_price))
                new_price = Decimal(str(scraped.price))

                is_valid = await crud.record_checked_price(
                    session=session,
                    product=product,
                    new_price=new_price,
                    new_title=scraped.title,
                    new_image_url=scraped.image_url,
                )

                # Якщо ціна була визнана аномальною (наприклад, падіння >90% через баг сайту) — не шлемо хибний алерт
                if not is_valid:
                    return

                if new_price < old_price:
                    await self._dispatch_price_drop_alerts(
                        product=product,
                        old_price=old_price,
                        new_price=new_price,
                        only_pro_tier=only_pro_tier,
                    )

    async def _dispatch_price_drop_alerts(
        self,
        *,
        product: Product,
        old_price: Decimal,
        new_price: Decimal,
        only_pro_tier: bool,
    ) -> None:
        """Формує та надсилає персоналізовані сповіщення підписникам товару з урахуванням Target Price та DND."""
        is_night = _is_night_hours_kyiv()

        for sub in product.subscribers:
            user = sub.user
            if user is None:
                continue

            if only_pro_tier and not user.has_active_pro:
                continue

            # Перевірка тумблера сповіщень
            if not getattr(user, "notifications_enabled", True):
                continue

            # Перевірка умови Бажаної ціни (Target Price)
            if sub.target_price is not None:
                target_dec = Decimal(str(sub.target_price))
                if new_price > target_dec:
                    continue

            # Нічний режим (Do Not Disturb 23:00–08:00): пакуємо знижку в ранковий дайджест
            if getattr(user, "dnd_enabled", False) and is_night:
                self._night_digest_queue[user.id].append(
                    {
                        "product_id": product.id,
                        "title": product.title,
                        "url": product.url,
                        "marketplace": product.marketplace,
                        "currency": product.currency,
                        "old_price": old_price,
                        "new_price": new_price,
                    }
                )
                continue

            lang = normalize_lang(user.language)
            text = build_price_drop_alert_card(
                title=product.title,
                marketplace=product.marketplace,
                currency=product.currency,
                old_price=old_price,
                new_price=new_price,
                target_price=sub.target_price,
                lang=lang,
            )

            kb = build_price_alert_keyboard(
                product_id=product.id,
                product_url=product.url,
                user_id=user.id,
                marketplace=product.marketplace,
                title=product.title,
                new_price_str=fmt_money(new_price, product.currency),
                old_price_str=fmt_money(old_price, product.currency),
                lang=lang,
            )

            await self._send_safe_alert(
                user_id=user.id,
                text=text,
                image_url=product.image_url,
                reply_markup=kb,
            )

    async def _send_safe_alert(
        self,
        *,
        user_id: int,
        text: str,
        image_url: str | None,
        reply_markup: object,
    ) -> None:
        """Надсилає повідомлення в Telegram з урахуванням Token-Bucket Rate Limiter та RetryAfter."""
        await self._tg_limiter.acquire()
        try:
            if image_url:
                try:
                    await self._bot.send_photo(
                        chat_id=user_id,
                        photo=image_url,
                        caption=text,
                        reply_markup=reply_markup,
                    )
                    return
                except Exception:
                    pass

            await self._bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=False,
            )
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.5)
            try:
                await self._bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
            except Exception:
                pass
        except TelegramForbiddenError:
            logger.info("Користувач %d заблокував бота, пропускаємо сповіщення.", user_id)
        except Exception as exc:
            logger.warning("Помилка надсилання сповіщення користувачу %d: %s", user_id, exc)
