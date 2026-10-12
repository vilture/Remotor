from typing import Literal

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from remotor.db import Database, Job
from remotor.flow import begin_download
from remotor.format import one_line
from remotor.handlers.jobs import cancel_prompt
from remotor.messages import edit_text
from remotor.never import assert_never
from remotor.picker import render_picker
from remotor.transmission import Transmission
from remotor.tree import FileTree, dumps_tree, loads_tree, select_all, select_none, toggle

router = Router(name="files")

FileAction = Literal["t", "d", "p", "a", "n", "g", "x", "u"]


@router.callback_query(F.data.startswith("f:"))
async def on_files(
    query: CallbackQuery,
    bot: Bot,
    db: Database,
    transmission: Transmission,
) -> None:
    parsed = parse_file_callback(query.data or "")
    if parsed is None or query.from_user is None or not isinstance(query.message, Message):
        await query.answer()
        return
    job_id, action, arg = parsed
    job = await db.get_job(job_id)
    if job is None or job.user_id != query.from_user.id:
        await query.answer("Загрузка не найдена", show_alert=True)
        return
    if job.state != "selecting" or not job.tree_json:
        await query.answer("Выбор файлов уже закрыт", show_alert=True)
        return
    try:
        tree = loads_tree(job.tree_json)
    except (ValueError, KeyError, TypeError):
        await query.answer("Не удалось открыть список файлов", show_alert=True)
        return
    await dispatch_file_action(query, bot, db, transmission, job, tree, action, arg)


async def dispatch_file_action(
    query: CallbackQuery,
    bot: Bot,
    db: Database,
    transmission: Transmission,
    job: Job,
    tree: FileTree,
    action: FileAction,
    arg: int | None,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return
    match action:
        case "t" | "d" | "p":
            if arg is None:
                await query.answer("Некорректная кнопка", show_alert=True)
                return
            await move_selection(query, db, job, tree, action, arg)
        case "a":
            select_all(tree)
            await save_and_show(query, db, job, tree, job.node_id, job.page)
        case "n":
            select_none(tree)
            await save_and_show(query, db, job, tree, job.node_id, job.page)
        case "u":
            node = tree.nodes.get(job.node_id)
            parent = node.parent_id if node is not None else tree.root_id
            target = tree.root_id if parent is None else parent
            await save_and_show(query, db, job, tree, target, 0)
        case "g":
            await start_selected(query, bot, db, transmission, job, tree)
        case "x":
            await edit_text(
                query.message,
                f"Отменить {one_line(job.name)}?",
                cancel_prompt(job.id),
            )
            await query.answer()
        case _ as unreachable:
            assert_never(unreachable)


async def move_selection(
    query: CallbackQuery,
    db: Database,
    job: Job,
    tree: FileTree,
    action: Literal["t", "d", "p"],
    arg: int,
) -> None:
    match action:
        case "t":
            node = tree.nodes.get(arg)
            if node is None:
                await query.answer("Файл не найден", show_alert=True)
                return
            toggle(tree, node)
            await save_and_show(query, db, job, tree, job.node_id, job.page)
        case "d":
            node = tree.nodes.get(arg)
            if node is None or not node.is_dir:
                await query.answer("Папка не найдена", show_alert=True)
                return
            await save_and_show(query, db, job, tree, node.id, 0)
        case "p":
            await save_and_show(query, db, job, tree, job.node_id, arg)
        case _ as unreachable:
            assert_never(unreachable)


async def start_selected(
    query: CallbackQuery,
    bot: Bot,
    db: Database,
    transmission: Transmission,
    job: Job,
    tree: FileTree,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return
    if not tree.wanted:
        await query.answer("Выберите хотя бы один файл", show_alert=True)
        return
    started = await begin_download(
        bot,
        db,
        transmission,
        job,
        name=job.name,
        wanted=set(tree.wanted),
        all_indexes=list(tree.nodes[tree.root_id].file_indexes),
        expect="selecting",
    )
    if started:
        await query.message.edit_text("Загрузка запущена.")
        await query.answer()
        return
    fresh = await db.get_job(job.id)
    if fresh is not None and fresh.state == "selecting":
        await query.answer("Не удалось запустить загрузку", show_alert=True)
        return
    await query.answer()


async def save_and_show(
    query: CallbackQuery,
    db: Database,
    job: Job,
    tree: FileTree,
    node_id: int,
    page: int,
) -> None:
    if not isinstance(query.message, Message):
        await query.answer()
        return
    if node_id not in tree.nodes:
        await query.answer("Папка не найдена", show_alert=True)
        return
    moved = await db.update_job(
        job.id,
        "selecting",
        tree_json=dumps_tree(tree),
        page=page,
        node_id=node_id,
    )
    if not moved:
        await query.answer("Выбор файлов уже закрыт", show_alert=True)
        return
    text, keyboard = render_picker(tree, node_id, page, job.id)
    await edit_text(query.message, text, keyboard)
    await query.answer()


def parse_file_callback(data: str) -> tuple[int, FileAction, int | None] | None:
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "f":
        return None
    try:
        job_id = int(parts[1])
    except ValueError:
        return None
    action = parts[2]
    arg: int | None = None
    if len(parts) > 3:
        try:
            arg = int(parts[3])
        except ValueError:
            return None
    if (
        action == "t"
        or action == "d"
        or action == "p"
        or action == "a"
        or action == "n"
        or action == "g"
        or action == "x"
        or action == "u"
    ):
        return job_id, action, arg
    return None
