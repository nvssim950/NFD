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
from nassimdl.core.adaptive import AdaptiveConcurrencyGate
from nassimdl.utils.http import parse_retry_after

logger = logging.getLogger(__name__)

# Chunk size for reading from the network
CHUNK_SIZE = 64 * 1024  # 64 KB
# How often to persist progress (seconds)
PROGRESS_SAVE_INTERVAL = 2.0


class ThrottleResponseError(aiohttp.ClientResponseError):
    """HTTP error carrying an optional Retry-After cooldown from the server."""

    def __init__(self, *args, retry_after: Optional[float] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


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
        concurrency_gate: Optional[AdaptiveConcurrencyGate] = None,
        on_progress: Optional[Callable[[int, int, float], None]] = None,
        on_save_progress: Optional[Callable[..., None]] = None,
    ):
        """
        Args:
            segment: Segment metadata (byte range, progress, etc.)
            url: Download URL
            bandwidth: Shared bandwidth controller
            concurrency_gate: Optional adaptive concurrency controller
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
        self.concurrency_gate = concurrency_gate
        self.on_progress = on_progress
        self.on_save_progress = on_save_progress

        self._paused = asyncio.Event()
        self._paused.set()  # Not paused initially
        self._cancelled = False
        self._speed: float = 0.0

    def _reset_speed(self) -> None:
        """Reset active speed gauge when idle, paused, finished, or retrying."""
        if self._speed != 0.0:
            self._speed = 0.0
            if self.on_progress:
                self.on_progress(self.segment.index, self.segment.bytes_downloaded, 0.0)

    async def download(self) -> None:
        """Download this segment with adaptive retry and concurrency feedback."""
        consecutive_failures = 0
        adaptive_retries = 0
        max_adaptive_retries = max(self.max_retries, 8)

        while consecutive_failures <= self.max_retries and not self._cancelled:
            if self.concurrency_gate:
                acquired = await self.concurrency_gate.acquire(
                    paused_event=self._paused,
                    is_cancelled=lambda: self._cancelled,
                )
                if not acquired or self._cancelled:
                    return

            bytes_before = self.segment.bytes_downloaded
            released = False
            try:
                await self._download_range()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if self.concurrency_gate:
                    self.concurrency_gate.release()
                    released = True
                self._reset_speed()

                bytes_gained = self.segment.bytes_downloaded - bytes_before
                status_code = getattr(e, "status", None)
                retry_after = getattr(e, "retry_after", None)

                # Fail fast on permanent URL / auth / missing resource errors
                if status_code in (401, 404, 410):
                    self.segment.status = SegmentStatus.ERROR
                    self.segment.error_message = str(e)
                    raise

                adapted = False
                if self.concurrency_gate:
                    if status_code in (429, 503, 509):
                        adapted = self.concurrency_gate.report_throttle(
                            status_code=status_code, retry_after=retry_after
                        )
                    elif status_code == 403 and (
                        bytes_gained > 0 or self.concurrency_gate.has_successful_activity
                    ):
                        adapted = self.concurrency_gate.report_throttle(
                            status_code=403, retry_after=retry_after
                        )
                    elif isinstance(
                        e,
                        (
                            aiohttp.ClientConnectionError,
                            aiohttp.ServerDisconnectedError,
                            asyncio.TimeoutError,
                            ConnectionError,
                        ),
                    ):
                        adapted = self.concurrency_gate.report_disconnect()

                if bytes_gained > 0:
                    consecutive_failures = 0
                    logger.info(
                        f"Segment {self.segment.index} made progress (+{bytes_gained} bytes) "
                        f"before error: {e}. Reconnecting..."
                    )
                elif adapted and adaptive_retries < max_adaptive_retries:
                    adaptive_retries += 1
                else:
                    consecutive_failures += 1

                self.segment.retries += 1
                if consecutive_failures <= self.max_retries and not self._cancelled:
                    if retry_after is not None:
                        delay = retry_after
                    elif adapted:
                        delay = 0.25  # Gate enforces shared cooldown during acquire()
                    else:
                        delay = (
                            min(self.retry_delay * (2 ** (consecutive_failures - 1)), 30.0)
                            if consecutive_failures > 0
                            else 1.0
                        )
                    logger.warning(
                        f"Segment {self.segment.index} error ({consecutive_failures}/{self.max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    self.segment.status = SegmentStatus.ERROR
                    self.segment.error_message = str(e)
                    raise
            finally:
                self._reset_speed()
                if self.concurrency_gate and not released:
                    self.concurrency_gate.release()

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

                if resp.status in (429, 503, 509):
                    retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                    raise ThrottleResponseError(
                        resp.request_info,
                        resp.history,
                        status=resp.status,
                        message=f"Server throttled request ({resp.status})",
                        retry_after=retry_after,
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
                            if self.concurrency_gate:
                                self.concurrency_gate.mark_activity()

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

        self._reset_speed()
        self.segment.status = SegmentStatus.COMPLETED
        logger.info(f"Segment {self.segment.index} completed: {self.segment.bytes_downloaded} bytes")

    def pause(self) -> None:
        """Pause this segment's download."""
        self._paused.clear()
        self.segment.status = SegmentStatus.PAUSED
        self._reset_speed()

    def resume(self) -> None:
        """Resume this segment's download."""
        self._paused.set()
        self.segment.status = SegmentStatus.DOWNLOADING

    def cancel(self) -> None:
        """Cancel this segment's download."""
        self._cancelled = True
        self._paused.set()  # Unpause so the loop can exit
        self._reset_speed()

    @property
    def speed(self) -> float:
        return self._speed
