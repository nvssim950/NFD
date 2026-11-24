"""Adaptive segment sizing and runtime server concurrency control."""

import asyncio
import logging
import time
from typing import Callable, Optional

from nassimdl.models.config import DownloadConfig
from nassimdl.utils.http import ProbeResult

logger = logging.getLogger(__name__)


def compute_auto_segments(config: DownloadConfig, probe: ProbeResult) -> int:
    """Choose the initial segment count (1-16) from file size and server probe signals."""
    if not probe.supports_range or not probe.file_size or probe.file_size <= 0:
        return 1

    target = config.get_segments_for_size(probe.file_size)

    # Respect server rate-limit headers if present
    if probe.ratelimit_remaining is not None:
        if probe.ratelimit_remaining <= 1:
            target = 1
        elif probe.ratelimit_remaining < target:
            target = max(1, probe.ratelimit_remaining)

    if probe.ratelimit_limit is not None and probe.ratelimit_limit < target:
        target = max(1, probe.ratelimit_limit)

    # Be conservative on slow/throttling origins observed during probe
    if probe.retry_after_seconds is not None:
        target = min(target, 2)
    elif probe.probe_latency_ms > 2000:
        target = min(target, 4)

    target = min(target, probe.file_size)
    return max(1, min(16, target))


class AdaptiveConcurrencyGate:
    """Controls active segment workers using slow-start ramp-up and AIMD throttle backoff.

    In manual mode (`auto_mode=False`), all segments run up to `total_segments`.
    In Auto mode (`auto_mode=True`), starts gently (up to 4 concurrent streams), ramps up
    towards `total_segments` when extra streams improve speed, and halves concurrency on
    server throttling (429/503/509) or multi-connection transport drops.
    """

    def __init__(
        self,
        total_segments: int,
        auto_mode: bool = True,
        probe_latency_ms: float = 0.0,
        probe_throttled: bool = False,
        initial_limit: Optional[int] = None,
    ):
        self.total_segments = max(1, total_segments)
        self.auto_mode = auto_mode

        if initial_limit is not None:
            start_limit = max(1, min(self.total_segments, initial_limit))
        elif not auto_mode:
            start_limit = self.total_segments
        elif probe_throttled:
            start_limit = min(self.total_segments, 2)
        elif probe_latency_ms > 1500:
            start_limit = min(self.total_segments, 2)
        else:
            start_limit = min(self.total_segments, 4)

        self._limit = start_limit
        self._max_limit = min(self.total_segments, 4) if (auto_mode and probe_throttled) else self.total_segments
        self._prev_limit = start_limit
        self._active = 0
        self._changed = asyncio.Event()

        now = time.monotonic()
        self._cooldown_until: float = 0.0
        self._last_throttle_time: float = 0.0
        self._last_step_time: float = now
        self._last_step_speed: Optional[float] = None
        self._throttled_before: bool = probe_throttled
        self._has_activity: bool = False
        self._recent_disconnects: list[float] = []
        self._status_reason: str = (
            "Slow-start ramp" if (auto_mode and start_limit < self.total_segments) else "Auto"
        )
        self.adaptation_events: int = 0

    @property
    def current_limit(self) -> int:
        return self._limit

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def has_successful_activity(self) -> bool:
        return self._has_activity

    @property
    def status_reason(self) -> str:
        return self._status_reason

    def mark_activity(self) -> None:
        """Record that at least one worker successfully received payload bytes."""
        self._has_activity = True

    async def acquire(
        self,
        paused_event: Optional[asyncio.Event] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Acquire a concurrency permit, honoring cooldowns, pause, and cancellation."""
        while True:
            if is_cancelled and is_cancelled():
                return False

            if paused_event is not None and not paused_event.is_set():
                await paused_event.wait()
                continue

            now = time.monotonic()
            if now < self._cooldown_until:
                await asyncio.sleep(min(self._cooldown_until - now, 0.25))
                continue

            if self._active < self._limit:
                self._active += 1
                return True

            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                continue

    def release(self) -> None:
        """Release a concurrency permit and wake waiting workers."""
        if self._active > 0:
            self._active -= 1
        self._changed.set()

    def _notify_sync(self) -> None:
        self._changed.set()

    def report_throttle(
        self,
        status_code: Optional[int | str] = None,
        retry_after: Optional[float] = None,
    ) -> bool:
        """Halve concurrency limit (debounced per burst) on server rate-limiting/overload.

        Returns True if an adaptive step-down or burst cooldown is active so the worker
        can yield and re-enter at the reduced concurrency without burning permanent failures.
        """
        if not self.auto_mode:
            return False

        now = time.monotonic()
        wait_sec = retry_after if retry_after is not None else 1.5
        wait_sec = max(0.5, min(30.0, wait_sec))
        self._cooldown_until = max(self._cooldown_until, now + wait_sec)

        # Debounce simultaneous burst failures within 1.5s into a single halving step
        if now - self._last_throttle_time < 1.5:
            return self._throttled_before

        self._last_throttle_time = now
        self._throttled_before = True

        if self._limit > 1:
            new_limit = max(1, self._limit // 2)
            self._prev_limit = self._limit
            self._limit = new_limit
            self._max_limit = new_limit
            self.adaptation_events += 1
            self._status_reason = f"Throttled ({status_code or 'limit'}) → {new_limit} streams"
            logger.warning(
                f"Server throttle detected ({status_code}); reducing active streams to {new_limit} "
                f"(cooldown {wait_sec:.1f}s)"
            )
            self._notify_sync()
            return True

        self._status_reason = f"Server limit ({status_code or 'limit'}) → 1 stream"
        return False

    def report_disconnect(self) -> bool:
        """Detect burst transport disconnects when multiple connections are active."""
        if not self.auto_mode or self._limit <= 1:
            return False

        now = time.monotonic()
        self._recent_disconnects = [t for t in self._recent_disconnects if now - t <= 3.0]
        self._recent_disconnects.append(now)

        if len(self._recent_disconnects) >= 2:
            self._recent_disconnects.clear()
            return self.report_throttle(status_code="disconnect", retry_after=1.0)
        return False

    def evaluate_speed(self, current_speed: float, remaining_segments: int) -> None:
        """Ramp up concurrency cautiously while additional streams improve throughput."""
        if not self.auto_mode or self._throttled_before:
            return
        if self._limit >= self._max_limit or remaining_segments <= self._limit:
            return

        now = time.monotonic()
        if now < self._cooldown_until or (now - self._last_step_time) < 1.5:
            return
        if current_speed <= 0 or self._active < min(self._limit, remaining_segments):
            return

        if self._last_step_speed is None:
            self._last_step_speed = current_speed
            self._prev_limit = self._limit
            self._limit = min(self._max_limit, self._limit + 4)
            self._last_step_time = now
            self._status_reason = f"Scaling to {self._limit} streams"
            logger.info(f"Auto ramp-up: {self._prev_limit} -> {self._limit} streams ({current_speed / 1024:.0f} KB/s)")
            self._notify_sync()
            return

        # Evaluate whether the previous step-up meaningfully increased speed (+12%)
        if current_speed >= self._last_step_speed * 1.12 or self._last_step_speed < 256 * 1024:
            self._last_step_speed = current_speed
            self._prev_limit = self._limit
            self._limit = min(self._max_limit, self._limit + 4)
            self._last_step_time = now
            self._status_reason = f"Scaling to {self._limit} streams"
            logger.info(f"Auto ramp-up: {self._prev_limit} -> {self._limit} streams ({current_speed / 1024:.0f} KB/s)")
            self._notify_sync()
        elif current_speed < self._last_step_speed * 0.85 and self._prev_limit < self._limit:
            # Speed dropped after adding streams; revert to previous optimal limit
            self._limit = self._prev_limit
            self._max_limit = self._prev_limit
            self.adaptation_events += 1
            self._status_reason = f"Optimal at {self._limit} streams"
            logger.info(f"Auto optimal: reverted to {self._limit} streams to avoid contention")
            self._notify_sync()
        else:
            # Bandwidth is saturated at current stream count; stop ramping up
            self._max_limit = self._limit
            self._status_reason = f"Optimal at {self._limit} streams"
            logger.info(f"Auto optimal: holding at {self._limit} streams (bandwidth saturated)")
