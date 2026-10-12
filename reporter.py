import asyncio
import logging
import time

from aiogram import Bot
from transmission_rpc.error import TransmissionConnectError, TransmissionError

from remotor.config import Settings
from remotor.db import Database, Job
from remotor.format import format_download, format_metadata, is_download_complete, one_line
from remotor.flow import (
    begin_download,
    drop_torrent,
    edit_job_message,
    fail_job,
    finish_job,
    metadata_expired,
)
from remotor.never import assert_never
from remotor.picker import render_picker
from remotor.profile import REPORTER_STATES
from remotor.transmission import TorrentGone, Transmission, TorrentSnapshot, safe_error
from remotor.tree import build_tree, dumps_tree

log = logging.getLogger(__name__)

_LOCAL_ERROR = 3
_METADATA_REANNOUNCE_SEC = 45


async def run_reporter(
    bot: Bot,
    db: Database,
    transmission: Transmission,
    settings: Settings,
) -> None:
    last_edit: dict[int, float] = {}
    last_reannounce: dict[int, float] = {}
    session_ready = False
    reported_down = False
    while True:
        try:
            if not session_ready:
                session_ready = await apply_saved_session(db, transmission)
                if session_ready:
                    reported_down = False
                elif not reported_down:
                    log.warning("не удалось применить настройки Transmission")
                    reported_down = True
            await tick(bot, db, transmission, settings, last_edit, last_reannounce)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("сбой цикла статуса")
        await asyncio.sleep(1)


async def apply_saved_session(db: Database, transmission: Transmission) -> bool:
    try:
        await transmission.apply_session(await db.get_profile())
    except TransmissionError:
        return False
    return True


async def tick(
    bot: Bot,
    db: Database,
    transmission: Transmission,
    settings: Settings,
    last_edit: dict[int, float],
    last_reannounce: dict[int, float],
) -> None:
    jobs = await db.jobs_in_states(REPORTER_STATES)
    for job in jobs:
        try:
            await tick_job(bot, db, transmission, settings, job, last_edit, last_reannounce)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("задача %s", job.id)


async def tick_job(
    bot: Bot,
    db: Database,
    transmission: Transmission,
    settings: Settings,
    job: Job,
    last_edit: dict[int, float],
    last_reannounce: dict[int, float],
) -> None:
    match job.state:
        case "metadata":
            await tick_metadata(bot, db, transmission, settings, job, last_edit, last_reannounce)
        case "downloading":
            await tick_download(bot, db, transmission, settings, job, last_edit)
        case "selecting" | "done" | "error" | "cancelled":
            return
        case _ as unreachable:
            assert_never(unreachable)


async def tick_metadata(
    bot: Bot,
    db: Database,
    transmission: Transmission,
    settings: Settings,
    job: Job,
    last_edit: dict[int, float],
    last_reannounce: dict[int, float],
) -> None:
    if not job.torrent_hash:
        await fail_job(bot, db, transmission, job, "У задачи нет хеша торрента.", remove=False)
        return
    if metadata_expired(job, settings.metadata_timeout_sec):
        await drop_torrent(transmission, job.torrent_hash, delete_data=False)
        await fail_job(
            bot,
            db,
            transmission,
            job,
            metadata_timeout_message(settings.metadata_timeout_sec),
            remove=False,
        )
        last_reannounce.pop(job.id, None)
        return
    try:
        snap, files = await transmission.inspect(job.torrent_hash)
    except TorrentGone:
        await fail_job(bot, db, transmission, job, "Торрент пропал из Transmission.", remove=False)
        return
    except TransmissionError as exc:
        log_rpc(exc, "опрос метаданных %s: %s", job.torrent_hash, safe_error(exc))
        return
    if snap.name and snap.name != job.name:
        await db.update_job(job.id, None, name=snap.name)
    if snap.metadata_percent >= 100 and not files:
        # Список файлов в RPC иногда появляется на тик позже metadataPercentComplete.
        return
    if not files:
        if snap.metadata_percent < 100 and snap.status == "stopped":
            try:
                await transmission.start(job.torrent_hash)
            except TransmissionError as exc:
                log_rpc(exc, "старт метаданных %s: %s", job.torrent_hash, safe_error(exc))
        if snap.metadata_percent < 100 and due(
            last_reannounce, job.id, _METADATA_REANNOUNCE_SEC
        ):
            try:
                await transmission.reannounce(job.torrent_hash)
            except TransmissionError as exc:
                log_rpc(exc, "reannounce %s: %s", job.torrent_hash, safe_error(exc))
        if due(last_edit, job.id, settings.status_interval_sec):
            await edit_job_message(
                bot,
                db,
                job,
                format_metadata(
                    snap.name or job.name,
                    snap.metadata_percent,
                    job.dest_dir,
                    snap.peers,
                ),
            )
        return
    fresh = await db.get_job(job.id)
    if fresh is None or fresh.state != "metadata" or not fresh.torrent_hash:
        return
    name = snap.name or fresh.name
    indexes = [item.index for item in files]
    if len(files) == 1:
        await edit_job_message(bot, db, fresh, f"Один файл, запускаю: {one_line(name)}")
        started = await begin_download(
            bot,
            db,
            transmission,
            fresh,
            name=name,
            wanted=set(indexes),
            all_indexes=indexes,
            expect="metadata",
        )
        if started:
            last_edit[fresh.id] = time.monotonic()
        return
    # Magnet links may download before the user picks files — pause until selection.
    try:
        await transmission.stop(job.torrent_hash)
    except TransmissionError as exc:
        log_rpc(exc, "не удалось поставить на паузу %s: %s", job.torrent_hash, safe_error(exc))
        return
    tree = build_tree(files)
    moved = await db.update_job(
        fresh.id,
        "metadata",
        state="selecting",
        name=name,
        tree_json=dumps_tree(tree),
        page=0,
        node_id=tree.root_id,
    )
    if not moved:
        return
    text, keyboard = render_picker(tree, tree.root_id, 0, fresh.id)
    await edit_job_message(bot, db, fresh, text, keyboard)
    last_edit.pop(fresh.id, None)


