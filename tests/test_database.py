"""Unit tests for SQLite database storage layer."""

import pytest
from pathlib import Path
from nassimdl.models.download import DownloadInfo, DownloadStatus, SegmentInfo, SegmentStatus
from nassimdl.storage.database import Database


@pytest.mark.asyncio
async def test_database_crud(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db = Database(db_file)
    await db.initialize()

    try:
        download = DownloadInfo(
            id=0,
            url="https://example.com/test.zip",
            filename="test.zip",
            output_dir=tmp_path,
            file_size=1024 * 1024,
            status=DownloadStatus.QUEUED,
            num_segments=2,
            supports_range=True,
        )

        dl_id = await db.save_download(download)
        assert dl_id > 0
        assert download.id == dl_id

        # Save segments
        seg0 = SegmentInfo(
            id=0,
            download_id=dl_id,
            index=0,
            start_byte=0,
            end_byte=524287,
            temp_path=tmp_path / "seg_0.part",
        )
        seg1 = SegmentInfo(
            id=0,
            download_id=dl_id,
            index=1,
            start_byte=524288,
            end_byte=1048575,
            temp_path=tmp_path / "seg_1.part",
        )
        await db.save_segments([seg0, seg1])

        # Retrieve download
        loaded = await db.get_download(dl_id)
        assert loaded is not None
        assert loaded.filename == "test.zip"
        assert loaded.status == DownloadStatus.QUEUED
        assert len(loaded.segments) == 2
        assert loaded.segments[0].start_byte == 0
        assert loaded.segments[1].start_byte == 524288

        # Update segment progress
        await db.update_segment_progress(loaded.segments[0].id, 100000, SegmentStatus.DOWNLOADING)
        updated_segments = await db.get_segments(dl_id)
        assert updated_segments[0].bytes_downloaded == 100000
        assert updated_segments[0].status == SegmentStatus.DOWNLOADING

        # Update download status
        await db.update_download_status(dl_id, DownloadStatus.COMPLETED)
        completed = await db.get_download(dl_id)
        assert completed.status == DownloadStatus.COMPLETED

        # List all
        all_dls = await db.get_all_downloads()
        assert len(all_dls) == 1

        # Delete download
        await db.delete_download(dl_id)
        deleted = await db.get_download(dl_id)
        assert deleted is None

    finally:
        await db.close()
