import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from remotor.profile import (
    DownloadProfile,
    JobState,
    SourceKind,
    default_profile,
    parse_encryption,
    parse_source,
    parse_state,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    download_dir TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    torrent_hash TEXT,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    dest_dir TEXT NOT NULL,
    state TEXT NOT NULL,
    tree_json TEXT,
    page INTEGER NOT NULL DEFAULT 0,
    node_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_hash ON jobs(torrent_hash);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_JOB_COLUMNS = {
    "message_id",
    "torrent_hash",
    "name",
    "state",
    "tree_json",
    "page",
    "node_id",
    "finished_at",
    "error",
    "dest_dir",
}


@dataclass
class Job:
    id: int
    user_id: int
    chat_id: int
    message_id: int | None
    torrent_hash: str | None
    name: str
    source: SourceKind
    dest_dir: str
    state: JobState
    tree_json: str | None
    page: int
    node_id: int
    created_at: str
    finished_at: str | None
    error: str | None


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        target = Path(self.path)
        if target.parent != Path("") and str(target.parent) != ".":
            target.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not open")
        return self._conn

    async def get_download_dir(self, user_id: int) -> str | None:
        async with self._lock:
            cursor = await self.conn.execute(
                "SELECT download_dir FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
        if row is None or row["download_dir"] is None:
            return None
        return str(row["download_dir"])

    async def set_download_dir(self, user_id: int, path: str | None) -> None:
        async with self._lock:
            await self.conn.execute(
                """
                INSERT INTO users(user_id, download_dir, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    download_dir = excluded.download_dir,
                    updated_at = excluded.updated_at
                """,
                (user_id, path, utcnow()),
            )
            await self.conn.commit()

    async def create_job(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_id: int | None,
        torrent_hash: str,
        name: str,
        source: SourceKind,
        dest_dir: str,
        state: JobState,
    ) -> Job:
        async with self._lock:
            cursor = await self.conn.execute(
                """
                INSERT INTO jobs(
                    user_id, chat_id, message_id, torrent_hash, name, source,
                    dest_dir, state, page, node_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (
                    user_id,
                    chat_id,
                    message_id,
                    torrent_hash,
                    name,
                    source,
                    dest_dir,
                    state,
                    utcnow(),
                ),
            )
            await self.conn.commit()
            job_id = int(cursor.lastrowid or 0)
            job = await self._get_job(job_id)
        if job is None:
            raise RuntimeError("job insert failed")
        return job

    async def get_job(self, job_id: int) -> Job | None:
        async with self._lock:
            return await self._get_job(job_id)

    async def find_active_by_hash(self, torrent_hash: str) -> Job | None:
        marks = ",".join("?" for _ in ("metadata", "selecting", "downloading"))
        async with self._lock:
            cursor = await self.conn.execute(
                f"""
                SELECT * FROM jobs
                WHERE torrent_hash = ? AND state IN ({marks})
                ORDER BY id DESC LIMIT 1
                """,
                (torrent_hash.lower(), "metadata", "selecting", "downloading"),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return job_from_row(row)

    async def jobs_in_states(self, states: tuple[JobState, ...]) -> list[Job]:
        if not states:
            return []
        marks = ",".join("?" for _ in states)
        async with self._lock:
            cursor = await self.conn.execute(
                f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY id",
                states,
            )
            rows = await cursor.fetchall()
        return [job_from_row(row) for row in rows]

    async def jobs_for_user(self, user_id: int, states: tuple[JobState, ...]) -> list[Job]:
        if not states:
            return []
        marks = ",".join("?" for _ in states)
        async with self._lock:
            cursor = await self.conn.execute(
                f"""
                SELECT * FROM jobs
                WHERE user_id = ? AND state IN ({marks})
                ORDER BY id
                """,
                (user_id, *states),
            )
            rows = await cursor.fetchall()
        return [job_from_row(row) for row in rows]

    async def update_job(
        self,
        job_id: int,
        expect_state: JobState | None,
        **fields: Any,
    ) -> bool:
        unknown = set(fields) - _JOB_COLUMNS
        if unknown:
            raise ValueError(f"unknown job fields: {sorted(unknown)}")
        if not fields:
            return True
        assignments = [f"{column} = ?" for column in fields]
        values: list[Any] = list(fields.values())
        sql = f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?"
        values.append(job_id)
        if expect_state is not None:
            sql += " AND state = ?"
            values.append(expect_state)
        async with self._lock:
            cursor = await self.conn.execute(sql, values)
            await self.conn.commit()
            return cursor.rowcount == 1

    async def get_profile(self) -> DownloadProfile:
        async with self._lock:
            cursor = await self.conn.execute("SELECT key, value FROM settings")
            rows = await cursor.fetchall()
        stored = {str(row["key"]): str(row["value"]) for row in rows}
        base = default_profile()
        limit_raw = stored.get("download_limit_kb", "")
        limit: int | None
        if limit_raw.strip() == "":
            limit = None
        else:
            try:
                parsed = int(limit_raw)
            except ValueError:
                parsed = -1
            limit = parsed if parsed > 0 else None
        return DownloadProfile(
            dht=flag(stored.get("dht"), base.dht),
            pex=flag(stored.get("pex"), base.pex),
            port_forwarding=flag(stored.get("port_forwarding"), base.port_forwarding),
            download_limit_kb=limit,
            encryption=parse_encryption(stored.get("encryption", base.encryption)),
            sequential_download=flag(stored.get("sequential_download"), base.sequential_download),
        )

    async def save_profile(self, profile: DownloadProfile) -> None:
        limit = "" if profile.download_limit_kb is None else str(profile.download_limit_kb)
        rows = [
            ("dht", "1" if profile.dht else "0"),
            ("pex", "1" if profile.pex else "0"),
            ("port_forwarding", "1" if profile.port_forwarding else "0"),
            ("download_limit_kb", limit),
            ("encryption", profile.encryption),
            ("sequential_download", "1" if profile.sequential_download else "0"),
        ]
        async with self._lock:
            await self.conn.executemany(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                rows,
            )
            await self.conn.commit()

    async def reset_profile(self) -> DownloadProfile:
        async with self._lock:
            await self.conn.execute("DELETE FROM settings")
            await self.conn.commit()
        return default_profile()

    async def _get_job(self, job_id: int) -> Job | None:
        cursor = await self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return job_from_row(row)


def flag(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    if value == "1":
        return True
    if value == "0":
        return False
    return default


def job_from_row(row: aiosqlite.Row) -> Job:
    message_id = row["message_id"]
    torrent_hash = row["torrent_hash"]
    return Job(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        chat_id=int(row["chat_id"]),
        message_id=None if message_id is None else int(message_id),
        torrent_hash=None if torrent_hash is None else str(torrent_hash),
        name=str(row["name"]),
        source=parse_source(str(row["source"])),
        dest_dir=str(row["dest_dir"]),
        state=parse_state(str(row["state"])),
        tree_json=None if row["tree_json"] is None else str(row["tree_json"]),
        page=int(row["page"]),
        node_id=int(row["node_id"]),
        created_at=str(row["created_at"]),
        finished_at=None if row["finished_at"] is None else str(row["finished_at"]),
        error=None if row["error"] is None else str(row["error"]),
    )
