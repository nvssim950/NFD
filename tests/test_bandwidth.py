"""Unit tests for BandwidthController."""

import pytest
import asyncio
from nassimdl.core.bandwidth import BandwidthController


@pytest.mark.asyncio
async def test_bandwidth_unlimited():
    bw = BandwidthController(0)
    assert bw.is_unlimited
    granted = await bw.acquire(1000000)
    assert granted == 1000000


@pytest.mark.asyncio
async def test_bandwidth_limiting():
    # 1000 bytes per second
    bw = BandwidthController(1000)
    assert not bw.is_unlimited
    assert bw.max_rate == 1000

    # Acquire small chunk
    granted = await bw.acquire(100)
    assert granted > 0
