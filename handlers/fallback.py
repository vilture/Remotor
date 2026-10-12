from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

router = Router(name="fallback")


@router.message(F.text)
async def fallback_text(message: Message) -> None:
    await message.answer("Пришлите magnet-ссылку или файл .torrent. Команды: /start")


@router.callback_query()
async def fallback_callback(query: CallbackQuery) -> None:
    await query.answer()
