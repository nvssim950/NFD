"""Token-bucket rate limiter for bandwidth control."""

import asyncio
import time


class BandwidthController:
    """
    Token-bucket rate limiter that controls download speed.
    
    Set max_bytes_per_second to 0 for unlimited speed.
    All segment workers share one controller instance.
    """

    def __init__(self, max_bytes_per_second: int = 0):
        self._max_rate = max_bytes_per_second
        self._tokens: float = 0
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()
        # Bucket capacity = 2 seconds worth of tokens (allows bursts)
        self._capacity = max_bytes_per_second * 2 if max_bytes_per_second > 0 else 0

    @property
    def max_rate(self) -> int:
        return self._max_rate

    @max_rate.setter
    def max_rate(self, value: int) -> None:
        self._max_rate = value
        self._capacity = value * 2 if value > 0 else 0

    @property
    def is_unlimited(self) -> bool:
        return self._max_rate <= 0

    async def acquire(self, num_bytes: int) -> int:
        """
        Request permission to transfer num_bytes.
        
        Returns the number of bytes actually allowed (may be less than requested).
        If rate limiting is disabled (max_rate=0), returns num_bytes immediately.
        """
        if self.is_unlimited:
            return num_bytes

        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._last_refill = now

            # Refill tokens
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._max_rate,
            )

            if self._tokens <= 0:
                # Need to wait for tokens
                wait_time = (num_bytes - self._tokens) / self._max_rate
                # Cap wait to avoid blocking too long
                wait_time = min(wait_time, 1.0)
                await asyncio.sleep(wait_time)
                # Refill after wait
                self._tokens += wait_time * self._max_rate

            # Grant as many bytes as available
            granted = min(num_bytes, int(self._tokens))
            granted = max(granted, 1)  # always allow at least 1 byte
            self._tokens -= granted
            return granted
