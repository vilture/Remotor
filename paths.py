import os
import pwd
import stat
import tempfile
from pathlib import Path


class PathError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def resolve_directory(path: str) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raise PathError("Нужен абсолютный путь.")
    try:
        resolved = raw.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PathError("Папка не существует.") from exc
    except OSError as exc:
        raise PathError(f"Не удалось открыть папку: {exc.strerror or exc}.") from exc
    if not resolved.is_dir():
        raise PathError("Это не папка.")
    return resolved


def resolve_in_jail(path: str, allowed_roots: list[str]) -> Path:
    resolved = resolve_directory(path)
    roots = existing_roots(allowed_roots)
    if not roots:
        raise PathError("Ни один разрешённый корень не существует.")
    if not any(is_inside(resolved, root) for root in roots):
        shown = ", ".join(str(root) for root in roots)
        raise PathError(f"Папка вне разрешённого каталога: {shown}.")
    return resolved


def validate_dir(
    path: str,
    allowed_roots: list[str],
    transmission_user: str,
) -> Path:
    """Проверяет каталог для загрузки: существует и доступен на запись демону.

    ``allowed_roots`` не ограничивает ручной ввод — только обзор в /folder (resolve_in_jail).
    """
    _ = allowed_roots
    resolved = resolve_directory(path)
    ensure_writable(resolved, transmission_user)
    return resolved


def existing_roots(allowed_roots: list[str]) -> list[Path]:
    roots: list[Path] = []
    for root in allowed_roots:
        try:
            resolved = Path(root).resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir():
            roots.append(resolved)
    return roots


def is_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def parent_in_jail(path: Path, allowed_roots: list[str]) -> Path | None:
    parent = path.parent
    if parent == path:
        return None
    try:
        return resolve_in_jail(str(parent), allowed_roots)
    except PathError:
        return None


def list_child_dirs(path: Path) -> list[str]:
    try:
        children = list(path.iterdir())
    except OSError as exc:
        raise PathError(f"Не удалось прочитать папку: {exc.strerror or exc}.") from exc
    names: list[str] = []
    for child in children:
        if child.name.startswith(".") or child.is_symlink():
            continue
        if child.is_dir():
            names.append(child.name)
    names.sort(key=str.lower)
    return names


def browse_start(current: str, allowed_roots: list[str]) -> Path:
    try:
        return resolve_in_jail(current, allowed_roots)
    except PathError:
        pass
    for root in allowed_roots:
        try:
            return resolve_in_jail(root, allowed_roots)
        except PathError:
            continue
    raise PathError("Ни один разрешённый корень не существует.")


def ensure_writable(path: Path, transmission_user: str) -> None:
    try:
        account = pwd.getpwnam(transmission_user)
    except KeyError as exc:
        raise PathError(
            f"Пользователь {transmission_user} не найден. Задайте TRANSMISSION_SYSTEM_USER."
        ) from exc
    try:
        group_ids = set(os.getgrouplist(transmission_user, account.pw_gid))
    except OSError:
        group_ids = {account.pw_gid}
    file_stat = path.stat()
    if not dir_writable(file_stat, account.pw_uid, group_ids):
        raise PathError(
            f"У пользователя {transmission_user} нет прав на запись в {path}."
        )
    if os.geteuid() != account.pw_uid:
        return
    probe: str | None = None
    try:
        descriptor, probe = tempfile.mkstemp(prefix=".remotor-write-test-", dir=path)
        os.close(descriptor)
        os.unlink(probe)
    except OSError as exc:
        if probe is not None:
            try:
                os.unlink(probe)
            except OSError:
                pass
        raise PathError(f"Не удалось записать в {path}: {exc.strerror or exc}.") from exc


def dir_writable(file_stat: os.stat_result, uid: int, group_ids: set[int]) -> bool:
    mode = file_stat.st_mode
    if file_stat.st_uid == uid:
        return bool(mode & stat.S_IWUSR and mode & stat.S_IXUSR)
    if file_stat.st_gid in group_ids:
        return bool(mode & stat.S_IWGRP and mode & stat.S_IXGRP)
    return bool(mode & stat.S_IWOTH and mode & stat.S_IXOTH)
