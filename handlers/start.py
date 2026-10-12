from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

router = Router(name="start")

_HELP = "\n".join(
    [
        "Пришлите magnet-ссылку или файл .torrent.",
        "",
        "/folder — папка загрузки",
        "/status — текущие загрузки",
        "/cancel — отменить загрузку",
        "/settings — настройки Transmission",
    ]
)


@router.message(Command("start"))
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(_HELP)
