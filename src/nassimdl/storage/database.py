"""SQLite-based persistence for download state and segment progress."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import aiosqlite

from nassimdl.models.download import (
    DownloadInfo,
    DownloadStatus,
    SegmentInfo,
    SegmentStatus,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    filename TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    file_size INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    num_segments INTEGER NOT NULL DEFAULT 1,
    auto_segments INTEGER NOT NULL DEFAULT 0,
    supports_range INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    completed_at REAL,
    error_message TEXT,
    hash_type TEXT,
    expected_hash TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    download_id INTEGER NOT NULL,
    seg_index INTEGER NOT NULL,
    start_byte INTEGER NOT NULL,
    end_byte INTEGER NOT NULL,
    bytes_downloaded INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    temp_path TEXT,
    error_message TEXT,
    retries INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (download_id) REFERENCES downloads(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_segments_download_id ON segments(download_id);
CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
"""


class Database:
    """Async SQLite database for persisting download state."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Open the database and create tables if needed."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(CREATE_TABLES_SQL)

        # Migrate existing downloads table if auto_segments column is absent
        async with self._db.execute("PRAGMA table_info(downloads)") as cursor:
            cols = {row["name"] for row in await cursor.fetchall()}
        if "auto_segments" not in cols:
            await self._db.execute(
                "ALTER TABLE downloads ADD COLUMN auto_segments INTEGER NOT NULL DEFAULT 0"
            )

        # Check/set schema version
        async with self._db.execute("SELECT COUNT(*) FROM schema_version") as cursor:
            row = await cursor.fetchone()
            if row[0] == 0:
                await self._db.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
        await self._db.commit()
        logger.info(f"Database initialized at {self.db_path}")

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    # ── Download CRUD ──────────────────────────────────────────────────

    async def save_download(self, download: DownloadInfo) -> int:
        """Insert a new download record. Returns the assigned ID."""
        async with self._db.execute(
            """INSERT INTO downloads
               (url, filename, output_dir, file_size, status, num_segments,
                auto_segments, supports_range, created_at, hash_type, expected_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                download.url,
                download.filename,
                str(download.output_dir),
                download.file_size,
                download.status.value,
                download.num_segments,
                1 if download.auto_segments else 0,
                1 if download.supports_range else 0,
                download.created_at,
                download.hash_type,
                download.expected_hash,
            ),
        ) as cursor:
            download_id = cursor.lastrowid
        await self._db.commit()
        download.id = download_id
        return download_id

    async def update_download_status(
        self, download_id: int, status: DownloadStatus,
        error_message: Optional[str] = None,
        completed_at: Optional[float] = None,
    ) -> None:
        """Update a download's status."""
        await self._db.execute(
            """UPDATE downloads
               SET status = ?, error_message = ?, completed_at = ?
               WHERE id = ?""",
            (status.value, error_message, completed_at, download_id),
        )
        await self._db.commit()

    async def update_download_probe(
        self, download_id: int, file_size: Optional[int],
        supports_range: bool, filename: str, url: str,
        num_segments: int,
    ) -> None:
        """Update download after probing the URL."""
        await self._db.execute(
            """UPDATE downloads
               SET file_size = ?, supports_range = ?, filename = ?,
                   url = ?, num_segments = ?
               WHERE id = ?""",
            (file_size, 1 if supports_range else 0, filename, url,
             num_segments, download_id),
        )
        await self._db.commit()

    async def get_download(self, download_id: int) -> Optional[DownloadInfo]:
        """Load a download with all its segments."""
        async with self._db.execute(
            "SELECT * FROM downloads WHERE id = ?", (download_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        
        download = self._row_to_download(row)
        download.segments = await self.get_segments(download_id)
        return download

    async def get_all_downloads(
        self, status: Optional[DownloadStatus] = None,
    ) -> list[DownloadInfo]:
        """Load all downloads, optionally filtered by status."""
        if status:
            query = "SELECT * FROM downloads WHERE status = ? ORDER BY created_at DESC"
            params = (status.value,)
        else:
            query = "SELECT * FROM downloads ORDER BY created_at DESC"
            params = ()
        
        downloads = []
        async with self._db.execute(query, params) as cursor:
            async for row in cursor:
                dl = self._row_to_download(row)
                dl.segments = await self.get_segments(dl.id)
                downloads.append(dl)
        return downloads

    async def delete_download(self, download_id: int) -> None:
        """Delete a download and its segments."""
        await self._db.execute("DELETE FROM downloads WHERE id = ?", (download_id,))
        await self._db.commit()

    # ── Segment CRUD ───────────────────────────────────────────────────

    async def save_segments(self, segments: list[SegmentInfo]) -> None:
        """Insert segment records for a download."""
        for seg in segments:
            async with self._db.execute(
                """INSERT INTO segments
                   (download_id, seg_index, start_byte, end_byte,
                    bytes_downloaded, status, temp_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    seg.download_id,
                    seg.index,
                    seg.start_byte,
                    seg.end_byte,
                    seg.bytes_downloaded,
                    seg.status.value,
                    str(seg.temp_path) if seg.temp_path else None,
                ),
            ) as cursor:
                seg.id = cursor.lastrowid
        await self._db.commit()

    async def update_segment_progress(
        self, segment_id: int, bytes_downloaded: int,
        status: Optional[SegmentStatus] = None,
    ) -> None:
        """Update a segment's progress. Called frequently during download."""
        if status:
            await self._db.execute(
                """UPDATE segments
                   SET bytes_downloaded = ?, status = ?
                   WHERE id = ?""",
                (bytes_downloaded, status.value, segment_id),
            )
        else:
            await self._db.execute(
                "UPDATE segments SET bytes_downloaded = ? WHERE id = ?",
                (bytes_downloaded, segment_id),
            )
        await self._db.commit()

    async def update_segment_status(
        self, segment_id: int, status: SegmentStatus,
        error_message: Optional[str] = None,
    ) -> None:
        """Update a segment's status."""
        await self._db.execute(
            """UPDATE segments
               SET status = ?, error_message = ?
               WHERE id = ?""",
            (status.value, error_message, segment_id),
        )
        await self._db.commit()

    async def get_segments(self, download_id: int) -> list[SegmentInfo]:
        """Load all segments for a download."""
        segments = []
        async with self._db.execute(
            "SELECT * FROM segments WHERE download_id = ? ORDER BY seg_index",
            (download_id,),
        ) as cursor:
            async for row in cursor:
                segments.append(self._row_to_segment(row))
        return segments

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_download(row) -> DownloadInfo:
        row_keys = row.keys()
        return DownloadInfo(
            id=row["id"],
            url=row["url"],
            filename=row["filename"],
            output_dir=Path(row["output_dir"]),
            file_size=row["file_size"],
            status=DownloadStatus(row["status"]),
            num_segments=row["num_segments"],
            auto_segments=bool(row["auto_segments"]) if "auto_segments" in row_keys else False,
            supports_range=bool(row["supports_range"]),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            error_message=row["error_message"],
            hash_type=row["hash_type"],
            expected_hash=row["expected_hash"],
        )

    @staticmethod
    def _row_to_segment(row) -> SegmentInfo:
        return SegmentInfo(
            id=row["id"],
            download_id=row["download_id"],
            index=row["seg_index"],
            start_byte=row["start_byte"],
            end_byte=row["end_byte"],
            bytes_downloaded=row["bytes_downloaded"],
            status=SegmentStatus(row["status"]),
            temp_path=Path(row["temp_path"]) if row["temp_path"] else None,
            error_message=row["error_message"],
            retries=row["retries"],
        )
