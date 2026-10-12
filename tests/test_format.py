from transmission_rpc.torrent import Status

from remotor.format import is_download_complete
from remotor.transmission import status_kind


def test_complete_when_download_reaches_100() -> None:
    assert is_download_complete(100, "stopped") is True
    assert is_download_complete(100, "seeding") is True
    assert is_download_complete(100, "downloading") is True


def test_incomplete_or_still_checking() -> None:
    assert is_download_complete(99.9, "stopped") is False
    assert is_download_complete(100, "checking") is False
    assert is_download_complete(100, "check_pending") is False


def test_status_kind_covers_transmission() -> None:
    assert status_kind(Status.STOPPED) == "stopped"
    assert status_kind(Status.DOWNLOADING) == "downloading"
    assert status_kind(Status.SEEDING) == "seeding"
    for status in Status:
        status_kind(status)
