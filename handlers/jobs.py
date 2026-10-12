from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from remotor.config import Settings
from remotor.db import Database, Job
from remotor.format import clip, format_bytes, one_line, progress_bar
from remotor.flow import cancel_job
from remotor.profile import ACTIVE_STATES, state_label
from transmission_rpc.error import TransmissionError

from remotor.transmission import TorrentGone, Transmission, safe_error

router = Router(name="jobs")


def cancel_prompt(job_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Удалить файлы", callback_data=f"c:{job_id}:y"),
                InlineKeyboardButton(text="Оставить файлы", callback_data=f"c:{job_id}:n"),
            ]
        ]
    )


@router.message(Command("status"))
async def status(
    message: Message,
    state: FSMContext,
    db: Database,
    transmission: Transmission,
) -> None:
    await state.clear()
    user = message.from_user
    if user is None:
        return
    jobs = await db.jobs_for_user(user.id, ACTIVE_STATES)
    if not jobs:
        await message.answer("Нет активных загрузок.")
        return
    lines: list[str] = []
    for job in jobs:
        lines.append(await format_job(job, transmission))
    await message.answer("\n\n".join(lines))


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    user = message.from_user
    if user is None:
        return
    jobs = await db.jobs_for_user(user.id, ACTIVE_STATES)
    if not jobs:
        await message.answer("Нет активных загрузок.")
        return
    if len(jobs) == 1:
        job = jobs[0]
        await message.answer(
            f"Отменить {one_line(job.name)}?",
            reply_markup=cancel_prompt(job.id),
        )
        return
    rows = [
        [InlineKeyboardButton(text=clip(one_line(job.name), 60), callback_data=f"c:{job.id}")]
        for job in jobs
    ]
    await message.answer("Что отменить?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("c:"))
async def on_cancel(
    query: CallbackQuery,
    db: Database,
    transmission: Transmission,
) -> None:
    parsed = parse_cancel(query.data or "")
    if parsed is None or query.from_user is None or not isinstance(query.message, Message):
        await query.answer()
        return
    job_id, choice = parsed
    job = await db.get_job(job_id)
    if job is None or job.user_id != query.from_user.id:
        await query.answer("Загрузка не найдена", show_alert=True)
        return
    if choice is None:
        await query.message.edit_text(
            f"Отменить {one_line(job.name)}?",
            reply_markup=cancel_prompt(job.id),
        )
        await query.answer()
        return
    delete_data = choice == "y"
    text = await cancel_job(db, transmission, job, delete_data=delete_data)
    await query.message.edit_text(text)
    await query.answer()


async def format_job(job: Job, transmission: Transmission) -> str:
    header = f"{one_line(job.name)}\n{state_label(job.state)} → {job.dest_dir}"
    if job.state != "downloading" or not job.torrent_hash:
        return header
    try:
        snap = await transmission.snapshot(job.torrent_hash)
    except TorrentGone:
        return f"{header}\nТоррент не найден в Transmission."
    except TransmissionError as exc:
        return f"{header}\nTransmission: {safe_error(exc)}"
    percent = snap.percent
    return "\n".join(
        [
            header,
            f"{progress_bar(percent)}  {percent:.1f}%",
            f"↓ {format_bytes(snap.rate_down)}/с  пиры: {snap.peers}",
        ]
    )


def parse_cancel(data: str) -> tuple[int, str | None] | None:
    parts = data.split(":")
    if len(parts) < 2 or parts[0] != "c":
        return None
    try:
        job_id = int(parts[1])
    except ValueError:
        return None
    if len(parts) == 2:
        return job_id, None
    choice = parts[2]
    if choice != "y" and choice != "n":
        return None
    return job_id, choice
