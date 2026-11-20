"""Segment worker: downloads a single byte-range chunk of a file."""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional, Callable

import aiohttp

from nassimdl.models.download import SegmentInfo, SegmentStatus
from nassimdl.core.bandwidth import BandwidthController

logger = logging.getLogger(__name__)

# Chunk size for reading from the network
CHUNK_SIZE = 64 * 1024  # 64 KB
# How often to persist progress (seconds)
PROGRESS_SAVE_INTERVAL = 2.0


class SegmentWorker:
    """
    Downloads a single segment (byte range) of a file.
    
    Supports pause/resume and bandwidth limiting.
    Reports progress via callback.
    """

    def __init__(
        self,
        segment: SegmentInfo,
        url: str,
        bandwidth: BandwidthController,
        user_agent: str = "NassimDL/0.1.0",
        timeout: int = 30,
        max_retries: int = 5,
        retry_delay: float = 2.0,
        on_progress: Optional[Callable[[int, int, float], None]] = None,
        on_save_progress: Optional[Callable[..., None]] = None,
    ):
        """
        Args:
            segment: Segment metadata (byte range, progress, etc.)
            url: Download URL
            bandwidth: Shared bandwidth controller
            on_progress: Callback(segment_index, bytes_downloaded, speed)
            on_save_progress: Callback(segment_id, bytes_downloaded, status=None) for DB persistence
        """
        self.segment = segment
        self.url = url
        self.bandwidth = bandwidth
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.on_progress = on_progress
        self.on_save_progress = on_save_progress

        self._paused = asyncio.Event()
        self._paused.set()  # Not paused initially
        self._cancelled = False
        self._speed: float = 0.0

    async def download(self) -> None:
        """Download this segment with retry logic.
        
        Uses consecutive failure tracking: if progress was made (bytes downloaded),
        the consecutive failure counter resets, preventing transient disconnects
        during large multi-gigabyte transfers from failing prematurely.
        """
        consecutive_failures = 0
        while consecutive_failures <= self.max_retries and not self._cancelled:
            bytes_before = self.segment.bytes_downloaded
            try:
                await self._download_range()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                bytes_gained = self.segment.bytes_downloaded - bytes_before
                if bytes_gained > 0:
                    consecutive_failures = 0
                    logger.info(
                        f"Segment {self.segment.index} made progress (+{bytes_gained} bytes) "
                        f"before error: {e}. Reconnecting..."
                    )
                else:
                    consecutive_failures += 1

                self.segment.retries += 1
                if consecutive_failures <= self.max_retries and not self._cancelled:
                    delay = min(self.retry_delay * (2 ** (consecutive_failures - 1)), 30.0) if consecutive_failures > 0 else 1.0
                    logger.warning(
                        f"Segment {self.segment.index} error ({consecutive_failures}/{self.max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    self.segment.status = SegmentStatus.ERROR
                    self.segment.error_message = str(e)
                    raise

    async def _download_range(self) -> None:
        """Perform the actual HTTP range request and write to temp file."""
        self.segment.status = SegmentStatus.DOWNLOADING
        
        # Ensure temp file directory exists
        self.segment.temp_path.parent.mkdir(parents=True, exist_ok=True)

        # Inspect actual bytes already written to disk for reliable resume
        disk_bytes = 0
        if self.segment.temp_path.exists():
            disk_bytes = self.segment.temp_path.stat().st_size
            if self.segment.total_bytes > 0 and disk_bytes > self.segment.total_bytes:
                # Disk file is larger than segment bounds; truncate to segment size
                with open(self.segment.temp_path, "r+b") as f:
                    f.truncate(self.segment.total_bytes)
                disk_bytes = self.segment.total_bytes

        self.segment.bytes_downloaded = disk_bytes

        # Check if already complete
        if self.segment.total_bytes > 0 and self.segment.bytes_downloaded >= self.segment.total_bytes:
            self.segment.status = SegmentStatus.COMPLETED
            if self.on_save_progress:
                self.on_save_progress(self.segment.id, self.segment.bytes_downloaded, SegmentStatus.COMPLETED)
            return

        range_start = self.segment.start_byte + self.segment.bytes_downloaded
        range_end = self.segment.end_byte

        if range_start > range_end:
            self.segment.status = SegmentStatus.COMPLETED
            if self.on_save_progress:
                self.on_save_progress(self.segment.id, self.segment.bytes_downloaded, SegmentStatus.COMPLETED)
            return

        headers = {
            "User-Agent": self.user_agent,
            "Range": f"bytes={range_start}-{range_end}",
        }

        timeout_obj = aiohttp.ClientTimeout(
            total=None,  # No total timeout for large files
            sock_connect=30,
            sock_read=60,  # 60s read timeout
        )

        last_save_time = time.monotonic()
        speed_window_start = time.monotonic()
        speed_window_bytes = 0

        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.get(self.url, headers=headers) as resp:
                if resp.status == 416:
                    # Range Not Satisfiable: already completed or at end
                    if self.segment.total_bytes > 0 and disk_bytes >= self.segment.total_bytes:
                        self.segment.status = SegmentStatus.COMPLETED
                        if self.on_save_progress:
                            self.on_save_progress(self.segment.id, self.segment.bytes_downloaded, SegmentStatus.COMPLETED)
                        return
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history, status=416, message="Range Not Satisfiable"
                    )

                if resp.status not in (200, 206):
                    raise aiohttp.ClientResponseError(
                        resp.request_info,
                        resp.history,
                        status=resp.status,
                        message=f"Server returned {resp.status}",
                    )

                # Open temp file in append mode (for resume)
                mode = "ab" if self.segment.bytes_downloaded > 0 else "wb"
                with open(self.segment.temp_path, mode) as f:
                    try:
                        async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                            # Check for pause
                            await self._paused.wait()

                            # Check for cancellation
                            if self._cancelled:
                                return

                            # Apply bandwidth limiting
                            allowed = await self.bandwidth.acquire(len(chunk))
                            f.write(chunk)

                            self.segment.bytes_downloaded += len(chunk)
                            speed_window_bytes += len(chunk)

                            # Calculate speed (sliding window)
                            now = time.monotonic()
                            elapsed = now - speed_window_start
                            if elapsed >= 1.0:
                                self._speed = speed_window_bytes / elapsed
                                speed_window_bytes = 0
                                speed_window_start = now

                            # Report progress
                            if self.on_progress:
                                self.on_progress(
                                    self.segment.index,
                                    self.segment.bytes_downloaded,
                                    self._speed,
                                )

                            # Periodically save progress to DB
                            if now - last_save_time >= PROGRESS_SAVE_INTERVAL:
                                if self.on_save_progress:
                                    self.on_save_progress(
                                        self.segment.id,
                                        self.segment.bytes_downloaded,
                                        SegmentStatus.DOWNLOADING,
                                    )
                                last_save_time = now
                    finally:
                        f.flush()
                        try:
                            os.fsync(f.fileno())
                        except OSError:
                            pass

        # Final save
        if self.on_save_progress:
            self.on_save_progress(
                self.segment.id,
                self.segment.bytes_downloaded,
                SegmentStatus.COMPLETED,
            )

        self.segment.status = SegmentStatus.COMPLETED
        logger.info(f"Segment {self.segment.index} completed: {self.segment.bytes_downloaded} bytes")

    def pause(self) -> None:
        """Pause this segment's download."""
        self._paused.clear()
        self.segment.status = SegmentStatus.PAUSED

    def resume(self) -> None:
        """Resume this segment's download."""
        self._paused.set()
        self.segment.status = SegmentStatus.DOWNLOADING

    def cancel(self) -> None:
        """Cancel this segment's download."""
        self._cancelled = True
        self._paused.set()  # Unpause so the loop can exit

    @property
    def speed(self) -> float:
        return self._speed
