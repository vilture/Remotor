import asyncio
from pathlib import Path

from remotor.db import Database
from remotor.profile import default_profile


def test_download_dir_and_profile(tmp_path: Path) -> None:
    asyncio.run(scenario(tmp_path / "remotor.db"))


async def scenario(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    assert await db.get_download_dir(7) is None
    await db.set_download_dir(7, "/tmp/remotor-downloads")
    assert await db.get_download_dir(7) == "/tmp/remotor-downloads"
    await db.set_download_dir(7, None)
    assert await db.get_download_dir(7) is None

    profile = await db.get_profile()
    assert profile == default_profile()
    await db.save_profile(default_profile())
    stored = await db.get_profile()
    assert stored.dht is True
    assert stored.download_limit_kb is None
    assert stored.encryption == "preferred"
    await db.reset_profile()
    assert await db.get_profile() == default_profile()

    job = await db.create_job(
        user_id=7,
        chat_id=8,
        message_id=9,
        torrent_hash="abc",
        name="Movie",
        source="magnet",
        dest_dir="/tmp/remotor-downloads",
        state="metadata",
    )
    assert await db.find_active_by_hash("ABC") == job
    assert await db.update_job(job.id, "downloading", state="done") is False
    assert await db.update_job(job.id, "metadata", state="downloading", name="Movie 2") is True
    fresh = await db.get_job(job.id)
    assert fresh is not None
    assert fresh.state == "downloading"
    assert fresh.name == "Movie 2"
    active = await db.jobs_for_user(7, ("downloading",))
    assert [item.id for item in active] == [job.id]
    await db.close()
