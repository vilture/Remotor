import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message
from transmission_rpc.error import TransmissionConnectError, TransmissionError

from remotor.config import Settings
from remotor.db import Database, Job, utcnow
from remotor.format import one_line
from remotor.magnet import magnet_hash
from remotor.paths import PathError, validate_dir
from remotor.profile import ACTIVE_STATES, JobState, SourceKind
from remotor.transmission import DuplicateTorrent, TorrentGone, Transmission, safe_error

log = logging.getLogger(__name__)

_EDIT_GONE = (
    "message to edit not found",
    "message can't be edited",
    "message_id_invalid",
    "message to be edited not found",
)


async def current_download_dir(db: Database, settings: Settings, user_id: int) -> str:
    stored = await db.get_download_dir(user_id)
    if stored:
        return stored
    return settings.default_download_dir


async def edit_job_message(
    bot: Bot,
    db: Database,
    job: Job,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    if job.message_id is None:
        await send_status(bot, db, job, text, keyboard)
        return
    try:
        kwargs: dict[str, object] = {}
        if keyboard is not None:
            kwargs["reply_markup"] = keyboard
        await bot.edit_message_text(
            text,
            chat_id=job.chat_id,
            message_id=job.message_id,
            **kwargs,
        )
    except TelegramBadRequest as exc:
        lowered = str(exc).lower()
        if "message is not modified" in lowered:
            return
        if any(part in lowered for part in _EDIT_GONE):
            await send_status(bot, db, job, text, keyboard)
            return
        log.warning("не удалось обновить статус задачи %s: %s", job.id, exc)


async def send_status(
    bot: Bot,
    db: Database,
    job: Job,
    text: str,
    keyboard: InlineKeyboardMarkup | None,
) -> None:
    kwargs: dict[str, object] = {}
    if keyboard is not None:
        kwargs["reply_markup"] = keyboard
    sent = await bot.send_message(job.chat_id, text, **kwargs)
    await db.update_job(job.id, None, message_id=sent.message_id)


async def fail_job(
    bot: Bot,
    db: Database,
    transmission: Transmission,
    job: Job,
    reason: str,
    *,
    remove: bool,
) -> None:
    fresh = await db.get_job(job.id)
    if fresh is None or fresh.state not in ACTIVE_STATES:
        return
    if remove and fresh.torrent_hash:
        await drop_torrent(transmission, fresh.torrent_hash, delete_data=False)
    moved = await db.update_job(
        fresh.id,
        fresh.state,
        state="error",
        error=reason,
        finished_at=utcnow(),
    )
    if not moved:
        return
    await bot.send_message(fresh.chat_id, f"Ошибка загрузки {one_line(fresh.name)}: {reason}")


async def finish_job(
    bot: Bot,
    db: Database,
    transmission: Transmission,
    job: Job,
    name: str,
) -> bool:
    fresh = await db.get_job(job.id)
    if fresh is None or fresh.state != "downloading":
        return False
    if fresh.torrent_hash:
        try:
            await transmission.stop(fresh.torrent_hash)
            await transmission.remove(fresh.torrent_hash, delete_data=False)
        except TransmissionError as exc:
            log.warning("не удалось снять раздачу %s: %s", fresh.torrent_hash, safe_error(exc))
            return False
    moved = await db.update_job(
        fresh.id,
        "downloading",
        state="done",
        name=name,
        finished_at=utcnow(),
        error=None,
    )
    if not moved:
        return False
    await bot.send_message(
        fresh.chat_id,
        f"Готово: {one_line(name)}\nПапка: {fresh.dest_dir}",
    )
    return True


async def begin_download(
    bot: Bot,
    db: Database,
    transmission: Transmission,
    job: Job,
    *,
    name: str,
    wanted: set[int],
    all_indexes: list[int],
    expect: JobState,
) -> bool:
    fresh = await db.get_job(job.id)
    if fresh is None or fresh.state != expect or not fresh.torrent_hash or not wanted:
        return False
    profile = await db.get_profile()
    unwanted = [index for index in all_indexes if index not in wanted]
    try:
        await transmission.arm(fresh.torrent_hash, sorted(wanted), unwanted, profile)
        await transmission.start(fresh.torrent_hash)
    except TransmissionConnectError:
        log.warning("нет связи с Transmission при старте задачи %s", fresh.id)
        return False
    except TorrentGone:
        await fail_job(
            bot,
            db,
            transmission,
            fresh,
            "Торрент пропал из Transmission.",
            remove=False,
        )
        return False
    except TransmissionError as exc:
        await fail_job(bot, db, transmission, fresh, safe_error(exc), remove=True)
        return False
    sent = await bot.send_message(fresh.chat_id, f"Загрузка начата: {one_line(name)}")
    return await db.update_job(
        fresh.id,
        expect,
        state="downloading",
        name=name,
        message_id=sent.message_id,
        error=None,
    )


async def cancel_job(
    db: Database,
    transmission: Transmission,
    job: Job,
    *,
    delete_data: bool,
) -> str:
    fresh = await db.get_job(job.id)
    if fresh is None or fresh.state not in ACTIVE_STATES:
        return "Эта загрузка уже завершена."
    if fresh.torrent_hash:
        try:
            await transmission.stop(fresh.torrent_hash)
            await transmission.remove(fresh.torrent_hash, delete_data=delete_data)
        except TransmissionError as exc:
            return f"Не удалось отменить в Transmission: {safe_error(exc)}"
    moved = await db.update_job(
        fresh.id,
        fresh.state,
        state="cancelled",
        finished_at=utcnow(),
        error=None,
    )
    if not moved:
        return "Эта загрузка уже завершена."
    return f"Загрузка отменена: {one_line(fresh.name)}"


async def start_download(
    message: Message,
    db: Database,
    transmission: Transmission,
    settings: Settings,
    source: str | Path,
    kind: SourceKind,
) -> None:
    user = message.from_user
    if user is None:
        return
    dest_raw = await current_download_dir(db, settings, user.id)
    try:
        dest = validate_dir(
            dest_raw,
            settings.allowed_roots,
            settings.transmission_system_user,
        )
    except PathError as exc:
        await message.answer(
            f"Папка загрузки недоступна: {exc.message}\nСмените её командой /folder"
        )
        return
    try:
        torrent_hash, name = await transmission.add(source, str(dest))
    except DuplicateTorrent as exc:
        await describe_duplicate(message, db, transmission, exc.torrent_hash, source)
        return
    except TransmissionError as exc:
        log.warning("не удалось добавить торрент: %s", safe_error(exc))
        await message.answer(f"Не удалось добавить торрент: {safe_error(exc)}")
        return
    sent = await message.answer(f"Получаю список файлов: {one_line(name)}")
    await db.create_job(
        user_id=user.id,
        chat_id=message.chat.id,
        message_id=sent.message_id,
        torrent_hash=torrent_hash,
        name=name,
        source=kind,
        dest_dir=str(dest),
        state="metadata",
    )


async def save_torrent_upload(message: Message, destination: Path) -> bool:
    document = message.document
    if document is None or message.bot is None:
        return False
    remote = await message.bot.get_file(document.file_id)
    if remote.file_path is None:
        await message.answer("Не удалось получить файл.")
        return False
    await message.bot.download_file(remote.file_path, destination=destination)
    return True


def temporary_torrent_path() -> Path:
    descriptor, name = tempfile.mkstemp(suffix=".torrent")
    os.close(descriptor)
    return Path(name)


async def describe_duplicate(
    message: Message,
    db: Database,
    transmission: Transmission,
    torrent_hash: str | None,
    source: str | Path,
) -> None:
    found = torrent_hash
    if found is None and isinstance(source, str):
        found = magnet_hash(source)
    if found is not None:
        job = await db.find_active_by_hash(found)
        if job is not None:
            await message.answer(f"Этот торрент уже загружается: {one_line(job.name)}")
            return
        try:
            snap = await transmission.snapshot(found)
        except (TorrentGone, TransmissionError):
            snap = None
        if snap is not None:
            await message.answer(f"Этот торрент уже есть в Transmission: {one_line(snap.name)}")
            return
    await message.answer("Этот торрент уже есть в Transmission.")


async def drop_torrent(
    transmission: Transmission,
    torrent_hash: str,
    *,
    delete_data: bool,
) -> None:
    try:
        await transmission.stop(torrent_hash)
        await transmission.remove(torrent_hash, delete_data=delete_data)
    except TransmissionError as exc:
        log.warning("не удалось убрать торрент %s: %s", torrent_hash, safe_error(exc))


def metadata_expired(job: Job, timeout_sec: int) -> bool:
    created = datetime.fromisoformat(job.created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created
    return age.total_seconds() >= timeout_sec
