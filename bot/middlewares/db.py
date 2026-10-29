from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser

from database import crud
from database.session import async_session_factory


class DbSessionMiddleware(BaseMiddleware):
    """
    Outer-middleware, що відкриває асинхронну сесію SQLAlchemy на час обробки апдейту,
    гарантує автоматичний rollback при будь-якому необробленому винятку (захист від
    висячих транзакцій та вичерпання пулу з'єднань) і синхронізує профіль користувача.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with async_session_factory() as session:
            try:
                data["session"] = session

                tg_user: TgUser | None = data.get("event_from_user")
                if tg_user is not None and not tg_user.is_bot:
                    db_user = await crud.get_or_create_user(
                        session=session,
                        user_id=tg_user.id,
                        username=tg_user.username,
                        language_code=tg_user.language_code,
                    )
                    data["db_user"] = db_user

                return await handler(event, data)
            except Exception:
                await session.rollback()
                raise
