import json
import math
from dataclasses import dataclass
from enum import Enum

from remotor.never import assert_never

PAGE_SIZE = 6


class Selection(Enum):
    ALL = "all"
    SOME = "some"
    NONE = "none"


@dataclass
class TorrentFile:
    index: int
    name: str
    size: int


@dataclass
class Node:
    id: int
    parent_id: int | None
    name: str
    is_dir: bool
    size: int
    file_indexes: list[int]
    children: list[int]


@dataclass
class FileTree:
    nodes: dict[int, Node]
    root_id: int
    wanted: set[int]


def build_tree(files: list[TorrentFile]) -> FileTree:
    root = Node(
        id=0,
        parent_id=None,
        name="",
        is_dir=True,
        size=0,
        file_indexes=[],
        children=[],
    )
    nodes = {0: root}
    dir_ids = {"": 0}
    next_id = 1
    for item in files:
        parts = [part for part in item.name.split("/") if part] or [f"file-{item.index}"]
        parent_id = 0
        walked = ""
        for directory in parts[:-1]:
            walked = f"{walked}/{directory}" if walked else directory
            existing = dir_ids.get(walked)
            if existing is None:
                node = Node(
                    id=next_id,
                    parent_id=parent_id,
                    name=directory,
                    is_dir=True,
                    size=0,
                    file_indexes=[],
                    children=[],
                )
                nodes[next_id] = node
                nodes[parent_id].children.append(next_id)
                dir_ids[walked] = next_id
                existing = next_id
                next_id += 1
            parent_id = existing
        leaf = Node(
            id=next_id,
            parent_id=parent_id,
            name=parts[-1],
            is_dir=False,
            size=item.size,
            file_indexes=[item.index],
            children=[],
        )
        nodes[next_id] = leaf
        nodes[parent_id].children.append(next_id)
        next_id += 1
    for node in nodes.values():
        node.children.sort(
            key=lambda child_id: (not nodes[child_id].is_dir, nodes[child_id].name.lower())
        )
    rollup(nodes, 0)
    return FileTree(nodes=nodes, root_id=0, wanted={item.index for item in files})


def rollup(nodes: dict[int, Node], node_id: int) -> tuple[list[int], int]:
    node = nodes[node_id]
    if not node.is_dir:
        return list(node.file_indexes), node.size
    indexes: list[int] = []
    size = 0
    for child_id in node.children:
        child_indexes, child_size = rollup(nodes, child_id)
        indexes.extend(child_indexes)
        size += child_size
    node.file_indexes = indexes
    node.size = size
    return indexes, size


def selection_of(tree: FileTree, node: Node) -> Selection:
    if not node.file_indexes:
        return Selection.NONE
    chosen = sum(1 for index in node.file_indexes if index in tree.wanted)
    if chosen == 0:
        return Selection.NONE
    if chosen == len(node.file_indexes):
        return Selection.ALL
    return Selection.SOME


def toggle(tree: FileTree, node: Node) -> None:
    indexes = set(node.file_indexes)
    match selection_of(tree, node):
        case Selection.ALL:
            tree.wanted -= indexes
        case Selection.SOME | Selection.NONE:
            tree.wanted |= indexes
        case _ as unreachable:
            assert_never(unreachable)


def select_all(tree: FileTree) -> None:
    tree.wanted = set(tree.nodes[tree.root_id].file_indexes)


def select_none(tree: FileTree) -> None:
    tree.wanted = set()


def children_page(
    tree: FileTree, node_id: int, page: int, page_size: int = PAGE_SIZE
) -> tuple[list[Node], int, int]:
    children = [tree.nodes[child_id] for child_id in tree.nodes[node_id].children]
    page_count = max(1, math.ceil(len(children) / page_size)) if children else 1
    current = min(max(page, 0), page_count - 1)
    start = current * page_size
    return children[start : start + page_size], current, page_count


def dumps_tree(tree: FileTree) -> str:
    payload = {
        "root_id": tree.root_id,
        "wanted": sorted(tree.wanted),
        "nodes": [
            {
                "id": node.id,
                "parent_id": node.parent_id,
                "name": node.name,
                "is_dir": node.is_dir,
                "size": node.size,
                "file_indexes": node.file_indexes,
                "children": node.children,
            }
            for node in tree.nodes.values()
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def loads_tree(raw: str) -> FileTree:
    payload = json.loads(raw)
    nodes: dict[int, Node] = {}
    for item in payload["nodes"]:
        parent = item["parent_id"]
        node = Node(
            id=int(item["id"]),
            parent_id=None if parent is None else int(parent),
            name=str(item["name"]),
            is_dir=bool(item["is_dir"]),
            size=int(item["size"]),
            file_indexes=[int(index) for index in item["file_indexes"]],
            children=[int(index) for index in item["children"]],
        )
        nodes[node.id] = node
    root_id = int(payload["root_id"])
    if root_id not in nodes:
        raise ValueError("tree root missing")
    return FileTree(
        nodes=nodes,
        root_id=root_id,
        wanted={int(index) for index in payload["wanted"]},
    )