async def tick_download(
    bot: Bot,
    db: Database,
    transmission: Transmission,
    settings: Settings,
    job: Job,
    last_edit: dict[int, float],
) -> None:
    if not job.torrent_hash:
        await fail_job(bot, db, transmission, job, "У задачи нет хеша торрента.", remove=False)
        return
    try:
        snap = await transmission.snapshot(job.torrent_hash)
    except TorrentGone:
        fresh = await db.get_job(job.id)
        if fresh is not None and fresh.state == "downloading":
            await fail_job(
                bot,
                db,
                transmission,
                fresh,
                "Торрент пропал из Transmission до подтверждения завершения.",
                remove=False,
            )
        return
    except TransmissionError as exc:
        log_rpc(exc, "опрос загрузки %s: %s", job.torrent_hash, safe_error(exc))
        return
    fresh = await db.get_job(job.id)
    if fresh is None or fresh.state != "downloading":
        return
    if snap.status == "stopped":
        try:
            await transmission.start(fresh.torrent_hash or job.torrent_hash)
            snap = await transmission.snapshot(fresh.torrent_hash or job.torrent_hash)
        except TransmissionError as exc:
            log_rpc(exc, "не удалось возобновить %s: %s", job.torrent_hash, safe_error(exc))
    if snap.error_code == _LOCAL_ERROR:
        await drop_torrent(transmission, fresh.torrent_hash or job.torrent_hash, delete_data=False)
        await fail_job(
            bot,
            db,
            transmission,
            fresh,
            snap.error_string or "Локальная ошибка Transmission.",
            remove=False,
        )
        return
    if is_download_complete(snap.percent, snap.status):
        await edit_job_message(
            bot,
            db,
            fresh,
            download_text(snap, fresh),
        )
        await finish_job(bot, db, transmission, fresh, snap.name or fresh.name)
        return
    if snap.name and snap.name != fresh.name:
        await db.update_job(fresh.id, None, name=snap.name)
    if not due(last_edit, fresh.id, settings.status_interval_sec):
        return
    await edit_job_message(bot, db, fresh, download_text(snap, fresh))


def download_text(snap: TorrentSnapshot, job: Job) -> str:
    return format_download(
        snap.name or job.name,
        snap.percent,
        snap.rate_down,
        snap.rate_up,
        snap.eta_seconds,
        snap.downloaded,
        snap.total,
        snap.peers,
        job.dest_dir,
        snap.status,
        tracker_warning(snap),
    )


def tracker_warning(snap: TorrentSnapshot) -> str | None:
    if snap.error_code in {1, 2} and snap.error_string:
        return snap.error_string
    return None


def log_rpc(exc: TransmissionError, message: str, *args: object) -> None:
    if isinstance(exc, TransmissionConnectError):
        log.debug(message, *args)
        return
    log.warning(message, *args)


def metadata_timeout_message(timeout_sec: int) -> str:
    return (
        f"Не удалось получить список файлов за {timeout_sec} с. "
        "Для magnet нужен пир с метаданными (трекер через прокси, DHT). "
        "Надёжнее прислать файл .torrent."
    )


def due(last_edit: dict[int, float], job_id: int, interval: int) -> bool:
    now = time.monotonic()
    previous = last_edit.get(job_id)
    if previous is None:
        last_edit[job_id] = now
        return False
    if now - previous < interval:
        return False
    last_edit[job_id] = now
    return True
