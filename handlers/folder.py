from pathlib import Path
from typing import Literal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from remotor.config import Settings
from remotor.db import Database
from remotor.flow import current_download_dir, start_download
from remotor.magnet import is_magnet
from remotor.messages import edit_text
from remotor.never import assert_never
from remotor.paths import (
    PathError,
    browse_start,
    list_child_dirs,
    parent_in_jail,
    resolve_in_jail,
    validate_dir,
)
from remotor.transmission import Transmission

router = Router(name="folder")

_PAGE = 6
DirAction = Literal["type", "def", "browse", "c", "s", "u", "o", "p"]


class FolderState(StatesGroup):
    waiting_path = State()
    browsing = State()


@router.message(Command("folder"))
async def folder_command(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    await state.clear()
    user = message.from_user
    if user is None:
        return
    await message.answer(
        await folder_text(db, settings, user.id),
        reply_markup=folder_keyboard(),
    )


@router.callback_query(F.data.startswith("dir:"))
async def on_dir(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    parsed = parse_dir(query.data or "")
    if parsed is None or query.from_user is None or not isinstance(query.message, Message):
        await query.answer()
        return
    action, arg = parsed
    match action:
        case "type":
            await state.set_state(FolderState.waiting_path)
            await query.message.answer("Пришлите абсолютный путь к папке.")
            await query.answer()
        case "def":
            await state.clear()
            await db.set_download_dir(query.from_user.id, None)
            await edit_text(
                query.message,
                f"Папка по умолчанию:\n{settings.default_download_dir}",
                folder_keyboard(),
            )
            await query.answer("Папка сброшена")
        case "browse":
            await open_browse(query, state, db, settings, query.from_user.id)
        case "c":
            await state.clear()
            await edit_text(
                query.message,
                await folder_text(db, settings, query.from_user.id),
                folder_keyboard(),
            )
            await query.answer()
        case "s":
            await choose_browsed(query, state, db, settings)
        case "u":
            await browse_up(query, state, settings)
        case "o":
            await browse_open(query, state, settings, arg)
        case "p":
            await browse_page(query, state, settings, arg)
        case _ as unreachable:
            assert_never(unreachable)


@router.message(FolderState.waiting_path, F.text)
async def on_path(
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
    user = message.from_user
    if user is None:
        return
    try:
        path = validate_dir(raw, settings.allowed_roots, settings.transmission_system_user)
    except PathError as exc:
        await message.answer(exc.message)
        return
    await db.set_download_dir(user.id, str(path))
    await state.clear()
    await message.answer(f"Папка сохранена:\n{path}")


async def open_browse(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
    user_id: int,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return
    current = await current_download_dir(db, settings, user_id)
    try:
        start = browse_start(current, settings.allowed_roots)
    except PathError as exc:
        await query.answer(exc.message, show_alert=True)
        return
    await state.set_state(FolderState.browsing)
    await show_browse(query.message, state, settings, start, 0)
    await query.answer()


async def choose_browsed(
    query: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    if query.from_user is None or not isinstance(query.message, Message):
        await query.answer()
        return
    data = await state.get_data()
    current = data.get("browse_path")
    if not isinstance(current, str):
        await query.answer("Откройте обзор заново", show_alert=True)
        return
    try:
        path = validate_dir(current, settings.allowed_roots, settings.transmission_system_user)
    except PathError as exc:
        await query.answer(exc.message, show_alert=True)
        return
    await db.set_download_dir(query.from_user.id, str(path))
    await state.clear()
    await query.message.edit_text(f"Папка сохранена:\n{path}", reply_markup=folder_keyboard())
    await query.answer()


async def browse_up(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return
    data = await state.get_data()
    current = data.get("browse_path")
    if not isinstance(current, str):
        await query.answer("Откройте обзор заново", show_alert=True)
        return
    try:
        path = resolve_in_jail(current, settings.allowed_roots)
    except PathError as exc:
        await query.answer(exc.message, show_alert=True)
        return
    parent = parent_in_jail(path, settings.allowed_roots)
    if parent is None:
        await query.answer("Выше разрешённого каталога нельзя", show_alert=True)
        return
    await show_browse(query.message, state, settings, parent, 0)
    await query.answer()


async def browse_open(
    query: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    arg: str | None,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return
    index = parse_index(arg)
    data = await state.get_data()
    names = data.get("browse_names")
    current = data.get("browse_path")
    if index is None or not isinstance(names, list) or not isinstance(current, str):
        await query.answer("Откройте обзор заново", show_alert=True)
        return
    if index < 0 or index >= len(names) or not isinstance(names[index], str):
        await query.answer("Папка уже не на этом месте", show_alert=True)
        return
    try:
        base = resolve_in_jail(current, settings.allowed_roots)
        path = resolve_in_jail(str(base / names[index]), settings.allowed_roots)
    except PathError as exc:
        await query.answer(exc.message, show_alert=True)
        return
    await show_browse(query.message, state, settings, path, 0)
    await query.answer()


async def browse_page(
    query: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    arg: str | None,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return
    page = parse_index(arg)
    data = await state.get_data()
    current = data.get("browse_path")
    if page is None or not isinstance(current, str):
        await query.answer("Откройте обзор заново", show_alert=True)
        return
    try:
        path = resolve_in_jail(current, settings.allowed_roots)
    except PathError as exc:
        await query.answer(exc.message, show_alert=True)
        return
    await show_browse(query.message, state, settings, path, page)
    await query.answer()


async def show_browse(
    message: Message,
    state: FSMContext,
    settings: Settings,
    path: Path,
    page: int,
) -> None:
    try:
        names = list_child_dirs(path)
    except PathError as exc:
        await edit_text(message, exc.message, folder_keyboard())
        return
    pages = max(1, (len(names) + _PAGE - 1) // _PAGE) if names else 1
    current = min(max(page, 0), pages - 1)
    await state.set_state(FolderState.browsing)
    await state.update_data(browse_path=str(path), browse_names=names, browse_page=current)
    parent = parent_in_jail(path, settings.allowed_roots)
    text = f"Обзор:\n{path}"
    if not names:
        text += "\nНет вложенных папок. Можно выбрать эту."
    await edit_text(
        message,
        text,
        browse_keyboard(names, current, pages, parent is not None),
    )


def folder_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Обзор", callback_data="dir:browse")],
            [InlineKeyboardButton(text="Ввести путь", callback_data="dir:type")],
            [InlineKeyboardButton(text="По умолчанию", callback_data="dir:def")],
        ]
    )


def browse_keyboard(
    names: list[str],
    page: int,
    pages: int,
    can_up: bool,
) -> InlineKeyboardMarkup:
    start = page * _PAGE
    rows: list[list[InlineKeyboardButton]] = []
    for offset, name in enumerate(names[start : start + _PAGE]):
        rows.append(
            [
                InlineKeyboardButton(
                    text=name[:60],
                    callback_data=f"dir:o:{start + offset}",
                )
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if can_up:
        nav.append(InlineKeyboardButton(text="Вверх", callback_data="dir:u"))
    if pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton(text="<", callback_data=f"dir:p:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=f"dir:p:{page}"))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton(text=">", callback_data=f"dir:p:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="Выбрать эту папку", callback_data="dir:s")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="dir:c")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def folder_text(db: Database, settings: Settings, user_id: int) -> str:
    current = await current_download_dir(db, settings, user_id)
    lines = [
        f"Папка загрузки:\n{current}",
        f"По умолчанию: {settings.default_download_dir}",
    ]
    try:
        validate_dir(current, settings.allowed_roots, settings.transmission_system_user)
    except PathError as exc:
        lines.append(exc.message)
    return "\n".join(lines)


def parse_dir(data: str) -> tuple[DirAction, str | None] | None:
    parts = data.split(":", 2)
    if len(parts) < 2 or parts[0] != "dir":
        return None
    action = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    if (
        action == "type"
        or action == "def"
        or action == "browse"
        or action == "c"
        or action == "s"
        or action == "u"
        or action == "o"
        or action == "p"
    ):
        return action, arg
    return None


def parse_index(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
