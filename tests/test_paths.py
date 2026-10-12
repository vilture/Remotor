import os
import pwd
from pathlib import Path

import pytest

from remotor.paths import PathError, resolve_in_jail, validate_dir


def current_user() -> str:
    return pwd.getpwuid(os.geteuid()).pw_name


def test_valid_directory(tmp_path: Path) -> None:
    root = tmp_path / "jail"
    target = root / "video"
    target.mkdir(parents=True)
    resolved = validate_dir(str(target), [str(root)], current_user())
    assert resolved == target.resolve()


def test_symlink_inside_jail_is_allowed(tmp_path: Path) -> None:
    root = tmp_path / "jail"
    target = root / "video"
    target.mkdir(parents=True)
    link = root / "alias"
    link.symlink_to(target)
    resolved = validate_dir(str(link), [str(root)], current_user())
    assert resolved == target.resolve()


def test_symlink_escaping_jail_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "jail"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "escape"
    link.symlink_to(outside)
    with pytest.raises(PathError, match="вне разрешённого"):
        resolve_in_jail(str(link), [str(root)])


def test_parent_traversal_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "jail"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(PathError, match="вне разрешённого"):
        resolve_in_jail(str(root / ".." / "outside"), [str(root)])


def test_relative_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PathError, match="абсолютный"):
        validate_dir("video", [str(tmp_path)], current_user())


def test_missing_directory(tmp_path: Path) -> None:
    root = tmp_path / "jail"
    root.mkdir()
    with pytest.raises(PathError, match="не существует"):
        validate_dir(str(root / "missing"), [str(root)], current_user())


def test_file_is_not_a_directory(tmp_path: Path) -> None:
    root = tmp_path / "jail"
    root.mkdir()
    target = root / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(PathError, match="не папка"):
        validate_dir(str(target), [str(root)], current_user())


def test_directory_without_write_bit(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores mode bits")
    root = tmp_path / "jail"
    target = root / "locked"
    target.mkdir(parents=True)
    target.chmod(0o555)
    with pytest.raises(PathError, match="нет прав"):
        validate_dir(str(target), [str(root)], current_user())


def test_unknown_transmission_user(tmp_path: Path) -> None:
    root = tmp_path / "jail"
    root.mkdir()
    with pytest.raises(PathError, match="не найден"):
        validate_dir(str(root), [str(root)], "no-such-remotor-user")


def test_validate_dir_ignores_allowed_roots_when_writable(tmp_path: Path) -> None:
    target = tmp_path / "video"
    target.mkdir()
    resolved = validate_dir(str(target), ["/no/such/remotor-root"], current_user())
    assert resolved == target.resolve()


def test_missing_allowed_root_blocks_browse_jail(tmp_path: Path) -> None:
    target = tmp_path / "video"
    target.mkdir()
    with pytest.raises(PathError, match="разрешённый корень"):
        resolve_in_jail(str(target), ["/no/such/remotor-root"])
