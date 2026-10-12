import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from transmission_rpc import Client
from transmission_rpc.constants import RatioLimitMode
from transmission_rpc.error import TransmissionConnectError, TransmissionError
from transmission_rpc.torrent import Status, Torrent

from remotor.config import Settings
from remotor.magnet import magnet_hash
from remotor.never import assert_never
from remotor.profile import DownloadProfile, StatusKind
from remotor.tree import TorrentFile

log = logging.getLogger(__name__)

T = TypeVar("T")

_SNAPSHOT_FIELDS = [
    "id",
    "hashString",
    "name",
    "percentDone",
    "status",
    "rateDownload",
    "rateUpload",
    "eta",
    "sizeWhenDone",
    "leftUntilDone",
    "peersConnected",
    "error",
    "errorString",
    "metadataPercentComplete",
]

_INSPECT_FIELDS = [*_SNAPSHOT_FIELDS, "files", "priorities", "wanted"]


class DuplicateTorrent(Exception):
    def __init__(self, torrent_hash: str | None) -> None:
        super().__init__("duplicate torrent")
        self.torrent_hash = torrent_hash


class TorrentGone(Exception):
    pass


@dataclass(frozen=True)
class TorrentSnapshot:
    torrent_hash: str
    name: str
    percent: float
    status: StatusKind
    rate_down: int
    rate_up: int
    eta_seconds: int | None
    downloaded: int
    total: int
    peers: int
    error_code: int
    error_string: str
    metadata_percent: float


def safe_error(exc: BaseException) -> str:
    if isinstance(exc, TransmissionConnectError):
        return "нет связи с Transmission"
    if type(exc).__name__ == "TransmissionAuthError":
        return "Transmission отклонил логин или пароль"
    if isinstance(exc, TransmissionError):
        message = exc.message.strip() or "ошибка Transmission"
        if "://" in message or "password" in message.lower():
            return "ошибка Transmission"
        return message
    return "ошибка Transmission"


