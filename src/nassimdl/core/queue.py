"""Queue manager: controls concurrent downloads and priority ordering."""

import asyncio
import logging
from typing import Optional, Callable

from nassimdl.models.download import DownloadInfo, DownloadStatus
from nassimdl.models.config import AppConfig
from nassimdl.core.task import DownloadTask
from nassimdl.core.bandwidth import BandwidthController
from nassimdl.storage.database import Database

logger = logging.getLogger(__name__)


class QueueManager:
    """
    Manages a queue of downloads, controlling how many run concurrently.
    """

    def __init__(
        self,
        config: AppConfig,
        db: Database,
        bandwidth: BandwidthController,
        on_progress: Optional[Callable] = None,
        on_status_change: Optional[Callable] = None,
    ):
        self.config = config
        self.db = db
        self.bandwidth = bandwidth
        self.on_progress = on_progress
        self.on_status_change = on_status_change

        self._active_tasks: dict[int, DownloadTask] = {}  # download_id -> task
        self._running_asyncio_tasks: dict[int, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(config.download.max_concurrent)
        self._queue: asyncio.Queue[DownloadInfo] = asyncio.Queue()
        self._processor_task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def active_count(self) -> int:
        return len(self._active_tasks)

    @property
    def active_downloads(self) -> list[DownloadTask]:
        return list(self._active_tasks.values())

    async def start(self) -> None:
        """Start the queue processor."""
        self._running = True
        self._processor_task = asyncio.create_task(self._process_queue())
        
        # Clean up any orphaned downloads left in DOWNLOADING or PROBING from a previous shutdown
        try:
            orphaned = await self.db.get_all_downloads(DownloadStatus.DOWNLOADING)
            for dl in orphaned:
                await self.db.update_download_status(dl.id, DownloadStatus.PAUSED)
                logger.info(f"Marked interrupted download {dl.id} ({dl.filename}) as paused on startup")
            probing = await self.db.get_all_downloads(DownloadStatus.PROBING)
            for dl in probing:
                await self.db.update_download_status(dl.id, DownloadStatus.PAUSED)
        except Exception as e:
            logger.warning(f"Error checking orphaned downloads on startup: {e}")

        logger.info("Queue manager started")

    async def stop(self) -> None:
        """Stop the queue processor and cancel active downloads."""
        self._running = False
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        
        # Cancel all active downloads
        for task in self._active_tasks.values():
            task.cancel()
        for atask in self._running_asyncio_tasks.values():
            atask.cancel()
        
        self._active_tasks.clear()
        self._running_asyncio_tasks.clear()
        logger.info("Queue manager stopped")

    async def add(self, download: DownloadInfo) -> None:
        """Add a download to the queue."""
        await self._queue.put(download)
        logger.info(f"Queued download: {download.filename} (ID: {download.id})")

    async def pause(self, download_id: int) -> bool:
        """Pause an active download."""
        task = self._active_tasks.get(download_id)
        if task:
            task.pause()
            await self.db.update_download_status(download_id, DownloadStatus.PAUSED)
            logger.info(f"Paused download {download_id}")
            return True
        return False

    async def resume(self, download_id: int) -> bool:
        """Resume a paused or failed download."""
        task = self._active_tasks.get(download_id)
        if task:
            task.resume()
            await self.db.update_download_status(download_id, DownloadStatus.DOWNLOADING)
            logger.info(f"Resumed active download {download_id}")
            return True
        
        # If not active, reload from DB and re-queue
        download = await self.db.get_download(download_id)
        if download and download.status in (DownloadStatus.PAUSED, DownloadStatus.ERROR, DownloadStatus.DOWNLOADING):
            download.status = DownloadStatus.QUEUED
            download.error_message = None
            await self.db.update_download_status(download_id, DownloadStatus.QUEUED, error_message=None)
            await self.add(download)
            logger.info(f"Re-queued download {download_id} for resume")
            return True
        return False

    async def remove(self, download_id: int) -> bool:
        """Remove/cancel a download."""
        # Cancel if active
        task = self._active_tasks.get(download_id)
        if task:
            task.cancel()
        atask = self._running_asyncio_tasks.get(download_id)
        if atask:
            atask.cancel()
        
        self._active_tasks.pop(download_id, None)
        self._running_asyncio_tasks.pop(download_id, None)
        
        await self.db.delete_download(download_id)
        logger.info(f"Removed download {download_id}")
        return True

    async def _process_queue(self) -> None:
        """Main loop: pull downloads from the queue and run them."""
        while self._running:
            try:
                download = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            await self._semaphore.acquire()
            atask = asyncio.create_task(self._run_download(download))
            self._running_asyncio_tasks[download.id] = atask

    async def _run_download(self, download: DownloadInfo) -> None:
        """Run a single download task within the semaphore."""
        task = DownloadTask(
            download=download,
            config=self.config,
            db=self.db,
            bandwidth=self.bandwidth,
            on_progress=self.on_progress,
            on_status_change=self.on_status_change,
        )
        self._active_tasks[download.id] = task

        try:
            await task.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Download {download.id} failed: {e}")
        finally:
            self._active_tasks.pop(download.id, None)
            self._running_asyncio_tasks.pop(download.id, None)
            self._semaphore.release()

    def get_task(self, download_id: int) -> Optional[DownloadTask]:
        """Get an active download task by ID."""
        return self._active_tasks.get(download_id)
