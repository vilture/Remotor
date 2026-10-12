from dataclasses import dataclass
from typing import Literal

from remotor.never import assert_never

Encryption = Literal["tolerated", "preferred", "required"]
JobState = Literal["metadata", "selecting", "downloading", "done", "error", "cancelled"]
SourceKind = Literal["magnet", "torrent"]
StatusKind = Literal[
    "stopped",
    "check_pending",
    "checking",
    "download_pending",
    "downloading",
    "seed_pending",
    "seeding",
]

ACTIVE_STATES: tuple[JobState, ...] = ("metadata", "selecting", "downloading")
REPORTER_STATES: tuple[JobState, ...] = ("metadata", "downloading")


@dataclass(frozen=True)
class DownloadProfile:
    dht: bool = True
    pex: bool = True
    port_forwarding: bool = True
    download_limit_kb: int | None = None
    encryption: Encryption = "preferred"
    sequential_download: bool = False


def default_profile() -> DownloadProfile:
    return DownloadProfile()


def parse_encryption(value: str) -> Encryption:
    if value == "tolerated" or value == "preferred" or value == "required":
        return value
    return "preferred"


def next_encryption(current: Encryption) -> Encryption:
    match current:
        case "tolerated":
            return "preferred"
        case "preferred":
            return "required"
        case "required":
            return "tolerated"
        case _ as unreachable:
            assert_never(unreachable)


def parse_state(value: str) -> JobState:
    match value:
        case "metadata" | "selecting" | "downloading" | "done" | "error" | "cancelled":
            return value
        case _:
            raise ValueError(f"unknown job state: {value}")


def parse_source(value: str) -> SourceKind:
    match value:
        case "magnet" | "torrent":
            return value
        case _:
            raise ValueError(f"unknown source: {value}")


def state_label(state: JobState) -> str:
    match state:
        case "metadata":
            return "получаю список файлов"
        case "selecting":
            return "выбор файлов"
        case "downloading":
            return "скачивается"
        case "done":
            return "готово"
        case "error":
            return "ошибка"
        case "cancelled":
            return "отменено"
        case _ as unreachable:
            assert_never(unreachable)