class Transmission:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached: Client | None = None
        self._lock = asyncio.Lock()

    async def add(self, source: str | Path, download_dir: str) -> tuple[str, str]:
        async with self._lock:
            return await asyncio.to_thread(self._add, source, download_dir)

    async def inspect(self, torrent_hash: str) -> tuple[TorrentSnapshot, list[TorrentFile]]:
        async with self._lock:
            return await asyncio.to_thread(self._inspect, torrent_hash)

    async def snapshot(self, torrent_hash: str) -> TorrentSnapshot:
        async with self._lock:
            return await asyncio.to_thread(self._snapshot, torrent_hash)

    async def stop(self, torrent_hash: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._stop, torrent_hash)

    async def remove(self, torrent_hash: str, *, delete_data: bool) -> None:
        async with self._lock:
            await asyncio.to_thread(self._remove, torrent_hash, delete_data)

    async def arm(
        self,
        torrent_hash: str,
        wanted: list[int],
        unwanted: list[int],
        profile: DownloadProfile,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(self._arm, torrent_hash, wanted, unwanted, profile)

    async def start(self, torrent_hash: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._start, torrent_hash)

    async def reannounce(self, torrent_hash: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._reannounce, torrent_hash)

    async def apply_session(self, profile: DownloadProfile) -> None:
        async with self._lock:
            await asyncio.to_thread(self._apply_session, profile)

    def _open(self) -> Client:
        if self._cached is not None:
            return self._cached
        username = self._settings.transmission_user or None
        password = self._settings.transmission_password or None
        self._cached = Client(
            host=self._settings.transmission_host,
            port=self._settings.transmission_port,
            username=username,
            password=password,
            timeout=30,
        )
        return self._cached

    def _run(self, func: Callable[[Client], T]) -> T:
        try:
            return func(self._open())
        except TransmissionError:
            self._cached = None
            raise

    def _add(self, source: str | Path, download_dir: str) -> tuple[str, str]:
        def operation(client: Client) -> tuple[str, str]:
            try:
                torrent = client.add_torrent(source, download_dir=download_dir, paused=False)
            except TransmissionError as exc:
                if "duplicate" in exc.message.lower():
                    source_text = source if isinstance(source, str) else ""
                    found = magnet_hash(source_text) if source_text else None
                    raise DuplicateTorrent(found) from exc
                raise
            torrent_hash = torrent.hash_string.lower()
            try:
                # Ratio 0 stops seeding after the download is complete.
                client.change_torrent(
                    torrent_hash,
                    seed_ratio_limit=0,
                    seed_ratio_mode=RatioLimitMode.Single,
                )
            except TransmissionError:
                try:
                    client.remove_torrent(torrent_hash, delete_data=False)
                except TransmissionError:
                    log.warning("не удалось убрать торрент после ошибки лимита раздачи")
                raise
            return torrent_hash, torrent.name or torrent_hash

        return self._run(operation)

    def _inspect(self, torrent_hash: str) -> tuple[TorrentSnapshot, list[TorrentFile]]:
        def operation(client: Client) -> tuple[TorrentSnapshot, list[TorrentFile]]:
            torrent = load_torrent(client, torrent_hash, _INSPECT_FIELDS)
            return snapshot_from(torrent), files_from(torrent)

        return self._run(operation)

    def _snapshot(self, torrent_hash: str) -> TorrentSnapshot:
        def operation(client: Client) -> TorrentSnapshot:
            return snapshot_from(load_torrent(client, torrent_hash, _SNAPSHOT_FIELDS))

        return self._run(operation)

    def _stop(self, torrent_hash: str) -> None:
        def operation(client: Client) -> None:
            try:
                client.stop_torrent(torrent_hash)
            except KeyError:
                return

        self._run(operation)

    def _remove(self, torrent_hash: str, delete_data: bool) -> None:
        def operation(client: Client) -> None:
            try:
                client.remove_torrent(torrent_hash, delete_data=delete_data)
            except KeyError:
                return

        self._run(operation)

    def _arm(
        self,
        torrent_hash: str,
        wanted: list[int],
        unwanted: list[int],
        profile: DownloadProfile,
    ) -> None:
        def operation(client: Client) -> None:
            client.change_torrent(
                torrent_hash,
                files_wanted=wanted or None,
                files_unwanted=unwanted or None,
                seed_ratio_limit=0,
                seed_ratio_mode=RatioLimitMode.Single,
                download_limited=profile.download_limit_kb is not None,
                download_limit=profile.download_limit_kb,
                sequential_download=profile.sequential_download,
            )

        self._run(operation)

    def _start(self, torrent_hash: str) -> None:
        def operation(client: Client) -> None:
            client.start_torrent_now(torrent_hash)

        self._run(operation)

    def _reannounce(self, torrent_hash: str) -> None:
        def operation(client: Client) -> None:
            try:
                client.reannounce_torrent(torrent_hash)
            except KeyError:
                return

        self._run(operation)

    def _apply_session(self, profile: DownloadProfile) -> None:
        def operation(client: Client) -> None:
            extra: dict[str, object] = {}
            proxy_url = self._settings.transmission_proxy_url.strip()
            if proxy_url:
                extra["proxy-url"] = proxy_url
            else:
                extra["proxy-url"] = ""
                extra["proxy-server-enabled"] = False
            client.set_session(
                dht_enabled=profile.dht,
                pex_enabled=profile.pex,
                lpd_enabled=True,
                port_forwarding_enabled=profile.port_forwarding,
                encryption=profile.encryption,
                download_queue_enabled=False,
                seed_queue_enabled=False,
                **extra,
            )

        self._run(operation)


def load_torrent(client: Client, torrent_hash: str, fields: list[str]) -> Torrent:
    try:
        return client.get_torrent(torrent_hash, arguments=fields)
    except KeyError as exc:
        raise TorrentGone from exc


def snapshot_from(torrent: Torrent) -> TorrentSnapshot:
    eta = torrent.eta
    eta_seconds = int(eta.total_seconds()) if eta is not None else None
    total = max(0, torrent.size_when_done)
    downloaded = max(0, total - torrent.left_until_done)
    return TorrentSnapshot(
        torrent_hash=torrent.hash_string.lower(),
        name=torrent.name or torrent.hash_string,
        percent=float(torrent.percent_done) * 100,
        status=status_kind(torrent.status),
        rate_down=torrent.rate_download,
        rate_up=torrent.rate_upload,
        eta_seconds=eta_seconds,
        downloaded=downloaded,
        total=total,
        peers=torrent.peers_connected,
        error_code=torrent.error,
        error_string=torrent.error_string,
        metadata_percent=float(torrent.metadata_percent_complete) * 100,
    )


def files_from(torrent: Torrent) -> list[TorrentFile]:
    if torrent.metadata_percent_complete < 1:
        return []
    if "files" not in torrent.fields:
        return []
    return [
        TorrentFile(index=item.id, name=item.name, size=item.size)
        for item in torrent.get_files()
    ]


def status_kind(status: Status) -> StatusKind:
    match status:
        case Status.STOPPED:
            return "stopped"
        case Status.CHECK_PENDING:
            return "check_pending"
        case Status.CHECKING:
            return "checking"
        case Status.DOWNLOAD_PENDING:
            return "download_pending"
        case Status.DOWNLOADING:
            return "downloading"
        case Status.SEED_PENDING:
            return "seed_pending"
        case Status.SEEDING:
            return "seeding"
        case _ as unreachable:
            assert_never(unreachable)
