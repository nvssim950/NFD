"""HTTP utility functions for probing URLs and parsing headers."""

import re
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

    async with aiohttp.ClientSession(timeout=timeout_obj) as session:
        # Try HEAD first
        try:
            async with session.head(url, headers=headers, allow_redirects=True) as resp:
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
                    )
        except Exception:
            pass

        # Fallback: Range probe with GET bytes=0-0
        range_headers = {**headers, "Range": "bytes=0-0"}
        async with session.get(url, headers=range_headers, allow_redirects=True) as resp:
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
            )
