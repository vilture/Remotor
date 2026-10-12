from dataclasses import replace
from typing import Literal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from transmission_rpc.error import TransmissionError

from remotor.config import Settings
from remotor.db import Database
from remotor.flow import start_download
from remotor.format import format_profile
from remotor.magnet import is_magnet
from remotor.messages import edit_text
from remotor.never import assert_never
from remotor.profile import DownloadProfile, next_encryption
from remotor.transmission import Transmission, safe_error

router = Router(name="settings")

ToggleAction = Literal["dht", "pex", "pf", "enc", "seq"]


class SettingsState(StatesGroup):
    waiting_limit = State()


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="DHT", callback_data="set:dht")],
            [InlineKeyboardButton(text="PEX", callback_data="set:pex")],
            [InlineKeyboardButton(text="Port mapping", callback_data="set:pf")],
            [InlineKeyboardButton(text="Шифрование", callback_data="set:enc")],
            [InlineKeyboardButton(text="Последовательно", callback_data="set:seq")],
            [InlineKeyboardButton(text="Лимит скачивания", callback_data="set:lim")],
            [InlineKeyboardButton(text="Сброс", callback_data="set:reset")],
        ]
    )


@router.message(Command("settings"))
async def show_settings(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    profile = await db.get_profile()
    await message.answer(format_profile(profile), reply_markup=settings_keyboard())


@router.callback_query(F.data.startswith("set:"))
async def on_setting(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
    transmission: Transmission,
) -> None:
    action = (query.data or "").split(":", 1)[-1]
    if not isinstance(query.message, Message):
        await query.answer()
        return
    await state.clear()
    match action:
        case "lim":
            await state.set_state(SettingsState.waiting_limit)
            await query.message.answer("Пришлите лимит скачивания в КБ/с. 0 — без лимита.")
            await query.answer()
            return
        case "reset":
            profile = await db.reset_profile()
        case "dht" | "pex" | "pf" | "enc" | "seq":
            profile = apply_toggle(await db.get_profile(), action)
            await db.save_profile(profile)
        case _:
            await query.answer()
            return
    problem = await push_profile(transmission, profile)
    text = format_profile(profile)
    if problem is not None:
        text = f"{text}\nНе применено к демону: {problem}"
    await edit_text(query.message, text, settings_keyboard())
    await query.answer()


@router.message(SettingsState.waiting_limit, F.text)
async def set_limit(
    message: Message,
    state: FSMContext,
    db: Database,
    transmission: Transmission,
    settings: Settings,
) -> None:
    raw = (message.text or "").strip()
    if is_magnet(raw):
        await state.clear()
        await start_download(message, db, transmission, settings, raw, "magnet")
        return
    parsed = parse_limit(raw)
    if parsed is None:
        if raw.isdigit() and int(raw) > 1_000_000:
            await message.answer("Слишком большой лимит.")
        else:
            await message.answer("Нужно целое число КБ/с или 0.")
        return
    profile = replace(await db.get_profile(), download_limit_kb=parsed[0])
    await db.save_profile(profile)
    await state.clear()
    problem = await push_profile(transmission, profile)
    text = format_profile(profile)
    if problem is not None:
        text = f"{text}\nНе применено к демону: {problem}"
    await message.answer(text, reply_markup=settings_keyboard())


def parse_limit(raw: str) -> tuple[int | None] | None:
    if raw in {"0", "нет", "без лимита"}:
        return (None,)
    if not raw.isdigit():
        return None
    value = int(raw)
    if value == 0:
        return (None,)
    if value > 1_000_000:
        return None
    return (value,)


def apply_toggle(profile: DownloadProfile, action: ToggleAction) -> DownloadProfile:
    match action:
        case "dht":
            return replace(profile, dht=not profile.dht)
        case "pex":
            return replace(profile, pex=not profile.pex)
        case "pf":
            return replace(profile, port_forwarding=not profile.port_forwarding)
        case "enc":
            return replace(profile, encryption=next_encryption(profile.encryption))
        case "seq":
            return replace(profile, sequential_download=not profile.sequential_download)
        case _ as unreachable:
            assert_never(unreachable)


async def push_profile(transmission: Transmission, profile: DownloadProfile) -> str | None:
    try:
        await transmission.apply_session(profile)
    except TransmissionError as exc:
        return safe_error(exc)
    return None
