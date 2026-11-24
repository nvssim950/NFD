"""HTTP utility functions for probing URLs and parsing headers."""

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import aiohttp


@dataclass
class ProbeResult:
    """Result of probing a URL."""
    url: str  # final URL after redirects
    file_size: Optional[int]  # None if unknown
    supports_range: bool
    filename: str
    content_type: Optional[str]
    probe_latency_ms: float = 0.0
    ratelimit_limit: Optional[int] = None
    ratelimit_remaining: Optional[int] = None
    retry_after_seconds: Optional[float] = None


def parse_retry_after(header_val: Optional[str]) -> Optional[float]:
    """Parse numeric Retry-After header seconds, clamped to [0.5, 30.0]."""
    if not header_val:
        return None
    val = header_val.strip()
    try:
        secs = float(val)
        return max(0.5, min(30.0, secs))
    except ValueError:
        return None


def _parse_int_header(headers, *names: str) -> Optional[int]:
    """Return the first positive/non-negative integer header among candidate names."""
    for name in names:
        raw = headers.get(name)
        if raw is not None:
            raw = raw.strip()
            if raw.isdigit():
                return int(raw)
    return None


def extract_filename_from_url(url: str) -> str:
    """Extract a filename from a URL path."""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    name = Path(path).name
    if not name or name == "/":
        return "download"
    name = name.split("?")[0].split("#")[0]
    return name if name else "download"


def parse_content_disposition(header: str) -> Optional[str]:
    """Extract filename from Content-Disposition header."""
    if not header:
        return None
    match = re.search(r"filename\*\s*=\s*(?:UTF-8''|utf-8'')(.+?)(?:;|$)", header, re.IGNORECASE)
    if match:
        return unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename\s*=\s*"?([^";\n]+)"?', header, re.IGNORECASE)
    if match:
        return match.group(1).strip().strip('"')
    return None


async def probe_url(
    url: str,
    user_agent: str = "NassimDL/0.1.0",
    timeout: int = 30,
) -> ProbeResult:
    """
    Probe URL to determine file size, range support, and filename.
    
    Tries HEAD request first. If HEAD fails or is rejected, falls back to a GET
    request with Range: bytes=0-0 to check range support without downloading the body.
    """
    headers = {"User-Agent": user_agent}
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    head_retry_after: Optional[float] = None

    async with aiohttp.ClientSession(timeout=timeout_obj) as session:
        # Try HEAD first
        start_t = time.monotonic()
        try:
            async with session.head(url, headers=headers, allow_redirects=True) as resp:
                latency_ms = (time.monotonic() - start_t) * 1000.0
                if resp.status == 200:
                    final_url = str(resp.url)
                    content_length = resp.headers.get("Content-Length")
                    file_size = int(content_length) if content_length and content_length.isdigit() else None
                    accept_ranges = resp.headers.get("Accept-Ranges", "").lower()
                    supports_range = accept_ranges == "bytes"

                    content_disp = resp.headers.get("Content-Disposition", "")
                    filename = parse_content_disposition(content_disp)
                    if not filename:
                        filename = extract_filename_from_url(final_url)

                    return ProbeResult(
                        url=final_url,
                        file_size=file_size,
                        supports_range=supports_range,
                        filename=filename,
                        content_type=resp.headers.get("Content-Type"),
                        probe_latency_ms=latency_ms,
                        ratelimit_limit=_parse_int_header(resp.headers, "X-RateLimit-Limit", "RateLimit-Limit"),
                        ratelimit_remaining=_parse_int_header(resp.headers, "X-RateLimit-Remaining", "RateLimit-Remaining"),
                    )
                if resp.status in (429, 503):
                    head_retry_after = parse_retry_after(resp.headers.get("Retry-After")) or 1.0
                    await asyncio.sleep(min(head_retry_after, 3.0))
        except Exception:
            pass

        # Fallback: Range probe with GET bytes=0-0 (retry once on transient 429/503)
        range_headers = {**headers, "Range": "bytes=0-0"}
        for attempt in range(2):
            start_t = time.monotonic()
            async with session.get(url, headers=range_headers, allow_redirects=True) as resp:
                latency_ms = (time.monotonic() - start_t) * 1000.0
                if resp.status in (429, 503) and attempt == 0:
                    retry_after = parse_retry_after(resp.headers.get("Retry-After")) or 1.5
                    head_retry_after = retry_after
                    await asyncio.sleep(min(retry_after, 5.0))
                    continue

                final_url = str(resp.url)
                supports_range = False
                file_size = None

                if resp.status == 206:
                    supports_range = True
                    content_range = resp.headers.get("Content-Range", "")
                    if "/" in content_range:
                        total_str = content_range.split("/")[-1].strip()
                        if total_str.isdigit():
                            file_size = int(total_str)
                elif resp.status == 200:
                    supports_range = resp.headers.get("Accept-Ranges", "").lower() == "bytes"
                    content_length = resp.headers.get("Content-Length")
                    if content_length and content_length.isdigit():
                        file_size = int(content_length)
                else:
                    resp.raise_for_status()

                content_disp = resp.headers.get("Content-Disposition", "")
                filename = parse_content_disposition(content_disp)
                if not filename:
                    filename = extract_filename_from_url(final_url)

                return ProbeResult(
                    url=final_url,
                    file_size=file_size,
                    supports_range=supports_range,
                    filename=filename,
                    content_type=resp.headers.get("Content-Type"),
                    probe_latency_ms=latency_ms,
                    ratelimit_limit=_parse_int_header(resp.headers, "X-RateLimit-Limit", "RateLimit-Limit"),
                    ratelimit_remaining=_parse_int_header(resp.headers, "X-RateLimit-Remaining", "RateLimit-Remaining"),
                    retry_after_seconds=head_retry_after,
                )
