from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import subprocess
import sys

_PROJECT_DIR = Path(__file__).resolve().parent
os.chdir(_PROJECT_DIR)
_PID_FILE = _PROJECT_DIR / ".bot.pid"
_VENV_PYTHON = _PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
_VENV_SITE_PACKAGES = _PROJECT_DIR / ".venv" / "Lib" / "site-packages"


def _stop_previous_bot_instance() -> None:
    """Зупиняє попередній фоновий процес бота (якщо залишився після перезапуску)."""
    current_pid = os.getpid()
    parent_pid = os.getppid()
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
            if old_pid > 0 and old_pid not in (current_pid, parent_pid):
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(old_pid)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        except Exception:
            pass
    try:
        _PID_FILE.write_text(str(current_pid), encoding="utf-8")
    except Exception:
        pass


# Підключаємо site-packages з .venv напряму (щоб і IDLE, і звичайний `python main.py` працювали в одному процесі)
if _VENV_SITE_PACKAGES.exists() and str(_VENV_SITE_PACKAGES) not in sys.path:
    sys.path.insert(0, str(_VENV_SITE_PACKAGES))

# Резервне перемикання на .venv\Scripts\python.exe лише якщо поточний інтерпретатор не бачить aiogram
if _VENV_PYTHON.exists() and Path(sys.executable).resolve() != _VENV_PYTHON.resolve() and "idlelib" not in sys.modules:
    try:
        import aiogram  # noqa: F401
    except ImportError:
        raise SystemExit(subprocess.call([str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from aiogram.utils.token import TokenValidationError

from bot.handlers import setup_routers
from bot.middlewares import DbSessionMiddleware
from config import settings
from database.session import close_db, init_db
from services.scheduler import PriceCheckScheduler
from services.scraper import scraper_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def set_bot_commands(bot: Bot) -> None:
    """Реєструє локалізоване меню команд у клієнті Telegram (UK / EN / RU)."""
    commands_uk = [
        BotCommand(command="start", description="Запустити бота / головне меню"),
        BotCommand(command="my_tracking", description="📦 Мої товари у відстеженні"),
        BotCommand(command="pro", description="⭐️ Тариф PRO (Telegram Stars)"),
        BotCommand(command="ref", description="🎁 Запросити друга (+7 днів PRO)"),
        BotCommand(command="export", description="📥 Експорт аналітики в CSV/Excel"),
        BotCommand(command="lang", description="🌐 Змінити мову (UA / EN / RU)"),
        BotCommand(command="admin", description="🛡 Статистика платформи"),
    ]
    commands_en = [
        BotCommand(command="start", description="Start bot / main menu"),
        BotCommand(command="my_tracking", description="📦 My tracked products"),
        BotCommand(command="pro", description="⭐️ PRO plan (Telegram Stars)"),
        BotCommand(command="ref", description="🎁 Invite a friend (+7d PRO)"),
        BotCommand(command="export", description="📥 Export analytics to CSV/Excel"),
        BotCommand(command="lang", description="🌐 Change language (UA / EN / RU)"),
        BotCommand(command="admin", description="🛡 Platform dashboard"),
    ]
    commands_ru = [
        BotCommand(command="start", description="Запустить бота / главное меню"),
        BotCommand(command="my_tracking", description="📦 Мои отслеживаемые товары"),
        BotCommand(command="pro", description="⭐️ Тариф PRO (Telegram Stars)"),
        BotCommand(command="ref", description="🎁 Пригласить друга (+7 дн. PRO)"),
        BotCommand(command="export", description="📥 Экспорт аналитики в CSV/Excel"),
        BotCommand(command="lang", description="🌐 Сменить язык (UA / EN / RU)"),
        BotCommand(command="admin", description="🛡 Статистика платформы"),
    ]
    await bot.set_my_commands(commands_uk)
    await bot.set_my_commands(commands_uk, language_code="uk")
    await bot.set_my_commands(commands_en, language_code="en")
    await bot.set_my_commands(commands_ru, language_code="ru")


async def interactive_console_demo() -> None:
    """Інтерактивний консольний режим тестування без Telegram-токена."""
    from test_smoke import run_smoke_tests

    print("\n" + "=" * 65)
    print("🧪 ЗАПУСК ДЕМО-РЕЖИМУ В КОНСОЛІ (без Telegram-токена)")
    print("=" * 65)
    user_url = input(
        "\nВставте посилання на товар з будь-якого магазину (або натисніть Enter для тестового товару): "
    ).strip()
    await run_smoke_tests(user_url if user_url else None)


async def main() -> None:
    """Точка входу застосунку з підтримкою Graceful Shutdown."""
    _stop_previous_bot_instance()
    raw_token = settings.bot_token.get_secret_value().strip()
    if not raw_token or "your_bot_token_here" in raw_token:
        print("\n" + "=" * 65)
        print("⚙️ НАЛАШТУВАННЯ PRICE TRACKER BOT")
        print("=" * 65)
        print("У файлі .env ще не вказано ваш реальний BOT_TOKEN від @BotFather.")
        entered = input(
            "\n👉 Вставте BOT_TOKEN сюди (або просто натисніть Enter, щоб запустити демо-тест у консолі): "
        ).strip()
        if not entered:
            await interactive_console_demo()
            return

        # Зберігаємо введений токен у .env для наступних запусків
        env_path = Path(__file__).resolve().parent / ".env"
        if env_path.exists():
            env_text = env_path.read_text(encoding="utf-8")
            env_text = env_text.replace(raw_token, entered)
            env_path.write_text(env_text, encoding="utf-8")
        raw_token = entered

    try:
        bot = Bot(
            token=raw_token,
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML,
                link_preview_is_disabled=True,
            ),
        )
    except TokenValidationError:
        logger.error(
            "❌ Некоректний формат BOT_TOKEN у файлі .env! Отримайте токен у @BotFather (формат 123456789:AAH...)."
        )
        return

    dp = Dispatcher(storage=MemoryStorage())

    # Реєстрація Middleware сесій бази даних для всіх типів оновлень (включно з платежами)
    db_middleware = DbSessionMiddleware()
    dp.message.middleware(db_middleware)
    dp.callback_query.middleware(db_middleware)
    dp.pre_checkout_query.middleware(db_middleware)

    # Підключення роутерів
    dp.include_router(setup_routers())

    scheduler = PriceCheckScheduler(bot=bot)

    try:
        logger.info("Ініціалізація бази даних та сервісів...")
        await init_db()
        await scraper_service.start()
        scheduler.start()
        await set_bot_commands(bot)

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Price Tracker Bot успішно запущено в режимі Long Polling.")
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            handle_signals=True,
        )
    finally:
        logger.info("Виконується Graceful Shutdown (SIGINT/SIGTERM)...")
        scheduler.shutdown(wait=True)
        await scraper_service.close()
        await close_db()
        await bot.session.close()
        if _PID_FILE.exists():
            try:
                _PID_FILE.unlink()
            except Exception:
                pass
        logger.info("Усі ресурси (БД, HTTPX, APScheduler, Bot) коректно закрито.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Роботу бота завершено користувачем.")
