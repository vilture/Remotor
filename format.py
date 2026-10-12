from remotor.never import assert_never
from remotor.profile import DownloadProfile, StatusKind


def one_line(value: str) -> str:
    return value.replace("\n", " ").replace("\r", " ").strip()


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def format_bytes(value: float) -> str:
    size = float(max(value, 0))
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == "ТБ":
            break
        size /= 1024
    if unit == "Б":
        return f"{int(size)} {unit}"
    return f"{size:.1f} {unit}"


def format_speed(value: float) -> str:
    return f"{format_bytes(value)}/с"


def format_eta(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "неизвестно"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {secs} с"
    return f"{secs} с"


def progress_bar(percent: float, width: int = 16) -> str:
    bounded = max(0.0, min(100.0, percent))
    filled = round(bounded / 100 * width)
    return "█" * filled + "░" * (width - filled)


def format_metadata(name: str, percent: float, dest: str, peers: int) -> str:
    lines = [
        f"Получаю список файлов: {one_line(name)}",
        f"{progress_bar(percent)}  {percent:.1f}%",
        f"Пиры: {peers}",
        f"Папка: {dest}",
    ]
    if peers == 0 and percent < 100:
        lines.append("Нет пиров — жду трекер/DHT. Для magnet это может занять несколько минут.")
    return "\n".join(lines)


def format_download(
    name: str,
    percent: float,
    rate_down: int,
    rate_up: int,
    eta_seconds: int | None,
    downloaded: int,
    total: int,
    peers: int,
    dest: str,
    status: StatusKind,
    warning: str | None,
) -> str:
    lines = [
        f"Загрузка: {one_line(name)}",
        f"{progress_bar(percent)}  {percent:.1f}%",
        f"↓ {format_speed(rate_down)}  ↑ {format_speed(rate_up)}",
        f"ETA: {format_eta(eta_seconds)}",
        f"Скачано: {format_bytes(downloaded)} / {format_bytes(total)}",
        f"Пиры: {peers}",
        f"Папка: {dest}",
    ]
    if status == "stopped":
        lines.append("Transmission: остановлен.")
    if peers == 0 and no_peer_warning(status):
        lines.append("Нет пиров, загрузка может не идти.")
    if warning:
        lines.append(one_line(warning))
    return "\n".join(lines)


def no_peer_warning(status: StatusKind) -> bool:
    match status:
        case "downloading" | "download_pending":
            return True
        case "stopped" | "check_pending" | "checking" | "seed_pending" | "seeding":
            return False
        case _ as unreachable:
            assert_never(unreachable)


def format_profile(profile: DownloadProfile) -> str:
    if profile.download_limit_kb is None:
        limit = "без лимита"
    else:
        limit = f"{profile.download_limit_kb} КБ/с"
    return "\n".join(
        [
            "Настройки скачивания",
            f"DHT: {on_off(profile.dht)}",
            f"PEX: {on_off(profile.pex)}",
            f"Port mapping: {on_off(profile.port_forwarding)}",
            f"Лимит скачивания: {limit}",
            f"Шифрование: {profile.encryption}",
            f"Последовательно (стрим): {on_off(profile.sequential_download)}",
            "Очередь Transmission выключена.",
            "После 100% раздача останавливается.",
        ]
    )


def on_off(enabled: bool) -> str:
    if enabled:
        return "включён"
    return "выключен"


def is_download_complete(percent: float, status: StatusKind) -> bool:
    if percent < 100:
        return False
    match status:
        case "downloading" | "download_pending" | "seed_pending" | "seeding" | "stopped":
            return True
        case "check_pending" | "checking":
            return False
        case _ as unreachable:
            assert_never(unreachable)
