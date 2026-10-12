from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from remotor.config import Settings
from remotor.db import Database
from remotor.flow import save_torrent_upload, start_download, temporary_torrent_path
from remotor.magnet import is_magnet
from remotor.transmission import Transmission

router = Router(name="torrent")


def is_torrent_document(message: Message) -> bool:
    document = message.document
    if document is None:
        return False
    name = (document.file_name or "").lower()
    if name.endswith(".torrent"):
        return True
    mime = (document.mime_type or "").lower()
    return mime == "application/x-bittorrent"


@router.message(F.text.func(is_magnet))
async def on_magnet(
    message: Message,
    state: FSMContext,
    db: Database,
    transmission: Transmission,
    settings: Settings,
) -> None:
    await state.clear()
    await start_download(message, db, transmission, settings, (message.text or "").strip(), "magnet")


@router.message(F.document)
async def on_document(
    message: Message,
    state: FSMContext,
    db: Database,
    transmission: Transmission,
    settings: Settings,
) -> None:
    if not is_torrent_document(message):
        await message.answer("Нужен файл .torrent или magnet-ссылка.")
        return
    await state.clear()
    path = temporary_torrent_path()
    try:
        saved = await save_torrent_upload(message, path)
        if not saved:
            return
        await start_download(message, db, transmission, settings, path, "torrent")
    finally:
        path.unlink(missing_ok=True)
