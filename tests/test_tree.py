from remotor.tree import (
    PAGE_SIZE,
    FileTree,
    Node,
    Selection,
    TorrentFile,
    build_tree,
    children_page,
    dumps_tree,
    loads_tree,
    select_none,
    selection_of,
    toggle,
)


def sample_files() -> list[TorrentFile]:
    return [
        TorrentFile(0, "Show/Season 1/E01.mkv", 100),
        TorrentFile(1, "Show/Season 1/E02.mkv", 200),
        TorrentFile(2, "Show/Season 2/E01.mkv", 300),
        TorrentFile(3, "readme.txt", 10),
    ]


def node_named(tree: FileTree, name: str) -> Node:
    for node in tree.nodes.values():
        if node.name == name:
            return node
    raise AssertionError(name)


def test_tree_groups_files_and_starts_selected() -> None:
    tree = build_tree(sample_files())
    show = node_named(tree, "Show")
    season = node_named(tree, "Season 1")
    assert set(season.file_indexes) == {0, 1}
    assert season.size == 300
    assert set(show.file_indexes) == {0, 1, 2}
    assert tree.wanted == {0, 1, 2, 3}
    root_children = [tree.nodes[child_id].name for child_id in tree.nodes[0].children]
    assert root_children == ["Show", "readme.txt"]


def test_toggle_folder_and_partial_parent() -> None:
    tree = build_tree(sample_files())
    season = node_named(tree, "Season 1")
    show = node_named(tree, "Show")
    toggle(tree, season)
    assert tree.wanted == {2, 3}
    assert selection_of(tree, season) is Selection.NONE
    assert selection_of(tree, show) is Selection.SOME
    toggle(tree, show)
    assert selection_of(tree, show) is Selection.ALL
    assert tree.wanted == {0, 1, 2, 3}


def test_select_none_then_toggle_file() -> None:
    tree = build_tree(sample_files())
    select_none(tree)
    readme = node_named(tree, "readme.txt")
    assert selection_of(tree, readme) is Selection.NONE
    toggle(tree, readme)
    assert tree.wanted == {3}


def test_roundtrip_and_pages() -> None:
    files = [TorrentFile(index, f"dir/file-{index}.mkv", index + 1) for index in range(PAGE_SIZE + 2)]
    tree = build_tree(files)
    restored = loads_tree(dumps_tree(tree))
    assert restored.wanted == tree.wanted
    assert restored.nodes[restored.root_id].file_indexes == tree.nodes[0].file_indexes
    folder = node_named(restored, "dir")
    first, page, pages = children_page(restored, folder.id, 0)
    second, _, _ = children_page(restored, folder.id, 1)
    assert page == 0
    assert pages == 2
    assert len(first) == PAGE_SIZE
    assert len(second) == 2
