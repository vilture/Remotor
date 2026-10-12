from typing import Any, Awaitable, Callable

from aiogram import Bot
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import TelegramObject, Update, User


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed: set[int]) -> None:
        self.allowed = allowed

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not isinstance(user, User) or user.id not in self.allowed:
            if isinstance(event, Update):
                await deny(data["bot"], event)
            return None
        return await handler(event, data)


async def deny(bot: Bot, update: Update) -> None:
    if update.message is not None:
        await bot.send_message(update.message.chat.id, "Нет доступа")
        return
    if update.edited_message is not None:
        await bot.send_message(update.edited_message.chat.id, "Нет доступа")
        return
    if update.callback_query is not None:
        await bot.answer_callback_query(
            update.callback_query.id,
            "Нет доступа",
            show_alert=True,
        )
