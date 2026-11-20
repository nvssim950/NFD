"""DownloadTask: orchestrates downloading one file via multiple segment workers."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, Callable

from nassimdl.models.download import (
    DownloadInfo,
    DownloadStatus,
    SegmentInfo,
    SegmentStatus,
)
from nassimdl.models.config import AppConfig
from nassimdl.core.segment import SegmentWorker
from nassimdl.core.merger import FileMerger
from nassimdl.core.bandwidth import BandwidthController
from nassimdl.utils.http import probe_url
from nassimdl.utils.hash import verify_hash
from nassimdl.storage.database import Database

logger = logging.getLogger(__name__)


class DownloadTask:
    """
    Manages the full lifecycle of downloading one file:
    probe → segment → download → merge → verify.
    """

    def __init__(
        self,
        download: DownloadInfo,
        config: AppConfig,
        db: Database,
        bandwidth: BandwidthController,
        on_progress: Optional[Callable] = None,
        on_status_change: Optional[Callable] = None,
    ):
        self.download = download
        self.config = config
        self.db = db
        self.bandwidth = bandwidth
        self.on_progress = on_progress
        self.on_status_change = on_status_change
        self._workers: list[SegmentWorker] = []
        self._worker_tasks: list[asyncio.Task] = []
        self._speed_by_segment: dict[int, float] = {}
        self._start_time: Optional[float] = None

    @property
    def total_speed(self) -> float:
        """Combined download speed across all segments."""
        return sum(self._speed_by_segment.values())

    @property
    def eta(self) -> Optional[float]:
        """Estimated time remaining in seconds."""
        speed = self.total_speed
        if speed <= 0 or not self.download.file_size:
            return None
        remaining = self.download.file_size - self.download.bytes_downloaded
        return remaining / speed

    async def run(self) -> None:
        """Execute the full download lifecycle."""
        try:
            self._start_time = time.monotonic()

            # Phase 1: Probe the URL
            await self._probe()

            # Phase 2: Create segments (if not resuming)
            if not self.download.segments:
                await self._create_segments()

            # Phase 3: Download all segments in parallel
            await self._download_segments()

            # Phase 4: Merge segments
            await self._merge()

            # Phase 5: Verify hash (if provided)
            if self.download.expected_hash:
                await self._verify()

            # Done!
            self.download.status = DownloadStatus.COMPLETED
            self.download.completed_at = time.time()
            await self.db.update_download_status(
                self.download.id,
                DownloadStatus.COMPLETED,
                completed_at=self.download.completed_at,
            )
            self._notify_status()
            logger.info(f"Download completed: {self.download.filename}")

        except asyncio.CancelledError:
            logger.info(f"Download cancelled: {self.download.filename}")
            raise
        except Exception as e:
            self.download.status = DownloadStatus.ERROR
            self.download.error_message = str(e)
            await self.db.update_download_status(
                self.download.id,
                DownloadStatus.ERROR,
                error_message=str(e),
            )
            self._notify_status()
            logger.error(f"Download failed: {self.download.filename}: {e}")
            raise

    async def _probe(self) -> None:
        """Probe the URL to get file info."""
        # If resuming and we already have file size and segments, skip re-probing
        if self.download.file_size is not None and self.download.segments:
            logger.info(f"Skipping probe for resumed download: {self.download.filename}")
            return

        if self.download.status not in (DownloadStatus.QUEUED, DownloadStatus.PROBING):
            return  # Already probed (resuming)

        self.download.status = DownloadStatus.PROBING
        await self.db.update_download_status(self.download.id, DownloadStatus.PROBING)
        self._notify_status()

        result = await probe_url(
            self.download.url,
            user_agent=self.config.network.user_agent,
            timeout=self.config.network.timeout_seconds,
        )

        self.download.url = result.url  # Follow redirects
        self.download.file_size = result.file_size
        self.download.supports_range = result.supports_range

        # Update filename if not explicitly provided or defaulted
        if (not self.download.filename or self.download.filename == "download") and result.filename:
            self.download.filename = result.filename

        # Decide segment count
        if result.supports_range and result.file_size:
            if not self.download.num_segments or self.download.num_segments <= 1:
                self.download.num_segments = self.config.download.get_segments_for_size(
                    result.file_size
                )
        else:
            self.download.num_segments = 1

        await self.db.update_download_probe(
            self.download.id,
            file_size=result.file_size,
            supports_range=result.supports_range,
            filename=self.download.filename,
            url=self.download.url,
            num_segments=self.download.num_segments,
        )

        logger.info(
            f"Probed {self.download.url}: size={result.file_size}, "
            f"range={result.supports_range}, segments={self.download.num_segments}"
        )

    async def _create_segments(self) -> None:
        """Split the file into segments based on file size."""
        file_size = self.download.file_size
        num_segments = self.download.num_segments
        temp_dir = self.config.download.temp_dir / str(self.download.id)
        temp_dir.mkdir(parents=True, exist_ok=True)

        if not file_size or not self.download.supports_range:
            # Single segment for unknown size or no range support
            seg = SegmentInfo(
                id=0,
                download_id=self.download.id,
                index=0,
                start_byte=0,
                end_byte=file_size - 1 if file_size else 0,
                temp_path=temp_dir / "segment_0.part",
            )
            self.download.segments = [seg]
        else:
            segment_size = file_size // num_segments
            segments = []
            for i in range(num_segments):
                start = i * segment_size
                end = (i + 1) * segment_size - 1 if i < num_segments - 1 else file_size - 1
                seg = SegmentInfo(
                    id=0,
                    download_id=self.download.id,
                    index=i,
                    start_byte=start,
                    end_byte=end,
                    temp_path=temp_dir / f"segment_{i}.part",
                )
                segments.append(seg)
            self.download.segments = segments

        await self.db.save_segments(self.download.segments)
        logger.info(f"Created {len(self.download.segments)} segments for {self.download.filename}")

    async def _download_segments(self) -> None:
        """Download all segments in parallel."""
        self.download.status = DownloadStatus.DOWNLOADING
        await self.db.update_download_status(self.download.id, DownloadStatus.DOWNLOADING)
        self._notify_status()

        # Create workers for incomplete segments
        self._workers = []
        for seg in self.download.segments:
            if seg.is_complete:
                continue
            worker = SegmentWorker(
                segment=seg,
                url=self.download.url,
                bandwidth=self.bandwidth,
                user_agent=self.config.network.user_agent,
                timeout=self.config.network.timeout_seconds,
                max_retries=self.config.network.max_retries,
                retry_delay=self.config.network.retry_delay_seconds,
                on_progress=self._on_segment_progress,
                on_save_progress=self._on_segment_save,
            )
            self._workers.append(worker)

        # Launch all workers as concurrent tasks
        self._worker_tasks = [
            asyncio.create_task(w.download()) for w in self._workers
        ]

        # Wait for all to complete
        try:
            await asyncio.gather(*self._worker_tasks)
        except Exception:
            for w in self._workers:
                w.cancel()
            for t in self._worker_tasks:
                if not t.done():
                    t.cancel()
            raise

        for seg in self.download.segments:
            await self.db.update_segment_progress(
                seg.id, seg.bytes_downloaded, SegmentStatus.COMPLETED
            )
        logger.info(f"All segments downloaded for {self.download.filename}")

    async def _merge(self) -> None:
        """Merge segment files into the final output."""
        self.download.status = DownloadStatus.MERGING
        await self.db.update_download_status(self.download.id, DownloadStatus.MERGING)
        self._notify_status()

        segment_paths = [seg.temp_path for seg in self.download.segments]
        output_path = self.download.output_path

        await FileMerger.merge(
            segment_paths=segment_paths,
            output_path=output_path,
            expected_size=self.download.file_size,
        )

        # Clean up temp files
        await FileMerger.cleanup_segments(segment_paths)
        
        # Try to remove the temp directory
        temp_dir = self.config.download.temp_dir / str(self.download.id)
        try:
            temp_dir.rmdir()
        except OSError:
            pass

    async def _verify(self) -> None:
        """Verify file integrity using hash."""
        self.download.status = DownloadStatus.VERIFYING
        await self.db.update_download_status(self.download.id, DownloadStatus.VERIFYING)
        self._notify_status()

        is_valid = await verify_hash(
            self.download.output_path,
            self.download.expected_hash,
            self.download.hash_type or "sha256",
        )
        if not is_valid:
            raise ValueError(
                f"Hash verification failed for {self.download.filename}. "
                f"Expected {self.download.hash_type}: {self.download.expected_hash}"
            )
        logger.info(f"Hash verified for {self.download.filename}")

    def _on_segment_progress(self, seg_index: int, bytes_downloaded: int, speed: float) -> None:
        """Called by segment workers to report progress."""
        self._speed_by_segment[seg_index] = speed
        if self.on_progress:
            self.on_progress(self.download)

    def _on_segment_save(
        self,
        segment_id: int,
        bytes_downloaded: int,
        status: Optional[SegmentStatus] = None,
    ) -> None:
        """Called by segment workers to persist progress. Schedules async DB update."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.db.update_segment_progress(segment_id, bytes_downloaded, status)
            )
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(
                    self.db.update_segment_progress(segment_id, bytes_downloaded, status)
                )
            except Exception:
                pass

    def _notify_status(self) -> None:
        """Notify listeners of status change."""
        if self.on_status_change:
            self.on_status_change(self.download)

    def pause(self) -> None:
        """Pause all segment workers."""
        self.download.status = DownloadStatus.PAUSED
        for worker in self._workers:
            worker.pause()

    def resume(self) -> None:
        """Resume all segment workers."""
        self.download.status = DownloadStatus.DOWNLOADING
        for worker in self._workers:
            worker.resume()

    def cancel(self) -> None:
        """Cancel all segment workers."""
        for worker in self._workers:
            worker.cancel()
        for task in self._worker_tasks:
            task.cancel()
