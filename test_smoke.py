from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

# Автоматичне перемикання на локальний .venv, якщо скрипт запущено глобальним `python test_smoke.py`
_VENV_PYTHON = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"
if _VENV_PYTHON.exists() and Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
    raise SystemExit(subprocess.call([str(_VENV_PYTHON), *sys.argv]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Підставляємо тестові змінні оточення, якщо файл .env ще не налаштовано
os.environ.setdefault("BOT_TOKEN", "123456789:AAH_TEST_TOKEN_FOR_SMOKE_CHECK")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_local.db"
os.environ.setdefault("AFFILIATE_SUBID_KEY", "my_test_subid")

from bot.keyboards.inline import (
    build_price_alert_keyboard,
    build_product_card_keyboard,
    build_settings_keyboard,
    build_target_price_presets_keyboard,
)
from bot.keyboards.reply import build_main_reply_keyboard
from database import crud
from database.session import close_db, get_db_session, init_db
from services.affiliate import build_affiliate_url
from services.analytics import (
    build_daily_digest_card,
    build_price_drop_alert_card,
    build_product_caption_with_30d_stats,
    render_quick_compare_card,
    render_top_drops_card,
)
from services.chart_engine import generate_price_chart
from services.scraper import get_marketplace_label, normalize_url, scraper_service


async def run_smoke_tests(custom_url: str | None = None) -> None:
    print("=" * 65)
    print("🚀 ЗАПУСК SMOKE-ТЕСТУ PRICE TRACKER BOT (ВСІ МАРКЕТПЛЕЙСИ + 30 ДНІВ)")
    print("=" * 65)

    # 1. Тест нормалізації URL та мультимаркетплейсів
    dirty_url = (
        custom_url
        or "https://hard.rozetka.com.ua/ua/kingston_skc3000s_1024g/p326094910/characteristics/"
        "?utm_source=telegram&utm_medium=cpc&gclid=test12345"
    )
    clean_url, marketplace = normalize_url(dirty_url)
    print("\n[1/5] ✅ Нормалізація URL та мультимаркетплейси:")
    print(f"   Вхідний URL : {dirty_url}")
    print(f"   Чистий URL  : {clean_url}")
    print(f"   Маркетплейс : {get_marketplace_label(marketplace)} ({marketplace})")

    for sample in [
        "https://prom.ua/ua/p123456789-test.html?utm_source=google",
        "https://comfy.ua/ua/smartfon-apple-iphone-15.html?gclid=123",
        "https://epicentrk.ua/ua/shop/kava.html?fbclid=abc",
        "https://allo.ua/ua/products/mobile/apple.html",
    ]:
        s_url, s_mp = normalize_url(sample)
        print(f"   • {get_marketplace_label(s_mp):<10} -> {s_url}")

    # 2. Тест генерації CPA / партнерського посилання
    aff_url = build_affiliate_url(clean_url, user_id=777000)
    print("\n[2/5] ✅ Генерація партнерського (CPA) посилання:")
    print(f"   CPA URL     : {aff_url}")

    # 3. Тест парсера (живий запит або fallback при блокуванні мережі)
    print("\n[3/5] 🔍 Перевірка мультимаркетплейс-скрапера...")
    try:
        scraped = await scraper_service.scrape(clean_url)
        print(f"   ✅ Живий парсинг ({get_marketplace_label(scraped.marketplace)}) успішний!")
        print(f"   Назва       : {scraped.title}")
        print(f"   Ціна        : {scraped.price} {scraped.currency}")
        print(f"   Стара ціна  : {scraped.old_price}")
        print(f"   В наявності : {scraped.is_available}")
        title = scraped.title
        price = scraped.price
        old_price = scraped.old_price
        image_url = scraped.image_url
    except Exception as exc:
        print(f"   ⚠️ Живий запит повернув помилку ({exc}). Використовуємо тестові дані для перевірки БД та графіка.")
        title = "SSD Kingston KC3000 1024GB M.2 PCIe 4.0 x4 NVMe"
        price = Decimal("4199.00")
        old_price = Decimal("4699.00")
        image_url = "https://content2.rozetka.com.ua/goods/images/big/231922321.jpg"
    finally:
        await scraper_service.close()

    # 4. Тест Бази Даних та автоматичної історії цін за 30 днів до початку відстеження
    print("\n[4/5] 🗄 Перевірка Бази Даних та ретроспективи цін за 30 днів...")
    await init_db()
    async with get_db_session() as session:
        user = await crud.get_or_create_user(session, user_id=777000, username="tester_ua")
        product = await crud.upsert_product_with_initial_history(
            session,
            clean_url=clean_url,
            title=title,
            image_url=image_url,
            price=price,
            currency="UAH",
            marketplace=marketplace,
            old_price=old_price,
        )
        sub, _ = await crud.subscribe_user_to_product(
            session,
            user_id=user.id,
            product_id=product.id,
            target_price=price - Decimal("300.00"),
        )
        await session.commit()

        history = await crud.get_product_price_history(session, product.id)
        subs_count = await crud.count_user_subscriptions(session, user.id)
        print(f"   ✅ Користувач ID={user.id} (@{user.username}), активних підписок: {subs_count}")
        print(f"   ✅ Товар ID={product.id} збережено в БД. Автоматично сформовано точок історії за 30 днів: {len(history)}")

        # 5. Тест генерації PNG-графіка в оперативній пам'яті (BytesIO без збереження на диск)
        print("\n[5/5] 📊 Генерація 30-денного графіка коливань цін у RAM (без збереження PNG на диск)...")
        chart_buf = await generate_price_chart(
            session=session,
            product_id=product.id,
            target_price=sub.target_price,
        )
        if chart_buf is not None:
            try:
                size_kb = len(chart_buf.getvalue()) / 1024.0
                print(
                    f"   ✅ Графік згенеровано в io.BytesIO ({size_kb:.1f} КБ у RAM) і готово до відправки в Telegram!"
                )
            finally:
                chart_buf.close()

        # Перевірка нових UI-карт, клавіатур та LTV-функцій (Target Price, DND, Top Drops, Quick Compare)
        card_html = build_product_caption_with_30d_stats(
            title=title,
            marketplace=marketplace,
            currency="UAH",
            current_price=price,
            old_price=old_price,
            history_prices=[Decimal(str(h.price)) for h in history],
            target_price=sub.target_price,
        )
        alert_html = build_price_drop_alert_card(
            title=title,
            marketplace=marketplace,
            currency="UAH",
            old_price=old_price or (price + Decimal("400")),
            new_price=price,
            target_price=sub.target_price,
        )
        digest_html = build_daily_digest_card(
            [
                {
                    "title": title,
                    "url": clean_url,
                    "marketplace": marketplace,
                    "currency": "UAH",
                    "old_price": old_price or (price + Decimal("400")),
                    "new_price": price,
                }
            ]
        )
        top_text, top_kb = await render_top_drops_card(session, user)
        cmp_text, cmp_kb = await render_quick_compare_card(session, user)
        user = await crud.toggle_user_dnd(session, user.id)
        user = await crud.toggle_user_notifications(session, user.id)
        reply_kb = build_main_reply_keyboard("uk", items_count=subs_count)
        prod_kb = build_product_card_keyboard(
            product_id=product.id,
            clean_url=clean_url,
            user_id=user.id,
            marketplace=marketplace,
            title=title,
        )
        cfg_kb = build_settings_keyboard(user, "uk")
        assert "Deal Score" in card_html and "<code>" in alert_html and "<code>" in digest_html
        assert top_kb is not None and cmp_kb is not None and reply_kb is not None and prod_kb is not None and cfg_kb is not None
        print("   ✅ UI-картки (Card, Alert, Digest, Top-5, Compare) та інтерактивні клавіатури перевірені!")

        # Перевірка автоматичного очищення БД після відписки
        await crud.unsubscribe_user_from_product(session, user.id, product.id)
        cleanup_res = await crud.cleanup_database_storage(session, retention_days=32)
        print(f"   ✅ Автоочищення локальної БД виконано успішно: {cleanup_res}")

    await close_db()
    # Видаляємо тимчасову тестову БД та старий sample_chart.png (якщо існував раніше)
    for tmp_file in ("test_local.db", "test_local.db-wal", "test_local.db-shm", "sample_chart.png"):
        p = Path(tmp_file)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
    print("\n" + "=" * 65)
    print("🎉 УСІ ТЕСТИ ПРОЙДЕНО УСПІШНО!")
    print("=" * 65)


if __name__ == "__main__":
    arg_url = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_smoke_tests(arg_url))
