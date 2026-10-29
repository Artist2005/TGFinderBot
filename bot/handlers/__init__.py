from __future__ import annotations

from aiogram import Router

from bot.handlers.callbacks import router as callbacks_router
from bot.handlers.commands import router as commands_router
from bot.handlers.links import router as links_router
from bot.handlers.payments import router as payments_router


def setup_routers() -> Router:
    """Об'єднує всі модульні роутери бота у головний роутер."""
    root_router = Router(name="root_router")
    root_router.include_router(payments_router)
    root_router.include_router(commands_router)
    root_router.include_router(callbacks_router)
    root_router.include_router(links_router)
    return root_router
