from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from remotor.format import clip, format_bytes
from remotor.never import assert_never
from remotor.tree import (
    PAGE_SIZE,
    FileTree,
    Selection,
    children_page,
    selection_of,
)


def file_callback(job_id: int, action: str, arg: int | None = None) -> str:
    if arg is None:
        data = f"f:{job_id}:{action}"
    else:
        data = f"f:{job_id}:{action}:{arg}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("callback_data is longer than 64 bytes")
    return data


def mark(state: Selection) -> str:
    match state:
        case Selection.ALL:
            return "[x]"
        case Selection.NONE:
            return "[ ]"
        case Selection.SOME:
            return "[~]"
        case _ as unreachable:
            assert_never(unreachable)


def render_picker(
    tree: FileTree, node_id: int, page: int, job_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    node = tree.nodes[node_id]
    title = "Весь торрент" if node.parent_id is None else node.name
    kids, current, pages = children_page(tree, node_id, page, PAGE_SIZE)
    total = len(tree.nodes[tree.root_id].file_indexes)
    chosen = len(tree.wanted)
    rows: list[list[InlineKeyboardButton]] = []
    for child in kids:
        state = selection_of(tree, child)
        if child.is_dir:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=mark(state),
                        callback_data=file_callback(job_id, "t", child.id),
                    ),
                    InlineKeyboardButton(
                        text=clip(f"{child.name} ({format_bytes(child.size)})", 60),
                        callback_data=file_callback(job_id, "d", child.id),
                    ),
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=clip(
                            f"{mark(state)} {child.name} ({format_bytes(child.size)})",
                            64,
                        ),
                        callback_data=file_callback(job_id, "t", child.id),
                    )
                ]
            )
    nav: list[InlineKeyboardButton] = []
    if node.parent_id is not None:
        nav.append(InlineKeyboardButton(text="Назад", callback_data=file_callback(job_id, "u")))
    if pages > 1:
        if current > 0:
            nav.append(
                InlineKeyboardButton(
                    text="<",
                    callback_data=file_callback(job_id, "p", current - 1),
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{current + 1}/{pages}",
                callback_data=file_callback(job_id, "p", current),
            )
        )
        if current + 1 < pages:
            nav.append(
                InlineKeyboardButton(
                    text=">",
                    callback_data=file_callback(job_id, "p", current + 1),
                )
            )
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text="Весь торрент", callback_data=file_callback(job_id, "a")),
            InlineKeyboardButton(text="Снять всё", callback_data=file_callback(job_id, "n")),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="Скачать", callback_data=file_callback(job_id, "g")),
            InlineKeyboardButton(text="Отмена", callback_data=file_callback(job_id, "x")),
        ]
    )
    text = f"Что скачивать: {title}\nВыбрано файлов: {chosen} из {total}"
    return text, InlineKeyboardMarkup(inline_keyboard=rows)
