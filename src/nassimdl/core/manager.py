"""DownloadManager: top-level orchestrator for NassimDL."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from nassimdl.models.download import DownloadInfo, DownloadStatus
from nassimdl.models.config import AppConfig
from nassimdl.core.queue import QueueManager
from nassimdl.core.bandwidth import BandwidthController
from nassimdl.storage.database import Database

logger = logging.getLogger(__name__)


class DownloadManager:
    """
    Central orchestrator — the main entry point for the download engine.
    
    Usage:
        manager = DownloadManager()
        await manager.start()
        download_id = await manager.add_download("https://example.com/file.zip")
        await manager.wait_for_download(download_id)
        await manager.stop()
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load()
        self.db = Database(self.config.db_path)
        self.bandwidth = BandwidthController(self.config.network.max_speed_bytes)
        self.queue = QueueManager(
            config=self.config,
            db=self.db,
            bandwidth=self.bandwidth,
            on_progress=self._on_progress,
            on_status_change=self._on_status_change,
        )
        self._progress_callbacks: list[callable] = []
        self._status_callbacks: list[callable] = []
        self._started = False

    async def start(self) -> None:
        """Initialize the manager: open DB, start queue processor."""
        if self._started:
            return
        
        # Ensure directories exist
        self.config.download.output_dir.mkdir(parents=True, exist_ok=True)
        self.config.download.temp_dir.mkdir(parents=True, exist_ok=True)

        # Setup logging
        self._setup_logging()

        await self.db.initialize()
        await self.queue.start()
        self._started = True
        logger.info("NassimDL Download Manager started")

    async def stop(self) -> None:
        """Shut down the manager gracefully."""
        if not self._started:
            return
        await self.queue.stop()
        await self.db.close()
        self._started = False
        logger.info("NassimDL Download Manager stopped")

    async def add_download(
        self,
        url: str,
        output_dir: Optional[Path] = None,
        filename: Optional[str] = None,
        num_segments: Optional[int] = None,
        hash_value: Optional[str] = None,
        hash_type: Optional[str] = None,
    ) -> int:
        """
        Add a new download.
        
        Args:
            url: The URL to download.
            output_dir: Where to save the file (default: config output_dir).
            filename: Override the filename (default: auto-detect).
            num_segments: Override segment count (default: auto based on size).
            hash_value: Expected hash for verification.
            hash_type: Hash algorithm ('md5', 'sha256', etc.).
            
        Returns:
            The download ID.
        """
        from nassimdl.utils.http import extract_filename_from_url

        download = DownloadInfo(
            id=0,
            url=url,
            filename=filename or extract_filename_from_url(url),
            output_dir=output_dir or self.config.download.output_dir,
            hash_type=hash_type,
            expected_hash=hash_value,
        )

        if num_segments:
            download.num_segments = num_segments

        download_id = await self.db.save_download(download)
        await self.queue.add(download)
        
        logger.info(f"Added download {download_id}: {url}")
        return download_id

    async def pause_download(self, download_id: int) -> bool:
        """Pause a download."""
        return await self.queue.pause(download_id)

    async def resume_download(self, download_id: int) -> bool:
        """Resume a paused download."""
        return await self.queue.resume(download_id)

    async def remove_download(self, download_id: int) -> bool:
        """Remove/cancel a download."""
        return await self.queue.remove(download_id)

    async def get_download(self, download_id: int) -> Optional[DownloadInfo]:
        """Get download info by ID."""
        return await self.db.get_download(download_id)

    async def list_downloads(
        self, status: Optional[DownloadStatus] = None,
    ) -> list[DownloadInfo]:
        """List all downloads, optionally filtered by status."""
        return await self.db.get_all_downloads(status)

    async def wait_for_download(self, download_id: int, poll_interval: float = 0.5) -> DownloadInfo:
        """Wait for a download to complete (or fail)."""
        while True:
            download = await self.db.get_download(download_id)
            if download and download.status in (
                DownloadStatus.COMPLETED,
                DownloadStatus.ERROR,
            ):
                return download
            await asyncio.sleep(poll_interval)

    def set_speed_limit(self, max_bytes_per_second: int) -> None:
        """Update the global speed limit."""
        self.bandwidth.max_rate = max_bytes_per_second
        logger.info(
            f"Speed limit set to {max_bytes_per_second} B/s"
            if max_bytes_per_second > 0
            else "Speed limit removed (unlimited)"
        )

    def on_progress(self, callback: callable) -> None:
        """Register a progress callback."""
        self._progress_callbacks.append(callback)

    def on_status_change(self, callback: callable) -> None:
        """Register a status change callback."""
        self._status_callbacks.append(callback)

    def _on_progress(self, download: DownloadInfo) -> None:
        """Internal progress handler — fans out to registered callbacks."""
        for cb in self._progress_callbacks:
            try:
                cb(download)
            except Exception:
                pass

    def _on_status_change(self, download: DownloadInfo) -> None:
        """Internal status handler — fans out to registered callbacks."""
        for cb in self._status_callbacks:
            try:
                cb(download)
            except Exception:
                pass

    def _setup_logging(self) -> None:
        """Configure logging for the application."""
        import os
        log_path = self.config.logging.file
        log_path.parent.mkdir(parents=True, exist_ok=True)

        handlers = [logging.FileHandler(str(log_path), encoding="utf-8")]
        if os.environ.get("NASSIMDL_VERBOSE") == "1":
            handlers.append(logging.StreamHandler())

        logging.basicConfig(
            level=getattr(logging, self.config.logging.level.upper(), logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=handlers,
            force=True,
        )
